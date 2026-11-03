import json
import numpy as np
import pytest
from rasterio.crs import CRS
from rasterio.transform import from_origin
from cs.geo import GeoValidationError, Grid, check_geotiff, clean_mask, mask_to_gdf, validate_grid, write_geojson, write_geotiff
from cs.sample_data import make_grid


def test_clean_mask_removes_speckle_keeps_blob():
    m = np.zeros((50, 50), bool); m[10:20, 10:20] = True; m[40, 40] = True
    out = clean_mask(m, min_pixels=5)
    assert out[15, 15] and not out[40, 40]


def test_area_requires_projected_metres():
    g = Grid(from_origin(73.8, 18.5, 0.0001, 0.0001), CRS.from_epsg(4326), 10, 10)
    with pytest.raises(GeoValidationError):
        validate_grid(g)


def test_shape_mismatch_rejected():
    with pytest.raises(GeoValidationError):
        validate_grid(make_grid(), x=np.zeros((5, 5)))


def test_polygon_area_matches_pixels(tmp_path):
    g = make_grid(); m = np.zeros((g.height, g.width), bool); m[10:20, 10:30] = True   # 200 px = 2 ha
    gdf = mask_to_gdf(m, g)
    assert gdf.area_ha.sum() == pytest.approx(2.0)
    write_geojson(gdf, tmp_path / "p.geojson")
    assert json.loads((tmp_path / "p.geojson").read_text())["features"]


def test_geotiff_roundtrip_and_empty_geojson(tmp_path):
    g = make_grid(); write_geotiff(tmp_path / "a.tif", np.zeros((g.height, g.width), "uint8"), g, nodata=255)
    assert check_geotiff(tmp_path / "a.tif", g)
    write_geojson(mask_to_gdf(np.zeros((g.height, g.width), bool), g), tmp_path / "e.geojson")
    assert json.loads((tmp_path / "e.geojson").read_text())["features"] == []
