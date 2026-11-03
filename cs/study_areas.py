"""Fixed-grid study areas (legacy validation AOI + synthetic test grid).

The Hyderabad pilot does NOT live here: its district/waterbody geometry comes from the provenance-tracked
registry (`cs/registry.py`) and its analysis grid is derived from that geometry (`geo.grid_for_geometry`).
"""
from __future__ import annotations

from rasterio.crs import CRS
from rasterio.transform import from_origin

from .geo import Grid

H = W = 200

STUDY_AREAS = {
    # Synthetic fixtures need *some* projected grid. It is placed over open ocean (UTM 44N, ~0.02 N / 81 E) so no
    # real land, place or waterbody is implied. Test/development only; never shown as a monitored area.
    "synthetic_test_grid": {
        "name": "SYNTHETIC test grid (not a real place)",
        "crs_epsg": 32644,
        "origin": (500000.0, 4000.0),
        "desc": "Deterministic synthetic fixture grid for tests and offline development.",
        "synthetic": True,
        "role": "test_fixture",
        "seasonal_profile": "sw_monsoon",
    },
    # Legacy validation AOI. Real Sentinel-2 L2A scenes are bundled in data/cache/pallikaranai_marsh/.
    # It is a fixed 2 km x 2 km analysis grid, NOT a verified administrative/waterbody boundary, and it is not
    # part of the Hyderabad pilot.
    "pallikaranai_marsh": {
        "name": "Pallikaranai Marsh, Chennai (legacy validation grid, not Hyderabad)",
        "crs_epsg": 32644,
        "origin": (414000.0, 1431000.0),
        "desc": "Legacy real-data validation grid (2 km x 2 km). Boundary is a rectangular analysis grid, not a verified AOI.",
        "synthetic": False,
        "role": "legacy_validation",
        "seasonal_profile": "ne_monsoon",
        "admin_district": "Chennai / Chengalpattu (Sholinganallur Taluk), Tamil Nadu",
        "wetland_authority": "Tamil Nadu Forest Department (Conservation Authority of Pallikaranai Marsh)",
        "jurisdiction_verified": False,   # carried over from the previous build; no authoritative source recorded
        "sentinel2_tiles": ["44PMV"],
        "datum_note": "UTM Zone 44N (EPSG:32644); origin 414000 m E, 1431000 m N",
    },
}


def make_grid(study_area: str = "synthetic_test_grid") -> Grid:
    from .config import UnknownStudyArea
    if study_area not in STUDY_AREAS:
        raise UnknownStudyArea(f"Unknown study area {study_area!r}; known: {sorted(STUDY_AREAS)}")
    info = STUDY_AREAS[study_area]
    return Grid(from_origin(info["origin"][0], info["origin"][1], 10.0, 10.0), CRS.from_epsg(info["crs_epsg"]), H, W)
