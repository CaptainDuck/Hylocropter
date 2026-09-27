"""The one thing that must never happen: the camera taking the app down with it.

There is no camera in CI, and the parts that talk to picamera2 cannot be tested
here. What *can* be tested — and what has now broken three times on real
hardware — is the failure handling around it. A ribbon knocked while the drone is
handled makes the sensor stop answering, and every one of those three bugs turned
that into a permanently frozen dashboard rather than a visible error:

  1. the preview asked for a stream format the Pi 4 cannot produce, so the first
     frame always failed;
  2. the failed camera object was never closed, so every retry after it died;
  3. capture_request() waited forever, so a stalled sensor froze the loop.

And then the fix for (3) moved the hang into the cleanup, which is what these
pin. All of them are about one property: the preview thread must always come
back and release its lock, whatever the hardware does.
"""

import threading
import time

import pytest

import camera as camera_mod


class HangingCamera:
    """A picamera2 that has stopped answering, like a sensor off the CSI bus.

    Both stop() and close() block forever: picamera2 dispatches them to the
    camera event loop and waits with no timeout, and that loop is gone.
    """

    def __init__(self):
        self.stop_called = threading.Event()

    def stop(self):
        self.stop_called.set()
        time.sleep(3600)

    def close(self):                                    # pragma: no cover
        time.sleep(3600)


class Polite:
    """A camera that shuts down normally."""

    def __init__(self):
        self.stopped = False
        self.closed = False

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


@pytest.fixture
def service():
    return camera_mod.CameraService({"preview_fps": 12}, dev_mode=True)


def test_closing_a_wedged_camera_gives_up_instead_of_blocking(service):
    """The bug this file exists for. The frame timeout fired, then _close_locked
    blocked forever inside Picamera2.stop() while holding the lock — trading a
    stuck capture for a stuck cleanup, which looks identical from the outside."""
    hung = HangingCamera()
    service._picam = hung

    started = time.monotonic()
    service._close_locked()
    took = time.monotonic() - started

    assert hung.stop_called.wait(1.0), "it should still have tried to stop it"
    assert took < camera_mod.CLOSE_WAIT_S + 1.5, (
        f"_close_locked took {took:.1f}s; a camera that will not shut down must "
        f"be abandoned, not waited on")


def test_the_camera_is_let_go_of_even_when_shutdown_hangs(service):
    """Whatever happened to the device, the service must not still be holding a
    reference to it — the next pass has to be free to try again."""
    service._picam = HangingCamera()
    service._close_locked()
    assert service._picam is None


def test_the_lock_is_free_afterwards(service):
    """The real damage was never the camera: it was that the preview thread held
    _lock while it hung, so every capture queued behind it froze too."""
    service._picam = HangingCamera()
    service._close_locked()
    assert service._lock.acquire(blocking=False), "the capture lock must be free"
    service._lock.release()


def test_a_healthy_camera_is_still_shut_down_properly(service):
    """The bounded wait must not turn into 'never bother stopping the camera'.
    Leaving a working device running would leak it just as surely."""
    cam = Polite()
    service._picam = cam
    service._close_locked()
    for _ in range(50):
        if cam.stopped and cam.closed:
            break
        time.sleep(0.02)
    assert cam.stopped and cam.closed
    assert service._picam is None


def test_closing_when_there_is_no_camera_is_harmless(service):
    service._picam = None
    service._close_locked()
    assert service._picam is None


# ── captures and the preview ─────────────────────────────────────────────────
# From the first real mission: one real photo, then every other capture in the
# flight was a synthetic frame filed as a real photo at a real GPS position.

@pytest.fixture
def real():
    """A service that is *not* in dev mode, as on the Pi."""
    return camera_mod.CameraService({"preview_fps": 12}, dev_mode=False)


def test_a_capture_never_goes_synthetic_just_because_the_camera_looked_missing(
        real, monkeypatch):
    """The preview may fall back to generated frames; a capture may not. Without
    a camera it has to fail, not quietly invent a plot."""
    monkeypatch.setattr(camera_mod.bndvi, "probe_camera",
                        lambda: {"available": False, "detail": "enumeration hiccup"})
    assert real.using_synthetic(), "the preview still falls back, and says so"
    assert not real.synthetic_requested()


def test_synthetic_captures_still_happen_when_asked_for(real, service):
    assert service.synthetic_requested()                 # dev mode
    real.use_synthetic(True)                             # the Debug button
    assert real.synthetic_requested()


def test_the_probe_does_not_enumerate_while_the_camera_is_in_use(real, monkeypatch):
    """Enumerating mid-capture raced libcamera and came back 'no camera'."""
    calls = []
    monkeypatch.setattr(camera_mod.bndvi, "probe_camera",
                        lambda: calls.append(1) or {"available": True})
    real.probe(force=True)
    assert len(calls) == 1

    with real._lock:
        info = real.probe(force=True)
    assert len(calls) == 1, "it enumerated while a capture held the camera"
    assert info["available"]


def test_a_capture_waits_out_a_preview_frame_instead_of_being_dropped(real):
    """The preview holds the lock for most of every frame, so refusing whenever
    it was taken lost about every other mission trigger."""
    real._lock.acquire()                                 # a preview frame in flight
    threading.Timer(0.3, real._lock.release).start()

    acquired, result = real.capture_locked(lambda: "photo")
    assert acquired and result == "photo"


def test_a_second_capture_is_still_refused_rather_than_queued(real):
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(5)
        return "first"

    first = threading.Thread(target=real.capture_locked, args=(slow,))
    first.start()
    assert started.wait(2)
    try:
        assert real.capture_locked(lambda: "second") == (False, None)
    finally:
        release.set()
        first.join(5)


def test_a_preview_that_never_lets_go_fails_the_capture_visibly(real, monkeypatch):
    monkeypatch.setattr(camera_mod, "CAPTURE_WAIT_S", 0.2)
    real._lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="busy"):
            real.capture_locked(lambda: "never")
    finally:
        real._lock.release()
    assert not real._paused.is_set(), "the preview must be allowed to resume"
    assert real.capture_locked(lambda: "later") == (True, "later")


# ── flight mode ──────────────────────────────────────────────────────────────

class FakeStill:
    """Enough of Picamera2 for the held still configuration."""

    camera_controls = {}

    def __init__(self):
        self.started = self.closed = False
        self.frames = 0

    def create_still_configuration(self, **kw):
        return kw

    def configure(self, config):
        self.config = config

    def start(self):
        self.started = True

    def capture_request(self, wait=True):
        return "job"

    def wait(self, job, timeout=None):
        self.frames += 1
        cam = self

        class Request:
            def make_array(self, name):
                import numpy as np
                return np.full((48, 64, 3), 100 + cam.frames, np.uint8)

            def get_metadata(self):
                return {}

            def release(self):
                pass
        return Request()

    def stop(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def held(monkeypatch):
    opened = []

    def open_camera(neutralise_isp=True):
        opened.append(FakeStill())
        return opened[-1]

    monkeypatch.setattr(camera_mod.bndvi, "open_camera", open_camera)
    svc = camera_mod.CameraService(
        {"preview_fps": 12, "resolution": [64, 48], "warmup_s": 0,
         "gain": 2.0, "exposure_us": 5000, "colour_gains": [1.0, 1.0]},
        dev_mode=False)
    return svc, opened


def test_a_flight_opens_the_camera_once_not_once_per_photo(held):
    """The whole point: the warm-up is paid per flight, not per trigger."""
    svc, opened = held
    svc.begin_flight()
    frames = [svc.grab_still()[0] for _ in range(5)]
    assert len(opened) == 1
    assert opened[0].config["main"]["size"] == (64, 48), "full still config"
    assert len({int(f[0, 0, 0]) for f in frames}) == 5, "a fresh frame each time"
    svc.end_flight()
    assert opened[0].closed and not svc.flying


def test_a_dropped_camera_is_reopened_mid_flight(held):
    svc, opened = held
    svc.begin_flight()
    svc.grab_still()
    svc.restart()                               # someone pressed Restart in Debug
    svc.grab_still()
    assert len(opened) == 2
    svc.end_flight()


def test_a_one_off_capture_is_refused_while_a_flight_holds_the_camera(held):
    svc, opened = held
    svc.begin_flight()
    try:
        with pytest.raises(RuntimeError, match="flight"):
            svc.capture_locked(lambda: "photo")
    finally:
        svc.end_flight()
    assert svc.capture_locked(lambda: "photo") == (True, "photo")
