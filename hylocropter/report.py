"""
"For the technical team": what has to be fixed before a flight's numbers can be
fully trusted.

The farmer-facing verdict uses whatever the settings were on the day. These
checks sit underneath it, folded away, and say which of those settings are
still placeholders. Each is worked out from what the flight recorded -- nothing
here is a fixed list of caveats -- so a check that has been dealt with turns
into a tick on its own.

Each check is ``{"level", "title": {"tl", "en"}, "detail"}``. Level is ``warn``
(needs fixing) or ``ok`` (checked and fine). The details stay in English: they
are for the people who will fix them, and the numbers do not translate.

Two items from the design are not here because nothing the device records can
measure them: whether the soil between plants reads as healthy (that needs the
pixels split into plant and ground), and whether the photos are in focus.
"""

from collections import Counter

import bndvi

# CALIBRATION.md aims for the white card's brightest pixels at 180-230. A
# flight whose brightest NIR pixels stay well under that is underexposed, and
# low signal biases BNDVI upward (RESEARCH-GAPS.md §11).
DARK_PEAK = 128


def flight_k(captures):
    """The leak coefficient a flight used: the most common one among its
    captures, or None when correction was off for all of them."""
    ks = [round((c.get("settings") or {}).get("nir_leak_coef") or 0, 3)
          for c in captures
          if (c.get("settings") or {}).get("correct_nir_leakage")]
    if not ks:
        return None
    return Counter(ks).most_common(1)[0][0]


def _when(flight):
    ts = flight.get("started_at") or ""
    return f"{ts[8:10]}/{ts[5:7]} {ts[11:16]}" if ts else flight.get("id", "?")


def _mean_channels(captures):
    """Flight-wide mean of each capture's NIR and blue means."""
    rows = [c["channels"] for c in captures
            if (c.get("channels") or {}).get("nir") is not None
            and c["channels"].get("blue") is not None]
    if not rows:
        return None
    return (sum(r["nir"] for r in rows) / len(rows),
            sum(r["blue"] for r in rows) / len(rows))


def _index(nir, blue, k=None):
    """BNDVI of two channel means, with or without leak correction."""
    if k is not None:
        blue = max(1e-6, blue - k * nir)
    return (nir - blue) / (nir + blue) if nir + blue > 0 else None


def _inside(lat, lon, pts):
    """Ray-casting point-in-polygon on (lat, lon) pairs."""
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        yi, xi = pts[i]
        yj, xj = pts[j]
        if (yi > lat) != (yj > lat) and \
                lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _block_outline(block):
    if block.get("points"):
        return block["points"]
    return [(block["south"], block["west"]), (block["south"], block["east"]),
            (block["north"], block["east"]), (block["north"], block["west"])]


def checks(flight, captures, history, cfg, tile_areas=(), blocks=()):
    """The technical checks for one flight.

    `history` is ``[(flight, its captures), ...]`` for every finished flight,
    so a leak coefficient that changed between flights can be spotted.
    `tile_areas` is the tile manifest's ``areas`` list; `blocks` the drawn
    survey blocks.
    """
    out = []
    readings = [c for c in captures if (c.get("stats") or {}).get("mean")
                is not None]
    k = flight_k(captures)

    # ── leak correction: measured? ──────────────────────────────────────
    measured = "leak" in (cfg.get("setup_done_steps") or [])
    ch = _mean_channels(readings)
    if k is not None and not measured:
        detail = (f"This flight used k = {k:.2f}, but the walkthrough’s "
                  f"“Leakage measured” step isn’t done, so k is a placeholder.")
        if ch:
            with_k, without = _index(ch[0], ch[1], k), _index(ch[0], ch[1])
            if with_k is not None and without is not None:
                detail += (f" On this flight’s average channels (NIR {ch[0]:.1f}, "
                           f"blue {ch[1]:.1f}) BNDVI is about {with_k:+.2f} with "
                           f"the correction and {without:+.2f} without it")
                t = flight.get("thresholds") or {}
                th = t.get("healthy", bndvi.DEFAULT_THRESHOLD_HEALTHY)
                tm = t.get("moderate", bndvi.DEFAULT_THRESHOLD_MODERATE)
                if bndvi.classify(with_k, th, tm) != bndvi.classify(without, th, tm):
                    detail += ", which is the difference between bands — the " \
                              "verdict depends on this number."
                else:
                    detail += "."
        out.append({"level": "warn",
                    "title": {"tl": "Hindi pa nasusukat ang k",
                              "en": "Leak correction k was never measured"},
                    "detail": detail})
    elif k is not None:
        out.append({"level": "ok",
                    "title": {"tl": "Nasukat na ang k",
                              "en": "Leak correction k was measured"},
                    "detail": f"k = {k:.2f}, from the walkthrough’s white-card step."})

    # ── leak correction: same across flights? ───────────────────────────
    ks = {}
    for f, caps in history:
        if f.get("stats"):
            ks.setdefault(flight_k(caps), []).append(f)
    if len(ks) > 1:
        usual = max(ks, key=lambda key: len(ks[key]))
        odd = [(key, fs) for key, fs in ks.items() if key != usual]
        parts = []
        for key, fs in odd:
            label = "off" if key is None else f"k = {key:.2f}"
            fs = sorted(fs, key=lambda f: f.get("started_at") or "")
            parts.append(f"{label} on " + ", ".join(_when(f) for f in fs))
        usual_label = "off" if usual is None else f"{usual:.2f}"
        out.append({"level": "warn",
                    "title": {"tl": "Nag-iba ang k sa pagitan ng lipad",
                              "en": "k changed between flights"},
                    "detail": ("; ".join(parts) + f"; {usual_label} on the rest. "
                               f"This flight used "
                               f"{'no correction' if k is None else f'k = {k:.2f}'}. "
                               "A trend across flights with different k shows "
                               "the setting changing, not the plants.")})

    # ── exposure ────────────────────────────────────────────────────────
    peaks = [c["channels"]["nir_max"] for c in readings
             if (c.get("channels") or {}).get("nir_max") is not None]
    flagged = sum(1 for c in readings
                  if (c.get("exposure_check") or {}).get("level") == "warn")
    if peaks:
        peaks.sort()
        median_peak = peaks[len(peaks) // 2]
        masking = cfg.get("mask_low_signal")
        if median_peak < DARK_PEAK or flagged:
            s0 = (readings[0].get("settings") or {})
            detail = (f"The brightest NIR pixels peak around {median_peak:.0f} of "
                      f"255 (median over {len(peaks)} photos)")
            if ch:
                detail += f"; channel means sit near {ch[0]:.0f} NIR / {ch[1]:.0f} blue"
            detail += (f" at {s0.get('exposure_us', '?')} µs, gain "
                       f"{s0.get('gain', '?')}. CALIBRATION.md aims for "
                       "180–230. Low signal biases BNDVI upward "
                       "(RESEARCH-GAPS.md §11)")
            detail += (", and low-signal masking is off." if not masking else ".")
            if flagged:
                detail += f" {flagged} photo(s) failed the exposure check."
            out.append({"level": "warn",
                        "title": {"tl": "Madilim ang mga litrato",
                                  "en": "Photos are dark"},
                        "detail": detail})
        else:
            out.append({"level": "ok",
                        "title": {"tl": "Sapat ang liwanag",
                                  "en": "Exposure is usable"},
                        "detail": f"Brightest NIR pixels peak around "
                                  f"{median_peak:.0f} of 255."})

    # ── thresholds ──────────────────────────────────────────────────────
    t = flight.get("thresholds") or {}
    if (t.get("healthy"), t.get("moderate")) == (
            bndvi.DEFAULT_THRESHOLD_HEALTHY, bndvi.DEFAULT_THRESHOLD_MODERATE):
        out.append({"level": "warn",
                    "title": {"tl": "Pangkalahatang threshold",
                              "en": "Thresholds are generic"},
                    "detail": (f"{t['healthy']:.2f} / {t['moderate']:.2f} are the "
                               "generic defaults, not values measured on dragon "
                               "fruit. The Objective 4 field validation is still "
                               "open (RESEARCH-GAPS.md §5).")})

    # ── synthetic frames ────────────────────────────────────────────────
    fake = sum(1 for c in captures if (c.get("settings") or {}).get("dev_mode"))
    if fake:
        out.append({"level": "warn",
                    "title": {"tl": "Hindi totoong litrato",
                              "en": "Synthetic frames"},
                    "detail": f"{fake} of {len(captures)} photos are generated "
                              "test frames, not camera images."})

    # ── where it flew ───────────────────────────────────────────────────
    placed = [c for c in captures if (c.get("geo") or {}).get("lat") is not None]
    if not placed and captures:
        out.append({"level": "warn",
                    "title": {"tl": "Walang GPS", "en": "No GPS on the photos"},
                    "detail": "None of the photos had a position, so they "
                              "could not be placed on the map."})
    if placed and tile_areas:
        def on_imagery(c):
            g = c["geo"]
            for a in tile_areas:
                b = a.get("tile_bounds") or a.get("bounds")
                if b and b["south"] <= g["lat"] <= b["north"] \
                        and b["west"] <= g["lon"] <= b["east"]:
                    return True
            return False
        off = [c for c in placed if not on_imagery(c)]
        if off:
            lons = [c["geo"]["lon"] for c in placed]
            lats = [c["geo"]["lat"] for c in placed]
            out.append({"level": "warn",
                        "title": {"tl": "Labas sa satellite map",
                                  "en": "Flight is outside the offline imagery"},
                        "detail": (f"{len(off)} of {len(placed)} photos fall "
                                   "outside the downloaded tiles, so they draw over "
                                   f"blank ground. This flight covered "
                                   f"{min(lats):.4f}–{max(lats):.4f}° N, "
                                   f"{min(lons):.4f}–{max(lons):.4f}° E. Download "
                                   "imagery for it in Settings.")})
    block = next((b for b in blocks if b.get("name") == flight.get("name")), None)
    if placed and block:
        outline = _block_outline(block)
        n_in = sum(1 for c in placed
                   if _inside(c["geo"]["lat"], c["geo"]["lon"], outline))
        if n_in < len(placed) / 2:
            out.append({"level": "warn",
                        "title": {"tl": "Labas sa block",
                                  "en": "Flight is mostly outside its block"},
                        "detail": (f"Only {n_in} of {len(placed)} photos fall "
                                   f"inside the “{block['name']}” outline. Either "
                                   "the outline is in the wrong place or the "
                                   "mission flew somewhere else.")})

    # ── camera lock ─────────────────────────────────────────────────────
    checked = [c for c in captures if c.get("control_check")]
    if checked:
        bad = [c for c in checked if c["control_check"].get("mismatches")]
        raw_isp = [c for c in checked
                   if c["control_check"].get("isp_neutralised") is False]
        if bad or raw_isp:
            out.append({"level": "warn",
                        "title": {"tl": "Hindi naka-lock ang camera",
                                  "en": "Camera lock check failed"},
                        "detail": (f"{len(bad)} of {len(checked)} photos reported "
                                   "settings different from the ones requested; "
                                   f"{len(raw_isp)} ran without the neutral ISP "
                                   "tuning.")})
        else:
            out.append({"level": "ok",
                        "title": {"tl": "Naka-lock ang camera",
                                  "en": "Camera lock and ISP check pass"},
                        "detail": ("Reported exposure, gain and colour gains match "
                                   f"the request on all {len(checked)} photos; ISP "
                                   "neutralised.")})

    # Things to fix first, then the ticks.
    out.sort(key=lambda c: c["level"] != "warn")
    return out
