"""KnowledgeItemStore — externally-gathered knowledge with provenance + review states.

The backbone of the knowledge-gathering layer. Every item an agent gathers is
stored here with full provenance and a review STATE. Nothing becomes durable
"accepted" knowledge without explicit human approval — agents only ever produce
items in `pending_review`.

States (the human-approval gate):
    discovered -> summarized -> pending_review -> accepted | rejected -> archived

On accept(), the item is promoted into the durable layers (the vault wiki + the
memory store), with its provenance. Rejected/pending items never enter memory.

Stdlib sqlite, zero infra; swaps to Postgres behind the same shape later.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any, Sequence

from prepende_brain.private_fs import prepare_private_sqlite

STATES = ("discovered", "summarized", "pending_review", "accepted", "rejected", "archived")


class KnowledgeItemStore:
    def __init__(self, path: str = "./.engram/knowledge.db") -> None:
        self.path = prepare_private_sqlite(path)
        with self._c() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS items ("
                "id TEXT PRIMARY KEY, scope TEXT NOT NULL, state TEXT NOT NULL, "
                "title TEXT, source_url TEXT, author TEXT, published TEXT, retrieved REAL NOT NULL, "
                "summary TEXT, claims TEXT, confidence REAL, related_entities TEXT, "
                "related_projects TEXT, relevance REAL, contradiction TEXT, "
                "topic TEXT, agent TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)"
            )

    def _c(self) -> sqlite3.Connection:
        prepare_private_sqlite(self.path)
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=3000")
        prepare_private_sqlite(self.path)
        return conn

    def add(self, *, scope: str, title: str, source_url: str = "", author: str = "",
            published: str = "", summary: str = "", claims: list[str] | None = None,
            confidence: float = 0.0, related_entities: list[str] | None = None,
            related_projects: list[str] | None = None, relevance: float = 0.0,
            contradiction: str = "none", topic: str = "", agent: str = "",
            state: str = "pending_review") -> str:
        assert state in STATES, f"bad state {state}"
        iid = f"k_{uuid.uuid4().hex[:12]}"
        now = time.time()
        with self._c() as c:
            c.execute(
                "INSERT INTO items (id,scope,state,title,source_url,author,published,retrieved,"
                "summary,claims,confidence,related_entities,related_projects,relevance,contradiction,"
                "topic,agent,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (iid, scope, state, title, source_url, author, published, now,
                 summary, json.dumps(claims or []), confidence, json.dumps(related_entities or []),
                 json.dumps(related_projects or []), relevance, contradiction, topic, agent, now, now),
            )
        return iid

    def set_state(self, item_id: str, state: str) -> None:
        assert state in STATES, f"bad state {state}"
        with self._c() as c:
            c.execute("UPDATE items SET state=?, updated_at=? WHERE id=?", (state, time.time(), item_id))

    def get(self, item_id: str) -> dict[str, Any] | None:
        with self._c() as c:
            r = c.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            return self._row(r) if r else None

    def list(self, *, scope: str, state: str | None = None, limit: int = 50) -> Sequence[dict[str, Any]]:
        q = "SELECT * FROM items WHERE scope=?"
        args: list[Any] = [scope]
        if state:
            q += " AND state=?"
            args.append(state)
        q += " ORDER BY updated_at DESC LIMIT ?"
        args.append(limit)
        with self._c() as c:
            return [self._row(r) for r in c.execute(q, args).fetchall()]

    @staticmethod
    def _row(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["claims"] = json.loads(d.get("claims") or "[]")
        d["related_entities"] = json.loads(d.get("related_entities") or "[]")
        d["related_projects"] = json.loads(d.get("related_projects") or "[]")
        return d
