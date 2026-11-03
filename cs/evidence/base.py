"""Evidence-source contract. Sentinel-1 SAR, Dynamic World and JRC GSW are EVIDENCE SOURCES inside the single
encroachment pipeline (cs/encroach.py + cs/fusion.py); none is a detector of its own.

Rules: a source returns real data with provenance, or an `Unavailable` outcome that says why. Nothing is ever
fabricated, interpolated or substituted, and unavailability is never converted into a negative finding.

Status vocabulary (deliberately has no "operational": that claim needs a recorded successful remote run):
  not_integrated | cache_only | blocked_credentials | implemented_untested_remote
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from ..geo import Grid
from ..imagery import NotConfigured  # noqa: F401  (re-exported: the single "unavailable" exception)

INTEGRATED = "integrated"   # kept for compatibility; no source currently claims it
CACHE_ONLY, NOT_INTEGRATED = "cache_only", "not_integrated"
BLOCKED_CREDENTIALS, UNTESTED_REMOTE = "blocked_credentials", "implemented_untested_remote"
NODATA = 255                # encoded evidence rasters are uint8 with 255 = not observed


@dataclass(frozen=True)
class EvidenceRequest:
    grid: Grid                 # common projected-metre analysis grid (derived from the registry AOI)
    aoi_mask: np.ndarray       # bool, polygon-clipped AOI on `grid`
    start: datetime            # tz-aware UTC: start of the baseline window
    end: datetime              # end of the recent window (analysis time)
    aoi_id: str = ""           # registry id; also the cache key
    recent_start: datetime | None = None   # baseline = [start, recent_start), recent = [recent_start, end]


@dataclass
class EvidenceLayer:
    source: str
    kind: str                                  # "scenes" | "raster"
    grid: Grid
    arrays: dict                               # name -> array on `grid` (uint8 rasters use 255 = unobserved)
    observed: np.ndarray                       # bool: where this source actually has usable data (gaps are NOT negatives)
    provenance: dict                           # dataset, endpoint, identifiers, dates, processing, versions, counts
    scenes: list = field(default_factory=list)
    synthetic: bool = False


@dataclass
class Unavailable:
    """Explicit 'no usable evidence' outcome. status: unavailable | insufficient."""
    source: str
    reason: str
    status: str = "unavailable"
    provenance: dict = field(default_factory=dict)


class EvidenceSource:
    key: str = ""
    label: str = ""
    status: str = NOT_INTEGRATED
    dataset: str = ""
    requirements: str = ""
    role: str = ""
    tested_remote: bool = False   # flipped only by a recorded successful run against the real service

    def describe(self) -> dict:
        return {"key": self.key, "label": self.label, "status": self.status, "tested_against_remote": self.tested_remote,
                "dataset": self.dataset, "role_in_pipeline": self.role, "requirements": self.requirements}

    def fetch(self, req: EvidenceRequest):  # pragma: no cover - interface
        raise NotImplementedError
