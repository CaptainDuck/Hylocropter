"""The mission's photos: grabbed in the air, analysed after landing.

The first real mission lost most of its photos because every trigger ran the
whole pipeline -- camera open, warm-up, analysis, figures -- on the MAVLink
thread. These pin the split that replaced it, end to end, in dev mode: synthetic
frames go through the identical grab / write / analyse path as real ones.
"""

import threading
import time

import pytest

import camera as camera_mod
import flights as flights_mod
import recorder as recorder_mod


class Settings(dict):
    """Just enough of settings.Settings for the recorder and the camera."""

    def camera_kwargs(self):
        return {"resolution": (320, 240), "exposure_us": 5000, "gain": 2.0,
                "warmup_s": 0, "colour_gains": (1.0, 1.0)}

    def analysis_kwargs(self):
        return {"correct_nir_leakage": False, "nir_leak_coef": 0.35,
                "threshold_healthy": 0.3, "threshold_moderate": 0.1,
                "mask_low_signal": True, "min_signal": 10, "save_array": False,
                "capture_format": "rgb888", "neutralise_isp": True}


@pytest.fixture
def rig(tmp_path):
    settings = Settings(preview_fps=12, resolution=[320, 240], warmup_s=0,
                        preview_scene="healthy")
    cam = camera_mod.CameraService(settings, dev_mode=True)
    store = flights_mod.Store(tmp_path)
    flight = store.open_flight(name="test block")
    return cam, store, recorder_mod.FlightRecorder(cam, store, settings), flight


def _fly(rec, flight_id, photos):
    rec.start(flight_id)
    for i in range(photos):
        assert rec.trigger({"lat": 14.1265 + i * 1e-5, "lon": 121.0768,
                            "source": "mavlink"})
        time.sleep(0.05)
    rec.stop()


def test_every_trigger_becomes_a_photo_and_then_a_record(rig):
    cam, store, rec, flight = rig
    _fly(rec, flight["id"], 5)

    assert rec.stats["taken"] == 5 and rec.stats["missed"] == 0
    assert len(recorder_mod.pending_frames(store, flight["id"])) == 5
    assert store.captures(flight_id=flight["id"]) == [], \
        "nothing is analysed in the air"

    ok, failed = recorder_mod.analyse_pending(store, flight["id"])
    assert (ok, failed) == (5, 0)
    records = store.captures(flight_id=flight["id"], newest_first=False)
    assert len(records) == 5
    assert len({r["id"] for r in records}) == 5, "ids must not collide"
    assert [r["geo"]["lat"] for r in records] == sorted(
        r["geo"]["lat"] for r in records), "photos keep their own positions"
    assert all(r["settings"]["dev_mode"] for r in records)
    assert recorder_mod.pending_frames(store, flight["id"]) == [], \
        "raw frames are cleared once analysed"


def test_the_trigger_never_blocks_the_mavlink_thread(rig, monkeypatch):
    """Even with the camera stuck, trigger() returns at once."""
    cam, store, rec, flight = rig
    stuck = threading.Event()
    monkeypatch.setattr(cam, "grab_still",
                        lambda scene="mixed": stuck.wait(10) or (None, {}))
    rec.start(flight["id"])
    started = time.monotonic()
    for _ in range(recorder_mod.TRIGGER_BACKLOG + 5):
        rec.trigger(None)
    assert time.monotonic() - started < 0.5
    assert rec.stats["missed"] >= 4, "a hopeless backlog is counted as missed"
    stuck.set()
    rec.stop()


def test_a_camera_failure_is_a_missed_photo_not_a_dead_flight(rig, monkeypatch):
    cam, store, rec, flight = rig
    real = cam.grab_still
    calls = []

    def flaky(scene="mixed"):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("camera delivered no frame")
        return real(scene=scene)

    monkeypatch.setattr(cam, "grab_still", flaky)
    _fly(rec, flight["id"], 3)
    assert rec.stats["taken"] == 2 and rec.stats["missed"] == 1


def test_the_camera_is_given_back_to_the_preview_after_landing(rig):
    cam, store, rec, flight = rig
    rec.start(flight["id"])
    for _ in range(50):
        if cam.flying:
            break
        time.sleep(0.01)
    assert cam.flying
    rec.stop()
    assert not cam.flying


def test_settings_changed_after_landing_do_not_rewrite_the_photos(rig):
    cam, store, rec, flight = rig
    _fly(rec, flight["id"], 1)
    rec.settings.analysis_kwargs = lambda: {"threshold_healthy": 0.9}
    recorder_mod.analyse_pending(store, flight["id"])
    record = store.captures(flight_id=flight["id"])[0]
    assert record["settings"]["threshold_healthy"] == 0.3


def test_a_frame_that_cannot_be_analysed_is_kept_for_a_retry(rig):
    cam, store, rec, flight = rig
    _fly(rec, flight["id"], 2)
    first = recorder_mod.pending_frames(store, flight["id"])[0]
    first.with_suffix(".npy").write_bytes(b"not a frame")
    ok, failed = recorder_mod.analyse_pending(store, flight["id"])
    assert (ok, failed) == (1, 1)
    assert recorder_mod.pending_frames(store, flight["id"]) == [first]
