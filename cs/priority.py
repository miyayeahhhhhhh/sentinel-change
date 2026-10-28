"""Review queues: separate flood and encroachment tables with visible factors. No combined score."""
from __future__ import annotations
import json

import pandas as pd

from .models import State

_RANK = {State.HIGH.value: 0, State.POTENTIAL.value: 1, State.NOT_ASSESSED.value: 2, State.FAILED.value: 3, State.NO_CHANGE.value: 4}


def queue_table(cases: list[dict], pipeline: str) -> pd.DataFrame:
    rows = []
    for c in cases:
        if c["pipeline"] != pipeline:
            continue
        ev = json.loads(c["evidence"])
        rows.append({"case_id": c["case_id"], "state": c["state"], "status": c["status"],
                     "score (heuristic)": c["score"],
                     "area_ha": ev.get("area_ha") if pipeline == "flood" else ev.get("loss_ha"),
                     "class": ev.get("class"), "weight_coverage": ev.get("score_weight_coverage"),
                     "data_age_days": ev.get("data_age_days"), "period_end": (c["period_end"] or "")[:10],
                     "inputs": "SYNTHETIC" if c["synthetic"] else "real", "created": c["created_at"]})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["_r"] = df["state"].map(_RANK)
    return df.sort_values(["_r", "score (heuristic)"], ascending=[True, False], na_position="last").drop(columns="_r").reset_index(drop=True)
