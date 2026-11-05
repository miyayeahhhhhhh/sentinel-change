"""Stabilisation: unavailable sources never fabricate evidence; registry run end-to-end (clipping, exports, PDF, audit)."""
import dataclasses
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import box

from cs import registry as R
from cs.config import encroach_params_for, utc
from cs.db import CaseStore
from cs.encroach import run_encroachment
from cs.evidence import NotConfigured
from cs.geo import polygon_mask
from cs.imagery import LiveProvider, Scene
from cs.models import State
from cs.report import build_pdf
from cs.sample_data import make_grid, s2_scenes
from cs.workflow import execute_encroachment_for_waterbody
from tests.synth import synthetic_s1

AS_OF = datetime(2026, 10, 31, tzinfo=timezone.utc)


def test_missing_or_misgridded_sar_changes_nothing():
    g, sc = s2_scenes("persistent_loss")
    base = run_encroachment(sc, g)
    none = run_encroachment(sc, g, supplementary_scenes=[])
    wrong = synthetic_s1(g)
    for s in wrong:                                           # SAR scenes on a different grid must be ignored, not crash
        s.bands["vv_db"] = s.bands["vv_db"][:50, :50]
    mis = run_encroachment(sc, g, supplementary_scenes=wrong)
    for r in (none, mis):
        assert r.state == base.state and r.score == base.score
        assert r.evidence["sensor_corroboration"]["status"] == "unassessed"
        assert r.evidence["evidence_sources"]["sentinel1"] == "not_requested"
    assert "ignored" in mis.evidence["sensor_corroboration"]["reason"]


def test_unrequested_sources_are_declared_and_carry_no_weight():
    g, sc = s2_scenes("persistent_loss")
    r = run_encroachment(sc, g)
    es = r.evidence["evidence_sources"]
    assert es["dynamic_world"] == es["jrc_gsw"] == es["sentinel1"] == "not_requested"
    assert sum("did not contribute" in x for x in r.limitations) == 3             # each missing source is declared, none is a negative
    assert {c["name"] for c in r.evidence["score_components"]} == {"persistence", "footprint", "spatial_coherence", "magnitude",
                                                                   "sensor_agreement", "image_quality", "recency", "corroboration",
                                                                   "landcover_transition", "historical_water"}
    unavailable = {c["name"]: c["available"] for c in r.evidence["score_components"]}
    assert unavailable["corroboration"] is False and unavailable["sensor_agreement"] is False   # no SAR -> unavailable weight is not earned


def test_no_imagery_is_not_assessed_never_no_change():
    g = make_grid()
    r = run_encroachment([], g)
    assert r.state == State.NOT_ASSESSED and r.score is None


class _FixtureProvider(LiveProvider):
    """TEST ONLY: synthetic scenes generated on the AOI grid, standing in for acquisition."""
    def __init__(self, settings, wb_geom, fail=False):
        super().__init__(settings); self.wb_geom, self.fail = wb_geom, fail

    def s2_scenes_for_grid(self, grid, cache_key, start, end, **kw):
        if self.fail:
            raise NotConfigured("no imagery (test)")
        water = polygon_mask(self.wb_geom, 4326, grid)
        east = water.copy(); east[:, : np.nonzero(water.any(0))[0].mean().astype(int)] = False    # eastern half of the waterbody
        scenes, y, m = [], start.year, start.month
        while datetime(y, m, 5, tzinfo=timezone.utc) < end:
            t = datetime(y, m, 5, 5, 0, tzinfo=timezone.utc)
            w = water & ~east if t >= encroach_params_for(profile="sw_monsoon", as_of=AS_OF, recent_months=12).recent_start else water
            scenes.append(Scene(f"TESTFIX-{y}{m:02d}", "sentinel-2", t, {"green": np.where(w, .08, .10).astype("float32"),
                             "swir1": np.where(w, .02, .25).astype("float32"), "scl": np.where(w, 6, 5).astype("uint8")},
                             "SYNTHETIC TEST FIXTURE", True, {}))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return scenes


@pytest.fixture
def reg(tmp_path):
    d = tmp_path / "reg"; d.mkdir(); from tests.conftest import write_empty_manifest; write_empty_manifest(d)
    def mk(name, geom):
        p = tmp_path / name; gpd.GeoDataFrame({"n": ["x"]}, geometry=[geom], crs=4326).to_file(p, driver="GeoJSON"); return p     # TEST geometry
    src = ["--publisher", "TEST", "--dataset", "TEST", "--url", "https://t.example.gov.in/x", "--licence", "TEST", "--retrieved", "2026-10-01"]
    wb = box(78.400, 17.400, 78.410, 17.410)
    R.main(["--registry", str(d), "add-district", "--file", str(mk("d.geojson", box(78.3, 17.3, 78.5, 17.5))), "--name", "H", *src])
    R.main(["--registry", str(d), "add-waterbody", "--file", str(mk("w.geojson", wb)), "--id", "lake", "--name", "Test lake", *src])
    return R.Registry.load(d), wb


def test_registry_run_end_to_end_clipping_exports_pdf_audit(settings, reg):
    registry, wb = reg
    assert registry.waterbody("lake").verified
    settings = dataclasses.replace(settings, mode="live")
    res, cid = execute_encroachment_for_waterbody(settings, "lake", registry=registry, as_of=AS_OF, provider=_FixtureProvider(settings, wb))
    assert res.state in (State.POTENTIAL, State.HIGH)
    aoi = res.arrays["aoi"]
    assert 0.2 < aoi.mean() < 0.95                                     # polygon AOI, not the full grid rectangle
    assert not (res.arrays["loss"] & ~aoi).any() and not (res.arrays["observed"] & ~aoi).any()
    water = polygon_mask(wb, 4326, res.grid)
    assert res.arrays["loss"].sum() / water.sum() == pytest.approx(0.5, abs=0.12)      # eastern half of the lake detected
    assert not (res.arrays["loss"] & ~water).any()
    assert res.evidence["aoi"]["status"] == "verified" and res.evidence["aoi"]["district"]["status"] == "verified"
    store = CaseStore(settings.db_path); row = store.get_case(cid)
    assert json.loads(row["geometry"])["type"] in ("Polygon", "MultiPolygon")                # stored AOI is the polygon
    ex = json.loads(row["exports"]); assert Path(ex["aoi_geojson"]).exists() and Path(ex["change_mask_tif"]).exists()
    pdf = build_pdf(store, cid, settings.output_dir / cid / "c.pdf")
    assert pdf.read_bytes()[:4] == b"%PDF"
    assert [a["action"] for a in store.audit(cid)] == ["case_created", "pdf_generated"]


def test_failed_acquisition_creates_no_case(settings, reg):
    registry, wb = reg
    settings = dataclasses.replace(settings, mode="live")
    with pytest.raises(NotConfigured):
        execute_encroachment_for_waterbody(settings, "lake", registry=registry, as_of=AS_OF, provider=_FixtureProvider(settings, wb, fail=True))
    assert CaseStore(settings.db_path).list_cases() == []


def test_shipped_registry_status_is_computed_not_assumed():
    r = R.Registry.load()
    assert r.pilot["district_name"] == "Hyderabad"
    if r.district is None or not r.district.verified:
        assert r.blockers()                    # a missing/unverified district must keep the pilot blocked
    else:
        assert not any("district" in b.lower() and "boundary" in b.lower() for b in r.blockers())
