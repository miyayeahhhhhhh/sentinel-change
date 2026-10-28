import numpy as np
from cs.config import EncroachParams
from cs.encroach import run_encroachment
from cs.flood import run_flood
from cs.models import SCORE_LABEL, State
from cs.sample_data import flood_scenes, s2_scenes


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
    assert r.evidence["seasonal_check"] == "not_possible" and r.state == State.POTENTIAL
    assert any("seasonal" in x.lower() for x in r.limitations)


def test_cloudy_scenes_are_excluded_from_analysis():
    g, s = s2_scenes(); r = run_encroachment(s, g)
    unusable = [x for x in r.evidence["scenes"] if not x["usable"]]
    assert len(unusable) == 3 and all(x["valid_frac"] < 0.6 for x in unusable)


def test_wrong_sensor_fails():
    g, s = flood_scenes(); assert run_encroachment(s, g).state == State.FAILED
