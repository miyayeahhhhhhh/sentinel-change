"""Imagery discovery/acquisition adapters.

SampleProvider: deterministic SYNTHETIC Sentinel-2 fixtures (tests/offline development only).
LiveProvider: real Sentinel-2 L2A from the open Earth Search STAC (no credentials) with a local npz cache; every raster
is warped onto the analysis grid with `geo.read_to_grid` (correct across UTM zones). Sentinel-1 is cache-only.
"""
from __future__ import annotations
import concurrent.futures
import json
import logging
import re
import shutil
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .config import ROOT, Settings
from .geo import Grid, footprint_wgs84, read_to_grid

log = logging.getLogger(__name__)
STAC_SEARCH = "https://earth-search.aws.element84.com/v1/search"
S2_QUANT = 10000.0
S2_BOA_OFFSET_DN = -1000.0                                   # BOA_ADD_OFFSET for L2A processing baseline >= 04.00
S2_PB04_START = datetime(2022, 1, 25, tzinfo=timezone.utc)   # baseline 04.00 operational from this date
_CACHE_LOCK = threading.Lock()


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
    """Live imagery/evidence requested but not implemented, not cached, or credentials missing."""


def boa_offset_applies(acquired: datetime, processing_baseline: str | None = None) -> bool:
    """L2A products from processing baseline 04.00 (25 Jan 2022) carry a -1000 DN offset that MUST be removed
    before computing reflectance/band ratios (MNDWI). Use the recorded baseline when known, else the date rule."""
    if processing_baseline:
        m = re.match(r"\s*(\d+)", str(processing_baseline))
        if m:
            return int(m.group(1)) >= 4
    return acquired >= S2_PB04_START


def s2_reflectance(dn: np.ndarray, acquired: datetime, processing_baseline: str | None = None) -> np.ndarray:
    """Digital numbers -> surface reflectance. DN 0 (nodata) stays 0; negatives after the offset are clipped to 0."""
    dn = dn.astype(np.float32)
    off = S2_BOA_OFFSET_DN if boa_offset_applies(acquired, processing_baseline) else 0.0
    return np.where(dn > 0, np.clip((dn + off) / S2_QUANT, 0.0, None), 0.0).astype(np.float32)


class SampleProvider:
    """Deterministic synthetic fixtures. NOT satellite data."""

    def s2_scenes(self, scenario: str = "persistent_loss", study_area: str = "synthetic_test_grid") -> tuple[Grid, list[Scene]]:
        from .sample_data import s2_scenes
        return s2_scenes(scenario, study_area=study_area)

    def s1_scenes(self, study_area: str = "synthetic_test_grid") -> tuple[Grid, list[Scene]]:
        raise NotConfigured("No Sentinel-1 fixtures exist; SAR corroboration stays 'unassessed' in sample mode.")


class LiveProvider:
    """Real Sentinel-2 L2A via open Earth Search STAC, cached under data/cache/<key>/ (sentinel-1: <key>_s1/)."""

    def __init__(self, settings: Settings):
        self.settings = settings

    # ---- cache -------------------------------------------------------------------------------
    def _cache_dir(self, key: str, sensor: str = "sentinel-2") -> Path:
        suffix = "" if sensor == "sentinel-2" else "_s1"
        bundled = ROOT / "data" / "cache" / f"{key}{suffix}"
        if bundled.exists() and list(bundled.glob("*.npz")):
            return bundled
        return self.settings.db_path.parent / "cache" / f"{key}{suffix}"

    def _load_cached(self, cache_dir: Path, sensor: str = "sentinel-2", grid: Grid | None = None) -> list[Scene]:
        scenes = []
        for p in sorted(cache_dir.glob("*.npz")):
            try:
                d = np.load(p)
                dt = datetime.fromisoformat(str(d["dt"]).replace("Z", "+00:00"))
                sid = str(d["sid"]) if "sid" in d.files else p.stem
                if grid is not None and "transform" in d.files:           # cache written on a different grid -> never reuse
                    if (int(d["epsg"]) != grid.crs.to_epsg()
                            or not np.allclose(d["transform"], list(grid.transform)[:6])
                            or d["green"].shape != (grid.height, grid.width)):
                        log.warning("cache %s is on a different grid; skipped", p.name)
                        continue
                if sensor == "sentinel-2":
                    pb = str(d["pb"]) if "pb" in d.files else None
                    tile = str(d["tile"]) if "tile" in d.files else (re.search(r"_(\d{2}[A-Z]{3})_", sid) or [None, "unknown"])[1]
                    bands = {"green": s2_reflectance(d["green"], dt, pb), "swir1": s2_reflectance(d["swir1"], dt, pb),
                             "scl": d["scl"].astype(np.uint8)}
                    meta = {"cloud_cover": float(d["cloud"]) if "cloud" in d.files else None, "tile": tile,
                            "processing_baseline": pb, "boa_offset_applied": boa_offset_applies(dt, pb)}
                    source = "Copernicus Sentinel-2 L2A (ESA / AWS Open Data)"
                else:
                    bands = {"vv_db": d["vv_db"].astype(np.float32)}
                    meta = {"orbit": str(d["orbit"]) if "orbit" in d.files else "IW"}
                    source = "Copernicus Sentinel-1 GRD (ESA)"
                scenes.append(Scene(sid, sensor, dt, bands, source, False, meta))
            except Exception as exc:
                log.warning("skipping unreadable cache file %s: %s", p.name, exc)
        return scenes

    # ---- legacy fixed-grid study areas ---------------------------------------------------------
    def s2_scenes(self, scenario: str = "", study_area: str = "pallikaranai_marsh") -> tuple[Grid, list[Scene]]:
        from .study_areas import STUDY_AREAS, make_grid
        grid = make_grid(study_area)
        if STUDY_AREAS[study_area].get("synthetic"):
            raise NotConfigured(f"'{study_area}' is a synthetic fixture grid; it has no live imagery.")
        cache_dir = self._cache_dir(study_area, "sentinel-2")
        if list(cache_dir.glob("*.npz")):
            scenes = self._load_cached(cache_dir, "sentinel-2")
            if scenes:
                return grid, scenes
        raise NotConfigured(f"No cached Sentinel-2 observations for legacy study area '{study_area}'. "
                            "Register an AOI and use s2_scenes_for_grid for live acquisition.")

    def s1_scenes(self, study_area: str) -> tuple[Grid, list[Scene]]:
        from .study_areas import make_grid
        grid = make_grid(study_area)
        d = self._cache_dir(study_area, "sentinel-1")
        scenes = self._load_cached(d, "sentinel-1") if d.exists() else []
        if scenes:
            return grid, scenes
        raise NotConfigured(f"No cached Sentinel-1 observations for '{study_area}' (acquisition not implemented).")

    # ---- registry AOIs: any grid, any UTM zone ---------------------------------------------------
    @staticmethod
    def _grid_stamp(grid: Grid) -> dict:
        return {"epsg": grid.crs.to_epsg(), "transform": [round(float(v), 6) for v in list(grid.transform)[:6]],
                "shape": [grid.height, grid.width]}

    @staticmethod
    def _read_manifest(cache_dir: Path) -> dict | None:
        try:
            return json.loads((cache_dir / "manifest.json").read_text())
        except Exception:
            return None

    def _manifest_matches(self, m: dict | None, grid: Grid, start: datetime, end: datetime, max_cloud: float) -> bool:
        """A cache is complete only when a manifest records a finished fetch of THIS request with no failed items."""
        if not m or m.get("search_failed") or m.get("n_failed", 1) != 0:
            return False
        q = m.get("query", {})
        try:
            return (m.get("grid") == self._grid_stamp(grid) and float(q["max_cloud"]) == float(max_cloud)
                    and datetime.fromisoformat(q["start"]) <= start and datetime.fromisoformat(q["end"]) >= end)
        except (KeyError, ValueError):
            return False

    def s2_scenes_for_grid(self, grid: Grid, cache_key: str, start: datetime, end: datetime,
                           max_cloud: float = 20.0, limit: int = 100, refresh: bool = False,
                           progress_callback=None) -> list[Scene]:
        """Real Sentinel-2 L2A on `grid`. A cache is served without a network call only when its manifest matches this
        request and recorded no failures; otherwise STAC is queried again (items already cached are not re-read, failed
        items are retried). `refresh=True` forces a new query. `self.last_acquisition` records found/read/failed counts.
        Raises NotConfigured when nothing real can be obtained (never returns placeholders)."""
        cache_dir = self.settings.db_path.parent / "cache" / cache_key
        with _CACHE_LOCK:
            manifest = self._read_manifest(cache_dir) if cache_dir.exists() else None
            complete = (not refresh) and self._manifest_matches(manifest, grid, start, end, max_cloud)
            self.last_acquisition = None
            if not complete:
                try:
                    manifest = self._fetch_s2_stac(grid, cache_dir, start, end, max_cloud, limit, progress_callback=progress_callback)
                except Exception as exc:
                    self.last_acquisition = {"status": "failed", "error": str(exc)[:300]}
                    raise NotConfigured(f"Live Sentinel-2 acquisition for '{cache_key}' failed: {exc}") from exc
            scenes = [s for s in self._load_cached(cache_dir, "sentinel-2", grid) if start <= s.acquired <= end]
            scenes.sort(key=lambda scene: scene.acquired, reverse=True)
            m = manifest or {}
            self.last_acquisition = {
                "status": "complete" if (m.get("n_failed") == 0 and not m.get("search_failed")) else "partial",
                "from_cache_without_query": complete, "found": m.get("n_found"), "cloud_filtered": m.get("n_cloud_filtered"),
                "candidates": m.get("n_candidates"), "read_ok": m.get("n_cached"), "failed": m.get("n_failed"),
                "errors": (m.get("errors") or [])[:10], "scenes_in_window": len(scenes), "max_cloud": max_cloud,
                "fetched_at": m.get("fetched_at"), "query": m.get("query")}
            if not scenes:
                raise NotConfigured(f"No usable Sentinel-2 observations for '{cache_key}' between {start:%Y-%m-%d} and {end:%Y-%m-%d} "
                                    f"(found {m.get('n_found')}, cloud-filtered {m.get('n_cloud_filtered')}, failed {m.get('n_failed')}).")
            return scenes

    def _stac_search(self, bbox, start: datetime, end: datetime, max_cloud: float, limit: int) -> list[dict]:
        """All items in the window, following STAC 'next' links (cloud filtering is done client-side so it is countable)."""
        body = {"collections": ["sentinel-2-l2a"], "bbox": [round(v, 5) for v in bbox], "limit": min(limit, 100),
                "datetime": f"{start:%Y-%m-%dT%H:%M:%SZ}/{end:%Y-%m-%dT%H:%M:%SZ}"}
        out, url, payload, nxt_method, seen = [], STAC_SEARCH, body, "POST", set()
        while url:
            page_key = (url, nxt_method, json.dumps(payload, sort_keys=True) if nxt_method == "POST" else "")
            if page_key in seen:
                raise RuntimeError("STAC pagination repeated a page request; acquisition stopped to avoid silently omitting scenes.")
            seen.add(page_key)
            post = (nxt_method == "POST")
            req = urllib.request.Request(url, data=json.dumps(payload).encode() if post else None, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.settings.http_timeout_s) as resp:
                page = json.loads(resp.read().decode())
            out += page.get("features", [])
            nxt = next((l for l in page.get("links", []) if l.get("rel") == "next"), None)
            if not nxt:
                break
            url, nxt_method = nxt.get("href", url), nxt.get("method", "POST").upper()
            payload = {**payload, **(nxt.get("body") or {})} if nxt_method == "POST" else payload
        out.sort(key=lambda f: (f.get("properties") or {}).get("datetime") or "", reverse=True)
        return out

    def _fetch_s2_stac(self, grid: Grid, cache_dir: Path, start: datetime, end: datetime, max_cloud: float, limit: int,
                       progress_callback=None) -> dict:
        """Search + per-item read. One failing item is recorded and skipped; it never aborts the rest."""
        import rasterio
        cache_dir.mkdir(parents=True, exist_ok=True)
        m = {"query": {"bbox": [round(v, 5) for v in footprint_wgs84(grid).bounds], "start": start.isoformat(), "end": end.isoformat(),
                       "max_cloud": max_cloud, "collection": "sentinel-2-l2a", "endpoint": STAC_SEARCH},
             "grid": self._grid_stamp(grid), "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "n_found": 0, "n_cloud_filtered": 0, "n_candidates": 0, "n_cached": 0, "n_failed": 0, "errors": [], "search_failed": False}
        if progress_callback:
            progress_callback("Searching Copernicus Sentinel-2 STAC catalog...")
        try:
            features = self._stac_search(m["query"]["bbox"], start, end, max_cloud, limit)
        except Exception as exc:
            m.update(search_failed=True, errors=[{"id": "<search>", "error": str(exc)[:200]}])
            (cache_dir / "manifest.json").write_text(json.dumps(m, indent=1))
            raise
        m["n_found"] = len(features)
        cands = [f for f in features if (f["properties"].get("eo:cloud_cover") is None or f["properties"]["eo:cloud_cover"] < max_cloud)]
        m["n_cloud_filtered"], m["n_candidates"] = len(features) - len(cands), len(cands)

        def _fetch_one(f: dict) -> tuple[str, bool, str | None]:
            sid = f.get("id", "?")
            target = cache_dir / f"{sid}.npz"
            if target.exists():
                return sid, True, None
            props, a = f["properties"], f["assets"]
            tmp = cache_dir / f"{sid}.{uuid.uuid4().hex[:8]}.tmp.npz"
            try:
                with rasterio.Env(AWS_NO_SIGN_REQUEST="YES"):
                    green = read_to_grid(a["green"]["href"], grid, "bilinear")
                    swir = read_to_grid(a["swir16"]["href"], grid, "bilinear")
                    scl = read_to_grid(a["scl"]["href"], grid, "nearest")
                tile = str(props.get("grid:code", "")).replace("MGRS-", "") or (re.search(r"_(\d{2}[A-Z]{3})_", sid) or [None, "unknown"])[1]
                np.savez_compressed(tmp, green=np.rint(green).astype("uint16"), swir1=np.rint(swir).astype("uint16"),
                                    scl=scl.astype("uint8"), dt=props["datetime"], cloud=props.get("eo:cloud_cover", np.nan),
                                    sid=sid, tile=tile, pb=str(props.get("s2:processing_baseline", "")),
                                    epsg=grid.crs.to_epsg(), transform=np.array(list(grid.transform)[:6]))
                if target.exists():
                    tmp.unlink(missing_ok=True)
                else:
                    try:
                        tmp.replace(target)
                    except OSError:
                        if target.exists():
                            tmp.unlink(missing_ok=True)
                        else:
                            time.sleep(0.05)
                            try:
                                tmp.replace(target)
                            except OSError:
                                shutil.move(str(tmp), str(target))
                return sid, True, None
            except Exception as exc:
                tmp.unlink(missing_ok=True)
                log.warning("Sentinel-2 item %s failed: %s", sid, exc)
                return sid, False, f"{type(exc).__name__}: {exc}"[:200]

        for f in cands:
            if (cache_dir / f"{f.get('id', '?')}.npz").exists():
                m["n_cached"] += 1

        to_download = [f for f in cands if not (cache_dir / f"{f.get('id', '?')}.npz").exists()]
        if progress_callback:
            progress_callback(f"Cached {m['n_cached']}/{len(cands)} scenes. Fetching {len(to_download)} remaining...", m["n_cached"], len(cands))

        if to_download:
            workers = min(6, len(to_download))
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(_fetch_one, f): f for f in to_download}
                for fut in concurrent.futures.as_completed(futures):
                    sid, ok, err = fut.result()
                    if ok:
                        m["n_cached"] += 1
                    else:
                        m["n_failed"] += 1
                        m["errors"].append({"id": sid, "error": err})
                    if progress_callback:
                        progress_callback(f"Downloaded {m['n_cached']}/{len(cands)} scenes...", m["n_cached"], len(cands))

        (cache_dir / "manifest.json").write_text(json.dumps(m, indent=1))
        return m


def get_provider(settings: Settings):
    return SampleProvider() if settings.mode == "sample" else LiveProvider(settings)
