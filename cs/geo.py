"""Geospatial helpers: grid validation, mask cleanup, vectorisation, raster/vector export."""
from __future__ import annotations
import json
import math
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import rasterio.warp
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.features import rasterize, shapes
from rasterio.transform import Affine, array_bounds, from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import calculate_default_transform, reproject, transform_bounds
from scipy import ndimage as ndi
from shapely.geometry import Polygon, box, mapping, shape


class GeoValidationError(ValueError):
    """Raised when rasters/grids are inconsistent or unsuitable for area calculation."""


@dataclass(frozen=True)
class Grid:
    transform: Affine
    crs: CRS
    height: int
    width: int

    @property
    def bounds(self) -> tuple:  # west, south, east, north in native CRS
        return array_bounds(self.height, self.width, self.transform)

    def bounds_wgs84(self) -> tuple:
        return transform_bounds(self.crs, "EPSG:4326", *self.bounds)

    def pixel_area_m2(self) -> float:
        """Pixel area in m2. Only valid for projected metre CRS (never degrees)."""
        if not self.crs.is_projected or "met" not in str(self.crs.linear_units).lower():
            raise GeoValidationError(f"Area needs a projected metre CRS, got {self.crs}")
        return abs(self.transform.a * self.transform.e)

    def aoi_geojson(self) -> dict:
        """True grid footprint in EPSG:4326 (a UTM rectangle is not an axis-aligned lon/lat rectangle)."""
        return json.loads(json.dumps(mapping(footprint_wgs84(self))))


def validate_grid(grid: Grid, **arrays: np.ndarray) -> None:
    if grid.transform.b or grid.transform.d:
        raise GeoValidationError("Rotated transforms are not supported")
    grid.pixel_area_m2()
    for name, a in arrays.items():
        if a.shape != (grid.height, grid.width):
            raise GeoValidationError(f"{name} shape {a.shape} != grid {(grid.height, grid.width)}")


def clean_mask(mask: np.ndarray, min_pixels: int, open_iter: int = 1) -> np.ndarray:
    """Morphological opening, then drop connected components smaller than min_pixels."""
    m = mask.astype(bool)
    if open_iter > 0 and m.any():
        m = ndi.binary_opening(m, structure=np.ones((3, 3), bool), iterations=open_iter)
    lab, n = ndi.label(m)
    if n == 0:
        return m
    sizes = ndi.sum(m, lab, index=np.arange(1, n + 1))
    return np.isin(lab, 1 + np.flatnonzero(sizes >= min_pixels))


def mask_to_gdf(mask: np.ndarray, grid: Grid) -> gpd.GeoDataFrame:
    m = mask.astype("uint8")
    geoms = []
    for g, v in shapes(m, mask=m.astype(bool), transform=grid.transform):
        if v == 1:
            s = shape(g)
            if not s.is_valid:
                s = s.buffer(0)
            if not s.is_empty:
                geoms.append(s)
    return gpd.GeoDataFrame(
        {"area_ha": [round(g.area / 1e4, 3) for g in geoms]}, geometry=geoms, crs=grid.crs
    )


def write_geotiff(path: Path, arr: np.ndarray, grid: Grid, nodata=None) -> None:
    with rasterio.open(
        path, "w", driver="GTiff", height=grid.height, width=grid.width, count=1,
        dtype=arr.dtype, crs=grid.crs, transform=grid.transform, nodata=nodata, compress="deflate",
    ) as dst:
        dst.write(arr, 1)


def check_geotiff(path: Path, grid: Grid) -> bool:
    with rasterio.open(path) as src:
        return (src.crs == grid.crs and src.transform == grid.transform
                and (src.height, src.width) == (grid.height, grid.width))


def write_geojson(gdf: gpd.GeoDataFrame, path: Path) -> None:
    """GeoJSON in EPSG:4326 (exchange CRS). Empty results still produce a valid FeatureCollection."""
    if len(gdf) == 0:
        Path(path).write_text('{"type": "FeatureCollection", "features": []}')
    else:
        Path(path).write_text(gdf.to_crs(4326).to_json())


# ---------------------------------------------------------------------------------------------
# CRS handling, reprojection, clipping and display alignment
# ---------------------------------------------------------------------------------------------
_RESAMPLING = {"nearest": Resampling.nearest, "bilinear": Resampling.bilinear, "cubic": Resampling.cubic,
               "average": Resampling.average, "mode": Resampling.mode}


def utm_epsg_for_lonlat(lon: float, lat: float) -> int:
    """EPSG code of the WGS84 UTM zone containing a lon/lat point."""
    if not (-180.0 <= lon <= 180.0 and -80.0 <= lat <= 84.0):
        raise GeoValidationError(f"lon/lat outside UTM range: {lon}, {lat}")
    zone = int((lon + 180.0) // 6.0) + 1
    return (32600 if lat >= 0 else 32700) + min(zone, 60)


def footprint_wgs84(grid: Grid, densify: int = 16) -> Polygon:
    """Grid footprint as a polygon in EPSG:4326, edges densified so projection curvature is kept."""
    w, s, e, n = grid.bounds
    ring = []
    for (x0, y0), (x1, y1) in (((w, s), (e, s)), ((e, s), (e, n)), ((e, n), (w, n)), ((w, n), (w, s))):
        for t in np.linspace(0, 1, densify, endpoint=False):
            ring.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0)))
    xs, ys = zip(*ring)
    lon, lat = rasterio.warp.transform(grid.crs, "EPSG:4326", list(xs), list(ys))
    return Polygon(zip(lon, lat))


def grid_for_geometry(geom, geom_crs, resolution: float = 10.0, pad_m: float = 100.0, crs=None) -> Grid:
    """Projected-metre grid covering a geometry (+pad), snapped to multiples of `resolution`.
    The UTM zone is chosen from the geometry centroid unless `crs` is given."""
    gs = gpd.GeoSeries([geom], crs=geom_crs)
    if gs.iloc[0].is_empty:
        raise GeoValidationError("Cannot build a grid for an empty geometry")
    if crs is None:
        c = gs.to_crs(4326).iloc[0].centroid
        crs = CRS.from_epsg(utm_epsg_for_lonlat(c.x, c.y))
    crs = CRS.from_user_input(crs)
    minx, miny, maxx, maxy = gs.to_crs(crs).total_bounds
    x0 = math.floor((minx - pad_m) / resolution) * resolution
    y1 = math.ceil((maxy + pad_m) / resolution) * resolution
    width = int(math.ceil((maxx + pad_m - x0) / resolution))
    height = int(math.ceil((y1 - (miny - pad_m)) / resolution))
    grid = Grid(from_origin(x0, y1, resolution, resolution), crs, height, width)
    grid.pixel_area_m2()   # raises unless projected metres
    return grid


def polygon_mask(geom, geom_crs, grid: Grid, all_touched: bool = False) -> np.ndarray:
    """Boolean raster mask of a polygon on `grid` (geometry reprojected to the grid CRS first)."""
    g = gpd.GeoSeries([geom], crs=geom_crs).to_crs(grid.crs).iloc[0]
    if g.is_empty:
        raise GeoValidationError("Empty geometry cannot be rasterised")
    if not g.is_valid:
        g = g.buffer(0)
    m = rasterize([(g, 1)], out_shape=(grid.height, grid.width), transform=grid.transform,
                  fill=0, all_touched=all_touched, dtype="uint8")
    return m.astype(bool)


def reproject_to_grid(arr: np.ndarray, src: Grid, dst: Grid, resampling: str = "nearest",
                      src_nodata=None, dst_nodata=None) -> np.ndarray:
    """Warp a 2-D array between grids/CRSs. Pixels outside the source become `dst_nodata`.
    Use 'nearest' for masks/classes and 'bilinear' for continuous fields."""
    if arr.shape != (src.height, src.width):
        raise GeoValidationError(f"array shape {arr.shape} != source grid {(src.height, src.width)}")
    if dst_nodata is None:
        dst_nodata = np.nan if np.issubdtype(arr.dtype, np.floating) else 0
    out = np.full((dst.height, dst.width), dst_nodata, dtype=arr.dtype)
    reproject(source=arr, destination=out, src_transform=src.transform, src_crs=src.crs, src_nodata=src_nodata,
              dst_transform=dst.transform, dst_crs=dst.crs, dst_nodata=dst_nodata, resampling=_RESAMPLING[resampling])
    return out


def read_to_grid(path, grid: Grid, resampling: str = "bilinear", band: int = 1, return_mask: bool = False):
    """Read a raster (local path or COG URL) in ITS OWN CRS and resample onto `grid` via a warped VRT.
    Replaces window reads that assume the source and target share a CRS (wrong across UTM zones).
    With return_mask=True also returns a bool array that is True only where the source actually has valid data
    (outside the source footprint or nodata -> False), so outside-coverage fill is never mistaken for a value."""
    with rasterio.open(path) as src:
        return read_dataset_to_grid(src, grid, resampling, band, return_mask)


def read_dataset_to_grid(src, grid: Grid, resampling: str = "bilinear", band: int | None = 1, return_mask: bool = False):
    """As read_to_grid for an open dataset; band=None reads every band -> (count, H, W)."""
    with WarpedVRT(src, crs=grid.crs, transform=grid.transform, width=grid.width, height=grid.height,
                   resampling=_RESAMPLING[resampling], add_alpha=False) as vrt:
        data = vrt.read() if band is None else vrt.read(band)
        if not return_mask:
            return data
        m = vrt.read_masks() if band is None else vrt.read_masks(band)
        return data, m > 0


def web_mercator_overlay(arr: np.ndarray, grid: Grid, resampling: str = "nearest", nodata=None,
                         max_px: int = 2_000_000) -> tuple[np.ndarray, list]:
    """Warp `arr` to EPSG:3857 so a Leaflet ImageOverlay (stretched linearly in Mercator space) is exact.
    Returns (array_3857, [[south, west], [north, east]]) with bounds in degrees. Outside-source pixels = nodata."""
    web = CRS.from_epsg(3857)
    w, s, e, n = grid.bounds
    tr, wd, ht = calculate_default_transform(grid.crs, web, grid.width, grid.height, w, s, e, n)
    if wd * ht > max_px:
        f = math.sqrt(wd * ht / max_px)
        tr, wd, ht = calculate_default_transform(grid.crs, web, grid.width, grid.height, w, s, e, n,
                                                 dst_width=int(wd / f), dst_height=int(ht / f))
    dst = Grid(tr, web, ht, wd)
    out = reproject_to_grid(arr, grid, dst, resampling, dst_nodata=nodata)
    bw, bs, be, bn = transform_bounds(web, "EPSG:4326", *dst.bounds)   # exact for Mercator (lon~x, lat~y)
    return out, [[bs, bw], [bn, be]]
