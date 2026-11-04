"""SQLite state: runs, proposals and every reviewer decision.

Idempotency lives here. Each proposal has a deterministic key built from
(kind, account, location, proposed values). A key is inserted once, ever,
so a re-run never re-proposes something that is pending, approved, applied
or rejected. If the CRM or website changes, the proposed values change, the
key changes, and the new situation is proposed fresh.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT, finished_at TEXT,
    n_locations INTEGER, n_accounts INTEGER,
    n_generated INTEGER, n_new INTEGER, n_superseded INTEGER,
    matches_json TEXT
);
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    run_id INTEGER,
    kind TEXT, title TEXT,
    account_id TEXT, location_slug TEXT,
    change_json TEXT, create_json TEXT, before_json TEXT, evidence_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | applied | rejected | failed | superseded
    created_at TEXT, decided_at TEXT, decided_by TEXT, decision_note TEXT,
    result_json TEXT, error TEXT
);
"""

JSON_FIELDS = ("change", "create", "before", "evidence", "result")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ---- runs
    def start_run(self):
        cur = self.db.execute("INSERT INTO runs (started_at) VALUES (?)", (now(),))
        self.db.commit()
        return cur.lastrowid

    def finish_run(self, run_id, **stats):
        stats["matches_json"] = json.dumps(stats.pop("matches", []))
        cols = ", ".join(f"{k} = ?" for k in stats)
        self.db.execute(f"UPDATE runs SET finished_at = ?, {cols} WHERE id = ?",
                        (now(), *stats.values(), run_id))
        self.db.commit()

    def last_run(self):
        row = self.db.execute("SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        run = dict(row)
        run["matches"] = json.loads(run.pop("matches_json") or "[]")
        return run

    # ---- proposals
    def add_proposals(self, run_id, proposals):
        """Insert proposals whose key has never been seen. Returns (new, superseded)."""
        keys = [p["key"] for p in proposals]
        new = 0
        for p in proposals:
            cur = self.db.execute(
                """INSERT OR IGNORE INTO proposals
                   (key, run_id, kind, title, account_id, location_slug,
                    change_json, create_json, before_json, evidence_json, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (p["key"], run_id, p["kind"], p["title"], p["account_id"], p["location_slug"],
                 json.dumps(p["change"]), json.dumps(p["create"]), json.dumps(p["before"]),
                 json.dumps(p["evidence"]), now()))
            new += cur.rowcount
        # Pending items this run no longer produces describe a situation that has
        # changed (fixed by hand, website updated...). Retire them so nobody approves stale data.
        placeholders = ",".join("?" * len(keys)) or "''"
        cur = self.db.execute(
            f"UPDATE proposals SET status = 'superseded', decided_at = ? "
            f"WHERE status IN ('pending', 'failed') AND key NOT IN ({placeholders})", (now(), *keys))
        self.db.commit()
        return new, cur.rowcount

    def _row(self, row):
        p = dict(row)
        for f in JSON_FIELDS:
            p[f] = json.loads(p.pop(f + "_json") or "null")
        return p

    def get(self, proposal_id):
        row = self.db.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, statuses=None):
        if statuses:
            q = f"SELECT * FROM proposals WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY id"
            rows = self.db.execute(q, statuses).fetchall()
        else:
            rows = self.db.execute("SELECT * FROM proposals ORDER BY id").fetchall()
        return [self._row(r) for r in rows]

    def counts(self):
        return {r["status"]: r["n"] for r in
                self.db.execute("SELECT status, COUNT(*) n FROM proposals GROUP BY status")}

    def decide(self, proposal_id, status, reviewer, note="", result=None, error=None):
        self.db.execute(
            """UPDATE proposals SET status = ?, decided_at = ?, decided_by = ?, decision_note = ?,
               result_json = ?, error = ? WHERE id = ?""",
            (status, now(), reviewer, note, json.dumps(result) if result is not None else None, error, proposal_id))
        self.db.commit()

    def reset(self):
        """Forget every run and decision. Used only by practice mode's Start over."""
        self.db.executescript("DELETE FROM proposals; DELETE FROM runs;")
        self.db.commit()

    def save_partial_result(self, proposal_id, result):
        """Remember progress (e.g. a created account id) so a retry never creates twice."""
        self.db.execute("UPDATE proposals SET result_json = ? WHERE id = ?", (json.dumps(result), proposal_id))
        self.db.commit()
