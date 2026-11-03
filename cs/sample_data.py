"""Deterministic SYNTHETIC Sentinel-2 fixtures on the synthetic test grid. Not real satellite observations."""
from __future__ import annotations
from datetime import datetime, timezone

import numpy as np
from .imagery import Scene
from .study_areas import H, W, STUDY_AREAS, make_grid  # noqa: F401  (re-exported for callers/tests)

SYNTH = "SYNTHETIC FIXTURE - not Sentinel data"


def _dt(y, m, d, hh=5, mm=30):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


# Wet-fraction lookup by month (SW-monsoon regime: Jun–Oct peak)
WET = {1: .3, 2: .2, 3: .1, 4: 0, 5: 0, 6: .3, 7: .8, 8: 1, 9: 1, 10: .8, 11: .6, 12: .4}

# NE-monsoon regime (Oct–Dec peak).
# Values represent approximate fractional water-extent variation through the year.
WET_NE = {1: .5, 2: .4, 3: .3, 4: .2, 5: .1, 6: .1, 7: .1, 8: .15, 9: .2, 10: .7, 11: 1.0, 12: .9}
HEAVY_CLOUD = {7, 19, 31}
LIGHT_CLOUD = {2, 10, 14, 22, 26, 34}


def s2_scenes(scenario: str = "persistent_loss", seed: int = 11, study_area: str = "synthetic_test_grid"):
    grid, rng = make_grid(study_area), np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W]
    ratio = np.sqrt(((xx - 100) / 45.0) ** 2 + ((yy - 100) / 32.0) ** 2)
    fill = (xx >= 128) & (yy >= 85) & (yy <= 115)
    has_fill = scenario in ("persistent_loss", "no_wet_season", "sparse")
    # Synthetic wet-fraction calendar follows the area's declared seasonal profile (no place-name checks).
    wet_lut = WET_NE if STUDY_AREAS[study_area]["seasonal_profile"] == "ne_monsoon" else WET
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
