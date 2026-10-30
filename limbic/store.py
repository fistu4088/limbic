"""SQLite persistence: agents, event log (episodic emotional memory), and
state snapshots for the timeline."""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    temperament TEXT NOT NULL,
    state TEXT NOT NULL,
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    t REAL NOT NULL,
    kind TEXT NOT NULL,
    source TEXT,
    speaker TEXT,
    appraisal TEXT,
    effects TEXT,
    recalled INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_agent_t ON events(agent, t);
CREATE TABLE IF NOT EXISTS snapshots (
    agent TEXT NOT NULL,
    t REAL NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS snapshots_agent_t ON snapshots(agent, t);
"""


class Store:
    def __init__(self, path: str = "limbic.db") -> None:
        self.path = path
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        with self.lock:
            self.db.close()

    # agents
    def get_agent(self, agent_id: str) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        if not r:
            return None
        return {"id": r["id"], "temperament": json.loads(r["temperament"]),
                "state": json.loads(r["state"]), "created": r["created"], "updated": r["updated"]}

    def list_agents(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT id, created, updated FROM agents ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def save_agent(self, agent_id: str, temperament: dict, state: dict, now: float) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO agents(id, temperament, state, created, updated) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET temperament=excluded.temperament, "
                "state=excluded.state, updated=excluded.updated",
                (agent_id, json.dumps(temperament), json.dumps(state), now, now))
            self.db.commit()

    def delete_agent(self, agent_id: str) -> None:
        with self.lock:
            for table, col in (("agents", "id"), ("events", "agent"), ("snapshots", "agent")):
                self.db.execute(f"DELETE FROM {table} WHERE {col}=?", (agent_id,))
            self.db.commit()

    # events
    def add_event(self, agent_id: str, t: float, kind: str, source: str | None,
                  speaker: str | None, appraisal: dict | None, effects: dict | None) -> int:
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO events(agent, t, kind, source, speaker, appraisal, effects) VALUES(?,?,?,?,?,?,?)",
                (agent_id, t, kind, source, speaker, json.dumps(appraisal), json.dumps(effects)))
            self.db.commit()
            return int(cur.lastrowid)

    def events(self, agent_id: str, since: float = 0, until: float | None = None,
               limit: int = 500, kind: str | None = None) -> list[dict]:
        q = "SELECT * FROM events WHERE agent=? AND t>=?"
        args: list[Any] = [agent_id, since]
        if until is not None:
            q += " AND t<=?"
            args.append(until)
        if kind:
            q += " AND kind=?"
            args.append(kind)
        q += " ORDER BY t DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            rows = self.db.execute(q, args).fetchall()
        return [self._ev(r) for r in rows]

    def get_event(self, agent_id: str, event_id: int) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM events WHERE agent=? AND id=?", (agent_id, event_id)).fetchone()
        return self._ev(r) if r else None

    def mark_recalled(self, event_id: int) -> None:
        with self.lock:
            self.db.execute("UPDATE events SET recalled=recalled+1 WHERE id=?", (event_id,))
            self.db.commit()

    @staticmethod
    def _ev(r) -> dict:
        return {"id": r["id"], "t": r["t"], "kind": r["kind"], "source": r["source"],
                "speaker": r["speaker"], "appraisal": json.loads(r["appraisal"] or "null"),
                "effects": json.loads(r["effects"] or "null"), "recalled": r["recalled"]}

    # snapshots
    def add_snapshot(self, agent_id: str, t: float, data: dict) -> None:
        with self.lock:
            self.db.execute("INSERT INTO snapshots(agent, t, data) VALUES(?,?,?)",
                            (agent_id, t, json.dumps(data)))
            self.db.commit()

    def last_snapshot_t(self, agent_id: str) -> float:
        with self.lock:
            r = self.db.execute("SELECT MAX(t) AS t FROM snapshots WHERE agent=?", (agent_id,)).fetchone()
        return float(r["t"] or 0.0)

    def history(self, agent_id: str, since: float = 0, max_points: int = 600) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT t, data FROM snapshots WHERE agent=? AND t>=? ORDER BY t",
                                   (agent_id, since)).fetchall()
        step = max(1, len(rows) // max_points)
        out = [{"t": r["t"], **json.loads(r["data"])} for r in rows[::step]]
        if rows and (len(rows) - 1) % step:
            last = rows[-1]
            out.append({"t": last["t"], **json.loads(last["data"])})
        return out
