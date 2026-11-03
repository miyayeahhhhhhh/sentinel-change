"""Sentinel-1 SAR evidence: terrain-corrected VV backscatter -> per-pixel water-loss evidence on the AOI grid.

Acquisition: Planetary Computer STAC `sentinel-1-rtc` (radiometrically terrain-corrected gamma0, linear power, COG,
anonymous SAS token). Implemented but NOT yet exercised against the live service (no network in the build sandbox).
Fallback: pre-cached scenes (legacy `vv_db` npz or this module's grid-stamped cache) are labelled mode="cache_only".

Compatibility rules: IW mode, VV present, product RTC; scenes are compared only within ONE (orbit_state, relative_orbit)
group so baseline and recent observations share viewing geometry.
"""
from __future__ import annotations
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

from ..geo import Grid, footprint_wgs84, read_to_grid
from ..imagery import Scene
from .base import (CACHE_ONLY, NODATA, UNTESTED_REMOTE, EvidenceLayer, EvidenceRequest, EvidenceSource, NotConfigured,
                   Unavailable)
from .stac import PlanetaryComputer, RemoteError, parse_dt

log = logging.getLogger(__name__)


def qc_db(vv: np.ndarray) -> np.ndarray:
    """Quality control: non-finite or physically implausible dB values become NaN (unobserved)."""
    v = vv.astype("float32").copy()
    v[~np.isfinite(v) | (v < -40) | (v > 5)] = np.nan
    return v


@dataclass(frozen=True)
class SarParams:
    """Uncalibrated starting values (gamma0 VV dB). Not validated against local ground truth."""
    water_db: float = -18.0
    speckle_px: int = 3                # nan-aware boxcar in the LINEAR domain before dB conversion
    min_scene_valid: float = 0.5
    min_baseline_scenes: int = 6
    min_recent_scenes: int = 4
    min_span_days: int = 90
    min_pixel_obs: int = 3
    base_water_freq: float = 0.5       # water in >= this share of baseline looks => was water
    recent_water_freq_max: float = 0.2 # water in <= this share of recent looks => no longer water

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------------------------- preprocessing
def to_db(vv_lin: np.ndarray, speckle_px: int = 3) -> np.ndarray:
    """Linear gamma0/sigma0 power -> speckle-reduced dB. Non-positive/non-finite pixels stay invalid (never filled)."""
    v = np.asarray(vv_lin, dtype="float32")
    bad = ~np.isfinite(v) | (v <= 0)
    vv = np.where(bad, 0.0, v).astype("float32")
    if speckle_px > 1:
        w = (~bad).astype("float32")
        num, den = ndi.uniform_filter(vv, speckle_px, mode="nearest"), ndi.uniform_filter(w, speckle_px, mode="nearest")
        m = np.where(den > 1e-6, num / np.maximum(den, 1e-6), np.nan)
    else:
        m = vv.copy()
    m[bad] = np.nan
    with np.errstate(divide="ignore", invalid="ignore"):
        return qc_db(10.0 * np.log10(m))


def check_s1_item(item: dict) -> tuple[bool, str, dict]:
    """Product compatibility from STAC metadata. Missing orbit/polarisation metadata = incompatible (cannot establish
    that observations are comparable)."""
    p = item.get("properties", {})
    pols = [str(x).upper() for x in (p.get("sar:polarizations") or [])]
    meta = {"orbit_state": p.get("sat:orbit_state"), "relative_orbit": p.get("sat:relative_orbit"),
            "polarizations": pols, "mode": p.get("sar:instrument_mode"), "platform": p.get("platform"),
            "product_type": p.get("sar:product_type")}
    if "VV" not in pols:
        return False, "no VV polarisation", meta
    if str(meta["mode"] or "").upper() != "IW":
        return False, f"instrument mode {meta['mode']!r} is not IW", meta
    if meta["orbit_state"] is None or meta["relative_orbit"] is None:
        return False, "orbit metadata missing; geometric comparability cannot be established", meta
    if "vv" not in item.get("assets", {}):
        return False, "no 'vv' asset", meta
    return True, "", meta


def select_orbit_group(items: list[dict], recent_start: datetime, p: SarParams):
    """Pick the (orbit_state, relative_orbit) group with the most balanced baseline/recent coverage."""
    groups: dict = {}
    for it in items:
        g = (str(it["_meta"]["orbit_state"]).lower(), int(it["_meta"]["relative_orbit"]))
        t = parse_dt(it["properties"]["datetime"])
        d = groups.setdefault(g, {"base": [], "rec": []})
        d["rec" if t >= recent_start else "base"].append(it)
    counts = {f"{k[0]}_{k[1]}": (len(v["base"]), len(v["rec"])) for k, v in groups.items()}
    ok = {k: v for k, v in groups.items() if len(v["base"]) >= p.min_baseline_scenes and len(v["rec"]) >= p.min_recent_scenes}
    if not ok:
        return None, counts
    best = max(ok, key=lambda k: (min(len(ok[k]["base"]), len(ok[k]["rec"])), len(ok[k]["base"]) + len(ok[k]["rec"])))
    return (best, ok[best]["base"] + ok[best]["rec"]), counts


# ---------------------------------------------------------------------------------------------- cache
def _cache_path(cache_dir: Path, sid: str) -> Path:
    return cache_dir / f"{sid}.npz"


def save_scene(cache_dir: Path, sc: Scene, grid: Grid) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(_cache_path(cache_dir, sc.scene_id), vv_lin=sc.bands["vv_lin"].astype("float32"), sid=sc.scene_id,
                        dt=sc.acquired.isoformat(), epsg=grid.crs.to_epsg(), transform=np.array(list(grid.transform)[:6]),
                        meta=json.dumps(sc.meta, default=str))


def load_cached_scenes(cache_dir: Path, grid: Grid | None = None) -> list[Scene]:
    """Grid-stamped cache of this module (vv_lin) and legacy cache files (vv_db; no grid stamp, shape-checked only)."""
    out = []
    for p in sorted(Path(cache_dir).glob("*.npz")) if Path(cache_dir).exists() else []:
        try:
            d = np.load(p)
            dt = parse_dt(str(d["dt"]))
            sid = str(d["sid"]) if "sid" in d.files else p.stem
            if grid is not None and "transform" in d.files and (
                    int(d["epsg"]) != grid.crs.to_epsg() or not np.allclose(d["transform"], list(grid.transform)[:6])):
                log.warning("S1 cache %s is on a different grid; skipped", p.name)
                continue
            if "vv_lin" in d.files:
                bands, meta = {"vv_lin": d["vv_lin"].astype("float32")}, json.loads(str(d["meta"]))
            elif "vv_db" in d.files:
                bands, meta = {"vv_db": d["vv_db"].astype("float32")}, {"orbit_state": None, "relative_orbit": None, "legacy_cache": True}
            else:
                continue
            arr = next(iter(bands.values()))
            if grid is not None and arr.shape != (grid.height, grid.width):
                log.warning("S1 cache %s has the wrong shape; skipped", p.name)
                continue
            out.append(Scene(sid, "sentinel-1", dt, bands, "Copernicus Sentinel-1 (cached)", False, meta))
        except Exception as exc:
            log.warning("unreadable S1 cache %s: %s", p.name, exc)
    return out


# ---------------------------------------------------------------------------------------------- acquisition
def acquire_scenes(pc: PlanetaryComputer, collection: str, req: EvidenceRequest, p: SarParams, cache_dir: Path,
                   reader=read_to_grid, max_items: int = 400) -> tuple[list[Scene], dict]:
    """Discover, filter for compatibility, choose one orbit group, read onto the grid. Returns (scenes, report)."""
    if req.recent_start is None:
        raise NotConfigured("recent_start is required: SAR evidence compares a baseline and a recent window")
    bbox = footprint_wgs84(req.grid).bounds
    items = pc.search(collection, bbox, req.start, req.end, limit=100)[:max_items]
    report = {"endpoint": pc.stac_url, "collection": collection, "n_found": len(items), "excluded": {}}
    compat = []
    for it in items:
        ok, why, meta = check_s1_item(it)
        if ok:
            it["_meta"] = meta
            compat.append(it)
        else:
            report["excluded"][why] = report["excluded"].get(why, 0) + 1
    report["n_compatible"] = len(compat)
    sel, counts = select_orbit_group(compat, req.recent_start, p)
    report["orbit_group_counts_base_recent"] = counts
    if sel is None:
        raise NotConfigured(f"insufficient compatible Sentinel-1 scenes per orbit group (need >= {p.min_baseline_scenes} baseline and "
                            f">= {p.min_recent_scenes} recent in ONE relative orbit); found {counts}")
    group, chosen = sel
    report["orbit_group"] = {"orbit_state": group[0], "relative_orbit": group[1]}
    cached = {s.scene_id: s for s in load_cached_scenes(cache_dir, req.grid) if "vv_lin" in s.bands}
    scenes, failed = [], []
    for it in chosen:
        sid = it["id"]
        if sid in cached:
            scenes.append(cached[sid])
            continue
        try:
            href = pc.sign(it["assets"]["vv"]["href"], collection)
            vv = reader(href, req.grid, "bilinear")
            sc = Scene(sid, "sentinel-1", parse_dt(it["properties"]["datetime"]), {"vv_lin": np.asarray(vv, "float32")},
                       "Copernicus Sentinel-1 RTC via Planetary Computer", False, {**it["_meta"], "item_id": sid})
            save_scene(cache_dir, sc, req.grid)
            scenes.append(sc)
        except Exception as exc:
            failed.append(f"{sid}: {type(exc).__name__}: {exc}")
    report["n_read"], report["n_failed"], report["failed"] = len(scenes), len(failed), failed[:5]
    if not scenes:
        raise NotConfigured(f"all {len(chosen)} selected Sentinel-1 scenes failed to read: {failed[:2]}")
    return scenes, report


# ---------------------------------------------------------------------------------------------- analysis
def analyze_sar(scenes: list[Scene], req: EvidenceRequest, p: SarParams, extra_provenance: dict | None = None):
    """Per-pixel water-loss evidence. Returns EvidenceLayer or Unavailable (never a silent negative)."""
    prov = {"dataset": "Sentinel-1 VV backscatter", "params": p.to_dict(), "processing": [
        "terrain-corrected input (RTC) when mode=live", f"nan-aware {p.speckle_px}x{p.speckle_px} boxcar in linear power",
        "10*log10 -> dB, QC to [-40, 5] dB", f"water = VV < {p.water_db} dB", "baseline vs recent water frequency within one orbit group"],
        **(extra_provenance or {})}
    if req.recent_start is None:
        return Unavailable("sentinel1", "recent_start not provided; baseline/recent comparison impossible", "unavailable", prov)
    aoi, H, W = req.aoi_mask.astype(bool), req.grid.height, req.grid.width
    stack = []
    for s in sorted(scenes, key=lambda x: x.acquired):
        db = to_db(s.bands["vv_lin"], p.speckle_px) if "vv_lin" in s.bands else qc_db(s.bands["vv_db"])
        if db.shape != (H, W):
            continue
        vf = float(np.isfinite(db)[aoi].mean()) if aoi.any() else 0.0
        if vf >= p.min_scene_valid:
            stack.append((s, db))
    prov["n_scenes_input"], prov["n_scenes_usable"] = len(scenes), len(stack)
    legacy = any(s.meta.get("legacy_cache") for s, _ in stack)
    groups = {(s.meta.get("orbit_state"), s.meta.get("relative_orbit")) for s, _ in stack}
    if len(groups) > 1:
        return Unavailable("sentinel1", f"scenes from {len(groups)} different orbit groups; refusing to compare mixed geometries",
                           "insufficient", prov)
    base = [(s, d) for s, d in stack if s.acquired < req.recent_start]
    rec = [(s, d) for s, d in stack if s.acquired >= req.recent_start]
    prov.update(mode="cache_only_legacy" if legacy else "scenes", n_baseline=len(base), n_recent=len(rec),
                scene_ids=[s.scene_id for s, _ in stack][:50], first=stack[0][0].acquired.isoformat() if stack else None,
                last=stack[-1][0].acquired.isoformat() if stack else None,
                orbit={"orbit_state": next(iter(groups))[0], "relative_orbit": next(iter(groups))[1]} if groups else None)
    if legacy:
        prov["label"] = "CACHE ONLY: legacy cached scenes without orbit metadata; orbit comparability NOT verified"
    if len(base) < p.min_baseline_scenes or len(rec) < p.min_recent_scenes:
        return Unavailable("sentinel1", f"{len(base)} baseline / {len(rec)} recent usable scenes (need >= {p.min_baseline_scenes} / "
                           f">= {p.min_recent_scenes})", "insufficient", prov)
    if (rec[-1][0].acquired - rec[0][0].acquired).days < p.min_span_days:
        return Unavailable("sentinel1", f"recent scenes span < {p.min_span_days} days", "insufficient", prov)

    def freq(items):
        n = np.zeros((H, W), np.int16); w = np.zeros((H, W), np.int16)
        for _, d in items:
            v = np.isfinite(d)
            n += v; w += v & (d < p.water_db)
        return n, np.where(n > 0, w / np.maximum(n, 1), np.nan).astype("float32")
    bn, bf = freq(base); rn, rf = freq(rec)
    observed = (bn >= p.min_pixel_obs) & (rn >= p.min_pixel_obs) & aoi
    loss = observed & (bf >= p.base_water_freq) & (rf <= p.recent_water_freq_max)
    enc = np.where(observed, loss.astype("uint8"), NODATA).astype("uint8")
    prov["observed_fraction_aoi"] = round(float(observed.sum() / max(aoi.sum(), 1)), 4)
    if observed.sum() == 0:
        return Unavailable("sentinel1", "no pixel has enough valid SAR observations in both windows", "insufficient", prov)
    return EvidenceLayer("sentinel1", "raster", req.grid, {"sar_water_loss": enc, "sar_base_water_freq": bf, "sar_rec_water_freq": rf},
                         observed, prov)


class Sentinel1Source(EvidenceSource):
    key, label = "sentinel1", "Sentinel-1 SAR (VV backscatter, RTC)"
    dataset = "Planetary Computer STAC 'sentinel-1-rtc' (terrain-corrected gamma0); legacy cache fallback"
    role = "Independent, cloud-free test of whether candidate pixels stopped being water (SAR water frequency baseline vs recent)."
    requirements = "Network access to the Planetary Computer STAC/SAS endpoints (anonymous). Coverage of the AOI by the collection is NOT assumed."

    def __init__(self, provider=None, cache_key: str | None = None, pc: PlanetaryComputer | None = None,
                 collection: str = "sentinel-1-rtc", params: SarParams | None = None, cache_dir: Path | None = None, reader=read_to_grid):
        self.provider, self.cache_key, self.pc, self.collection = provider, cache_key, pc, collection
        self.params, self.cache_dir, self.reader = params or SarParams(), cache_dir, reader

    def _dir(self) -> Path | None:
        if self.cache_dir:
            return self.cache_dir
        if self.provider is not None and self.cache_key:
            return self.provider._cache_dir(self.cache_key, "sentinel-1")
        return None

    @property
    def status(self) -> str:  # type: ignore[override]
        return UNTESTED_REMOTE if self.pc is not None else (
            CACHE_ONLY if (self._dir() and self._dir().exists() and any(self._dir().glob("*.npz"))) else UNTESTED_REMOTE)

    def fetch(self, req: EvidenceRequest):
        d = self._dir()
        extra = {"endpoint": self.pc.stac_url if self.pc else None, "collection": self.collection}
        try:
            if self.pc is not None:
                cd = d or Path("data/cache") / f"{req.aoi_id}_s1"
                scenes, report = acquire_scenes(self.pc, self.collection, req, self.params, cd, self.reader)
                return analyze_sar(scenes, req, self.params, {**extra, "acquisition": report})
        except (NotConfigured, RemoteError) as exc:
            live_reason = f"live acquisition unavailable: {exc}"
        else:
            live_reason = "live acquisition not configured"
        scenes = load_cached_scenes(d, req.grid) if d else []        # clearly-labelled cache-only fallback
        if scenes:
            return analyze_sar([s for s in scenes if req.start <= s.acquired <= req.end], req, self.params,
                               {**extra, "live_status": live_reason, "fallback": "cache_only"})
        return Unavailable(self.key, f"{live_reason}; no cached scenes on this grid", "unavailable", extra)
