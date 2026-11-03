"""Settings, encroachment parameters and seasonal (wet-season) profiles."""
from __future__ import annotations
import dataclasses
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def utc(y: int, m: int, d: int, hh: int = 0) -> datetime:
    return datetime(y, m, d, hh, tzinfo=timezone.utc)


def _jsonable(d: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in d.items()}


@dataclass(frozen=True)
class Settings:
    """Runtime settings. Everything secret/credentialed comes from the environment, never from source."""
    mode: str = "sample"
    db_path: Path = ROOT / "data" / "changesentinel.db"
    output_dir: Path = ROOT / "outputs"
    registry_dir: Path = ROOT / "data" / "registry"
    gee_project: str = ""            # Earth Engine cloud project id (Dynamic World)
    gee_service_account: str = ""    # optional service-account e-mail; the key FILE PATH below is read at run time, never stored
    gee_key_file: str = ""
    pc_stac_url: str = "https://planetarycomputer.microsoft.com/api/stac/v1"   # Sentinel-1 RTC + JRC GSW (public; anonymous SAS token)
    pc_sas_url: str = "https://planetarycomputer.microsoft.com/api/sas/v1/token"
    pc_subscription_key: str = ""    # optional (higher rate limits); env only
    s1_collection: str = "sentinel-1-rtc"
    jrc_collection: str = "jrc-gsw"
    http_timeout_s: float = 30.0

    def __post_init__(self) -> None:
        if self.mode not in ("sample", "live"):
            raise ValueError(f"CS_MODE must be 'sample' or 'live', got {self.mode!r}")

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ
        return cls(
            mode=e.get("CS_MODE", "sample").strip().lower(),
            db_path=Path(e.get("CS_DB_PATH", ROOT / "data" / "changesentinel.db")),
            output_dir=Path(e.get("CS_OUTPUT_DIR", ROOT / "outputs")),
            registry_dir=Path(e.get("CS_REGISTRY_DIR", ROOT / "data" / "registry")),
            gee_project=e.get("CS_GEE_PROJECT", ""),
            gee_service_account=e.get("CS_GEE_SERVICE_ACCOUNT", ""),
            gee_key_file=e.get("CS_GEE_KEY_FILE", ""),
            pc_stac_url=e.get("CS_PC_STAC_URL", cls.pc_stac_url),
            pc_sas_url=e.get("CS_PC_SAS_URL", cls.pc_sas_url),
            pc_subscription_key=e.get("CS_PC_SUBSCRIPTION_KEY", ""),
            s1_collection=e.get("CS_S1_COLLECTION", cls.s1_collection),
            jrc_collection=e.get("CS_JRC_COLLECTION", cls.jrc_collection),
            http_timeout_s=float(e.get("CS_HTTP_TIMEOUT_S", cls.http_timeout_s)),
        )


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
    require_independent_support: bool = False   # True: a detection with no supporting independent source is capped at 50 (never HIGH)

    def to_dict(self) -> dict:
        return _jsonable(asdict(self))


@dataclass(frozen=True)
class SeasonalProfile:
    """Wet-season definition used by the seasonality safeguard. One profile per climatic regime,
    referenced BY NAME from each study area / AOI (never inferred from a place name in code)."""
    key: str
    label: str
    wet_months: tuple
    basis: str
    validated: bool = False


SEASONAL_PROFILES = {
    "sw_monsoon": SeasonalProfile(
        "sw_monsoon", "South-west monsoon regime", (6, 7, 8, 9, 10),
        "ASSUMED regime default (Jun-Oct wet). Not yet validated against local rainfall records or JRC monthly history."),
    "ne_monsoon": SeasonalProfile(
        "ne_monsoon", "North-east monsoon regime", (10, 11, 12),
        "ASSUMED regime default (Oct-Dec wet). Not yet validated against local rainfall records or JRC monthly history."),
}


class UnknownStudyArea(KeyError):
    """Raised instead of silently falling back to default parameters/grids for an unknown area."""


def seasonal_profile(key: str) -> SeasonalProfile:
    try:
        return SEASONAL_PROFILES[key]
    except KeyError:
        raise UnknownStudyArea(f"Unknown seasonal profile {key!r}; known: {sorted(SEASONAL_PROFILES)}") from None


def encroach_params_for(study_area: str | None = None, profile: str | None = None,
                        base: EncroachParams | None = None, as_of: datetime | None = None,
                        recent_months: int | None = None) -> EncroachParams:
    """EncroachParams for a study area (or an explicit seasonal profile key).
    - Wet months always come from the area's declared seasonal profile.
    - Unknown areas/profiles raise (no silent SW-monsoon default).
    - `base` lets callers layer UI overrides ON TOP of the area-specific values.
    - `as_of` + `recent_months` set a rolling recent window (>= 12 months so one full wet season is covered)."""
    if profile is None:
        if study_area is None:
            raise UnknownStudyArea("A study area or seasonal profile key is required")
        from .study_areas import STUDY_AREAS
        if study_area not in STUDY_AREAS:
            raise UnknownStudyArea(f"Unknown study area {study_area!r}; known: {sorted(STUDY_AREAS)}")
        profile = STUDY_AREAS[study_area]["seasonal_profile"]
    prof = seasonal_profile(profile)
    p = base if base is not None else EncroachParams()
    p = dataclasses.replace(p, wet_months=prof.wet_months)
    if study_area is not None and base is None:
        over = STUDY_AREAS_PARAM_OVERRIDES.get(study_area, {})
        p = dataclasses.replace(p, **over)
    if as_of is not None and recent_months is not None:
        if recent_months < 12:
            raise ValueError("recent window must be >= 12 months to include a full wet season")
        p = dataclasses.replace(p, recent_start=as_of - timedelta(days=round(recent_months * 30.4375)))
    return p


# Study-area-specific starting values (not calibrated). Keyed by study-area key, applied only when no base is given.
STUDY_AREAS_PARAM_OVERRIDES = {
    "pallikaranai_marsh": {"recent_start": utc(2024, 1, 1), "area_cap_ha": 5.0},
}


# Convenience constant for the legacy Chennai validation grid (kept for existing callers/tests).
PALLIKARANAI_ENCROACH_PARAMS = encroach_params_for("pallikaranai_marsh")
