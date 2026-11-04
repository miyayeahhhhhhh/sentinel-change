"""Orchestration: provider -> pipeline -> export -> case record. Used by the UI and the tests."""
from __future__ import annotations
import dataclasses
import json
from datetime import datetime, timedelta, timezone
from time import perf_counter

import numpy as np
from shapely.geometry import mapping

from .config import EncroachParams, Settings, encroach_params_for
from .db import CaseStore
from .encroach import run_encroachment
from .evidence import EvidenceRequest, NotConfigured
from .evidence.acquire import acquire_evidence, source_states
from .exports import export_result
from .geo import grid_for_geometry, polygon_mask
from .integrations.emailer import send_case_alert
from .integrations.neo4j_sync import sync_case
from .imagery import LiveProvider, get_provider
from .models import Result
from .scope import email_block_reasons
from .registry import Registry, analysis_aoi, area_km2, jurisdiction_text
from .study_areas import STUDY_AREAS, make_grid


def _register(settings: Settings, result: Result) -> tuple[Result, str]:
    store = CaseStore(settings.db_path)
    case_id = store.next_id(result.pipeline.value)
    exports = export_result(result, case_id, settings.output_dir)
    store.create_case(case_id, result, exports)
    # Optional side effects are isolated: external outages never invalidate a saved case.
    import os
    case = store.get_case(case_id) or {}
    if all(os.getenv(k, "").strip() for k in ("CS_NEO4J_URI", "CS_NEO4J_USER", "CS_NEO4J_PASSWORD")):
        _, detail = sync_case(case)
        store.log(case_id, "integration", "neo4j_sync", detail)
    if all(os.getenv(k, "").strip() for k in ("CS_SMTP_HOST", "CS_SMTP_FROM", "CS_ALERT_TO")):
        try:
            from .report import build_pdf
            pdf = build_pdf(store, case_id, settings.output_dir / case_id / "case_report.pdf")
        except Exception as exc:
            pdf = None
            store.log(case_id, "integration", "pdf_generation_failed", str(exc))
        blocked = email_block_reasons(case, [])
        if blocked:   # automatic alerts only for real, assessed, flagged cases; never for synthetic/legacy/unassessed
            store.log(case_id, "integration", "email_alert_skipped", "; ".join(blocked))
        else:
            _, detail = send_case_alert(case, str(pdf) if pdf else None)
            store.log(case_id, "integration", "email_alert", detail)
    return result, case_id


def _acq_counts(acq: dict) -> dict:
    return {k: acq.get(k) for k in ("found", "cloud_filtered", "read_ok", "failed", "scenes_in_window")}


def _record_evidence_run(store: CaseStore, key: str, aoi_id: str, o, standin: bool) -> None:
    """One run-log row per source attempt. Unavailable/insufficient outcomes are recorded as such, never as success."""
    if hasattr(o, "arrays"):
        prov = getattr(o, "provenance", {}) or {}
        counts = {"observed_px": int(o.observed.sum()), **{k: prov[k] for k in ("n_scenes", "n_images") if k in prov}}
        store.record_source_run(key, aoi_id, "succeeded", counts, standin=standin)
        return
    reason = getattr(o, "reason", "")
    outcome = "partial" if getattr(o, "status", "") == "insufficient" else ("failed" if "failed" in reason.lower() else "unavailable")
    store.record_source_run(key, aoi_id, outcome, {}, reason, standin=standin)


def _legacy_context(study_area: str) -> dict:
    info = STUDY_AREAS[study_area]
    admin, auth = info.get("admin_district"), info.get("wetland_authority")
    ctx = {"aoi": {"id": study_area, "name": info["name"], "status": "unverified", "kind": "fixed_analysis_grid",
                   "note": "Rectangular analysis grid; not a registry AOI."}}
    if admin or auth:
        ctx["jurisdiction"] = f"{admin} (Authority: {auth})" if admin and auth else (admin or auth)
        ctx["jurisdiction_note"] = "label carried over from earlier build; no authoritative source recorded (unverified)"
    return ctx


def execute_encroachment(settings: Settings, scenario: str, params: EncroachParams | None = None,
                         study_area: str = "synthetic_test_grid", refresh: bool = True, progress_callback=None):
    """Run sample fixtures in sample mode; live fixed grids refresh real imagery and all configured evidence sources."""
    provider = get_provider(settings)
    if settings.mode == "live":
        info = STUDY_AREAS[study_area]
        if info.get("synthetic"):
            raise NotConfigured("A synthetic study grid cannot be used in Live mode. Select a registered waterbody or a real fixed study area.")
        as_of = datetime.now(timezone.utc)
        effective = encroach_params_for(study_area, base=params, as_of=as_of, recent_months=12)
        start = effective.recent_start - timedelta(days=round(3 * 365.25))
        grid = make_grid(study_area)
        store = CaseStore(settings.db_path)
        mark = store.last_run_id()
        if progress_callback:
            progress_callback("Searching the live catalog for Sentinel-2 observations through now...")
        scenes = provider.s2_scenes_for_grid(grid, study_area, start, as_of, refresh=refresh,
                                             progress_callback=progress_callback)
        acquisition = getattr(provider, "last_acquisition", None) or {}
        store.record_source_run("sentinel2", study_area,
                                "succeeded" if acquisition.get("status") == "complete" else "partial",
                                _acq_counts(acquisition), standin=False)
        req = EvidenceRequest(grid, np.ones((grid.height, grid.width), dtype=bool), start, as_of,
                              study_area, recent_start=effective.recent_start)
        if progress_callback:
            progress_callback("Refreshing Sentinel-1, Dynamic World and JRC evidence...")
        outcomes = acquire_evidence(settings, req, refresh=refresh)
        for name, outcome in outcomes.items():
            _record_evidence_run(store, name, study_area, outcome, standin=False)
        ctx = _legacy_context(study_area)
        ctx["acquisition"] = acquisition
        ctx["evidence_sources"] = source_states(outcomes)
        analyzed = run_encroachment(scenes, grid, effective, context=ctx, evidence=outcomes, as_of=as_of)
        result, case_id = _register(settings, analyzed)
        store.set_run_case(study_area, case_id, mark)
        return result, case_id

    grid, scenes = provider.s2_scenes(scenario, study_area=study_area)
    supp = None
    try:
        _, supp = provider.s1_scenes(study_area)
    except NotConfigured:
        supp = None
    # explicit params win; otherwise area-specific (seasonal-profile) parameters - never a silent global default
    effective = params if params is not None else encroach_params_for(study_area)
    ctx = _legacy_context(study_area)
    ctx["evidence_sources"] = {"sentinel1": "used" if supp else "not_used"}
    return _register(settings, run_encroachment(scenes, grid, effective, supplementary_scenes=supp, context=ctx))


def execute_encroachment_for_waterbody(settings: Settings, wb_id: str, params: EncroachParams | None = None,
                                       buffer_m: float = 500.0, as_of: datetime | None = None, recent_months: int = 12,
                                       baseline_years: int = 3, registry: Registry | None = None,
                                       allow_unverified: bool = False, provider=None, evidence_outcomes: dict | None = None,
                                       pc=None, dw_client=None, s1_reader=None, jrc_reader=None,
                                       refresh: bool = True, progress_callback=None):
    """Registry AOI run: polygon AOI (waterbody + buffer, clipped to the pilot district) on a UTM grid derived from it.
    Needs real imagery (live mode). Refuses unverified AOIs unless explicitly allowed (then the case says so)."""
    reg = registry or Registry.load(settings.registry_dir)
    wb = reg.waterbody(wb_id)
    if wb.geometry is None:
        raise NotConfigured(f"Waterbody '{wb_id}' has no usable geometry: {'; '.join(wb.reasons)}")
    if not wb.verified and not allow_unverified:
        raise NotConfigured(f"Waterbody '{wb_id}' is '{wb.status}' ({'; '.join(wb.reasons)}); refusing to run on an unverified AOI.")
    provider_given = provider is not None
    provider = provider or get_provider(settings)
    if not isinstance(provider, LiveProvider):
        raise NotConfigured("Registry AOIs need real imagery: set CS_MODE=live. Sample mode never substitutes synthetic data for a real AOI.")
    as_of = as_of or datetime.now(timezone.utc)
    aoi = analysis_aoi(wb, reg.district, buffer_m)
    grid = grid_for_geometry(aoi, 4326, resolution=10.0, pad_m=30.0)
    mask = polygon_mask(aoi, 4326, grid)
    eff = encroach_params_for(profile=reg.pilot["seasonal_profile"], base=params, as_of=as_of, recent_months=recent_months)
    start = eff.recent_start - timedelta(days=round(baseline_years * 365.25))
    store = CaseStore(settings.db_path)
    mark = store.last_run_id()
    standin = provider_given or any(x is not None for x in (pc, dw_client, s1_reader, jrc_reader, evidence_outcomes))
    s2_started = perf_counter()
    print(f"[CS-TIMING] sentinel2: started; window={start:%Y-%m-%d}..{as_of:%Y-%m-%d}; refresh={refresh}", flush=True)
    if progress_callback:
        progress_callback("Retrieving Sentinel-2 observations from STAC...")
    try:
        scenes = provider.s2_scenes_for_grid(grid, wb.id, start, as_of, **({"refresh": True} if refresh else {}), progress_callback=progress_callback)
        s2_acq = getattr(provider, "last_acquisition", None) or {}
        print(f"[CS-TIMING] sentinel2: completed in {perf_counter() - s2_started:.2f}s; scenes={len(scenes)}; acquisition={s2_acq}", flush=True)
    except NotConfigured as exc:
        print(f"[CS-TIMING] sentinel2: failed after {perf_counter() - s2_started:.2f}s; error={exc}", flush=True)
        acq = getattr(provider, "last_acquisition", None) or {}
        store.record_source_run("sentinel2", wb.id, "failed", _acq_counts(acq), str(exc), standin=standin)
        raise
    acq = getattr(provider, "last_acquisition", None) or {}
    store.record_source_run("sentinel2", wb.id, "succeeded" if acq.get("status") == "complete" else "partial", _acq_counts(acq),
                            "; ".join(f"{e['id']}: {e['error']}" for e in (acq.get("errors") or [])[:3]), standin=standin)
    req = EvidenceRequest(grid, mask, start, as_of, wb.id, recent_start=eff.recent_start)
    evidence_started = perf_counter()
    if progress_callback:
        progress_callback(f"Gathering supplementary evidence ({len(scenes)} S2 scenes loaded)...")
    print("[CS-TIMING] supplementary evidence: starting acquisition", flush=True)
    outcomes = evidence_outcomes if evidence_outcomes is not None else acquire_evidence(
        settings, req, pc=pc, dw_client=dw_client, s1_reader=s1_reader, jrc_reader=jrc_reader, refresh=refresh)
    print(f"[CS-TIMING] supplementary evidence: acquisition function returned in {perf_counter() - evidence_started:.2f}s", flush=True)
    for k, o in outcomes.items():
        _record_evidence_run(store, k, wb.id, o, standin)
    eff = dataclasses.replace(eff, require_independent_support=True)    # registry runs: single-source detections never reach HIGH
    jur, note = jurisdiction_text(reg)
    ctx = {"jurisdiction": jur, "jurisdiction_note": note, "acquisition": acq or None,
           "aoi": {"id": wb.id, "name": wb.name, "status": wb.status, "status_reasons": wb.reasons, "kind": "registry_waterbody_buffer",
                   "buffer_m": buffer_m, "area_km2": round(area_km2(aoi), 3), "waterbody": wb.provenance(),
                   "district": reg.district.provenance() if reg.district else None,
                   "grid": {"crs": f"EPSG:{grid.crs.to_epsg()}", "pixel_m": 10.0, "shape": [grid.height, grid.width]},
                   "geometry_geojson": json.loads(json.dumps(mapping(aoi)))}}
    analysis_started = perf_counter()
    if progress_callback:
        progress_callback("Running MNDWI persistence & encroachment fusion...")
    print("[CS-TIMING] analysis/fusion: started", flush=True)
    analyzed = run_encroachment(scenes, grid, eff, aoi_mask=mask, as_of=as_of, context=ctx, evidence=outcomes)
    print(f"[CS-TIMING] analysis/fusion: completed in {perf_counter() - analysis_started:.2f}s", flush=True)
    register_started = perf_counter()
    if progress_callback:
        progress_callback("Finalizing case record and exports...")
    result, case_id = _register(settings, analyzed)
    print(f"[CS-TIMING] export/case registration/integrations: completed in {perf_counter() - register_started:.2f}s; case_id={case_id}", flush=True)
    store.set_run_case(wb.id, case_id, mark)
    return result, case_id
