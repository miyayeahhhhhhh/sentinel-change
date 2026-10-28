import json
from pathlib import Path
import numpy as np
import pytest
import rasterio

from cs.config import FloodParams, EncroachParams
from cs.db import CaseStore
from cs.flood import run_flood
from cs.encroach import run_encroachment
from cs.geo import mask_to_gdf, write_geojson, check_geotiff
from cs.models import State
from cs.sample_data import STUDY_AREAS, flood_scenes, make_grid, s2_scenes
from cs.workflow import execute_flood, execute_encroachment


def test_case_id_monotonicity_after_deletion(settings):
    store = CaseStore(settings.db_path)
    id1 = store.next_id("flood")
    assert id1 == "FLD-0001"

    # Create two cases
    r1, _ = execute_flood(settings, "flood_event")
    r2, cid2 = execute_flood(settings, "no_flood")
    assert cid2 == "FLD-0002"

    # Simulate deleting the first case
    with store._c() as con:
        con.execute("DELETE FROM cases WHERE case_id='FLD-0001'")

    # next_id should still be FLD-0003, NOT FLD-0002 (which would cause collision)
    id3 = store.next_id("flood")
    assert id3 == "FLD-0003"


def test_reviewer_standalone_notes_and_audit(settings):
    _, cid = execute_encroachment(settings, "persistent_loss")
    store = CaseStore(settings.db_path)

    assert len(store.notes(cid)) == 0
    store.add_note(cid, "analyst_bob", "Reviewing high-water line boundary near eastern jetty.")

    notes = store.notes(cid)
    assert len(notes) == 1
    assert notes[0]["reviewer"] == "analyst_bob"
    assert "eastern jetty" in notes[0]["note"]

    audit = store.audit(cid)
    note_actions = [a for a in audit if a["action"] == "note_added"]
    assert len(note_actions) == 1
    assert note_actions[0]["actor"] == "analyst_bob"


def test_study_areas_registry_and_grids():
    for key, info in STUDY_AREAS.items():
        grid = make_grid(key)
        assert grid.crs.to_epsg() == info["crs_epsg"]
        assert grid.crs.is_projected
        assert grid.pixel_area_m2() == pytest.approx(100.0)
        assert len(grid.bounds) == 4
        w, s, e, n = grid.bounds_wgs84()
        assert -180 <= w <= 180 and -90 <= s <= 90


def test_cross_sensor_corroboration_flood_with_s2(settings):
    grid, s1_list = flood_scenes("flood_event")
    _, s2_list = s2_scenes("persistent_loss")

    # Run flood with S2 supplementary scenes
    res = run_flood(s1_list, grid, FloodParams(), supplementary_scenes=s2_list)
    assert res.state in (State.POTENTIAL, State.HIGH)
    sc = res.evidence.get("sensor_corroboration")
    assert sc is not None
    assert sc["status"] in ("corroborated", "inconclusive", "cloud_obscured")
    assert "optical" in sc["reason"].lower() or "sentinel-2" in sc["reason"].lower()


def test_cross_sensor_corroboration_encroachment_with_s1(settings):
    grid, s2_list = s2_scenes("persistent_loss")
    _, s1_list = flood_scenes("no_flood")

    # Run encroachment with S1 supplementary scenes
    res = run_encroachment(s2_list, grid, EncroachParams(), supplementary_scenes=s1_list)
    assert res.state in (State.POTENTIAL, State.HIGH)
    sc = res.evidence.get("sensor_corroboration")
    assert sc is not None
    assert sc["status"] in ("corroborated", "inconclusive", "partial")
    assert "sar" in sc["reason"].lower() or "sentinel-1" in sc["reason"].lower()


def test_absence_of_corroboration_is_not_disproof(settings):
    grid, s1_list = flood_scenes("flood_event")
    # Run with empty supplementary scenes
    res = run_flood(s1_list, grid, FloodParams(), supplementary_scenes=[])
    sc = res.evidence.get("sensor_corroboration")
    assert sc["status"] == "unassessed"
    assert "absence of optical corroboration does not disprove" in sc["reason"].lower()
    # Pipeline still successfully detects flood based on primary sensor!
    assert res.state in (State.POTENTIAL, State.HIGH)


def test_mask_to_gdf_produces_strictly_valid_geometries():
    grid = make_grid()
    # Complex non-convex mask with hole
    mask = np.zeros((grid.height, grid.width), bool)
    mask[20:80, 20:80] = True
    mask[40:60, 40:60] = False
    mask[150, 150] = True

    gdf = mask_to_gdf(mask, grid)
    assert not gdf.empty
    assert all(geom.is_valid for geom in gdf.geometry)
    assert gdf.crs == grid.crs


def test_all_export_files_generated_and_valid(settings):
    res, cid = execute_flood(settings, "flood_event")
    store = CaseStore(settings.db_path)
    case_row = store.get_case(cid)
    exports = json.loads(case_row["exports"])

    # 1. change_mask.tif
    mask_path = Path(exports["change_mask_tif"])
    assert mask_path.exists()
    assert check_geotiff(mask_path, res.grid)
    with rasterio.open(mask_path) as src:
        assert src.nodata == 255
        data = src.read(1)
        assert set(np.unique(data)).issubset({0, 1, 255})

    # 2. coverage.tif
    cov_path = Path(exports["coverage_tif"])
    assert cov_path.exists()
    assert check_geotiff(cov_path, res.grid)

    # 3. polygons.geojson
    poly_path = Path(exports["polygons_geojson"])
    assert poly_path.exists()
    gj = json.loads(poly_path.read_text(encoding="utf-8"))
    assert gj["type"] == "FeatureCollection"
    assert len(gj["features"]) > 0

    # 4. evidence.json
    ev_path = Path(exports["evidence_json"])
    assert ev_path.exists()
    ev_data = json.loads(ev_path.read_text(encoding="utf-8"))
    assert ev_data["case_id"] == cid
    assert ev_data["pipeline"] == "flood"
    assert "metrics" in ev_data["evidence"]
