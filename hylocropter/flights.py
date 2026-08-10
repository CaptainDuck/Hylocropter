"""
Flight and capture storage.

A flight groups many GPS-tagged captures; the farm map is a grid built by
binning each capture's mean BNDVI into the cell its position falls in.

Still JSON files, no database — this device has one user and a flat list of
flights, and a JSON index is something you can read with your eyes when
something goes wrong in the field.

Two problems in the old index are fixed here:

* The capture lock used to be released *before* the read-append-write of
  `captures.json`, so two overlapping requests could lose a record. All index
  mutation now happens under one lock.
* Writes were plain `write_text()`. On an SD card in a drone, a power cut
  mid-write truncates the file and the dashboard comes back empty. Writes are
  now temp-file + `os.replace()`, which is atomic.
"""

import datetime
import json
import logging
import math
import os
import shutil
import tempfile
import threading
from pathlib import Path

import bndvi

log = logging.getLogger("hylocropter.flights")

# Map overlay resolution. Matches the mockup's 14x9 so the map reads the same,
# and it is about right for a 10 ha plot -- ~40 m cells at 620 m across.
GRID_COLS, GRID_ROWS = 14, 9

# How far outside the outermost capture the flight bounds extend, so edge
# captures are not painted on the very border of the overlay.
BOUNDS_PAD_M = 25.0
# Fallback half-size when a flight has one capture, or none with GPS.
BOUNDS_MIN_M = 60.0

# "Every capture, whatever flight it belongs to" -- distinct from None, which is a
# real stored value meaning "this capture belongs to no flight" (a ground capture,
# or anything migrated from the old dashboard). Using None for both meant asking
# for ground captures quietly returned all of them.
ALL = object()


class Store:
    """Owns hylocropter_data/: the two indexes and the per-flight directories."""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.captures_file = self.data_dir / "captures.json"
        self.flights_file = self.data_dir / "flights.json"
        self.ground_dir = self.data_dir / "ground"
        self._lock = threading.RLock()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.ground_dir.mkdir(parents=True, exist_ok=True)

    # ── low-level IO ──────────────────────────────────────────────────────

    @staticmethod
    def _read(path):
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text())
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError) as exc:
            log.error("could not read %s: %s", path.name, exc)
            return []

    @staticmethod
    def _write(path, records):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(records, fh, indent=2)
            os.replace(tmp, path)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise

    # ── captures ──────────────────────────────────────────────────────────

    def captures(self, flight_id=ALL, newest_first=True):
        """Captures, optionally for one flight.

        `flight_id=None` means the ground captures -- the ones belonging to no
        flight. Omit the argument entirely for all of them.
        """
        with self._lock:
            records = self._read(self.captures_file)
        if flight_id is not ALL:
            records = [r for r in records if r.get("flight_id") == flight_id]
        return sorted(records, key=lambda r: r.get("timestamp", ""),
                      reverse=newest_first)

    def ground_captures(self, newest_first=True):
        """Captures taken without a flight, including migrated legacy records."""
        return self.captures(flight_id=None, newest_first=newest_first)

    def capture(self, capture_id):
        return next((r for r in self.captures() if r["id"] == capture_id), None)

    def add_capture(self, record):
        with self._lock:
            records = self._read(self.captures_file)
            records.append(record)
            self._write(self.captures_file, records)
        return record

    def update_capture(self, capture_id, patch):
        with self._lock:
            records = self._read(self.captures_file)
            record = next((r for r in records if r["id"] == capture_id), None)
            if record is None:
                return None
            for key in ("label", "notes"):
                if key in patch:
                    record[key] = patch[key] or None
            self._write(self.captures_file, records)
            return record

    def delete_capture(self, capture_id):
        with self._lock:
            records = self._read(self.captures_file)
            record = next((r for r in records if r["id"] == capture_id), None)
            if record is None:
                return False
            base = self.capture_dir(record.get("flight_id"))
            for name in record.get("files", {}).values():
                (base / name).unlink(missing_ok=True)
            self._write(self.captures_file,
                        [r for r in records if r["id"] != capture_id])
            return True

    def capture_dir(self, flight_id):
        """Where a capture's artefacts live. Ground captures go in ground/."""
        if not flight_id:
            return self.ground_dir
        d = self.data_dir / flight_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def file_path(self, capture_id, key):
        """Resolve one artefact via the index -- never by globbing the dir."""
        record = self.capture(capture_id)
        if not record:
            return None
        name = record.get("files", {}).get(key)
        if not name:
            return None
        return self.capture_dir(record.get("flight_id")) / name

    # ── flights ───────────────────────────────────────────────────────────

    def flights(self, newest_first=True):
        with self._lock:
            records = self._read(self.flights_file)
        return sorted(records, key=lambda f: f.get("started_at", ""),
                      reverse=newest_first)

    def flight(self, flight_id):
        return next((f for f in self.flights() if f["id"] == flight_id), None)

    def open_flight(self, name=None, trigger="mission", mission=None,
                    thresholds=None):
        """Start a flight. Returns the new record."""
        now = datetime.datetime.now()
        flight = {
            "id": "F-" + now.strftime("%Y%m%d-%H%M"),
            "name": name or "Untitled flight",
            "started_at": now.isoformat(timespec="seconds"),
            "ended_at": None,
            "duration_s": None,
            "capture_ids": [],
            "capture_count": 0,
            "bounds": None,
            "grid": None,
            "stats": None,
            "classification": None,
            "altitude_m": None,
            "mission": mission or {},
            "trigger": trigger,
            "status": "recording",
            "thresholds": thresholds or {
                "healthy": bndvi.DEFAULT_THRESHOLD_HEALTHY,
                "moderate": bndvi.DEFAULT_THRESHOLD_MODERATE},
        }
        with self._lock:
            records = self._read(self.flights_file)
            # A second flight in the same minute would collide on id.
            existing = {f["id"] for f in records}
            if flight["id"] in existing:
                suffix = 2
                while f"{flight['id']}-{suffix}" in existing:
                    suffix += 1
                flight["id"] = f"{flight['id']}-{suffix}"
            records.append(flight)
            self._write(self.flights_file, records)
        self.capture_dir(flight["id"])
        log.info("flight %s opened (%s)", flight["id"], flight["name"])
        return flight

    def update_flight(self, flight_id, patch):
        with self._lock:
            records = self._read(self.flights_file)
            flight = next((f for f in records if f["id"] == flight_id), None)
            if flight is None:
                return None
            flight.update(patch)
            self._write(self.flights_file, records)
            return flight

    def attach_capture(self, flight_id, capture_id):
        with self._lock:
            records = self._read(self.flights_file)
            flight = next((f for f in records if f["id"] == flight_id), None)
            if flight is None:
                return None
            if capture_id not in flight["capture_ids"]:
                flight["capture_ids"].append(capture_id)
            flight["capture_count"] = len(flight["capture_ids"])
            self._write(self.flights_file, records)
            return flight

    def delete_flight(self, flight_id, keep_records=False):
        """Delete a flight. `keep_records=True` removes only the image files.

        That distinction backs the mockup's "Free up space" dialog, which
        promises: "This removes flights older than 30 days and their photos.
        The numbers stay in the record."
        """
        with self._lock:
            flights = self._read(self.flights_file)
            flight = next((f for f in flights if f["id"] == flight_id), None)
            if flight is None:
                return False
            freed = 0
            d = self.data_dir / flight_id
            if d.exists():
                for p in d.rglob("*"):
                    if p.is_file():
                        freed += p.stat().st_size
                shutil.rmtree(d, ignore_errors=True)

            captures = self._read(self.captures_file)
            if keep_records:
                for r in captures:
                    if r.get("flight_id") == flight_id:
                        r["files"] = {}
                        r["files_purged"] = True
                flight["files_purged"] = True
                flight["status"] = flight.get("status", "ok")
                self._write(self.captures_file, captures)
                self._write(self.flights_file, flights)
            else:
                self._write(self.captures_file,
                            [r for r in captures
                             if r.get("flight_id") != flight_id])
                self._write(self.flights_file,
                            [f for f in flights if f["id"] != flight_id])
            return freed

    # ── analysis: bounds, grid, summary ───────────────────────────────────

    def close_flight(self, flight_id, thresholds=None):
        """Finalise a flight: bounds, grid, aggregate stats, classification."""
        captures = self.captures(flight_id=flight_id, newest_first=False)
        flight = self.flight(flight_id)
        if flight is None:
            return None
        th = thresholds or flight.get("thresholds") or {}
        t_healthy = th.get("healthy", bndvi.DEFAULT_THRESHOLD_HEALTHY)
        t_moderate = th.get("moderate", bndvi.DEFAULT_THRESHOLD_MODERATE)

        started = flight.get("started_at")
        ended = datetime.datetime.now()
        duration = None
        if started:
            try:
                duration = int(
                    (ended - datetime.datetime.fromisoformat(started))
                    .total_seconds())
            except ValueError:
                duration = None

        patch = {
            "ended_at": ended.isoformat(timespec="seconds"),
            "duration_s": duration,
            "capture_count": len(captures),
            "capture_ids": [c["id"] for c in captures],
            "status": "ok" if captures else "failed",
            "thresholds": {"healthy": t_healthy, "moderate": t_moderate},
        }

        readable = [c for c in captures if has_reading(c)]
        if captures:
            patch["stats"] = aggregate_stats(captures)
            # A flight where nothing could be read is not "stressed" -- it is
            # unmeasured, and the templates already render that as "No reading".
            patch["classification"] = (
                bndvi.classify(patch["stats"]["mean"], t_healthy, t_moderate)
                if readable else None)
            alts = [c["geo"]["rel_alt_m"] for c in captures
                    if c.get("geo") and c["geo"].get("rel_alt_m")]
            patch["altitude_m"] = (round(sum(alts) / len(alts), 1)
                                   if alts else None)
            bounds = compute_bounds(captures)
            patch["bounds"] = bounds
            patch["grid"] = build_grid(captures, bounds, t_healthy, t_moderate)

        flight = self.update_flight(flight_id, patch)
        log.info("flight %s closed — %d captures, mean %s", flight_id,
                 len(captures),
                 f"{patch['stats']['mean']:+.3f}" if readable else "n/a")
        return flight

    def recolour_flight(self, flight_id, t_healthy, t_moderate):
        """Recompute band shares for a flight at new thresholds.

        The stored per-capture `stats` are left alone deliberately -- they record
        what was measured at capture time. This only refreshes the flight-level
        summary the map legend reads from.
        """
        flight = self.flight(flight_id)
        if not flight or not flight.get("grid"):
            return flight
        cells = [c for c in flight["grid"]["cells"] if c is not None]
        if not cells:
            return flight
        total = len(cells)
        stats = dict(flight.get("stats") or {})
        stats["healthy_pct"] = 100.0 * sum(c > t_healthy for c in cells) / total
        stats["stressed_pct"] = 100.0 * sum(c < t_moderate for c in cells) / total
        stats["moderate_pct"] = 100.0 - stats["healthy_pct"] - stats["stressed_pct"]
        return self.update_flight(flight_id, {
            "stats": stats,
            "thresholds": {"healthy": t_healthy, "moderate": t_moderate},
            "classification": bndvi.classify(stats.get("mean") or 0.0,
                                             t_healthy, t_moderate),
        })

    # ── migration ─────────────────────────────────────────────────────────

    def migrate_legacy(self, legacy_dir):
        """Bring forward a pre-Hylocropter bndvi_output/ directory.

        Old records get flight_id/geo of None so they show up as ground
        captures rather than being silently dropped.
        """
        legacy_dir = Path(legacy_dir)
        legacy_index = legacy_dir / "captures.json"
        if not legacy_index.exists():
            return 0
        legacy = self._read(legacy_index)
        if not legacy:
            return 0

        with self._lock:
            current = self._read(self.captures_file)
            known = {r["id"] for r in current}
            moved = 0
            for record in legacy:
                if record.get("id") in known:
                    continue
                record.setdefault("flight_id", None)
                record.setdefault("geo", None)
                record.setdefault("trigger", "manual")
                for name in record.get("files", {}).values():
                    src = legacy_dir / name
                    if src.exists():
                        shutil.copy2(src, self.ground_dir / name)
                current.append(record)
                moved += 1
            if moved:
                self._write(self.captures_file, current)
        if moved:
            log.info("migrated %d legacy captures from %s", moved, legacy_dir)
        return moved

    # ── device stats ──────────────────────────────────────────────────────

    def disk_usage(self):
        total = 0
        for p in self.data_dir.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
        return total


# ── module-level maths (pure, easy to reason about and test) ─────────────────

def has_reading(capture):
    """Did this capture actually measure anything?

    A capture whose every pixel fell below the signal floor stores a mean of
    None rather than a number -- there was nothing to average. Such a capture
    must be skipped by every aggregate rather than counted as a reading of zero,
    which would drag a flight's mean toward "stressed" on the strength of a photo
    that measured nothing at all. Legacy records predate the floor and always
    carry a number, so they pass unchanged.
    """
    stats = capture.get("stats")
    return bool(stats) and stats.get("mean") is not None


def aggregate_stats(captures):
    """Flight-level statistics from its captures.

    Means are weighted equally per capture rather than per pixel. Captures are
    all the same resolution, so this is the same answer with less bookkeeping.
    """
    # Only captures that produced a reading. A frame masked out entirely -- lens
    # cap left on, a photo triggered in deep shade -- stores stats of None, and
    # must not be averaged in as if it had measured zero.
    captures = [c for c in captures if has_reading(c)]

    def mean_of(key):
        vals = [c["stats"][key] for c in captures if c["stats"].get(key) is not None]
        return sum(vals) / len(vals) if vals else 0.0

    means = [c["stats"]["mean"] for c in captures]
    return {
        "mean": mean_of("mean"),
        "min": min((c["stats"]["min"] for c in captures), default=0.0),
        "max": max((c["stats"]["max"] for c in captures), default=0.0),
        "std": (math.sqrt(sum((m - (sum(means) / len(means))) ** 2
                              for m in means) / len(means)) if means else 0.0),
        "healthy_pct": mean_of("healthy_pct"),
        "moderate_pct": mean_of("moderate_pct"),
        "stressed_pct": mean_of("stressed_pct"),
    }


def compute_bounds(captures):
    """Bounding box around the geotagged captures, padded.

    Returns None when nothing has a fix — the map then falls back to the plot
    bounds from settings, and the UI says the flight has no GPS.
    """
    pts = [(c["geo"]["lat"], c["geo"]["lon"]) for c in captures
           if c.get("geo") and c["geo"].get("lat") is not None]
    if not pts:
        return None
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    mid_lat = (min(lats) + max(lats)) / 2

    pad_lat = BOUNDS_PAD_M / 111_320.0
    pad_lon = BOUNDS_PAD_M / (111_320.0 * max(0.01,
                                              math.cos(math.radians(mid_lat))))
    south, north = min(lats) - pad_lat, max(lats) + pad_lat
    west, east = min(lons) - pad_lon, max(lons) + pad_lon

    # One capture (or several at one spot) gives a degenerate box; open it out
    # so the overlay has somewhere to draw.
    min_lat = BOUNDS_MIN_M / 111_320.0
    min_lon = BOUNDS_MIN_M / (111_320.0 * max(0.01,
                                              math.cos(math.radians(mid_lat))))
    if north - south < min_lat:
        south, north = mid_lat - min_lat, mid_lat + min_lat
    if east - west < min_lon:
        mid_lon = (west + east) / 2
        west, east = mid_lon - min_lon, mid_lon + min_lon

    return {"south": south, "west": west, "north": north, "east": east}


def build_grid(captures, bounds, t_healthy, t_moderate,
               cols=GRID_COLS, rows=GRID_ROWS):
    """Bin capture means into a cols x rows grid over `bounds`.

    Empty cells stay None. That matters: painting an unvisited cell as
    mid-range would invent healthy ground the drone never flew over, which is
    exactly the kind of thing a farmer would act on.
    """
    cells = [None] * (cols * rows)
    if not bounds:
        return {"cols": cols, "rows": rows, "cells": cells, "covered": 0}

    sums = [0.0] * (cols * rows)
    counts = [0] * (cols * rows)
    dlat = bounds["north"] - bounds["south"]
    dlon = bounds["east"] - bounds["west"]
    if dlat <= 0 or dlon <= 0:
        return {"cols": cols, "rows": rows, "cells": cells, "covered": 0}

    for c in captures:
        geo = c.get("geo")
        # No reading is not the same as a reading of zero: a capture that was
        # entirely below the signal floor leaves its cell null, exactly as a cell
        # the drone never flew over stays null.
        if not geo or geo.get("lat") is None or not has_reading(c):
            continue
        fx = (geo["lon"] - bounds["west"]) / dlon
        fy = 1.0 - (geo["lat"] - bounds["south"]) / dlat   # row 0 is north
        if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
            continue
        col = min(cols - 1, int(fx * cols))
        row = min(rows - 1, int(fy * rows))
        idx = row * cols + col
        sums[idx] += c["stats"]["mean"]
        counts[idx] += 1

    covered = 0
    for i in range(cols * rows):
        if counts[i]:
            cells[i] = round(sums[i] / counts[i], 4)
            covered += 1
    return {"cols": cols, "rows": rows, "cells": cells, "covered": covered}


def footprint(geo, fov_h_deg=62.2, fov_v_deg=48.8):
    """How much ground one photo covers, as a centre + half-extents in metres.

    Straight trigonometry for a nadir-pointing camera: at height h, a lens with
    horizontal angle of view a covers 2*h*tan(a/2) across. For the Pi Camera v2
    (62.2 x 48.8 degrees) at 12 m that is about 14.5 x 10.9 m.

    Returns None when there is no usable height — without altitude the footprint
    is unknowable, and guessing one would put invented ground on the map.
    """
    if not geo:
        return None
    height = geo.get("rel_alt_m")
    if not height or height <= 0:
        return None
    half_w = height * math.tan(math.radians(fov_h_deg / 2.0))
    half_h = height * math.tan(math.radians(fov_v_deg / 2.0))
    return {
        "lat": geo["lat"], "lon": geo["lon"],
        "half_w_m": round(half_w, 3), "half_h_m": round(half_h, 3),
        "width_m": round(half_w * 2, 2), "height_m": round(half_h * 2, 2),
        "heading_deg": geo.get("heading_deg") or 0.0,
        "height_m_agl": height,
        # metres per pixel at the capture's own resolution, i.e. the ground
        # sampling distance — the honest limit on what this data can resolve
        "gsd_cm": None,
    }


def ground_sampling_distance_cm(geo, resolution, fov_h_deg=62.2):
    """Centimetres per pixel on the ground. The real resolution limit."""
    fp = footprint(geo, fov_h_deg)
    if not fp or not resolution:
        return None
    return round(fp["width_m"] / max(1, resolution[0]) * 100, 2)


# ── survey blocks ────────────────────────────────────────────────────────────
# A block is a named rectangle inside the downloaded vicinity: the ground one
# flight actually covers. Real plots are not squares and a farm has several of
# them, so this is a list of bounds rather than a centre and a side length.

M_PER_DEG_LAT = 111_320.0
# Placeholder used when nothing has been marked yet. Deliberately modest, and the
# UI says out loud that it is a placeholder rather than anyone's field.
PLACEHOLDER_BLOCK_M = 100


def m_per_deg_lon(lat):
    """Metres per degree of longitude at a latitude. Shrinks toward the poles."""
    return M_PER_DEG_LAT * max(0.01, math.cos(math.radians(lat)))


# ── polygon geometry ─────────────────────────────────────────────────────────
#
# A block used to be an axis-aligned rectangle, which cannot describe a plot that
# runs diagonally. The bounding box of a 100 x 40 m plot at 45 degrees is about
# 99 x 99 m -- 2.4x the area -- so planning on the box overestimates the battery
# and flies lines over the neighbour's ground. Blocks are polygons now; a
# rectangle is simply a four-point one, so there is a single path through the
# code rather than two.
#
# All of this works in a local metres frame centred on the plot: over a few
# hundred metres the earth is flat enough that the error is far below GPS noise,
# and it keeps the maths ordinary planar geometry.

def to_local_m(points, origin=None):
    """lat/lon -> (x east, y north) metres about `origin` (default: first point)."""
    if origin is None:
        origin = points[0]
    lat0, lon0 = origin
    k = m_per_deg_lon(lat0)
    return [((lon - lon0) * k, (lat - lat0) * M_PER_DEG_LAT)
            for lat, lon in points]


def to_latlon(xy, origin):
    lat0, lon0 = origin
    k = m_per_deg_lon(lat0)
    return [(lat0 + y / M_PER_DEG_LAT, lon0 + x / k) for x, y in xy]


def polygon_area_m2(points):
    """Shoelace area, in square metres. Sign-free -- winding order is the
    operator's clicking order and carries no meaning here."""
    if len(points) < 3:
        return 0.0
    xy = to_local_m(points)
    total = 0.0
    for i in range(len(xy)):
        x0, y0 = xy[i]
        x1, y1 = xy[(i + 1) % len(xy)]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


def convex_hull(xy):
    """Monotone chain hull. Used only to orient the flight lines, so a concave
    plot is oriented by its overall extent -- the lines are still clipped to the
    true outline afterwards."""
    pts = sorted(set(xy))
    if len(pts) < 3:
        return pts
    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2:
                (ax, ay), (bx, by) = out[-2], out[-1]
                if (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax) > 0:
                    break
                out.pop()
            out.append(p)
        return out
    return half(pts)[:-1] + half(reversed(pts))[:-1]


def min_area_rect(xy):
    """Smallest-area enclosing rectangle: (angle_rad, long_m, short_m).

    Rotating calipers over the hull. The angle is that of the rectangle's LONG
    side, which is the direction to fly: every turn costs battery and altitude
    hold, so the fewest, longest lines win.
    """
    hull = convex_hull(xy)
    if len(hull) < 3:
        return 0.0, 0.0, 0.0
    best = None
    for i in range(len(hull)):
        x0, y0 = hull[i]
        x1, y1 = hull[(i + 1) % len(hull)]
        edge = math.atan2(y1 - y0, x1 - x0)
        c, s = math.cos(-edge), math.sin(-edge)
        us = [x * c - y * s for x, y in hull]
        vs = [x * s + y * c for x, y in hull]
        w, h = max(us) - min(us), max(vs) - min(vs)
        if best is None or w * h < best[0]:
            best = (w * h, edge, w, h)
    _, edge, w, h = best
    if w >= h:
        return edge, w, h
    # The long side is the other one, so rotate a quarter turn to name it.
    return edge + math.pi / 2.0, h, w


def clip_line_to_polygon(xy, angle, offset):
    """Where an infinite line crosses the polygon, as a list of (start, end)
    spans measured along the line.

    Rotating into the line's own frame turns this into "which y = offset spans
    lie inside", which handles concave plots correctly: a C-shaped block gives
    two separate spans on the lines that cross its notch, and the drone should
    genuinely not fly the gap between them.
    """
    c, s = math.cos(-angle), math.sin(-angle)
    rot = [(x * c - y * s, x * s + y * c) for x, y in xy]
    xs = []
    n = len(rot)
    for i in range(n):
        (ax, ay), (bx, by) = rot[i], rot[(i + 1) % n]
        if (ay > offset) == (by > offset):
            continue                      # both sides, or both on the same side
        t = (offset - ay) / (by - ay)
        xs.append(ax + t * (bx - ax))
    xs.sort()
    # Even-odd: inside between alternate crossings.
    return [(xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2)
            if xs[i + 1] - xs[i] > 0.01]


def survey_lines(points, spacing_m, angle=None):
    """Flight lines covering the polygon: list of ((lat,lon),(lat,lon)) pairs.

    Lines run along the plot's own long axis and are clipped to its outline, so
    the count reflects the ground you actually own rather than its bounding box.
    """
    if len(points) < 3 or spacing_m <= 0:
        return []
    origin = points[0]
    xy = to_local_m(points, origin)
    if angle is None:
        angle, _, _ = min_area_rect(xy)
    c, s = math.cos(-angle), math.sin(-angle)
    vs = [x * s + y * c for x, y in xy]
    lo, hi = min(vs), max(vs)
    span = hi - lo
    # Centre the lines in the block: half a spacing in from each edge, so the
    # swaths sit over the plot rather than half of the first one hanging off it.
    # The epsilon matters: rotating into the line frame leaves a plot that is
    # exactly 40 m across measuring 40.0000000001, and a bare ceil() then adds a
    # whole extra flight line for a rounding error.
    count = max(1, int(math.ceil(span / spacing_m - 1e-9)))
    start = lo + (span - (count - 1) * spacing_m) / 2.0
    ca, sa = math.cos(angle), math.sin(angle)
    out = []
    for i in range(count):
        offset = start + i * spacing_m
        for (a, b) in clip_line_to_polygon(xy, angle, offset):
            p0 = (a * ca - offset * sa, a * sa + offset * ca)
            p1 = (b * ca - offset * sa, b * sa + offset * ca)
            out.append(tuple(to_latlon([p0, p1], origin)))
    return out


MIN_BLOCK_M = 5
MIN_BLOCK_AREA_M2 = 25


def _clean_points(raw):
    """[[lat, lon], ...] -> validated list, or None.

    Consecutive duplicates are dropped: a double-click while drawing would
    otherwise leave a zero-length edge, which makes the hull and the line
    clipping do undefined things.
    """
    if not isinstance(raw, (list, tuple)) or len(raw) < 3:
        return None
    pts = []
    for p in raw:
        try:
            lat = float(p[0])
            lon = float(p[1])
        except (TypeError, ValueError, IndexError, KeyError):
            return None
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
            return None
        if not pts or (abs(pts[-1][0] - lat) > 1e-9 or abs(pts[-1][1] - lon) > 1e-9):
            pts.append((lat, lon))
    # A closed ring repeats its first point; store it open.
    if len(pts) > 1 and abs(pts[0][0] - pts[-1][0]) < 1e-9 \
            and abs(pts[0][1] - pts[-1][1]) < 1e-9:
        pts.pop()
    return pts if len(pts) >= 3 else None


def normalise_block(block, index=0):
    """Validate and tidy one block. Returns None if it isn't usable.

    A block is a polygon. `points` is the outline as clicked; south/west/north/
    east are kept as its bounding box because the map, the flight filter and
    every stored flight already read them, and a record written before polygons
    existed has only those. A plain rectangle is stored as a four-point polygon,
    so there is one shape of block downstream rather than two.

    Corners arrive in whatever order they were clicked, so nothing here assumes a
    winding direction. A block smaller than a single photo footprint is a stray
    double-click, not a plot.
    """
    if not isinstance(block, dict):
        return None

    points = _clean_points(block.get("points"))
    if points is not None:
        lats = [p[0] for p in points]
        lons = [p[1] for p in points]
        south, north = min(lats), max(lats)
        west, east = min(lons), max(lons)
        mid_lat = (south + north) / 2.0
        if (east - west) * m_per_deg_lon(mid_lat) < MIN_BLOCK_M \
                and (north - south) * M_PER_DEG_LAT < MIN_BLOCK_M:
            return None
        if polygon_area_m2(points) < MIN_BLOCK_AREA_M2:
            return None          # three clicks in a line, not a plot
        name = str(block.get("name") or "").strip() or f"Block {index + 1}"
        return {
            "id": str(block.get("id") or "").strip() or f"b{index + 1}",
            "name": name[:60],
            "south": south, "west": west, "north": north, "east": east,
            "points": [[round(a, 7), round(b, 7)] for a, b in points],
        }

    try:
        south = min(90.0, max(-90.0, float(block["south"])))
        north = min(90.0, max(-90.0, float(block["north"])))
        west = min(180.0, max(-180.0, float(block["west"])))
        east = min(180.0, max(-180.0, float(block["east"])))
    except (KeyError, TypeError, ValueError):
        return None
    if south > north:
        south, north = north, south
    if west > east:
        west, east = east, west

    mid_lat = (south + north) / 2.0
    width_m = (east - west) * m_per_deg_lon(mid_lat)
    height_m = (north - south) * M_PER_DEG_LAT
    if width_m < MIN_BLOCK_M or height_m < MIN_BLOCK_M:
        return None

    name = str(block.get("name") or "").strip() or f"Block {index + 1}"
    return {
        "id": str(block.get("id") or "").strip() or f"b{index + 1}",
        "name": name[:60],
        "south": south, "west": west, "north": north, "east": east,
        # A rectangle is just a four-point polygon. Giving it `points` here means
        # blocks stored before polygons existed come forward automatically and
        # everything downstream handles exactly one kind of shape.
        "points": [[round(south, 7), round(west, 7)],
                   [round(south, 7), round(east, 7)],
                   [round(north, 7), round(east, 7)],
                   [round(north, 7), round(west, 7)]],
    }


def block_dimensions(block):
    """The block's own dimensions, in metres, plus its true area.

    `width_m`/`height_m` are the sides of the smallest rectangle that encloses
    the plot, not its north-south bounding box: for a plot lying at 45 degrees
    those differ by more than a factor of two, and the bounding box is the wrong
    one to plan a flight from. For an axis-aligned block they are the same thing.

    `area_ha` is the shoelace area of the outline, so an L-shaped block does not
    claim the ground in its notch.
    """
    mid_lat = (block["south"] + block["north"]) / 2.0
    centre = {"centre_lat": mid_lat,
              "centre_lon": (block["west"] + block["east"]) / 2.0}
    points = _clean_points(block.get("points"))
    if points is None:
        width_m = (block["east"] - block["west"]) * m_per_deg_lon(mid_lat)
        height_m = (block["north"] - block["south"]) * M_PER_DEG_LAT
        return dict(centre, width_m=round(width_m, 1),
                    height_m=round(height_m, 1),
                    area_ha=round(width_m * height_m / 10_000.0, 3))
    _, long_m, short_m = min_area_rect(to_local_m(points))
    return dict(centre,
                width_m=round(long_m, 1),
                height_m=round(short_m, 1),
                area_ha=round(polygon_area_m2(points) / 10_000.0, 3),
                vertices=len(points))


def block_by_id(blocks, block_id):
    return next((b for b in (blocks or []) if b.get("id") == block_id), None)


# Areas you can practise over before going anywhere near the farm. Sizes only --
# no coordinates, because the point is that they are wherever you happen to be,
# and the offline imagery only covers Tanauan. Every mission number except the
# map itself depends on the block's *dimensions*, so a size is enough to plan,
# rehearse, and check the numbers against a real flight.
TEST_AREAS = [
    {"id": "t-court", "name": "Basketball court", "w": 28, "h": 15,
     "note": "The smallest sane test. One pass, a handful of photos."},
    {"id": "t-yard", "name": "Yard or car park", "w": 40, "h": 30,
     "note": "Enough ground to see the lawnmower pattern work."},
    {"id": "t-pitch", "name": "Football field", "w": 105, "h": 68,
     "note": "About 0.7 ha — close to a real block, and a school has one."},
    {"id": "t-hectare", "name": "One hectare", "w": 100, "h": 100,
     "note": "The round number to sanity-check photo counts against."},
]


def test_area_by_id(area_id):
    return next((a for a in TEST_AREAS if a["id"] == area_id), None)


# Typical F450 mapping speed. Slow enough that 5000 us of shutter does not smear.
DEFAULT_SURVEY_SPEED_MS = 3.0
# Measured: raw JPEG + heatmap + false colour + thumbnail + npz at 8 MP.
BYTES_PER_CAPTURE = 8 * 1024 * 1024
# A 3S 5200 mAh pack on an F450 realistically gives this much useful survey time.
USABLE_FLIGHT_MINUTES = 10.0


# How far off a cardinal a line can be and still be worth calling "east-west".
# Below this the words are more use to the person flying than a bearing is, and a
# block drawn by hand is never exactly 90.000 degrees anyway.
CARDINAL_TOLERANCE_DEG = 5.0


def _bearing_label(angle_rad):
    """Flight-line direction, in words where words are clearer.

    The maths works in maths convention (x east, anticlockwise from east); a
    bearing is clockwise from north. Lines are undirected, so 200 degrees and 20
    degrees describe the same set of lines -- fold into 0-179 rather than
    printing two names for one thing.
    """
    bearing = (90.0 - math.degrees(angle_rad)) % 180.0
    if min(bearing, 180.0 - bearing) <= CARDINAL_TOLERANCE_DEG:
        return "north–south"
    if abs(bearing - 90.0) <= CARDINAL_TOLERANCE_DEG:
        return "east–west"
    return f"{bearing:.0f}° from north"


def mission_plan(altitude_m, fov_h_deg=62.2, fov_v_deg=48.8,
                 forward_overlap=0.40, side_overlap=0.30, plot_side_m=None,
                 plot_w_m=None, plot_h_m=None, polygon=None,
                 speed_ms=DEFAULT_SURVEY_SPEED_MS, resolution=(3280, 2464)):
    """Work out what to type into Mission Planner for a given altitude.

    The camera is assumed mounted with its **wide** axis across the flight track,
    which is the usual way round because it maximises swath width. So the
    62.2-degree axis sets the line spacing and the 48.8-degree axis sets how far
    apart the photos are along each line.

    The block is a rectangle, `plot_w_m` x `plot_h_m` -- real plots are not
    squares. `plot_side_m` is a shorthand for a square one. **Flight lines run
    along the block's longer axis**, because each turn costs battery and altitude
    hold: a 200 x 60 m strip flown the long way is 6 lines and 5 turns, flown the
    short way it is 20 lines and 19 turns for the same ground.

    Overlap defaults are deliberately modest. This system places each photo by
    telemetry rather than stitching a true orthomosaic, so it needs only enough
    overlap to avoid gaps when GPS wanders -- not the 70-80% a
    structure-from-motion pipeline would want. Raise them if you later switch to
    real photogrammetry.

    Returns everything needed for the pre-flight card, including the two numbers
    that go straight into the mission: CAM_TRIGG_DIST and the line spacing.
    """
    h = max(1.0, float(altitude_m))
    swath_w = 2 * h * math.tan(math.radians(fov_h_deg / 2.0))   # across track
    along_h = 2 * h * math.tan(math.radians(fov_v_deg / 2.0))   # along track

    forward_overlap = min(0.9, max(0.0, float(forward_overlap)))
    side_overlap = min(0.9, max(0.0, float(side_overlap)))

    photo_spacing = along_h * (1.0 - forward_overlap)
    line_spacing = swath_w * (1.0 - side_overlap)

    # A square shorthand, an explicit rectangle, or the placeholder.
    if plot_w_m is None and plot_h_m is None and plot_side_m is None:
        plot_side_m = PLACEHOLDER_BLOCK_M
    width = max(10.0, float(plot_w_m if plot_w_m is not None else plot_side_m))
    height = max(10.0, float(plot_h_m if plot_h_m is not None else plot_side_m))

    # Fly the long way. `across` is the axis the lines step along.
    along = max(width, height)
    across = min(width, height)
    line_direction = ("east–west" if width >= height else "north–south")
    legs = None

    if polygon:
        # Plan the shape that was actually drawn. Lines follow the plot's own
        # long axis and stop at its edges, so a plot lying at 45 degrees is not
        # charged for the 2.4x of neighbouring ground its bounding box covers,
        # and an L-shaped one is not charged for its notch.
        origin = polygon[0]
        xy = to_local_m(polygon, origin)
        angle, along, across = min_area_rect(xy)
        legs = survey_lines(polygon, max(0.5, line_spacing), angle)
        line_direction = _bearing_label(angle)
        width, height = along, across

    if legs:
        leg_lengths = []
        for (a, b) in legs:
            dx = (b[1] - a[1]) * m_per_deg_lon(a[0])
            dy = (b[0] - a[0]) * M_PER_DEG_LAT
            leg_lengths.append(math.hypot(dx, dy))
        lines = len(leg_lengths)
        photos = sum(max(1, math.ceil(d / max(0.5, photo_spacing)) + 1)
                     for d in leg_lengths)
        per_line = max(1, round(photos / lines))
        # Flown legs plus the hop between the end of one and the start of the
        # next. Approximating each hop as one line spacing is what the rectangle
        # case has always done, and over a convex plot it is very close.
        path_m = sum(leg_lengths) + (lines - 1) * line_spacing
    else:
        lines = max(1, math.ceil(across / max(0.5, line_spacing)))
        per_line = max(1, math.ceil(along / max(0.5, photo_spacing)) + 1)
        photos = lines * per_line

        # Lawnmower path: every leg, plus the turns between them.
        path_m = lines * along + (lines - 1) * line_spacing
    minutes = path_m / max(0.3, float(speed_ms)) / 60.0

    gsd_cm = swath_w / max(1, resolution[0]) * 100 if resolution else None

    warnings = []
    if minutes > USABLE_FLIGHT_MINUTES:
        warnings.append(
            f"About {minutes:.0f} minutes of flying — more than one 3S pack "
            f"realistically covers. Split this block into smaller ones, fly "
            f"higher, or reduce the overlap.")
    if photo_spacing < 1.5:
        warnings.append(
            f"Photos every {photo_spacing:.1f} m is faster than the camera can "
            f"comfortably capture and save at full resolution. Fly higher or "
            f"lower the forward overlap.")
    if h < 5:
        warnings.append("Below about 5 m the footprint is tiny and you will need "
                        "a great many photos to cover anything.")
    if h > 60:
        warnings.append("Above 60 m each pixel covers several centimetres, so "
                        "individual plant detail is lost.")

    return {
        "altitude_m": round(h, 1),
        "footprint_w_m": round(swath_w, 2),
        "footprint_h_m": round(along_h, 2),
        "gsd_cm": round(gsd_cm, 2) if gsd_cm else None,
        "forward_overlap_pct": round(forward_overlap * 100),
        "side_overlap_pct": round(side_overlap * 100),
        # the two numbers that go into Mission Planner
        "trigger_distance_m": round(photo_spacing, 1),
        "line_spacing_m": round(line_spacing, 1),
        "plot_w_m": round(width),
        "plot_h_m": round(height),
        # The drawn outline's true area when there is one, not its enclosing
        # rectangle -- otherwise an L-shaped block claims the ground in its notch.
        "plot_area_ha": round((polygon_area_m2(polygon) if polygon
                               else width * height) / 10_000.0, 2),
        "line_direction": line_direction,
        # The actual flight lines, so the map can draw what the drone will fly
        # rather than leaving the operator to imagine it.
        "legs": [[list(a), list(b)] for a, b in legs] if legs else None,
        "lines": lines,
        "photos_per_line": per_line,
        "photos": photos,
        "path_m": round(path_m),
        "minutes": round(minutes, 1),
        "speed_ms": round(float(speed_ms), 1),
        "storage_mb": round(photos * BYTES_PER_CAPTURE / (1024 * 1024)),
        "warnings": warnings,
    }


def summarise(mean, stressed_pct, has_gps=True):
    """Plain-language summary, in the mockup's voice.

    The thesis asks for exactly this: a farm-level status summary in ordinary
    words, with the numbers available underneath for technical review.
    """
    if mean > 0.38:
        headline = "The plants look healthy."
        advice = "Nothing to do today. Fly again in three days."
        plain = (f"Only about {round(stressed_pct)} out of every 100 spots came "
                 f"back weak. Nothing unusual for this block.")
    elif mean > 0.22:
        headline = "Mostly fine, a few spots to check."
        advice = "Walk the red patches in the next day or two."
        plain = (f"About {round(stressed_pct)} out of every 100 spots came back "
                 f"weak — they show up red on the map.")
    else:
        headline = "Several rows need attention."
        advice = "Check the water lines on the red rows today."
        plain = (f"About {round(stressed_pct)} out of every 100 spots came back "
                 f"weak. The red areas are where to start.")
    if not has_gps:
        plain += (" These photos had no GPS fix, so they could not be placed on "
                  "the map.")
    return {"headline": headline, "plain": plain, "advice": advice}
