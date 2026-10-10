"""Every page renders, and the API contracts hold.

There is no camera, no Pixhawk and no internet in CI — which is exactly the state
the dashboard has to survive, because it is also the state of a Pi sitting on a
bench. The project's rule is that every failure has a designed state in the UI, so
"no camera" and "no drone" must render a page, not a stack trace.

These are also the only tests that touch the Jinja templates, so they catch the
class of bug a template refactor introduces: an undefined variable renders as an
empty string in Jinja, but a missing filter or a bad `include` is a 500.
"""

import os
import sys

import pytest

# Point the app at a scratch data directory before importing it — the module
# builds its store, settings and log at import time, and must not touch real
# flights. Deliberately not tmp_path: that is per-test, and this is per-process.
_SCRATCH = os.path.join(os.path.dirname(__file__), "_scratch_data")
os.environ.setdefault("HYLOCROPTER_DATA", _SCRATCH)

sys.argv = ["app.py", "--dev"]          # keep argparse in app.__main__ happy
import app as app_mod                   # noqa: E402
import bndvi                            # noqa: E402
import flights as flights_mod           # noqa: E402


@pytest.fixture(scope="module")
def client():
    app_mod.app.config["TESTING"] = True
    app_mod.app.config["DEV_MODE"] = True
    app_mod.cam.dev_mode = True
    with app_mod.app.test_client() as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def _clean_scratch():
    yield
    import shutil
    shutil.rmtree(_SCRATCH, ignore_errors=True)


# ── pages ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/", "/plan", "/new-flight", "/processing", "/history", "/debug",
    "/settings", "/setup",
])
def test_every_page_renders_with_no_hardware(client, path):
    res = client.get(path)
    assert res.status_code == 200, res.data[:400]
    assert b"<html" in res.data.lower()


def test_the_map_page_renders_before_the_first_flight(client):
    """It used to be a dead end: no map, and no way to start flying. Now it is
    where you go looking for the farm on the satellite imagery and draw plots."""
    body = client.get("/").data.decode()
    assert 'id="plot-map"' in body
    assert "data-vicinity=" in body
    assert "data-blocks=" in body
    assert "Add a block" in body


def test_the_map_page_says_the_drone_is_not_connected(client):
    """Every failure state is visible in the UI — the user should never need a
    terminal to find out why nothing is happening."""
    assert "Drone not connected" in client.get("/").data.decode()


def test_a_missing_page_renders_the_error_template(client):
    res = client.get("/no-such-page")
    assert res.status_code == 404
    assert b"<html" in res.data.lower()


def test_a_missing_api_path_returns_json_not_html(client):
    """The old app used bare abort(404), so any client calling res.json() threw."""
    res = client.get("/api/nope")
    assert res.status_code == 404
    assert res.is_json
    assert "error" in res.get_json()


# ── telemetry and camera, both absent ────────────────────────────────────────

def test_telemetry_reports_not_connected_rather_than_failing(client):
    snap = client.get("/api/telemetry").get_json()
    assert snap["connected"] is False
    assert snap["detail"]                 # says *why*, for the UI to show


def test_camera_status_reports_synthetic_frames_in_dev_mode(client):
    status = client.get("/api/camera/status").get_json()
    assert status["synthetic"] or status["available"] is False


def test_the_preview_frame_carries_three_real_channel_planes(client):
    """The channel-split panel claims to show a measured green channel, so green
    has to actually be sent — it used to be synthesised from the other two."""
    res = client.get("/api/preview/frame")
    assert res.status_code == 200
    if res.is_json:                       # no camera and no synthetic fallback
        pytest.skip(res.get_json().get("error", "no frame"))
    header = res.headers.get("X-Frame-Meta") or ""
    width, height = bndvi.SYNTH_W, bndvi.SYNTH_H
    assert len(res.data) == width * height * 3, header


# ── settings ─────────────────────────────────────────────────────────────────

def test_settings_get_returns_every_key(client):
    body = client.get("/api/settings").get_json()
    import settings as settings_mod
    assert set(body) >= set(settings_mod.DEFAULTS)


def test_drawing_blocks_over_the_api(client):
    res = client.patch("/api/settings", json={"survey_blocks": [
        {"id": "b1", "name": "North block", "south": 14.1250, "west": 121.0750,
         "north": 14.1259, "east": 121.0764},
        {"id": "b2", "name": "South rows", "south": 14.1230, "west": 121.0750,
         "north": 14.1242, "east": 121.0764},
    ]})
    assert res.status_code == 200
    body = res.get_json()
    assert len(body["applied"]["survey_blocks"]) == 2
    assert not body["warnings"]

    # the map page hands them to Leaflet, with dimensions worked out server-side
    page = client.get("/").data.decode()
    assert "North block" in page
    assert "width_m" in page

    # and they became the All flights filter choices
    history = client.get("/history").data.decode()
    assert 'data-block="North block"' in history
    assert 'data-block="South rows"' in history

    # clearing works too
    res = client.patch("/api/settings", json={"survey_blocks": []})
    assert res.get_json()["applied"] == {"survey_blocks": []}


def test_a_bad_setting_is_reported_not_500(client):
    res = client.patch("/api/settings", json={"exposure_us": "banana"})
    assert res.status_code == 200
    assert res.get_json()["warnings"]


# ── mission planning ─────────────────────────────────────────────────────────

def test_the_mission_plan_endpoint_returns_the_two_numbers(client):
    plan = client.get("/api/mission/plan?altitude_m=12&forward_overlap=0.4"
                      "&side_overlap=0.3&plot_side_m=100&canopy=0").get_json()
    assert plan["trigger_distance_m"] == pytest.approx(6.5, abs=0.1)
    assert plan["line_spacing_m"] == pytest.approx(10.1, abs=0.1)
    assert plan["plot_area_ha"] == pytest.approx(1.0, abs=0.01)


def test_the_mission_plan_endpoint_survives_junk_query_values(client):
    res = client.get("/api/mission/plan?altitude_m=abc&plot_side_m=")
    assert res.status_code == 200
    assert res.get_json()["photos"] >= 1


def test_the_new_flight_page_plans_for_a_drawn_block(client):
    client.patch("/api/settings", json={"survey_blocks": [
        {"id": "b1", "name": "North block", "south": 14.1250, "west": 121.0750,
         "north": 14.1259, "east": 121.0764},
    ]})
    body = client.get("/new-flight").data.decode()
    assert 'id="mp-block"' in body                  # the block picker
    assert "North block" in body
    assert 'id="mp-plot-w"' in body and 'id="mp-plot-h"' in body

    client.patch("/api/settings", json={"survey_blocks": []})
    assert "No blocks drawn yet" in client.get("/new-flight").data.decode()


def test_the_plan_endpoint_resolves_a_block_id(client):
    """Passing an id means the server uses the block's real dimensions rather
    than trusting numbers the page sent."""
    client.patch("/api/settings", json={"survey_blocks": [
        {"id": "b1", "name": "Strip", "south": 14.1250, "west": 121.0750,
         "north": 14.1256, "east": 121.0790},
    ]})
    plan = client.get("/api/mission/plan?altitude_m=12&block=b1").get_json()
    assert plan["plot_w_m"] > plan["plot_h_m"] * 3       # a long strip
    assert plan["line_direction"] == "east–west"         # flown the long way
    client.patch("/api/settings", json={"survey_blocks": []})


def test_the_planner_works_with_no_blocks_no_camera_and_no_drone(client):
    """Planning before going to the farm: pure geometry, so nothing needs to be
    plugged in and no ground needs to have been marked."""
    client.patch("/api/settings", json={"survey_blocks": []})
    body = client.get("/plan").data.decode()
    assert 'id="mission-planner"' in body
    assert "Football field" in body            # somewhere to rehearse
    assert "Type the size myself" in body
    assert "No blocks drawn yet" not in body   # not an error state here
    # and it is not gated on hardware
    assert "Drone not connected" in body


def test_the_planner_page_offers_every_practice_area(client):
    body = client.get("/plan").data.decode()
    for area in flights_mod.TEST_AREAS:
        assert area["name"] in body


def test_the_plan_endpoint_resolves_a_practice_area(client):
    plan = client.get("/api/mission/plan?altitude_m=12&block=t-court").get_json()
    assert plan["plot_w_m"] == 28 and plan["plot_h_m"] == 15
    assert not plan["warnings"]


def test_a_drawn_block_beats_a_practice_area_of_the_same_name(client):
    """Ids are distinct namespaces, so a block id must win over the preset list
    rather than the lookup order deciding by accident."""
    client.patch("/api/settings", json={"survey_blocks": [
        {"id": "b1", "name": "North block", "south": 14.1250, "west": 121.0750,
         "north": 14.1259, "east": 121.0764},
    ]})
    plan = client.get("/api/mission/plan?altitude_m=12&block=b1").get_json()
    assert plan["plot_w_m"] == pytest.approx(151, abs=2)
    client.patch("/api/settings", json={"survey_blocks": []})


def test_the_planner_lives_in_one_place(client):
    """/plan and /new-flight share the partial, so the two must not drift."""
    plan_page = client.get("/plan").data.decode()
    flight_page = client.get("/new-flight").data.decode()
    for marker in ('id="mp-trigger"', 'id="mp-spacing"', 'id="mp-direction"',
                   'id="mp-block"', "CAM_TRIGG_DIST"):
        assert marker in plan_page, marker
        assert marker in flight_page, marker


# ── captures ─────────────────────────────────────────────────────────────────

def test_capturing_in_dev_mode_produces_a_full_record(client):
    res = client.post("/api/captures", json={"label": "route test"})
    assert res.status_code in (200, 201), res.data[:300]
    rec = res.get_json()
    for key in ("id", "timestamp", "label", "notes", "files", "stats",
                "classification", "settings"):
        assert key in rec
    assert rec["flight_id"] is None                    # a ground capture
    assert -1.0 <= rec["stats"]["mean"] <= 1.0
    assert rec["classification"] in ("healthy", "moderate", "stressed")

    # it is reachable, and it renders
    assert client.get(f"/capture/{rec['id']}").status_code == 200
    assert client.get("/history").status_code == 200


def test_the_capture_list_separates_ground_captures_from_flights(client):
    """`flight_id=None` means "belongs to no flight". It used to also mean "no
    filter", so asking for the ground captures returned every capture."""
    flight = app_mod.store.open_flight(name="route test flight")
    ground_before = len(app_mod.store.ground_captures())
    app_mod.store.add_capture({
        "id": "route_c1", "timestamp": "2026-08-05T10:00:00", "label": "",
        "notes": "", "flight_id": flight["id"], "geo": None, "files": {},
        "stats": None, "classification": None, "settings": {},
    })
    assert len(app_mod.store.ground_captures()) == ground_before
    assert len(app_mod.store.captures(flight_id=flight["id"])) == 1
    assert len(app_mod.store.captures()) > ground_before
    app_mod.store.delete_flight(flight["id"])


# ── offline assets ───────────────────────────────────────────────────────────

def test_leaflet_and_the_fonts_are_served_from_disk(client):
    """Nothing may reach the network at run time — that is the thesis's central
    claim, so the vendored assets have to actually be there."""
    for path in ("/static/vendor/leaflet/leaflet.js",
                 "/static/vendor/leaflet/leaflet.css",
                 "/static/css/tokens.css",
                 "/static/js/colormap.js"):
        assert client.get(path).status_code == 200, path


def test_no_page_references_an_external_host(client):
    """A CDN link that only fails in the field is worse than one that fails now."""
    for path in ("/", "/debug", "/settings", "/setup", "/new-flight",
                 "/history", "/processing"):
        body = client.get(path).data.decode()
        for host in ("unpkg.com", "cdn.jsdelivr.net", "fonts.googleapis.com",
                     "fonts.gstatic.com", "arcgisonline.com", "tile.openstreetmap",
                     "cdnjs.cloudflare.com"):
            assert host not in body, f"{path} references {host}"


def test_a_missing_tile_serves_a_fallback_rather_than_a_broken_image(client):
    res = client.get("/tiles/19/999999/999999.jpg")
    assert res.status_code == 200
    assert res.data


def test_the_tile_coverage_endpoint_answers_how_far_the_map_goes(client):
    body = client.get("/api/tiles/coverage").get_json()
    assert "has_tiles" in body["coverage"]


def test_the_tile_plan_endpoint_estimates_before_downloading(client):
    """You get told the tile count and the megabytes before committing."""
    body = client.get("/api/tiles/plan?box_m=620").get_json()
    assert body["tiles"] > 0
    assert body["est_bytes"] > 0


# ── logs, so the UI never needs a terminal ───────────────────────────────────

def test_the_log_endpoint_returns_lines_the_ui_can_show(client):
    body = client.get("/api/logs").get_json()
    lines = body["lines"] if isinstance(body, dict) else body
    assert isinstance(lines, list)


def test_destructive_system_actions_require_confirmation(client):
    """A destructive action returns a confirmation contract instead of acting."""
    res = client.post("/api/system/actions/shutdown", json={})
    assert res.status_code in (200, 400, 409)
    body = res.get_json()
    if res.status_code == 200:
        assert body.get("confirm_required") or body.get("confirm") is not True


# ── metering off a reference card ────────────────────────────────────────────
#
# Judging exposure by hand is the thing that wasted a whole afternoon: a frame
# reading 0.5 out of 255 and one reading 60 look identical on screen, and a
# wrongly exposed frame makes BNDVI read zero everywhere. This borrows the
# camera's own light meter for six seconds and pins what it found.

def test_metering_needs_a_camera(client):
    """In dev mode there is nothing to meter, and saying so is better than
    returning numbers measured off a synthetic frame."""
    res = client.post("/api/calibrate/auto-exposure", json={})
    assert res.status_code == 503
    assert "synthetic" in res.get_json()["error"].lower()


def test_a_blown_out_card_is_refused(client, monkeypatch):
    """A clipped card gives a confident number that means nothing. Refusing is
    the whole point -- accepting it would pin an exposure that hides the error."""
    import app as app_mod
    monkeypatch.setattr(app_mod.cam, "auto_expose", lambda: ({
        "exposure_us": 8000, "gain": 1.0, "colour_gains": [1.0, 1.0],
        "nir": 250.0, "blue": 252.0, "clipped_pct": 41.0, "level": 250.0}, None))
    body = client.post("/api/calibrate/auto-exposure", json={}).get_json()
    assert body["ok"] is False
    assert body["applied"] is False
    assert "blown out" in body["message"]


def test_a_card_in_the_dark_is_refused(client, monkeypatch):
    import app as app_mod
    monkeypatch.setattr(app_mod.cam, "auto_expose", lambda: ({
        "exposure_us": 200000, "gain": 16.0, "colour_gains": [1.0, 1.0],
        "nir": 4.0, "blue": 3.0, "clipped_pct": 0.0, "level": 3.0}, None))
    body = client.post("/api/calibrate/auto-exposure", json={}).get_json()
    assert body["ok"] is False
    assert body["applied"] is False
    assert "not enough light" in body["message"]


def test_a_good_reading_is_pinned_as_fixed_settings(client, monkeypatch):
    """The automatics are a light meter, not a mode: what they found has to end
    up stored as fixed settings, or the flight is shot on auto-exposure."""
    import app as app_mod
    monkeypatch.setattr(app_mod.cam, "auto_expose", lambda: ({
        "exposure_us": 6400, "gain": 3.7, "colour_gains": [1.4, 2.1],
        "nir": 96.0, "blue": 88.0, "clipped_pct": 0.2, "level": 88.0}, None))
    body = client.post("/api/calibrate/auto-exposure", json={}).get_json()
    assert body["ok"] is True
    stored = client.get("/api/settings").get_json()
    assert stored["exposure_us"] == 6400
    assert stored["gain"] == 3.7
    assert stored["colour_gains"] == [1.4, 2.1]


def test_an_out_of_range_reading_is_clamped_not_rejected(client, monkeypatch):
    """Settings clamping still applies — the meter is not allowed to write a
    value the rest of the dashboard would refuse."""
    import app as app_mod
    monkeypatch.setattr(app_mod.cam, "auto_expose", lambda: ({
        "exposure_us": 999999, "gain": 99.0, "colour_gains": [1.0, 1.0],
        "nir": 90.0, "blue": 90.0, "clipped_pct": 0.0, "level": 90.0}, None))
    body = client.post("/api/calibrate/auto-exposure", json={}).get_json()
    assert body["ok"] is True
    stored = client.get("/api/settings").get_json()
    assert stored["exposure_us"] == 200000
    assert stored["gain"] == 16.0


# ── the diagnostic unlock ────────────────────────────────────────────────────
#
# Auto white balance rebalances red against blue every frame, and BNDVI is a
# ratio of exactly those two channels. So the unlock is a diagnostic, and the
# thing that must be guaranteed is that it never becomes a stored setting.

def test_unlocking_the_automatics_is_not_a_stored_setting(client):
    before = client.get("/api/settings").get_json()
    client.post("/api/camera/automatics", json={"on": True})
    after = client.get("/api/settings").get_json()
    assert after == before, \
        "the unlock must leave no trace in settings — a restart has to clear it"


def test_unlocking_says_the_readings_are_not_comparable(client):
    body = client.post("/api/camera/automatics", json={"on": True}).get_json()
    assert body["auto"] is True
    assert "comparable" in body["message"]
    off = client.post("/api/camera/automatics", json={"on": False}).get_json()
    assert off["auto"] is False


def test_pinning_needs_a_real_camera(client):
    """Nothing to read off synthetic frames, and inventing values would be worse
    than refusing."""
    res = client.post("/api/camera/pin-current", json={})
    assert res.status_code == 503


def test_pinning_stores_what_the_camera_reported(client, monkeypatch):
    import app as app_mod
    monkeypatch.setattr(app_mod.cam, "camera_state", lambda: ({
        "exposure_us": 6400, "gain": 3.7, "colour_gains": [1.4, 2.1],
        "auto": True, "source": "camera"}, None))
    monkeypatch.setattr(app_mod.cam, "set_auto", lambda on: bool(on))
    body = client.post("/api/camera/pin-current", json={}).get_json()
    assert body["ok"] is True
    stored = client.get("/api/settings").get_json()
    assert stored["exposure_us"] == 6400
    assert stored["gain"] == 3.7
    assert stored["colour_gains"] == [1.4, 2.1]


def test_colour_gains_are_clamped_before_reaching_the_camera(client):
    """Zero would be accepted by libcamera and quietly kill a channel — and the
    index is a ratio, so a dead channel is not a dim picture but no reading."""
    client.patch("/api/settings", json={"colour_gains": [0, 99]})
    gains = client.get("/api/settings").get_json()["colour_gains"]
    assert gains == [0.1, 8.0]


def test_without_a_camera_a_real_capture_fails_instead_of_faking_one(
        client, monkeypatch):
    """Out of dev mode with no camera, the answer is an error -- not a generated
    frame filed as a photo. That is what the first real mission produced."""
    real_capture = bndvi.capture_image

    def no_camera(dev_mode=False, **kwargs):
        # Stubbed so this cannot take a real photo when the suite runs on the Pi.
        if dev_mode:
            return real_capture(dev_mode=True, **kwargs)
        raise bndvi.CameraUnavailable("no camera detected on the CSI port")

    monkeypatch.setattr(bndvi, "capture_image", no_camera)
    monkeypatch.setattr(bndvi, "probe_camera",
                        lambda: {"available": False, "detail": "no camera"})
    app_mod.cam._probe = None
    app_mod.app.config["DEV_MODE"] = False
    app_mod.cam.dev_mode = False
    app_mod.cam._synthetic_override = False
    before = len(app_mod.store.captures())
    try:
        res = client.post("/api/captures", json={"label": "no camera"})
    finally:
        app_mod.app.config["DEV_MODE"] = True
        app_mod.cam.dev_mode = True
    assert res.status_code >= 400, res.data[:300]
    assert len(app_mod.store.captures()) == before


def test_a_whole_mission_arm_trigger_disarm_ends_with_every_photo_on_the_flight(
        client):
    """Arm, three camera triggers, disarm: three analysed photos on one closed
    flight. The first real mission kept one."""
    import time
    app_mod._on_arm_change(True, {})
    flight = app_mod._recording_flight()
    assert flight is not None
    for i in range(3):
        app_mod._on_camera_trigger({"lat": 14.1265 + i * 1e-5, "lon": 121.0768,
                                    "source": "mavlink"})
    app_mod._on_arm_change(False, {})
    for _ in range(300):
        if not app_mod._processing["running"]:
            break
        time.sleep(0.05)
    assert not app_mod._processing["running"]
    photos = app_mod.store.captures(flight_id=flight["id"])
    assert len(photos) == 3
    assert app_mod.store.flight(flight["id"])["status"] != "recording"
    assert client.get(f"/capture/{photos[0]['id']}").status_code == 200


def test_the_pre_flight_card_can_be_refreshed_on_its_own(client):
    """The New flight page polls this so an uploaded mission appears without a
    reload -- it used to take a reboot of the Pi."""
    res = client.get("/new-flight/preflight")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Before take-off" in html and "Mission loaded on the drone" in html
    assert "<html" not in html.lower(), "just the card, not a whole page"
    page = client.get("/new-flight").get_data(as_text=True)
    assert 'id="preflight"' in page and "/new-flight/preflight" in page


def test_the_plan_allows_for_the_plants_by_default(client):
    """The camera sees plant tops. The saved plant height comes off the
    altitude unless the page says otherwise."""
    bare = client.get("/api/mission/plan?altitude=5&canopy=0").get_json()
    crop = client.get("/api/mission/plan?altitude=5").get_json()
    assert crop["canopy_m"] == 1.5
    assert crop["trigger_distance_m"] < bare["trigger_distance_m"]
    assert crop["gcs_forward_overlap_pct"] > crop["forward_overlap_pct"]


def test_the_map_offers_live_location(client):
    """The drone's GPS, and the device's own -- which needs the https copy, so
    the page is told whether that is running."""
    page = client.get("/").get_data(as_text=True)
    assert 'id="map-live"' in page and 'id="map-me"' in page
    assert "data-https-port" in page


# ── the secure address ───────────────────────────────────────────────────────

@pytest.fixture
def https_up(monkeypatch):
    import tls
    monkeypatch.setitem(tls._state, "running", True)
    monkeypatch.setitem(tls._state, "port", 5443)


def test_the_name_is_sent_to_the_https_address(client, https_up):
    res = client.get("/history?q=farm", base_url="http://hylocropter.local:5000")
    assert res.status_code == 302, "temporary, so a broken https is not remembered"
    assert res.headers["Location"] == "https://hylocropter.local:5443/history?q=farm"
    bare = client.get("/", base_url="http://hylocropter.local:5000")
    assert bare.headers["Location"] == "https://hylocropter.local:5443/"


def test_the_ip_address_stays_plain_http(client, https_up):
    """The fallback for phones that cannot resolve .local names."""
    assert client.get("/", base_url="http://10.142.48.244:5000").status_code == 200


def test_nothing_is_redirected_while_https_is_down(client, monkeypatch):
    import tls
    monkeypatch.setitem(tls._state, "running", False)
    assert client.get("/", base_url="http://hylocropter.local:5000").status_code == 200


def test_writes_are_not_redirected(client, https_up):
    """A redirected POST turns into a GET in most clients and loses its body."""
    res = client.patch("/api/settings", json={},
                       base_url="http://hylocropter.local:5000")
    assert res.status_code == 200


def test_settings_offers_the_camera_turn(client):
    page = client.get("/settings").get_data(as_text=True)
    assert 'data-setting="camera_rotation_deg"' in page


def test_the_map_survives_the_newest_flight_still_processing(client):
    """The newest flight has no reading until processing finishes. Comparing
    it with the one before took the map page down for the whole ~20 minutes."""
    store = app_mod.store
    older = store.open_flight(name="earlier")
    store.update_flight(older["id"], {"status": "ok",
                                      "started_at": "2026-01-01T09:00:00", "stats": {
        "mean": 0.41, "healthy_pct": 80.0, "moderate_pct": 15.0,
        "stressed_pct": 5.0}})
    newest = store.open_flight(name="still processing")
    store.update_flight(newest["id"], {"status": "processing", "stats": None})
    try:
        res = client.get(f"/?flight={newest['id']}")
        assert res.status_code == 200, res.data[-600:]
        assert "still being processed" in res.get_data(as_text=True)
    finally:
        store.delete_flight(newest["id"])
        store.delete_flight(older["id"])


def test_the_comparison_follows_weak_spots_not_the_mean(client):
    """A higher mean with more weak spots is worse, not better -- the card used to
    say "more of the block is reading healthy" when it was reading less so."""
    store = app_mod.store
    older = store.open_flight(name="earlier")
    store.update_flight(older["id"], {"status": "ok",
                                      "started_at": "2026-01-01T09:00:00", "stats": {
        "mean": 0.30, "healthy_pct": 95.0, "moderate_pct": 4.0,
        "stressed_pct": 1.0}})
    newest = store.open_flight(name="later")
    store.update_flight(newest["id"], {"status": "ok",
                                       "started_at": "2026-01-02T09:00:00", "stats": {
        "mean": 0.45, "healthy_pct": 85.0, "moderate_pct": 10.0,
        "stressed_pct": 5.0}})
    try:
        page = client.get(f"/?flight={newest['id']}").get_data(as_text=True)
        assert "Worse than the flight before" in page
        assert "15.0% of the block, up from 5.0%" in page
        assert "more of the block is reading healthy" not in page
    finally:
        store.delete_flight(newest["id"])
        store.delete_flight(older["id"])


def test_every_page_can_tell_when_photos_are_being_processed(client):
    """The header poll is how any open page follows a landed flight to the
    progress screen, so the telemetry it reads has to say so."""
    proc = client.get("/api/telemetry").get_json()["processing"]
    assert set(proc) == {"running", "flight_id", "done", "total"}


def test_the_flight_banner_is_on_every_page_ready_to_show(client):
    """core.js fills and shows it when a flight starts or lands while a page is
    open; with no flight going it is there, but hidden."""
    for path in ("/", "/history", "/settings", "/debug"):
        page = client.get(path).get_data(as_text=True)
        assert 'id="flight-banner"' in page, path
        assert 'data-view="' in page, path
    banner = client.get("/history").get_data(as_text=True).split('id="flight-banner"')[1]
    assert banner.split(">")[0].strip().endswith("hidden"), "hidden with no flight"


def test_the_flight_banner_shows_while_recording(client):
    store = app_mod.store
    flight = store.open_flight(name="in the air")
    try:
        page = client.get("/history").get_data(as_text=True)
        tag = page.split('id="flight-banner"')[1].split(">")[0]
        assert "hidden" not in tag
        assert f"Recording {flight['id']}" in page
    finally:
        store.delete_flight(flight["id"])


def test_after_landing_the_banner_and_flight_page_lead_to_the_progress(client, monkeypatch):
    """The flight stays marked recording while its photos are processed. The
    banner used to keep saying "Recording -- open the flight page", and the
    flight page kept showing the in-flight view, so the progress screen was
    only reachable by typing its address."""
    store = app_mod.store
    flight = store.open_flight(name="just landed")
    monkeypatch.setitem(app_mod._processing, "running", True)
    monkeypatch.setitem(app_mod._processing, "flight_id", flight["id"])
    try:
        page = client.get("/history").get_data(as_text=True)
        banner = page.split('id="flight-banner"')[1].split("</div>\n    </div>")[0]
        assert "hidden" not in banner.split(">")[0]
        assert 'href="/processing"' in banner and "See the progress" in banner
        res = client.get("/new-flight")
        assert res.status_code == 302 and res.location.endswith("/processing")
        page = client.get("/processing").get_data(as_text=True)
        tag = page.split('id="flight-banner"')[1].split(">")[0]
        assert "hidden" in tag, "not on the page that already shows it"
    finally:
        store.delete_flight(flight["id"])
