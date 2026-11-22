from __future__ import annotations

import sqlite3

import pytest

from cs.auth import AuthError, AuthStore
from cs.db import CaseStore
from cs.validation import ValidationError, ValidationStore


PASSWORD = "Strong-passphrase-2026!"


def setup_store(tmp_path):
    path = tmp_path / "validation.db"
    CaseStore(path)
    auth = AuthStore(path)
    auth.bootstrap_admin("admin.one", "admin@example.org", PASSWORD)
    auth.create_reviewer("admin.one", "reviewer.one", "reviewer@example.org", PASSWORD)
    with sqlite3.connect(path) as con:
        for case_id, state in (("ENC-TP", "potential_change_detected"), ("ENC-FP", "high_priority_review"),
                               ("ENC-FN", "no_change_detected"), ("ENC-TN", "no_change_detected"),
                               ("ENC-PENDING", "potential_change_detected")):
            con.execute("INSERT INTO cases VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (case_id, "encroachment", state, "potential", None, None, None, "summary", "{}", "[]", "{}", None, None, 0, "{}", "2026-01-01", "2026-01-01"))
    return ValidationStore(path)


def test_metrics_and_audit_are_independent_of_cases(tmp_path):
    store = setup_store(tmp_path)
    save = lambda case, label, status="validated": store.save_record("admin.one", case_id=case, observation_date="2026-01-02", reference_label=label, reference_source="field_inspection", reference_notes="Independent field visit", status=status)
    save("ENC-TP", "confirmed_persistent_conversion")
    save("ENC-FP", "seasonal_water_level_fluctuation")
    save("ENC-FN", "confirmed_persistent_conversion")
    save("ENC-TN", "no_significant_waterbody_change")
    save("ENC-PENDING", "uncertain_insufficient_evidence", "pending")
    metrics = store.metrics("admin.one")
    assert {key: metrics[key] for key in ("TP", "FP", "FN", "TN")} == {"TP": 1, "FP": 1, "FN": 1, "TN": 1}
    assert metrics["validated_cases"] == 4 and metrics["pending"] == 1
    assert metrics["accuracy"] == metrics["precision"] == metrics["recall"] == metrics["f1"] == metrics["specificity"] == 0.5
    with sqlite3.connect(store.path) as con:
        assert con.execute("SELECT state FROM cases WHERE case_id='ENC-TP'").fetchone()[0] == "potential_change_detected"
        assert con.execute("SELECT action FROM audit_log WHERE case_id='ENC-TP'").fetchone()[0] == "validation_created"


def test_permissions_filters_and_zero_denominators(tmp_path):
    store = setup_store(tmp_path)
    with pytest.raises(AuthError):
        store.records("reviewer.one")
    with pytest.raises(AuthError):
        store.save_record("reviewer.one", case_id="ENC-TP", observation_date="", reference_label="confirmed_persistent_conversion", reference_source="field_inspection", reference_notes="", status="validated")
    store.save_record("admin.one", case_id="ENC-TP", observation_date="2026-02-01", reference_label="confirmed_persistent_conversion", reference_source="field_inspection", reference_notes="", status="validated")
    store.save_record("admin.one", case_id="ENC-FP", observation_date="2026-03-01", reference_label="uncertain_insufficient_evidence", reference_source="official_waterbody_records", reference_notes="", status="inconclusive")
    assert len(store.records("admin.one", status="inconclusive")) == 1
    metrics = store.metrics("admin.one")
    assert metrics["specificity"] is None and metrics["precision"] == 1.0


def test_migration_is_idempotent_and_rejects_invalid_validated_record(tmp_path):
    store = setup_store(tmp_path)
    assert ValidationStore(store.path).metrics("admin.one")["total_evaluated"] == 0
    with pytest.raises(ValidationError):
        store.save_record("admin.one", case_id="ENC-PENDING", observation_date="", reference_label="uncertain_insufficient_evidence", reference_source="field_inspection", reference_notes="", status="validated")


def test_update_preserves_validation_history(tmp_path):
    store = setup_store(tmp_path)
    kwargs = dict(actor="admin.one", case_id="ENC-TP", observation_date="2026-01-02",
                  reference_source="field_inspection", reference_notes="Initial independent visit", status="validated")
    store.save_record(reference_label="confirmed_persistent_conversion", **kwargs)
    updated = store.save_record(reference_label="seasonal_water_level_fluctuation", **{**kwargs, "reference_notes": "Corrected after follow-up"})
    assert updated["outcome"] == "FP"
    with sqlite3.connect(store.path) as con:
        history = con.execute("SELECT action FROM validation_history ORDER BY id").fetchall()
    assert [row[0] for row in history] == ["validation_created", "validation_updated"]
