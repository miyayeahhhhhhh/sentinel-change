"""Change Sentinel - Streamlit GIS Monitoring Console. UI only: all analysis lives in cs/*."""
from __future__ import annotations
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
import dataclasses
import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from cs import mapview, registry as registry_mod, workflow
from cs.config import SEASONAL_PROFILES, Settings, encroach_params_for
from cs.db import DECISIONS, STATUSES, CaseStore
from cs.imagery import NotConfigured
from cs.models import DISCLAIMER, STATE_LABELS, State
from cs.priority import queue_table, filter_queue_by_case_id, RANKING_RATIONALE
from cs.report import build_pdf
from cs.integrations.sarvam import LANGUAGES
from cs.integrations.native_report import build_native_report
from cs.integrations.emailer import guarded_send
from cs import scope as scope_mod
from cs.evidence import evidence_sources
from cs.study_areas import STUDY_AREAS

st.set_page_config(page_title="Change Sentinel | GIS Monitoring Console", layout="wide", initial_sidebar_state="expanded")

# Professional GIS console stylesheet (neutral base, dark slate text, restrained blue accents, compact data density)
st.markdown("""<style>
/* Root and main container */
html, body, [class*="css"], .stApp {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    color: #0f172a !important;
    background-color: #f8fafc !important;
}
.stApp {background: #f8fafc !important;}

/* =========================================================
   SIDEBAR VISIBILITY & HIGH-CONTRAST STYLING
   ========================================================= */
section[data-testid="stSidebar"] {
    background-color: #ffffff !important;
    border-right: 1px solid #cbd5e1 !important;
}

/* Force dark readable typography on all sidebar text */
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3,
section[data-testid="stSidebar"] h4,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] span,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] strong,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
section[data-testid="stSidebar"] [data-testid="stRadio"] label,
section[data-testid="stSidebar"] [data-testid="stRadio"] label p,
section[data-testid="stSidebar"] [data-testid="stRadio"] label span,
section[data-testid="stSidebar"] [data-testid="stRadio"] div[data-testid="stMarkdownContainer"] p {
    color: #0f172a !important;
    font-weight: 500 !important;
}

/* Sidebar captions */
section[data-testid="stSidebar"] .stCaption,
section[data-testid="stSidebar"] .stCaption p,
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
    color: #475569 !important;
}

/* Sidebar inputs */
section[data-testid="stSidebar"] input {
    background-color: #f1f5f9 !important;
    color: #0f172a !important;
    border: 1px solid #cbd5e1 !important;
    border-radius: 3px !important;
}

/* Sidebar radio button items */
section[data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] > label {
    padding: 6px 8px !important;
    border-radius: 4px !important;
    margin-bottom: 2px !important;
    background: transparent;
}
section[data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] > label:hover {
    background: #f1f5f9 !important;
}

/* Dividers */
section[data-testid="stSidebar"] hr {
    border-color: #e2e8f0 !important;
}

/* =========================================================
   MAIN CONSOLE STYLING
   ========================================================= */
.block-container {
    padding-top: 1.0rem;
    padding-bottom: 2.0rem;
    max-width: 1680px;
}
h1, h2, h3, h4 {
    color: #0f172a !important;
    font-weight: 600;
    letter-spacing: -0.01em;
}
.cs-header {
    font-size: 13px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #334155 !important;
    margin-bottom: 4px;
}
.cs-badge {
    display: inline-block;
    padding: 3px 9px;
    border: 1px solid;
    border-radius: 3px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.02em;
}
.cs-tag {
    display: inline-block;
    padding: 2px 6px;
    background: #edf2f7;
    border: 1px solid #cbd5e1;
    border-radius: 2px;
    font-size: 11px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    color: #334155;
}
.cs-card {
    background: #ffffff !important;
    border: 1px solid #e2e8f0 !important;
    border-radius: 3px;
    padding: 12px 14px;
    margin-bottom: 12px;
}
.cs-card * {
    color: #0f172a;
}
.cs-card-title {
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #475569 !important;
    border-bottom: 1px solid #edf2f7;
    padding-bottom: 4px;
    margin-bottom: 8px;
}
.cs-note {
    border: 1px solid #cbd5e1;
    background: #ffffff;
    padding: 9px 12px;
    border-radius: 3px;
    font-size: 12px;
    line-height: 1.45;
    color: #334155;
    margin-top: 6px;
    margin-bottom: 6px;
}
.cs-sample {
    border: 1px solid #d97706;
    background: #fffbeb;
    padding: 6px 12px;
    border-radius: 2px;
    font-size: 11px;
    font-weight: 600;
    color: #92400e;
    letter-spacing: 0.03em;
    margin-bottom: 8px;
}
div[data-testid="stMetricValue"] {
    font-size: 24px;
    font-weight: 700;
    color: #0f172a !important;
}
div[data-testid="stMetricValue"] * {
    color: #0f172a !important;
}
.stDataFrame {
    font-size: 12px;
}
</style>""", unsafe_allow_html=True)

SETTINGS = Settings.from_env()
STORE = CaseStore(SETTINGS.db_path)

# Accessible status colors and badge rendering
COLORS = {
    State.NO_CHANGE: {"color": "#166534", "bg": "#f0fdf4", "border": "#86efac"},
    State.NOT_ASSESSED: {"color": "#334155", "bg": "#f8fafc", "border": "#94a3b8"},
    State.FAILED: {"color": "#991b1b", "bg": "#fef2f2", "border": "#fca5a5"},
    State.POTENTIAL: {"color": "#854d0e", "bg": "#fefce8", "border": "#fde047"},
    State.HIGH: {"color": "#7f1d1d", "bg": "#fff1f2", "border": "#f43f5e"},
}


def badge(state: State) -> str:
    c = COLORS[state]
    return f"<span class='cs-badge' style='color:{c['color']};background:{c['bg']};border-color:{c['border']}'>{STATE_LABELS[state]}</span>"


def _boundaries(reg, res) -> dict:
    """Registry records to draw for the current result (district + the analysed waterbody, when it came from the registry)."""
    out = {"district": reg.district if reg.district is not None and reg.district.geometry is not None else None, "waterbody": None}
    aid = (res.evidence.get("aoi") or {}).get("id")
    if aid and any(w.id == aid for w in reg.waterbodies):
        out["waterbody"] = reg.waterbody(aid)
    return out


def _render_acquisition(ev: dict) -> None:
    """Visible reason for 'insufficient': what the imagery search found, read, failed and filtered."""
    a = ev.get("acquisition")
    if not a:
        return
    with st.expander("Sentinel-2 acquisition details", expanded=False):
        c = st.columns(5)
        c[0].metric("Found", a.get("found") if a.get("found") is not None else "-")
        c[1].metric("Cloud-filtered", a.get("cloud_filtered") if a.get("cloud_filtered") is not None else "-")
        c[2].metric("Read OK", a.get("read_ok") if a.get("read_ok") is not None else "-")
        c[3].metric("Failed", a.get("failed") if a.get("failed") is not None else "-")
        c[4].metric("In window", a.get("scenes_in_window", "-"))
        st.caption(f"Status: **{a.get('status')}** | cloud limit < {a.get('max_cloud')}% | fetched {a.get('fetched_at')}"
                   + (" | served from a complete cache" if a.get("from_cache_without_query") else ""))
        if a.get("status") == "partial":
            st.warning("Some scenes could not be read. Tick 'Refresh imagery' and run again to retry the failed ones.")
        for e in a.get("errors") or []:
            st.code(f"{e.get('id')}: {e.get('error')}")


def _render_sources(ev: dict) -> None:
    """Per-source state, verdict, counts, dates, provenance and the reason when unavailable."""
    fus = (ev.get("evidence_fusion") or {}).get("sources") or {}
    states = ev.get("evidence_sources") or {}
    if not (fus or states):
        return
    with st.expander("Independent evidence sources", expanded=False):
        for k in ("sentinel1", "dynamic_world", "jrc_gsw"):
            rec, state = fus.get(k) or {}, states.get(k, "not_requested")
            verdict = rec.get("verdict") or rec.get("status") or state
            st.markdown(f"**{k}** - state: `{str(state)[:60]}` | verdict: `{verdict}`")
            if rec.get("reason"):
                st.caption(rec["reason"])
            prov = rec.get("provenance") or {}
            if prov:
                st.json({kk: prov[kk] for kk in list(prov)[:12]}, expanded=False)


def render_workspace(actor: str, settings: Settings = SETTINGS) -> None:
    key = "res_encroachment"
    pipeline = "encroachment"
    st.markdown("<div class='cs-header'>Waterbody Encroachment Workspace — Potential Encroachment Indicators (human review required)</div>", unsafe_allow_html=True)
    reg = registry_mod.Registry.load(settings.registry_dir)

    c0, c1, c3 = st.columns([2.0, 3.4, 1.2])
    target = c0.radio("Analysis target", ["Registry waterbody (Hyderabad pilot)", "Fixed grid (legacy / synthetic)"], key="target")
    study_area, wb_id, scenario, allow_unverified = None, None, "", False
    if target.startswith("Registry"):
        if not reg.waterbodies:
            c1.warning("No waterbody records are registered, so there is nothing to analyse. See the Pilot registry page.")
        else:
            wb_id = c1.selectbox("Waterbody / AOI", [w.id for w in reg.waterbodies],
                                 format_func=lambda i: f"{reg.waterbody(i).name}  [{reg.waterbody(i).status}]",
                                 key="selected_waterbody")
            sel = reg.waterbody(wb_id)
            if not sel.verified:
                allow_unverified = c1.checkbox(f"Allow {sel.status} AOI (the case will be labelled '{sel.status}')", value=False)
        profile = reg.pilot["seasonal_profile"]
        base_params = encroach_params_for(profile=profile)
        st.caption(f"Pilot: {reg.pilot['district_name']} district, {reg.pilot['region']} | seasonal profile: "
                   f"{SEASONAL_PROFILES[profile].label}, wet months {base_params.wet_months} ({'validated' if SEASONAL_PROFILES[profile].validated else 'ASSUMED, not validated'})")
        if settings.mode != "live":
            st.caption("Registry waterbody runs require Live mode; sample fixtures are not used for real AOIs.")
    else:
        keys = list(STUDY_AREAS)
        study_area = c1.selectbox("Fixed study grid", keys, format_func=lambda k: STUDY_AREAS[k]["name"])
        info = STUDY_AREAS[study_area]
        if info.get("synthetic"):
            scenario = c1.selectbox("Synthetic scenario", ["persistent_loss", "seasonal_only", "no_wet_season", "sparse"])
        base_params = encroach_params_for(study_area)
        st.caption(f"{info['desc']} | Grid: EPSG:{info['crs_epsg']} (10 m) | seasonal profile: {info['seasonal_profile']}, "
                   f"wet months {base_params.wet_months} (applied automatically; not overridden by the controls below)")
    synthetic_target = bool(study_area and STUDY_AREAS[study_area].get("synthetic"))
    run = c3.button("Run live analysis", type="primary", key="run_encroachment", width="stretch",
                    disabled=settings.mode != "live" or synthetic_target)
    if settings.mode != "live":
        st.caption("Switch Provider Mode to Live to run an analysis. Sample mode cannot create a real observation case.")
    elif synthetic_target:
        st.caption("Choose a registered waterbody or the real legacy study area; synthetic grids cannot run in Live mode.")
    refresh = True

    with st.expander("Method Parameters & Sensitivity Thresholds", expanded=False):
        a, b, c = st.columns(3)
        params = dataclasses.replace(
            base_params,     # layered ON TOP of the area-specific seasonal parameters
            mndwi_thr=a.slider("MNDWI water threshold", -0.3, 0.3, base_params.mndwi_thr, 0.05, help="Water index threshold (Green - SWIR1)/(Green + SWIR1)"),
            persist=b.slider("Persistence ratio r_min", 0.5, 1.0, base_params.persist, 0.05, help="Fraction of recent observations where pixel must remain dry"),
            min_area_ha=c.number_input("Minimum reportable area (ha)", 0.1, 20.0, base_params.min_area_ha, 0.1, help="Area floor below which result is 'no change'"),
        )

    if run:
        try:
            with st.status("Executing geospatial analysis pipeline...", expanded=True) as status_box:
                def _update_progress(msg, cur=None, tot=None):
                    status_box.write(f"- {msg}")

                if wb_id:
                    res, case_id = workflow.execute_encroachment_for_waterbody(
                        settings, wb_id, params, allow_unverified=allow_unverified,
                        refresh=refresh, progress_callback=_update_progress
                    )
                    st.session_state[key] = (res, case_id)
                elif study_area:
                    res, case_id = workflow.execute_encroachment(settings, scenario, params, study_area=study_area,
                                                                 refresh=refresh, progress_callback=_update_progress)
                    st.session_state[key] = (res, case_id)
                else:
                    st.error("Nothing to run: no analysis target is available.")
                status_box.update(label="Analysis completed successfully!", state="complete", expanded=False)
        except NotConfigured as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"Analysis error: {e}")

    if key not in st.session_state:
        st.info("No active analysis in this workspace. Choose a target and click 'Run analysis'. Each run generates an immutable case record.")
        return

    res, case_id = st.session_state[key]
    ev = res.evidence

    # Two-column layout: Map (left, 60%) and Evidence/Review panel (right, 40%)
    left, right = st.columns([3.2, 2.0], gap="medium")

    with right:
        st.markdown(f"<div class='cs-card'>", unsafe_allow_html=True)
        st.markdown(f"**Case {case_id}** &nbsp; {badge(res.state)}", unsafe_allow_html=True)
        st.write(res.summary)
        st.markdown("</div>", unsafe_allow_html=True)
        _render_acquisition(ev)
        _render_sources(ev)

        # Heuristic Review-Priority Score Card
        st.markdown("<div class='cs-card'>", unsafe_allow_html=True)
        st.markdown("<div class='cs-card-title'>Heuristic Review-Priority Score</div>", unsafe_allow_html=True)
        if res.score is not None:
            m1, m2 = st.columns([1.5, 2.5])
            m1.metric("Priority Score", f"{res.score:.0f} / 100")
            cov = ev.get("score_weight_coverage", 1.0)
            m2.write(f"**Evidence weight coverage:** `{cov:.0%}`")
            m2.caption(res.score_label)
            if ev.get("limited_evidence"):
                st.warning("Evidence coverage is below floor threshold (0.60); heuristic score has been capped at 50.")
            if ev.get("score_components"):
                with st.expander("Inspect Score Components & Available Weights", expanded=False):
                    st.dataframe(pd.DataFrame(ev["score_components"]), hide_index=True, width="stretch")
        else:
            if res.state == State.NO_CHANGE:
                st.write("Score: **None** (No potential change detected above area threshold).")
            elif res.state == State.NOT_ASSESSED:
                st.write("Score: **None** (Not assessed due to data insufficiency or swath gap; unobserved is never proof of no change).")
            else:
                st.write("Score: **None** (Analysis failed).")
        st.markdown("</div>", unsafe_allow_html=True)

        # Cross-Sensor Corroboration Card
        cs_ = ev.get("sensor_corroboration")
        if cs_:
            st.markdown("<div class='cs-card'>", unsafe_allow_html=True)
            st.markdown("<div class='cs-card-title'>Cross-Sensor Corroboration</div>", unsafe_allow_html=True)
            status_tag = cs_.get("status", "unknown").upper()
            tag_color = "#166534" if status_tag == "CORROBORATED" else ("#1e40af" if status_tag == "PARTIAL" else "#475569")
            st.markdown(f"<span class='cs-tag' style='color:{tag_color};font-weight:700'>{status_tag}</span>", unsafe_allow_html=True)
            st.write(cs_.get("reason", ""))
            st.caption("Note: Independent sensor corroboration provides supporting evidence when available; absence of corroboration is not automatic disproof.")
            st.markdown("</div>", unsafe_allow_html=True)

        # Seasonality Safeguard Card (for Encroachment)
        if pipeline == "encroachment":
            st.markdown("<div class='cs-card'>", unsafe_allow_html=True)
            st.markdown("<div class='cs-card-title'>Seasonality Safeguard</div>", unsafe_allow_html=True)
            s_check = ev.get("seasonal_check")
            if s_check == "performed":
                st.markdown("<span class='cs-tag' style='color:#166534;font-weight:700'>ASSESSED</span> Recent observations compared with baseline observations from the same calendar months.", unsafe_allow_html=True)
                st.caption(f"Same-month footprint coverage: {ev.get('seasonal_comparison', {}).get('historical_footprint_coverage', 0):.0%}. Month matching reduces recurring seasonal confounding; it does not measure water level.")
                if ev.get("wet_season_check") != "performed":
                    st.caption("Recent wet-season observations are insufficient; this result cannot receive high-priority status. The seasonal profile is an unvalidated regional assumption.")
            else:
                st.markdown("<span class='cs-tag' style='color:#991b1b;font-weight:700'>INSUFFICIENT</span> Same-month baseline comparison did not meet the coverage requirement.", unsafe_allow_html=True)
                st.caption("The pipeline records this as unassessed; missing seasonal comparisons are not interpreted as no change.")
            st.markdown("</div>", unsafe_allow_html=True)

        # Analytical Indicators
        if ev.get("metrics"):
            with st.expander("Analytical Indicators & Measurements", expanded=True):
                st.dataframe(pd.DataFrame(ev["metrics"]).astype(str), hide_index=True, width="stretch")

        # Observation scenes table
        scenes = ev.get("scenes", [])
        if scenes:
            with st.expander(f"Contributing Observations ({len(scenes)} scenes)", expanded=False):
                st.dataframe(pd.DataFrame(scenes), hide_index=True, width="stretch")

        # Method Limitations
        with st.expander("Method Limitations & Assumptions", expanded=False):
            for x in res.limitations:
                st.markdown(f"- {x}")

    with left:
        if res.synthetic:
            st.markdown("<div class='cs-sample'>SAMPLE MODE: Deterministic synthetic fixture. Not real satellite observations.</div>", unsafe_allow_html=True)

        # Layer controls: each layer is an independent toggle, grouped by purpose
        bnd = _boundaries(reg, res)
        cat = mapview.layer_catalog(res, bnd)
        shown = []
        if res.state in (State.NOT_ASSESSED, State.FAILED):
            with st.expander("Why no result map is available", expanded=False):
                st.caption(f"{ev.get('reason') or res.summary} Observation-coverage layers are hidden by default; this is not a no-change result.")
        with st.expander("Map layers", expanded=False):
            cols = st.columns(len(cat))
            for col, (grp, names) in zip(cols, cat.items()):
                if not names:
                    col.caption(f"**{grp}**: nothing to show" + (" - no usable Sentinel-2 scene produced a context image for this case." if grp == "Source imagery" else "."))
                    continue
                shown += col.multiselect(grp, names, default=mapview.default_layers(res, names), key=f"ly_{grp}")
        op = st.slider("Layer Opacity", 0.1, 1.0, 0.75, 0.05, key="op_encroachment")
        aoi_meta = res.evidence.get("aoi") or {}
        if aoi_meta:
            st.caption(f"AOI: {aoi_meta.get('name')} | status: **{aoi_meta.get('status')}** | kind: {aoi_meta.get('kind')}"
                       + (f" | buffer {aoi_meta['buffer_m']:.0f} m | area {aoi_meta['area_km2']} km²" if aoi_meta.get("area_km2") else ""))

        # Temporal replay
        idx = None
        if scenes:
            st.markdown("<div class='cs-header' style='margin-top:6px'>Temporal Replay (Sensor Passes)</div>", unsafe_allow_html=True)
            labels = [f"{r['acquired'][:10]}{'' if r['usable'] else ' (excluded)'}" for r in scenes]
            pick = st.select_slider("Select pass date", labels, value=labels[-1], key=f"rp_{pipeline}", label_visibility="collapsed")
            idx = labels.index(pick)
            r = scenes[idx]
            role_label = r.get("role", "observation")
            st.caption(f"Observation: **{r['scene_id']}** | Acquired: `{r['acquired'][:16]} UTC` | Valid Coverage: `{r['valid_frac']:.0%}` | Role: `{role_label}` | {'[INCLUDED]' if r['usable'] else '[EXCLUDED - low coverage]'}")

            c_rp1, c_rp2 = st.columns([2, 2])
            show_replay = c_rp1.checkbox("Display replay water layer for this date", value=(mapview.L_REPLAY in shown), key=f"sr_{pipeline}")
            if show_replay and mapview.L_REPLAY not in shown:
                shown = shown + [mapview.L_REPLAY]

        m = mapview.build_map(res, shown, op, idx, bnd)
        st_folium(m, height=580, width="100%", returned_objects=[], key=f"map_{case_id}_{'-'.join(shown)}_{op}_{idx}")

        # Historical water fraction time series (encroachment)
        if ev.get("timeseries"):
            ts = pd.DataFrame(ev["timeseries"]).dropna(subset=["ref_wet_frac"])
            if not ts.empty:
                st.markdown("<div class='cs-header' style='margin-top:10px'>Historical Water Footprint Persistence Series</div>", unsafe_allow_html=True)
                st.caption("Ratio of baseline water footprint water-covered per usable pass (values near 1.0 = full extent, drops = retreat or dry season).")
                st.line_chart(ts.set_index("date")["ref_wet_frac"], height=160)



def render_dashboard(settings: Settings = SETTINGS) -> None:
    """Operational overview. Metrics are derived only from stored cases and registry records."""
    st.title("Change Sentinel")
    st.caption("Waterbody change monitoring · evidence-led screening · human review required")
    reg = registry_mod.Registry.load(settings.registry_dir)
    all_cases = STORE.list_cases(pipeline="encroachment")
    sel = st.radio("Scope", [scope_mod.PILOT, scope_mod.SYNTHETIC, scope_mod.LEGACY], horizontal=True, key="dash_scope",
                   format_func=lambda s: scope_mod.SCOPE_LABELS[s])
    cases = scope_mod.filter_scope(all_cases, sel)
    assessed = [c for c in cases if scope_mod.usable_evidence(c)]
    awaiting = [c for c in cases if scope_mod.awaiting_review(c)]
    verified_aois = [w for w in reg.waterbodies if w.verified]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Cases recorded (this scope)", len(cases))
    m2.metric("Awaiting review", len(awaiting) if sel == scope_mod.PILOT else "n/a")
    m3.metric("Verified AOIs", len(verified_aois))
    m4.metric("Assessments with usable evidence", len(assessed) if sel == scope_mod.PILOT else "n/a")
    if sel != scope_mod.PILOT:
        st.caption("Synthetic and legacy cases are never counted as pilot assessments or as awaiting review.")
    elif len(all_cases) > len(cases):
        st.caption(f"{len(all_cases) - len(cases)} synthetic/legacy case(s) are excluded from these counts.")

    st.markdown("### Operational readiness")
    st.caption("Generated from the registry, the evidence adapters and the recorded run log - not hand-written. "
               "A source is 'tested against remote' only after a recorded successful real run.")
    st.dataframe(pd.DataFrame(scope_mod.readiness_rows(reg, evidence_sources(settings=settings), STORE)).fillna(""),
                 hide_index=True, width="stretch")
    smtp_on = all(os.getenv(k, "").strip() for k in ("CS_SMTP_HOST", "CS_SMTP_FROM"))
    st.caption(f"E-mail: SMTP {'configured' if smtp_on else 'not configured'}; sending is analyst-confirmed and blocked for synthetic, legacy, "
               "unassessed or unreviewed cases. Neo4j mirror: " + ("configured" if os.getenv("CS_NEO4J_URI") else "not configured") +
               ". Authentication: none (the operator label is audit attribution only).")

    st.markdown("### Geographic data readiness")
    if reg.district is None or not reg.district.verified:
        st.warning("Pilot district boundary is not verified. Geographic analysis for the pilot must remain blocked until an authoritative boundary is registered.")
    else:
        st.success(f"District boundary verified: {reg.district.name}")
    if not verified_aois:
        st.warning("No verified waterbody AOIs are available. Register and validate an authoritative waterbody geometry before running a real-area assessment.")
    else:
        st.write(f"{len(verified_aois)} verified waterbody AOI(s) are registered.")

    if reg.blockers():
        with st.expander("Current registry blockers", expanded=True):
            for blocker in reg.blockers():
                st.write(f"- {blocker}")

    st.markdown("### Recent cases")
    if cases:
        recent = pd.DataFrame([{"case_id": c["case_id"], "state": c["state"], "review status": scope_mod.display_status(c),
                                "score": c["score"], "created_at": c["created_at"], "badge": scope_mod.scope_badge(c)} for c in cases[:8]])
        st.dataframe(recent, hide_index=True, width="stretch")
        st.caption("Synthetic cases are visibly identified and must never be represented as real-world observations.")
    else:
        st.info("No cases in this scope. Run an assessment in the Analysis workspace after selecting a valid study area or verified AOI.")

    st.caption("Detections are screening indicators for human review. Unavailable evidence is recorded as unassessed.")


def render_registry(settings: Settings) -> None:
    import folium
    st.markdown("<div class='cs-header'>Pilot Registry — Boundary & Waterbody Provenance</div>", unsafe_allow_html=True)
    reg = registry_mod.Registry.load(settings.registry_dir)
    p = reg.pilot
    st.markdown(f"**Region:** {p['region']} &nbsp;|&nbsp; **Pilot district:** {p['district_name']} (`{p['district_id']}`)")
    st.caption(p["selection_note"])
    for b in reg.blockers():
        st.error(b)
    st.markdown("#### Pilot district boundary")
    if reg.district is None:
        st.warning("No boundary file registered. Nothing is drawn and nothing is treated as verified.")
    else:
        d = reg.district
        st.markdown(f"Status: **{d.status}** | source: {d.source.get('publisher')} - {d.source.get('dataset')} | retrieved {d.source.get('retrieved_utc')} | licence {d.source.get('licence')}")
        st.caption(f"URL: {d.source.get('url')} | sha256 {d.sha256[:16]}...")
        for r in d.reasons:
            st.caption(f"- {r}")
    st.markdown("#### Waterbody / AOI records")
    if reg.waterbodies:
        st.dataframe(pd.DataFrame([{"id": w.id, "name": w.name, "status": w.status, "publisher": w.source.get("publisher"),
                                    "retrieved": w.source.get("retrieved_utc"), "inside district": None if w.containment is None else f"{w.containment:.0%}",
                                    "notes": "; ".join(w.reasons)} for w in reg.waterbodies]), hide_index=True, width="stretch")
    else:
        st.info("None registered.")
    recs = [r for r in ([reg.district] if reg.district else []) + reg.waterbodies if r.geometry is not None]
    if recs:
        s, w_, n, e = None, None, None, None
        bounds = [r.geometry.bounds for r in recs]
        w_, s, e, n = min(b[0] for b in bounds), min(b[1] for b in bounds), max(b[2] for b in bounds), max(b[3] for b in bounds)
        m = folium.Map(location=[(s + n) / 2, (w_ + e) / 2], zoom_start=11, tiles="OpenStreetMap", control_scale=True)
        for r in recs:
            mapview._record_layer(r, r.name, r.kind == "waterbody").add_to(m)
        m.fit_bounds([[s, w_], [n, e]])
        st_folium(m, height=420, width="100%", returned_objects=[], key="registry_map")
    st.markdown("#### Evidence source readiness")
    st.dataframe(pd.DataFrame(scope_mod.readiness_rows(reg, evidence_sources(settings=settings), STORE)[2:]).fillna(""),
                 hide_index=True, width="stretch")
    st.caption("Generated from adapter status and the recorded run log. Sources that are unavailable contribute no evidence and are never treated as negative evidence.")
    with st.expander("How to register official data"):
        st.code("python -m cs.registry add-district --file <official_file> --field <name_column> --value Hyderabad \\\n"
                "    --name Hyderabad --publisher \"<publisher>\" --dataset \"<dataset title>\" --url <download page URL> \\\n"
                "    --licence \"<licence>\" --retrieved YYYY-MM-DD\n"
                "python -m cs.registry add-waterbody --file <file> --id <slug> --name \"<lake name>\" ... (same source options)\n"
                "python -m cs.registry status", language="bash")


def render_queue(actor: str) -> None:
    st.markdown("<div class='cs-header'>Reviewer Verification Queue</div>", unsafe_allow_html=True)

    c1, c2, c3 = st.columns([1.5, 2.5, 1.5])
    available_pipelines = sorted({case["pipeline"] for case in STORE.list_cases()})
    pl = c1.selectbox("Pipeline", ["(all)"] + available_pipelines, index=0)
    pl = None if pl == "(all)" else pl
    states = c2.multiselect(
        "Filter by State",
        [s.value for s in State],
        default=[s.value for s in State]
    )
    status = c3.selectbox("Filter by Review Status", ["(any)"] + list(STATUSES))

    scope_sel = st.radio("Scope", ["all", scope_mod.PILOT, scope_mod.SYNTHETIC, scope_mod.LEGACY], horizontal=True, key="queue_scope",
                         format_func=lambda s: "All (mixed - not for counting)" if s == "all" else scope_mod.SCOPE_LABELS[s])
    cases = scope_mod.filter_scope(STORE.list_cases(pl, states or None, None if status == "(any)" else status), scope_sel)
    df = filter_queue_by_case_id(queue_table(cases, pl),
                                 st.text_input("Search cases by ID", placeholder="e.g. ENC-0003", key="case_id_search"))

    if df.empty:
        st.info("No cases match the selected filters. Adjust the filters or run an analysis to create a case record.")
        return

    st.caption("Sorted strictly by analysis state, then heuristic review-priority score descending.")
    st.dataframe(df, hide_index=True, width="stretch")

    with st.expander("Ranking Rationale (how cases are ordered)", expanded=False):
        st.markdown(f"_{RANKING_RATIONALE}_")

    st.markdown("---")
    st.markdown("<div class='cs-header'>Case Review & Decision Console</div>", unsafe_allow_html=True)

    cid = st.selectbox("Select Case to Inspect", df["case_id"].tolist())
    c = STORE.get_case(cid)

    jump_col, info_col = st.columns([1.35, 3.65])
    if jump_col.button("Open Analysis Workspace →", key=f"open_analysis_{cid}", type="primary", width="stretch"):
        st.session_state["target"] = "Registry waterbody (Hyderabad pilot)"
        st.session_state["page_nav"] = "Analysis workspace"
        st.rerun()
    info_col.caption("Run a fresh satellite assessment from the Analysis workspace. This creates a new immutable case; it does not overwrite this case.")

    c_left, c_right = st.columns([3, 2], gap="large")

    with c_left:
        st.markdown(f"### Case {cid} &nbsp; {badge(State(c['state']))}", unsafe_allow_html=True)
        st.markdown(f"**Review status:** `{scope_mod.display_status(c)}` &nbsp;|&nbsp; **Scope:** `{scope_mod.SCOPE_LABELS[scope_mod.case_scope(c)]}` &nbsp;|&nbsp; **Pipeline:** `{c['pipeline']}` &nbsp;|&nbsp; **Created:** `{c['created_at']} UTC`")
        if c["synthetic"]:
            st.markdown("<div class='cs-sample'>SAMPLE DATA: Synthetic input fixture.</div>", unsafe_allow_html=True)
        st.write(c["summary"])
        st.caption("Sarvam AI generates a native-language companion report. Original evidence and measurements remain unchanged.")
        with st.expander("Case data and evidence (full record)", expanded=False):
            full_record = dict(c)
            for field in ("geometry", "limitations", "params", "evidence", "exports"):
                value = full_record.get(field)
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except (TypeError, ValueError):
                        pass
                full_record[field] = value
            st.json(full_record, expanded=False)

        exp = json.loads(c["exports"])
        baseline_cmp = Path(exp.get("baseline_matched_frequency_tif", ""))
        recent_cmp = Path(exp.get("recent_matched_frequency_tif", ""))
        if baseline_cmp.is_file() and recent_cmp.is_file():
            st.markdown("#### Georeferenced before-and-after water-frequency comparison")
            st.caption("Observed Sentinel-2 water frequency for calendar months shared by both periods. Both layers retain the exported CRS, transform, and pixel grid; this is derived evidence, not raw optical imagery.")
            try:
                comparison = mapview.build_frequency_comparison_map(baseline_cmp, recent_cmp)
                st_folium(comparison, height=500, width="100%", returned_objects=[], key=f"frequency_comparison_{cid}")
            except (ValueError, OSError) as exc:
                st.warning(f"Georeferenced comparison is unavailable: {exc}")
        preview_path = Path(exp.get("preview_png", ""))
        if preview_path.is_file():
            st.markdown("#### Raster analysis preview")
            st.image(str(preview_path), caption="Analysis-grid preview · not georeferenced", width="stretch")

        # Explicit human-review completion for insufficient-evidence cases.
        # This records a reviewer decision; it deliberately does NOT rewrite the immutable algorithm state.
        if c["state"] == State.NOT_ASSESSED.value:
            prior_decisions = STORE.decisions(cid)
            already_recorded = any(d.get("decision") == "insufficient_evidence" for d in prior_decisions)
            with st.container(border=True):
                st.markdown("#### Assessment outcome")
                st.warning("The algorithm could not assess this case because usable imagery did not meet the required coverage. A reviewer may close the review as **insufficient evidence**, but cannot manually convert this into a successful satellite assessment.")
                if already_recorded:
                    st.success("Human review outcome already recorded: insufficient evidence. The analysis state remains not_assessed.")
                else:
                    with st.form(f"complete_insufficient_review_{cid}"):
                        review_rationale = st.text_area("Reviewer rationale (recommended)", placeholder="Example: Reviewed the available imagery; baseline coverage is insufficient to determine waterbody change.")
                        confirm_review = st.checkbox("I inspected the available evidence and agree this case cannot be assessed with the current imagery.", key=f"confirm_insufficient_{cid}")
                        if st.form_submit_button("Complete review — insufficient evidence", type="primary"):
                            if not confirm_review:
                                st.error("Please confirm that you inspected the available evidence.")
                            else:
                                STORE.set_status(cid, "reviewed", actor)
                                STORE.add_decision(cid, actor, "insufficient_evidence", review_rationale.strip() or "Reviewed; insufficient usable imagery to determine change.")
                                st.success("Review completed. The case is recorded as reviewed — insufficient evidence; its algorithm state remains not_assessed.")
                                st.rerun()

        # Reviewer Action Forms
        st.markdown("#### Reviewer Actions")
        tab_status, tab_note, tab_decision = st.tabs(["Update Status", "Add Internal Note", "Record Formal Decision"])

        with tab_status:
            with st.form(f"form_st_{cid}"):
                ns = st.selectbox("Set Review Status", STATUSES, index=STATUSES.index(c["status"]))
                if st.form_submit_button("Update Status"):
                    STORE.set_status(cid, ns, actor)
                    st.success(f"Status updated to '{ns}'.")
                    st.rerun()

        with tab_note:
            with st.form(f"form_note_{cid}"):
                note_text = st.text_area("Reviewer Note", placeholder="Record observations, boundary ambiguities, or verification rationale...")
                if st.form_submit_button("Save Reviewer Note"):
                    if note_text.strip():
                        STORE.add_note(cid, actor, note_text.strip())
                        st.success("Reviewer note recorded in audit trail.")
                        st.rerun()
                    else:
                        st.error("Note text cannot be empty.")

        with tab_decision:
            with st.form(f"form_dec_{cid}"):
                st.caption("Reviewer decisions are stored in an append-only table. The original sensor evidence is immutable and never rewritten.")
                d = st.selectbox("Decision", DECISIONS)
                dec_note = st.text_area("Decision Rationale", placeholder="Provide technical justification for this determination...")
                if st.form_submit_button("Commit Formal Decision"):
                    STORE.add_decision(cid, actor, d, dec_note.strip())
                    st.success(f"Decision '{d}' committed to case record.")
                    st.rerun()

    with c_right:
        # Export and Download Center
        st.markdown("#### Export & Download Center")
        st.markdown("<div class='cs-card'>", unsafe_allow_html=True)
        st.markdown("<div class='cs-card-title'>Geospatial Outputs & Reports</div>", unsafe_allow_html=True)

        # PDF generation & download
        pdf_path = SETTINGS.output_dir / cid / f"{cid}_case_file.pdf"
        if st.button("Generate / Refresh PDF Case File", key=f"gen_pdf_{cid}"):
            build_pdf(STORE, cid, pdf_path)
            st.session_state[f"pdf_{cid}"] = pdf_path.read_bytes()

        if pdf_path.exists() and f"pdf_{cid}" not in st.session_state:
            st.session_state[f"pdf_{cid}"] = pdf_path.read_bytes()

        if f"pdf_{cid}" in st.session_state:
            st.download_button(
                "📥 Download PDF Case File",
                st.session_state[f"pdf_{cid}"],
                file_name=f"{cid}_case_file.pdf",
                mime="application/pdf",
                width="stretch"
            )

        # Sarvam AI native-language report + officer email workflow
        st.markdown("#### Bilingual Officer Report (Sarvam AI)")
        language_options = ["Telugu", "Hindi", "Marathi", "Tamil", "Kannada", "Bengali", "Gujarati", "Malayalam", "Punjabi", "Odia", "Urdu"]
        language_name = st.selectbox("Native report language", language_options, index=0, key=f"native_lang_{cid}")
        st.caption("Native-language text is MACHINE-TRANSLATED (Sarvam AI); the English PDF is authoritative.")
        native_path = SETTINGS.output_dir / cid / f"{cid}_{LANGUAGES[language_name]}_report.docx"
        if st.button("Generate native-language report with Sarvam AI", key=f"sarvam_report_{cid}"):
            try:
                with st.spinner("Generating machine-translated report with Sarvam AI..."):
                    build_native_report(STORE, cid, native_path, language_name, LANGUAGES[language_name])
                st.session_state[f"native_report_{cid}"] = native_path.read_bytes()
                st.success(f"{language_name} report generated (machine-translated).")
            except Exception as exc:
                st.error(f"Sarvam report generation failed: {exc}")
        if native_path.exists():
            st.download_button("Download native-language report (machine-translated)", native_path.read_bytes(),
                file_name=native_path.name,
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key=f"download_native_{cid}")
        blocks = scope_mod.email_block_reasons(c, STORE.decisions(cid))
        officer_email = st.text_input("Officer e-mail (type the recipient; nothing is pre-filled)", value="", key=f"officer_email_{cid}",
                                      placeholder="name@example.gov.in", disabled=bool(blocks))
        if blocks:
            st.warning("E-mail sending is disabled for this case: " + "; ".join(blocks) + ".")
        confirmed = st.checkbox("I have reviewed the report and confirm this recipient may receive it", key=f"email_confirm_{cid}", disabled=bool(blocks))
        if st.button("Send English PDF + native report to officer", key=f"send_report_{cid}", type="primary", disabled=bool(blocks)):
            if not pdf_path.exists():
                st.error("Generate the English PDF case file first.")
            else:
                extra = [str(native_path)] if native_path.exists() else []
                ok, message = guarded_send(STORE, c, actor, officer_email, confirmed, str(pdf_path), extra)
                (st.success if ok else st.error)(message)

        # GeoTIFF change mask download
        tif_mask = Path(exp.get("change_mask_tif", ""))
        if tif_mask.exists():
            st.download_button(
                "📥 Download Change Mask (GeoTIFF)",
                tif_mask.read_bytes(),
                file_name=f"{cid}_change_mask.tif",
                mime="image/tiff",
                width="stretch"
            )

        # GeoTIFF coverage download
        tif_cov = Path(exp.get("coverage_tif", ""))
        if tif_cov.exists():
            st.download_button(
                "📥 Download Coverage Grid (GeoTIFF)",
                tif_cov.read_bytes(),
                file_name=f"{cid}_coverage.tif",
                mime="image/tiff",
                width="stretch"
            )

        # GeoJSON polygons download
        geojson_poly = Path(exp.get("polygons_geojson", ""))
        if geojson_poly.exists():
            st.download_button(
                "📥 Download Vector Polygons (GeoJSON EPSG:4326)",
                geojson_poly.read_text(encoding="utf-8"),
                file_name=f"{cid}_polygons.geojson",
                mime="application/geo+json",
                width="stretch"
            )

        # Structured metadata JSON download
        ev_json = Path(exp.get("evidence_json", ""))
        if ev_json.exists():
            st.download_button(
                "📥 Download Evidence Metadata (JSON)",
                ev_json.read_text(encoding="utf-8"),
                file_name=f"{cid}_evidence.json",
                mime="application/json",
                width="stretch"
            )
        st.markdown("</div>", unsafe_allow_html=True)

        # Reviewer Notes History
        notes_list = STORE.notes(cid)
        st.markdown(f"#### Reviewer Notes ({len(notes_list)})")
        if notes_list:
            st.dataframe(pd.DataFrame(notes_list)[["ts", "reviewer", "note"]], hide_index=True, width="stretch")
        else:
            st.caption("No internal notes recorded yet.")

        # Reviewer Decisions History
        decisions_list = STORE.decisions(cid)
        st.markdown(f"#### Formal Decisions ({len(decisions_list)})")
        if decisions_list:
            st.dataframe(pd.DataFrame(decisions_list)[["decided_at", "reviewer", "decision", "note"]], hide_index=True, width="stretch")
        else:
            st.caption("No formal determinations committed yet.")

        # Audit Trail
        audit_list = STORE.audit(cid)
        with st.expander(f"Append-Only Audit Trail ({len(audit_list)} entries)", expanded=False):
            st.dataframe(pd.DataFrame(audit_list)[["ts", "actor", "action", "detail"]], hide_index=True, width="stretch")


def render_methods() -> None:
    st.markdown("<div class='cs-header'>Methodology, Standards & System Status</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='cs-note'><b>Statutory & Regulatory Notice:</b> {DISCLAIMER}</div>", unsafe_allow_html=True)
    st.markdown("""
### One encroachment pipeline, three evidence sources
The product runs **one** potential-encroachment pipeline for the Hyderabad pilot. Sentinel-1 SAR, Dynamic World land cover
and JRC Global Surface Water are *evidence sources inside it*, not separate detectors. (The earlier flood/drought workflows were removed.)

**Sentinel-2 L2A MNDWI persistence (the detector; see the readiness table for what has actually been run live):**
- SCL mask for cloud/shadow/invalid pixels; L2A -1000 DN BOA offset removed before reflectance; MNDWI = (Green - SWIR1)/(Green + SWIR1).
- Historical footprint from baseline water frequency; per-pixel persistence of dryness in the recent window.
- **Seasonality safeguard:** wet months come from the area's declared seasonal profile (currently ASSUMED regime defaults, not validated).
- Classes E1/E2 (persistent loss / bare-built transition), E3 (seasonal, suppressed), E4 (insufficient evidence).
- Outputs are *potential encroachment indicators* for human and field review. They are not findings of illegality, ownership or intent.

**Evidence sources:** Sentinel-1 RTC, Dynamic World and JRC GSW are independent evidence sources. Each yields a verdict (supports / neutral / contradicts / insufficient / unavailable) with provenance; none adds an uncalibrated numeric weight. A *contradicts* verdict caps the score at 50 and blocks high priority; a registry run with no independent support is capped at 50. JRC is a historical reference (its coverage period and the gap to the recent window are recorded), not a live observation. Unavailable sources are never treated as negative evidence.

### Geometry
Analysis grids are projected-metre UTM grids derived from the AOI polygon. Rasters are reprojected with GDAL warps and clipped to the
polygon; map overlays are warped to Web Mercator so they align with the basemap. Boundary status (verified/provisional/unverified/invalid)
is computed from recorded provenance + checksum + geometry checks.

### Scores
Heuristic review-priority scores (0-100), unavailable evidence earns no score weight and coverage below 60% is capped at 50. Not probabilities.
""")
    st.markdown("### System Capability Matrix")
    st.caption("Live status rows are generated from the registry, adapter status and the recorded run log.")
    st.dataframe(pd.DataFrame(scope_mod.readiness_rows(registry_mod.Registry.load(ACTIVE_SETTINGS.registry_dir),
                                                       evidence_sources(settings=ACTIVE_SETTINGS), STORE)).fillna(""), hide_index=True, width="stretch")
    st.dataframe(pd.DataFrame([
        ["CRS handling, UTM grid from polygon, polygon clipping, warped map overlays", "Unit-tested"],
        ["Case management, audit trail, PDF case file, GeoTIFF/GeoJSON/JSON exports", "Preserved and unit-tested"],
        ["Authentication and role permissions", "Not implemented; operator label is audit attribution only"],
        ["DEM / HAND masks, OSM exposure join, threshold calibration", "Not implemented (python -m cs.calibration reports detections vs reviewer decisions)"],
    ], columns=["Capability", "Implementation Status"]), hide_index=True, width="stretch")
    st.caption("Authentication and role-based permissions are also a later milestone. The current operator label is for local audit attribution only, not identity verification.")


with st.sidebar:
    st.markdown("### Change Sentinel")
    st.caption("Waterbody Encroachment Monitoring - Hyderabad pilot")
    page = st.radio("Navigation", ["Dashboard", "Pilot registry", "Analysis workspace", "Case management", "Methods and readiness"], label_visibility="collapsed", key="page_nav")
    actor = st.text_input("Local operator label", "local_analyst", help="Audit attribution only. This is not a login or authentication mechanism.")
    st.divider()

    st.markdown("**Data Source Mode**")
    mode_choice = st.radio("Provider Mode", ["Sample (Synthetic Fixtures)", "Live (Current Imagery + Evidence)"],
                           index=0 if SETTINGS.mode == "sample" else 1,
                           help="Sample uses deterministic test fixtures. Live queries current imagery and attempts the integrated independent evidence sources.")
    current_mode = "live" if "Live" in mode_choice else "sample"
    ACTIVE_SETTINGS = dataclasses.replace(SETTINGS, mode=current_mode)
    if current_mode == "sample":
        st.markdown("<div class='cs-sample'>MODE: SAMPLE (Synthetic Fixtures)</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='cs-badge' style='color:#166534;background:#f0fdf4;border-color:#86efac;margin-bottom:6px'>MODE: LIVE (Real Sentinel-2 Data)</div>", unsafe_allow_html=True)
        st.caption("Each run refreshes Sentinel-2, Sentinel-1, Dynamic World and JRC catalog/composite data when available. JRC is historical reference data.")

    st.caption(f"Database: `{SETTINGS.db_path.name}`")
    st.caption(f"Exports: `{SETTINGS.output_dir.name}/`")
    st.divider()
    st.caption("Satellite-derived indication of potential encroachment for human review. Contains modified Copernicus Sentinel data.")

if page == "Dashboard":
    render_dashboard(ACTIVE_SETTINGS)
elif page == "Pilot registry":
    render_registry(ACTIVE_SETTINGS)
elif page == "Analysis workspace":
    render_workspace(actor, ACTIVE_SETTINGS)
elif page == "Case management":
    render_queue(actor)
else:
    render_methods()


# Optional Neo4j graph access panel (added by neo4j-extension).
from cs.neo4j_dashboard_extension import render_neo4j_panel
with st.expander("Neo4j Knowledge Graph", expanded=False):
    render_neo4j_panel()
