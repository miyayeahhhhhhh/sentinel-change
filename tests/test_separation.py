import pytest
from cs import encroach
from cs.models import Pipeline, Result, State
from cs.sample_data import make_grid


def test_single_pipeline_only():
    assert [p.value for p in Pipeline] == ["encroachment"]


def test_score_only_with_flagged_states():
    kw = dict(pipeline=Pipeline.ENCROACH, summary="", evidence={}, limitations=[], params={}, grid=make_grid(), arrays={}, primary_layer="loss")
    with pytest.raises(ValueError):
        Result(state=State.NO_CHANGE, score=50.0, **kw)
    with pytest.raises(ValueError):
        Result(state=State.HIGH, score=None, **kw)


def test_score_weights_account_for_unavailable_evidence_without_renormalising():
    from cs.config import EncroachParams
    assert abs(sum(encroach.ENC_WEIGHTS.values()) - 1) < 1e-9
    comp = {k: 1.0 for k in encroach.ENC_WEIGHTS}
    assert encroach._enc_score(comp, EncroachParams())[0] == 100.0
    sparse = {k: (1.0 if k == "recency" else None) for k in encroach.ENC_WEIGHTS}
    score, cov, _ = encroach._enc_score(sparse, EncroachParams())
    assert cov < 0.6 and score == 5.0
