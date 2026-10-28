"""Geospatial helpers: grid validation, mask cleanup, vectorisation, raster/vector export."""
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.features import shapes
from rasterio.transform import Affine, array_bounds
from rasterio.warp import transform_bounds
from scipy import ndimage as ndi
from shapely.geometry import box, mapping, shape


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
        w, s, e, n = self.bounds_wgs84()
        return json.loads(json.dumps(mapping(box(w, s, e, n))))


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
