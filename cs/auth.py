"""Isolated SQLite authentication and authorisation for the Streamlit UI.

This module owns only additive ``auth_*`` tables in the existing SQLite file.
It deliberately has no dependency on the analysis or Neo4j integrations.
"""
from __future__ import annotations

import hmac
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError


ADMIN = "admin"
REVIEWER = "reviewer"
ROLES = (ADMIN, REVIEWER)
_USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_HASHER = PasswordHasher()


class AuthError(ValueError):
    """A safe, user-presentable authentication or authorisation failure."""


@dataclass(frozen=True)
class User:
    id: int
    username: str
    email: str
    role: str
    active: bool


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp() -> str:
    return _now().isoformat(timespec="seconds")


class AuthStore:
    """Authentication store with idempotent, versioned additive migrations."""

    def __init__(self, path: Path, *, max_failures: int = 5, lock_seconds: int = 900):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_failures = max_failures
        self.lock_seconds = lock_seconds
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def migrate(self) -> None:
        """Apply auth_schema_v1 once; never changes existing application tables."""
        with self._connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS auth_schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            if con.execute("SELECT 1 FROM auth_schema_migrations WHERE version=1").fetchone():
                return
            con.executescript("""
                CREATE TABLE IF NOT EXISTS auth_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin', 'reviewer')),
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
                    created_by TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    failed_attempts INTEGER NOT NULL DEFAULT 0,
                    locked_until TEXT
                );
                CREATE TABLE IF NOT EXISTS auth_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor TEXT,
                    action TEXT NOT NULL,
                    subject_username TEXT,
                    detail TEXT,
                    ts TEXT NOT NULL
                );
            """)
            con.execute("INSERT INTO auth_schema_migrations(version, applied_at) VALUES(1, ?)", (_stamp(),))

    def _audit(self, con: sqlite3.Connection, actor: str | None, action: str, subject: str | None, detail: str = "") -> None:
        con.execute("INSERT INTO auth_audit_log(actor,action,subject_username,detail,ts) VALUES(?,?,?,?,?)",
                    (actor, action, subject, detail, _stamp()))

    @staticmethod
    def _user(row: sqlite3.Row | None) -> User | None:
        return None if row is None else User(row["id"], row["username"], row["email"], row["role"], bool(row["active"]))

    def has_admin(self) -> bool:
        with self._connect() as con:
            return con.execute("SELECT 1 FROM auth_users WHERE role=? LIMIT 1", (ADMIN,)).fetchone() is not None

    def get_user(self, username: str) -> User | None:
        with self._connect() as con:
            return self._user(con.execute("SELECT * FROM auth_users WHERE username=?", (username.strip(),)).fetchone())

    def _validate_new_user(self, username: str, email: str, password: str) -> tuple[str, str]:
        username, email = username.strip(), email.strip().lower()
        if not _USERNAME.fullmatch(username):
            raise AuthError("Username must be 3–64 characters: letters, digits, dot, underscore, or hyphen.")
        if not _EMAIL.fullmatch(email) or len(email) > 254:
            raise AuthError("Enter a valid email address.")
        if len(password) < 12 or sum(bool(re.search(p, password)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]")) < 3:
            raise AuthError("Password must be at least 12 characters and include three character types.")
        return username, email

    def bootstrap_admin(self, username: str, email: str, password: str) -> User:
        """Create the first admin only. Intended for the local CLI bootstrap command."""
        username, email = self._validate_new_user(username, email, password)
        with self._connect() as con:
            if con.execute("SELECT 1 FROM auth_users WHERE role=?", (ADMIN,)).fetchone():
                raise AuthError("An Admin account already exists; bootstrap is disabled.")
            try:
                cur = con.execute("INSERT INTO auth_users(username,email,password_hash,role,active,created_at,updated_at) VALUES(?,?,?,?,1,?,?)",
                                  (username, email, _HASHER.hash(password), ADMIN, _stamp(), _stamp()))
            except sqlite3.IntegrityError as exc:
                raise AuthError("Username or email is already in use.") from exc
            self._audit(con, username, "admin_bootstrapped", username)
            return User(cur.lastrowid, username, email, ADMIN, True)

    def require_admin(self, actor: str) -> User:
        user = self.get_user(actor)
        if user is None or not user.active or user.role != ADMIN:
            raise AuthError("Administrator permission is required.")
        return user

    def create_reviewer(self, actor: str, username: str, email: str, password: str) -> User:
        self.require_admin(actor)
        username, email = self._validate_new_user(username, email, password)
        with self._connect() as con:
            try:
                cur = con.execute("INSERT INTO auth_users(username,email,password_hash,role,active,created_by,created_at,updated_at) VALUES(?,?,?,?,1,?,?,?)",
                                  (username, email, _HASHER.hash(password), REVIEWER, actor, _stamp(), _stamp()))
            except sqlite3.IntegrityError as exc:
                raise AuthError("Username or email is already in use.") from exc
            self._audit(con, actor, "reviewer_created", username)
            return User(cur.lastrowid, username, email, REVIEWER, True)

    def list_reviewers(self, actor: str) -> list[User]:
        self.require_admin(actor)
        with self._connect() as con:
            return [self._user(r) for r in con.execute("SELECT * FROM auth_users WHERE role=? ORDER BY username", (REVIEWER,))]

    def set_reviewer_active(self, actor: str, username: str, active: bool) -> None:
        self.require_admin(actor)
        with self._connect() as con:
            row = con.execute("SELECT role FROM auth_users WHERE username=?", (username,)).fetchone()
            if row is None or row["role"] != REVIEWER:
                raise AuthError("Reviewer account not found.")
            con.execute("UPDATE auth_users SET active=?, failed_attempts=0, locked_until=NULL, updated_at=? WHERE username=?",
                        (int(active), _stamp(), username))
            self._audit(con, actor, "reviewer_reactivated" if active else "reviewer_deactivated", username)

    def reset_reviewer_password(self, actor: str, username: str, password: str) -> None:
        self.require_admin(actor)
        self._validate_new_user("valid_user", "valid@example.org", password)
        with self._connect() as con:
            row = con.execute("SELECT role FROM auth_users WHERE username=?", (username,)).fetchone()
            if row is None or row["role"] != REVIEWER:
                raise AuthError("Reviewer account not found.")
            con.execute("UPDATE auth_users SET password_hash=?, failed_attempts=0, locked_until=NULL, updated_at=? WHERE username=?",
                        (_HASHER.hash(password), _stamp(), username))
            self._audit(con, actor, "reviewer_password_reset", username)

    def authenticate(self, username: str, password: str) -> User | None:
        """Return a user only after a valid password; persist a temporary lockout."""
        username = username.strip()
        with self._connect() as con:
            row = con.execute("SELECT * FROM auth_users WHERE username=?", (username,)).fetchone()
            if row is None:
                # Equalise timing without revealing whether a username exists.
                _HASHER.hash(password or secrets.token_urlsafe(16))
                return None
            user = self._user(row)
            until = datetime.fromisoformat(row["locked_until"]) if row["locked_until"] else None
            if not user.active or (until and until > _now()):
                return None
            try:
                valid = _HASHER.verify(row["password_hash"], password)
            except (VerifyMismatchError, InvalidHashError):
                valid = False
            if not valid:
                failures = row["failed_attempts"] + 1
                lock_until = (_now() + timedelta(seconds=self.lock_seconds)).isoformat(timespec="seconds") if failures >= self.max_failures else None
                con.execute("UPDATE auth_users SET failed_attempts=?, locked_until=?, updated_at=? WHERE id=?",
                            (0 if lock_until else failures, lock_until, _stamp(), user.id))
                self._audit(con, username, "login_failed", username, "locked" if lock_until else "")
                return None
            if _HASHER.check_needs_rehash(row["password_hash"]):
                con.execute("UPDATE auth_users SET password_hash=? WHERE id=?", (_HASHER.hash(password), user.id))
            con.execute("UPDATE auth_users SET failed_attempts=0, locked_until=NULL, updated_at=? WHERE id=?", (_stamp(), user.id))
            self._audit(con, username, "login_succeeded", username)
            return user
