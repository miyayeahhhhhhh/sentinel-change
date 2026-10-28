"""Settings and per-pipeline parameter sets. Flood and encroachment parameters are separate classes."""
from __future__ import annotations
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def utc(y: int, m: int, d: int, hh: int = 0) -> datetime:
    return datetime(y, m, d, hh, tzinfo=timezone.utc)


def _jsonable(d: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in d.items()}


@dataclass(frozen=True)
class Settings:
    mode: str = "sample"
    db_path: Path = ROOT / "data" / "changesentinel.db"
    output_dir: Path = ROOT / "outputs"
    gee_project: str = ""
    cdse_user: str = ""
    cdse_password: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ
        return cls(
            mode=e.get("CS_MODE", "sample").split("#")[0].strip().lower(),
            db_path=Path(e.get("CS_DB_PATH", ROOT / "data" / "changesentinel.db")),
            output_dir=Path(e.get("CS_OUTPUT_DIR", ROOT / "outputs")),
            gee_project=e.get("CS_GEE_PROJECT", ""),
            cdse_user=e.get("CS_CDSE_USER", ""),
            cdse_password=e.get("CS_CDSE_PASSWORD", ""),
        )


@dataclass(frozen=True)
class FloodParams:
    water_db: float = -18.0          # VV below this = water-like
    diff_db: float = 3.0             # required drop vs reference
    min_pixels: int = 5              # minimum mapping unit (blueprint: >= ~5 px)
    open_iter: int = 1
    min_scene_coverage: float = 0.5  # scene usable if finite fraction >= this
    min_aoi_coverage: float = 0.6    # reference+event overlap needed to assess
    min_pre_scenes: int = 2
    min_event_scenes: int = 1
    min_area_ha: float = 0.5
    high_score: float = 60.0
    limited_evidence_floor: float = 0.6
    area_floor_ha: float = 0.5
    area_cap_ha: float = 50.0
    max_age_days: float = 30.0       # recency decays linearly to 0 over this
    event_start: datetime = utc(2025, 8, 1)

    def to_dict(self) -> dict:
        return _jsonable(asdict(self))


@dataclass(frozen=True)
class EncroachParams:
    mndwi_thr: float = 0.0
    min_scene_valid: float = 0.6     # scene usable if valid fraction over AOI >= this
    recent_start: datetime = utc(2025, 1, 1)
    min_baseline_obs: int = 4
    min_recent_obs: int = 4          # n_min in the persistence rule
    ref_freq: float = 0.25           # historical-footprint water frequency
    persist: float = 0.8             # r_min
    min_pixel_obs: int = 4
    min_wet_pixel_obs: int = 2
    wet_months: tuple = (6, 7, 8, 9, 10)
    min_wet_scenes: int = 2
    min_span_days: int = 90          # T_min
    min_pixels: int = 5
    open_iter: int = 1
    min_area_ha: float = 0.5
    bare_swir: float = 0.20          # mean SWIR1 above this => bare/built-like transition (E2)
    high_score: float = 60.0
    limited_evidence_floor: float = 0.6
    area_floor_ha: float = 0.5
    area_cap_ha: float = 10.0
    max_age_days: float = 30.0

    def to_dict(self) -> dict:
        return _jsonable(asdict(self))
