"""SQLite case store. Results are immutable once stored; review actions are append-only tables."""
from __future__ import annotations
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .models import Result, State

STATUSES = ("potential", "under_review", "reviewed")
DECISIONS = ("confirmed_change", "natural_seasonal", "false_positive", "field_inspection", "insufficient_evidence")
SCHEMA = """
CREATE TABLE IF NOT EXISTS cases(case_id TEXT PRIMARY KEY, pipeline TEXT NOT NULL, state TEXT NOT NULL, status TEXT NOT NULL,
  geometry TEXT, period_start TEXT, period_end TEXT, summary TEXT, evidence TEXT, limitations TEXT, params TEXT,
  score REAL, score_label TEXT, synthetic INTEGER, exports TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS reviewer_decision(id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, reviewer TEXT,
  decision TEXT, note TEXT, decided_at TEXT);
CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, reviewer TEXT, note TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, actor TEXT, action TEXT, detail TEXT, ts TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CaseStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._c() as c:
            c.executescript(SCHEMA)

    def _c(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return _Ctx(con)

    def next_id(self, pipeline: str) -> str:
        prefix = "FLD" if pipeline == "flood" else "ENC"
        with self._c() as c:
            rows = c.execute("SELECT case_id FROM cases WHERE pipeline=?", (pipeline,)).fetchall()
        max_num = 0
        for r in rows:
            cid = r["case_id"]
            if cid.startswith(f"{prefix}-"):
                try:
                    max_num = max(max_num, int(cid.split("-")[1]))
                except (IndexError, ValueError):
                    pass
        return f"{prefix}-{max_num + 1:04d}"

    def create_case(self, case_id: str, result: Result, exports: dict, actor: str = "system") -> None:
        t = now()
        with self._c() as c:
            c.execute("INSERT INTO cases VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                case_id, result.pipeline.value, result.state.value, "potential", json.dumps(result.grid.aoi_geojson()),
                result.period["start"], result.period["end"], result.summary, json.dumps(result.evidence, default=str),
                json.dumps(result.limitations), json.dumps(result.params), result.score, result.score_label,
                int(result.synthetic), json.dumps(exports), t, t))
            c.execute("INSERT INTO audit_log(case_id,actor,action,detail,ts) VALUES(?,?,?,?,?)",
                      (case_id, actor, "case_created", f"state={result.state.value}", t))

    def list_cases(self, pipeline=None, states=None, status=None) -> list[dict]:
        q, args = "SELECT * FROM cases WHERE 1=1", []
        if pipeline:
            q += " AND pipeline=?"; args.append(pipeline)
        if states:
            q += f" AND state IN ({','.join('?' * len(states))})"; args += list(states)
        if status:
            q += " AND status=?"; args.append(status)
        with self._c() as c:
            rows = [dict(r) for r in c.execute(q + " ORDER BY created_at DESC, case_id DESC", args)]
        return rows

    def get_case(self, case_id: str) -> dict | None:
        with self._c() as c:
            r = c.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
        return dict(r) if r else None

    def set_status(self, case_id: str, status: str, actor: str) -> None:
        if status not in STATUSES:
            raise ValueError(status)
        with self._c() as c:
            old = c.execute("SELECT status FROM cases WHERE case_id=?", (case_id,)).fetchone()["status"]
            c.execute("UPDATE cases SET status=?, updated_at=? WHERE case_id=?", (status, now(), case_id))
            c.execute("INSERT INTO audit_log(case_id,actor,action,detail,ts) VALUES(?,?,?,?,?)",
                      (case_id, actor, "status_changed", f"{old} -> {status}", now()))

    def add_decision(self, case_id: str, reviewer: str, decision: str, note: str = "") -> None:
        """Reviewer feedback is stored separately; the original evidence record is never rewritten."""
        if decision not in DECISIONS:
            raise ValueError(decision)
        with self._c() as c:
            c.execute("INSERT INTO reviewer_decision(case_id,reviewer,decision,note,decided_at) VALUES(?,?,?,?,?)",
                      (case_id, reviewer, decision, note, now()))
            c.execute("INSERT INTO audit_log(case_id,actor,action,detail,ts) VALUES(?,?,?,?,?)",
                      (case_id, reviewer, "reviewer_decision", decision, now()))

    def add_note(self, case_id: str, reviewer: str, note: str) -> None:
        with self._c() as c:
            c.execute("INSERT INTO notes(case_id,reviewer,note,ts) VALUES(?,?,?,?)", (case_id, reviewer, note, now()))
            c.execute("INSERT INTO audit_log(case_id,actor,action,detail,ts) VALUES(?,?,?,?,?)",
                      (case_id, reviewer, "note_added", note[:80], now()))

    def _list(self, table: str, case_id: str) -> list[dict]:
        with self._c() as c:
            return [dict(r) for r in c.execute(f"SELECT * FROM {table} WHERE case_id=? ORDER BY id", (case_id,))]

    def audit(self, case_id): return self._list("audit_log", case_id)
    def decisions(self, case_id): return self._list("reviewer_decision", case_id)
    def notes(self, case_id): return self._list("notes", case_id)

    def log(self, case_id: str, actor: str, action: str, detail: str = "") -> None:
        with self._c() as c:
            c.execute("INSERT INTO audit_log(case_id,actor,action,detail,ts) VALUES(?,?,?,?,?)", (case_id, actor, action, detail, now()))


class _Ctx:
    def __init__(self, con): self.con = con
    def __enter__(self): return self.con
    def __exit__(self, et, ev, tb):
        self.con.commit() if et is None else self.con.rollback()
        self.con.close()
