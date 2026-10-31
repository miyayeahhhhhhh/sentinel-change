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
    """Real satellite imagery provider using freely accessible Copernicus Sentinel data.
    
    Accesses Sentinel-2 L2A via open AWS Earth Search STAC (no credentials required)
    and caches downloaded sub-grids locally in data/cache/{study_area}/ for fast,
    repeatable, offline-capable analysis.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def _cache_dir(self, study_area: str, sensor: str = "sentinel-2") -> Path:
        suffix = "" if sensor == "sentinel-2" else "_s1"
        from .config import ROOT
        bundled = ROOT / "data" / "cache" / f"{study_area}{suffix}"
        if bundled.exists() and list(bundled.glob("*.npz")):
            return bundled
        return self.settings.db_path.parent / "cache" / f"{study_area}{suffix}"

    def _load_cached(self, cache_dir: Path, sensor: str = "sentinel-2") -> list[Scene]:
        import numpy as np
        scenes = []
        for p in sorted(cache_dir.glob("*.npz")):
            try:
                d = np.load(p)
                dt_str = str(d["dt"])
                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                if sensor == "sentinel-2":
                    bands = {
                        "green": d["green"].astype(np.float32) / 10000.0,
                        "swir1": d["swir1"].astype(np.float32) / 10000.0,
                        "scl": d["scl"].astype(np.uint8),
                    }
                    meta = {"cloud_cover": float(d.get("cloud", 0.0)), "tile": str(d.get("tile", "44PMV"))}
                else:
                    bands = {"vv_db": d["vv_db"].astype(np.float32)}
                    meta = {"orbit": str(d.get("orbit", "IW"))}
                scenes.append(Scene(
                    scene_id=str(d.get("sid", p.stem)),
                    sensor=sensor,
                    acquired=dt,
                    bands=bands,
                    source="Copernicus Sentinel-2 L2A (ESA / AWS Open Data)" if sensor == "sentinel-2" else "Copernicus Sentinel-1 GRD (ESA)",
                    synthetic=False,
                    meta=meta,
                ))
            except Exception as exc:
                continue
        return scenes

    def s2_scenes(self, scenario: str = "", study_area: str = "pallikaranai_marsh") -> tuple[Grid, list[Scene]]:
        from .sample_data import make_grid
        grid = make_grid(study_area)
        cache_dir = self._cache_dir(study_area, "sentinel-2")

        if cache_dir.exists() and list(cache_dir.glob("*.npz")):
            scenes = self._load_cached(cache_dir, "sentinel-2")
            if scenes:
                return grid, scenes

        # If not cached, attempt live discovery via open Earth Search STAC
        try:
            scenes = self._fetch_s2_stac(grid, study_area, cache_dir)
            if scenes:
                return grid, scenes
        except Exception as exc:
            raise NotConfigured(
                f"Live Sentinel-2 acquisition for '{study_area}' failed: {exc}. "
                f"Ensure internet connectivity or run with CS_MODE=sample."
            ) from exc

        raise NotConfigured(f"No usable Sentinel-2 observations found for study area '{study_area}'.")

    def _fetch_s2_stac(self, grid: Grid, study_area: str, cache_dir: Path) -> list[Scene]:
        import json
        import urllib.request
        import numpy as np
        import rasterio
        from rasterio.windows import from_bounds

        cache_dir.mkdir(parents=True, exist_ok=True)
        w, s, e, n = grid.bounds_wgs84()
        body = {
            "collections": ["sentinel-2-l2a"],
            "bbox": [round(w, 4), round(s, 4), round(e, 4), round(n, 4)],
            "datetime": "2023-01-01T00:00:00Z/2024-12-31T23:59:59Z",
            "query": {"eo:cloud_cover": {"lt": 15}},
            "limit": 20,
        }
        req = urllib.request.Request(
            "https://earth-search.aws.element84.com/v1/search",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        features = data.get("features", [])
        if not features:
            return []

        b = grid.bounds
        with rasterio.Env(AWS_NO_SIGN_REQUEST="YES"):
            for f in features[:10]:
                sid = f["id"]
                target = cache_dir / f"{sid}.npz"
                if target.exists():
                    continue
                g_url = f["assets"]["green"]["href"]
                s_url = f["assets"]["swir16"]["href"]
                scl_url = f["assets"]["scl"]["href"]
                dt = f["properties"]["datetime"]
                cloud = f["properties"].get("eo:cloud_cover", 0.0)

                with rasterio.open(g_url) as src:
                    g = src.read(1, window=from_bounds(*b, transform=src.transform), out_shape=(grid.height, grid.width))
                with rasterio.open(s_url) as src:
                    swir = src.read(1, window=from_bounds(*b, transform=src.transform), out_shape=(grid.height, grid.width), resampling=rasterio.enums.Resampling.bilinear)
                with rasterio.open(scl_url) as src:
                    scl = src.read(1, window=from_bounds(*b, transform=src.transform), out_shape=(grid.height, grid.width), resampling=rasterio.enums.Resampling.nearest)

                np.savez_compressed(target, green=g, swir1=swir, scl=scl, dt=dt, cloud=cloud, sid=sid, tile="44PMV")

        return self._load_cached(cache_dir, "sentinel-2")

    def flood_scenes(self, scenario: str = "", study_area: str = "solapur_bhima") -> tuple[Grid, list[Scene]]:
        from .sample_data import make_grid
        grid = make_grid(study_area)
        cache_dir = self._cache_dir(study_area, "sentinel-1")
        if cache_dir.exists() and list(cache_dir.glob("*.npz")):
            scenes = self._load_cached(cache_dir, "sentinel-1")
            if scenes:
                return grid, scenes
        raise NotConfigured(
            f"Live Sentinel-1 SAR acquisition for '{study_area}' requires calibrated GRD terrain correction "
            f"or pre-cached SAR observations in data/cache/{study_area}_s1/. "
            f"Flood mapping is designated as secondary; use CS_MODE=sample for tested scenarios."
        )


def get_provider(settings: Settings):
    return SampleProvider() if settings.mode == "sample" else LiveProvider(settings)
