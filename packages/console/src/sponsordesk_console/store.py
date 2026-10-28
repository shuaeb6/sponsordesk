"""Workflow state, the close guard, and the audit log (SQLite)."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("sponsordesk.audit")


@dataclass
class CloseCheck:
    allowed: bool
    reasons: list[str]


def check_close(status: str, criteria: list[str], confirmed: list[str], note: str) -> CloseCheck:
    """Hard rule: a ticket may close only when it is resolved and every criterion is met."""
    reasons: list[str] = []
    if status != "resolved":
        reasons.append(f"Ticket status is '{status}'; only resolved tickets can be closed.")
    missing = [c for c in criteria if c not in set(confirmed)]
    if missing:
        reasons.append("Unconfirmed resolution criteria: " + "; ".join(missing))
    if len(note.strip()) < 10:
        reasons.append("A resolution note of at least 10 characters is required.")
    return CloseCheck(allowed=not reasons, reasons=reasons)


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS workflow (
                ticket_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                proposal TEXT,
                decision TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at TEXT NOT NULL,
                ticket_id TEXT NOT NULL,
                actor TEXT NOT NULL,
                event TEXT NOT NULL,
                detail TEXT NOT NULL
            );
            """
        )

    def _now(self) -> str:
        return datetime.now(UTC).isoformat(timespec="seconds")

    def audit(self, ticket_id: str, actor: str, event: str, detail: dict[str, Any]) -> None:
        payload = json.dumps(detail, default=str)
        with self._lock:
            self._db.execute(
                "INSERT INTO audit (at, ticket_id, actor, event, detail) VALUES (?, ?, ?, ?, ?)",
                (self._now(), ticket_id, actor, event, payload),
            )
            self._db.commit()
        log.info(
            json.dumps(
                {"event": event, "ticket_id": ticket_id, "actor": actor, "detail": detail},
                default=str,
            )
        )

    def status(self, ticket_id: str) -> str:
        row = self._db.execute(
            "SELECT status FROM workflow WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        return str(row["status"]) if row else "new"

    def state(self, ticket_id: str) -> dict[str, Any]:
        row = self._db.execute(
            "SELECT * FROM workflow WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        if not row:
            return {"status": "new", "proposal": None, "decision": None}
        return {
            "status": row["status"],
            "proposal": json.loads(row["proposal"]) if row["proposal"] else None,
            "decision": json.loads(row["decision"]) if row["decision"] else None,
        }

    def statuses(self) -> dict[str, str]:
        rows = self._db.execute("SELECT ticket_id, status FROM workflow").fetchall()
        return {r["ticket_id"]: r["status"] for r in rows}

    def set(
        self,
        ticket_id: str,
        status: str,
        *,
        proposal: dict[str, Any] | None = None,
        decision: dict[str, Any] | None = None,
    ) -> None:
        current = self.state(ticket_id)
        with self._lock:
            self._db.execute(
                "INSERT INTO workflow (ticket_id, status, proposal, decision, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(ticket_id) DO UPDATE SET "
                "status = excluded.status, proposal = excluded.proposal, "
                "decision = excluded.decision, updated_at = excluded.updated_at",
                (
                    ticket_id,
                    status,
                    json.dumps(proposal if proposal is not None else current["proposal"]),
                    json.dumps(decision if decision is not None else current["decision"]),
                    self._now(),
                ),
            )
            self._db.commit()

    def events(self, ticket_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM audit"
        args: tuple[Any, ...] = ()
        if ticket_id:
            query += " WHERE ticket_id = ?"
            args = (ticket_id,)
        query += " ORDER BY id DESC LIMIT ?"
        rows = self._db.execute(query, (*args, limit)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]

    def counts(self) -> dict[str, int]:
        rows = self._db.execute("SELECT event, COUNT(*) AS n FROM audit GROUP BY event").fetchall()
        return {r["event"]: int(r["n"]) for r in rows}
