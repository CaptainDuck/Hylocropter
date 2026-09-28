"""
MAVLink telemetry from the Pixhawk.

The Pi is a passenger. Mission Planner flies the aircraft; this module only
*reads* — link state, flight mode, arm state, battery, GPS fix, position, the
loaded mission, and camera-trigger events. It never arms, never commands, never
uploads a mission. That boundary is deliberate (see DEPLOYMENT.md) and should
not be relaxed.

Design constraints that shaped this:

* **Nothing here may block or crash the dashboard.** pymavlink missing, no
  serial port, wrong baud, or a silent bus all resolve to the same
  "not connected" snapshot. The Debug view has to keep working on a bench with
  no drone attached.
* **One reader thread, one snapshot.** The UI polls a plain dict. No queues, no
  per-request connections.
* **Staleness is explicit.** A link that stops mid-flight looks identical to a
  live one if you only check "did we ever connect", so every snapshot carries
  the age of the last heartbeat.

⚠️ UNVERIFIED AGAINST HARDWARE. There was no flight controller and no SITL
available when this was written, so the not-connected paths are well tested but
the message decoding is not. Test against ArduPilot SITL over UDP first — set
`mavlink_connection` to `udp:127.0.0.1:14550` in Settings. See RESEARCH-GAPS.md
section 7.
"""

import logging
import threading
import time

log = logging.getLogger("hylocropter.telemetry")

# No heartbeat for this long and we call the link dead. ArduPilot sends
# HEARTBEAT at 1 Hz, so 3 s is three missed beats.
HEARTBEAT_TIMEOUT_S = 3.0
RECONNECT_DELAY_S = 5.0

# A controller that has just rebooted starts heartbeating before it will answer
# the mission protocol, so the first re-read after a reboot can be dropped with
# no reply and no error. Ask again a few times before giving up.
MISSION_REREAD_ATTEMPTS = 3
MISSION_REREAD_DELAY_S = 3.0

# The controller never tells us a ground station uploaded a new mission -- that
# conversation happens on another link. So while the drone is on the ground, ask
# again every so often. A 50-item survey is about 3 KB, roughly 5% of a 57600
# baud link for one second in every ten. Never while armed: in flight the link is
# for triggers and position, and nobody uploads a new plan mid-survey.
MISSION_POLL_S = 10.0

# Mission items that are places to fly through. Everything else in a survey --
# DO_SET_CAM_TRIGG_DIST, DO_CHANGE_SPEED -- carries no position.
MAV_CMD_NAV_WAYPOINT = 16

# ArduPilot copter mode numbers -> names, for the modes this project sees.
# AUTO is the one that matters: a mission is running.
COPTER_MODES = {
    0: "STABILIZE", 1: "ACROBATIC", 2: "ALT_HOLD", 3: "AUTO", 4: "GUIDED",
    5: "LOITER", 6: "RTL", 7: "CIRCLE", 9: "LAND", 16: "POSHOLD",
    17: "BRAKE", 20: "GUIDED_NOGPS", 21: "SMART_RTL",
}

GPS_FIX_LABELS = {
    0: "no GPS", 1: "no fix", 2: "2D fix", 3: "3D fix",
    4: "3D DGPS", 5: "RTK float", 6: "RTK fixed",
}


def _blank_snapshot():
    return {
        "connected": False,
        "status": "disconnected",     # disconnected|connecting|connected|stale
        "detail": "not connected",
        "connection": None,
        "armed": False,
        "mode": None,
        "battery_pct": None,
        "battery_v": None,
        "gps": {"fix_type": 0, "fix_label": "no GPS", "satellites": 0,
                "hdop": None},
        "position": None,             # {lat, lon, alt_m, rel_alt_m, heading_deg}
        "mission": {"count": 0, "current": 0, "altitude_m": None,
                    "line_spacing_m": None, "loaded": False},
        "trigger_count": 0,
        "last_heartbeat_age_s": None,
        "messages_seen": 0,
    }


class TelemetryService:
    """Background MAVLink reader exposing a single snapshot dict."""

    def __init__(self, settings, on_trigger=None, on_arm_change=None):
        self.settings = settings
        self.on_trigger = on_trigger
        self.on_arm_change = on_arm_change
        self._lock = threading.Lock()
        self._snap = _blank_snapshot()
        self._thread = None
        self._stop = threading.Event()
        self._conn = None
        self._last_heartbeat = 0.0
        self._armed = False
        self._mission_items = {}
        self._mission_retries = 0
        self._mission_asked_at = 0.0
        self._mission_id = None        # the controller's own mission checksum
        self._unavailable_reason = None
        self._seen_feedback = False
        self._last_img_idx = None

    # ── lifecycle ─────────────────────────────────────────────────────────

    def start(self):
        if not self.settings.get("mavlink_enabled", True):
            self._set(status="disabled", detail="MAVLink is switched off in Settings")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mavlink",
                                        daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._close()

    def reconnect(self):
        """The 'Reconnect to the drone' action."""
        log.info("MAVLink reconnect requested")
        self._close()
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None
        self._set(**_blank_snapshot())
        self.start()
        return self.snapshot()

    # ── snapshot ──────────────────────────────────────────────────────────

    def snapshot(self):
        """Current telemetry, with staleness resolved at read time."""
        with self._lock:
            snap = dict(self._snap)
            snap["gps"] = dict(snap["gps"])
            snap["mission"] = dict(snap["mission"])
            if snap["position"]:
                snap["position"] = dict(snap["position"])

        if self._last_heartbeat:
            age = time.time() - self._last_heartbeat
            snap["last_heartbeat_age_s"] = round(age, 1)
            if age > HEARTBEAT_TIMEOUT_S and snap["status"] == "connected":
                # Held data is still shown -- the mockup's "Showing the last
                # data saved on this device" banner -- but flagged as stale.
                snap["status"] = "stale"
                snap["connected"] = False
                snap["detail"] = f"no heartbeat for {age:.0f}s"
        return snap

    def _set(self, **fields):
        with self._lock:
            self._snap.update(fields)

    # ── the reader ────────────────────────────────────────────────────────

    def _run(self):
        while not self._stop.is_set():
            if not self._connect():
                time.sleep(RECONNECT_DELAY_S)
                continue
            try:
                self._pump()
            except Exception as exc:
                log.warning("MAVLink read failed: %s", exc)
                self._set(status="disconnected", connected=False,
                          detail=f"link error: {exc}")
                self._close()
                time.sleep(RECONNECT_DELAY_S)

    def _connect(self):
        try:
            from pymavlink import mavutil
        except ImportError:
            if self._unavailable_reason != "pymavlink":
                log.warning("pymavlink is not installed — telemetry disabled")
                self._unavailable_reason = "pymavlink"
            self._set(status="unavailable", connected=False,
                      detail="pymavlink is not installed (pip install pymavlink)")
            self._stop.wait(30)
            return False

        target = str(self.settings.get("mavlink_connection"))
        baud = int(self.settings.get("mavlink_baud", 57600))
        # A bare device path means serial; anything with a scheme is passed
        # straight through, so udp:127.0.0.1:14550 works for SITL.
        device = target if ":" in target else target
        self._set(status="connecting", connected=False, connection=target,
                  detail=f"opening {target}")
        try:
            self._conn = mavutil.mavlink_connection(
                device, baud=baud, source_system=255, autoreconnect=False)
        except Exception as exc:
            self._set(status="disconnected", connected=False,
                      detail=f"cannot open {target}: {exc}")
            return False

        # Wait for a heartbeat rather than assuming an open port means a drone.
        # A serial device that exists but has nothing on it is the common case
        # on a bench, and it must not look like success.
        try:
            hb = self._conn.wait_heartbeat(timeout=6)
        except Exception as exc:
            hb = None
            log.debug("wait_heartbeat raised: %s", exc)
        if hb is None:
            self._set(status="disconnected", connected=False,
                      detail=f"opened {target} but no heartbeat — is the flight "
                             f"controller powered and the baud right?")
            self._close()
            return False

        self._last_heartbeat = time.time()
        self._set(status="connected", connected=True,
                  detail=f"connected on {target}")
        log.info("MAVLink connected on %s (system %s)", target,
                 self._conn.target_system)
        self._request_streams()
        self._mission_retries = MISSION_REREAD_ATTEMPTS
        self._request_mission()
        return True

    def _request_streams(self):
        """Ask for the data we need at a modest rate.

        Deliberately low: the Pi is also running the camera and the web app, and
        4 Hz position is far more than enough to geotag a photo.
        """
        try:
            from pymavlink import mavutil
            self._conn.mav.request_data_stream_send(
                self._conn.target_system, self._conn.target_component,
                mavutil.mavlink.MAV_DATA_STREAM_ALL, 4, 1)
        except Exception as exc:
            log.debug("could not request data streams: %s", exc)

    def _request_mission(self):
        self._mission_asked_at = time.time()
        try:
            self._conn.mav.mission_request_list_send(
                self._conn.target_system, self._conn.target_component)
        except Exception as exc:
            log.debug("could not request mission list: %s", exc)

    def _retry_mission_read(self):
        """Re-ask for the mission if a post-reboot read went unanswered.

        There is no error to catch here — a request the controller was not yet
        ready for is simply never answered — so the only signal is the absence
        of a MISSION_COUNT, and the only remedy is to ask again.
        """
        if not self._mission_retries:
            return
        if time.time() - self._mission_asked_at < MISSION_REREAD_DELAY_S:
            return
        self._mission_retries -= 1
        log.debug("mission re-read unanswered, %d attempt(s) left",
                  self._mission_retries)
        self._request_mission()

    def _poll_mission(self):
        """Re-read the mission now and then, so an upload shows up on its own.

        Rather than rebooting the Pi to see what Mission Planner or
        QGroundControl just sent. Skipped while armed and while a read is still
        being retried.
        """
        if self._armed or self._mission_retries:
            return
        if time.time() - self._mission_asked_at < MISSION_POLL_S:
            return
        self._request_mission()

    def _pump(self):
        seen = 0
        while not self._stop.is_set():
            self._retry_mission_read()
            self._poll_mission()
            msg = self._conn.recv_match(blocking=True, timeout=1.0)
            if msg is None:
                # Timeout is normal; snapshot() decides if that means stale.
                continue
            seen += 1
            self._handle(msg)
            if seen % 20 == 0:
                self._set(messages_seen=seen)

    def _handle(self, msg):
        # Not everything on this bus is the aircraft. A ground station sharing
        # the link -- Mission Planner announces itself as system 255, component
        # 190, MAV_TYPE_GCS -- heartbeats at 1 Hz with base_mode flags of its
        # own, and those describe the station, not the airframe. Ours arrives
        # with 0x80 set, which decodes as ARMED. Accepting both sources flips
        # the snapshot every second and, far worse, makes _on_arm_change() open
        # a flight on one heartbeat and close it (starting processing) on the
        # next. Take the aircraft's traffic and nothing else.
        #
        # target_system is latched by pymavlink from the first non-GCS
        # heartbeat, so it is the autopilot even when a station heartbeats
        # first; until it is set there is nothing to compare against and we let
        # messages through rather than going deaf.
        target = getattr(self._conn, "target_system", 0)
        if target and msg.get_srcSystem() != target:
            return

        kind = msg.get_type()

        if kind == "HEARTBEAT":
            # A controller reboot never closes this port — it is a memory-
            # mapped PL011, not a USB adapter that vanishes — so _pump() keeps
            # running and _connect() does not, which means the stream and
            # mission requests made there are never re-issued. Spot the gap
            # here instead: a rebooted controller holds whatever mission it now
            # has, and without a re-read the snapshot would keep reporting the
            # previous one for the rest of the session.
            now = time.time()
            gap = now - self._last_heartbeat if self._last_heartbeat else 0.0
            resumed = gap > HEARTBEAT_TIMEOUT_S
            self._last_heartbeat = now
            armed = bool(msg.base_mode & 0x80)   # MAV_MODE_FLAG_SAFETY_ARMED
            mode = COPTER_MODES.get(msg.custom_mode, f"mode {msg.custom_mode}")
            self._set(status="connected", connected=True, armed=armed, mode=mode,
                      detail=f"connected — {mode}")
            if armed != self._armed:
                self._armed = armed
                log.info("aircraft %s", "ARMED" if armed else "DISARMED")
                if self.on_arm_change:
                    try:
                        self.on_arm_change(armed, self.snapshot())
                    except Exception:
                        log.exception("arm-change handler failed")
            if resumed:
                log.info("link resumed after %.0fs without a heartbeat — "
                         "re-reading streams and mission", gap)
                self._request_streams()
                self._mission_retries = MISSION_REREAD_ATTEMPTS
                self._request_mission()

        elif kind in ("SYS_STATUS", "BATTERY_STATUS"):
            pct = getattr(msg, "battery_remaining", None)
            volts = getattr(msg, "voltage_battery", None)
            if kind == "BATTERY_STATUS":
                cells = [v for v in getattr(msg, "voltages", []) if 0 < v < 65535]
                volts = sum(cells) if cells else None
            self._set(
                battery_pct=(pct if pct not in (None, -1) else None),
                battery_v=(round(volts / 1000.0, 2)
                           if volts not in (None, 0, 65535) else None),
            )

        elif kind == "GPS_RAW_INT":
            hdop = getattr(msg, "eph", None)
            self._set(gps={
                "fix_type": msg.fix_type,
                "fix_label": GPS_FIX_LABELS.get(msg.fix_type,
                                                f"fix {msg.fix_type}"),
                "satellites": msg.satellites_visible,
                # eph is cm of horizontal dilution; report it in metres.
                "hdop": (round(hdop / 100.0, 2)
                         if hdop not in (None, 65535) else None),
            })

        elif kind == "GLOBAL_POSITION_INT":
            # lat/lon are 1e7-scaled degrees; altitudes are millimetres;
            # hdg is centidegrees.
            self._set(position={
                "lat": msg.lat / 1e7,
                "lon": msg.lon / 1e7,
                "alt_m": round(msg.alt / 1000.0, 2),
                "rel_alt_m": round(msg.relative_alt / 1000.0, 2),
                "heading_deg": (round(msg.hdg / 100.0, 1)
                                if msg.hdg != 65535 else None),
            })

        elif kind == "MISSION_COUNT":
            self._mission_retries = 0
            # Newer ArduPilot stamps each mission with a checksum. When it has
            # one and it has not changed, the items have not either, and the
            # poll costs one message instead of a full download.
            opaque = getattr(msg, "opaque_id", 0) or None
            unchanged = (opaque is not None and opaque == self._mission_id
                         and len(self._mission_items) == msg.count)
            if opaque is not None:
                self._mission_id = opaque
            if unchanged:
                return
            if msg.count != self.snapshot()["mission"]["count"]:
                log.info("mission on the controller: %d items", msg.count)
            with self._lock:
                self._snap["mission"]["count"] = msg.count
                self._snap["mission"]["loaded"] = msg.count > 0
                if not msg.count:
                    # _update_mission_geometry() bails out below two points, so
                    # a mission that has been cleared would otherwise keep
                    # reporting the altitude and spacing of the old one.
                    self._snap["mission"]["altitude_m"] = None
                    self._snap["mission"]["line_spacing_m"] = None
            self._mission_items.clear()
            # Pull the items so we can report altitude and line spacing.
            for seq in range(min(msg.count, 200)):
                try:
                    self._conn.mav.mission_request_int_send(
                        self._conn.target_system, self._conn.target_component,
                        seq)
                except Exception:
                    break

        elif kind in ("MISSION_ITEM_INT", "MISSION_ITEM"):
            scale = 1e7 if kind == "MISSION_ITEM_INT" else 1.0
            self._mission_items[msg.seq] = (
                msg.x / scale, msg.y / scale, msg.z,
                getattr(msg, "command", MAV_CMD_NAV_WAYPOINT))
            self._update_mission_geometry()

        elif kind == "MISSION_CURRENT":
            with self._lock:
                self._snap["mission"]["current"] = msg.seq
            # Newer ArduPilot also reports the mission checksum here, about
            # once a second, so a changed mission is noticed straight away
            # rather than at the next poll. (Not the `total` field beside it:
            # that leaves out the home item, so it never equals MISSION_COUNT.)
            ident = getattr(msg, "mission_id", 0) or None
            changed = (ident is not None and self._mission_id is not None
                       and ident != self._mission_id)
            if (changed and not self._armed
                    and time.time() - self._mission_asked_at > MISSION_REREAD_DELAY_S):
                self._request_mission()

        elif kind in ("CAMERA_TRIGGER", "CAMERA_FEEDBACK"):
            if not self._first_report_of_shot(msg, kind):
                return
            with self._lock:
                self._snap["trigger_count"] += 1
            if self.on_trigger:
                try:
                    self.on_trigger(self._trigger_geo(msg))
                except Exception:
                    log.exception("camera-trigger handler failed")

    def _first_report_of_shot(self, msg, kind):
        """One shutter, one photo.

        A controller can report the same shot as both CAMERA_TRIGGER and
        CAMERA_FEEDBACK, and each used to take a photo. FEEDBACK is the better of
        the two (it carries the position at the shutter), so once any has been
        seen the TRIGGER messages are ignored, and a FEEDBACK repeating an image
        index already handled is too.
        """
        if kind == "CAMERA_FEEDBACK":
            self._seen_feedback = True
            idx = getattr(msg, "img_idx", None)
            if idx is not None and idx == self._last_img_idx:
                return False
            self._last_img_idx = idx
            return True
        return not self._seen_feedback

    def _trigger_geo(self, msg):
        """Position for a triggered capture.

        CAMERA_FEEDBACK carries the position the controller recorded at shutter
        time, which is more accurate than whatever GLOBAL_POSITION_INT we
        happen to hold. Prefer it when present.
        """
        if msg.get_type() == "CAMERA_FEEDBACK":
            snap = self.snapshot()
            return {
                "lat": msg.lat / 1e7,
                "lon": msg.lng / 1e7,
                "alt_m": round(getattr(msg, "alt_msl", 0.0), 2),
                "rel_alt_m": round(getattr(msg, "alt_rel", 0.0), 2),
                "heading_deg": snap["position"]["heading_deg"] if snap["position"] else None,
                "fix_type": snap["gps"]["fix_type"],
                "satellites": snap["gps"]["satellites"],
                "hdop": snap["gps"]["hdop"],
                "source": "mavlink",
            }
        return self.geo_now()

    def geo_now(self):
        """Best-available geotag right now, or None with no fix."""
        snap = self.snapshot()
        pos = snap["position"]
        if not pos or snap["gps"]["fix_type"] < 2:
            return None
        return {
            "lat": pos["lat"], "lon": pos["lon"],
            "alt_m": pos["alt_m"], "rel_alt_m": pos["rel_alt_m"],
            "heading_deg": pos["heading_deg"],
            "fix_type": snap["gps"]["fix_type"],
            "satellites": snap["gps"]["satellites"],
            "hdop": snap["gps"]["hdop"],
            "source": "mavlink",
        }

    def _update_mission_geometry(self):
        """Derive mission altitude and line spacing from the waypoints.

        Only real waypoints count. Item 0 is ArduPilot's home position, whose
        altitude is above sea level rather than above launch -- averaging it in
        is how a 5 m survey once reported 51.5 m.

        Line spacing is the distance between parallel flight lines, measured
        across them: every waypoint is projected onto the axis perpendicular to
        the longest leg, the waypoints of one line land on the same offset, and
        the spacing is the median step between those offsets. Consecutive-point
        distances do not work here -- a survey with turnarounds alternates line,
        2 m turnaround, crossover, so their median is the turnaround.
        """
        pts = [(lat, lon, alt) for seq, (lat, lon, alt, cmd)
               in sorted(self._mission_items.items())
               if seq > 0 and cmd == MAV_CMD_NAV_WAYPOINT and (lat or lon)]
        if len(pts) < 2:
            return
        alts = [alt for _, _, alt in pts if alt]
        spacing = _line_spacing_m(pts)
        with self._lock:
            self._snap["mission"]["altitude_m"] = (
                round(sum(alts) / len(alts), 1) if alts else None)
            self._snap["mission"]["line_spacing_m"] = spacing

    def _close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


def _haversine_m(lat1, lon1, lat2, lon2):
    import math
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _line_spacing_m(pts):
    """Distance between parallel survey lines, or None for a simple route."""
    import math
    lat0 = pts[0][0]
    k = 111_320.0
    xy = [((lon - pts[0][1]) * k * math.cos(math.radians(lat0)),
           (lat - lat0) * k) for lat, lon, _ in pts]
    legs = [(xy[i], xy[i + 1]) for i in range(len(xy) - 1)]
    (ax, ay), (bx, by) = max(legs, key=lambda l: math.dist(*l))
    length = math.dist((ax, ay), (bx, by))
    if length < 1.0:
        return None
    nx, ny = -(by - ay) / length, (bx - ax) / length       # across the lines
    offsets = sorted(x * nx + y * ny for x, y in xy)
    steps = [b - a for a, b in zip(offsets, offsets[1:]) if b - a > 0.5]
    if not steps:
        return None
    return round(sorted(steps)[len(steps) // 2], 1)
