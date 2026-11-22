from cs.confidence import estimate_detection_confidence
from cs.config import Settings
from cs.workflow import execute_encroachment


def evidence(values, *, seasonal="performed", contradicted=None):
    return {
        "score_components": [{"name": name, "normalised": value, "available": value is not None} for name, value in values.items()],
        "seasonal_check": seasonal,
        "evidence_fusion": {"contradicted_by": contradicted or []},
        "scenes": [{"usable": True}, {"usable": True}, {"usable": False}],
    }


COMPLETE = {"persistence": .9, "footprint": .9, "spatial_coherence": .9, "magnitude": .9,
            "image_quality": .9, "recency": .9, "sensor_agreement": .9, "corroboration": .9,
            "landcover_transition": .9, "historical_water": .9}


def test_complete_supporting_evidence_is_strong_and_bounded():
    result = estimate_detection_confidence("high_priority_review", evidence(COMPLETE))
    assert result.score == 91.5 and result.category == "Strong supporting evidence"
    assert result.observation_count == 2 and 0 <= result.score <= 100


def test_partial_evidence_is_not_treated_as_negative_but_is_capped():
    values = {**COMPLETE, "sensor_agreement": None, "corroboration": None, "landcover_transition": None, "historical_water": None}
    result = estimate_detection_confidence("potential_change_detected", evidence(values))
    assert result.score == 79.0 and result.category == "Moderate supporting evidence"
    assert "Independent supporting source" in result.missing


def test_missing_or_single_group_evidence_is_insufficient():
    values = {name: .8 for name in ("persistence", "footprint", "spatial_coherence", "magnitude")}
    result = estimate_detection_confidence("potential_change_detected", evidence(values, seasonal="not_possible"))
    assert result.score is None and result.category == "Insufficient evidence"


def test_conflicting_evidence_is_reflected_and_no_change_is_not_scored():
    conflict = estimate_detection_confidence("potential_change_detected", evidence(COMPLETE, contradicted=["dynamic_world"]))
    assert conflict.score == 50.0 and conflict.conflicting
    absent = estimate_detection_confidence("not_assessed", evidence(COMPLETE))
    assert absent.score is None and absent.category == "Not applicable"


def test_real_sample_run_analysis_result_is_consumed_without_pipeline_changes(tmp_path):
    result, _ = execute_encroachment(Settings(db_path=tmp_path / "cases.db", output_dir=tmp_path / "outputs"), "persistent_loss")
    estimate = estimate_detection_confidence(result.state.value, result.evidence)
    assert estimate.score is None or 0 <= estimate.score <= 100
