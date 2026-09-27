"""
Takes the mission's photos in the air, analyses them after landing.

The first real mission showed the old way could not keep up. Every trigger ran
the whole pipeline -- open the camera, three seconds of warm-up, grab, compute
BNDVI, render the figures -- on the MAVLink reader thread, which stopped reading
the flight controller for the five to ten seconds that took. A survey that asks
for a photo a second lost most of them.

So a flight is split in two, the way the thesis describes it:

  in the air     trigger -> queue -> grab one frame from the held camera
                 -> queue -> write the raw frame to disk
  after landing  each raw frame -> the unchanged analysis pipeline -> a record

Three threads, and none of them the MAVLink reader: `trigger()` only appends to a
queue, so the flight controller is always being listened to. Grabbing and writing
are separate so a slow SD card delays the disk, not the next photo -- the photo
has to be taken the moment the trigger arrives or it lands in the wrong place.

Raw frames are plain .npy (about 24 MB at 8 MP). Not JPEG: its compression works
on colour differences and would damage exactly the red/blue ratio BNDVI is
built on. Not compressed .npz: zlib on 24 MB is about as slow as the analysis we
are trying to get out of the air. They are deleted once analysed.

Dev mode goes through all of it -- synthetic frames are grabbed, queued, written
and analysed exactly like real ones.
"""

import datetime
import json
import logging
import queue
import threading
import time
from pathlib import Path

import numpy as np

import bndvi

log = logging.getLogger("hylocropter.recorder")

# Photos waiting to be grabbed. A backlog means the camera is behind the mission;
# past this it is so far behind that a late photo would be placed metres from
# where it was taken, so it is better counted as missed.
TRIGGER_BACKLOG = 8

# Frames waiting to be written, at ~24 MB each. Bounded so a stalled SD card runs
# out of queue rather than out of RAM.
WRITE_BACKLOG = 4

# How long stop() waits for the queues to drain after the drone disarms.
DRAIN_WAIT_S = 30.0

PENDING = "pending"
_STOP = object()


def pending_dir(store, flight_id):
    return Path(store.capture_dir(flight_id)) / PENDING


def pending_frames(store, flight_id):
    """Sidecars of frames taken but not yet analysed, oldest first."""
    d = pending_dir(store, flight_id)
    if not d.is_dir():
        return []
    return sorted(d.glob("*.json"))


def new_capture_id(when, number):
    """The shutter time plus the photo's number in the flight. Ids used to be
    the time to the second alone, so two photos in one second shared an id and
    the second overwrote the first one's files."""
    return when.strftime("%Y%m%d_%H%M%S_") + f"{number:04d}"


class FlightRecorder:
    def __init__(self, cam, store, settings):
        self.cam = cam
        self.store = store
        self.settings = settings
        self._lock = threading.Lock()
        self._flight_id = None
        self._triggers = None
        self._writes = None
        self._threads = []
        self._ready = threading.Event()
        self.stats = {}

    # ── the flight ────────────────────────────────────────────────────────

    def start(self, flight_id):
        """Hold the camera and start listening for triggers. Idempotent."""
        with self._lock:
            if self._flight_id == flight_id:
                return
            if self._flight_id is not None:
                raise RuntimeError(f"already recording {self._flight_id}")
            self._flight_id = flight_id
            self._triggers = queue.Queue(maxsize=TRIGGER_BACKLOG)
            self._writes = queue.Queue(maxsize=WRITE_BACKLOG)
            self._ready.clear()
            self.stats = {"flight_id": flight_id, "triggers": 0, "taken": 0,
                          "written": 0, "missed": 0, "last_error": None}
            self._threads = [
                threading.Thread(target=self._grabber, args=(flight_id,),
                                 name="recorder-grab", daemon=True),
                threading.Thread(target=self._writer, args=(flight_id,),
                                 name="recorder-write", daemon=True),
            ]
            for t in self._threads:
                t.start()
        log.info("recorder started for %s", flight_id)

    @property
    def flight_id(self):
        return self._flight_id

    def trigger(self, geo, trigger="distance"):
        """Called on the MAVLink thread for every camera trigger. Never blocks."""
        triggers = self._triggers
        if triggers is None:
            return False
        self.stats["triggers"] += 1
        when = datetime.datetime.now()
        try:
            triggers.put_nowait({"geo": geo, "trigger": trigger, "when": when,
                                 "number": self.stats["triggers"]})
            return True
        except queue.Full:
            self._miss(f"the camera is {TRIGGER_BACKLOG} photos behind the "
                       f"mission -- fly slower or space the photos further apart")
            return False

    def stop(self, timeout=DRAIN_WAIT_S):
        """Finish the photos already asked for, then let go of the camera."""
        with self._lock:
            flight_id, threads = self._flight_id, self._threads
            if flight_id is None:
                return
            self._triggers.put(_STOP)          # the grabber passes it on
        deadline = time.monotonic() + timeout
        for t in threads:
            t.join(max(0.1, deadline - time.monotonic()))
        if any(t.is_alive() for t in threads):
            log.warning("recorder for %s did not drain within %.0fs",
                        flight_id, timeout)
        with self._lock:
            self._flight_id = self._triggers = self._writes = None
            self._threads = []
        log.info("recorder stopped for %s: %s", flight_id, self.stats)

    def _miss(self, why):
        self.stats["missed"] += 1
        self.stats["last_error"] = why
        log.warning("Missed a mission photo: %s", why, extra={"activity": True})

    # ── the two workers ───────────────────────────────────────────────────

    def _grabber(self, flight_id):
        try:
            self.cam.begin_flight()
        except Exception as exc:
            # Keep going: grab_still() reopens on every photo, so a camera that
            # comes back mid-flight is still used.
            log.warning("Could not open the camera for the flight: %s", exc,
                        extra={"activity": True})
        self._ready.set()
        scene = self.settings.get("preview_scene", "mixed")
        try:
            while True:
                job = self._triggers.get()
                if job is _STOP:
                    break
                try:
                    rgb, report = self.cam.grab_still(scene=scene)
                except Exception as exc:
                    self._miss(f"the camera did not give a frame: {exc}")
                    continue
                self.stats["taken"] += 1
                job.update(rgb=rgb, report=report,
                           synthetic=self.cam.synthetic_requested(),
                           camera=_plain(self.settings.camera_kwargs()),
                           analysis=_plain(self.settings.analysis_kwargs()))
                self._writes.put(job)          # blocks only if the disk stalls
        finally:
            self._writes.put(_STOP)
            try:
                self.cam.end_flight()
            except Exception:
                log.exception("releasing the camera after the flight failed")

    def _writer(self, flight_id):
        out = pending_dir(self.store, flight_id)
        out.mkdir(parents=True, exist_ok=True)
        while True:
            job = self._writes.get()
            if job is _STOP:
                break
            capture_id = new_capture_id(job["when"], job["number"])
            try:
                np.save(out / f"{capture_id}.npy", job["rgb"],
                        allow_pickle=False)
                sidecar = {
                    "id": capture_id,
                    "timestamp": job["when"].isoformat(timespec="milliseconds"),
                    "geo": job["geo"],
                    "trigger": job["trigger"],
                    "synthetic": job["synthetic"],
                    "control_report": job["report"],
                    # What the settings were when the shutter fired, so changing
                    # them between landing and processing cannot rewrite it.
                    "camera": job["camera"],
                    "analysis": job["analysis"],
                }
                # Sidecar last and atomically: a frame only counts once both
                # halves are on disk, so a power cut mid-write leaves an orphan
                # .npy rather than a sidecar pointing at half a file.
                tmp = out / f"{capture_id}.json.tmp"
                tmp.write_text(json.dumps(sidecar))
                tmp.replace(out / f"{capture_id}.json")
                self.stats["written"] += 1
            except Exception as exc:
                self._miss(f"could not save the photo to disk: {exc}")


# ── after landing ────────────────────────────────────────────────────────────

def analyse_pending(store, flight_id, progress=None):
    """Run every waiting frame through the normal pipeline. Returns (ok, failed).

    A frame that fails is left where it is, so pressing Process again retries it
    rather than losing the photo.
    """
    sidecars = pending_frames(store, flight_id)
    out_dir = store.capture_dir(flight_id)
    ok = failed = 0
    for i, path in enumerate(sidecars, 1):
        try:
            meta = json.loads(path.read_text())
            frame = path.with_suffix(".npy")
            rgb = np.load(frame, allow_pickle=False)
            camera = dict(meta.get("camera") or {})
            for key in ("resolution", "colour_gains"):
                if key in camera:
                    camera[key] = tuple(camera[key])
            record = bndvi.capture_and_analyse(
                out_dir, rgb=rgb, capture_id=meta["id"],
                timestamp=meta.get("timestamp"),
                control_report=meta.get("control_report"),
                dev_mode=bool(meta.get("synthetic")),
                flight_id=flight_id, geo=meta.get("geo"),
                trigger=meta.get("trigger", "distance"),
                **{**(meta.get("analysis") or {}),
                   # The frame is already in hand, so there is no DNG to write.
                   "capture_format": "rgb888"},
                **camera)
            store.add_capture(record)
            store.attach_capture(flight_id, record["id"])
            frame.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            ok += 1
        except Exception as exc:
            failed += 1
            log.warning("Could not analyse photo %s: %s", path.stem, exc,
                        extra={"activity": True})
        if progress:
            progress(i, len(sidecars))
    return ok, failed


def _plain(d):
    """Settings as JSON-safe values (tuples become lists)."""
    return {k: (list(v) if isinstance(v, tuple) else v) for k, v in d.items()}
