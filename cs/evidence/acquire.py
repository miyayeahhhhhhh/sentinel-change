"""Collect the three evidence sources for one AOI. Every source yields an EvidenceLayer or an explicit Unavailable;
exceptions never abort the run and never become negative findings."""
from __future__ import annotations
from pathlib import Path
from time import perf_counter

from ..config import Settings
from .base import EvidenceRequest, NotConfigured, Unavailable
from .dynamic_world import DynamicWorldSource
from .jrc_gsw import JRCSurfaceWaterSource
from .sentinel1 import Sentinel1Source
from .stac import PlanetaryComputer


def make_pc(settings: Settings, transport=None) -> PlanetaryComputer:
    kw = {"transport": transport} if transport else {}
    return PlanetaryComputer(settings.pc_stac_url, settings.pc_sas_url, settings.pc_subscription_key, settings.http_timeout_s, **kw)


def acquire_evidence(settings: Settings, req: EvidenceRequest, pc: PlanetaryComputer | None = None, dw_client=None,
                     cache_root: Path | None = None, sources: tuple = ("sentinel1", "dynamic_world", "jrc_gsw"),
                     s1_reader=None, jrc_reader=None) -> dict:
    total_started = perf_counter()
    pc_started = perf_counter()
    pc = pc or make_pc(settings)
    print(f"[CS-TIMING] Planetary Computer client ready in {perf_counter() - pc_started:.2f}s", flush=True)
    cache_dir = (cache_root or settings.db_path.parent / "cache") / f"{req.aoi_id}_s1"
    out = {}
    builders = {
        "sentinel1": lambda: Sentinel1Source(pc=pc, collection=settings.s1_collection, cache_dir=cache_dir,
                                             **({"reader": s1_reader} if s1_reader else {})),
        "dynamic_world": lambda: DynamicWorldSource(client=dw_client, settings=None if dw_client else settings),
        "jrc_gsw": lambda: JRCSurfaceWaterSource(pc, settings.jrc_collection, **({"reader": jrc_reader} if jrc_reader else {})),
    }
    for k in sources:
        started = perf_counter()
        print(f"[CS-TIMING] {k}: started", flush=True)
        try:
            out[k] = builders[k]().fetch(req)
        except NotConfigured as exc:
            out[k] = Unavailable(k, str(exc))
        except Exception as exc:                              # recorded, never silent, never a negative finding
            out[k] = Unavailable(k, f"{type(exc).__name__}: {exc}")
        elapsed = perf_counter() - started
        outcome = out[k]
        if hasattr(outcome, "arrays"):
            observed = int(outcome.observed.sum())
            print(f"[CS-TIMING] {k}: completed in {elapsed:.2f}s; observed_pixels={observed}; arrays={len(outcome.arrays)}", flush=True)
        else:
            print(f"[CS-TIMING] {k}: {outcome.status} after {elapsed:.2f}s; reason={outcome.reason[:240]}", flush=True)
    print(f"[CS-TIMING] supplementary evidence total: {perf_counter() - total_started:.2f}s", flush=True)
    return out


def source_states(outcomes: dict) -> dict:
    """Human-readable per-source state for the case record."""
    s = {}
    for k, o in outcomes.items():
        s[k] = "used" if hasattr(o, "arrays") else f"{o.status}: {o.reason}"
    return s
