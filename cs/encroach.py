"""Water-body encroachment pipeline (Sentinel-2 L2A MNDWI persistence) = the single encroachment pipeline.
Sentinel-1 / Dynamic World / JRC GSW are evidence sources attached to it (see cs/evidence); only what is
actually integrated contributes to a result, and the case record states which.

Classes (blueprint s2): E1 persistent observed extent loss, E2 bare/built-like spectral signal,
E3 likely seasonal (suppressed, logged), E4 insufficient evidence. Output = 'suspected encroachment':
a change in observed water extent, never a legal finding.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone

import numpy as np
import rasterio.warp
from scipy import ndimage as ndi

from .config import EncroachParams
from .evidence.sentinel1 import qc_db
from .fusion import attributed_polygons, fuse
from .geo import Grid, clean_mask, mask_to_gdf, validate_grid
from .models import Pipeline, Result, State, failed_result

log = logging.getLogger(__name__)
BAD_SCL = [0, 1, 2, 3, 7, 8, 9, 10, 11]   # nodata, saturated, dark/shadow, cloud shadow/low-probability cloud, cloud, cirrus, snow
ENC_WEIGHTS = {"persistence": .20, "footprint": .15, "spatial_coherence": .15, "magnitude": .10,
               "sensor_agreement": .10, "image_quality": .10, "recency": .05, "corroboration": .05,
               "landcover_transition": .05, "historical_water": .05}
SOURCES = ["Contains modified Copernicus Sentinel-2 data (when real data is used). "
           "Sample mode: SYNTHETIC FIXTURE, not Sentinel data."]


def _enc_score(c: dict, p: EncroachParams):
    rows, num, den = [], 0.0, 0.0
    total_weight = sum(ENC_WEIGHTS.values())
    for name, w in ENC_WEIGHTS.items():
        v = c.get(name)
        rows.append({"name": name, "normalised": None if v is None else round(float(v), 3),
                     "weight": w, "available": v is not None,
                     "weighted_contribution": 0.0 if v is None else round(w * float(v), 4)})
        if v is not None:
            num += w * float(v)
            den += w
    coverage = den / total_weight
    # Missing evidence contributes no positive score; availability is reported separately.
    score = 100.0 * num / total_weight if total_weight else 0.0
    if coverage < p.limited_evidence_floor:
        score = min(score, 50.0)
    return round(score, 1), round(coverage, 3), rows


def run_encroachment(scenes, grid: Grid, params: EncroachParams = EncroachParams(), aoi_mask=None, as_of=None,
                     supplementary_scenes=None, context: dict | None = None, evidence: dict | None = None) -> Result:
    """`context` (optional) carries registry provenance: {'jurisdiction', 'jurisdiction_note', 'aoi', 'evidence_sources'}.
    `evidence` maps 'sentinel1'/'dynamic_world'/'jrc_gsw' to an EvidenceLayer or Unavailable (cs.evidence); sources not present
    in the mapping were not requested. Unavailable sources contribute nothing."""
    try:
        return _analyze(scenes, grid, params, aoi_mask, as_of or datetime.now(timezone.utc),
                        supplementary_scenes=supplementary_scenes, context=context or {}, evidence_in=evidence)
    except Exception as exc:
        log.exception("encroachment analysis failed")
        return failed_result(Pipeline.ENCROACH, grid, exc, params.to_dict())


def _analyze(scenes, grid, p: EncroachParams, aoi_mask, as_of, supplementary_scenes=None, context=None, evidence_in=None) -> Result:
    context = context or {}
    validate_grid(grid)
    H, W = grid.height, grid.width
    aoi = np.ones((H, W), bool) if aoi_mask is None else aoi_mask.astype(bool)
    if not aoi.any():
        raise ValueError("AOI mask is empty")
    px_ha = grid.pixel_area_m2() / 1e4
    scenes = list(scenes)
    daily_scenes = {}
    for sc in sorted(scenes, key=lambda s: s.acquired):
        if sc.sensor != "sentinel-2":
            raise ValueError(f"Encroachment pipeline accepts Sentinel-2 only, got {sc.sensor}")
        g, s, scl = sc.bands["green"], sc.bands["swir1"], sc.bands["scl"]
        validate_grid(grid, green=g, swir1=s, scl=scl)
        valid = ~np.isin(scl, BAD_SCL) & aoi
        daily_scenes.setdefault(sc.acquired.date(), []).append((sc, g, s, valid))
    obs = []
    for day, items in sorted(daily_scenes.items()):
        valid_stack = np.stack([item[3] for item in items])
        valid = valid_stack.any(axis=0)
        # Mosaic same-day overlapping items pixel-wise so tile overlap is never counted
        # as multiple independent temporal observations.
        chosen = np.argmax(valid_stack, axis=0)
        green = np.take_along_axis(np.stack([item[1] for item in items]), chosen[None], axis=0)[0]
        swir = np.take_along_axis(np.stack([item[2] for item in items]), chosen[None], axis=0)[0]
        mndwi = (green - swir) / (green + swir + 1e-6)
        vf = float(valid.sum() / aoi.sum())
        ids = [item[0].scene_id for item in items]
        obs.append({"id": ids[0] if len(ids) == 1 else f"DAILY-MOSAIC-{day.isoformat()}", "source_ids": ids,
                    "date": items[0][0].acquired, "vf": vf, "usable": vf >= p.min_scene_valid,
                    "valid": valid, "water": (mndwi > p.mndwi_thr) & valid, "mndwi": mndwi, "swir": swir})
    synthetic = all(s.synthetic for s in scenes) if scenes else True
    base = [o for o in obs if o["usable"] and o["date"] < p.recent_start]
    rec = [o for o in obs if o["usable"] and o["date"] >= p.recent_start]
    rows = [{"scene_id": o["id"], "source_scene_ids": o["source_ids"], "source_scene_count": len(o["source_ids"]),
             "acquired": o["date"].isoformat(), "valid_frac": round(o["vf"], 3), "usable": o["usable"],
             "role": "baseline" if o["date"] < p.recent_start else "recent"} for o in obs]
    period = {"start": rows[0]["acquired"], "end": rows[-1]["acquired"]} if rows else {"start": None, "end": None}
    zeros = np.zeros((H, W), bool)
    arrays = {"loss": zeros, "seasonal": zeros, "ref_water": zeros, "observed": zeros, "aoi": aoi,
              "coverage": np.zeros((H, W), np.float32), "context": np.full((H, W), np.nan, np.float32),
              "baseline_water_frequency": np.full((H, W), np.nan, np.float32),
              "recent_water_frequency": np.full((H, W), np.nan, np.float32),
              "baseline_matched_frequency": np.full((H, W), np.nan, np.float32),
              "recent_matched_frequency": np.full((H, W), np.nan, np.float32),
              "water_stack": np.stack([np.where(o["valid"], o["water"], 255).astype("uint8") for o in obs])
              if obs else np.zeros((0, H, W), np.uint8)}
    sources = SOURCES

    def not_assessed(reason: str, extra=None) -> Result:   # E4
        ev = {"period": period, "scenes": rows, "reason": reason, "class": "E4", "sources": sources,
              "aoi": context.get("aoi"), "jurisdiction": context.get("jurisdiction"),
              "evidence_sources": context.get("evidence_sources") or {},
              "acquisition": context.get("acquisition"),
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
    # Compare each recent calendar month only with baseline acquisitions from the same
    # month. This avoids confusing recurring seasonal water variation with a change.
    # Existing count, water-frequency and persistence parameters are reused as-is.
    expected_recent_water = np.zeros((H, W), np.int16)
    expected_recent_valid = np.zeros((H, W), np.int16)
    comparable_recent_valid = np.zeros((H, W), np.int16)
    matched_bw, matched_bv = np.zeros((H, W), np.int16), np.zeros((H, W), np.int16)
    matched_rw, matched_rv = np.zeros((H, W), np.int16), np.zeros((H, W), np.int16)
    last_expected_water = np.zeros((H, W), bool)
    seen_expected = np.zeros((H, W), bool)
    matched_months = []
    for month in sorted({o["date"].month for o in rec}):
        hist = [o for o in base if o["date"].month == month]
        recent = [o for o in rec if o["date"].month == month]
        if not hist:
            continue
        matched_months.append(month)
        hb = sum(o["water"].astype(np.int16) for o in hist)
        hv = sum(o["valid"].astype(np.int16) for o in hist)
        rb = sum(o["water"].astype(np.int16) for o in recent)
        rv_month = sum(o["valid"].astype(np.int16) for o in recent)
        matched_bw += hb
        matched_bv += hv
        matched_rw += rb
        matched_rv += rv_month
        hist_month_usable = hv >= p.min_wet_pixel_obs
        comparable_recent_valid += np.where(hist_month_usable, rv_month, 0)
        historically_wet = hist_month_usable & (hb / np.maximum(hv, 1) >= p.ref_freq)
        expected_recent_valid += np.where(historically_wet, rv_month, 0)
        expected_recent_water += np.where(historically_wet, rb, 0)
        for o in recent:
            eligible = o["valid"] & historically_wet
            last_expected_water = np.where(eligible, o["water"], last_expected_water)
            seen_expected |= eligible
    month_comparable = comparable_recent_valid >= p.min_pixel_obs
    seasonal_comparable = ref & month_comparable
    seasonal_coverage = float(seasonal_comparable.sum() / max(ref.sum(), 1))
    matched_dry = 1 - expected_recent_water / np.maximum(expected_recent_valid, 1)
    month_candidate = (ref & (expected_recent_valid >= p.min_pixel_obs) & (matched_dry >= p.persist) &
                       seen_expected & ~last_expected_water)
    wet_rec = [o for o in rec if o["date"].month in p.wet_months]
    seasonal_check = seasonal_coverage >= p.min_scene_valid
    if seasonal_coverage < p.min_scene_valid:
        arrays.update(
            ref_water=ref, observed=(rv > 0) & aoi, coverage=(rv / len(rec)).astype("float32") * aoi,
            baseline_water_frequency=np.divide(bw, bv, out=np.full((H, W), np.nan, np.float32), where=bv > 0),
            recent_water_frequency=np.divide(rw, rv, out=np.full((H, W), np.nan, np.float32), where=rv > 0),
            baseline_matched_frequency=np.divide(matched_bw, matched_bv, out=np.full((H, W), np.nan, np.float32), where=matched_bv > 0),
            recent_matched_frequency=np.divide(matched_rw, matched_rv, out=np.full((H, W), np.nan, np.float32), where=matched_rv > 0),
        )
        return not_assessed(
            f"same-calendar-month comparison covers only {seasonal_coverage:.0%} of the historical water footprint "
            f"(need >= {p.min_scene_valid:.0%})",
            {"seasonal_comparison": {"status": "insufficient", "months_compared": matched_months,
                                     "historical_footprint_coverage": round(seasonal_coverage, 3)}})
    all_season_candidate = ref & (rv >= p.min_pixel_obs) & (dry >= p.persist)
    seasonal = clean_mask(all_season_candidate & ~month_candidate, p.min_pixels, p.open_iter)
    loss = clean_mask(month_candidate, p.min_pixels, p.open_iter)
    cov = (rv / len(rec)).astype("float32") * aoi
    last = max((o for o in obs if o["usable"]), key=lambda o: o["date"])
    arrays.update(loss=loss, seasonal=seasonal, ref_water=ref, observed=(rv > 0) & aoi, coverage=cov,
                  context=last["mndwi"].astype("float32"),
                  baseline_water_frequency=np.divide(bw, bv, out=np.full((H, W), np.nan, np.float32), where=bv > 0),
                  recent_water_frequency=np.divide(rw, rv, out=np.full((H, W), np.nan, np.float32), where=rv > 0),
                  baseline_matched_frequency=np.divide(matched_bw, matched_bv, out=np.full((H, W), np.nan, np.float32), where=matched_bv > 0),
                  recent_matched_frequency=np.divide(matched_rw, matched_rv, out=np.full((H, W), np.nan, np.float32), where=matched_rv > 0))
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
        {"indicator": "Potential persistent extent loss", "value": f"{loss_ha:.2f} ha", "note": "UTM pixel area"},
        {"indicator": "Area uncertainty (boundary pixels)", "value": "", "note": ""},
        {"indicator": "Seasonally unmatched contraction (E3, suppressed)", "value": f"{seas_ha:.2f} ha" if seasonal_check else "not separable",
         "note": "recently dry pixels did not persist against the same-calendar-month baseline" if seasonal_check else "same-month comparison unavailable"},
        {"indicator": "Usable observations (baseline / recent)", "value": f"{len(base)} / {len(rec)}", "note": f"{n_unusable} excluded as cloudy/low-quality"},
        {"indicator": "Same-month comparison coverage", "value": f"{seasonal_coverage:.0%}",
         "note": f"historical water footprint with sufficient same-month baseline and recent observations; months {matched_months}; remainder unassessed"},
        {"indicator": "Wet-season recent observations", "value": len(wet_rec), "note": "context only; seasonality safeguard uses same-calendar-month comparison"},
        {"indicator": "Data age at analysis", "value": f"{age_days:.0f} days", "note": "since last usable observation"},
    ]
    sar_corrob = None
    sensor_agree = None
    corrob = {"status": "unassessed", "reason": "SAR corroboration (Sentinel-1) unassessed: no supplementary SAR acquisitions in recent window. Absence of SAR evidence does not disprove optical persistence."}
    fus = fuse(loss, evidence_in)
    s1_new = fus["sources"].get("sentinel1", {}).get("status") == "assessed"
    if supplementary_scenes and loss.any() and not s1_new:
        s1_all = [s for s in supplementary_scenes if getattr(s, "sensor", "") == "sentinel-1" and "vv_db" in getattr(s, "bands", {}) and s.acquired >= p.recent_start]
        s1_cand = [s for s in s1_all if s.bands["vv_db"].shape == loss.shape]      # scenes not on this grid are never used
        if len(s1_cand) < len(s1_all):
            corrob["reason"] += f" {len(s1_all) - len(s1_cand)} Sentinel-1 scene(s) not on the analysis grid were ignored."
        if s1_cand:
            vv_means = []
            for s1_sc in s1_cand:
                vv = qc_db(s1_sc.bands["vv_db"])
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
    if loss.any():            # centroid of the FLAGGED area (pixel centres), not of the analysis grid
        rr, cc = np.nonzero(loss)
        t = grid.transform                                    # rotation-free (validate_grid)
        cx, cy = t.c + (float(cc.mean()) + 0.5) * t.a, t.f + (float(rr.mean()) + 0.5) * t.e
        clon, clat = rasterio.warp.transform(grid.crs, "EPSG:4326", [cx], [cy])
        centroid, basis = [round(clat[0], 5), round(clon[0], 5)], "flagged_area"
    else:
        centroid, basis = [round((s_deg + n_deg) / 2.0, 5), round((w_deg + e_deg) / 2.0, 5)], "analysis_grid"
    coords = {"centroid_wgs84": centroid, "centroid_basis": basis,
              "bbox_wgs84": [round(w_deg, 5), round(s_deg, 5), round(e_deg, 5), round(n_deg, 5)],
              "crs": f"EPSG:{grid.crs.to_epsg()}"}
    jurisdiction = context.get("jurisdiction")
    srcs_state = dict(context.get("evidence_sources") or {})
    srcs_state.setdefault("sentinel2_mndwi", "used")
    for k, src_rec in fus["sources"].items():
        st = src_rec["status"]
        source_status = src_rec.get("source_status")
        if st == "not_evaluated" and source_status == "not_requested":
            srcs_state[k] = "not_requested"
        elif st == "not_evaluated" and source_status not in (None, "available"):
            srcs_state[k] = f"{source_status}: {src_rec.get('source_reason', '')}"
        else:
            srcs_state[k] = "used" if st == "assessed" else (st if st in ("not_requested", "not_evaluated") else f"{st}: {src_rec.get('reason', '')}")
    if s1_new:
        sensor_agree = fus["components"]["sensor_agreement"]
        corrob = {"status": "assessed", "reason": "Sentinel-1 water-frequency evidence (baseline vs recent, one orbit group).", **fus["sources"]["sentinel1"]}
    srcs_state.setdefault("sentinel1", "used" if sar_corrob is not None else "not_requested")
    srcs_state.setdefault("dynamic_world", "not_requested")
    srcs_state.setdefault("jrc_gsw", "not_requested")

    evidence = {"period": period, "scenes": rows, "timeseries": ts, "metrics": metrics, "sources": sources,
                "loss_ha": round(float(loss_ha), 3), "seasonal_ha": round(float(seas_ha), 3), "reference_ha": round(float(ref_ha), 3),
                "seasonal_check": "performed" if seasonal_check else "not_possible", "data_age_days": round(age_days, 1),
                "wet_season_check": "performed" if len(wet_rec) >= p.min_wet_scenes else "not_possible",
                "seasonal_comparison": {"status": "assessed", "months_compared": matched_months,
                                        "historical_footprint_coverage": round(seasonal_coverage, 3),
                                        "unassessed_footprint_share": round(1.0 - seasonal_coverage, 3),
                                        "rationale": "recent calendar months are compared with baseline observations from the same months"},
                "class": None, "jurisdiction": jurisdiction, "coordinates": coords,
                "acquisition": context.get("acquisition"),
                "sensor_corroboration": corrob, "aoi": context.get("aoi"), "evidence_sources": srcs_state,
                "evidence_fusion": {"sources": fus["sources"], "supported_by": fus["supported_by"], "contradicted_by": fus["contradicted_by"]}}
    limitations = ["Optical-only, rule-based persistence logic; thresholds are uncalibrated starting values.",
                   "Month matching reduces recurring seasonal confounding but does not measure water level or eliminate interannual hydrologic variation.",
                   "Sentinel-1 SAR corroboration is evaluated against recent SAR passes when available; absence of SAR corroboration is not automatic disproof.",
                   "A change in observed water extent is not proof of illegal encroachment, ownership or intent."]
    for k, label in (("sentinel1", "Sentinel-1 SAR"), ("dynamic_world", "Dynamic World land cover"), ("jrc_gsw", "JRC Global Surface Water")):
        st = srcs_state.get(k, "")
        if st not in ("used",) and not (k == "sentinel1" and sar_corrob is not None):
            limitations.append(f"{label} did not contribute to this result ({st or 'unavailable'}); its absence is not evidence of no change.")
    if len(wet_rec) < p.min_wet_scenes:
        limitations.insert(0, "Too few recent observations in the configured wet months: interannual seasonal contraction cannot be excluded; this result is capped below high-priority status. The profile is unvalidated.")
    if seasonal_coverage < 1.0:
        limitations.append(f"Only {seasonal_coverage:.0%} of the historical water footprint met the same-month observation-coverage rule; the remainder is unassessed, not no-change.")
    if synthetic:
        limitations.append("Inputs are SYNTHETIC fixtures; results are not satellite detections.")
    if loss_ha < p.min_area_ha:
        return Result(Pipeline.ENCROACH, State.NO_CHANGE,
                      f"No persistent water-extent loss above {p.min_area_ha} ha was detected in the seasonally comparable "
                      f"{seasonal_coverage:.0%} of the historical footprint ({len(rec)} usable recent, {len(base)} baseline observations). "
                      "The remainder is unassessed.", evidence, limitations, p.to_dict(), grid, arrays, "loss",
                      mask_to_gdf(loss, grid), None, synthetic)
    boundary = loss & ~ndi.binary_erosion(loss)
    unc = float(boundary.sum() * px_ha)
    swir_values = [o["swir"][loss & o["valid"]] for o in rec if np.any(loss & o["valid"])]
    swir_mean = float(np.concatenate(swir_values).mean())
    evidence["class"] = "E2" if swir_mean > p.bare_swir else "E1"
    evidence["mean_swir1_reflectance"] = round(swir_mean, 3)
    metrics[2].update(value=f"+/- {unc:.2f} ha", note="boundary pixel count x pixel area")
    lab, n = ndi.label(loss)
    sizes = ndi.sum(loss, lab, np.arange(1, n + 1))
    comp = {"persistence": float(matched_dry[loss].mean()),
            "footprint": float(core[loss].mean()),
            "spatial_coherence": float(sizes[sizes >= 4 * p.min_pixels].sum() / sizes.sum()),
            "magnitude": float(np.clip((loss_ha - p.area_floor_ha) / (p.area_cap_ha - p.area_floor_ha), 0, 1)),
            "sensor_agreement": sensor_agree,
            "image_quality": float(seasonal_coverage * np.mean([o["vf"] for o in rec])),
            "recency": float(np.clip(1 - age_days / p.max_age_days, 0, 1)), "corroboration": sar_corrob,
            "landcover_transition": fus["components"].get("landcover_transition"),
            "historical_water": fus["components"].get("historical_water")}
    score, ev_cov, comp_rows = _enc_score(comp, p)
    support = {"supported_by": fus["supported_by"], "contradicted_by": fus["contradicted_by"], "notes": []}
    if fus["contradicted_by"]:
        score = min(score, 50.0)
        support["notes"].append("Score capped at 50: independent evidence disagrees (" + ", ".join(fus["contradicted_by"]) + ").")
    current_support = any(source in fus["supported_by"] for source in ("sentinel1", "dynamic_world"))
    if p.require_independent_support and not fus["supported_by"]:
        score = min(score, 50.0)
        support["notes"].append("Single-source detection (Sentinel-2 only): unavailable sources earn no score weight; the score is capped at 50.")
    if not current_support:
        support["notes"].append("No current independent source supports the water-loss candidate; historical JRC context alone cannot establish current change.")
    evidence["support_assessment"] = support
    rec_verif = [
        "1. Cadastral Revenue Survey: Cross-reference flagged coordinates with Revenue Department FMB survey numbers to verify statutory wetland boundaries.",
        "2. Ground Surface Inspection: Verify in the field whether surface transition represents hard fill/construction debris or seasonal weed die-back.",
        "3. Drainage Network Check: Inspect peripheral culverts and channels for artificial blockage, bunding, or unauthorized diversion.",
        "4. High-Resolution Validation: Review sub-metre commercial optical or UAV imagery prior to statutory action under Wetland Conservation Rules.",
    ]
    conf_rat = (
        f"Heuristic review-priority score {score:.0f}/100 (not a probability) derived from persistence ({comp['persistence']:.2f}), "
        f"footprint core ({comp['footprint']:.2f}), coherence ({comp['spatial_coherence']:.2f}), "
        f"and magnitude ({comp['magnitude']:.2f}). Class {evidence['class']} indicates "
        f"{'a bare/built-like spectral signal, not proof of fill' if evidence['class'] == 'E2' else 'persistent observed extent loss'}."
    )
    evidence.update(
        score_components=comp_rows,
        score_weight_coverage=ev_cov,
        limited_evidence=ev_cov < p.limited_evidence_floor,
        method="Sentinel-2 L2A MNDWI persistence analysis with SCL cloud/shadow filtering and same-calendar-month baseline comparison.",
        confidence_rationale=conf_rat,
        recommended_verification=rec_verif,
    )
    metrics.append({"indicator": "Centroid coordinates", "value": f"{coords['centroid_wgs84'][0]}°N, {coords['centroid_wgs84'][1]}°E", "note": "WGS84 centroid"})
    if jurisdiction:
        metrics.append({"indicator": "Administrative jurisdiction", "value": jurisdiction[:45], "note": context.get("jurisdiction_note", "source status not recorded")})
    metrics.append({"indicator": "Score weight coverage", "value": f"{ev_cov:.0%}", "note": "share of score weights with available evidence"})
    metrics.append({"indicator": "Spectral transition", "value": f"{evidence['class']}",
                    "note": f"mean SWIR1 {swir_mean:.2f} (bare/built-like if > {p.bare_swir})"})
    if sar_corrob is not None:
        metrics.append({"indicator": "SAR corroboration (S1)", "value": f"{overall_vv:.1f} dB", "note": "VV backscatter over loss area"})
    high = (score >= p.high_score and seasonal_check and len(wet_rec) >= p.min_wet_scenes and
            current_support and not fus["contradicted_by"])
    arrays.update(fus["arrays"])
    poly = attributed_polygons(loss, grid, fus)
    return Result(Pipeline.ENCROACH, State.HIGH if high else State.POTENTIAL,
                  f"Observed water-extent loss candidate: {loss_ha:.1f} ha inside the historical footprint "
                  f"({evidence['class']}). Satellite evidence does not confirm encroachment; reviewer and field confirmation are required.", evidence, limitations,
                  p.to_dict(), grid, arrays, "loss", poly, score, synthetic)
