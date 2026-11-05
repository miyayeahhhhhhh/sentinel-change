import numpy as np
from dataclasses import replace
from cs.config import EncroachParams
from cs.encroach import run_encroachment
from cs.models import SCORE_LABEL, State
from cs.sample_data import make_grid, s2_scenes
from tests.synth import synthetic_s1


def test_persistent_loss_found_and_seasonal_fringe_suppressed():
    g, s = s2_scenes("persistent_loss"); r = run_encroachment(s, g)
    assert r.state in (State.POTENTIAL, State.HIGH) and r.score_label == SCORE_LABEL
    ys, xs = np.where(r.arrays["loss"])
    assert xs.min() >= 128 and ys.min() >= 85 and ys.max() <= 115      # only the filled sector
    assert r.evidence["loss_ha"] > 3 and r.evidence["seasonal_check"] == "performed"
    assert r.evidence["class"] in ("E1", "E2")


def test_seasonal_only_is_no_change():
    g, s = s2_scenes("seasonal_only"); r = run_encroachment(s, g)
    assert r.state == State.NO_CHANGE and r.score is None


def test_sparse_recent_observations_not_assessed():
    g, s = s2_scenes("sparse"); r = run_encroachment(s, g)
    assert r.state == State.NOT_ASSESSED and r.evidence["class"] == "E4"


def test_without_wet_season_never_high_priority():
    g, s = s2_scenes("no_wet_season"); r = run_encroachment(s, g)
    assert r.evidence["seasonal_check"] == "performed"
    assert r.evidence["wet_season_check"] == "not_possible" and r.state == State.POTENTIAL
    assert any("seasonal" in x.lower() for x in r.limitations)


def test_cloudy_scenes_are_excluded_from_analysis():
    g, s = s2_scenes(); r = run_encroachment(s, g)
    unusable = [x for x in r.evidence["scenes"] if not x["usable"]]
    assert len(unusable) == 3 and all(x["valid_frac"] < 0.6 for x in unusable)


def test_scl_dark_and_low_probability_cloud_pixels_are_unobserved():
    for scl_class in (2, 7):
        g, scenes = s2_scenes("persistent_loss")
        for scene in scenes:
            scene.bands["scl"][100, 150] = scl_class
        result = run_encroachment(scenes, g)
        assert (result.arrays["water_stack"][:, 100, 150] == 255).all()
        assert not result.arrays["loss"][100, 150]


def test_month_matched_baseline_missing_yields_unassessed():
    g, scenes = s2_scenes("no_wet_season")
    scenes = [s for s in scenes if s.acquired.year >= 2025 or s.acquired.month in range(6, 11)]
    result = run_encroachment(scenes, g)
    assert result.state == State.NOT_ASSESSED
    assert result.evidence["seasonal_comparison"]["status"] == "insufficient"


def test_single_temporary_dry_pass_does_not_survive_persistence_rule():
    g, scenes = s2_scenes("persistent_loss")
    recent = [s for s in scenes if s.acquired.year >= 2025]
    for scene in recent[:-1]:
        scene.bands["green"][85:116, 128:][...] = 0.08
        scene.bands["swir1"][85:116, 128:][...] = 0.02
    result = run_encroachment(scenes, g)
    assert not result.arrays["loss"][100, 150]


def test_same_date_scene_overlap_is_mosaicked_not_counted_as_an_extra_pass():
    g, scenes = s2_scenes("persistent_loss")
    duplicate = replace(scenes[-1], scene_id="DUPLICATE-TILE-SAME-DATE")
    result = run_encroachment(scenes + [duplicate], g)
    assert len(result.evidence["scenes"]) == len(scenes)
    daily = next(row for row in result.evidence["scenes"] if len(row["source_scene_ids"]) == 2)
    assert daily["source_scene_count"] == 2


def test_wrong_sensor_fails():
    g = make_grid(); assert run_encroachment(synthetic_s1(g), g).state == State.FAILED
