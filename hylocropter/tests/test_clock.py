"""The Pi has no real-time clock; GPS time has to stand in for it.

This is the 13 October flight that was filed as F-20261010-1657: the Pi booted
offline on fake-hwclock's last saved time, three days stale, and stamped the
flight and all 63 photos with it. The flights list and the map showed the wrong
date -- the one thing a farmer compares flights by.

Times here are that flight's: GPS says 05:40 UTC on the 13th (13:40 in
Tanauan), the Pi says 16:57 on the 10th.
"""

import datetime
import time

import pytest

import clock as clock_mod
import flights as flights_mod
import recorder as recorder_mod
import telemetry as telemetry_mod

UTC = datetime.timezone.utc
GPS_UTC = datetime.datetime(2026, 10, 13, 5, 40, 0, tzinfo=UTC).timestamp()
PI_WALL = datetime.datetime(2026, 10, 10, 8, 57, 0, tzinfo=UTC).timestamp()


class FakeTime:
    """A wall clock and a monotonic clock the test can move independently."""

    def __init__(self, wall):
        self.wall_s = wall
        self.mono_s = 1000.0

    def wall(self):
        return self.wall_s

    def mono(self):
        return self.mono_s

    def tick(self, seconds):
        self.wall_s += seconds
        self.mono_s += seconds


@pytest.fixture(autouse=True)
def manila(monkeypatch):
    """The Pi runs in Asia/Manila, and flight ids are local time."""
    monkeypatch.setenv("TZ", "Asia/Manila")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def ft():
    return FakeTime(PI_WALL)


@pytest.fixture
def clk(ft):
    return clock_mod.Clock(wall=ft.wall, mono=ft.mono)


# ── the offset ───────────────────────────────────────────────────────────────

def test_with_no_gps_it_is_the_pi_clock_and_says_so(clk, ft):
    assert clk.timestamp() == ft.wall()
    assert not clk.corrected()
    assert clk.status() == {"source": "unsynced", "offset_s": None}


def test_a_stale_pi_clock_is_replaced_by_gps_time(clk):
    assert clk.note_gps_time(GPS_UTC)
    assert clk.corrected()
    assert clk.now() == datetime.datetime(2026, 10, 13, 13, 40, 0)
    status = clk.status()
    assert status["source"] == "gps"
    assert status["offset_s"] == pytest.approx(GPS_UTC - PI_WALL)


def test_gps_time_is_carried_forward(clk, ft):
    clk.note_gps_time(GPS_UTC)
    ft.tick(90)
    assert clk.now() == datetime.datetime(2026, 10, 13, 13, 41, 30)


def test_a_jump_of_the_pi_clock_does_not_drag_gps_time_along(clk, ft):
    """Carried forward on the monotonic clock, so someone running `date` or an
    NTP sync on the bench mid-flight cannot move the timestamps."""
    clk.note_gps_time(GPS_UTC)
    ft.wall_s += 7 * 86400
    ft.mono_s += 10
    assert clk.now() == datetime.datetime(2026, 10, 13, 13, 40, 10)


def test_a_clock_that_is_nearly_right_is_left_alone(clk, ft):
    """Radio latency must not make consecutive photos switch clocks."""
    clk.note_gps_time(ft.wall() + 30)
    assert not clk.corrected()
    assert clk.timestamp() == ft.wall()
    assert clk.status() == {"source": "system", "offset_s": 30.0}


def test_the_threshold_is_two_minutes(clk, ft):
    clk.note_gps_time(ft.wall() + 119)
    assert not clk.corrected()
    clk.note_gps_time(ft.wall() + 121)
    assert clk.corrected()


def test_a_pi_clock_that_is_ahead_is_corrected_too(clk, ft):
    clk.note_gps_time(ft.wall() - 3600)
    assert clk.corrected()
    assert clk.status()["offset_s"] == -3600.0


@pytest.mark.parametrize("bogus", [0, None, 315964800.0])  # nothing, 1980 epoch
def test_a_gps_without_the_time_yet_is_ignored(clk, bogus):
    assert not clk.note_gps_time(bogus)
    assert clk.status()["source"] == "unsynced"


def test_the_offset_is_described_for_people():
    assert clock_mod.describe_offset(GPS_UTC - PI_WALL) == "2 d 20 h behind"
    assert clock_mod.describe_offset(-300) == "5 min ahead"
    assert clock_mod.describe_offset(3 * 3600 + 120) == "3 h 2 min behind"


def test_the_note_on_a_record():
    assert "2 d 20 h behind" in clock_mod.note(
        {"source": "gps", "offset_s": GPS_UTC - PI_WALL})
    assert "may be wrong" in clock_mod.note({"source": "unsynced"})
    assert clock_mod.note({"source": "system", "offset_s": 3.0}) is None
    assert clock_mod.note(None) is None            # records older than this


# ── telemetry feeds it ───────────────────────────────────────────────────────

class Msg:
    def __init__(self, kind, **fields):
        self._kind = kind
        self.__dict__.update(fields)

    def get_type(self):
        return self._kind

    def get_srcSystem(self):
        return 1


class Conn:
    target_system = 1
    target_component = 1


@pytest.fixture
def svc(clk):
    s = telemetry_mod.TelemetryService({"mavlink_connection": "/dev/ttyAMA0"},
                                       clock=clk)
    s._conn = Conn()
    return s


def _gps(fix):
    return Msg("GPS_RAW_INT", fix_type=fix, satellites_visible=9, eph=90)


def _system_time(unix_s):
    return Msg("SYSTEM_TIME", time_unix_usec=int(unix_s * 1e6),
               time_boot_ms=60_000)


def test_system_time_is_used_once_there_is_a_3d_fix(svc, clk):
    svc._handle(_gps(3))
    svc._handle(_system_time(GPS_UTC))
    assert clk.corrected()
    assert svc.snapshot()["clock"]["source"] == "gps"


def test_system_time_without_a_3d_fix_is_not_trusted(svc, clk):
    svc._handle(_gps(2))
    svc._handle(_system_time(GPS_UTC))
    assert not clk.gps_seen()
    assert svc.snapshot()["clock"]["source"] == "unsynced"


def test_a_controller_without_gps_time_sends_zero(svc, clk):
    svc._handle(_gps(3))
    svc._handle(Msg("SYSTEM_TIME", time_unix_usec=0, time_boot_ms=5000))
    assert not clk.gps_seen()


# ── flights and photos use it ────────────────────────────────────────────────

def test_a_flight_is_named_and_stamped_with_gps_time(tmp_path, clk):
    clk.note_gps_time(GPS_UTC)
    store = flights_mod.Store(tmp_path, clock=clk)
    flight = store.open_flight(name="Block A")
    assert flight["id"] == "F-20261013-1340"            # not F-20261010-1657
    assert flight["started_at"] == "2026-10-13T13:40:00"
    assert flight["clock"]["source"] == "gps"
    assert (tmp_path / "F-20261013-1340").is_dir()


def test_without_gps_a_flight_uses_the_pi_clock_and_is_flagged(tmp_path, clk):
    store = flights_mod.Store(tmp_path, clock=clk)
    flight = store.open_flight()
    assert flight["id"] == "F-20261010-1657"
    assert flight["clock"] == {"source": "unsynced", "offset_s": None}


def test_closing_uses_gps_time_for_the_end_and_duration(tmp_path, clk, ft):
    clk.note_gps_time(GPS_UTC)
    store = flights_mod.Store(tmp_path, clock=clk)
    flight = store.open_flight()
    ft.tick(12 * 60)
    closed = store.close_flight(flight["id"])
    assert closed["ended_at"] == "2026-10-13T13:52:00"
    assert closed["duration_s"] == 720


def test_a_start_stamped_before_gps_time_is_corrected_at_close(tmp_path, clk,
                                                               ft):
    """Armed from the dashboard before the GPS had a fix: the start is the
    Pi's clock. By landing GPS time is known, so the start is moved -- but the
    id is not, because it names the folder and every photo path."""
    store = flights_mod.Store(tmp_path, clock=clk)
    flight = store.open_flight()
    assert flight["id"] == "F-20261010-1657"
    ft.tick(60)
    clk.note_gps_time(GPS_UTC + 60)
    ft.tick(10 * 60)
    closed = store.close_flight(flight["id"])
    assert closed["id"] == "F-20261010-1657"
    assert closed["started_at"] == "2026-10-13T13:40:00"
    assert closed["ended_at"] == "2026-10-13T13:51:00"
    assert closed["duration_s"] == 660
    assert closed["clock"]["source"] == "gps"
    assert closed["clock"]["start_corrected"]
    assert store.flight("F-20261010-1657")["started_at"] == "2026-10-13T13:40:00"


def test_a_start_stamped_by_a_clock_that_agreed_is_not_moved(tmp_path, clk, ft):
    clk.note_gps_time(ft.wall() + 5)
    store = flights_mod.Store(tmp_path, clock=clk)
    flight = store.open_flight()
    closed = store.close_flight(flight["id"])
    assert closed["started_at"] == flight["started_at"]
    assert "start_corrected" not in closed["clock"]


def test_a_photo_is_stamped_with_gps_time(tmp_path, clk):
    import queue
    clk.note_gps_time(GPS_UTC)
    store = flights_mod.Store(tmp_path, clock=clk)
    rec = recorder_mod.FlightRecorder(cam=None, store=store, settings={})
    rec._triggers = queue.Queue()
    rec.stats = {"triggers": 0, "missed": 0}
    assert rec.trigger({"lat": 14.1, "lon": 121.0})
    job = rec._triggers.get_nowait()
    assert job["when"] == datetime.datetime(2026, 10, 13, 13, 40, 0)
    assert job["clock"]["source"] == "gps"
    assert recorder_mod.new_capture_id(job["when"], 1) == "20261013_134000_0001"


# ── re-dating a flight recorded before this fix ─────────────────────────────

import json                                                  # noqa: E402
import sys                                                   # noqa: E402
from pathlib import Path                                     # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))
import fix_flight_clock                                      # noqa: E402

JUMP = 247_531        # what the 13 October log and photo 0028 both say


@pytest.fixture
def stale_flight(tmp_path):
    """F-20261010-1657 as it was stored: start and photos on the Pi's stale
    clock, ended_at after the clock was put right, photo 0002 timing itself
    across the jump. Plus an unrelated flight and ground capture."""
    old = "F-20261010-1657"
    (tmp_path / old).mkdir()
    (tmp_path / old / "thumb_20261010_165725_0001.jpg").write_text("x")
    flights = [
        {"id": old, "status": "ok", "started_at": "2026-10-10T16:57:06",
         "ended_at": "2026-10-13T14:05:34", "duration_s": 248908},
        {"id": "F-20261013-1518", "status": "ok",
         "started_at": "2026-10-13T15:18:01", "ended_at": "2026-10-13T15:42:18"},
    ]
    captures = [
        {"id": "20261010_165725_0001", "flight_id": old,
         "timestamp": "2026-10-10T16:57:25.316", "process_ms": 20999,
         "files": {"thumb": "thumb_20261010_165725_0001.jpg"}},
        {"id": "20261010_165727_0002", "flight_id": old,
         "timestamp": "2026-10-10T16:57:27.082", "process_ms": 247_550_631},
        {"id": "20261013_151900_0001", "flight_id": "F-20261013-1518",
         "timestamp": "2026-10-13T15:19:00.000"},
        {"id": "20261001_120000", "flight_id": None,
         "timestamp": "2026-10-01T12:00:00"},
    ]
    (tmp_path / "flights.json").write_text(json.dumps(flights))
    (tmp_path / "captures.json").write_text(json.dumps(captures))
    return tmp_path


def _fix(data, *extra):
    return fix_flight_clock.main(["F-20261010-1657", "--offset", str(JUMP),
                                  "--before", "2026-10-11", "--data", str(data),
                                  *extra])


def test_a_dry_run_writes_nothing(stale_flight):
    before = (stale_flight / "flights.json").read_text()
    _fix(stale_flight, "--new-id", "F-20261013-1342")
    assert (stale_flight / "flights.json").read_text() == before
    assert (stale_flight / "F-20261010-1657").is_dir()


def test_the_13_october_flight_is_re_dated_and_renamed(stale_flight):
    _fix(stale_flight, "--new-id", "F-20261013-1342", "--apply")
    flights = json.loads((stale_flight / "flights.json").read_text())
    caps = {c["id"]: c for c in
            json.loads((stale_flight / "captures.json").read_text())}
    f = next(f for f in flights if f["id"] == "F-20261013-1342")
    assert f["started_at"] == "2026-10-13T13:42:37"
    assert f["ended_at"] == "2026-10-13T14:05:34"      # was already right
    assert f["duration_s"] == 1377
    assert f["clock"] == {"source": "corrected", "offset_s": JUMP,
                          "renamed_from": "F-20261010-1657"}
    first = caps["20261010_165725_0001"]
    assert first["timestamp"] == "2026-10-13T13:42:56.316"
    assert first["flight_id"] == "F-20261013-1342"
    assert caps["20261010_165727_0002"]["process_ms"] == 19631
    # The folder moved and the photo still resolves through the store.
    assert not (stale_flight / "F-20261010-1657").exists()
    store = flights_mod.Store(stale_flight)
    assert store.file_path("20261010_165725_0001", "thumb").exists()
    # Nothing else was touched.
    assert caps["20261013_151900_0001"]["timestamp"] == "2026-10-13T15:19:00.000"
    assert caps["20261001_120000"]["timestamp"] == "2026-10-01T12:00:00"
    assert "clock" not in caps["20261013_151900_0001"]
    assert len(list(stale_flight.glob("*.bak-clockfix-*"))) == 2


def test_re_dating_twice_does_not_move_it_twice(stale_flight):
    _fix(stale_flight, "--apply")
    _fix(stale_flight, "--apply")
    flights = json.loads((stale_flight / "flights.json").read_text())
    assert flights[0]["started_at"] == "2026-10-13T13:42:37"


def test_it_will_not_rename_onto_an_existing_flight(stale_flight):
    with pytest.raises(SystemExit):
        _fix(stale_flight, "--new-id", "F-20261013-1518", "--apply")
    assert (stale_flight / "F-20261010-1657").is_dir()


def test_a_corrected_record_says_so():
    assert "corrected afterwards" in clock_mod.note(
        {"source": "corrected", "offset_s": JUMP})
