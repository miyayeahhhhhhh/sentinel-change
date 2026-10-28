"""Shared data model. No analytics here: states, result container, score labelling rules."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

import geopandas as gpd
import numpy as np

from .geo import Grid


class State(str, Enum):
    NO_CHANGE = "no_change_detected"
    NOT_ASSESSED = "not_assessed"
    FAILED = "analysis_failed"
    POTENTIAL = "potential_change_detected"
    HIGH = "high_priority_review"


class Pipeline(str, Enum):
    FLOOD = "flood"
    ENCROACH = "encroachment"


STATE_LABELS = {
    State.NO_CHANGE: "No change detected",
    State.NOT_ASSESSED: "Not assessed - no usable imagery",
    State.FAILED: "Analysis failed",
    State.POTENTIAL: "Potential change detected",
    State.HIGH: "High-priority review",
}
SCORED_STATES = {State.POTENTIAL, State.HIGH}
SCORE_LABEL = "Heuristic evidence score (0-100). Not a probability or calibrated confidence."
DISCLAIMER = ("Satellite-derived indication for human review. Not evidence of legal status, "
              "ownership, intent, or confirmed flood damage.")


@dataclass
class Result:
    pipeline: Pipeline
    state: State
    summary: str
    evidence: dict
    limitations: list
    params: dict
    grid: Grid
    arrays: dict
    primary_layer: str
    polygons: gpd.GeoDataFrame | None = None
    score: float | None = None
    synthetic: bool = True
    score_label: str = SCORE_LABEL

    def __post_init__(self) -> None:
        if self.state not in SCORED_STATES and self.score is not None:
            raise ValueError(f"A score must not be attached to state {self.state.value}")
        if self.state in SCORED_STATES and self.score is None:
            raise ValueError("Flagged states require a heuristic score")

    @property
    def period(self) -> dict:
        return self.evidence.get("period", {"start": None, "end": None})


def failed_result(pipeline: Pipeline, grid: Grid, exc: Exception, params: dict) -> Result:
    h, w = grid.height, grid.width
    key = "flood" if pipeline == Pipeline.FLOOD else "loss"
    return Result(
        pipeline=pipeline, state=State.FAILED,
        summary=f"Analysis failed: {type(exc).__name__}: {exc}",
        evidence={"error": str(exc), "metrics": [], "scenes": [], "period": {"start": None, "end": None}},
        limitations=["Run failed; no conclusion about change can be drawn from this record."],
        params=params, grid=grid,
        arrays={key: np.zeros((h, w), bool), "observed": np.zeros((h, w), bool),
                "coverage": np.zeros((h, w), np.float32)},
        primary_layer=key,
    )
