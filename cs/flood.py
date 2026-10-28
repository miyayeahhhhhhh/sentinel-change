"""Flood pipeline (Sentinel-1 VV dB change detection). Fully independent of cs.encroach.

Classes (blueprint s2): F1 potential floodwater, F2 permanent water (excluded), F3 masked/unobserved,
F4 no data. Scoring below is flood-specific and shares no code with the encroachment pipeline.
"""
from __future__ import annotations
import logging
import warnings
from datetime import datetime, timezone

import numpy as np
from scipy import ndimage as ndi

from .config import FloodParams
from .geo import Grid, clean_mask, mask_to_gdf, validate_grid
from .models import Pipeline, Result, State, failed_result

log = logging.getLogger(__name__)
FLOOD_WEIGHTS = {"event_support": .25, "area_magnitude": .20, "backscatter_drop": .20,
                 "spatial_coherence": .15, "image_quality": .10, "recency": .05, "optical_agreement": .05}
SOURCES = ["Contains modified Copernicus Sentinel-1 data (when real data is used). "
           "Sample mode: SYNTHETIC FIXTURE, not Sentinel data."]


def qc_db(vv: np.ndarray) -> np.ndarray:
    """Quality control: non-finite or physically implausible dB values become NaN (unobserved)."""
    v = vv.astype("float32").copy()
    v[~np.isfinite(v) | (v < -40) | (v > 5)] = np.nan
    return v


def _flood_score(c: dict, p: FloodParams) -> tuple[float, float, list]:
    """Weighted sum over AVAILABLE components with renormalised weights; returns (score, coverage, rows)."""
    rows, num, den = [], 0.0, 0.0
    for name, w in FLOOD_WEIGHTS.items():
        v = c.get(name)
        rows.append({"name": name, "normalised": None if v is None else round(float(v), 3),
                     "weight": w, "available": v is not None})
        if v is not None:
            num += w * float(v)
            den += w
    coverage = den / sum(FLOOD_WEIGHTS.values())
    score = 100.0 * num / den if den else 0.0
    if coverage < p.limited_evidence_floor:
        score = min(score, 50.0)
    return round(score, 1), round(coverage, 3), rows


def run_flood(scenes, grid: Grid, params: FloodParams = FloodParams(), aoi_mask=None, as_of=None, supplementary_scenes=None) -> Result:
    try:
        return _analyze(scenes, grid, params, aoi_mask, as_of or datetime.now(timezone.utc), supplementary_scenes=supplementary_scenes)
    except Exception as exc:  # never convert a failure into "no change"
        log.exception("flood analysis failed")
        return failed_result(Pipeline.FLOOD, grid, exc, params.to_dict())


def _analyze(scenes, grid, p: FloodParams, aoi_mask, as_of, supplementary_scenes=None) -> Result:
    validate_grid(grid)
    H, W = grid.height, grid.width
    aoi = np.ones((H, W), bool) if aoi_mask is None else aoi_mask.astype(bool)
    if not aoi.any():
        raise ValueError("AOI mask is empty")
    px_ha = grid.pixel_area_m2() / 1e4
    rows, pre, ev, stack = [], [], [], []
    for sc in sorted(scenes, key=lambda s: s.acquired):
        if sc.sensor != "sentinel-1":
            raise ValueError(f"Flood pipeline accepts Sentinel-1 only, got {sc.sensor}")
        vv = qc_db(sc.bands["vv_db"])
        validate_grid(grid, vv=vv)
        vv[~aoi] = np.nan
        cov = float(np.isfinite(vv)[aoi].mean())
        role = "event" if sc.acquired >= p.event_start else "reference"
        usable = cov >= p.min_scene_coverage
        rows.append({"scene_id": sc.scene_id, "sensor": sc.sensor, "acquired": sc.acquired.isoformat(),
                     "role": role, "valid_frac": round(cov, 3), "usable": usable, "source": sc.source})
        (pre if role == "reference" else ev).append(vv) if usable else None
        stack.append(np.where(np.isfinite(vv), (vv < p.water_db).astype("uint8"), 255).astype("uint8"))
    period = {"start": rows[0]["acquired"], "end": rows[-1]["acquired"]} if rows else {"start": None, "end": None}
    synthetic = all(s.synthetic for s in scenes) if scenes else True
    n_pre, n_ev = len(pre), len(ev)
    base_arrays = {"flood": np.zeros((H, W), bool), "perm_water": np.zeros((H, W), bool),
                   "observed": np.zeros((H, W), bool), "coverage": np.zeros((H, W), np.float32),
                   "context": np.full((H, W), np.nan, np.float32),
                   "water_stack": np.stack(stack) if stack else np.zeros((0, H, W), np.uint8)}

    def not_assessed(reason: str, extra=None) -> Result:
        ev_ = {"period": period, "scenes": rows, "reason": reason, "n_reference_usable": n_pre,
               "n_event_usable": n_ev, "metrics": [{"indicator": "Usable reference scenes", "value": n_pre, "note": f"need >= {p.min_pre_scenes}"},
               {"indicator": "Usable event scenes", "value": n_ev, "note": f"need >= {p.min_event_scenes}"}],
               "sources": SOURCES, "class": "F4"}
        ev_.update(extra or {})
        return Result(Pipeline.FLOOD, State.NOT_ASSESSED, f"Not assessed: {reason}. This is NOT a 'no flood' result.",
                      ev_, ["Insufficient usable imagery or coverage: no conclusion about flooding is possible."],
                      p.to_dict(), grid, base_arrays, "flood", None, None, synthetic)

    if n_pre < p.min_pre_scenes or n_ev < p.min_event_scenes:
        return not_assessed(f"{n_pre} usable reference and {n_ev} usable event scene(s); "
                            f"need >= {p.min_pre_scenes} and >= {p.min_event_scenes}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        pre_st, ev_st = np.stack(pre), np.stack(ev)
        pre_ref, ev_ref = np.nanmedian(pre_st, 0), np.nanmedian(ev_st, 0)
    observed = np.isfinite(pre_ref) & np.isfinite(ev_ref) & aoi
    aoi_cov = float(observed.sum() / aoi.sum())
    coverage = np.minimum(np.isfinite(pre_st).mean(0), np.isfinite(ev_st).mean(0)).astype("float32") * aoi
    base_arrays.update(observed=observed, coverage=coverage, context=ev_ref.astype("float32"))
    if aoi_cov < p.min_aoi_coverage:
        return not_assessed(f"reference/event overlap covers {aoi_cov:.0%} of the AOI (< {p.min_aoi_coverage:.0%})",
                            {"aoi_coverage": round(aoi_cov, 3)})

    perm = observed & (pre_ref < p.water_db)                                  # F2
    raw = observed & (ev_ref < p.water_db) & ~perm & ((ev_ref - pre_ref) < -p.diff_db)
    flood = clean_mask(raw, p.min_pixels, p.open_iter)                         # F1
    base_arrays.update(flood=flood, perm_water=perm)
    area_ha = float(flood.sum() * px_ha)
    unobs_ha = float((aoi & ~observed).sum() * px_ha)
    last_ev = max(datetime.fromisoformat(r["acquired"]) for r in rows if r["role"] == "event" and r["usable"])
    age_days = (as_of - last_ev).total_seconds() / 86400
    metrics = [
        {"indicator": "Potential floodwater area (F1)", "value": f"{area_ha:.2f} ha", "note": "valid observed pixels only, UTM pixel area"},
        {"indicator": "Permanent/reference water excluded (F2)", "value": f"{perm.sum() * px_ha:.2f} ha", "note": f"pre-event VV < {p.water_db} dB"},
        {"indicator": "Unobserved / masked area (F3)", "value": f"{unobs_ha:.2f} ha", "note": "no valid reference+event data; not assessable"},
        {"indicator": "AOI analysis coverage", "value": f"{aoi_cov:.1%}", "note": f"min {p.min_aoi_coverage:.0%}"},
        {"indicator": "Usable reference / event scenes", "value": f"{n_pre} / {n_ev}", "note": "after quality control"},
        {"indicator": "Data age at analysis", "value": f"{age_days:.0f} days", "note": "since last usable event scene"},
    ]
    opt_agreement = None
    corrob = {"status": "unassessed", "reason": "Optical corroboration (Sentinel-2) unassessed: no coincident optical pass available in event window. Absence of optical corroboration does not disprove SAR detection."}
    if supplementary_scenes and flood.any():
        s2_cand = [s for s in supplementary_scenes if getattr(s, "sensor", "") == "sentinel-2" and "green" in getattr(s, "bands", {}) and "swir1" in getattr(s, "bands", {})]
        if s2_cand:
            s2_best = min(s2_cand, key=lambda s: abs((s.acquired - last_ev).total_seconds()))
            g_b, s_b = s2_best.bands["green"], s2_best.bands["swir1"]
            scl_b = s2_best.bands.get("scl")
            v_opt = np.ones((H, W), bool)
            if scl_b is not None:
                v_opt = ~np.isin(scl_b, [0, 1, 3, 8, 9, 10, 11])
            mndwi = (g_b - s_b) / (g_b + s_b + 1e-6)
            flood_valid_opt = flood & v_opt & aoi
            date_str = s2_best.acquired.strftime("%Y-%m-%d")
            if flood_valid_opt.sum() >= p.min_pixels:
                opt_agree_frac = float((mndwi > 0.0)[flood_valid_opt].mean())
                opt_agreement = float(np.clip(opt_agree_frac, 0, 1))
                if opt_agree_frac >= 0.5:
                    corrob = {
                        "status": "corroborated",
                        "reason": f"Sentinel-2 optical pass on {date_str} independently corroborates standing water across {opt_agree_frac:.0%} of cloud-free flood extent (MNDWI > 0).",
                        "optical_agreement": round(opt_agree_frac, 3),
                        "date": date_str
                    }
                else:
                    corrob = {
                        "status": "inconclusive",
                        "reason": f"Sentinel-2 pass on {date_str} shows optical water across {opt_agree_frac:.0%} of cloud-free flood extent. Note: absence of optical agreement does not disprove SAR detection (canopy, turbidity, or water recession).",
                        "optical_agreement": round(opt_agree_frac, 3),
                        "date": date_str
                    }
            else:
                corrob = {
                    "status": "cloud_obscured",
                    "reason": f"Sentinel-2 pass on {date_str} was cloud-obscured over the flood zone. Absence of optical corroboration does not disprove SAR detection."
                }
    evidence = {"period": period, "scenes": rows, "metrics": metrics, "sources": SOURCES, "aoi_coverage": round(aoi_cov, 3),
                "area_ha": round(area_ha, 3), "unobserved_ha": round(unobs_ha, 3), "perm_water_ha": round(float(perm.sum() * px_ha), 3),
                "data_age_days": round(age_days, 1), "class": "F1" if flood.any() else None,
                "sensor_corroboration": corrob,
                "jurisdiction": None, "exposure": None}
    limitations = ["Rule-based SAR change detection; wind-roughened water, smooth surfaces, urban double-bounce, "
                   "vegetated flooding and radar shadow are NOT modelled (DEM/HAND masks not implemented).",
                   "Optical corroboration is checked against coincident Sentinel-2 observations when available; absence of optical corroboration is not automatic disproof.",
                   "Jurisdiction lookup is not implemented (no admin layer loaded).",
                   "Pixels without valid data are 'not assessed', never 'no flood'."]
    if synthetic:
        limitations.append("Inputs are SYNTHETIC fixtures; results are not satellite detections.")
    if area_ha < p.min_area_ha:
        evidence["metrics"].append({"indicator": "Threshold", "value": f"< {p.min_area_ha} ha", "note": "minimum reportable area"})
        return Result(Pipeline.FLOOD, State.NO_CHANGE,
                      f"No potential floodwater above {p.min_area_ha} ha found in {n_ev} usable event scene(s) "
                      f"over the observed {aoi_cov:.0%} of the AOI.", evidence, limitations, p.to_dict(), grid,
                      base_arrays, "flood", mask_to_gdf(flood, grid), None, synthetic)
    # --- evidence components (flood-specific) ---
    drop = float(np.mean((pre_ref - ev_ref)[flood]))
    lab, n = ndi.label(flood)
    sizes = ndi.sum(flood, lab, np.arange(1, n + 1))
    coherence = float(sizes[sizes >= 4 * p.min_pixels].sum() / sizes.sum())
    valid_ev = np.isfinite(ev_st)
    dark_all = ((ev_st < p.water_db) | ~valid_ev).all(0) & valid_ev.any(0)
    support = float(dark_all[flood].mean()) if n_ev >= 2 else None
    comp = {"event_support": support,
            "area_magnitude": float(np.clip((area_ha - p.area_floor_ha) / (p.area_cap_ha - p.area_floor_ha), 0, 1)),
            "backscatter_drop": float(np.clip(drop / 8.0, 0, 1)), "spatial_coherence": coherence,
            "image_quality": float(np.mean([r["valid_frac"] for r in rows if r["usable"]])),
            "recency": float(np.clip(1 - age_days / p.max_age_days, 0, 1)), "optical_agreement": opt_agreement}
    score, ev_cov, comp_rows = _flood_score(comp, p)
    evidence.update(score_components=comp_rows, score_weight_coverage=ev_cov, mean_drop_db=round(drop, 2),
                    limited_evidence=ev_cov < p.limited_evidence_floor)
    evidence["metrics"] += [{"indicator": "Mean backscatter drop in F1", "value": f"{drop:.1f} dB", "note": "reference - event"},
                            {"indicator": "Score weight coverage", "value": f"{ev_cov:.0%}", "note": "share of score weights with available evidence"}]
    if opt_agreement is not None:
        evidence["metrics"].append({"indicator": "Optical corroboration (S2)", "value": f"{opt_agreement:.0%}", "note": "MNDWI water agreement in cloud-free flood area"})
    state = State.HIGH if score >= p.high_score else State.POTENTIAL
    return Result(Pipeline.FLOOD, state,
                  f"Potential floodwater: {area_ha:.1f} ha observed across {n_ev} usable event scene(s). "
                  "Requires reviewer confirmation.", evidence, limitations, p.to_dict(), grid, base_arrays,
                  "flood", mask_to_gdf(flood, grid), score, synthetic)
