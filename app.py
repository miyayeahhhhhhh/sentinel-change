"""Change Sentinel - Streamlit GIS Monitoring Console. UI only: all analysis lives in cs/*."""
from __future__ import annotations
import dataclasses
import json
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from cs import mapview, workflow
from cs.config import EncroachParams, FloodParams, Settings
from cs.db import DECISIONS, STATUSES, CaseStore
from cs.imagery import NotConfigured
from cs.models import DISCLAIMER, STATE_LABELS, State
from cs.priority import queue_table
from cs.report import build_pdf
from cs.sample_data import STUDY_AREAS

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

SCENARIOS = {
    "flood": {
        "flood_event": "Synthetic: flood event present (monsoon surge)",
        "no_flood": "Synthetic: normal baseline conditions (no flood)",
        "insufficient": "Synthetic: too few reference scenes (unassessed)",
        "partial_coverage": "Synthetic: event scenes mostly unobserved / swath gaps",
    },
    "encroachment": {
        "persistent_loss": "Synthetic: persistent shoreline loss (fill) + seasonal fringe",
        "seasonal_only": "Synthetic: seasonal contraction only (suppressed from flagged)",
        "no_wet_season": "Synthetic: loss detected, but no wet-season observations (seasonality caution)",
        "sparse": "Synthetic: too few recent observations (unassessed)",
    },
}


def badge(state: State) -> str:
    c = COLORS[state]
    return f"<span class='cs-badge' style='color:{c['color']};background:{c['bg']};border-color:{c['border']}'>{STATE_LABELS[state]}</span>"


def render_workspace(pipeline: str, actor: str) -> None:
    key = f"res_{pipeline}"
    title = "Sentinel-1 SAR Flood Mapping Console" if pipeline == "flood" else "Sentinel-2 L2A Water-Body Encroachment Console"
    st.markdown(f"<div class='cs-header'>{title}</div>", unsafe_allow_html=True)

    # Control bar: Study Area, Scenario, Run
    c1, c2, c3 = st.columns([2.2, 3.2, 1.2])
    study_area = c1.selectbox("Study Area (AOI)", list(STUDY_AREAS.keys()), format_func=lambda k: STUDY_AREAS[k]["name"], key=f"sa_{pipeline}")
    scenario = c2.selectbox("Observation Scenario (Sample mode)", list(SCENARIOS[pipeline]), format_func=SCENARIOS[pipeline].get, key=f"sc_{pipeline}")
    run = c3.button("Run analysis", type="primary", key=f"run_{pipeline}", use_container_width=True)

    sa_info = STUDY_AREAS[study_area]
    st.caption(f"AOI: {sa_info['name']} | Grid: EPSG:{sa_info['crs_epsg']} (10 m UTM projected metres) | Context: {sa_info['desc']}")

    with st.expander("Method Parameters & Sensitivity Thresholds", expanded=False):
        if pipeline == "flood":
            a, b, c = st.columns(3)
            params = dataclasses.replace(
                FloodParams(),
                water_db=a.slider("Water threshold VV (dB)", -25.0, -10.0, -18.0, 0.5, help="Specular water backscatter upper threshold"),
                diff_db=b.slider("Required drop vs reference (dB)", 1.0, 8.0, 3.0, 0.5, help="Minimum backscatter attenuation vs baseline"),
                min_area_ha=c.number_input("Minimum reportable area (ha)", 0.1, 20.0, 0.5, 0.1, help="Area floor below which result is 'no change'"),
            )
        else:
            a, b, c = st.columns(3)
            params = dataclasses.replace(
                EncroachParams(),
                mndwi_thr=a.slider("MNDWI water threshold", -0.3, 0.3, 0.0, 0.05, help="Water index threshold (Green - SWIR1)/(Green + SWIR1)"),
                persist=b.slider("Persistence ratio r_min", 0.5, 1.0, 0.8, 0.05, help="Fraction of recent observations where pixel must remain dry"),
                min_area_ha=c.number_input("Minimum reportable area (ha)", 0.1, 20.0, 0.5, 0.1, help="Area floor below which result is 'no change'"),
            )

    if run:
        try:
            with st.spinner("Executing geospatial analysis pipeline..."):
                fn = workflow.execute_flood if pipeline == "flood" else workflow.execute_encroachment
                st.session_state[key] = fn(SETTINGS, scenario, params, study_area=study_area)
        except NotConfigured as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"Analysis error: {e}")

    if key not in st.session_state:
        st.info("No active analysis in this workspace. Select a study area and scenario, then click 'Run analysis'. Each run generates an immutable case record.")
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
                    st.dataframe(pd.DataFrame(ev["score_components"]), hide_index=True, use_container_width=True)
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
                st.markdown("<span class='cs-tag' style='color:#166534;font-weight:700'>VERIFIED</span> Wet-season observations available in recent window.", unsafe_allow_html=True)
                st.caption(f"Seasonal contraction ({ev.get('seasonal_ha', 0):.2f} ha, Class E3) successfully separated and suppressed from flagged loss.")
            else:
                st.markdown("<span class='cs-tag' style='color:#991b1b;font-weight:700'>CAUTION</span> No wet-season observations in recent window.", unsafe_allow_html=True)
                st.caption("Natural seasonal contraction cannot be ruled out. Review priority is capped below high-priority review.")
            st.markdown("</div>", unsafe_allow_html=True)

        # Analytical Indicators
        if ev.get("metrics"):
            with st.expander("Analytical Indicators & Measurements", expanded=True):
                st.dataframe(pd.DataFrame(ev["metrics"]).astype(str), hide_index=True, use_container_width=True)

        # Observation scenes table
        scenes = ev.get("scenes", [])
        if scenes:
            with st.expander(f"Contributing Observations ({len(scenes)} scenes)", expanded=False):
                st.dataframe(pd.DataFrame(scenes), hide_index=True, use_container_width=True)

        # Method Limitations
        with st.expander("Method Limitations & Assumptions", expanded=False):
            for x in res.limitations:
                st.markdown(f"- {x}")

    with left:
        if res.synthetic:
            st.markdown("<div class='cs-sample'>SAMPLE MODE: Deterministic synthetic fixture. Not real satellite observations.</div>", unsafe_allow_html=True)

        # Layer controls
        names = mapview.layer_names(res)
        default = names[:1] + ["Data coverage / gaps"]
        lc1, lc2 = st.columns([3.2, 1.2])
        shown = lc1.multiselect("Active Map Layers", names, default=default, key=f"ly_{pipeline}")
        op = lc2.slider("Layer Opacity", 0.1, 1.0, 0.75, 0.05, key=f"op_{pipeline}")

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
            show_replay = c_rp1.checkbox("Display replay water layer for this date", value=("Replay: water on selected date" in shown), key=f"sr_{pipeline}")
            if show_replay and "Replay: water on selected date" not in shown:
                shown = shown + ["Replay: water on selected date"]

        # Build and render Folium map
        m = mapview.build_map(res, shown, op, idx)
        st_folium(m, height=580, width="100%", returned_objects=[], key=f"map_{pipeline}_{case_id}_{'-'.join(shown)}_{op}_{idx}")

        # Historical water fraction time series (encroachment)
        if ev.get("timeseries"):
            ts = pd.DataFrame(ev["timeseries"]).dropna(subset=["ref_wet_frac"])
            if not ts.empty:
                st.markdown("<div class='cs-header' style='margin-top:10px'>Historical Water Footprint Persistence Series</div>", unsafe_allow_html=True)
                st.caption("Ratio of baseline water footprint observed flooded per usable pass (values near 1.0 = full extent, drops = retreat or dry season).")
                st.line_chart(ts.set_index("date")["ref_wet_frac"], height=160)


def render_queue(actor: str) -> None:
    st.markdown("<div class='cs-header'>Reviewer Verification Queue</div>", unsafe_allow_html=True)

    c1, c2, c3 = st.columns([1.5, 2.5, 1.5])
    pl = c1.radio("Pipeline Queue", ["flood", "encroachment"], horizontal=True)
    states = c2.multiselect(
        "Filter by State",
        [s.value for s in State],
        default=[State.HIGH.value, State.POTENTIAL.value, State.NOT_ASSESSED.value, State.FAILED.value]
    )
    status = c3.selectbox("Filter by Review Status", ["(any)"] + list(STATUSES))

    cases = STORE.list_cases(pl, states, None if status == "(any)" else status)
    df = queue_table(cases, pl)

    if df.empty:
        st.info("No cases match the selected filters. Run an analysis in the Flood or Encroachment workspace to create a case record. Absence of cases is not proof of no change.")
        return

    st.caption("Queues are maintained independently. Sorted strictly by analysis state, then heuristic review-priority score descending.")
    st.dataframe(df, hide_index=True, use_container_width=True)

    st.markdown("---")
    st.markdown("<div class='cs-header'>Case Review & Decision Console</div>", unsafe_allow_html=True)

    cid = st.selectbox("Select Case to Inspect", df["case_id"].tolist())
    c = STORE.get_case(cid)

    c_left, c_right = st.columns([3, 2], gap="large")

    with c_left:
        st.markdown(f"### Case {cid} &nbsp; {badge(State(c['state']))}", unsafe_allow_html=True)
        st.markdown(f"**Status:** `{c['status']}` &nbsp;|&nbsp; **Pipeline:** `{c['pipeline']}` &nbsp;|&nbsp; **Created:** `{c['created_at']} UTC`")
        if c["synthetic"]:
            st.markdown("<div class='cs-sample'>SAMPLE DATA: Synthetic input fixture.</div>", unsafe_allow_html=True)
        st.write(c["summary"])

        exp = json.loads(c["exports"])
        preview_path = Path(exp.get("preview_png", ""))
        if preview_path.exists():
            st.image(str(preview_path), caption=f"Raster Analysis Grid Preview (Red = Flagged change, Grey = Unobserved)", width=420)

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
                use_container_width=True
            )

        # GeoTIFF change mask download
        tif_mask = Path(exp.get("change_mask_tif", ""))
        if tif_mask.exists():
            st.download_button(
                "📥 Download Change Mask (GeoTIFF)",
                tif_mask.read_bytes(),
                file_name=f"{cid}_change_mask.tif",
                mime="image/tiff",
                use_container_width=True
            )

        # GeoTIFF coverage download
        tif_cov = Path(exp.get("coverage_tif", ""))
        if tif_cov.exists():
            st.download_button(
                "📥 Download Coverage Grid (GeoTIFF)",
                tif_cov.read_bytes(),
                file_name=f"{cid}_coverage.tif",
                mime="image/tiff",
                use_container_width=True
            )

        # GeoJSON polygons download
        geojson_poly = Path(exp.get("polygons_geojson", ""))
        if geojson_poly.exists():
            st.download_button(
                "📥 Download Vector Polygons (GeoJSON EPSG:4326)",
                geojson_poly.read_text(encoding="utf-8"),
                file_name=f"{cid}_polygons.geojson",
                mime="application/geo+json",
                use_container_width=True
            )

        # Structured metadata JSON download
        ev_json = Path(exp.get("evidence_json", ""))
        if ev_json.exists():
            st.download_button(
                "📥 Download Evidence Metadata (JSON)",
                ev_json.read_text(encoding="utf-8"),
                file_name=f"{cid}_evidence.json",
                mime="application/json",
                use_container_width=True
            )
        st.markdown("</div>", unsafe_allow_html=True)

        # Reviewer Notes History
        notes_list = STORE.notes(cid)
        st.markdown(f"#### Reviewer Notes ({len(notes_list)})")
        if notes_list:
            st.dataframe(pd.DataFrame(notes_list)[["ts", "reviewer", "note"]], hide_index=True, use_container_width=True)
        else:
            st.caption("No internal notes recorded yet.")

        # Reviewer Decisions History
        decisions_list = STORE.decisions(cid)
        st.markdown(f"#### Formal Decisions ({len(decisions_list)})")
        if decisions_list:
            st.dataframe(pd.DataFrame(decisions_list)[["decided_at", "reviewer", "decision", "note"]], hide_index=True, use_container_width=True)
        else:
            st.caption("No formal determinations committed yet.")

        # Audit Trail
        audit_list = STORE.audit(cid)
        with st.expander(f"Append-Only Audit Trail ({len(audit_list)} entries)", expanded=False):
            st.dataframe(pd.DataFrame(audit_list)[["ts", "actor", "action", "detail"]], hide_index=True, use_container_width=True)


def render_methods() -> None:
    st.markdown("<div class='cs-header'>Methodology, Standards & System Status</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='cs-note'><b>Statutory & Regulatory Notice:</b> {DISCLAIMER}</div>", unsafe_allow_html=True)

    st.markdown("""
### Analytical Pipeline Architecture

The platform operates two **independent** observation pipelines sharing only common geospatial storage, export utilities, and the reviewer case engine:

1. **Flood Inundation Mapping (Sentinel-1 SAR VV dB):**
   - Quality control filters physically implausible dB values (-40 to +5 dB).
   - Generates temporal nanmedian reference mosaic vs event median mosaic.
   - Detects specular surface attenuation (VV < threshold and drop vs reference > diff_db).
   - Permanent water bodies (F2) are explicitly excluded using reference baseline backscatter.
   - Morphological opening cleans speckle noise, followed by minimum mapping unit connected component filtering.
   - Outputs classes: **F1** (Potential floodwater), **F2** (Permanent water excluded), **F3** (Unobserved/masked), **F4** (No data/unassessed).

2. **Water-Body Encroachment & Loss (Sentinel-2 L2A MNDWI Persistence):**
   - Applies Scene Classification Layer (SCL) masking to exclude clouds, cirrus, cloud shadows, and invalid pixels.
   - Evaluates MNDWI = (Green - SWIR1) / (Green + SWIR1).
   - Establishes historical baseline water footprint from multi-year water frequency (>= 0.25).
   - Evaluates per-pixel persistence across recent observation window (dry fraction >= 0.80, minimum observations, minimum span).
   - **Seasonality Safeguard:** Requires wet-season recent observations to distinguish persistent filling/loss (E1/E2) from natural dry-season contraction (E3).
   - Evaluates mean SWIR1 reflectance (> 0.20 indicates bare ground, dry fill, or construction transition E2).
   - Outputs classes: **E1** (Persistent extent loss), **E2** (Bare/built transition), **E3** (Seasonal contraction - suppressed), **E4** (Insufficient observations).

### Evidence Scoring & Review Priority
Heuristic review-priority scores (0–100) are computed as weighted sums over **available** evidence components with renormalised weights.
**Scores are heuristic review priorities, not probabilities.**
When available evidence coverage is below 60%, the score is capped at 50 to prevent overstating uncorroborated detections.

### Cross-Sensor Corroboration
When coincident observations exist across sensors, independent supporting evidence is presented:
- Optical MNDWI verification for SAR flood extents.
- SAR backscatter roughness verification for optical encroachment extents.
**Absence of cross-sensor corroboration is not automatic disproof** due to cloud cover, vegetation canopy, or timing differences.
""")

    st.markdown("### System Capability Matrix")
    st.dataframe(pd.DataFrame([
        ["Sentinel-1 SAR flood pipeline (F1-F4, QC, diff, area)", "Operational (Deterministic Sample Fixture)"],
        ["Sentinel-2 L2A encroachment pipeline (E1-E4, MNDWI, persistence)", "Operational (Deterministic Sample Fixture)"],
        ["Seasonality safeguard (wet-season isolation & suppression of E3)", "Operational"],
        ["Cross-sensor corroboration (independent optical & SAR validation)", "Operational (Sample Fixtures)"],
        ["Temporal replay with observation metadata and water layer", "Operational"],
        ["Data coverage & gap mapping (nodata propagation)", "Operational"],
        ["Reviewer decision, standalone notes, and append-only audit trail", "Operational"],
        ["Geospatial exports (GeoTIFF nodata=255, GeoJSON EPSG:4326, JSON metadata)", "Operational"],
        ["PDF Case File generation (ReportLab with maps, metrics, tables)", "Operational"],
        ["Live satellite STAC / Earth Engine automated ingestion", "Scaffolded (see README for Earth Engine/CDSE setup)"],
        ["DEM / HAND hydraulic and terrain shadow masks", "Roadmap / Scaffolded"],
        ["OpenStreetMap infrastructure exposure join", "Roadmap / Scaffolded"],
    ], columns=["Capability", "Implementation Status"]), hide_index=True, use_container_width=True)


with st.sidebar:
    st.markdown("### Change Sentinel")
    st.caption("Geospatial Change Monitoring & Review Console")
    page = st.radio("Navigation", ["Flood workspace", "Encroachment workspace", "Review queues", "Methods and status"], label_visibility="collapsed")
    actor = st.text_input("Reviewer Identity", "analyst_reviewer")
    st.divider()

    st.markdown("**Environment Configuration**")
    if SETTINGS.mode == "sample":
        st.markdown("<div class='cs-sample'>MODE: SAMPLE (Synthetic Fixtures)</div>", unsafe_allow_html=True)
    else:
        st.warning("Live mode requested. Live adapters require configured external credentials.")

    st.caption(f"Database: `{SETTINGS.db_path.name}`")
    st.caption(f"Exports: `{SETTINGS.output_dir.name}/`")
    st.divider()
    st.caption("Satellite-derived indication for human review. Contains modified Copernicus Sentinel data (when real data is configured).")

if page == "Flood workspace":
    render_workspace("flood", actor)
elif page == "Encroachment workspace":
    render_workspace("encroachment", actor)
elif page == "Review queues":
    render_queue(actor)
else:
    render_methods()

