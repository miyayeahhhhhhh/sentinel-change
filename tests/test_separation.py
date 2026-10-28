import ast
import dataclasses
from pathlib import Path
import pytest
from cs import encroach, flood
from cs.config import EncroachParams, FloodParams
from cs.models import Pipeline, Result, State
from cs.sample_data import make_grid


def _imports(mod):
    tree = ast.parse(Path(mod.__file__).read_text())
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            names.add((n.module or "") if n.level == 0 else "." * n.level + (n.module or ""))
            names.update(a.name for a in n.names)
        elif isinstance(n, ast.Import):
            names.update(a.name for a in n.names)
    return names


def test_pipelines_do_not_import_each_other():
    assert not ({"flood", "cs.flood"} & _imports(encroach))
    assert not ({"encroach", "cs.encroach"} & _imports(flood))


def test_separate_params_weights_and_scorers():
    assert FloodParams is not EncroachParams
    assert flood.FLOOD_WEIGHTS != encroach.ENC_WEIGHTS
    assert flood._flood_score is not encroach._enc_score
    assert abs(sum(flood.FLOOD_WEIGHTS.values()) - 1) < 1e-9 and abs(sum(encroach.ENC_WEIGHTS.values()) - 1) < 1e-9


def test_score_only_with_flagged_states():
    kw = dict(pipeline=Pipeline.FLOOD, summary="", evidence={}, limitations=[], params={}, grid=make_grid(), arrays={}, primary_layer="flood")
    with pytest.raises(ValueError):
        Result(state=State.NO_CHANGE, score=50.0, **kw)
    with pytest.raises(ValueError):
        Result(state=State.HIGH, score=None, **kw)


def test_score_weights_renormalise_and_cap_when_evidence_missing():
    comp = {k: 1.0 for k in flood.FLOOD_WEIGHTS}
    assert flood._flood_score(comp, FloodParams())[0] == 100.0
    sparse = {k: (1.0 if k == "recency" else None) for k in flood.FLOOD_WEIGHTS}
    score, cov, _ = flood._flood_score(sparse, FloodParams())
    assert cov < 0.6 and score <= 50.0
