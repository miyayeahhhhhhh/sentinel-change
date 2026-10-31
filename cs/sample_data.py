"""Deterministic SYNTHETIC fixtures on a 200x200 10 m UTM 43N grid. Not real satellite observations."""
from __future__ import annotations
from datetime import datetime, timezone

import numpy as np
from rasterio.crs import CRS
from rasterio.transform import from_origin

from .geo import Grid
from .imagery import Scene

H = W = 200
SYNTH = "SYNTHETIC FIXTURE - not Sentinel data"


STUDY_AREAS = {
    "solapur_bhima": {
        "name": "Bhima River Basin (Solapur, MH)",
        "crs_epsg": 32643,
        "origin": (374000.0, 2052000.0),
        "desc": "Monsoon floodplain & seasonal reservoir storage",
    },
    "vijayawada_krishna": {
        "name": "Krishna River Delta (Vijayawada, AP)",
        "crs_epsg": 32644,
        "origin": (412000.0, 1826000.0),
        "desc": "Barrage floodplain & riverbank transitions",
    },
    "pulicat_lake": {
        "name": "Pulicat Lagoon Wetland (AP/TN)",
        "crs_epsg": 32644,
        "origin": (418000.0, 1485000.0),
        "desc": "Coastal wetland & peripheral waterbody fringe",
    },
    # PRIMARY study area: Pallikaranai Marsh, Chennai, Tamil Nadu
    # Marsh core: ~12.936°N, 80.215°E → UTM Zone 44N (EPSG:32644)
    # UTM 44N easting ~414000, northing ~1431000 (verified against WGS84 → UTM44N projection)
    # Grid covers ~2 km × 2 km at 10 m resolution (200×200 px) over the marsh reserve forest.
    # Real Sentinel-2 MGRS tile: 44PMV (granule covers Chennai/Pallikaranai).
    # Wet season: NE monsoon (Oct–Dec); dry season: Feb–May.
    "pallikaranai_marsh": {
        "name": "Pallikaranai Marsh (Chennai, TN) — Primary AOI",
        "crs_epsg": 32644,
        "origin": (414000.0, 1431000.0),
        "desc": (
            "PRIMARY: Persistent wetland/waterbody loss detection. "
            "Pallikaranai Marsh, Chennai, Tamil Nadu. "
            "NE monsoon wet season (Oct–Dec). "
            "Live S2 tile: 44PMV. Admin: Chennai / Chengalpattu / Kancheepuram district boundary, "
            "Greater Chennai Corporation / Sholinganallur."
        ),
        # Metadata fields for evidence traceability and case files
        "admin_district": "Chennai / Chengalpattu (Sholinganallur Taluk), Tamil Nadu",
        "wetland_authority": "Tamil Nadu Forest Department (Conservation Authority of Pallikaranai Marsh)",
        "sentinel2_tiles": ["44PMV"],
        "sentinel1_orbit": "Ascending / Descending (IW, VV+VH)",
        "datum_note": "UTM Zone 44N (EPSG:32644); origin 414000m E, 1431000m N (~12.936°N, 80.215°E)",
    },
}


def make_grid(study_area: str = "solapur_bhima") -> Grid:
    info = STUDY_AREAS.get(study_area, STUDY_AREAS["solapur_bhima"])
    return Grid(from_origin(info["origin"][0], info["origin"][1], 10.0, 10.0), CRS.from_epsg(info["crs_epsg"]), H, W)


def _dt(y, m, d, hh=5, mm=30):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def flood_scenes(scenario: str = "flood_event", seed: int = 7, study_area: str = "solapur_bhima"):
    grid, rng = make_grid(study_area), np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W]
    rc = 100 + 18 * np.sin(xx / 28.0)
    d = np.abs(yy - rc)
    river = d <= 4
    zone = (d <= 28) & (xx >= 40) & (xx <= 150) & ~river
    shadow = (yy >= 150) & (yy < 175) & (xx >= 20) & (xx < 60)   # persistently dark, must NOT flag

    def vv(flooded: bool):
        v = rng.normal(-11, 1.2, (H, W))
        v[river] = rng.normal(-22, 1.0, river.sum())
        v[shadow] = rng.normal(-21, 1.0, shadow.sum())
        if flooded:
            v[zone] = rng.normal(-20, 1.2, zone.sum())
            v[rng.integers(0, H, 30), rng.integers(0, W, 30)] = -20.0   # speckle
        return v

    pre_dates = [(2025, 7, 4), (2025, 7, 16), (2025, 7, 28)]
    ev_dates = [(2025, 8, 9), (2025, 8, 21)]
    if scenario == "insufficient":
        pre_dates = pre_dates[:1]
    scenes = []
    for y, m, dd in pre_dates:
        scenes.append(((y, m, dd), vv(False)))
    for i, (y, m, dd) in enumerate(ev_dates):
        v = vv(scenario in ("flood_event", "insufficient", "partial_coverage"))
        if scenario == "flood_event" and i == 1:
            v[:70, :] = np.nan                       # swath gap
        if scenario == "partial_coverage":
            v[:150, :] = np.nan                      # mostly missing
        scenes.append(((y, m, dd), v))
    out = [Scene(f"SYNTH-S1-{y}{m:02d}{d_:02d}", "sentinel-1", _dt(y, m, d_), {"vv_db": v.astype("float32")},
                 SYNTH, True, {"orbit": "synthetic"}) for (y, m, d_), v in scenes]
    return grid, out


# Wet-fraction lookup by month (SW monsoon regions: Jun–Oct peak)
WET = {1: .3, 2: .2, 3: .1, 4: 0, 5: 0, 6: .3, 7: .8, 8: 1, 9: 1, 10: .8, 11: .6, 12: .4}

# Pallikaranai Marsh uses NE monsoon (Oct–Dec peak). Separate lookup for correct seasonality.
# Values represent approximate fractional water-extent variation through the year.
WET_NE = {1: .5, 2: .4, 3: .3, 4: .2, 5: .1, 6: .1, 7: .1, 8: .15, 9: .2, 10: .7, 11: 1.0, 12: .9}
HEAVY_CLOUD = {7, 19, 31}
LIGHT_CLOUD = {2, 10, 14, 22, 26, 34}


def s2_scenes(scenario: str = "persistent_loss", seed: int = 11, study_area: str = "solapur_bhima"):
    grid, rng = make_grid(study_area), np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W]
    ratio = np.sqrt(((xx - 100) / 45.0) ** 2 + ((yy - 100) / 32.0) ** 2)
    fill = (xx >= 128) & (yy >= 85) & (yy <= 115)
    has_fill = scenario in ("persistent_loss", "no_wet_season", "sparse")
    # Use NE-monsoon seasonal calendar for Pallikaranai; SW-monsoon for all others.
    wet_lut = WET_NE if study_area == "pallikaranai_marsh" else WET
    scenes, i = [], -1
    for y in (2023, 2024, 2025):
        for m in range(1, 13):
            i += 1
            if scenario == "sparse" and y == 2025 and m != 2:
                continue
            if scenario == "no_wet_season" and y == 2025 and 6 <= m <= 10:
                continue
            water = ratio <= 0.88 + 0.12 * wet_lut[m]
            if has_fill and (y, m) >= (2025, 1 if scenario == "no_wet_season" else 3):
                water = water & ~fill
            g = np.where(water, 0.08, 0.10) + rng.normal(0, 0.008, (H, W))
            s = np.where(water, 0.02, 0.25) + rng.normal(0, 0.008, (H, W))
            scl = np.where(water, 6, 5).astype("uint8")
            if i in HEAVY_CLOUD:
                g[:185], s[:185], scl[:185] = 0.5, 0.5, 9
            elif i in LIGHT_CLOUD:
                g[20:80, 20:90], s[20:80, 20:90], scl[20:80, 20:90] = 0.5, 0.5, 9
                g[80:90, 20:90], s[80:90, 20:90], scl[80:90, 20:90] = 0.04, 0.03, 3   # shadow
            scenes.append(Scene(f"SYNTH-S2-{y}{m:02d}05", "sentinel-2", _dt(y, m, 5, 5, 0),
                                {"green": g.astype("float32"), "swir1": s.astype("float32"), "scl": scl},
                                SYNTH, True))
    return grid, scenes
