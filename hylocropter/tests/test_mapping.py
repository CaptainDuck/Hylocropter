"""Turning geotagged photos into a farm stress map.

This is the chain the thesis calls for: each photo has a GPS position and an
altitude, the altitude gives it a ground footprint, the positions give the flight
a bounding box, and the per-photo means bin into a grid that gets painted over
satellite imagery.

Two properties are worth more than all the others and are tested hardest:

  * **Unvisited ground stays unknown.** An empty grid cell must stay None. A
    mid-range colour on ground the drone never overflew is invented data a farmer
    could act on.
  * **North is up.** Row 0 is the northern edge. Getting that backwards flips the
    map vertically and sends someone to the wrong end of the field.
"""

import math

import pytest

import flights
import tiles


def capture(lat=None, lon=None, mean=0.4, rel_alt=12.0, heading=0.0,
            stats=True, cid="c1"):
    """A capture record shaped like the real ones."""
    geo = None
    if lat is not None:
        geo = {"lat": lat, "lon": lon, "rel_alt_m": rel_alt,
               "heading_deg": heading, "fix_type": 3, "satellites": 12}
    return {
        "id": cid,
        "geo": geo,
        "stats": ({"mean": mean, "min": mean - 0.2, "max": mean + 0.2,
                   "healthy_pct": 60.0, "moderate_pct": 30.0,
                   "stressed_pct": 10.0} if stats else None),
        "settings": {"resolution": [3280, 2464]},
    }


# ── one photo's patch of ground ──────────────────────────────────────────────

def test_footprint_matches_the_lens_trigonometry():
    """2*h*tan(fov/2). At 12 m the Pi Camera v2 covers about 14.5 x 10.9 m."""
    fp = flights.footprint({"lat": 14.1, "lon": 121.0, "rel_alt_m": 12.0})
    assert fp["width_m"] == pytest.approx(
        2 * 12.0 * math.tan(math.radians(62.2 / 2)), abs=0.01)
    assert fp["height_m"] == pytest.approx(
        2 * 12.0 * math.tan(math.radians(48.8 / 2)), abs=0.01)
    assert fp["width_m"] == pytest.approx(14.48, abs=0.01)
    assert fp["height_m"] == pytest.approx(10.89, abs=0.01)


def test_footprint_scales_linearly_with_height():
    a = flights.footprint({"lat": 0, "lon": 0, "rel_alt_m": 10.0})
    b = flights.footprint({"lat": 0, "lon": 0, "rel_alt_m": 20.0})
    assert b["width_m"] == pytest.approx(a["width_m"] * 2, abs=0.01)


def test_footprint_is_wider_than_it_is_tall():
    """The wide axis sits across the flight track, which is what makes the line
    spacing the bigger of the two numbers in the mission plan."""
    fp = flights.footprint({"lat": 0, "lon": 0, "rel_alt_m": 15.0})
    assert fp["width_m"] > fp["height_m"]


@pytest.mark.parametrize("geo", [
    None,
    {"lat": 14.1, "lon": 121.0},                      # no altitude at all
    {"lat": 14.1, "lon": 121.0, "rel_alt_m": None},
    {"lat": 14.1, "lon": 121.0, "rel_alt_m": 0.0},
    {"lat": 14.1, "lon": 121.0, "rel_alt_m": -3.0},
])
def test_footprint_refuses_to_guess_without_a_height(geo):
    """No altitude means the footprint is unknowable. Guessing one would put
    invented ground on the map, so the map falls back to the grid instead."""
    assert flights.footprint(geo) is None


def test_footprint_carries_the_heading_through():
    fp = flights.footprint({"lat": 0, "lon": 0, "rel_alt_m": 12.0,
                            "heading_deg": 274.0})
    assert fp["heading_deg"] == 274.0


def test_ground_sampling_distance():
    """The honest resolution limit: 14.48 m across 3280 px is 0.44 cm/px."""
    gsd = flights.ground_sampling_distance_cm(
        {"lat": 0, "lon": 0, "rel_alt_m": 12.0}, (3280, 2464))
    assert gsd == pytest.approx(0.44, abs=0.01)


def test_gsd_is_none_without_altitude_or_resolution():
    assert flights.ground_sampling_distance_cm({"lat": 0, "lon": 0}, (3280, 2464)) is None
    assert flights.ground_sampling_distance_cm(
        {"lat": 0, "lon": 0, "rel_alt_m": 12.0}, None) is None


# ── the flight's extent ──────────────────────────────────────────────────────

def test_bounds_are_none_without_any_fix():
    assert flights.compute_bounds([capture(), capture()]) is None
    assert flights.compute_bounds([]) is None


def test_bounds_enclose_every_geotagged_capture():
    caps = [capture(14.100, 121.000), capture(14.110, 121.020)]
    b = flights.compute_bounds(caps)
    assert b["south"] < 14.100 and b["north"] > 14.110
    assert b["west"] < 121.000 and b["east"] > 121.020


def test_bounds_ignore_captures_with_no_fix():
    mixed = [capture(14.100, 121.000), capture(None), capture(14.101, 121.001)]
    b = flights.compute_bounds(mixed)
    assert b is not None
    assert b["north"] < 14.11        # the None did not widen anything


def test_a_single_capture_still_gets_a_drawable_box():
    """A degenerate box has nowhere to draw the overlay, so it gets opened out."""
    b = flights.compute_bounds([capture(14.1265, 121.0768)])
    assert b["north"] > b["south"]
    assert b["east"] > b["west"]
    height_m = (b["north"] - b["south"]) * 111_320
    assert height_m > 10


# ── binning into the grid ────────────────────────────────────────────────────

def test_unvisited_cells_stay_none():
    """The rule that keeps the map honest."""
    caps = [capture(14.1000, 121.0000, mean=0.5)]
    bounds = flights.compute_bounds(caps)
    grid = flights.build_grid(caps, bounds, 0.3, 0.1)
    assert grid["covered"] == 1
    assert grid["cells"].count(None) == grid["cols"] * grid["rows"] - 1


def test_grid_is_the_expected_shape():
    grid = flights.build_grid([], None, 0.3, 0.1)
    assert grid["cols"] == flights.GRID_COLS
    assert grid["rows"] == flights.GRID_ROWS
    assert len(grid["cells"]) == flights.GRID_COLS * flights.GRID_ROWS
    assert grid["covered"] == 0
    assert set(grid["cells"]) == {None}


def test_row_zero_is_the_northern_edge():
    """North is up. If this inverts, the map is flipped and the walk-the-red-rows
    advice sends someone to the wrong end of the field."""
    bounds = {"south": 14.00, "north": 14.02, "west": 121.00, "east": 121.02}
    north = capture(14.0199, 121.010, mean=0.8, cid="n")
    south = capture(14.0001, 121.010, mean=-0.5, cid="s")
    grid = flights.build_grid([north, south], bounds, 0.3, 0.1)
    cols = grid["cols"]
    top = [c for c in grid["cells"][:cols] if c is not None]
    bottom = [c for c in grid["cells"][-cols:] if c is not None]
    assert top == [pytest.approx(0.8)]
    assert bottom == [pytest.approx(-0.5)]


def test_west_is_the_left_edge():
    bounds = {"south": 14.00, "north": 14.02, "west": 121.00, "east": 121.02}
    west = capture(14.010, 121.0001, mean=0.7, cid="w")
    grid = flights.build_grid([west], bounds, 0.3, 0.1)
    idx = grid["cells"].index(pytest.approx(0.7))
    assert idx % grid["cols"] == 0


def test_several_photos_in_one_cell_are_averaged():
    bounds = {"south": 14.00, "north": 14.02, "west": 121.00, "east": 121.02}
    caps = [capture(14.0100, 121.0100, mean=0.2, cid="a"),
            capture(14.0101, 121.0101, mean=0.6, cid="b")]
    grid = flights.build_grid(caps, bounds, 0.3, 0.1)
    values = [c for c in grid["cells"] if c is not None]
    assert values == [pytest.approx(0.4)]
    assert grid["covered"] == 1


def test_captures_outside_the_bounds_are_dropped_not_clamped():
    """Clamping would smear a stray fix onto the edge of the field."""
    bounds = {"south": 14.00, "north": 14.02, "west": 121.00, "east": 121.02}
    grid = flights.build_grid([capture(15.0, 122.0, mean=0.9)], bounds, 0.3, 0.1)
    assert grid["covered"] == 0


def test_captures_without_stats_are_skipped():
    bounds = {"south": 14.00, "north": 14.02, "west": 121.00, "east": 121.02}
    grid = flights.build_grid([capture(14.01, 121.01, stats=False)],
                              bounds, 0.3, 0.1)
    assert grid["covered"] == 0


def test_a_degenerate_bounds_box_yields_an_empty_grid():
    bounds = {"south": 14.0, "north": 14.0, "west": 121.0, "east": 121.0}
    grid = flights.build_grid([capture(14.0, 121.0)], bounds, 0.3, 0.1)
    assert grid["covered"] == 0


# ── flight-level statistics ──────────────────────────────────────────────────

def test_aggregate_stats_average_the_captures():
    caps = [capture(mean=0.2, cid="a"), capture(mean=0.6, cid="b")]
    s = flights.aggregate_stats(caps)
    assert s["mean"] == pytest.approx(0.4)
    assert s["min"] == pytest.approx(0.0)      # 0.2 - 0.2
    assert s["max"] == pytest.approx(0.8)      # 0.6 + 0.2
    assert s["std"] == pytest.approx(0.2)


def test_aggregate_stats_survive_an_empty_flight():
    s = flights.aggregate_stats([])
    assert s["mean"] == 0.0 and s["std"] == 0.0


def test_aggregate_stats_ignore_failed_captures():
    caps = [capture(mean=0.4, cid="a"), capture(stats=False, cid="b")]
    assert flights.aggregate_stats(caps)["mean"] == pytest.approx(0.4)


# ── the whole mapping chain, end to end ──────────────────────────────────────

def test_a_lawnmower_flight_maps_stress_to_the_right_corner():
    """The process the user actually cares about: fly a grid over a field whose
    eastern half is stressed, and the map has to come out stressed in the east,
    healthy in the west, and unknown where the drone never went."""
    caps = []
    lat0, lon0 = 14.1200, 121.0700
    step = 0.00018                       # ~20 m
    for row in range(6):
        for col in range(6):
            lat = lat0 + row * step
            lon = lon0 + col * step
            stressed = col >= 3
            caps.append(capture(lat, lon, mean=(-0.1 if stressed else 0.55),
                                cid=f"r{row}c{col}"))

    bounds = flights.compute_bounds(caps)
    grid = flights.build_grid(caps, bounds, 0.3, 0.1)
    stats = flights.aggregate_stats(caps)

    assert grid["covered"] > 20
    assert grid["cells"].count(None) > 0, "a 6x6 flight cannot fill a 14x9 grid"

    cols = grid["cols"]
    west, east = [], []
    for i, v in enumerate(grid["cells"]):
        if v is None:
            continue
        (west if (i % cols) < cols / 2 else east).append(v)
    assert sum(west) / len(west) > 0.3, "the healthy half must read healthy"
    assert sum(east) / len(east) < 0.1, "the stressed half must read stressed"

    # and the flight-level verdict is the average of the two
    import bndvi
    assert bndvi.classify(stats["mean"]) == "moderate"


def test_a_flight_with_no_gps_still_produces_stats_but_no_map():
    """A flight flown with the flight controller unplugged: the numbers are real,
    the placement is not, and the UI has to be able to say so."""
    caps = [capture(mean=0.45, cid="a"), capture(mean=0.35, cid="b")]
    bounds = flights.compute_bounds(caps)
    grid = flights.build_grid(caps, bounds, 0.3, 0.1)
    stats = flights.aggregate_stats(caps)
    assert bounds is None
    assert grid["covered"] == 0
    assert stats["mean"] == pytest.approx(0.4)
    assert "no GPS" in flights.summarise(stats["mean"], 10, has_gps=False)["plain"]


# ── mission planning ────────────────────────────────────────────────────────

def test_plan_footprint_agrees_with_the_footprint_helper():
    """One geometry, two callers. They must not drift apart."""
    plan = flights.mission_plan(altitude_m=12)
    fp = flights.footprint({"lat": 0, "lon": 0, "rel_alt_m": 12.0})
    assert plan["footprint_w_m"] == pytest.approx(fp["width_m"], abs=0.01)
    assert plan["footprint_h_m"] == pytest.approx(fp["height_m"], abs=0.01)


def test_trigger_distance_is_the_along_track_footprint_less_overlap():
    plan = flights.mission_plan(altitude_m=12, forward_overlap=0.40)
    assert plan["trigger_distance_m"] == pytest.approx(
        plan["footprint_h_m"] * 0.60, abs=0.06)


def test_line_spacing_is_the_across_track_footprint_less_overlap():
    plan = flights.mission_plan(altitude_m=12, side_overlap=0.30)
    assert plan["line_spacing_m"] == pytest.approx(
        plan["footprint_w_m"] * 0.70, abs=0.06)


def test_the_two_numbers_mission_planner_needs_at_twelve_metres():
    """Regression pin on the pair that gets typed into Mission Planner."""
    plan = flights.mission_plan(altitude_m=12, forward_overlap=0.40,
                               side_overlap=0.30, plot_side_m=100)
    assert plan["trigger_distance_m"] == pytest.approx(6.5, abs=0.1)
    assert plan["line_spacing_m"] == pytest.approx(10.1, abs=0.1)
    assert plan["plot_area_ha"] == pytest.approx(1.0, abs=0.01)


def test_more_overlap_means_more_photos():
    low = flights.mission_plan(12, forward_overlap=0.2, side_overlap=0.2)
    high = flights.mission_plan(12, forward_overlap=0.7, side_overlap=0.7)
    assert high["photos"] > low["photos"]
    assert high["trigger_distance_m"] < low["trigger_distance_m"]


def test_flying_higher_needs_fewer_photos():
    assert (flights.mission_plan(30, plot_side_m=200)["photos"]
            < flights.mission_plan(10, plot_side_m=200)["photos"])


def test_a_bigger_block_needs_more_photos_and_more_time():
    small = flights.mission_plan(12, plot_side_m=100)
    big = flights.mission_plan(12, plot_side_m=300)
    assert big["photos"] > small["photos"]
    assert big["minutes"] > small["minutes"]
    assert big["storage_mb"] > small["storage_mb"]


def test_a_one_hectare_block_fits_inside_one_battery():
    """The reason the survey block is separate from the downloaded vicinity: a
    hectare is a short flight, the whole 38 ha vicinity is not."""
    plan = flights.mission_plan(12, plot_side_m=100)
    assert plan["minutes"] < flights.USABLE_FLIGHT_MINUTES
    assert not plan["warnings"]


def test_planning_the_whole_vicinity_warns_about_the_battery():
    plan = flights.mission_plan(12, plot_side_m=620)
    assert plan["minutes"] > flights.USABLE_FLIGHT_MINUTES
    assert any("minutes of flying" in w for w in plan["warnings"])


def test_plan_warns_when_photos_come_faster_than_the_camera_can_save():
    plan = flights.mission_plan(5, forward_overlap=0.85)
    assert any("faster than the camera" in w for w in plan["warnings"])


@pytest.mark.parametrize("altitude,fragment", [(3, "Below about 5 m"),
                                               (80, "Above 60 m")])
def test_plan_warns_at_silly_altitudes(altitude, fragment):
    plan = flights.mission_plan(altitude, plot_side_m=100)
    assert any(fragment in w for w in plan["warnings"])


def test_plan_clamps_nonsense_inputs_rather_than_crashing():
    """These come off sliders and a number field, so they have to be safe."""
    plan = flights.mission_plan(0, forward_overlap=-1, side_overlap=5,
                               plot_side_m=0)
    assert plan["altitude_m"] >= 1
    assert 0 <= plan["forward_overlap_pct"] <= 90
    assert 0 <= plan["side_overlap_pct"] <= 90
    assert plan["plot_w_m"] >= 10
    assert plan["plot_h_m"] >= 10
    assert plan["photos"] >= 1


def test_plan_reports_gsd_from_the_capture_resolution():
    hi = flights.mission_plan(12, resolution=(3280, 2464))
    lo = flights.mission_plan(12, resolution=(640, 480))
    assert hi["gsd_cm"] < lo["gsd_cm"]
    assert hi["gsd_cm"] == pytest.approx(0.44, abs=0.01)


def test_lines_run_along_the_longer_axis():
    """Each turn costs battery and altitude hold, so a strip is flown the long
    way: 6 lines and 5 turns rather than 20 lines and 19 turns for the same
    ground."""
    long_way = flights.mission_plan(12, plot_w_m=200, plot_h_m=60)
    assert long_way["line_direction"] == "east–west"
    assert long_way["lines"] == pytest.approx(6, abs=1)

    # the same rectangle stood on end plans the same number of lines, just the
    # other way round -- the maths must not depend on which axis is which
    on_end = flights.mission_plan(12, plot_w_m=60, plot_h_m=200)
    assert on_end["line_direction"] == "north–south"
    assert on_end["lines"] == long_way["lines"]
    assert on_end["photos"] == long_way["photos"]
    assert on_end["minutes"] == pytest.approx(long_way["minutes"], abs=0.1)


def test_flying_a_strip_the_wrong_way_would_cost_more_turns():
    """Guards the choice above: stepping along the long axis really is worse."""
    plan = flights.mission_plan(12, plot_w_m=200, plot_h_m=60)
    spacing = plan["line_spacing_m"]
    wrong_way_lines = math.ceil(200 / spacing)
    assert wrong_way_lines > plan["lines"] * 2


def test_a_square_shorthand_still_works():
    square = flights.mission_plan(12, plot_side_m=100)
    explicit = flights.mission_plan(12, plot_w_m=100, plot_h_m=100)
    assert square["photos"] == explicit["photos"]
    assert square["plot_area_ha"] == explicit["plot_area_ha"]


def test_area_is_width_times_height():
    plan = flights.mission_plan(12, plot_w_m=200, plot_h_m=50)
    assert plan["plot_area_ha"] == pytest.approx(1.0, abs=0.01)


def test_plan_falls_back_to_the_placeholder_when_nothing_is_drawn():
    plan = flights.mission_plan(12)
    assert plan["plot_w_m"] == flights.PLACEHOLDER_BLOCK_M
    assert plan["plot_h_m"] == flights.PLACEHOLDER_BLOCK_M


# ── block geometry ──────────────────────────────────────────────────────────

def rect(south=14.1250, west=121.0750, north=14.1259, east=121.0764, **kw):
    base = {"id": "b1", "name": "North block", "south": south, "west": west,
            "north": north, "east": east}
    base.update(kw)
    return base


def test_block_dimensions_are_metres_on_the_ground():
    dims = flights.block_dimensions(rect())
    # 0.0009 deg of latitude is ~100 m; 0.0014 deg of longitude at 14.1 N is ~151 m
    assert dims["height_m"] == pytest.approx(100, abs=2)
    assert dims["width_m"] == pytest.approx(151, abs=2)
    assert dims["area_ha"] == pytest.approx(1.51, abs=0.05)


def test_longitude_shrinks_toward_the_poles():
    """A degree of longitude is 111 km at the equator and nothing at the pole. Get
    this wrong and every block is the wrong width."""
    assert flights.m_per_deg_lon(0) == pytest.approx(111_320, abs=1)
    assert flights.m_per_deg_lon(60) == pytest.approx(55_660, abs=100)
    assert flights.m_per_deg_lon(14.1) == pytest.approx(108_000, abs=500)
    # and it never reaches zero, so no division blows up at the pole
    assert flights.m_per_deg_lon(90) > 0


def test_normalise_sorts_corners_clicked_in_any_order():
    out = flights.normalise_block(rect(south=14.1259, north=14.1250,
                                       west=121.0764, east=121.0750))
    assert out["south"] < out["north"]
    assert out["west"] < out["east"]


def test_normalise_rejects_a_block_too_small_to_fly():
    assert flights.normalise_block(
        rect(north=14.12501, east=121.07501)) is None


@pytest.mark.parametrize("bad", [
    None, "north block", 42, {}, {"south": 14.1}, {"south": "x", "west": 1,
                                                   "north": 2, "east": 3},
])
def test_normalise_rejects_junk(bad):
    assert flights.normalise_block(bad) is None


def test_normalise_names_an_unnamed_block_by_position():
    out = flights.normalise_block({"south": 14.1250, "west": 121.0750,
                                   "north": 14.1259, "east": 121.0764},
                                  index=2)
    assert out["name"] == "Block 3"
    assert out["id"] == "b3"


def test_normalise_clamps_impossible_coordinates():
    out = flights.normalise_block({"south": -400, "north": 400,
                                   "west": -900, "east": 900, "name": "Earth"})
    assert out["south"] >= -90 and out["north"] <= 90
    assert out["west"] >= -180 and out["east"] <= 180


def test_block_by_id_finds_the_right_one():
    blocks = [rect(id="b1"), rect(id="b2", name="South")]
    assert flights.block_by_id(blocks, "b2")["name"] == "South"
    assert flights.block_by_id(blocks, "nope") is None
    assert flights.block_by_id(None, "b1") is None


def test_a_drawn_block_plans_a_flight_that_fits_a_battery():
    """End to end: draw a 1.5 ha plot, and the plan for it is one short flight."""
    dims = flights.block_dimensions(rect())
    plan = flights.mission_plan(12, plot_w_m=dims["width_m"],
                               plot_h_m=dims["height_m"])
    assert plan["plot_area_ha"] == pytest.approx(1.51, abs=0.05)
    assert plan["minutes"] < flights.USABLE_FLIGHT_MINUTES
    assert not plan["warnings"]


# ── practice areas ──────────────────────────────────────────────────────────

def test_every_practice_area_is_complete_and_unique():
    ids = [a["id"] for a in flights.TEST_AREAS]
    assert len(set(ids)) == len(ids)
    for a in flights.TEST_AREAS:
        assert a["name"] and a["note"]
        assert a["w"] >= 10 and a["h"] >= 10


def test_practice_areas_are_all_small_enough_to_rehearse_on_one_battery():
    """The point of them is a flight you can actually complete at school. One that
    warned about the battery would be useless as a rehearsal."""
    for a in flights.TEST_AREAS:
        plan = flights.mission_plan(12, plot_w_m=a["w"], plot_h_m=a["h"])
        assert plan["minutes"] < flights.USABLE_FLIGHT_MINUTES, a["name"]
        assert not plan["warnings"], (a["name"], plan["warnings"])


def test_practice_areas_span_a_useful_range():
    """A basketball court and a hectare should not plan the same flight."""
    smallest = min(flights.TEST_AREAS, key=lambda a: a["w"] * a["h"])
    biggest = max(flights.TEST_AREAS, key=lambda a: a["w"] * a["h"])
    assert (flights.mission_plan(12, plot_w_m=biggest["w"],
                                plot_h_m=biggest["h"])["photos"]
            > flights.mission_plan(12, plot_w_m=smallest["w"],
                                   plot_h_m=smallest["h"])["photos"] * 3)


def test_a_hectare_preset_really_is_a_hectare():
    """The round number people sanity-check photo counts against."""
    area = flights.test_area_by_id("t-hectare")
    assert area["w"] * area["h"] == 10_000
    plan = flights.mission_plan(12, plot_w_m=area["w"], plot_h_m=area["h"])
    assert plan["plot_area_ha"] == pytest.approx(1.0)


def test_test_area_by_id():
    assert flights.test_area_by_id("t-pitch")["w"] == 105
    assert flights.test_area_by_id("nope") is None
    assert flights.test_area_by_id(None) is None


def test_a_square_practice_area_still_picks_a_line_direction():
    """One hectare is square, so neither axis is longer. It must still answer,
    rather than leaving the field blank in the UI."""
    area = flights.test_area_by_id("t-hectare")
    plan = flights.mission_plan(12, plot_w_m=area["w"], plot_h_m=area["h"])
    assert plan["line_direction"] in ("east–west", "north–south")


# ── plain-language summary ───────────────────────────────────────────────────

@pytest.mark.parametrize("mean,fragment", [
    (0.55, "look healthy"),
    (0.30, "a few spots"),
    (0.05, "need attention"),
])
def test_summary_voice_follows_the_mean(mean, fragment):
    assert fragment in flights.summarise(mean, 12)["headline"]


def test_summary_always_offers_something_to_do():
    for mean in (0.6, 0.3, 0.0, -0.5):
        s = flights.summarise(mean, 20)
        assert s["advice"] and s["plain"] and s["headline"]


# ── captures that measured nothing ───────────────────────────────────────────
#
# A capture whose every pixel fell below the signal floor stores a mean of None.
# Counting that as a reading of zero would drag a flight toward "stressed" on the
# strength of a photo that measured nothing, and painting its cell would invent
# ground truth -- the same reason unvisited cells stay null.

def _capture(cid, lat, lon, mean):
    return {"id": cid, "geo": {"lat": lat, "lon": lon},
            "stats": None if mean is None else {
                "mean": mean, "min": mean, "max": mean, "std": 0.0,
                "healthy_pct": 100.0, "moderate_pct": 0.0, "stressed_pct": 0.0}}


def test_has_reading_separates_measured_from_unmeasured():
    assert flights.has_reading(_capture("a", 0, 0, 0.5))
    assert not flights.has_reading(_capture("b", 0, 0, None))
    assert not flights.has_reading({"id": "c"})
    # A capture predating the floor always carries a number, so it still counts.
    assert flights.has_reading({"stats": {"mean": 0.0}})


def test_an_unreadable_capture_is_not_averaged_in_as_zero():
    good = [_capture("a", 0, 0, 0.6), _capture("b", 0, 0, 0.6)]
    with_dark = good + [_capture("c", 0, 0, None)]
    assert (flights.aggregate_stats(with_dark)["mean"]
            == pytest.approx(flights.aggregate_stats(good)["mean"]))
    assert flights.aggregate_stats(with_dark)["mean"] == pytest.approx(0.6)


def test_a_cell_holding_only_unreadable_captures_stays_null():
    bounds = {"south": 0.0, "west": 0.0, "north": 1.0, "east": 1.0}
    grid = flights.build_grid([_capture("a", 0.5, 0.5, None)], bounds, 0.3, 0.1)
    assert all(c is None for c in grid["cells"]), \
        "an unmeasured capture must not paint a cell"
    assert grid["covered"] == 0


# ── several downloaded areas in one tile tree ────────────────────────────────
#
# Tiles are a global {z}/{x}/{y} tree, so downloading a second vicinity adds to
# the map instead of replacing it. The manifest therefore has to remember every
# area, or the coverage outline gets drawn around whichever one was fetched last
# -- which, with the farm and the campus 60 km apart, means drawing the edge of
# the imagery over ground that has none.

FARM_AREA = {"centre": [14.1265, 121.0768], "box_m": 620, "area_ha": 38.4,
             "tile_bounds": {"south": 14.12, "west": 121.07,
                             "north": 14.13, "east": 121.08}}
SCHOOL_AREA = {"centre": [13.94291, 121.14773], "box_m": 1500, "area_ha": 225.0,
               "tile_bounds": {"south": 13.93, "west": 121.14,
                               "north": 13.95, "east": 121.16}}


def test_a_second_download_does_not_forget_the_first():
    areas = tiles.imagery_areas({"areas": [FARM_AREA]}, SCHOOL_AREA)
    assert [a["box_m"] for a in areas] == [620, 1500]


def test_a_manifest_written_before_multiple_areas_is_carried_forward():
    """The farm's imagery was downloaded before this feature existed. Its record
    lives in the manifest's top-level keys and must survive the next download."""
    areas = tiles.imagery_areas(dict(FARM_AREA), SCHOOL_AREA)
    assert [a["box_m"] for a in areas] == [620, 1500]


def test_downloading_the_same_place_twice_replaces_rather_than_repeats():
    areas = tiles.imagery_areas({"areas": [FARM_AREA, SCHOOL_AREA]}, SCHOOL_AREA)
    assert len(areas) == 2


def test_coverage_describes_the_area_you_are_looking_at():
    areas = [FARM_AREA, SCHOOL_AREA]
    assert tiles.area_for(areas, (14.1265, 121.0768))["box_m"] == 620
    assert tiles.area_for(areas, (13.94291, 121.14773))["box_m"] == 1500


def test_a_centre_outside_every_area_falls_back_to_the_nearest():
    """Somewhere with no imagery at all must still resolve, not raise -- the map
    has to render before anything has been downloaded for that spot."""
    assert tiles.area_for([FARM_AREA, SCHOOL_AREA], (0.0, 0.0)) is not None
    assert tiles.area_for([], (14.0, 121.0)) is None


# ── zoom levels the imagery source doesn't actually have ─────────────────────
#
# Esri does not answer 404 where it has no imagery -- it serves one placeholder
# image reading "Map data not yet available". Downloaded blindly that tiles a
# picture of the words "no imagery" across the map, which reads as a broken
# dashboard. It has real zoom 19 over the Tanauan farm but stops at 18 over
# De La Salle Lipa, so this is not hypothetical.

def _write_tiles(root, z, x_range, y_range, content):
    for i, x in enumerate(x_range):
        for j, y in enumerate(y_range):
            p = root / str(z) / str(x)
            p.mkdir(parents=True, exist_ok=True)
            body = content if isinstance(content, bytes) else content(i, j)
            (p / f"{y}.jpg").write_bytes(body)


def test_a_zoom_of_identical_tiles_is_recognised_as_placeholder(tmp_path):
    _write_tiles(tmp_path, 19, range(0, 4), range(0, 4), b"same-placeholder")
    assert tiles.placeholder_zooms(tmp_path, {19: (0, 3, 0, 3)}) == [19]


def test_real_imagery_is_not_mistaken_for_a_placeholder(tmp_path):
    _write_tiles(tmp_path, 18, range(0, 4), range(0, 4),
                 lambda i, j: f"tile-{i}-{j}".encode())
    assert tiles.placeholder_zooms(tmp_path, {18: (0, 3, 0, 3)}) == []


def test_a_handful_of_tiles_is_not_enough_to_condemn_a_zoom(tmp_path):
    """A 2x2 patch of sea or bare field is legitimately uniform. Judging that as
    placeholder would throw away real imagery, so require a decent sample."""
    _write_tiles(tmp_path, 19, range(0, 2), range(0, 2), b"uniform")
    assert tiles.placeholder_zooms(tmp_path, {19: (0, 1, 0, 1)}) == []


def test_coverage_reports_the_zooms_that_location_actually_has():
    """map.js takes maxNativeZoom from this. If the campus claimed zoom 19 the
    map would request placeholder tiles instead of upscaling real zoom-18."""
    farm = dict(FARM_AREA, zooms=[16, 17, 18, 19])
    school = dict(SCHOOL_AREA, zooms=[16, 17, 18])
    assert tiles.area_for([farm, school], (14.1265, 121.0768))["zooms"][-1] == 19
    assert tiles.area_for([farm, school], (13.94291, 121.14773))["zooms"][-1] == 18


# ── polygon blocks ───────────────────────────────────────────────────────────
#
# A block used to be an axis-aligned rectangle, which cannot describe a plot that
# runs diagonally or has more than four corners. Planning on the bounding box of
# a 45-degree plot means flying 2.4x the ground, most of it the neighbour's.

FARM_LAT, FARM_LON = 14.1265, 121.0768


def _poly(xy_m, rotate_deg=0.0, origin=(FARM_LAT, FARM_LON)):
    """A polygon from metres, optionally rotated, as lat/lon."""
    th = math.radians(rotate_deg)
    c, s = math.cos(th), math.sin(th)
    return flights.to_latlon(
        [(x * c - y * s, x * s + y * c) for x, y in xy_m], origin)


RECT_100x40 = [(0, 0), (100, 0), (100, 40), (0, 40)]


def test_polygon_area_is_the_real_area_not_the_bounding_box():
    rotated = _poly(RECT_100x40, 45)
    assert flights.polygon_area_m2(rotated) == pytest.approx(4000, rel=0.01)
    lats = [p[0] for p in rotated]
    lons = [p[1] for p in rotated]
    box = ((max(lons) - min(lons)) * flights.m_per_deg_lon(FARM_LAT)
           * (max(lats) - min(lats)) * flights.M_PER_DEG_LAT)
    assert box > 9000, "the bounding box really is more than twice the plot"


@pytest.mark.parametrize("deg", [0, 17, 30, 45, 90, 137])
def test_the_plot_is_measured_the_same_whichever_way_it_lies(deg):
    """Rotating a field does not change it. If any of these drift, the planner is
    measuring the map's axes rather than the plot's."""
    _, long_m, short_m = flights.min_area_rect(
        flights.to_local_m(_poly(RECT_100x40, deg)))
    assert long_m == pytest.approx(100, rel=0.02)
    assert short_m == pytest.approx(40, rel=0.02)


@pytest.mark.parametrize("deg", [0, 30, 45, 90])
def test_flight_lines_are_the_same_count_and_length_at_any_angle(deg):
    lines = flights.survey_lines(_poly(RECT_100x40, deg), 10.0)
    assert len(lines) == 4
    for (a, b) in lines:
        dx = (b[1] - a[1]) * flights.m_per_deg_lon(a[0])
        dy = (b[0] - a[0]) * flights.M_PER_DEG_LAT
        assert math.hypot(dx, dy) == pytest.approx(100, rel=0.02)


def test_an_exact_fit_does_not_gain_a_line_to_rounding():
    """40 m across at 10 m spacing is four lines. Rotating into the line frame
    leaves it measuring 40.0000000001, and a bare ceil() would charge five."""
    assert len(flights.survey_lines(_poly(RECT_100x40, 30), 10.0)) == 4


def test_lines_stop_at_the_edges_of_a_concave_plot():
    """A C-shaped block: lines crossing the notch must come back as two separate
    legs. Flying the gap would waste battery over ground that isn't the plot."""
    c_shape = _poly([(0, 0), (100, 0), (100, 40), (70, 40),
                     (70, 15), (30, 15), (30, 40), (0, 40)])
    legs = flights.survey_lines(c_shape, 5.0)
    assert len(legs) > 8, "some sweeps must have split in two"
    assert flights.polygon_area_m2(c_shape) == pytest.approx(3000, rel=0.02)


def test_planning_a_diagonal_plot_beats_planning_its_bounding_box():
    """The whole point. A 200x60 m plot at 45 degrees fits one battery when
    planned on its outline, and looks like a 20-minute flight on its box."""
    poly = _poly([(0, 0), (200, 0), (200, 60), (0, 60)], 45)
    lats = [p[0] for p in poly]
    lons = [p[1] for p in poly]
    box = flights.mission_plan(
        12,
        plot_w_m=(max(lons) - min(lons)) * flights.m_per_deg_lon(FARM_LAT),
        plot_h_m=(max(lats) - min(lats)) * flights.M_PER_DEG_LAT)
    real = flights.mission_plan(12, polygon=poly)
    assert real["minutes"] < box["minutes"] / 2
    assert real["lines"] < box["lines"] / 2
    assert real["plot_area_ha"] == pytest.approx(1.2, rel=0.05)


def test_a_polygon_rectangle_plans_identically_to_the_rectangle_path():
    """Backwards compatibility, pinned: an axis-aligned polygon must produce the
    same plan as the width/height path it replaces."""
    plain = flights.mission_plan(12, plot_w_m=200, plot_h_m=60)
    poly = flights.mission_plan(12, polygon=_poly([(0, 0), (200, 0),
                                                   (200, 60), (0, 60)]))
    for key in ("lines", "photos", "path_m", "minutes", "line_direction"):
        assert poly[key] == plain[key], f"{key} drifted"


def test_flight_lines_are_named_in_words_when_they_are_cardinal():
    poly = flights.mission_plan(12, polygon=_poly([(0, 0), (200, 0),
                                                   (200, 60), (0, 60)]))
    assert poly["line_direction"] == "east–west"
    diagonal = flights.mission_plan(12, polygon=_poly(RECT_100x40, 45))
    assert "°" in diagonal["line_direction"], "a diagonal needs a bearing"


# ── the block model ──────────────────────────────────────────────────────────

def test_a_rectangle_block_gains_points_so_there_is_one_kind_of_shape():
    """Blocks saved before polygons existed have only bounds. They must come
    forward with an outline, or half the code would need a second path."""
    b = flights.normalise_block({"south": 14.12, "west": 121.07,
                                 "north": 14.13, "east": 121.08})
    assert len(b["points"]) == 4
    assert flights.polygon_area_m2([tuple(p) for p in b["points"]]) > 0


def test_a_polygon_block_keeps_its_outline_and_derives_its_bounds():
    pts = [[14.120, 121.070], [14.126, 121.078], [14.121, 121.081]]
    b = flights.normalise_block({"name": "Wedge", "points": pts})
    assert len(b["points"]) == 3
    assert b["south"] == pytest.approx(14.120)
    assert b["north"] == pytest.approx(14.126)
    assert b["east"] == pytest.approx(121.081)


def test_three_clicks_in_a_line_are_not_a_plot():
    """Zero area, so there is nothing to fly — a slip while drawing."""
    assert flights.normalise_block({"points": [[14.12, 121.07], [14.13, 121.08],
                                               [14.14, 121.09]]}) is None


def test_a_double_click_while_drawing_does_not_break_the_outline():
    pts = [[14.120, 121.070], [14.120, 121.070], [14.126, 121.078],
           [14.121, 121.081]]
    b = flights.normalise_block({"points": pts})
    assert len(b["points"]) == 3, "the repeated corner must be dropped"


def test_a_closed_ring_is_stored_open():
    """Leaflet hands back the first point again to close the ring; storing it
    would leave a zero-length edge for the line clipper to trip over."""
    pts = [[14.120, 121.070], [14.126, 121.078], [14.121, 121.081],
           [14.120, 121.070]]
    assert len(flights.normalise_block({"points": pts})["points"]) == 3


def test_block_dimensions_report_the_plots_own_axes():
    rotated = _poly(RECT_100x40, 40)
    b = flights.normalise_block({"points": [list(p) for p in rotated]})
    d = flights.block_dimensions(b)
    assert d["width_m"] == pytest.approx(100, rel=0.02)
    assert d["height_m"] == pytest.approx(40, rel=0.02)
    assert d["area_ha"] == pytest.approx(0.4, rel=0.02)
    assert d["vertices"] == 4
