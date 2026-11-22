"""Independent reference validation for stored Change Sentinel cases.

This module consumes immutable case records; it never changes analysis output,
case state, detection geometry, or satellite-processing behaviour.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .auth import AuthStore


REFERENCE_LABELS = (
    "confirmed_persistent_conversion",
    "seasonal_water_level_fluctuation",
    "no_significant_waterbody_change",
    "uncertain_insufficient_evidence",
)
REFERENCE_LABELS_DISPLAY = {
    "confirmed_persistent_conversion": "Confirmed persistent waterbody conversion",
    "seasonal_water_level_fluctuation": "Seasonal water-level fluctuation",
    "no_significant_waterbody_change": "No significant waterbody change",
    "uncertain_insufficient_evidence": "Uncertain / insufficient evidence",
}
VALIDATION_STATUSES = ("pending", "validated", "inconclusive")
REFERENCE_SOURCES = (
    "field_inspection",
    "official_waterbody_records",
    "independently_verified_satellite_imagery",
    "reliable_historical_records",
    "other_documented_ground_observation",
)
SOURCE_DISPLAY = {
    "field_inspection": "Field inspection (independent field evidence)",
    "official_waterbody_records": "Official waterbody records",
    "independently_verified_satellite_imagery": "Independently verified satellite imagery (secondary remote sensing)",
    "reliable_historical_records": "Reliable historical records",
    "other_documented_ground_observation": "Other documented ground observation",
}
POSITIVE_STATES = {"potential_change_detected", "high_priority_review"}
NEGATIVE_STATES = {"no_change_detected"}


class ValidationError(ValueError):
    """Safe validation-record input or authorisation failure."""


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ValidationStore:
    """Versioned, additive validation tables in the existing SQLite database."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.auth = AuthStore(self.path)
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def migrate(self) -> None:
        with self._connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS validation_schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            if con.execute("SELECT 1 FROM validation_schema_migrations WHERE version=1").fetchone():
                return
            con.executescript("""
                CREATE TABLE IF NOT EXISTS validation_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL UNIQUE,
                    waterbody_id TEXT,
                    observation_date TEXT,
                    reference_label TEXT NOT NULL,
                    reference_source TEXT NOT NULL,
                    reference_notes TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('pending', 'validated', 'inconclusive')),
                    predicted_result TEXT NOT NULL,
                    outcome TEXT,
                    validator TEXT NOT NULL,
                    validated_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS validation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    validation_id INTEGER NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    ts TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_validation_status ON validation_records(status);
                CREATE INDEX IF NOT EXISTS idx_validation_waterbody ON validation_records(waterbody_id);
            """)
            con.execute("INSERT INTO validation_schema_migrations(version, applied_at) VALUES(1, ?)", (_stamp(),))

    def _require_admin(self, actor: str) -> None:
        self.auth.require_admin(actor)

    @staticmethod
    def prediction_for(case: dict) -> tuple[str, bool | None]:
        state = case["state"]
        if state in POSITIVE_STATES:
            return "Potential persistent conversion detected", True
        if state in NEGATIVE_STATES:
            return "No conversion detected", False
        return "Not assessed / analysis unavailable", None

    @staticmethod
    def _outcome(predicted_positive: bool | None, reference_label: str, status: str) -> str | None:
        if status != "validated" or predicted_positive is None or reference_label == "uncertain_insufficient_evidence":
            return None
        reference_positive = reference_label == "confirmed_persistent_conversion"
        if predicted_positive and reference_positive:
            return "TP"
        if predicted_positive and not reference_positive:
            return "FP"
        if not predicted_positive and reference_positive:
            return "FN"
        return "TN"

    def _case(self, con: sqlite3.Connection, case_id: str) -> dict:
        row = con.execute("SELECT case_id,state,evidence,created_at FROM cases WHERE case_id=?", (case_id,)).fetchone()
        if row is None:
            raise ValidationError("Case not found.")
        return dict(row)

    @staticmethod
    def _waterbody(case: dict) -> str:
        try:
            return (json.loads(case.get("evidence") or "{}").get("aoi") or {}).get("id") or ""
        except (TypeError, ValueError):
            return ""

    def save_record(self, actor: str, *, case_id: str, observation_date: str, reference_label: str,
                    reference_source: str, reference_notes: str, status: str) -> dict:
        """Create or update one record while preserving a validation-history event and case audit entry."""
        self._require_admin(actor)
        case_id = case_id.strip()
        if reference_label not in REFERENCE_LABELS or reference_source not in REFERENCE_SOURCES or status not in VALIDATION_STATUSES:
            raise ValidationError("Invalid reference label, source, or validation status.")
        if status == "validated" and reference_label == "uncertain_insufficient_evidence":
            raise ValidationError("Uncertain evidence must be marked pending or inconclusive, not validated.")
        with self._connect() as con:
            case = self._case(con, case_id)
            prediction, predicted_positive = self.prediction_for(case)
            outcome = self._outcome(predicted_positive, reference_label, status)
            if status == "validated" and outcome is None:
                raise ValidationError("Only assessed positive or no-change cases can contribute validated accuracy metrics.")
            previous = con.execute("SELECT id FROM validation_records WHERE case_id=?", (case_id,)).fetchone()
            values = (self._waterbody(case), observation_date.strip() or None, reference_label, reference_source,
                      reference_notes.strip(), status, prediction, outcome, actor, _stamp(), _stamp(), case_id)
            if previous:
                con.execute("""UPDATE validation_records SET waterbody_id=?,observation_date=?,reference_label=?,reference_source=?,
                               reference_notes=?,status=?,predicted_result=?,outcome=?,validator=?,validated_at=?,updated_at=? WHERE case_id=?""", values)
                validation_id, action = previous["id"], "validation_updated"
            else:
                cur = con.execute("""INSERT INTO validation_records(waterbody_id,observation_date,reference_label,reference_source,
                                  reference_notes,status,predicted_result,outcome,validator,validated_at,updated_at,case_id)
                                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", values)
                validation_id, action = cur.lastrowid, "validation_created"
            detail = f"status={status}; reference={reference_label}; outcome={outcome or 'excluded'}"
            con.execute("INSERT INTO validation_history(validation_id,actor,action,detail,ts) VALUES(?,?,?,?,?)",
                        (validation_id, actor, action, detail, _stamp()))
            # Existing case audit table is append-only; original case fields remain untouched.
            con.execute("INSERT INTO audit_log(case_id,actor,action,detail,ts) VALUES(?,?,?,?,?)",
                        (case_id, actor, action, detail, _stamp()))
            return dict(con.execute("SELECT * FROM validation_records WHERE id=?", (validation_id,)).fetchone())

    def records(self, actor: str, *, status: str | None = None, outcome: str | None = None,
                waterbody_id: str | None = None, date_from: str | None = None, date_to: str | None = None) -> list[dict]:
        self._require_admin(actor)
        query, args = "SELECT * FROM validation_records WHERE 1=1", []
        for field, value in (("status", status), ("outcome", outcome), ("waterbody_id", waterbody_id)):
            if value:
                query += f" AND {field}=?"; args.append(value)
        if date_from:
            query += " AND COALESCE(observation_date, validated_at)>=?"; args.append(date_from)
        if date_to:
            query += " AND COALESCE(observation_date, validated_at)<=?"; args.append(date_to)
        with self._connect() as con:
            return [dict(row) for row in con.execute(query + " ORDER BY validated_at DESC, id DESC", args)]

    def metrics(self, actor: str) -> dict:
        rows = self.records(actor)
        counts = {key: sum(row["outcome"] == key for row in rows) for key in ("TP", "FP", "FN", "TN")}
        n = sum(counts.values())
        ratio = lambda numerator, denominator: None if not denominator else numerator / denominator
        precision = ratio(counts["TP"], counts["TP"] + counts["FP"])
        recall = ratio(counts["TP"], counts["TP"] + counts["FN"])
        return {
            **counts, "validated_cases": n,
            "pending": sum(row["status"] == "pending" for row in rows),
            "inconclusive": sum(row["status"] == "inconclusive" for row in rows),
            "total_evaluated": len(rows),
            "accuracy": ratio(counts["TP"] + counts["TN"], n),
            "precision": precision, "recall": recall,
            "f1": None if precision is None or recall is None or precision + recall == 0 else 2 * precision * recall / (precision + recall),
            "specificity": ratio(counts["TN"], counts["TN"] + counts["FP"]),
        }
