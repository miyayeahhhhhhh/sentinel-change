from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from cs.auth import ADMIN, REVIEWER, AuthError, AuthStore


PASSWORD = "Strong-passphrase-2026!"


def store(tmp_path):
    return AuthStore(tmp_path / "auth.db", max_failures=3, lock_seconds=60)


def test_bootstrap_login_and_password_hash(tmp_path):
    auth = store(tmp_path)
    admin = auth.bootstrap_admin("admin.one", "admin@example.org", PASSWORD)
    assert admin.role == ADMIN
    assert auth.authenticate("admin.one", PASSWORD).role == ADMIN
    with sqlite3.connect(auth.path) as con:
        password_hash = con.execute("SELECT password_hash FROM auth_users WHERE username='admin.one'").fetchone()[0]
    assert password_hash != PASSWORD and password_hash.startswith("$argon2")
    with pytest.raises(AuthError):
        auth.bootstrap_admin("another", "other@example.org", PASSWORD)


def test_admin_manages_reviewer_and_backend_permission(tmp_path):
    auth = store(tmp_path)
    auth.bootstrap_admin("admin.one", "admin@example.org", PASSWORD)
    reviewer = auth.create_reviewer("admin.one", "reviewer.one", "reviewer@example.org", PASSWORD)
    assert reviewer.role == REVIEWER
    assert auth.authenticate("reviewer.one", PASSWORD).username == "reviewer.one"
    with pytest.raises(AuthError):
        auth.create_reviewer("reviewer.one", "not.allowed", "no@example.org", PASSWORD)
    with pytest.raises(AuthError):
        auth.create_reviewer("admin.one", "reviewer.two", "reviewer@example.org", PASSWORD)
    auth.set_reviewer_active("admin.one", "reviewer.one", False)
    assert auth.authenticate("reviewer.one", PASSWORD) is None
    auth.set_reviewer_active("admin.one", "reviewer.one", True)
    auth.reset_reviewer_password("admin.one", "reviewer.one", "Another-strong-password-2026!")
    assert auth.authenticate("reviewer.one", "Another-strong-password-2026!")


def test_invalid_login_rate_limits(tmp_path):
    auth = store(tmp_path)
    auth.bootstrap_admin("admin.one", "admin@example.org", PASSWORD)
    for _ in range(3):
        assert auth.authenticate("admin.one", "wrong password") is None
    assert auth.authenticate("admin.one", PASSWORD) is None
    with sqlite3.connect(auth.path) as con:
        locked = con.execute("SELECT locked_until FROM auth_users WHERE username='admin.one'").fetchone()[0]
    assert datetime.fromisoformat(locked) > datetime.now(timezone.utc) - timedelta(seconds=1)
