from pathlib import Path
import sys

APP = Path(sys.argv[1] if len(sys.argv) > 1 else "app.py")
text = APP.read_text(encoding="utf-8")
original = text

def replace_once(old, new, label):
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Cannot safely apply {label}: expected exactly 1 match, found {count}. No file written.")
    text = text.replace(old, new, 1)

replace_once(
    'page = st.radio("Navigation", ["Dashboard", "Pilot registry", "Analysis workspace", "Case management", "Methods and readiness"], label_visibility="collapsed")',
    'page = st.radio("Navigation", ["Dashboard", "Pilot registry", "Analysis workspace", "Case management", "Methods and readiness"], label_visibility="collapsed", key="page_nav")',
    "stable navigation key")

replace_once(
    'wb_id = c1.selectbox("Waterbody / AOI", [w.id for w in reg.waterbodies],\n                                 format_func=lambda i: f"{reg.waterbody(i).name}  [{reg.waterbody(i).status}]")',
    'wb_id = c1.selectbox("Waterbody / AOI", [w.id for w in reg.waterbodies],\n                                 format_func=lambda i: f"{reg.waterbody(i).name}  [{reg.waterbody(i).status}]",\n                                 key="selected_waterbody")',
    "addressable waterbody selector")

replace_once(
    'cid = st.selectbox("Select Case to Inspect", df["case_id"].tolist())\n    c = STORE.get_case(cid)\n\n    c_left, c_right = st.columns([3, 2], gap="large")',
    'cid = st.selectbox("Select Case to Inspect", df["case_id"].tolist())\n    c = STORE.get_case(cid)\n\n    jump_col, info_col = st.columns([1.35, 3.65])\n    if jump_col.button("Open Analysis Workspace →", key=f"open_analysis_{cid}", type="primary", width="stretch"):\n        st.session_state["target"] = "Registry waterbody (Hyderabad pilot)"\n        st.session_state["page_nav"] = "Analysis workspace"\n        st.rerun()\n    info_col.caption("Run a fresh satellite assessment from the Analysis workspace. This creates a new immutable case; it does not overwrite this case.")\n\n    c_left, c_right = st.columns([3, 2], gap="large")',
    "case-to-analysis navigation")

needle = '        # Reviewer Action Forms\n        st.markdown("#### Reviewer Actions")\n        tab_status, tab_note, tab_decision = st.tabs(["Update Status", "Add Internal Note", "Record Formal Decision"])'
replacement = '''        # Explicit human-review completion for insufficient-evidence cases.
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
        tab_status, tab_note, tab_decision = st.tabs(["Update Status", "Add Internal Note", "Record Formal Decision"])'''
replace_once(needle, replacement, "insufficient-evidence review action")

replace_once(
    '        preview_path = Path(exp.get("preview_png", ""))\n        if preview_path.exists():\n            st.image(str(preview_path), caption="Raster analysis grid preview (red = flagged indicator, grey = unobserved; north-up grid, not a map-aligned overlay)", width=420)',
    '        preview_path = Path(exp.get("preview_png", ""))\n        if preview_path.exists():\n            st.info("A raster preview exists for this case, but it is a north-up analysis-grid thumbnail, not a geographically aligned map. Do not use it to judge the location of a candidate. Use the GeoJSON/GeoTIFF exports for GIS inspection; a map-aligned case viewer is a separate enhancement.")',
    "honest preview explanation")

if text == original:
    raise SystemExit("No changes were made.")
APP.write_text(text, encoding="utf-8")
print(f"Extension applied successfully to {APP}")
print("Added: case-to-analysis navigation, explicit insufficient-evidence review completion, and honest preview labeling.")
