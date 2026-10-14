"""
What time it really is, when the Pi itself doesn't know.

The Pi 4 has no real-time clock, and in the field it is offline on its own
hotspot, so there is no NTP either. It boots with fake-hwclock's last saved time,
which can be days old. That is how a flight flown on 13 October at 13:40 was filed
as F-20261010-1657 with its photos stamped 10 October -- the wrong date on the
flights list and the map, which is the one thing a farmer compares flights by.

The flight controller does know: once its GPS has a 3D fix, ArduPilot stamps
SYSTEM_TIME with UTC from the satellites. `telemetry.py` hands each of those to
`note_gps_time()`, and `now()` then answers with the GPS time carried forward on
the monotonic clock. Monotonic, not the wall clock, so a later jump of the system
clock (an NTP sync on a bench, someone running `date`) cannot drag it along.

The correction is only applied past `TRUST_THRESHOLD_S`. Below that the Pi's own
clock is right enough, and switching sources for a few seconds of radio latency
would only make consecutive timestamps jitter.

This never sets the system clock: that needs root, and the dashboard does not
assume it has it. It only changes the timestamps Hylocropter writes.
"""

import datetime
import logging
import threading
import time

log = logging.getLogger("hylocropter.clock")

# A Pi clock this far from GPS is wrong, not just drifting.
TRUST_THRESHOLD_S = 120.0

# Anything before this from the controller is a GPS that has not got the time
# yet (ArduPilot sends 0, some receivers send their 1980 epoch).
EARLIEST_PLAUSIBLE = datetime.datetime(2024, 1, 1,
                                       tzinfo=datetime.timezone.utc).timestamp()


def describe_offset(seconds):
    """'2 d 20 h behind', '5 min ahead' -- for people, not logs."""
    s = abs(int(round(seconds)))
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, sec = divmod(rem, 60)
    if d:
        text = f"{d} d {h} h"
    elif h:
        text = f"{h} h {m} min"
    elif m:
        text = f"{m} min"
    else:
        text = f"{sec} s"
    return f"{text} {'behind' if seconds > 0 else 'ahead'}"


def note(status):
    """One line for a record's page about where its date came from, or None.

    Nothing for records stamped by a clock GPS agreed with, and nothing for
    records older than this check -- they have no `clock` key at all.
    """
    status = status or {}
    source = status.get("source")
    if source == "gps":
        off = status.get("offset_s")
        when = f"was {describe_offset(off)} — " if off is not None else "was off — "
        return f"The Pi's clock {when}GPS time was used."
    if source == "corrected":
        off = status.get("offset_s")
        was = f"was {describe_offset(off)}" if off is not None else "was wrong"
        return (f"The Pi's clock {was} when this was recorded; the date was "
                f"corrected afterwards.")
    if source == "unsynced":
        return ("The drone's GPS had not given the time yet, so this date is "
                "the Pi's own clock and may be wrong.")
    return None


class Clock:
    """System time, corrected by GPS once the controller has told us it."""

    def __init__(self, wall=time.time, mono=time.monotonic,
                 threshold_s=TRUST_THRESHOLD_S):
        self._wall = wall
        self._mono = mono
        self.threshold_s = threshold_s
        self._lock = threading.Lock()
        self._gps_unix = None       # GPS UTC, as a unix timestamp ...
        self._gps_mono = None       # ... and the monotonic reading it came with
        self._announced = False

    def note_gps_time(self, unix_s):
        """Record a GPS time fix. Returns True if it was accepted."""
        if not unix_s or unix_s < EARLIEST_PLAUSIBLE:
            return False
        with self._lock:
            first = self._gps_unix is None
            self._gps_unix = float(unix_s)
            self._gps_mono = self._mono()
        offset = self.offset_s()
        if first and not self._announced:
            self._announced = True
            if self.corrected():
                log.warning("The Pi's clock is %s GPS time -- flights and photos "
                            "will be stamped with GPS time instead",
                            describe_offset(offset), extra={"activity": True})
            else:
                log.info("GPS time received; the Pi's clock agrees "
                         "(off by %.1f s)", offset)
        return True

    def gps_now(self):
        """GPS UTC carried forward to now, as a unix timestamp, or None."""
        with self._lock:
            if self._gps_unix is None:
                return None
            return self._gps_unix + (self._mono() - self._gps_mono)

    def offset_s(self):
        """GPS minus the system clock, in seconds; None before any GPS time.
        Positive means the Pi is behind."""
        gps = self.gps_now()
        return None if gps is None else gps - self._wall()

    def gps_seen(self):
        return self._gps_unix is not None

    def corrected(self):
        """Whether now() is overriding the system clock."""
        off = self.offset_s()
        return off is not None and abs(off) > self.threshold_s

    def timestamp(self):
        """Trusted unix time."""
        return self.gps_now() if self.corrected() else self._wall()

    def now(self):
        """Trusted local time, naive like datetime.datetime.now()."""
        return datetime.datetime.fromtimestamp(self.timestamp())

    def status(self):
        """What the timestamps written right now are based on.

        Recorded on every flight and capture, so a record says for itself
        whether its date can be believed:

          gps        the Pi was off and GPS time was used instead
          system     GPS time was seen and the Pi's clock agreed
          unsynced   no GPS time this boot -- the Pi's clock may be anything
        """
        off = self.offset_s()
        if off is None:
            return {"source": "unsynced", "offset_s": None}
        source = "gps" if abs(off) > self.threshold_s else "system"
        return {"source": source, "offset_s": round(off, 1)}


# The one the app uses. Telemetry feeds it; flights, the recorder and captures
# read it.
default = Clock()
