"""Reviewer console: HTTP API and static UI."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sponsordesk_core.taxonomy import CallType, Team
from sponsordesk_synth import generate

from sponsordesk_console.engine import RESOLUTION_CRITERIA, TEAM_LABELS, Engine
from sponsordesk_console.store import Store, check_close

logging.basicConfig(level=logging.INFO, format="%(message)s")
STATIC = Path(__file__).parent / "static"


class State:
    engine: Engine
    store: Store
    quality: dict[str, Any]


S = State()


def _quality(engine: Engine) -> dict[str, Any]:
    """Held-out routing accuracy and corpus-wide PII leakage, computed at startup."""
    test_ids = engine.report.test_ids
    routed_ok = sum(engine.triage(i).team == engine.labels[i].team.value for i in test_ids)
    leaks, spans = 0, 0
    for t in engine.data.tickets:
        subject, body, _ = engine.redact_ticket(t)
        text = f"{subject}\n{body}"
        for span in engine.labels[t.ticket_id].pii:
            spans += 1
            leaks += span.value in text
    return {
        "classifier_accuracy": round(engine.report.accuracy, 3),
        "classifier_macro_f1": round(engine.report.macro_f1, 3),
        "routing_accuracy": round(routed_ok / len(test_ids), 3),
        "pii_spans": spans,
        "pii_leaks": leaks,
        "test_size": engine.report.test_size,
        "train_size": engine.report.train_size,
        "labels": engine.report.labels,
        "confusion": engine.report.confusion,
    }


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    started = time.perf_counter()
    S.engine = Engine(generate(int(os.environ.get("SYNTH_SEED", "42"))))
    S.store = Store(os.environ.get("WORKFLOW_DB", ":memory:"))
    S.quality = _quality(S.engine)
    logging.getLogger("sponsordesk").info(
        '{"event": "startup", "seconds": %.2f}', time.perf_counter() - started)
    yield


app = FastAPI(title="SponsorDesk", version="0.1.0", lifespan=lifespan)


# --- reads ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    return {
        "call_types": [c.value for c in CallType],
        "teams": [{"id": t.value, "label": TEAM_LABELS[t]} for t in Team],
    }


@app.get("/api/tickets")
def list_tickets(
    status: str | None = None, q: str | None = None, limit: int = Query(500, le=500)
) -> list[dict[str, Any]]:
    statuses = S.store.statuses()
    out = []
    for t in sorted(S.engine.tickets.values(), key=lambda t: t.created_at, reverse=True):
        st = statuses.get(t.ticket_id, "new")
        if status and st != status:
            continue
        if q and q.lower() not in f"{t.ticket_id} {t.subject}".lower():
            continue
        sponsor = S.engine.sponsors[t.sponsor_id]
        subject, _, _ = S.engine.redact_ticket(t, "human_reviewer")
        out.append({
            "ticket_id": t.ticket_id, "subject": subject, "sponsor": sponsor.legal_name,
            "created_at": t.created_at.isoformat(), "priority": t.priority.value,
            "channel": t.channel.value, "status": st,
        })
        if len(out) >= limit:
            break
    return out


@app.get("/api/tickets/{ticket_id}")
def get_ticket(ticket_id: str) -> dict[str, Any]:
    t = _ticket(ticket_id)
    sponsor = S.engine.sponsors[t.sponsor_id]
    subject, body, counts = S.engine.redact_ticket(t, "human_reviewer")
    return {
        "ticket_id": t.ticket_id,
        "sponsor": {"id": sponsor.sponsor_id, "name": sponsor.legal_name,
                    "tier": sponsor.service_tier, "rm": sponsor.relationship_manager},
        "created_at": t.created_at.isoformat(),
        "channel": t.channel.value,
        "priority": t.priority.value,
        "requester": {"name": t.requester.name, "title": t.requester.title},
        "subject": subject,
        "body": body,
        "masked_counts": counts,
        **S.store.state(ticket_id),
        "events": S.store.events(ticket_id),
    }


@app.get("/api/metrics")
def metrics() -> dict[str, Any]:
    statuses = S.store.statuses()
    events = S.store.counts()
    decided = events.get("approved", 0) + events.get("edited", 0) + events.get("rejected", 0)
    by_status: dict[str, int] = {}
    for st in statuses.values():
        by_status[st] = by_status.get(st, 0) + 1
    return {
        "tickets": len(S.engine.tickets),
        "by_status": by_status,
        "events": events,
        "approval_rate": round((events.get("approved", 0) + events.get("edited", 0)) / decided, 3)
        if decided else None,
        "quality": S.quality,
    }


@app.get("/api/audit")
def audit(ticket_id: str | None = None, limit: int = Query(100, le=500)) -> list[dict[str, Any]]:
    return S.store.events(ticket_id, limit)


# --- actions -------------------------------------------------------------------------


@app.post("/api/tickets/{ticket_id}/triage")
def triage(ticket_id: str) -> dict[str, Any]:
    _ticket(ticket_id)
    if S.store.status(ticket_id) in ("closed",):
        raise HTTPException(409, "Ticket is closed.")
    proposal = S.engine.triage(ticket_id).as_dict()
    S.store.set(ticket_id, "pending_approval", proposal=proposal)
    S.store.audit(ticket_id, "triage-engine", "proposed", {
        "call_type": proposal["call_type"], "confidence": proposal["confidence"],
        "team": proposal["team"], "root_cause": proposal["root_cause"],
        "redactions": proposal["redaction_counts"],
    })
    return proposal


class Decision(BaseModel):
    action: Literal["approve", "edit", "reject"]
    reviewer: str = Field(min_length=2, max_length=60)
    draft_reply: str | None = Field(default=None, max_length=5000)
    team: Team | None = None
    reason: str | None = Field(default=None, max_length=500)


@app.post("/api/tickets/{ticket_id}/decision")
def decide(ticket_id: str, decision: Decision) -> dict[str, Any]:
    _ticket(ticket_id)
    state = S.store.state(ticket_id)
    if state["status"] != "pending_approval" or not state["proposal"]:
        raise HTTPException(409, "No proposal is awaiting approval for this ticket.")
    proposal = state["proposal"]
    if decision.action == "reject":
        S.store.set(ticket_id, "new", decision=decision.model_dump(mode="json"))
        S.store.audit(ticket_id, decision.reviewer, "rejected", {"reason": decision.reason})
        return S.store.state(ticket_id)
    edited = decision.action == "edit" and (
        (decision.draft_reply and decision.draft_reply != proposal["draft_reply"])
        or (decision.team and decision.team.value != proposal["team"])
    )
    final = {
        "team": (decision.team.value if decision.team else proposal["team"]),
        "draft_reply": decision.draft_reply or proposal["draft_reply"],
        "reviewer": decision.reviewer,
    }
    S.store.set(ticket_id, "routed", decision=final)
    S.store.audit(ticket_id, decision.reviewer, "edited" if edited else "approved", {
        "team": final["team"], "misroute": final["team"] != proposal["team"],
    })
    return S.store.state(ticket_id)


class Resolve(BaseModel):
    reviewer: str = Field(min_length=2, max_length=60)


@app.post("/api/tickets/{ticket_id}/resolve")
def resolve(ticket_id: str, body: Resolve) -> dict[str, Any]:
    _ticket(ticket_id)
    if S.store.status(ticket_id) != "routed":
        raise HTTPException(409, "Only routed tickets can be marked resolved.")
    S.store.set(ticket_id, "resolved")
    S.store.audit(ticket_id, body.reviewer, "resolved", {})
    return S.store.state(ticket_id)


class Close(BaseModel):
    reviewer: str = Field(min_length=2, max_length=60)
    confirmed: list[str] = Field(default_factory=list)
    note: str = Field(default="", max_length=2000)


@app.post("/api/tickets/{ticket_id}/close")
def close(ticket_id: str, body: Close) -> dict[str, Any]:
    _ticket(ticket_id)
    state = S.store.state(ticket_id)
    call_type = CallType(state["proposal"]["call_type"]) if state["proposal"] else None
    criteria = RESOLUTION_CRITERIA[call_type] if call_type else ["Ticket has been triaged"]
    check = check_close(state["status"], criteria, body.confirmed, body.note)
    if not check.allowed:
        S.store.audit(ticket_id, body.reviewer, "close_blocked", {"reasons": check.reasons})
        raise HTTPException(409, {"message": "Close blocked by guard", "reasons": check.reasons})
    S.store.set(ticket_id, "closed")
    S.store.audit(ticket_id, body.reviewer, "closed", {"note_length": len(body.note)})
    return S.store.state(ticket_id)


def _ticket(ticket_id: str) -> Any:
    t = S.engine.tickets.get(ticket_id)
    if t is None:
        raise HTTPException(404, "Ticket not found.")
    return t


# --- UI --------------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


def run() -> None:
    uvicorn.run("sponsordesk_console.main:app", host="0.0.0.0",  # noqa: S104
                port=int(os.environ.get("PORT", "8000")), log_level="info")
