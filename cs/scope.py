"""Case scope, honest review status, e-mail gating and run-log driven readiness.

Pure functions over case rows (dicts from CaseStore) so the UI and the tests share one definition.
"""
from __future__ import annotations
import json

PILOT, SYNTHETIC, LEGACY = "pilot", "synthetic", "legacy"
SCOPE_LABELS = {PILOT: "Hyderabad pilot (real)", SYNTHETIC: "Synthetic", LEGACY: "Legacy Chennai grid"}
FLAGGED_STATES = {"potential_change_detected", "high_priority_review"}
ASSESSED_STATES = FLAGGED_STATES | {"no_change_detected"}


def _evidence(case: dict) -> dict:
    ev = case.get("evidence")
    if isinstance(ev, dict):
        return ev
    try:
        return json.loads(ev or "{}")
    except ValueError:
        return {}


def case_scope(case: dict) -> str:
    """synthetic beats everything; a registry-AOI run is the pilot; any other (fixed-grid) case is legacy."""
    if case.get("synthetic"):
        return SYNTHETIC
    aoi = _evidence(case).get("aoi") or {}
    return PILOT if aoi.get("kind") == "registry_waterbody_buffer" else LEGACY


def scope_badge(case: dict) -> str:
    s = case_scope(case)
    return "" if s == PILOT else ("SYNTHETIC" if s == SYNTHETIC else "LEGACY")


def display_status(case: dict) -> str:
    """UI-layer review status. Stored values are untouched. 'potential' is only the default for a flagged case; a case that
    was never assessed (or found no change) is not 'potential' anything."""
    state, status = case.get("state"), case.get("status")
    if status != "potential":
        return status
    return {"not_assessed": "unassessed", "analysis_failed": "analysis failed",
            "no_change_detected": "no change detected - screening only"}.get(state, status)


def awaiting_review(case: dict) -> bool:
    return case_scope(case) == PILOT and case.get("state") in FLAGGED_STATES and case.get("status") == "potential"


def usable_evidence(case: dict) -> bool:
    return case_scope(case) == PILOT and case.get("state") in ASSESSED_STATES


def filter_scope(cases: list[dict], scope: str | None) -> list[dict]:
    return cases if scope in (None, "all") else [c for c in cases if case_scope(c) == scope]


def email_block_reasons(case: dict, decisions: list | None = None) -> list[str]:
    """Reasons sending is disabled. Empty list = sending is allowed (still needs an explicit confirmation)."""
    r = []
    s = case_scope(case)
    if s == SYNTHETIC:
        r.append("synthetic case")
    if s == LEGACY:
        r.append("legacy (non-pilot) case")
    if case.get("state") not in FLAGGED_STATES:
        r.append("case was not assessed as a potential change" if case.get("state") != "no_change_detected" else "no change detected")
    if case.get("status") == "potential" and not decisions:
        r.append("case has not been reviewed")
    return r


def readiness_rows(registry, sources: dict, store) -> list[dict]:
    """Generated (never hard-coded) readiness: registry status + adapter status + the recorded run log.
    tested_against_remote is true only when a real (non stand-in) run succeeded."""
    rows = []
    d = getattr(registry, "district", None)
    if d is None:
        state, detail = "not registered", "register with python -m cs.registry add-district"
    else:
        state, detail = d.status, ("; ".join(d.reasons) if d.reasons else d.name)
    rows.append({"item": "District boundary", "state": state, "detail": detail, "tested_against_remote": "n/a", "last_run": ""})
    wbs = list(getattr(registry, "waterbodies", []) or [])
    rows.append({"item": "Waterbody records", "state": f"{sum(1 for w in wbs if w.verified)} verified of {len(wbs)}",
                 "detail": ", ".join(f"{w.name} ({w.status})" for w in wbs[:6]), "tested_against_remote": "n/a", "last_run": ""})
    for key, label in (("sentinel2", "Sentinel-2 L2A (live, Earth Search)"), ("sentinel1", "Sentinel-1 RTC"),
                       ("dynamic_world", "Dynamic World"), ("jrc_gsw", "JRC Global Surface Water")):
        runs = [r for r in store.source_runs(key, 20) if not r["standin"]]
        last = runs[0] if runs else None
        ok = any(r["outcome"] == "succeeded" for r in runs)
        adapter = sources[key].status if key in sources else "implemented"
        if last:
            cnt = ", ".join(f"{k}={v}" for k, v in (last["counts"] or {}).items() if v is not None)[:90]
            txt = f"exercised {last['ts'][:10]}: {last['outcome']}" + (f" ({cnt})" if cnt else "") + (f" - {last['error'][:120]}" if last["error"] else "")
        else:
            txt = "no recorded run"
        rows.append({"item": label, "state": adapter, "tested_against_remote": "yes" if ok else "no", "last_run": txt, "detail": ""})
    return rows
