"""Live acquisition path exercised with LOCAL test COGs standing in for the STAC assets (no network)."""
import dataclasses
from datetime import datetime, timezone
import numpy as np
import pyproj
import pytest
import rasterio
from rasterio.transform import from_origin

from cs.imagery import LiveProvider, boa_offset_applies, s2_reflectance
from cs.geo import Grid, grid_for_geometry
from shapely.geometry import box


def test_boa_offset_rule_and_reflectance():
    assert boa_offset_applies(datetime(2023, 5, 1, tzinfo=timezone.utc)) and not boa_offset_applies(datetime(2021, 5, 1, tzinfo=timezone.utc))
    assert boa_offset_applies(datetime(2021, 5, 1, tzinfo=timezone.utc), "04.00") and not boa_offset_applies(datetime(2023, 1, 1, tzinfo=timezone.utc), "03.01")
    r = s2_reflectance(np.array([0, 1000, 3000], "uint16"), datetime(2024, 1, 1, tzinfo=timezone.utc))
    assert r.tolist() == [0.0, 0.0, pytest.approx(0.2)]


def test_stac_path_warps_across_utm_zones_and_caches(settings, tmp_path, monkeypatch):
    poly = box(77.97, 17.39, 77.99, 17.41)                    # straddles the zone 43/44 edge region -> grid in 32643
    grid = grid_for_geometry(poly, 4326, 10.0, pad_m=100)
    other = 32644 if grid.crs.to_epsg() == 32643 else 32643    # serve the "asset" in the OTHER zone
    t = pyproj.Transformer.from_crs(4326, other, always_xy=True)
    x, y = t.transform(77.98, 17.40)
    tr = from_origin(x - 2000, y + 2000, 10.0, 10.0)
    def make(path, val, dtype):
        with rasterio.open(path, "w", driver="GTiff", height=400, width=400, count=1, dtype=dtype, crs=f"EPSG:{other}", transform=tr) as d:
            d.write(np.full((400, 400), val, dtype), 1)
    for n, v, dt in (("g", 1500, "uint16"), ("s", 1100, "uint16"), ("scl", 4, "uint8")):
        make(tmp_path / f"{n}.tif", v, dt)
    feat = {"id": "TEST_44QKF_20240101_0_L2A", "properties": {"datetime": "2024-01-01T05:00:00Z", "eo:cloud_cover": 1.0,
            "grid:code": "MGRS-44QKF", "s2:processing_baseline": "05.10"},
            "assets": {"green": {"href": str(tmp_path / "g.tif")}, "swir16": {"href": str(tmp_path / "s.tif")}, "scl": {"href": str(tmp_path / "scl.tif")}}}
    prov = LiveProvider(dataclasses.replace(settings, mode="live"))
    monkeypatch.setattr(prov, "_stac_search", lambda *a, **k: [feat])
    start, end = datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2024, 6, 1, tzinfo=timezone.utc)
    scenes = prov.s2_scenes_for_grid(grid, "aoi-test", start, end)
    assert len(scenes) == 1 and scenes[0].meta["tile"] == "44QKF" and scenes[0].meta["boa_offset_applied"]
    c = (grid.height // 2, grid.width // 2)
    assert scenes[0].bands["green"][c] == pytest.approx(0.05) and scenes[0].bands["swir1"][c] == pytest.approx(0.01)
    monkeypatch.setattr(prov, "_stac_search", lambda *a, **k: pytest.fail("cache must be used"))
    assert len(prov.s2_scenes_for_grid(grid, "aoi-test", start, end)) == 1
    other_grid = Grid(type(grid.transform)(grid.transform.a, grid.transform.b, grid.transform.c + 500,
                                  grid.transform.d, grid.transform.e, grid.transform.f),
                         grid.crs, grid.height, grid.width)
    monkeypatch.setattr(prov, "_stac_search", lambda *a, **k: [])
    with pytest.raises(Exception, match="No usable"):          # a cache on a different grid is never reused
        prov.s2_scenes_for_grid(other_grid, "aoi-test", start, end)
