"""Review queues: separate flood and encroachment tables with visible factors. No combined score."""
from __future__ import annotations
import json

import pandas as pd

from .models import State

_RANK = {State.HIGH.value: 0, State.POTENTIAL.value: 1, State.NOT_ASSESSED.value: 2, State.FAILED.value: 3, State.NO_CHANGE.value: 4}

# Documented ranking rationale — shown to reviewers in the queue UI.
RANKING_RATIONALE = (
    "Candidates are ranked by (1) analysis state: HIGH-priority review first, then POTENTIAL, "
    "then NOT_ASSESSED/FAILED, then NO_CHANGE; (2) within the same state, by heuristic score "
    "descending (higher score = stronger multi-factor evidence). "
    "Score factors include: persistence of dry condition across observation window, "
    "affected area (relative to area floor/cap), spatial coherence of connected loss polygons, "
    "spectral transition magnitude (MNDWI drop / VV backscatter drop), "
    "evidence quality (mean scene valid-pixel fraction), "
    "data recency (age since last usable observation), "
    "and cross-sensor corroboration (SAR confirming optical or vice versa). "
    "Verified administrative jurisdiction and coordinates are extracted from the study area registry "
    "and displayed for officer attribution. "
    "Scores are heuristic review priorities, not probabilities or legal determinations. "
    "Unranked factors (area under-threshold, insufficient imagery) appear at the bottom."
)


def queue_table(cases: list[dict], pipeline: str) -> pd.DataFrame:
    rows = []
    for c in cases:
        if c["pipeline"] != pipeline:
            continue
        ev = json.loads(c["evidence"])
        rows.append({"case_id": c["case_id"], "state": c["state"], "status": c["status"],
                     "score (heuristic)": c["score"],
                     "area_ha": ev.get("area_ha") if pipeline == "flood" else ev.get("loss_ha"),
                     "class": ev.get("class"),
                     "jurisdiction": (ev.get("jurisdiction") or "-")[:35],
                     "weight_coverage": ev.get("score_weight_coverage"),
                     "data_age_days": ev.get("data_age_days"), "period_end": (c["period_end"] or "")[:10],
                     "inputs": "SYNTHETIC" if c["synthetic"] else "real", "created": c["created_at"]})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["_r"] = df["state"].map(_RANK)
    return df.sort_values(["_r", "score (heuristic)"], ascending=[True, False], na_position="last").drop(columns="_r").reset_index(drop=True)
