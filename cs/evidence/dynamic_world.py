"""Dynamic World (GOOGLE/DYNAMICWORLD/V1) land-cover probabilities -> water -> built/bare transition evidence.

Dynamic World is distributed through Google Earth Engine only, so this needs `earthengine-api` plus Earth Engine
credentials (CS_GEE_PROJECT and either prior `earthengine authenticate` or a service account via CS_GEE_SERVICE_ACCOUNT +
CS_GEE_KEY_FILE). Without them the source reports `blocked_credentials`. The Earth Engine client is implemented but has
NEVER been run against the real service; analysis logic is tested with a labelled local stand-in client.

Class probabilities are kept (mean probability composites per window + per-pixel image counts); the hard transition
codes are derived from them with explicit thresholds and the probabilities are exported alongside.
"""
from __future__ import annotations
import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np

from ..config import Settings
from ..geo import Grid, footprint_wgs84
from .base import BLOCKED_CREDENTIALS, NODATA, UNTESTED_REMOTE, EvidenceLayer, EvidenceRequest, EvidenceSource, NotConfigured, Unavailable

DW_CLASSES = {0: "water", 1: "trees", 2: "grass", 3: "flooded_vegetation", 4: "crops",
              5: "shrub_and_scrub", 6: "built", 7: "bare", 8: "snow_and_ice"}
DW_BANDS = [DW_CLASSES[i] for i in range(9)]
DW_ENCROACHMENT_CLASSES = (6, 7)
T_NONE, T_BUILT, T_BARE, T_OTHER = 0, 1, 2, 3        # transition codes in the encoded raster


@dataclass(frozen=True)
class DwParams:
    """Heuristic starting values, not calibrated."""
    was_water: float = 0.5        # mean water probability in the baseline window
    now_not_water: float = 0.3    # mean water probability in the recent window
    min_images: int = 3           # images per window
    min_pixel_obs: int = 2        # images per pixel per window
    dest_min: float = 0.3         # destination class probability needed to call built/bare

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DWComposite:
    probs: dict                   # band name -> float32 (H, W) mean probability (NaN where unobserved)
    n_obs: np.ndarray             # per-pixel image count
    n_images: int
    image_ids: list
    window: tuple                 # (start, end) requested
    first: str | None = None
    last: str | None = None
    meta: dict = field(default_factory=dict)


class EarthEngineDWClient:
    """Real client. Requires earthengine-api and credentials; never exercised against the live service in this repo."""

    def __init__(self, project: str, service_account: str = "", key_file: str = "", max_download_bytes: int = 30_000_000):
        self.project, self.service_account, self.key_file, self.max_bytes = project, service_account, key_file, max_download_bytes   # per-tile cap
        self._ee_mod = None

    def _ee(self):
        if self._ee_mod is not None:
            return self._ee_mod
        try:
            import ee
        except ImportError as exc:
            raise NotConfigured("earthengine-api is not installed (pip install earthengine-api)") from exc
        if not self.project:
            raise NotConfigured("CS_GEE_PROJECT is not set")
        try:
            if self.service_account and self.key_file:
                ee.Initialize(ee.ServiceAccountCredentials(self.service_account, self.key_file), project=self.project)
            else:
                ee.Initialize(project=self.project)      # stored `earthengine authenticate` credentials
        except Exception as exc:
            raise NotConfigured(f"Earth Engine initialisation failed ({type(exc).__name__}): {exc}") from exc
        self._ee_mod = ee
        return ee

    def available(self) -> tuple[bool, str]:
        try:
            self._ee()
            return True, ""
        except NotConfigured as exc:
            return False, str(exc)

    def composite(self, grid: Grid, start: datetime, end: datetime) -> DWComposite:
        ee = self._ee()
        region = ee.Geometry.Polygon([list(footprint_wgs84(grid).exterior.coords)])
        coll = (ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1").filterBounds(region)
                .filterDate(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")))
        n_images = int(coll.size().getInfo())
        if n_images == 0:
            raise NotConfigured(f"no Dynamic World images between {start:%Y-%m-%d} and {end:%Y-%m-%d} over the AOI")
        ids = sorted(coll.aggregate_array("system:index").getInfo())
        img = coll.select(DW_BANDS).mean().addBands(coll.select("water").count().rename("n_obs"))
        full = np.full((len(DW_BANDS) + 1, grid.height, grid.width), np.nan, np.float32)
        for r0, c0, sub in split_grid(grid, self.max_bytes // ((len(DW_BANDS) + 1) * 4)):    # one download per tile
            full[:, r0:r0 + sub.height, c0:c0 + sub.width] = self._download(ee, img, sub)
        n_obs = np.nan_to_num(full[-1], nan=0.0).astype("int16")
        probs = {b: np.where(n_obs > 0, full[i], np.nan).astype("float32") for i, b in enumerate(DW_BANDS)}
        return DWComposite(probs, n_obs, n_images, ids, (start, end), ids[0] if ids else None, ids[-1] if ids else None,
                           {"image_ids_sha256": hashlib.sha256("\n".join(ids).encode()).hexdigest()})

    def _download(self, ee, img, sub: Grid) -> np.ndarray:
        import urllib.request
        from rasterio.io import MemoryFile
        from ..geo import read_dataset_to_grid
        region = ee.Geometry.Polygon([list(footprint_wgs84(sub).exterior.coords)])
        url = img.getDownloadURL({"region": region, "crs": f"EPSG:{sub.crs.to_epsg()}", "crs_transform": list(sub.transform)[:6],
                                  "format": "GEO_TIFF"})
        with urllib.request.urlopen(url, timeout=120) as r, MemoryFile(r.read()) as mf, mf.open() as src:
            stack = read_dataset_to_grid(src, sub, "nearest", band=None)
        if stack.shape[0] != len(DW_BANDS) + 1:
            raise NotConfigured(f"unexpected Earth Engine band count {stack.shape[0]}")
        return stack.astype("float32")


def split_grid(grid: Grid, max_pixels: int):
    """Yield (row0, col0, sub-grid) tiles of at most `max_pixels` pixels, so a large AOI becomes several downloads instead of a
    silent failure. Sub-grid transforms use explicit Affine fields (no Affine @ Affine)."""
    from affine import Affine
    side = max(16, int(max_pixels ** 0.5))
    t = grid.transform
    for r0 in range(0, grid.height, side):
        for c0 in range(0, grid.width, side):
            h, w = min(side, grid.height - r0), min(side, grid.width - c0)
            yield r0, c0, Grid(Affine(t.a, t.b, t.c + c0 * t.a + r0 * t.b, t.d, t.e, t.f + c0 * t.d + r0 * t.e), grid.crs, h, w)


class CachedDWClient:
    """Wraps a client so a re-run for the same AOI grid + window + band set does not download again (results only; the
    cache holds real downloaded composites and is keyed on everything that changes them)."""

    def __init__(self, inner, cache_dir, force_refresh: bool = False):
        self.inner, self.cache_dir, self.force_refresh = inner, cache_dir, force_refresh

    def available(self):
        return self.inner.available()

    def _key(self, grid, start, end) -> str:
        s = f"{grid.crs.to_epsg()}|{[round(float(v), 6) for v in list(grid.transform)[:6]]}|{grid.height}x{grid.width}|{start:%Y-%m-%d}|{end:%Y-%m-%d}|{','.join(DW_BANDS)}"
        return hashlib.sha1(s.encode()).hexdigest()[:16]

    def composite(self, grid: Grid, start: datetime, end: datetime) -> DWComposite:
        import json
        from pathlib import Path
        f = Path(self.cache_dir) / f"dw_{self._key(grid, start, end)}.npz"
        if f.exists() and not self.force_refresh:
            try:
                d = np.load(f, allow_pickle=False)
                meta = json.loads(str(d["meta"]))
                return DWComposite({b: d[b] for b in DW_BANDS}, d["n_obs"], meta["n_images"], meta["ids"], (start, end),
                                   meta["ids"][0] if meta["ids"] else None, meta["ids"][-1] if meta["ids"] else None,
                                   {**meta["meta"], "from_cache": True})
            except Exception:
                pass                                                       # unreadable cache -> download again
        c = self.inner.composite(grid, start, end)
        f.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(f, n_obs=c.n_obs, meta=json.dumps({"n_images": c.n_images, "ids": c.image_ids, "meta": c.meta}),
                            **{b: c.probs[b] for b in DW_BANDS})
        return c


def classify_transitions(base: DWComposite, rec: DWComposite, aoi: np.ndarray, p: DwParams) -> tuple[np.ndarray, dict]:
    """Encoded transition raster (0 none, 1 water->built, 2 water->bare, 3 water->other; 255 unobserved) + probability stats."""
    obs = (base.n_obs >= p.min_pixel_obs) & (rec.n_obs >= p.min_pixel_obs) & aoi.astype(bool)
    w0, w1 = base.probs["water"], rec.probs["water"]
    lost = obs & (w0 >= p.was_water) & (w1 < p.now_not_water)
    veg = sum(np.nan_to_num(rec.probs[k]) for k in ("trees", "grass", "flooded_vegetation", "crops", "shrub_and_scrub"))
    dest = np.stack([np.nan_to_num(rec.probs["built"]), np.nan_to_num(rec.probs["bare"]), veg])   # built, bare, other
    arg = dest.argmax(0)
    top = dest.max(0)
    code = np.where(lost, np.where((arg == 0) & (top >= p.dest_min), T_BUILT, np.where((arg == 1) & (top >= p.dest_min), T_BARE, T_OTHER)), T_NONE)
    enc = np.where(obs, code, NODATA).astype("uint8")
    stats = {"observed_px": int(obs.sum()), "water_to_built_px": int((enc == T_BUILT).sum()), "water_to_bare_px": int((enc == T_BARE).sum()),
             "water_to_other_px": int((enc == T_OTHER).sum())}
    return enc, stats


class DynamicWorldSource(EvidenceSource):
    key, label = "dynamic_world", "Dynamic World land cover (probabilities)"
    dataset = "GOOGLE/DYNAMICWORLD/V1 via Google Earth Engine"
    role = "Land-cover probability change inside the candidate loss: water -> built / bare ground, with class probabilities preserved."
    requirements = "earthengine-api + Earth Engine access (CS_GEE_PROJECT; stored auth or CS_GEE_SERVICE_ACCOUNT + CS_GEE_KEY_FILE)."

    def __init__(self, client=None, params: DwParams | None = None, settings: Settings | None = None,
                 refresh: bool = False):
        self.params = params or DwParams()
        if client is None and settings is not None:
            client = CachedDWClient(EarthEngineDWClient(settings.gee_project, settings.gee_service_account, settings.gee_key_file),
                                    settings.db_path.parent / "cache" / "dynamic_world", force_refresh=refresh)
        self.client = client

    @property
    def status(self) -> str:  # type: ignore[override]
        if self.client is None:
            return BLOCKED_CREDENTIALS
        if hasattr(self.client, "available") and not self.client.available()[0]:
            return BLOCKED_CREDENTIALS
        return UNTESTED_REMOTE

    def fetch(self, req: EvidenceRequest):
        prov = {"dataset": self.dataset, "params": self.params.to_dict()}
        if self.client is None or (hasattr(self.client, "available") and not self.client.available()[0]):
            why = self.client.available()[1] if self.client is not None else "no Earth Engine client configured"
            return Unavailable(self.key, f"Dynamic World unavailable: {why}", "unavailable", prov)
        if req.recent_start is None:
            return Unavailable(self.key, "recent_start missing; baseline/recent windows undefined", "unavailable", prov)
        if not req.start < req.recent_start <= req.end:
            return Unavailable(self.key, "baseline and recent windows are not ordered/non-overlapping", "unavailable", prov)
        try:
            base = self.client.composite(req.grid, req.start, req.recent_start)
            rec = self.client.composite(req.grid, req.recent_start, req.end)
        except NotConfigured as exc:
            return Unavailable(self.key, f"Dynamic World unavailable: {exc}", "unavailable", prov)
        except Exception as exc:
            return Unavailable(self.key, f"Dynamic World acquisition failed: {type(exc).__name__}: {exc}", "unavailable", prov)
        prov.update(baseline={"n_images": base.n_images, "first": base.first, "last": base.last, **base.meta},
                    recent={"n_images": rec.n_images, "first": rec.first, "last": rec.last, **rec.meta},
                    windows={"baseline": [req.start.isoformat(), req.recent_start.isoformat()], "recent": [req.recent_start.isoformat(), req.end.isoformat()]},
                    composite="per-pixel mean of class probabilities over images in each window")
        if base.n_images < self.params.min_images or rec.n_images < self.params.min_images:
            return Unavailable(self.key, f"{base.n_images} baseline / {rec.n_images} recent Dynamic World images (need >= {self.params.min_images} each)",
                               "insufficient", prov)
        enc, stats = classify_transitions(base, rec, req.aoi_mask, self.params)
        prov["stats"] = stats
        if stats["observed_px"] == 0:
            return Unavailable(self.key, "no pixel has enough Dynamic World observations in both windows", "insufficient", prov)
        arrays = {"dw_transition": enc, "dw_water_prob_base": base.probs["water"], "dw_water_prob_rec": rec.probs["water"],
                  "dw_built_prob_rec": rec.probs["built"], "dw_bare_prob_rec": rec.probs["bare"],
                  "dw_n_obs_base": base.n_obs, "dw_n_obs_rec": rec.n_obs}
        return EvidenceLayer(self.key, "raster", req.grid, arrays, enc != NODATA, prov)
