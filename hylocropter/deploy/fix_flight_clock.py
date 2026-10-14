#!/usr/bin/env python3
"""
Re-date a flight that was stamped by the Pi's wrong clock.

The Pi has no real-time clock and boots offline on fake-hwclock's last saved
time. Before clock.py took GPS time, that put the 13 October flight on
10 October (F-20261010-1657). This shifts a flight's times and its photos'
timestamps by a known offset and, optionally, renames the flight to the id it
should have had.

    # look first -- nothing is written without --apply
    python hylocropter/deploy/fix_flight_clock.py F-20261010-1657 \\
        --offset 247531 --before 2026-10-11 --new-id F-20261013-1342
    # then the same with --apply

--before limits the shift to timestamps earlier than that moment: a clock that
was put right partway through (that flight's ended_at already was) must not be
moved again, and it makes a second run harmless.

What changes, and nothing else:
  flights.json   the flight's started_at / ended_at / duration_s, its id with
                 --new-id, and a `clock` note the map page shows
  captures.json  each photo's timestamp, flight_id with --new-id, a `clock`
                 note, and a process_ms that swallowed the clock jump
  the folder     renamed with --new-id

Photo ids and file names keep the old date: they are only names, and renaming
hundreds of files buys nothing. Both indexes are backed up first. Stop the
dashboard before --apply (`sudo systemctl stop hylocropter`) so it cannot write
in between.
"""

import argparse
import datetime
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "hylocropter_data"


def _parse(ts):
    return datetime.datetime.fromisoformat(ts)


def _shift(ts, offset, before, timespec):
    """ts moved by offset, or unchanged if it is at or after `before`."""
    if not ts:
        return ts
    t = _parse(ts)
    if before is not None and t >= before:
        return ts
    return (t + datetime.timedelta(seconds=offset)).isoformat(timespec=timespec)


def _timespec(ts):
    return "milliseconds" if "." in ts else "seconds"


def _write(path, records):
    """Temp file + os.replace(), like flights.Store: a power cut must not
    leave a truncated index."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(records, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def plan(data_dir, flight_id, offset, before=None, new_id=None):
    """Work out the change. Returns (flights, captures, report lines)."""
    flights = json.loads((data_dir / "flights.json").read_text())
    captures = json.loads((data_dir / "captures.json").read_text())
    flight = next((f for f in flights if f["id"] == flight_id), None)
    if flight is None:
        raise SystemExit(f"no flight {flight_id} in {data_dir}")
    if flight.get("status") == "recording":
        raise SystemExit(f"{flight_id} is still recording -- land and process it first")
    if new_id and new_id != flight_id:
        if any(f["id"] == new_id for f in flights):
            raise SystemExit(f"a flight {new_id} already exists")
        if (data_dir / new_id).exists():
            raise SystemExit(f"{data_dir / new_id} already exists")

    report = []
    for key in ("started_at", "ended_at"):
        old = flight.get(key)
        new = _shift(old, offset, before, "seconds")
        if new != old:
            report.append(f"  {key}: {old} -> {new}")
        flight[key] = new
    if flight.get("started_at") and flight.get("ended_at"):
        duration = int((_parse(flight["ended_at"])
                        - _parse(flight["started_at"])).total_seconds())
        if duration != flight.get("duration_s"):
            report.append(f"  duration_s: {flight.get('duration_s')} -> {duration}")
        flight["duration_s"] = duration
    note = {"source": "corrected", "offset_s": offset}
    if new_id and new_id != flight_id:
        note["renamed_from"] = flight_id
        report.append(f"  id: {flight_id} -> {new_id}")
        flight["id"] = new_id
    flight["clock"] = note

    mine = [c for c in captures if c.get("flight_id") == flight_id]
    moved = 0
    for c in mine:
        old = c.get("timestamp")
        c["timestamp"] = _shift(old, offset, before, _timespec(old or ""))
        moved += c["timestamp"] != old
        # The photo whose processing straddled the jump timed itself across it.
        pm = c.get("process_ms")
        if pm and pm > abs(offset) * 1000:
            c["process_ms"] = round(pm - abs(offset) * 1000)
            report.append(f"  {c['id']} process_ms: {pm} -> {c['process_ms']}")
        c["clock"] = {"source": "corrected", "offset_s": offset}
        if new_id:
            c["flight_id"] = new_id
    if mine:
        first = min(mine, key=lambda c: c["id"])
        last = max(mine, key=lambda c: c["id"])
        report.append(f"  {moved} of {len(mine)} photo timestamps moved; "
                      f"{first['id']} now {first['timestamp']}, "
                      f"{last['id']} now {last['timestamp']}")
    return flights, captures, report


def apply(data_dir, flights, captures, flight_id, new_id=None):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    for name in ("flights.json", "captures.json"):
        shutil.copy2(data_dir / name, data_dir / f"{name}.bak-clockfix-{stamp}")
    renamed = False
    if new_id and new_id != flight_id and (data_dir / flight_id).exists():
        (data_dir / flight_id).rename(data_dir / new_id)
        renamed = True
    try:
        _write(data_dir / "captures.json", captures)
        _write(data_dir / "flights.json", flights)
    except Exception:
        if renamed:
            (data_dir / new_id).rename(data_dir / flight_id)
        raise
    return stamp


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("flight_id")
    ap.add_argument("--offset", type=float, required=True,
                    help="seconds to add (positive: the Pi was behind)")
    ap.add_argument("--before", type=_parse,
                    help="only shift timestamps earlier than this (local time)")
    ap.add_argument("--new-id", help="rename the flight and its folder")
    ap.add_argument("--data", type=Path, default=DATA_DIR)
    ap.add_argument("--apply", action="store_true",
                    help="write the change (default: only show it)")
    args = ap.parse_args(argv)
    offset = int(args.offset) if args.offset == int(args.offset) else args.offset

    flights, captures, report = plan(args.data, args.flight_id, offset,
                                     args.before, args.new_id)
    print(f"{args.flight_id} in {args.data}:")
    print("\n".join(report) or "  nothing to change")
    if not args.apply:
        print("\nDry run -- nothing written. Add --apply to do it.")
        return 0
    stamp = apply(args.data, flights, captures, args.flight_id, args.new_id)
    print(f"\nDone. Backups: flights.json.bak-clockfix-{stamp}, "
          f"captures.json.bak-clockfix-{stamp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
