"""Evidence fusion inside the Sentinel-2 pipeline, and the registry workflow with stand-in services (synthetic; no remote)."""
import dataclasses
import json
from pathlib import Path

import numpy as np
import pytest

from cs.config import EncroachParams
from cs.db import CaseStore
from cs.evidence import EvidenceLayer, Unavailable
from cs.evidence.stac import PlanetaryComputer
from cs.models import State
from cs.sample_data import s2_scenes
from cs.encroach import run_encroachment
from cs.workflow import execute_encroachment_for_waterbody
from tests.m2_fixtures import AS_OF, FakeDW, fake_pc, jrc_item, make_jrc_reader, make_s1_reader, make_transport, s1_items
from tests.test_stabilization import _FixtureProvider, reg  # noqa: F401  (fixture)


def _layers(loss, grid, **bad):
    obs = np.ones(loss.shape, bool)
    enc = lambda v: np.where(loss, v, 0).astype("uint8")
    L = {"sentinel1": EvidenceLayer("sentinel1", "raster", grid, {"sar_water_loss": enc(1), "sar_rec_water_freq": np.where(loss, 0.05, 0.9).astype("float32")}, obs, {"dataset": "SYNTHETIC"}),
         "dynamic_world": EvidenceLayer("dynamic_world", "raster", grid, {"dw_transition": enc(1), "dw_water_prob_rec": np.full(loss.shape, .05, "float32"),
                                        "dw_built_prob_rec": np.full(loss.shape, .8, "float32"), "dw_bare_prob_rec": np.full(loss.shape, .05, "float32")}, obs, {"dataset": "SYNTHETIC"}),
         "jrc_gsw": EvidenceLayer("jrc_gsw", "raster", grid, {"jrc_class": enc(2)}, obs, {"dataset": "SYNTHETIC", "coverage_end": "2021-12-31"})}
    for k, v in bad.items():
        L[k] = v
    return L


@pytest.fixture(scope="module")
def base():
    g, sc = s2_scenes("persistent_loss")
    return g, sc, run_encroachment(sc, g)


def test_supporting_sources_enter_score_and_provenance(base):
    g, sc, b = base
    r = run_encroachment(sc, g, evidence=_layers(b.arrays["loss"].astype(bool), g))
    ev = r.evidence
    assert ev["evidence_fusion"]["supported_by"] == ["sentinel1", "dynamic_world", "jrc_gsw"]
    names = {c["name"]: c for c in ev["score_components"]}
    assert all(names[k]["available"] for k in ("sensor_agreement", "landcover_transition", "historical_water"))
    assert ev["evidence_sources"]["dynamic_world"] == "used" and r.state == State.HIGH
    assert {"sar_water_loss", "dw_transition", "jrc_class"} <= set(r.arrays)
    assert {"sar_support", "dw_support", "jrc_permanent"} <= set(r.polygons.columns)


def test_unavailable_sources_do_not_change_result_or_invent_confidence(base):
    g, sc, b = base
    out = {k: Unavailable(k, "simulated outage") for k in ("sentinel1", "dynamic_world", "jrc_gsw")}
    r = run_encroachment(sc, g, evidence=out)
    assert r.state == b.state and r.evidence["loss_ha"] == b.evidence["loss_ha"]
    assert r.score == pytest.approx(b.score)                       # missing sources: weights dropped, nothing zero-filled or defaulted
    comps = {c["name"]: c["available"] for c in r.evidence["score_components"]}
    assert not any(comps[k] for k in ("sensor_agreement", "landcover_transition", "historical_water"))
    assert all("simulated outage" in v for k, v in r.evidence["evidence_sources"].items() if k != "sentinel2_mndwi")
    assert sum("did not contribute" in x for x in r.limitations) == 3


def test_single_source_detection_is_capped_when_independent_support_required(base):
    g, sc, b = base
    p = dataclasses.replace(EncroachParams(), require_independent_support=True)
    r = run_encroachment(sc, g, p, evidence={k: Unavailable(k, "x") for k in ("sentinel1", "dynamic_world", "jrc_gsw")})
    assert r.state == State.POTENTIAL and r.score <= 50 and any("Single-source" in n for n in r.evidence["support_assessment"]["notes"])
    ok = run_encroachment(sc, g, p, evidence=_layers(b.arrays["loss"].astype(bool), g))
    assert ok.state == State.HIGH


def test_contradicting_source_blocks_high(base):
    g, sc, b = base
    loss = b.arrays["loss"].astype(bool)
    bad = EvidenceLayer("dynamic_world", "raster", g, {"dw_transition": np.where(loss, 0, 0).astype("uint8"), "dw_water_prob_rec": np.full(loss.shape, .9, "float32"),
                        "dw_built_prob_rec": np.full(loss.shape, .02, "float32"), "dw_bare_prob_rec": np.full(loss.shape, .02, "float32")}, np.ones(loss.shape, bool), {})
    r = run_encroachment(sc, g, evidence=_layers(loss, g, dynamic_world=bad))
    assert r.evidence["evidence_fusion"]["contradicted_by"] == ["dynamic_world"] and r.state == State.POTENTIAL and r.score <= 50


def test_no_candidate_loss_stays_no_change_regardless_of_sources():
    g, sc = s2_scenes("seasonal_only")
    r = run_encroachment(sc, g, evidence={"sentinel1": Unavailable("sentinel1", "x")})
    assert r.state in (State.NO_CHANGE, State.NOT_ASSESSED) and r.score is None


def _run(settings, registry, wb, **kw):
    settings = dataclasses.replace(settings, mode="live")
    return settings, execute_encroachment_for_waterbody(settings, "lake", registry=registry, as_of=AS_OF, provider=_FixtureProvider(settings, wb), **kw)


def test_registry_workflow_with_all_three_sources_records_provenance(settings, reg):
    registry, wb = reg
    s, (res, cid) = _run(settings, registry, wb, pc=fake_pc(s1=s1_items(), jrc_items=[jrc_item()]), dw_client=FakeDW(wb),
                         s1_reader=make_s1_reader(wb), jrc_reader=make_jrc_reader(wb))
    ev = res.evidence
    assert set(ev["evidence_fusion"]["supported_by"]) == {"sentinel1", "dynamic_world", "jrc_gsw"} and res.state == State.HIGH
    assert all(ev["evidence_sources"][k] == "used" for k in ("sentinel1", "dynamic_world", "jrc_gsw"))
    prov = ev["evidence_fusion"]["sources"]
    assert prov["sentinel1"]["provenance"]["orbit"]["relative_orbit"] == 125 and prov["jrc_gsw"]["provenance"]["coverage_end"].startswith("2021")
    assert prov["dynamic_world"]["provenance"]["baseline"]["n_images"] == 6
    store = CaseStore(s.db_path); ex = json.loads(store.get_case(cid)["exports"])
    for k in ("evidence_sar_water_loss_tif", "evidence_dw_transition_tif", "evidence_jrc_class_tif"):
        assert Path(ex[k]).exists()
    saved = json.loads(Path(ex["evidence_json"]).read_text()) if "evidence_json" in ex else None
    gj = json.loads(Path(ex["polygons_geojson"]).read_text())
    assert gj["features"] and "sar_support" in gj["features"][0]["properties"]
    # the detected loss stays inside the AOI polygon and inside the lake
    assert not (res.arrays["loss"] & ~res.arrays["aoi"]).any()


def test_registry_workflow_with_everything_down_is_capped_and_declared(settings, reg):
    registry, wb = reg
    down = PlanetaryComputer("u", "s", transport=make_transport(fail=True))
    s, (res, cid) = _run(settings, registry, wb, pc=down, dw_client=None)
    assert res.state == State.POTENTIAL and res.score <= 50
    assert res.evidence["evidence_fusion"]["supported_by"] == []
    for k in ("sentinel1", "dynamic_world", "jrc_gsw"):
        assert res.evidence["evidence_sources"][k] != "used" and "unavailable" in res.evidence["evidence_sources"][k]
    assert any("Single-source" in n for n in res.evidence["support_assessment"]["notes"])


def test_registry_workflow_contradiction_from_dynamic_world(settings, reg):
    registry, wb = reg
    s, (res, cid) = _run(settings, registry, wb, pc=PlanetaryComputer("u", "s", transport=make_transport(fail=True)), dw_client=FakeDW(wb, "water_persists"))
    assert res.evidence["evidence_fusion"]["contradicted_by"] == ["dynamic_world"] and res.state != State.HIGH
