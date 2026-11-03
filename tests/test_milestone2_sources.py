"""Milestone 2: source adapters (stand-in transports/readers), preprocessing, alignment, temporal compatibility, fusion.
NONE of these tests touches a real remote service."""
import dataclasses
from datetime import timedelta

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from cs.evidence import EvidenceLayer, EvidenceRequest, NotConfigured, Unavailable, evidence_sources
from cs.evidence import sentinel1 as S1
from cs.evidence.dynamic_world import DWComposite, DwParams, DynamicWorldSource, EarthEngineDWClient, classify_transitions
from cs.evidence.jrc_gsw import JRCSurfaceWaterSource, JrcParams, classify_history
from cs.evidence.stac import PlanetaryComputer, RemoteError
from cs.fusion import attributed_polygons, fuse
from cs.geo import Grid, grid_for_geometry, polygon_mask, read_to_grid
from tests.m2_fixtures import (AS_OF, RECENT_START, START, FakeDW, fake_pc, jrc_item, lake_masks, make_jrc_reader,
                               make_s1_reader, make_transport, s1_items)

LAKE = box(78.405, 17.405, 78.415, 17.415)


@pytest.fixture(scope="module")
def grid():
    return grid_for_geometry(box(78.40, 17.40, 78.42, 17.42), 4326, 10.0, pad_m=50)


@pytest.fixture(scope="module")
def req(grid):
    return EvidenceRequest(grid, polygon_mask(box(78.401, 17.401, 78.419, 17.419), 4326, grid), START, AS_OF, "t-aoi", RECENT_START)


# ------------------------------------------------------------------ STAC / PC client
def test_pc_pagination_token_cache_and_signing():
    pages = [{"features": [{"id": "a"}], "links": [{"rel": "next", "method": "POST", "href": "https://stac.test/v1/search?p=2", "body": {"token": "n"}}]},
             {"features": [{"id": "b"}], "links": []}]
    calls = []

    def tr(url, body=None, headers=None, timeout=30):
        calls.append((url, body))
        return {"token": "sig=1", "msft:expiry": "2099-01-01T00:00:00Z"} if "/sas/" in url else pages[len([c for c in calls if "/sas/" not in c[0]]) - 1]
    pc = PlanetaryComputer("https://stac.test/v1", "https://stac.test/sas", "KEY", transport=tr)
    assert [i["id"] for i in pc.search("c", (1, 2, 3, 4), START, AS_OF)] == ["a", "b"]
    assert calls[1][1]["token"] == "n" and "datetime" in calls[1][1]
    assert pc.sign("https://x/y.tif", "c") == "https://x/y.tif?sig=1" and pc.sign("https://x/y.tif?a=1", "c").endswith("&sig=1")
    pc.sign("https://x/z.tif", "c")
    assert sum("/sas/" in c[0] for c in calls) == 1                         # token cached


def test_remote_failure_raises_remote_error():
    pc = fake_pc(fail=True)
    with pytest.raises(RemoteError):
        pc.search("sentinel-1-rtc", (1, 2, 3, 4))


# ------------------------------------------------------------------ Sentinel-1
def test_item_compatibility_rules():
    good = s1_items()[0]
    assert S1.check_s1_item(good)[0]
    for mut, why in (({"sar:polarizations": ["HH"]}, "VV"), ({"sar:instrument_mode": "EW"}, "IW"), ({"sat:relative_orbit": None}, "orbit")):
        it = {**good, "properties": {**good["properties"], **mut}}
        ok, reason, _ = S1.check_s1_item(it)
        assert not ok and why in reason


def test_preprocessing_speckle_db_and_invalid_pixels():
    lin = np.full((20, 20), 0.01, "float32"); lin[5, 5] = 0.0; lin[6, 6] = np.nan; lin[7, 7] = -1
    db = S1.to_db(lin, 3)
    assert db[10, 10] == pytest.approx(-20.0, abs=0.01)
    assert np.isnan(db[5, 5]) and np.isnan(db[6, 6]) and np.isnan(db[7, 7])          # invalid stays invalid, never filled
    assert np.isfinite(db[5, 6])                                                      # neighbours still use valid pixels
    assert np.isnan(S1.qc_db(np.array([-50.0, 9.0]))).all()


def test_acquire_selects_one_orbit_group_and_reports_exclusions(grid, req, tmp_path):
    items = s1_items((("ascending", 125, 1.0), ("descending", 33, 0.5)))
    items.append({**items[0], "id": "bad", "properties": {**items[0]["properties"], "sar:polarizations": ["HH"]}})
    pc = fake_pc(s1=items)
    rd = make_s1_reader(LAKE)
    scenes, rep = S1.acquire_scenes(pc, "sentinel-1-rtc", req, S1.SarParams(), tmp_path, rd)
    assert rep["orbit_group"] == {"orbit_state": "ascending", "relative_orbit": 125}
    assert {s.meta["relative_orbit"] for s in scenes} == {125}
    assert rep["excluded"]["no VV polarisation"] == 1 and "ascending_125" in rep["orbit_group_counts_base_recent"]
    n = len(rd.calls)
    S1.acquire_scenes(pc, "sentinel-1-rtc", req, S1.SarParams(), tmp_path, rd)         # second call served from the grid-stamped cache
    assert len(rd.calls) == n


def test_acquire_insufficient_and_failed_reads_are_explicit(req, tmp_path):
    few = [i for i in s1_items() if parse(i) >= RECENT_START][:3] + [i for i in s1_items() if parse(i) < RECENT_START][:10]
    with pytest.raises(NotConfigured, match="insufficient compatible"):
        S1.acquire_scenes(fake_pc(s1=few), "sentinel-1-rtc", req, S1.SarParams(), tmp_path / "a", make_s1_reader(LAKE))
    items = s1_items()
    fail = {items[0]["id"], items[1]["id"]}
    scenes, rep = S1.acquire_scenes(fake_pc(s1=items), "sentinel-1-rtc", req, S1.SarParams(), tmp_path / "b", make_s1_reader(LAKE, fail_ids=fail))
    assert rep["n_failed"] == 2 and len(scenes) == len(items) - 2
    with pytest.raises(NotConfigured, match="failed to read"):
        S1.acquire_scenes(fake_pc(s1=items), "sentinel-1-rtc", req, S1.SarParams(), tmp_path / "c", make_s1_reader(LAKE, fail_ids={i["id"] for i in items}))


def parse(item):
    from cs.evidence.stac import parse_dt
    return parse_dt(item["properties"]["datetime"])


def test_sar_analysis_detects_lake_loss_with_provenance(grid, req, tmp_path):
    src = S1.Sentinel1Source(pc=fake_pc(s1=s1_items()), cache_dir=tmp_path, reader=make_s1_reader(LAKE))
    layer = src.fetch(req)
    assert isinstance(layer, EvidenceLayer) and layer.arrays["sar_water_loss"].shape == (grid.height, grid.width)
    water, east = lake_masks(LAKE, grid)
    enc = layer.arrays["sar_water_loss"]
    inside = req.aoi_mask
    assert (enc[inside & east & (enc != 255)] == 1).mean() > 0.9                       # eastern half lost
    assert (enc[inside & water & ~east & (enc != 255)] == 1).mean() < 0.1               # western half still water
    assert (enc[~inside] == 255).all()                                                 # outside AOI unobserved, not "0"
    pv = layer.provenance
    assert pv["orbit"] == {"orbit_state": "ascending", "relative_orbit": 125} and pv["n_baseline"] >= 6 and pv["n_recent"] >= 4
    assert pv["acquisition"]["endpoint"] == "https://stac.test/v1" and pv["scene_ids"][0].startswith("S1-")
    assert pv["params"]["water_db"] == -18.0 and any("boxcar" in x for x in pv["processing"])


def test_sar_insufficient_cases_are_unavailable_not_negative(grid, req):
    mk = lambda items: [S1.Scene(i["id"], "sentinel-1", parse(i), {"vv_lin": np.full((grid.height, grid.width), .05, "float32")}, "x", False,
                                 {"orbit_state": i["properties"]["sat:orbit_state"], "relative_orbit": i["properties"]["sat:relative_orbit"]})
                        for i in items]
    items = s1_items()
    only_base = mk([i for i in items if parse(i) < RECENT_START])
    r = S1.analyze_sar(only_base, req, S1.SarParams())
    assert isinstance(r, Unavailable) and r.status == "insufficient" and "recent" in r.reason
    mixed = mk(items[:20]); mixed[0].meta["relative_orbit"] = 7
    assert "different orbit groups" in S1.analyze_sar(mixed, req, S1.SarParams()).reason
    no_recent_start = dataclasses.replace(req, recent_start=None)
    assert isinstance(S1.analyze_sar(mk(items), no_recent_start, S1.SarParams()), Unavailable)


def test_sar_source_failure_falls_back_to_labelled_cache_or_unavailable(grid, req, tmp_path):
    down = S1.Sentinel1Source(pc=PlanetaryComputer("u", "s", transport=make_transport(fail=True)), cache_dir=tmp_path / "empty")
    r = down.fetch(req)
    assert isinstance(r, Unavailable) and "no cached scenes" in r.reason and "simulated outage" in r.reason
    # legacy dB cache, no orbit metadata -> usable only as CACHE ONLY, labelled
    d = tmp_path / "legacy"; d.mkdir(); water, east = lake_masks(LAKE, grid)
    for i, it in enumerate(s1_items()):
        wet = water & ~east if parse(it) >= RECENT_START else water
        np.savez(d / f"{it['id']}.npz", vv_db=np.where(wet, -22.0, -10.0).astype("float32"), dt=it["properties"]["datetime"], sid=it["id"])
    layer = S1.Sentinel1Source(pc=PlanetaryComputer("u", "s", transport=make_transport(fail=True)), cache_dir=d).fetch(req)
    assert isinstance(layer, EvidenceLayer) and layer.provenance["mode"] == "cache_only_legacy" and "CACHE ONLY" in layer.provenance["label"]
    assert layer.provenance["fallback"] == "cache_only" and "live acquisition unavailable" in layer.provenance["live_status"]


def test_cached_scene_on_other_grid_is_never_used(grid, tmp_path):
    sc = S1.Scene("x", "sentinel-1", START, {"vv_lin": np.ones((grid.height, grid.width), "float32")}, "s", False, {})
    S1.save_scene(tmp_path, sc, grid)
    t = grid.transform
    shifted = Grid(type(t)(t.a, t.b, t.c + 500.0, t.d, t.e, t.f), grid.crs, grid.height, grid.width)
    assert len(S1.load_cached_scenes(tmp_path, grid)) == 1 and S1.load_cached_scenes(tmp_path, shifted) == []


def test_real_reader_aligns_cross_zone_cog_onto_grid(grid, tmp_path):
    """Uses the real read_to_grid with a local GeoTIFF in a DIFFERENT UTM zone standing in for a signed COG href."""
    import pyproj
    cx, cy = (78.41, 17.41)
    other = 32643 if grid.crs.to_epsg() == 32644 else 32644
    x, y = pyproj.Transformer.from_crs(4326, other, always_xy=True).transform(cx, cy)
    arr = np.zeros((300, 300), "float32"); arr[148:152, 148:152] = 1.0
    p = tmp_path / "vv.tif"
    with rasterio.open(p, "w", driver="GTiff", height=300, width=300, count=1, dtype="float32", crs=f"EPSG:{other}",
                       transform=from_origin(x - 1500, y + 1500, 10, 10)) as d:
        d.write(arr, 1)
    out = read_to_grid(p, grid, "nearest")
    r, c = np.argwhere(out == 1.0).mean(0)
    gx, gy = pyproj.Transformer.from_crs(4326, grid.crs, always_xy=True).transform(cx, cy)
    assert np.hypot(grid.transform.c + (c + .5) * 10 - gx, grid.transform.f - (r + .5) * 10 - gy) < 30


# ------------------------------------------------------------------ Dynamic World
def test_dw_probabilities_preserved_and_transitions_classified(grid, req):
    src = DynamicWorldSource(client=FakeDW(LAKE))
    layer = src.fetch(req)
    assert isinstance(layer, EvidenceLayer)
    tr, (water, east) = layer.arrays["dw_transition"], lake_masks(LAKE, grid)
    assert (tr[req.aoi_mask & east] == 1).mean() > 0.95                               # water -> built
    assert (tr[req.aoi_mask & water & ~east] == 0).all() and (tr[~req.aoi_mask] == 255).all()
    for k in ("dw_water_prob_base", "dw_water_prob_rec", "dw_built_prob_rec", "dw_bare_prob_rec"):
        assert layer.arrays[k].dtype == np.float32
    assert np.nanmax(layer.arrays["dw_water_prob_base"]) == pytest.approx(0.9)
    pv = layer.provenance
    assert pv["baseline"]["n_images"] == 6 and pv["windows"]["recent"][0] == RECENT_START.isoformat() and pv["stats"]["water_to_built_px"] > 0


def test_dw_bare_and_vegetation_destinations(grid, req):
    assert (DynamicWorldSource(client=FakeDW(LAKE, "bare")).fetch(req).arrays["dw_transition"] == 2).any()
    v = DynamicWorldSource(client=FakeDW(LAKE, "veg")).fetch(req).arrays["dw_transition"]
    assert (v == 3).any() and not (v == 1).any()                                       # vegetation is not counted as built/bare


def test_dw_missing_coverage_and_temporal_mismatch(grid, req):
    assert DynamicWorldSource(client=None).status == "blocked_credentials"
    r = DynamicWorldSource(client=None).fetch(req)
    assert isinstance(r, Unavailable) and "no Earth Engine client" in r.reason
    r = DynamicWorldSource(client=FakeDW(LAKE, n_images=1)).fetch(req)
    assert r.status == "insufficient" and "need >= 3" in r.reason
    r = DynamicWorldSource(client=FakeDW(LAKE, n_obs=1)).fetch(req)                    # too few images per pixel -> no pixel observed
    assert isinstance(r, Unavailable) and r.status == "insufficient"
    bad = dataclasses.replace(req, recent_start=req.end + timedelta(days=1))
    assert "not ordered" in DynamicWorldSource(client=FakeDW(LAKE)).fetch(bad).reason
    assert "recent_start missing" in DynamicWorldSource(client=FakeDW(LAKE)).fetch(dataclasses.replace(req, recent_start=None)).reason


def test_real_ee_client_reports_blocked_without_library_or_credentials():
    c = EarthEngineDWClient("")
    ok, why = c.available()
    assert not ok and ("earthengine-api" in why or "CS_GEE_PROJECT" in why)
    assert DynamicWorldSource(client=c).status == "blocked_credentials"


def test_dw_classification_thresholds_are_probability_based(grid):
    H, W = 10, 10
    z = lambda v: np.full((H, W), v, "float32")
    probs = lambda w, b: {k: z(0.0) for k in ("water", "trees", "grass", "flooded_vegetation", "crops", "shrub_and_scrub", "built", "bare", "snow_and_ice")} | {"water": z(w), "built": z(b)}
    base = DWComposite(probs(0.6, 0.0), np.full((H, W), 3, "int16"), 5, [], (None, None))
    rec = DWComposite(probs(0.25, 0.5), np.full((H, W), 3, "int16"), 5, [], (None, None))
    enc, st = classify_transitions(base, rec, np.ones((H, W), bool), DwParams())
    assert (enc == 1).all()
    rec2 = DWComposite(probs(0.35, 0.5), np.full((H, W), 3, "int16"), 5, [], (None, None))        # water prob still >= 0.3 -> no transition
    assert (classify_transitions(base, rec2, np.ones((H, W), bool), DwParams())[0] == 0).all()


# ------------------------------------------------------------------ JRC
def test_jrc_history_classes_coverage_and_temporal_role(grid, req):
    src = JRCSurfaceWaterSource(fake_pc(jrc_items=[jrc_item()]), reader=make_jrc_reader(LAKE))
    layer = src.fetch(req)
    water, _ = lake_masks(LAKE, grid)
    cls = layer.arrays["jrc_class"]
    assert (cls[req.aoi_mask & water] == 2).all() and (cls[req.aoi_mask & ~water] == 0).all() and (cls[~req.aoi_mask] == 255).all()
    pv = layer.provenance
    assert pv["coverage_end"].startswith("2021") and pv["gap_to_recent_window_days"] > 365 * 3          # not a recent observation
    assert pv["temporal_role"] == "historical reference; not a live observation" and pv["item_ids"] == ["jrc-gsw-TEST-tile"]
    seasonal = JRCSurfaceWaterSource(fake_pc(jrc_items=[jrc_item()]), reader=make_jrc_reader(LAKE, seasonal=True)).fetch(req)
    assert (seasonal.arrays["jrc_class"][req.aoi_mask & water] == 1).all()


def test_jrc_unavailable_paths(req):
    assert isinstance(JRCSurfaceWaterSource(None).fetch(req), Unavailable)
    assert "no JRC GSW items" in JRCSurfaceWaterSource(fake_pc(jrc_items=[])).fetch(req).reason
    assert "unavailable" in JRCSurfaceWaterSource(PlanetaryComputer("u", "s", transport=make_transport(fail=True))).fetch(req).reason
    r = JRCSurfaceWaterSource(fake_pc(jrc_items=[jrc_item()]), reader=make_jrc_reader(LAKE, cover=False)).fetch(req)
    assert r.status == "insufficient"                                                                  # outside tile coverage is NOT "never water"
    def boom(*a, **k): raise OSError("read")
    assert "read failed" in JRCSurfaceWaterSource(fake_pc(jrc_items=[jrc_item()]), reader=boom).fetch(req).reason


def test_classify_history_nodata_and_rules():
    occ = np.array([[0, 30, 90, 255, 80]], "uint8"); seas = np.array([[0, 4, 12, 255, 6]], "uint8")
    assert classify_history(occ, seas, JrcParams()).tolist() == [[0, 1, 2, 255, 1]]
    assert classify_history(occ, None, JrcParams()).tolist() == [[0, 1, 2, 255, 2]]


# ------------------------------------------------------------------ status honesty
def test_no_source_claims_operational_or_remote_tested():
    for s in evidence_sources().values():
        d = s.describe()
        assert d["status"] in ("implemented_untested_remote", "blocked_credentials", "cache_only", "not_integrated")
        assert d["tested_against_remote"] is False
    assert evidence_sources()["dynamic_world"].status == "blocked_credentials"


# ------------------------------------------------------------------ fusion
def _loss(grid, req):
    _, east = lake_masks(LAKE, grid)
    return east & req.aoi_mask


@pytest.fixture(scope="module")
def layers(grid, req, tmp_path_factory):
    d = tmp_path_factory.mktemp("c")
    return {"sentinel1": S1.Sentinel1Source(pc=fake_pc(s1=s1_items()), cache_dir=d, reader=make_s1_reader(LAKE)).fetch(req),
            "dynamic_world": DynamicWorldSource(client=FakeDW(LAKE)).fetch(req),
            "jrc_gsw": JRCSurfaceWaterSource(fake_pc(jrc_items=[jrc_item()]), reader=make_jrc_reader(LAKE)).fetch(req)}


def test_fusion_all_three_support_and_attribute_polygons(grid, req, layers):
    loss = _loss(grid, req)
    f = fuse(loss, layers)
    assert set(f["supported_by"]) == {"sentinel1", "dynamic_world", "jrc_gsw"} and f["contradicted_by"] == []
    assert f["components"]["sensor_agreement"] > 0.9 and f["components"]["landcover_transition"] > 0.9 and f["components"]["historical_water"] > 0.9
    gdf = attributed_polygons(loss, grid, f)
    assert {"sar_support", "dw_support", "jrc_permanent"} <= set(gdf.columns) and gdf.crs == grid.crs
    assert gdf["area_ha"].sum() == pytest.approx(loss.sum() * 0.01, rel=0.02)
    assert gdf["sar_support"].iloc[0] > 0.9


def test_fusion_unavailable_sources_add_nothing(grid, req):
    loss = _loss(grid, req)
    out = {"sentinel1": Unavailable("sentinel1", "down"), "dynamic_world": Unavailable("dynamic_world", "no creds"),
           "jrc_gsw": Unavailable("jrc_gsw", "x", "insufficient")}
    f = fuse(loss, out)
    assert f["components"] == {} and f["supported_by"] == [] and f["contradicted_by"] == []
    assert {v["status"] for v in f["sources"].values()} == {"unavailable", "insufficient"}
    assert fuse(loss, None)["sources"]["jrc_gsw"]["status"] == "not_requested"
    assert fuse(np.zeros_like(loss), out)["sources"]["sentinel1"]["status"] == "not_evaluated"


def test_fusion_contradiction_neutral_and_insufficient_overlap(grid, req, layers):
    loss = _loss(grid, req)
    persists = DynamicWorldSource(client=FakeDW(LAKE, "water_persists")).fetch(req)
    f = fuse(loss, {"dynamic_world": persists})
    assert f["contradicted_by"] == ["dynamic_world"]
    seasonal = JRCSurfaceWaterSource(fake_pc(jrc_items=[jrc_item()]), reader=make_jrc_reader(LAKE, seasonal=True)).fetch(req)
    assert fuse(loss, {"jrc_gsw": seasonal})["contradicted_by"] == ["jrc_gsw"]
    outside = np.zeros_like(loss); outside[0:5, 0:5] = True                                  # candidate where sources have no data
    f = fuse(outside, layers)
    assert all(v["status"] == "insufficient_overlap" for v in f["sources"].values()) and f["components"] == {}
