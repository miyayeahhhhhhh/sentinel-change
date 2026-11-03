"""JRC Global Surface Water: HISTORICAL reference (occurrence, seasonality) - not a live observation.

Source: Planetary Computer STAC collection `jrc-gsw` (anonymous SAS). The product has a fixed temporal coverage
(reported by the service on each item and recorded in provenance; v1.4 ends in 2021 per JRC documentation, which is NOT
assumed by code) and is updated rarely, so it can say what was historically permanent vs seasonal water but nothing
about the recent window. Implemented but NOT exercised against the live service.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass

import numpy as np

from ..geo import footprint_wgs84, read_to_grid
from .base import NODATA, UNTESTED_REMOTE, EvidenceLayer, EvidenceRequest, EvidenceSource, NotConfigured, Unavailable
from .stac import PlanetaryComputer, RemoteError

GSW_BANDS = ("occurrence", "change_abs", "change_norm", "seasonality", "recurrence", "transition", "max_extent")
J_NEVER, J_SEASONAL, J_PERMANENT = 0, 1, 2           # encoded class values (255 = no data)


@dataclass(frozen=True)
class JrcParams:
    perm_occurrence: float = 75.0     # % of valid months with water
    perm_months: int = 10             # months/year with water (seasonality)

    def to_dict(self) -> dict:
        return asdict(self)


def classify_history(occurrence: np.ndarray, seasonality: np.ndarray | None, p: JrcParams) -> np.ndarray:
    """0 never water, 1 seasonal, 2 permanent, 255 no data. GSW convention: 255 = no data in occurrence/seasonality."""
    occ = occurrence.astype("float32")
    valid = (occurrence != 255) & (occ >= 0) & (occ <= 100)
    seas_ok = seasonality is not None
    seas = seasonality.astype("float32") if seas_ok else np.zeros_like(occ)
    seas_valid = (seasonality != 255) if seas_ok else np.zeros(occ.shape, bool)
    perm = valid & (occ >= p.perm_occurrence) & np.where(seas_valid, seas >= p.perm_months, True)
    cls = np.where(valid, np.where(occ == 0, J_NEVER, np.where(perm, J_PERMANENT, J_SEASONAL)), NODATA)
    return cls.astype("uint8")


class JRCSurfaceWaterSource(EvidenceSource):
    key, label = "jrc_gsw", "JRC Global Surface Water (historical occurrence & seasonality)"
    status = UNTESTED_REMOTE
    dataset = "Planetary Computer STAC 'jrc-gsw' (JRC Global Surface Water)"
    role = "Historical reference: was this pixel permanent or seasonal water? Separates persistent change from normal seasonal variation."
    requirements = "Network access to Planetary Computer STAC/SAS (anonymous). Static product with fixed temporal coverage; not a live observation."

    def __init__(self, pc: PlanetaryComputer | None = None, collection: str = "jrc-gsw", params: JrcParams | None = None, reader=read_to_grid):
        self.pc, self.collection, self.params, self.reader = pc, collection, params or JrcParams(), reader

    def fetch(self, req: EvidenceRequest):
        prov = {"dataset": self.dataset, "collection": self.collection, "params": self.params.to_dict(),
                "temporal_role": "historical reference; not a live observation"}
        if self.pc is None:
            return Unavailable(self.key, "JRC GSW client not configured", "unavailable", prov)
        prov["endpoint"] = self.pc.stac_url
        try:
            items = self.pc.search(self.collection, footprint_wgs84(req.grid).bounds, limit=20)
            if not items:
                return Unavailable(self.key, "no JRC GSW items cover the AOI", "unavailable", prov)
            H, W = req.grid.height, req.grid.width
            occ, seas = np.full((H, W), NODATA, "uint8"), np.full((H, W), NODATA, "uint8")
            have_seas = False
            for it in items:
                for name, out in (("occurrence", occ), ("seasonality", seas)):
                    if name not in it.get("assets", {}):
                        continue
                    data, ok = self.reader(self.pc.sign(it["assets"][name]["href"], self.collection), req.grid, "nearest", 1, True)
                    data = np.asarray(data)
                    take = ok & (data != 255) & (out == NODATA)
                    out[take] = data[take].astype("uint8")
                    have_seas |= name == "seasonality"
        except (RemoteError, NotConfigured) as exc:
            return Unavailable(self.key, f"JRC GSW unavailable: {exc}", "unavailable", prov)
        except Exception as exc:
            return Unavailable(self.key, f"JRC GSW read failed: {type(exc).__name__}: {exc}", "unavailable", prov)
        starts = [it["properties"].get("start_datetime") or it["properties"].get("datetime") for it in items]
        ends = [it["properties"].get("end_datetime") or it["properties"].get("datetime") for it in items]
        prov.update(item_ids=[it["id"] for it in items][:20], n_items=len(items),
                    coverage_start=min((s for s in starts if s), default=None), coverage_end=max((e for e in ends if e), default=None),
                    coverage_reported_by_service=any(ends))
        cls = classify_history(occ, seas if have_seas else None, self.params)
        cls = np.where(req.aoi_mask, cls, NODATA).astype("uint8")
        observed = cls != NODATA
        if not observed.any():
            return Unavailable(self.key, "JRC GSW has no valid pixels over the AOI", "insufficient", prov)
        prov["observed_fraction_aoi"] = round(float(observed.sum() / max(req.aoi_mask.sum(), 1)), 4)
        if req.recent_start is not None and prov["coverage_end"]:
            from .stac import parse_dt
            prov["gap_to_recent_window_days"] = (req.recent_start - parse_dt(prov["coverage_end"])).days
        return EvidenceLayer(self.key, "raster", req.grid, {"jrc_class": cls, "jrc_occurrence": np.where(occ == NODATA, np.nan, occ).astype("float32")},
                             observed, prov)
