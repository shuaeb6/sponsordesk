from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sponsordesk_console.main import app
from sponsordesk_console.store import check_close


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def test_quality_gates(client: TestClient) -> None:
    q = client.get("/api/metrics").json()["quality"]
    assert q["pii_leaks"] == 0
    assert q["classifier_accuracy"] >= 0.85
    assert q["routing_accuracy"] >= 0.85


def test_proposal_contains_no_raw_pii(client: TestClient) -> None:
    from sponsordesk_console.main import S

    for ticket_id in S.engine.report.test_ids[:40]:
        proposal = client.post(f"/api/tickets/{ticket_id}/triage").json()
        model_text = proposal["model_input_preview"] + proposal["draft_reply"]
        for span in S.engine.labels[ticket_id].pii:
            assert span.value not in model_text, (ticket_id, span)


def test_unresolved_ticket_cannot_close(client: TestClient) -> None:
    tid = "TKT-100010"
    client.post(f"/api/tickets/{tid}/triage")
    client.post(f"/api/tickets/{tid}/decision", json={"action": "approve", "reviewer": "qa"})
    r = client.post(f"/api/tickets/{tid}/close",
                    json={"reviewer": "qa", "confirmed": [], "note": "done done done"})
    assert r.status_code == 409
    events = [e["event"] for e in client.get(f"/api/audit?ticket_id={tid}").json()]
    assert "close_blocked" in events


def test_full_lifecycle_closes_only_with_all_criteria(client: TestClient) -> None:
    tid = "TKT-100020"
    proposal = client.post(f"/api/tickets/{tid}/triage").json()
    client.post(f"/api/tickets/{tid}/decision", json={"action": "approve", "reviewer": "qa"})
    client.post(f"/api/tickets/{tid}/resolve", json={"reviewer": "qa"})
    criteria = proposal["resolution_criteria"]
    partial = client.post(f"/api/tickets/{tid}/close",
                          json={"reviewer": "qa", "confirmed": criteria[:-1],
                                "note": "fixed and confirmed"})
    assert partial.status_code == 409
    ok = client.post(f"/api/tickets/{tid}/close",
                     json={"reviewer": "qa", "confirmed": criteria, "note": "fixed and confirmed"})
    assert ok.status_code == 200
    assert ok.json()["status"] == "closed"


def test_decision_requires_pending_proposal(client: TestClient) -> None:
    r = client.post("/api/tickets/TKT-100030/decision",
                    json={"action": "approve", "reviewer": "qa"})
    assert r.status_code == 409


@pytest.mark.parametrize("status", ["new", "pending_approval", "routed", "closed"])
def test_guard_rejects_every_non_resolved_status(status: str) -> None:
    assert not check_close(status, ["a"], ["a"], "long enough note").allowed


def test_guard_accepts_complete_resolution() -> None:
    assert check_close("resolved", ["a", "b"], ["b", "a"], "long enough note").allowed
