"""Water-body encroachment pipeline (Sentinel-2 L2A MNDWI persistence). Independent of cs.flood.

Classes (blueprint s2): E1 persistent extent loss, E2 filling/construction-like transition,
E3 likely seasonal (suppressed, logged), E4 insufficient evidence. Output = 'suspected encroachment':
a change in observed water extent, never a legal finding.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone

import numpy as np
from scipy import ndimage as ndi

from .config import EncroachParams
from .geo import Grid, clean_mask, mask_to_gdf, validate_grid
from .models import Pipeline, Result, State, failed_result

log = logging.getLogger(__name__)
BAD_SCL = [0, 1, 3, 8, 9, 10, 11]   # nodata, saturated, cloud shadow, cloud med/high, cirrus, snow
ENC_WEIGHTS = {"persistence": .25, "footprint": .20, "spatial_coherence": .15, "magnitude": .10,
               "sensor_agreement": .10, "image_quality": .10, "recency": .05, "corroboration": .05}
SOURCES = ["Contains modified Copernicus Sentinel-2 data (when real data is used). "
           "Sample mode: SYNTHETIC FIXTURE, not Sentinel data."]


def _enc_score(c: dict, p: EncroachParams):
    rows, num, den = [], 0.0, 0.0
    for name, w in ENC_WEIGHTS.items():
        v = c.get(name)
        rows.append({"name": name, "normalised": None if v is None else round(float(v), 3),
                     "weight": w, "available": v is not None})
        if v is not None:
            num += w * float(v)
            den += w
    coverage = den / sum(ENC_WEIGHTS.values())
    score = 100.0 * num / den if den else 0.0
    if coverage < p.limited_evidence_floor:
        score = min(score, 50.0)
    return round(score, 1), round(coverage, 3), rows


def run_encroachment(scenes, grid: Grid, params: EncroachParams = EncroachParams(), aoi_mask=None, as_of=None, supplementary_scenes=None) -> Result:
    try:
        return _analyze(scenes, grid, params, aoi_mask, as_of or datetime.now(timezone.utc), supplementary_scenes=supplementary_scenes)
    except Exception as exc:
        log.exception("encroachment analysis failed")
        return failed_result(Pipeline.ENCROACH, grid, exc, params.to_dict())


def _analyze(scenes, grid, p: EncroachParams, aoi_mask, as_of, supplementary_scenes=None) -> Result:
    validate_grid(grid)
    H, W = grid.height, grid.width
    aoi = np.ones((H, W), bool) if aoi_mask is None else aoi_mask.astype(bool)
    if not aoi.any():
        raise ValueError("AOI mask is empty")
    px_ha = grid.pixel_area_m2() / 1e4
    obs = []
    for sc in sorted(scenes, key=lambda s: s.acquired):
        if sc.sensor != "sentinel-2":
            raise ValueError(f"Encroachment pipeline accepts Sentinel-2 only, got {sc.sensor}")
        g, s, scl = sc.bands["green"], sc.bands["swir1"], sc.bands["scl"]
        validate_grid(grid, green=g, swir1=s, scl=scl)
        valid = ~np.isin(scl, BAD_SCL) & aoi
        vf = float(valid.sum() / aoi.sum())
        mndwi = (g - s) / (g + s + 1e-6)
        obs.append({"id": sc.scene_id, "date": sc.acquired, "vf": vf, "usable": vf >= p.min_scene_valid,
                    "valid": valid, "water": (mndwi > p.mndwi_thr) & valid, "mndwi": mndwi, "swir": s})
    synthetic = all(s.synthetic for s in scenes) if scenes else True
    base = [o for o in obs if o["usable"] and o["date"] < p.recent_start]
    rec = [o for o in obs if o["usable"] and o["date"] >= p.recent_start]
    rows = [{"scene_id": o["id"], "acquired": o["date"].isoformat(), "valid_frac": round(o["vf"], 3), "usable": o["usable"],
             "role": "baseline" if o["date"] < p.recent_start else "recent"} for o in obs]
    period = {"start": rows[0]["acquired"], "end": rows[-1]["acquired"]} if rows else {"start": None, "end": None}
    zeros = np.zeros((H, W), bool)
    arrays = {"loss": zeros, "seasonal": zeros, "ref_water": zeros, "observed": zeros,
              "coverage": np.zeros((H, W), np.float32), "context": np.full((H, W), np.nan, np.float32),
              "water_stack": np.stack([np.where(o["valid"], o["water"], 255).astype("uint8") for o in obs])
              if obs else np.zeros((0, H, W), np.uint8)}
    sources = SOURCES

    def not_assessed(reason: str, extra=None) -> Result:   # E4
        ev = {"period": period, "scenes": rows, "reason": reason, "class": "E4", "sources": sources,
              "metrics": [{"indicator": "Usable baseline observations", "value": len(base), "note": f"need >= {p.min_baseline_obs}"},
                          {"indicator": "Usable recent observations", "value": len(rec), "note": f"need >= {p.min_recent_obs}"}]}
        ev.update(extra or {})
        return Result(Pipeline.ENCROACH, State.NOT_ASSESSED,
                      f"Insufficient evidence (E4): {reason}. This is NOT a 'no change' result.", ev,
                      ["Persistence rule could not be evaluated; candidates are neither promoted nor dropped."],
                      p.to_dict(), grid, arrays, "loss", None, None, synthetic)

    if len(base) < p.min_baseline_obs or len(rec) < p.min_recent_obs:
        return not_assessed(f"{len(base)} usable baseline and {len(rec)} usable recent observations "
                            f"(need >= {p.min_baseline_obs} and >= {p.min_recent_obs})")
    span = (rec[-1]["date"] - rec[0]["date"]).days
    if span < p.min_span_days:
        return not_assessed(f"recent observations span only {span} days (< {p.min_span_days})")

    bw, bv = sum(o["water"].astype(np.int16) for o in base), sum(o["valid"].astype(np.int16) for o in base)
    freq = np.where(bv > 0, bw / np.maximum(bv, 1), 0.0)
    ref = (bv >= 2) & (freq >= p.ref_freq)
    if ref.sum() * px_ha < p.min_area_ha:
        return not_assessed("no historical water footprint identified in the baseline period")
    core = (bv >= 2) & (freq >= 0.5)
    rw, rv = sum(o["water"].astype(np.int16) for o in rec), sum(o["valid"].astype(np.int16) for o in rec)
    dry = np.where(rv > 0, 1 - rw / np.maximum(rv, 1), 0.0)
    last_water = np.zeros((H, W), bool)
    seen = np.zeros((H, W), bool)
    for o in rec:                                         # most recent valid observation per pixel
        last_water = np.where(o["valid"], o["water"], last_water)
        seen |= o["valid"]
    cand = ref & (rv >= p.min_pixel_obs) & (dry >= p.persist) & seen & ~last_water
    wet_rec = [o for o in rec if o["date"].month in p.wet_months]
    seasonal_check = len(wet_rec) >= p.min_wet_scenes
    if seasonal_check:
        ww, wv = sum(o["water"].astype(np.int16) for o in wet_rec), sum(o["valid"].astype(np.int16) for o in wet_rec)
        wet_dry = np.where(wv > 0, 1 - ww / np.maximum(wv, 1), 0.0)
        confirmed = cand & (wv >= p.min_wet_pixel_obs) & (wet_dry >= p.persist)
        seasonal = clean_mask(cand & ~confirmed, p.min_pixels, p.open_iter)
    else:
        confirmed, seasonal = cand, zeros
    loss = clean_mask(confirmed, p.min_pixels, p.open_iter)
    cov = (rv / len(rec)).astype("float32") * aoi
    last = max((o for o in obs if o["usable"]), key=lambda o: o["date"])
    arrays.update(loss=loss, seasonal=seasonal, ref_water=ref, observed=(rv > 0) & aoi, coverage=cov,
                  context=last["mndwi"].astype("float32"))
    age_days = (as_of - last["date"]).total_seconds() / 86400
    loss_ha, seas_ha, ref_ha = loss.sum() * px_ha, seasonal.sum() * px_ha, ref.sum() * px_ha
    n_unusable = sum(1 for o in obs if not o["usable"])
    ts = []
    for o in obs:
        vr = (o["valid"] & ref).sum()
        ts.append({"date": o["date"].date().isoformat(), "usable": o["usable"],
                   "ref_wet_frac": round(float((o["water"] & ref).sum() / vr), 3) if o["usable"] and vr else None})
    metrics = [
        {"indicator": "Historical footprint (baseline)", "value": f"{ref_ha:.2f} ha", "note": f"water frequency >= {p.ref_freq}"},
        {"indicator": "Suspected persistent extent loss", "value": f"{loss_ha:.2f} ha", "note": "UTM pixel area"},
        {"indicator": "Area uncertainty (boundary pixels)", "value": "", "note": ""},
        {"indicator": "Likely seasonal contraction (E3, suppressed)", "value": f"{seas_ha:.2f} ha" if seasonal_check else "not separable",
         "note": "dry in recent period but wet in wet-season observations" if seasonal_check else "no wet-season observations"},
        {"indicator": "Usable observations (baseline / recent)", "value": f"{len(base)} / {len(rec)}", "note": f"{n_unusable} excluded as cloudy/low-quality"},
        {"indicator": "Wet-season recent observations", "value": len(wet_rec), "note": f"seasonal check {'performed' if seasonal_check else 'NOT possible'}"},
        {"indicator": "Data age at analysis", "value": f"{age_days:.0f} days", "note": "since last usable observation"},
    ]
    sar_corrob = None
    sensor_agree = None
    corrob = {"status": "unassessed", "reason": "SAR corroboration (Sentinel-1) unassessed: no supplementary SAR acquisitions in recent window. Absence of SAR evidence does not disprove optical persistence."}
    if supplementary_scenes and loss.any():
        s1_cand = [s for s in supplementary_scenes if getattr(s, "sensor", "") == "sentinel-1" and "vv_db" in getattr(s, "bands", {}) and s.acquired >= p.recent_start]
        if s1_cand:
            vv_means = []
            for s1_sc in s1_cand:
                vv = s1_sc.bands["vv_db"]
                vv_loss = vv[loss & np.isfinite(vv)]
                if len(vv_loss):
                    vv_means.append(float(np.mean(vv_loss)))
            if vv_means:
                overall_vv = float(np.mean(vv_means))
                if overall_vv > -15.0:
                    corrob = {
                        "status": "corroborated",
                        "reason": f"Sentinel-1 SAR VV backscatter ({overall_vv:.1f} dB > -15 dB) exhibits elevated roughness characteristic of dry ground, independently corroborating absence of open water.",
                        "mean_vv_db": round(overall_vv, 2),
                        "n_sar_scenes": len(s1_cand)
                    }
                    sar_corrob = 1.0
                    sensor_agree = 1.0
                elif overall_vv < -18.0:
                    corrob = {
                        "status": "inconclusive",
                        "reason": f"Sentinel-1 SAR VV backscatter ({overall_vv:.1f} dB) indicates low reflectivity; possible intermittent pooling, saturated soil, or smooth mud. Absence of SAR corroboration does not disprove optical persistence.",
                        "mean_vv_db": round(overall_vv, 2),
                        "n_sar_scenes": len(s1_cand)
                    }
                    sar_corrob = 0.4
                    sensor_agree = 0.4
                else:
                    corrob = {
                        "status": "partial",
                        "reason": f"Sentinel-1 SAR backscatter ({overall_vv:.1f} dB) is intermediate between specular water and rough dry ground.",
                        "mean_vv_db": round(overall_vv, 2),
                        "n_sar_scenes": len(s1_cand)
                    }
                    sar_corrob = 0.7
                    sensor_agree = 0.7
    w_deg, s_deg, e_deg, n_deg = grid.bounds_wgs84()
    coords = {
        "centroid_wgs84": [round((s_deg + n_deg) / 2.0, 5), round((w_deg + e_deg) / 2.0, 5)],
        "bbox_wgs84": [round(w_deg, 5), round(s_deg, 5), round(e_deg, 5), round(n_deg, 5)],
        "crs": f"EPSG:{grid.crs.to_epsg()}",
    }
    # Resolve administrative jurisdiction from study area registry
    from .sample_data import STUDY_AREAS
    jurisdiction = None
    for sa_info in STUDY_AREAS.values():
        if (grid.crs.to_epsg() == sa_info.get("crs_epsg")
                and abs(grid.transform.c - sa_info.get("origin", (0, 0))[0]) < 500
                and abs(grid.transform.f - sa_info.get("origin", (0, 0))[1]) < 500):
            admin = sa_info.get("admin_district")
            auth = sa_info.get("wetland_authority")
            jurisdiction = f"{admin} (Authority: {auth})" if (admin and auth) else (admin or auth)
            break

    evidence = {"period": period, "scenes": rows, "timeseries": ts, "metrics": metrics, "sources": sources,
                "loss_ha": round(float(loss_ha), 3), "seasonal_ha": round(float(seas_ha), 3), "reference_ha": round(float(ref_ha), 3),
                "seasonal_check": "performed" if seasonal_check else "not_possible", "data_age_days": round(age_days, 1),
                "class": None, "jurisdiction": jurisdiction, "coordinates": coords,
                "sensor_corroboration": corrob}
    limitations = ["Optical-only, rule-based persistence logic; thresholds are uncalibrated starting values.",
                   "Water-level proxy normalisation (whole-shoreline retreat test) is NOT implemented; "
                   "seasonality is handled via wet-season persistence only.",
                   "Sentinel-1 SAR corroboration is evaluated against recent SAR passes when available; absence of SAR corroboration is not automatic disproof.",
                   "A change in observed water extent is not proof of illegal encroachment, ownership or intent."]
    if not seasonal_check:
        limitations.insert(0, "No wet-season observations in the recent window: natural seasonal contraction cannot be excluded.")
    if synthetic:
        limitations.append("Inputs are SYNTHETIC fixtures; results are not satellite detections.")
    if loss_ha < p.min_area_ha:
        return Result(Pipeline.ENCROACH, State.NO_CHANGE,
                      f"No persistent water-extent loss above {p.min_area_ha} ha across {len(rec)} usable recent observations "
                      f"({len(base)} baseline).", evidence, limitations, p.to_dict(), grid, arrays, "loss",
                      mask_to_gdf(loss, grid), None, synthetic)
    boundary = loss & ~ndi.binary_erosion(loss)
    unc = float(boundary.sum() * px_ha)
    swir_mean = float(np.mean([o["swir"][loss].mean() for o in rec]))
    evidence["class"] = "E2" if swir_mean > p.bare_swir else "E1"
    evidence["mean_swir1_reflectance"] = round(swir_mean, 3)
    metrics[2].update(value=f"+/- {unc:.2f} ha", note="boundary pixel count x pixel area")
    dry_cnt = (rv - rw)[loss]
    lab, n = ndi.label(loss)
    sizes = ndi.sum(loss, lab, np.arange(1, n + 1))
    comp = {"persistence": float(min(1.0, dry_cnt.mean() / 6.0) * dry[loss].mean()),
            "footprint": float(core[loss].mean()),
            "spatial_coherence": float(sizes[sizes >= 4 * p.min_pixels].sum() / sizes.sum()),
            "magnitude": float(np.clip((loss_ha - p.area_floor_ha) / (p.area_cap_ha - p.area_floor_ha), 0, 1)),
            "sensor_agreement": sensor_agree,
            "image_quality": float(np.mean([o["vf"] for o in rec])),
            "recency": float(np.clip(1 - age_days / p.max_age_days, 0, 1)), "corroboration": sar_corrob}
    score, ev_cov, comp_rows = _enc_score(comp, p)
    rec_verif = [
        "1. Cadastral Revenue Survey: Cross-reference flagged coordinates with Revenue Department FMB survey numbers to verify statutory wetland boundaries.",
        "2. Ground Surface Inspection: Verify in the field whether surface transition represents hard fill/construction debris or seasonal weed die-back.",
        "3. Drainage Network Check: Inspect peripheral culverts and channels for artificial blockage, bunding, or unauthorized diversion.",
        "4. High-Resolution Validation: Review sub-metre commercial optical or UAV imagery prior to statutory action under Wetland Conservation Rules.",
    ]
    conf_rat = (
        f"Review score {score:.0f}/100 derived from persistence ({comp['persistence']:.2f}), "
        f"footprint core ({comp['footprint']:.2f}), coherence ({comp['spatial_coherence']:.2f}), "
        f"and magnitude ({comp['magnitude']:.2f}). Class {evidence['class']} indicates "
        f"{'bare ground/fill spectral transition' if evidence['class'] == 'E2' else 'persistent open extent loss'}."
    )
    evidence.update(
        score_components=comp_rows,
        score_weight_coverage=ev_cov,
        limited_evidence=ev_cov < p.limited_evidence_floor,
        method="Sentinel-2 L2A MNDWI multi-temporal persistence analysis with SCL cloud/shadow filtering and seasonal safeguard.",
        confidence_rationale=conf_rat,
        recommended_verification=rec_verif,
    )
    metrics.append({"indicator": "Centroid coordinates", "value": f"{coords['centroid_wgs84'][0]}°N, {coords['centroid_wgs84'][1]}°E", "note": "WGS84 centroid"})
    if jurisdiction:
        metrics.append({"indicator": "Administrative jurisdiction", "value": jurisdiction[:45], "note": "verified authority layer"})
    metrics.append({"indicator": "Score weight coverage", "value": f"{ev_cov:.0%}", "note": "share of score weights with available evidence"})
    metrics.append({"indicator": "Spectral transition", "value": f"{evidence['class']}",
                    "note": f"mean SWIR1 {swir_mean:.2f} (bare/built-like if > {p.bare_swir})"})
    if sar_corrob is not None:
        metrics.append({"indicator": "SAR corroboration (S1)", "value": f"{overall_vv:.1f} dB", "note": "VV backscatter over loss area"})
    high = score >= p.high_score and seasonal_check
    return Result(Pipeline.ENCROACH, State.HIGH if high else State.POTENTIAL,
                  f"Suspected encroachment: {loss_ha:.1f} ha of persistent water-extent loss inside the historical footprint "
                  f"({evidence['class']}). Requires reviewer confirmation; not a legal finding.", evidence, limitations,
                  p.to_dict(), grid, arrays, "loss", mask_to_gdf(loss, grid), score, synthetic)
