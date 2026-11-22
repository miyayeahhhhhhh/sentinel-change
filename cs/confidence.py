"""Read-only estimated detection confidence derived from existing result evidence.

This is an evidence-strength presentation aid, not calibrated accuracy or a
probability.  It does not participate in detection, prioritisation, or storage.
"""
from __future__ import annotations

from dataclasses import dataclass


PRIMARY = ("persistence", "footprint", "spatial_coherence", "magnitude")
QUALITY = ("image_quality", "recency")
INDEPENDENT = ("sensor_agreement", "corroboration", "landcover_transition", "historical_water")
GROUP_WEIGHTS = {"Primary optical persistence": 45, "Image quality and recency": 20,
                 "Same-season comparison": 15, "Independent supporting source": 20}


@dataclass(frozen=True)
class ConfidenceEstimate:
    score: float | None
    category: str
    supporting: list[str]
    conflicting: list[str]
    missing: list[str]
    observation_count: int
    coverage: float
    rationale: str


def _components(evidence: dict) -> dict:
    return {row.get("name"): row.get("normalised") for row in evidence.get("score_components", []) if row.get("available")}


def _weighted(values: dict, names: tuple[str, ...], weights: tuple[float, ...] | None = None) -> float | None:
    present = [(name, values[name]) for name in names if values.get(name) is not None]
    if not present:
        return None
    weights = weights or tuple(1.0 for _ in names)
    weight_map = dict(zip(names, weights))
    numerator = sum(float(value) * weight_map[name] for name, value in present)
    return numerator / sum(weight_map[name] for name, _ in present)


def estimate_detection_confidence(state: str, evidence: dict) -> ConfidenceEstimate:
    """Estimate support only for a detected change using already-produced evidence.

    Formula: weighted mean of available evidence groups (primary 45, quality 20,
    same-season 15, independent 20).  At least two groups and 60% available
    group weight are required.  Coverage below 80% or no independent group
    caps the estimate at 79; contradictory independent evidence caps it at 50.  Missing data is neither
    scored as adverse nor silently treated as support.
    """
    if state not in {"potential_change_detected", "high_priority_review"}:
        return ConfidenceEstimate(None, "Not applicable", [], [], ["No detected persistent-conversion candidate"], 0, 0.0,
                                  "Confidence is only estimated for detected change candidates; this result is not a positive detection.")
    values = _components(evidence)
    groups: dict[str, float] = {}
    primary = _weighted(values, PRIMARY, (0.20, 0.15, 0.15, 0.10))
    if primary is not None and all(values.get(name) is not None for name in PRIMARY):
        groups["Primary optical persistence"] = primary
    quality = _weighted(values, QUALITY, (0.10, 0.05))
    if quality is not None:
        groups["Image quality and recency"] = quality
    if evidence.get("seasonal_check") == "performed":
        groups["Same-season comparison"] = 1.0
    independent = _weighted(values, INDEPENDENT)
    if independent is not None:
        groups["Independent supporting source"] = independent

    missing = [name for name in GROUP_WEIGHTS if name not in groups]
    coverage_weight = sum(GROUP_WEIGHTS[name] for name in groups)
    coverage = coverage_weight / sum(GROUP_WEIGHTS.values())
    observations = sum(bool(row.get("usable")) for row in evidence.get("scenes", []))
    fusion = evidence.get("evidence_fusion") or {}
    contradictory = fusion.get("contradicted_by") or []
    conflicts = [f"Independent evidence conflicts: {source}" for source in contradictory]
    supporting = [f"{name} ({value:.0%})" for name, value in groups.items()]

    if "Primary optical persistence" not in groups or len(groups) < 2 or coverage < 0.60:
        return ConfidenceEstimate(None, "Insufficient evidence", supporting, conflicts, missing, observations, coverage,
                                  "Insufficient evidence to calculate confidence: a complete primary optical signal plus at least one additional evidence group and 60% group-weight coverage are required.")
    score = 100 * sum(GROUP_WEIGHTS[name] * value for name, value in groups.items()) / coverage_weight
    caps = []
    if coverage < 0.80:
        score = min(score, 79.0)
        caps.append("coverage below 80% limits the estimate to the moderate band")
    if "Independent supporting source" not in groups:
        score = min(score, 79.0)
        caps.append("no independent supporting source limits the estimate to the moderate band")
    if contradictory:
        score = min(score, 50.0)
        caps.append("conflicting independent evidence limits the estimate to 50%")
    score = round(max(0.0, min(100.0, score)), 1)
    category = "Strong supporting evidence" if score >= 80 else "Moderate supporting evidence" if score >= 60 else "Limited supporting evidence" if score >= 40 else "Weak supporting evidence"
    rationale = "Weighted evidence-strength estimate from available, non-duplicated evidence groups; it is not calibrated detection accuracy or a probability."
    if caps:
        rationale += " " + "; ".join(caps) + "."
    return ConfidenceEstimate(score, category, supporting, conflicts, missing, observations, coverage, rationale)
