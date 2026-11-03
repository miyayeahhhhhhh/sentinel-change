"""Evidence sources for the unified encroachment pipeline."""
from .base import (BLOCKED_CREDENTIALS, CACHE_ONLY, INTEGRATED, NODATA, NOT_INTEGRATED, UNTESTED_REMOTE, EvidenceLayer,
                   EvidenceRequest, EvidenceSource, NotConfigured, Unavailable)
from .dynamic_world import DynamicWorldSource
from .jrc_gsw import JRCSurfaceWaterSource
from .sentinel1 import Sentinel1Source, qc_db


def evidence_sources(provider=None, cache_key: str | None = None, settings=None, pc=None) -> dict:
    """The three required evidence sources with their honest status (no source is ever reported 'operational')."""
    srcs = [Sentinel1Source(provider, cache_key, pc=pc), DynamicWorldSource(settings=settings), JRCSurfaceWaterSource(pc)]
    return {s.key: s for s in srcs}
