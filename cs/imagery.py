"""Imagery discovery/acquisition adapters. Only the deterministic sample provider is functional."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime

from .config import Settings
from .geo import Grid


@dataclass
class Scene:
    scene_id: str
    sensor: str                # "sentinel-1" | "sentinel-2"
    acquired: datetime         # tz-aware UTC
    bands: dict                # name -> 2D array on the common grid
    source: str
    synthetic: bool = False
    meta: dict = field(default_factory=dict)


class NotConfigured(RuntimeError):
    """Live imagery requested but not implemented / credentials missing."""


class SampleProvider:
    """Deterministic synthetic fixtures. NOT satellite data."""

    def flood_scenes(self, scenario: str = "flood_event", study_area: str = "solapur_bhima") -> tuple[Grid, list[Scene]]:
        from .sample_data import flood_scenes
        return flood_scenes(scenario, study_area=study_area)

    def s2_scenes(self, scenario: str = "persistent_loss", study_area: str = "solapur_bhima") -> tuple[Grid, list[Scene]]:
        from .sample_data import s2_scenes
        return s2_scenes(scenario, study_area=study_area)


class LiveProvider:
    """SCAFFOLD. Intended routes: Earth Engine export (COPERNICUS/S1_GRD, S2_SR_HARMONIZED) or
    CDSE STAC/openEO, exported once per AOI as clipped GeoTIFFs on a UTM grid and cached with
    request parameters + checksum. See README 'Live imagery'."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def _fail(self):
        raise NotConfigured("Live imagery adapters are scaffolded, not implemented. Use CS_MODE=sample "
                            "or implement discovery/export in cs/imagery.py (see README).")

    def flood_scenes(self, scenario: str = ""):
        self._fail()

    def s2_scenes(self, scenario: str = ""):
        self._fail()


def get_provider(settings: Settings):
    return SampleProvider() if settings.mode == "sample" else LiveProvider(settings)
