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
