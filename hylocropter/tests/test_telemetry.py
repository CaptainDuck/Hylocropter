"""What happens to the snapshot when the flight controller reboots under us.

The Pixhawk talks to the Pi over TELEM2 on GPIO 14/15 — a memory-mapped PL011,
not a USB adapter. That distinction is the whole reason this file exists. A USB
serial device disappears when the controller powers down, `recv_match` raises,
and `_run()` tears the connection down and rebuilds it through `_connect()`. A
hardware UART does none of that: the port stays open, `_pump()` just sees a
minute of silence, and `_connect()` — the only place that asks for stream rates
and reads the mission — never runs again.

So the link recovers on its own and *looks* healthy, while the mission readback
silently describes the mission the controller was holding before the reboot.
That is the dangerous shape of this bug: not a visible failure, but confident
stale data about which plan the aircraft is flying.
"""

import time

import pytest

import telemetry as telemetry_mod
from telemetry import MISSION_REREAD_ATTEMPTS, MISSION_REREAD_DELAY_S


class Msg:
    def __init__(self, kind, src_system=1, **fields):
        self._kind = kind
        self._src = src_system
        self.__dict__.update(fields)

    def get_type(self):
        return self._kind

    def get_srcSystem(self):
        return self._src


class RecordingMav:
    """Stands in for pymavlink's `conn.mav`, counting what was asked for."""

    def __init__(self):
        self.mission_list_requests = 0
        self.stream_requests = 0
        self.item_requests = []

    def mission_request_list_send(self, *a, **kw):
        self.mission_list_requests += 1

    def request_data_stream_send(self, *a, **kw):
        self.stream_requests += 1

    def mission_request_int_send(self, sysid, compid, seq):
        self.item_requests.append(seq)


class FakeConn:
    def __init__(self):
        self.mav = RecordingMav()
        self.target_system = 1
        self.target_component = 1


def heartbeat(mode=0, base_mode=81, src_system=1):
    # base_mode 81 is what ArduPilot sends disarmed; bit 0x80 is the arm flag.
    return Msg("HEARTBEAT", src_system=src_system,
               base_mode=base_mode, custom_mode=mode)


def gcs_heartbeat():
    """What Mission Planner puts on the bus: system 255, MAV_TYPE_GCS.

    base_mode 192 is 0x80 | 0x40. The 0x80 is MAV_MODE_FLAG_SAFETY_ARMED, which
    is why taking a station's heartbeat at face value reads as an armed
    aircraft -- but those flags describe the station.
    """
    return heartbeat(base_mode=192, src_system=255)


@pytest.fixture
def svc():
    s = telemetry_mod.TelemetryService({"mavlink_connection": "/dev/ttyAMA0",
                                        "mavlink_baud": 57600})
    s._conn = FakeConn()
    return s


def test_steady_link_does_not_re_read(svc):
    """The common case must stay quiet — one heartbeat a second, no re-reads."""
    for _ in range(5):
        svc._handle(heartbeat())
    assert svc._conn.mav.mission_list_requests == 0
    assert svc._conn.mav.stream_requests == 0


def test_reboot_gap_triggers_a_re_read(svc):
    """A heartbeat after a gap means a new controller state — re-read it."""
    svc._handle(heartbeat())
    assert svc._conn.mav.mission_list_requests == 0

    # The controller goes away and comes back. Rewinding the clock we hold is
    # exactly what a power cycle looks like from this side: the port never
    # closed, we simply heard nothing for a while.
    svc._last_heartbeat -= telemetry_mod.HEARTBEAT_TIMEOUT_S + 5

    svc._handle(heartbeat())
    assert svc._conn.mav.mission_list_requests == 1
    assert svc._conn.mav.stream_requests == 1, "stream rates reset on reboot too"


def test_mission_answer_stops_the_retries(svc):
    """MISSION_COUNT is the only acknowledgement the protocol gives us."""
    svc._last_heartbeat = time.time() - 60
    svc._handle(heartbeat())
    assert svc._mission_retries == MISSION_REREAD_ATTEMPTS

    svc._handle(Msg("MISSION_COUNT", count=4))
    assert svc._mission_retries == 0

    # And no amount of pumping asks again.
    before = svc._conn.mav.mission_list_requests
    svc._mission_asked_at -= MISSION_REREAD_DELAY_S * 10
    svc._retry_mission_read()
    assert svc._conn.mav.mission_list_requests == before


def test_unanswered_re_read_is_retried_then_gives_up(svc):
    """A controller mid-boot heartbeats before it will serve the mission.

    There is no error to catch — the request is simply never answered — so the
    absence of a MISSION_COUNT is the only signal, and asking again is the only
    remedy. It must not become an unbounded retry loop against a controller
    that genuinely has nothing to say.
    """
    svc._last_heartbeat = time.time() - 60
    svc._handle(heartbeat())
    asked = svc._conn.mav.mission_list_requests

    for _ in range(MISSION_REREAD_ATTEMPTS + 3):
        svc._mission_asked_at -= MISSION_REREAD_DELAY_S + 1
        svc._retry_mission_read()

    assert svc._conn.mav.mission_list_requests == asked + MISSION_REREAD_ATTEMPTS
    assert svc._mission_retries == 0


def test_retry_respects_the_delay(svc):
    """Don't machine-gun the controller between attempts."""
    svc._last_heartbeat = time.time() - 60
    svc._handle(heartbeat())
    asked = svc._conn.mav.mission_list_requests

    svc._retry_mission_read()          # too soon, we only just asked
    assert svc._conn.mav.mission_list_requests == asked


def test_cleared_mission_drops_its_geometry(svc):
    """A mission that has been wiped must not keep the old numbers.

    `_update_mission_geometry()` returns early below two waypoints, so nothing
    else would ever clear these — the pre-flight card would go on offering the
    altitude and line spacing of a mission the controller no longer holds.
    """
    svc._snap["mission"].update({"count": 4, "loaded": True,
                                 "altitude_m": 3.0, "line_spacing_m": 11.6})

    svc._handle(Msg("MISSION_COUNT", count=0))

    m = svc.snapshot()["mission"]
    assert m["count"] == 0
    assert m["loaded"] is False
    assert m["altitude_m"] is None
    assert m["line_spacing_m"] is None


def test_a_real_mission_still_reads_back(svc):
    """The fix must not break the ordinary readback path."""
    svc._handle(Msg("MISSION_COUNT", count=3))
    assert svc._conn.mav.item_requests == [0, 1, 2]
    assert svc.snapshot()["mission"]["loaded"] is True


# ── a ground station sharing the link ────────────────────────────────────────
#
# Observed on the bench on 2026-09-26: Mission Planner connected alongside the
# Pi put two heartbeat sources on the same wire.
#
#   sys 1   comp 1    type=2 autopilot=3   base_mode=81    armed=False
#   sys 255 comp 190  type=6 autopilot=8   base_mode=192   armed=True
#
# The dashboard read ARMED off a disarmed aircraft, because whichever heartbeat
# landed last won.


def test_a_ground_station_heartbeat_is_not_the_aircraft(svc):
    svc._handle(heartbeat())
    assert svc.snapshot()["armed"] is False

    svc._handle(gcs_heartbeat())
    assert svc.snapshot()["armed"] is False, \
        "Mission Planner's base_mode 0x80 must not read as an armed airframe"


def test_a_ground_station_cannot_open_a_flight(svc):
    """The consequence that matters: _on_arm_change drives flight recording.

    Left unfiltered this fires about once a second with a station connected --
    opening a flight on its heartbeat and closing it, which starts processing,
    on the aircraft's.
    """
    events = []
    svc.on_arm_change = lambda armed, snap: events.append(armed)

    svc._handle(heartbeat())
    for _ in range(5):
        svc._handle(gcs_heartbeat())
        svc._handle(heartbeat())

    assert events == [], "no arm transition should have been reported"


def test_the_aircraft_really_can_arm(svc):
    """The filter must not make us deaf to a genuine arming."""
    events = []
    svc.on_arm_change = lambda armed, snap: events.append(armed)

    svc._handle(heartbeat())
    svc._handle(heartbeat(base_mode=81 | 0x80))     # same source, now armed
    assert svc.snapshot()["armed"] is True
    assert events == [True]

    svc._handle(heartbeat())
    assert svc.snapshot()["armed"] is False
    assert events == [True, False]


def test_other_traffic_from_a_station_is_ignored_too(svc):
    """Not just heartbeats -- nothing from another system describes our drone."""
    svc._handle(Msg("MISSION_COUNT", count=4))
    assert svc.snapshot()["mission"]["count"] == 4

    svc._handle(Msg("MISSION_COUNT", src_system=255, count=99))
    assert svc.snapshot()["mission"]["count"] == 4


def test_messages_pass_before_a_target_is_latched(svc):
    """pymavlink sets target_system from the first non-GCS heartbeat.

    Until then there is nothing to compare against, and dropping everything
    would mean never connecting at all.
    """
    svc._conn.target_system = 0
    svc._handle(heartbeat(src_system=7))
    assert svc.snapshot()["mode"] == "STABILIZE"
