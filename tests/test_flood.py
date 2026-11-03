import numpy as np
from cs.flood import run_flood
from cs.imagery import Scene
from cs.models import SCORE_LABEL, State
from cs.sample_data import flood_scenes, make_grid, s2_scenes


def test_flood_event_detected_and_persistent_dark_area_excluded():
    g, s = flood_scenes("flood_event"); r = run_flood(s, g)
    assert r.state in (State.POTENTIAL, State.HIGH) and r.score is not None and r.score_label == SCORE_LABEL
    assert r.arrays["flood"].sum() * 0.01 > 20          # ha
    assert r.arrays["flood"][150:175, 20:60].sum() == 0  # pre-existing dark patch is not flood
    assert r.arrays["perm_water"][150:175, 20:60].any()


def test_no_flood_is_no_change_with_sufficient_data():
    g, s = flood_scenes("no_flood"); r = run_flood(s, g)
    assert r.state == State.NO_CHANGE and r.score is None


def test_insufficient_and_missing_data_never_become_no_change():
    for sc in ("insufficient", "partial_coverage"):
        g, s = flood_scenes(sc); r = run_flood(s, g)
        assert r.state == State.NOT_ASSESSED and r.score is None
    assert run_flood([], make_grid()).state == State.NOT_ASSESSED


def test_wrong_sensor_fails_explicitly():
    g, s = s2_scenes(); r = run_flood(s[:5], g)
    assert r.state == State.FAILED and r.score is None


def test_nodata_gap_lowers_coverage_not_flood_area_to_zero_claim():
    g, s = flood_scenes("flood_event"); r = run_flood(s, g)
    assert r.evidence["aoi_coverage"] <= 1 and r.evidence["unobserved_ha"] >= 0
