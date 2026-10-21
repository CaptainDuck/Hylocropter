"""The "For the technical team" checks: each one is worked out from what the
flight recorded, so fixing the cause turns it into a tick on its own."""

import report


def _cap(cid="c", k=0.4, correct=True, nir=36.0, blue=30.0, nir_max=110.0,
         lat=14.1306, lon=121.0823, mismatches=(), dev=False):
    return {
        "id": cid,
        "stats": {"mean": 0.38, "healthy_pct": 99.0, "moderate_pct": 1.0,
                  "stressed_pct": 0.0},
        "settings": {"correct_nir_leakage": correct, "nir_leak_coef": k,
                     "exposure_us": 1900, "gain": 1.5, "dev_mode": dev},
        "channels": {"nir": nir, "blue": blue, "nir_max": nir_max},
        "geo": {"lat": lat, "lon": lon},
        "control_check": {"mismatches": list(mismatches),
                          "isp_neutralised": True},
    }


def _flight(fid="F1", name="farm 1", started="2026-10-13T15:18:01"):
    return {"id": fid, "name": name, "started_at": started,
            "stats": {"mean": 0.38},
            "thresholds": {"healthy": 0.3, "moderate": 0.1}}


def _titles(checks, level="warn"):
    return {c["title"]["en"] for c in checks if c["level"] == level}


def test_an_unmeasured_k_says_how_much_the_verdict_leans_on_it():
    caps = [_cap()]
    out = report.checks(_flight(), caps, [(_flight(), caps)], {})
    k = next(c for c in out if c["title"]["en"].startswith("Leak correction k was never"))
    # NIR 36 / blue 30: about +0.40 corrected, +0.09 raw -- a different band.
    assert "+0.40" in k["detail"] and "+0.09" in k["detail"]
    assert "the verdict depends on this number" in k["detail"]


def test_a_measured_k_is_a_tick():
    caps = [_cap()]
    out = report.checks(_flight(), caps, [(_flight(), caps)],
                        {"setup_done_steps": ["leak"]})
    assert "Leak correction k was measured" in _titles(out, "ok")


def test_k_changing_between_flights_is_flagged():
    a, b = _flight("A", started="2026-09-28T12:43:00"), _flight("B")
    history = [(a, [_cap(k=0.45)]), (b, [_cap(k=0.4)]),
               (_flight("C", started="2026-10-09T16:03:00"), [_cap(k=0.4)])]
    out = report.checks(b, [_cap(k=0.4)], history, {})
    detail = next(c["detail"] for c in out if c["title"]["en"] == "k changed between flights")
    assert "k = 0.45 on 28/09 12:43" in detail


def test_dark_photos_and_generic_thresholds_are_flagged():
    caps = [_cap()]
    out = report.checks(_flight(), caps, [(_flight(), caps)], {})
    assert {"Photos are dark", "Thresholds are generic"} <= _titles(out)
    bright = [_cap(nir_max=200)]
    out = report.checks(_flight(), bright, [(_flight(), bright)], {})
    assert "Exposure is usable" in _titles(out, "ok")


def test_a_flight_off_the_imagery_and_outside_its_block_is_flagged():
    caps = [_cap()]
    areas = [{"tile_bounds": {"south": 14.12, "west": 121.07,
                              "north": 14.13, "east": 121.08}}]
    block = {"name": "farm 1", "south": 14.129, "west": 121.080,
             "north": 14.1305, "east": 121.0822}
    out = report.checks(_flight(), caps, [(_flight(), caps)], {},
                        tile_areas=areas, blocks=[block])
    assert {"Flight is outside the offline imagery",
            "Flight is mostly outside its block"} <= _titles(out)
    inside = [_cap(lat=14.125, lon=121.075)]
    block2 = dict(block, south=14.12, west=121.07)
    out = report.checks(_flight(), inside, [(_flight(), inside)], {},
                        tile_areas=areas, blocks=[block2])
    assert not {"Flight is outside the offline imagery",
                "Flight is mostly outside its block"} & _titles(out)


def test_the_camera_lock_check_passes_or_fails_on_the_records():
    good = [_cap()]
    assert "Camera lock and ISP check pass" in _titles(
        report.checks(_flight(), good, [], {}), "ok")
    bad = [_cap(mismatches=["ExposureTime"])]
    assert "Camera lock check failed" in _titles(
        report.checks(_flight(), bad, [], {}))


def test_warnings_come_before_ticks():
    caps = [_cap()]
    levels = [c["level"] for c in report.checks(_flight(), caps, [], {})]
    assert levels == sorted(levels, key=lambda lv: lv != "warn")
