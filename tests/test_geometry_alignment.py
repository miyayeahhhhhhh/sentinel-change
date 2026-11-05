"""CRS transformation, reprojection, polygon clipping and map-overlay alignment (TEST rasters are synthetic and local)."""
import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from shapely.geometry import box

from cs import mapview
from cs.encroach import run_encroachment
from cs.geo import (Grid, GeoValidationError, footprint_wgs84, grid_for_geometry, polygon_mask, read_to_grid,
                    reproject_to_grid, utm_epsg_for_lonlat, web_mercator_overlay)
from cs.sample_data import make_grid, s2_scenes


def test_utm_zone_selection():
    assert utm_epsg_for_lonlat(78.47, 17.38) == 32644
    assert utm_epsg_for_lonlat(77.9, 17.4) == 32643          # west of 78E is zone 43
    assert utm_epsg_for_lonlat(80.2, -5) == 32744


def test_grid_for_geometry_covers_and_snaps():
    poly = box(78.40, 17.40, 78.45, 17.44)
    g = grid_for_geometry(poly, 4326, 10.0, pad_m=50)
    assert g.crs.to_epsg() == 32644 and g.pixel_area_m2() == pytest.approx(100.0)
    assert g.transform.c % 10 == 0 and g.transform.f % 10 == 0
    fp = footprint_wgs84(g)
    assert fp.contains(poly)


def test_polygon_mask_is_polygon_not_rectangle():
    tri = __import__("shapely.geometry", fromlist=["Polygon"]).Polygon([(78.40, 17.40), (78.46, 17.40), (78.40, 17.46)])
    g = grid_for_geometry(tri, 4326, 10.0, pad_m=20)
    m = polygon_mask(tri, 4326, g)
    frac = m.sum() / m.size
    assert 0.2 < frac < 0.6                                   # a right triangle fills ~half of its bbox
    area_km2 = m.sum() * 100 / 1e6
    assert area_km2 == pytest.approx(0.5 * 6.6 * 6.6, rel=0.05) # ~0.06 deg of lon/lat ~ 6.6 km
    with pytest.raises(GeoValidationError):
        polygon_mask(__import__("shapely.geometry", fromlist=["Polygon"]).Polygon(), 4326, g)


def test_reproject_marker_lands_on_same_ground_position():
    src = make_grid()                                          # EPSG:32644
    dst = Grid(from_origin(src.transform.c - 500, src.transform.f + 500, 20.0, 20.0), CRS.from_epsg(32644), 120, 120)
    a = np.zeros((src.height, src.width), "uint8"); a[98:102, 48:52] = 1
    out = reproject_to_grid(a, src, dst, "nearest", dst_nodata=255)
    r, c = np.argwhere(out == 1).mean(0)
    x_src, y_src = src.transform.c + 50.0 * 10, src.transform.f - 100.0 * 10
    x_dst, y_dst = dst.transform.c + (c + .5) * 20, dst.transform.f - (r + .5) * 20
    assert abs(x_src - x_dst) <= 15 and abs(y_src - y_dst) <= 15


def test_read_to_grid_across_utm_zones(tmp_path):
    """Source COG in zone 43 warped onto a zone-44 grid: a marker must land at the right lon/lat (old window read would not)."""
    lon, lat = 77.98, 17.40                                    # zone 43 side, close to the 78E boundary
    import pyproj
    t = pyproj.Transformer.from_crs(4326, 32643, always_xy=True)
    x0, y0 = t.transform(lon, lat)
    src_tr = from_origin(x0 - 1000, y0 + 1000, 10.0, 10.0)
    arr = np.zeros((200, 200), "uint16"); arr[100:104, 100:104] = 7000     # marker at ground position (x0, y0)
    path = tmp_path / "t.tif"
    with rasterio.open(path, "w", driver="GTiff", height=200, width=200, count=1, dtype="uint16", crs="EPSG:32643", transform=src_tr) as d:
        d.write(arr, 1)
    to44 = pyproj.Transformer.from_crs(4326, 32644, always_xy=True)
    gx, gy = to44.transform(lon, lat)
    grid = Grid(from_origin(gx - 600, gy + 600, 10.0, 10.0), CRS.from_epsg(32644), 120, 120)
    out = read_to_grid(path, grid, "nearest")
    r, c = np.argwhere(out == 7000).mean(0)
    px = grid.transform.c + (c + .5) * 10, grid.transform.f - (r + .5) * 10
    assert np.hypot(px[0] - gx, px[1] - gy) < 40               # within ~4 px of the true ground position


def test_web_mercator_overlay_alignment_roundtrip():
    """A marker pixel in the UTM grid must sit at the right lon/lat inside the Mercator overlay (<= 1 output px)."""
    import pyproj
    g = make_grid()
    a = np.zeros((g.height, g.width), "uint8"); a[40, 160] = 1; a[41, 160] = 1; a[40, 161] = 1; a[41, 161] = 1
    out, ((s, w), (n, e)) = web_mercator_overlay(a, g, "nearest", nodata=255)
    r, c = np.argwhere(out == 1).mean(0)
    # overlay pixel -> lon/lat (Mercator is linear in y-merc, not in lat)
    R = 6378137.0
    x_min, x_max = np.radians(w) * R, np.radians(e) * R
    y = lambda lat: R * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))
    xm = x_min + (c + .5) / out.shape[1] * (x_max - x_min)
    ym = y(n) - (r + .5) / out.shape[0] * (y(n) - y(s))
    lon = np.degrees(xm / R); lat = np.degrees(2 * np.arctan(np.exp(ym / R)) - np.pi / 2)
    tx, ty = g.transform.c + 161.0 * 10, g.transform.f - 41.0 * 10       # centre of the 2x2 marker
    exp_lon, exp_lat = pyproj.Transformer.from_crs(g.crs, 4326, always_xy=True).transform(tx, ty)
    px_deg = (e - w) / out.shape[1]
    assert abs(lon - exp_lon) <= 1.5 * px_deg and abs(lat - exp_lat) <= 1.5 * px_deg


def test_footprint_is_not_axis_aligned_bbox():
    g = grid_for_geometry(box(78.40, 17.40, 78.46, 17.46), 4326, 10.0)      # ~2.6 deg from the zone-44 central meridian
    fp = footprint_wgs84(g)
    assert fp.area < fp.envelope.area * 0.9999                                # tilted quadrilateral, not a lon/lat rectangle


def test_mask_overlay_does_not_paint_background():
    g = make_grid(); m = np.zeros((g.height, g.width), bool); m[10:20, 10:20] = True
    rgba, _ = mapview._mask_overlay(m, g, (200, 60, 40))
    frac = (rgba[..., 3] > 0).mean()
    assert 0.002 < frac < 0.01                                                # ~100 of 40000 px, not the whole footprint


def test_georeferenced_frequency_comparison_requires_aligned_rasters(tmp_path):
    g = make_grid()
    before = np.zeros((g.height, g.width), "float32")
    after = np.ones((g.height, g.width), "float32")
    before[20:30, 40:50] = 1
    after[20:30, 40:50] = 0
    before_path, after_path = tmp_path / "before.tif", tmp_path / "after.tif"
    with rasterio.open(before_path, "w", driver="GTiff", height=g.height, width=g.width, count=1,
                       dtype="float32", crs=g.crs, transform=g.transform, nodata=-9999) as dst:
        dst.write(before, 1)
    with rasterio.open(after_path, "w", driver="GTiff", height=g.height, width=g.width, count=1,
                       dtype="float32", crs=g.crs, transform=g.transform, nodata=-9999) as dst:
        dst.write(after, 1)
    rendered = mapview.build_frequency_comparison_map(before_path, after_path).get_root().render()
    assert "same-month baseline water frequency" in rendered
    assert "recent water frequency (same months)" in rendered

    misaligned = Grid(from_origin(g.transform.c + 10, g.transform.f, 10, 10), g.crs, g.height, g.width)
    with rasterio.open(after_path, "w", driver="GTiff", height=misaligned.height, width=misaligned.width, count=1,
                       dtype="float32", crs=misaligned.crs, transform=misaligned.transform, nodata=-9999) as dst:
        dst.write(after, 1)
    with pytest.raises(ValueError, match="not spatially aligned"):
        mapview.build_frequency_comparison_map(before_path, after_path)


def test_map_layers_are_independent_and_aoi_clipped():
    g, sc = s2_scenes("persistent_loss")
    aoi = np.zeros((g.height, g.width), bool); aoi[60:140, 60:180] = True
    r = run_encroachment(sc, g, aoi_mask=aoi)
    cat = mapview.layer_catalog(r)
    assert set(cat) == {"Boundaries", "Source imagery", "Observation coverage", "Analysis evidence"}
    assert not (r.arrays["loss"] & ~aoi).any() and not (r.arrays["observed"] & ~aoi).any()
    all_layers = mapview.layer_names(r)
    for subset in ([], all_layers[:1], all_layers):
        html = mapview.build_map(r, subset, 0.7, 3).get_root().render()
        for name in all_layers:
            assert name.split(" (")[0] in html or name in html
    only_one = mapview.build_map(r, [mapview.L_IMG], 0.7, None).get_root().render()
    assert only_one.count("feature_group") >= len(all_layers) - 1       # every layer is its own group, shown or not
