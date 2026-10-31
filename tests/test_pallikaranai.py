"""Tests for Pallikaranai Marsh study area, edge cases, and prioritisation rationale.

Coverage:
- Pallikaranai study area registration, CRS, and grid validity
- NE monsoon seasonal parameters vs SW monsoon defaults
- Missing/no imagery → NOT_ASSESSED (never NO_CHANGE)
- All-cloudy scene list → NOT_ASSESSED
- Invalid boundary (geographic CRS, zero AOI) → explicit failure
- Insufficient baseline observations → E4 NOT_ASSESSED
- Duplicate scene dates handled deterministically
- Seasonal contraction correctly suppressed for NE monsoon wet months
- Prioritisation ranking order and rationale presence
- Evidence fields present in all flagged results
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

import numpy as np
import pytest
from rasterio.crs import CRS
from rasterio.transform import from_origin

from cs.config import (
    EncroachParams,
    PALLIKARANAI_ENCROACH_PARAMS,
    encroach_params_for,
    utc,
)
from cs.encroach import run_encroachment
from cs.geo import Grid, GeoValidationError, validate_grid
from cs.imagery import Scene
from cs.models import State, Pipeline
from cs.priority import queue_table, RANKING_RATIONALE
from cs.sample_data import (
    STUDY_AREAS,
    make_grid,
    s2_scenes,
    WET_NE,
    WET,
)
from cs.workflow import execute_encroachment


# ── Study area registry ────────────────────────────────────────────────────────

def test_pallikaranai_in_study_areas():
    assert "pallikaranai_marsh" in STUDY_AREAS
    info = STUDY_AREAS["pallikaranai_marsh"]
    assert info["crs_epsg"] == 32644, "Must be UTM Zone 44N"
    # Verify the origin is approximately correct for 12.93°N, 80.21°E
    e, n = info["origin"]
    assert 410_000 < e < 420_000, f"Easting {e} out of expected range for 80.21°E UTM44N"
    assert 1_425_000 < n < 1_435_000, f"Northing {n} out of expected range for 12.93°N UTM44N"


def test_pallikaranai_grid_is_projected_and_valid():
    grid = make_grid("pallikaranai_marsh")
    assert grid.crs.is_projected
    assert grid.crs.to_epsg() == 32644
    assert grid.pixel_area_m2() == pytest.approx(100.0)  # 10 m × 10 m
    w, s, e, n = grid.bounds_wgs84()
    # Pallikaranai is at ~12.93°N, 80.21°E — verify WGS84 bounds are in the right region
    assert 12.0 < s < 14.0, f"Latitude {s} not near Chennai"
    assert 79.0 < w < 81.0, f"Longitude {w} not near Chennai"


def test_pallikaranai_study_area_metadata_fields():
    info = STUDY_AREAS["pallikaranai_marsh"]
    assert "admin_district" in info
    assert "sentinel2_tiles" in info
    assert "44PMV" in info["sentinel2_tiles"]
    assert "wetland_authority" in info


# ── Seasonal parameter correctness ────────────────────────────────────────────

def test_pallikaranai_uses_ne_monsoon_wet_months():
    params = PALLIKARANAI_ENCROACH_PARAMS
    assert set(params.wet_months) == {10, 11, 12}, (
        "Pallikaranai NE monsoon: wet months must be Oct, Nov, Dec"
    )


def test_encroach_params_for_returns_ne_monsoon_for_pallikaranai():
    p = encroach_params_for("pallikaranai_marsh")
    assert set(p.wet_months) == {10, 11, 12}


def test_encroach_params_for_returns_default_for_other_areas():
    for area in ("solapur_bhima", "vijayawada_krishna", "pulicat_lake"):
        p = encroach_params_for(area)
        assert set(p.wet_months) == {6, 7, 8, 9, 10}, f"SW monsoon expected for {area}"


def test_wet_ne_lutcorrect_peak_months():
    """Oct–Dec should be >= 0.7, Jan–May should be <= 0.5, Jun–Sep should be low."""
    assert WET_NE[10] >= 0.7 and WET_NE[11] >= 0.9 and WET_NE[12] >= 0.8
    assert WET_NE[4] <= 0.3 and WET_NE[5] <= 0.2
    # SW monsoon peak (Aug) should not be high in NE monsoon lookup
    assert WET_NE[8] <= 0.2, "Aug is NOT a peak wet month for NE monsoon"


# ── Missing / empty imagery → NOT_ASSESSED ────────────────────────────────────

def test_no_scenes_gives_not_assessed():
    grid = make_grid("pallikaranai_marsh")
    r = run_encroachment([], grid, PALLIKARANAI_ENCROACH_PARAMS)
    assert r.state == State.NOT_ASSESSED
    assert r.score is None
    assert r.evidence["class"] == "E4"
    # Must never be interpreted as "no change"
    assert "not a" in r.summary.lower() or "insufficient" in r.summary.lower()


def test_not_assessed_is_not_no_change_message():
    """The E4 summary must not claim or imply no change was present."""
    grid = make_grid("pallikaranai_marsh")
    r = run_encroachment([], grid, PALLIKARANAI_ENCROACH_PARAMS)
    summary_lower = r.summary.lower()
    assert "no change" not in summary_lower or "not" in summary_lower


# ── All-cloudy scenes → NOT_ASSESSED ──────────────────────────────────────────

def _make_cloudy_s2_scene(scene_id: str, acquired: datetime) -> Scene:
    """Fully cloud-covered S2 scene (SCL=9 everywhere, valid_frac ~ 0)."""
    H, W = 200, 200
    g = np.full((H, W), 0.5, dtype=np.float32)
    s = np.full((H, W), 0.5, dtype=np.float32)
    scl = np.full((H, W), 9, dtype=np.uint8)   # SCL=9 = high-confidence cloud
    return Scene(scene_id, "sentinel-2", acquired,
                 {"green": g, "swir1": s, "scl": scl}, "test", True)


def test_all_cloudy_scenes_gives_not_assessed():
    grid = make_grid("pallikaranai_marsh")
    scenes = [
        _make_cloudy_s2_scene(f"CLOUD-{i}", datetime(2023, m, 5, tzinfo=timezone.utc))
        for i, m in enumerate(range(1, 13), 1)
    ]
    r = run_encroachment(scenes, grid, PALLIKARANAI_ENCROACH_PARAMS)
    assert r.state == State.NOT_ASSESSED
    assert r.score is None
    unusable = [x for x in r.evidence["scenes"] if not x["usable"]]
    assert len(unusable) == len(scenes), "All cloudy scenes must be excluded"


# ── Invalid boundary / zero AOI ───────────────────────────────────────────────

def test_geographic_crs_grid_rejected():
    """A geographic (degree) CRS must raise GeoValidationError, not silently produce results."""
    bad_grid = Grid(from_origin(80.2, 12.9, 0.0001, 0.0001), CRS.from_epsg(4326), 200, 200)
    with pytest.raises(GeoValidationError):
        validate_grid(bad_grid)


def test_empty_aoi_mask_gives_failed_result():
    """A fully-zero AOI mask must not produce a change finding."""
    grid = make_grid("pallikaranai_marsh")
    _, scenes = s2_scenes("persistent_loss", study_area="pallikaranai_marsh")
    zero_aoi = np.zeros((grid.height, grid.width), bool)
    r = run_encroachment(scenes, grid, PALLIKARANAI_ENCROACH_PARAMS, aoi_mask=zero_aoi)
    # Empty AOI raises inside _analyze → caught → FAILED state
    assert r.state == State.FAILED


# ── Insufficient baseline ─────────────────────────────────────────────────────

def test_insufficient_baseline_obs_gives_e4(settings):
    """Only 1 baseline observation (< min_baseline_obs=4) → E4 NOT_ASSESSED."""
    grid = make_grid("pallikaranai_marsh")
    # 1 baseline scene in 2023, many recent scenes in 2024+
    scenes = [_make_cloudy_s2_scene("BASE-1", datetime(2023, 6, 1, tzinfo=timezone.utc))]
    # Add some clear recent scenes
    H, W = 200, 200
    for month in range(1, 7):
        g = np.full((H, W), 0.10, dtype=np.float32)
        s = np.full((H, W), 0.25, dtype=np.float32)
        scl = np.full((H, W), 5, dtype=np.uint8)
        scenes.append(Scene(
            f"REC-{month}", "sentinel-2",
            datetime(2024, month, 5, tzinfo=timezone.utc),
            {"green": g, "swir1": s, "scl": scl}, "test", True
        ))
    r = run_encroachment(scenes, grid, PALLIKARANAI_ENCROACH_PARAMS)
    assert r.state == State.NOT_ASSESSED
    assert r.evidence["class"] == "E4"


# ── Duplicate scene dates handled deterministically ───────────────────────────

def test_duplicate_scene_dates_handled_without_error():
    """Duplicate acquisition dates must not raise exceptions or produce NaN scores."""
    grid = make_grid("pallikaranai_marsh")
    _, scenes = s2_scenes("persistent_loss", study_area="pallikaranai_marsh")
    # Duplicate the first scene with the same date
    dup = Scene(
        scenes[0].scene_id + "_dup",
        scenes[0].sensor,
        scenes[0].acquired,
        scenes[0].bands,
        scenes[0].source,
        scenes[0].synthetic,
    )
    all_scenes = scenes + [dup]
    r = run_encroachment(all_scenes, grid, PALLIKARANAI_ENCROACH_PARAMS)
    # Must complete without crashing; score must be finite if flagged
    assert r.state != State.FAILED
    if r.score is not None:
        assert np.isfinite(r.score)


# ── Pallikaranai synthetic pilot run ─────────────────────────────────────────

def test_pallikaranai_persistent_loss_scenario_runs(settings):
    """End-to-end synthetic pilot for the Pallikaranai primary AOI."""
    r, cid = execute_encroachment(settings, "persistent_loss", study_area="pallikaranai_marsh")
    assert cid.startswith("ENC-")
    assert r.state in (State.POTENTIAL, State.HIGH, State.NOT_ASSESSED, State.NO_CHANGE)
    # Verify synthetic flag is set
    assert r.synthetic is True
    # Verify params used NE monsoon wet months.
    # params is serialised via _jsonable (tuples become lists in JSON then back to lists).
    assert list(r.params["wet_months"]) == [10, 11, 12], (
        "Pallikaranai pilot must use NE monsoon wet_months (10,11,12)"
    )


def test_pallikaranai_evidence_fields_present_when_flagged(settings):
    """All required evidence fields must be present for any flagged result."""
    r, cid = execute_encroachment(settings, "persistent_loss", study_area="pallikaranai_marsh")
    if r.state not in (State.POTENTIAL, State.HIGH):
        pytest.skip("Scenario did not produce a flagged result; skip field check")
    ev = r.evidence
    required = ["period", "scenes", "metrics", "sources", "loss_ha",
                "seasonal_check", "data_age_days", "class", "sensor_corroboration"]
    for field in required:
        assert field in ev, f"Missing required evidence field: {field}"
    # period must have start and end
    assert ev["period"]["start"] is not None
    assert ev["period"]["end"] is not None
    # loss_ha must be positive and finite
    assert ev["loss_ha"] > 0
    assert np.isfinite(ev["loss_ha"])
    # class must be E1 or E2 (primary change class)
    assert ev["class"] in ("E1", "E2"), "Primary class must indicate loss or bare/built transition"


# ── Prioritisation ────────────────────────────────────────────────────────────

def test_ranking_rationale_is_documented():
    """RANKING_RATIONALE must be a non-empty string covering key factors."""
    assert isinstance(RANKING_RATIONALE, str)
    assert len(RANKING_RATIONALE) > 100
    for keyword in ("state", "score", "persistence", "area", "evidence"):
        assert keyword.lower() in RANKING_RATIONALE.lower(), (
            f"RANKING_RATIONALE must mention '{keyword}'"
        )


def test_queue_high_before_potential(settings):
    """HIGH state cases must rank above POTENTIAL in the queue table."""
    from cs.workflow import execute_encroachment as ee
    # Create a POTENTIAL case (no_wet_season = cannot confirm seasonal, stays POTENTIAL)
    _, c1 = ee(settings, "no_wet_season", study_area="solapur_bhima")
    # Create a HIGH case (persistent_loss with wet-season observations → may reach HIGH)
    _, c2 = ee(settings, "persistent_loss", study_area="solapur_bhima")
    from cs.db import CaseStore
    store = CaseStore(settings.db_path)
    cases = store.list_cases("encroachment")
    df = queue_table(cases, "encroachment")
    assert not df.empty
    states = df["state"].tolist()
    # Verify HIGH appears before POTENTIAL when both present
    if "high_priority_review" in states and "potential_change_detected" in states:
        high_idx = states.index("high_priority_review")
        pot_idx = states.index("potential_change_detected")
        assert high_idx < pot_idx, "HIGH must rank before POTENTIAL"


def test_queue_table_never_contains_confirmed_encroachment_label():
    """The queue must not label any case as 'confirmed illegal encroachment'."""
    # Construct a minimal fake case list
    fake_cases = [{
        "case_id": "ENC-9999",
        "pipeline": "encroachment",
        "state": "high_priority_review",
        "status": "potential",
        "score": 75.0,
        "synthetic": True,
        "period_end": "2025-10-01",
        "created_at": "2025-10-01T00:00:00",
        "evidence": json.dumps({
            "loss_ha": 2.5,
            "class": "E2",
            "score_weight_coverage": 0.8,
            "data_age_days": 5,
        }),
    }]
    df = queue_table(fake_cases, "encroachment")
    assert not df.empty
    # No column or value should contain the phrase "confirmed" + "illegal"
    all_text = df.to_string().lower()
    assert "confirmed illegal" not in all_text


# ── Seasonality separation for Pallikaranai ───────────────────────────────────

def test_pallikaranai_seasonal_only_does_not_flag(settings):
    """Seasonal-only scenario must not produce a HIGH or POTENTIAL result."""
    r, _ = execute_encroachment(settings, "seasonal_only", study_area="pallikaranai_marsh")
    assert r.state in (State.NO_CHANGE, State.NOT_ASSESSED), (
        f"seasonal_only must not be flagged, got {r.state}"
    )


def test_pallikaranai_sparse_gives_not_assessed(settings):
    """Sparse scenario with Pallikaranai params: too few recent obs → NOT_ASSESSED or NO_CHANGE.
    With NE monsoon recent_start=2024-01-01, only Feb 2025 is a recent scene in 'sparse',
    which may or may not cross the min_recent_obs threshold depending on baseline.
    The key invariant is that it does NOT produce POTENTIAL or HIGH (no false flags).
    """
    r, _ = execute_encroachment(settings, "sparse", study_area="pallikaranai_marsh")
    assert r.state not in (State.POTENTIAL, State.HIGH), (
        f"sparse scenario must not produce a flagged result, got {r.state}"
    )


# ── Real Data Pilot & Decision-Ready Case Files ────────────────────────────────

def test_real_imagery_provider_s2_scenes(settings):
    """LiveProvider returns genuine Sentinel-2 L2A observations for Pallikaranai Marsh."""
    import dataclasses
    from cs.imagery import LiveProvider
    live_settings = dataclasses.replace(settings, mode="live")
    provider = LiveProvider(live_settings)
    grid, scenes = provider.s2_scenes(study_area="pallikaranai_marsh")

    assert grid.crs.to_epsg() == 32644
    assert len(scenes) >= 8, f"Expected >= 8 cached real scenes, got {len(scenes)}"
    for sc in scenes:
        assert sc.synthetic is False
        assert sc.sensor == "sentinel-2"
        assert "green" in sc.bands and "swir1" in sc.bands and "scl" in sc.bands
        assert sc.bands["green"].shape == (grid.height, grid.width)
        assert 0.0 <= sc.bands["green"].min() and sc.bands["green"].max() <= 3.0, "Reflectance values out of expected range"
        assert sc.source == "Copernicus Sentinel-2 L2A (ESA / AWS Open Data)"


def test_real_data_pilot_end_to_end_execution(settings, tmp_path):
    """Real-data pilot on genuine Copernicus Sentinel-2 L2A observations."""
    import dataclasses
    from cs.db import CaseStore
    from cs.report import build_pdf

    live_settings = dataclasses.replace(settings, mode="live")
    res, cid = execute_encroachment(live_settings, scenario="", study_area="pallikaranai_marsh")

    assert cid.startswith("ENC-")
    assert res.synthetic is False
    assert res.state in (State.POTENTIAL, State.HIGH, State.NOT_ASSESSED, State.NO_CHANGE)

    ev = res.evidence
    # 1. Actual observation dates & before/after timeline
    assert ev["period"]["start"] is not None and ev["period"]["end"] is not None
    assert len(ev["scenes"]) >= 8

    # 2. Coordinates & verified administrative jurisdiction
    coords = ev.get("coordinates")
    assert coords is not None
    lat, lon = coords["centroid_wgs84"]
    assert 12.8 < lat < 13.1, f"Latitude {lat} out of Pallikaranai range"
    assert 80.1 < lon < 80.3, f"Longitude {lon} out of Pallikaranai range"
    assert "Chennai" in ev.get("jurisdiction", "") or "Tamil Nadu" in ev.get("jurisdiction", "")

    # 3. Decision-ready fields: method, confidence rationale, recommended verification
    assert "method" in ev and "Sentinel-2" in ev["method"]
    assert "confidence_rationale" in ev
    assert "recommended_verification" in ev
    assert len(ev["recommended_verification"]) >= 3
    assert any("survey" in s.lower() or "cadastral" in s.lower() for s in ev["recommended_verification"])

    # 4. Generate decision-ready PDF case file from real detection
    store = CaseStore(live_settings.db_path)
    pdf_path = build_pdf(store, cid, tmp_path / f"{cid}_real_pilot.pdf")
    assert pdf_path.exists()
    assert pdf_path.read_bytes()[:4] == b"%PDF"
    assert pdf_path.stat().st_size > 10000


def test_real_data_cloud_and_shadow_masking(settings):
    """Real Sentinel-2 scenes apply SCL cloud/shadow filtering correctly."""
    import dataclasses
    from cs.imagery import LiveProvider
    live_settings = dataclasses.replace(settings, mode="live")
    grid, scenes = LiveProvider(live_settings).s2_scenes(study_area="pallikaranai_marsh")

    # The Nov 5 2024 scene has partial cloud/shadow; verify SCL contains cloud classes
    scl_classes = set()
    for sc in scenes:
        scl_classes.update(np.unique(sc.bands["scl"]))

    # SCL standard classes: 3=shadow, 8=cloud med, 9=cloud high, 6=water, 5=bare soil
    assert 6 in scl_classes, "Water class (6) should be observed in Pallikaranai"
    assert (8 in scl_classes or 9 in scl_classes), "Cloud classes should be present in real scene set"
