"""Evidence fusion: relate independent sources to the Sentinel-2 candidate loss mask on the common grid.

Principles: a source that is unavailable, insufficient or barely overlapping contributes NOTHING (no component, no
default value); weak support is "neutral", not a contradiction; only positive disagreement contradicts.
"""
from __future__ import annotations
import numpy as np
from rasterio.features import shapes
from scipy import ndimage as ndi
from shapely.geometry import shape

import geopandas as gpd

from .evidence.base import NODATA, EvidenceLayer, Unavailable
from .geo import Grid

MIN_COVER = 0.30   # share of candidate-loss pixels that a source must observe before it may speak


def _skip(name: str, o) -> dict:
    if o is None:
        return {"status": "not_requested", "verdict": None, "reason": "source not requested"}
    return {"status": o.status, "verdict": None, "reason": o.reason, "provenance": o.provenance}


def _cover(loss: np.ndarray, observed: np.ndarray) -> tuple[np.ndarray, float]:
    obs = loss & observed
    return obs, float(obs.sum() / max(loss.sum(), 1))


def fuse(loss: np.ndarray, outcomes: dict | None) -> dict:
    outcomes = outcomes or {}
    out = {"sources": {}, "components": {}, "supported_by": [], "contradicted_by": [], "arrays": {}, "limitations": []}
    if not loss.any():
        for k in ("sentinel1", "dynamic_world", "jrc_gsw"):
            out["sources"][k] = {"status": "not_evaluated", "verdict": None, "reason": "no candidate loss"}
        return out

    def finish(key, layer, rec, verdict, comp_name=None, comp_val=None):
        rec.update(status="assessed", verdict=verdict, provenance=layer.provenance)
        out["sources"][key] = rec
        if comp_name:
            out["components"][comp_name] = comp_val
        if verdict == "supports":
            out["supported_by"].append(key)
        if verdict == "contradicts":
            out["contradicted_by"].append(key)
        for k, v in layer.arrays.items():
            if v.dtype == np.uint8:
                out["arrays"][k] = v

    def insufficient(key, layer, cover):
        out["sources"][key] = {"status": "insufficient_overlap", "verdict": None, "coverage_of_candidate": round(cover, 3),
                               "reason": f"source observes only {cover:.0%} of the candidate loss (< {MIN_COVER:.0%}); not used",
                               "provenance": layer.provenance}

    for key in ("sentinel1", "dynamic_world", "jrc_gsw"):
        o = outcomes.get(key)
        if not isinstance(o, EvidenceLayer):
            out["sources"][key] = _skip(key, o)
            continue
        a = o.arrays
        if key == "sentinel1":
            obs, cov = _cover(loss, a["sar_water_loss"] != NODATA)
            if cov < MIN_COVER:
                insufficient(key, o, cov); continue
            agree = float((a["sar_water_loss"][obs] == 1).mean())
            rec_f = float(np.nanmean(a["sar_rec_water_freq"][obs]))
            verdict = "contradicts" if rec_f >= 0.6 else ("supports" if agree >= 0.5 else "neutral")
            finish(key, o, {"coverage_of_candidate": round(cov, 3), "sar_water_loss_share": round(agree, 3),
                            "mean_recent_water_frequency": round(rec_f, 3),
                            "reading": "SAR also shows loss of water" if verdict == "supports" else
                                       ("SAR still sees water in most recent looks" if verdict == "contradicts" else "SAR change weak or partial; not disproof")},
                   verdict, "sensor_agreement", agree)
        elif key == "dynamic_world":
            tr = a["dw_transition"]
            obs, cov = _cover(loss, tr != NODATA)
            if cov < MIN_COVER:
                insufficient(key, o, cov); continue
            bb = float(np.isin(tr[obs], (1, 2)).mean())
            other = float((tr[obs] == 3).mean())
            wprob = float(np.nanmean(a["dw_water_prob_rec"][obs]))
            verdict = "contradicts" if wprob >= 0.5 else ("supports" if bb >= 0.5 else "neutral")
            finish(key, o, {"coverage_of_candidate": round(cov, 3), "water_to_built_or_bare_share": round(bb, 3),
                            "water_to_other_cover_share": round(other, 3), "mean_recent_water_probability": round(wprob, 3),
                            "mean_recent_built_probability": round(float(np.nanmean(a["dw_built_prob_rec"][obs])), 3),
                            "mean_recent_bare_probability": round(float(np.nanmean(a["dw_bare_prob_rec"][obs])), 3),
                            "reading": "land cover changed from water to built/bare" if verdict == "supports" else
                                       ("Dynamic World still reads water" if verdict == "contradicts" else "no clear built/bare transition")},
                   verdict, "landcover_transition", bb)
        else:
            cls = a["jrc_class"]
            obs, cov = _cover(loss, cls != NODATA)
            if cov < MIN_COVER:
                insufficient(key, o, cov); continue
            perm, seas, never = (float((cls[obs] == v).mean()) for v in (2, 1, 0))
            verdict = "contradicts" if seas >= 0.5 else ("supports" if perm >= 0.5 else "neutral")
            finish(key, o, {"coverage_of_candidate": round(cov, 3), "historically_permanent_share": round(perm, 3),
                            "historically_seasonal_share": round(seas, 3), "never_water_share": round(never, 3),
                            "reading": "historically permanent water now lost" if verdict == "supports" else
                                       ("mostly historically seasonal water: normal variation plausible" if verdict == "contradicts" else "history inconclusive"),
                            "temporal_note": "historical reference only; coverage ends " + str(o.provenance.get("coverage_end") or "(not reported)")},
                   verdict, "historical_water", perm)
    return out


def attributed_polygons(loss: np.ndarray, grid: Grid, fus: dict) -> gpd.GeoDataFrame:
    """Change polygons (grid CRS, 4-connected components) with per-polygon support shares from each assessed source."""
    lab, n = ndi.label(loss)
    geoms, ids = [], []
    for g, v in shapes(lab.astype("int32"), mask=lab > 0, transform=grid.transform):
        s = shape(g)
        s = s if s.is_valid else s.buffer(0)
        if not s.is_empty:
            geoms.append(s); ids.append(int(v))
    cols = {"component": ids, "area_ha": [round(g.area / 1e4, 3) for g in geoms]}
    a = fus.get("arrays", {})
    for col, key, test in (("sar_support", "sar_water_loss", lambda x: x == 1), ("dw_support", "dw_transition", lambda x: np.isin(x, (1, 2))),
                           ("jrc_permanent", "jrc_class", lambda x: x == 2)):
        if key in a:
            vals = []
            for i in ids:
                m = (lab == i) & (a[key] != NODATA)
                vals.append(round(float(test(a[key][m]).mean()), 3) if m.any() else None)
            cols[col] = vals
    return gpd.GeoDataFrame(cols, geometry=geoms, crs=grid.crs)
