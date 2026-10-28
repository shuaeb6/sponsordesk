from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
from sponsordesk_core.models import PayrollErrorKind, PayrollFileStatus, Severity
from sponsordesk_core.taxonomy import Team, route
from sponsordesk_synth import Dataset, generate, write
from sponsordesk_synth.identifiers import (
    SAFE_EIN_RE,
    SAFE_EMAIL_RE,
    SAFE_PHONE_RE,
    SAFE_SSN_RE,
)
from sponsordesk_synth.tickets import TICKET_MIX


@pytest.fixture(scope="module")
def data() -> Dataset:
    return generate(42)


def test_same_seed_produces_identical_bytes(tmp_path: Path) -> None:
    a = write(generate(7), tmp_path / "a")
    b = write(generate(7), tmp_path / "b")
    assert a["sha256"] == b["sha256"]


def test_different_seed_changes_output(tmp_path: Path) -> None:
    a = write(generate(7), tmp_path / "a")
    b = write(generate(8), tmp_path / "b")
    assert a["sha256"]["tickets.jsonl"] != b["sha256"]["tickets.jsonl"]


def test_counts_match_spec(data: Dataset) -> None:
    assert len(data.sponsors) == 30
    assert len(data.tickets) == sum(TICKET_MIX.values()) == 500
    for call_type, expected in TICKET_MIX.items():
        assert sum(lb.call_type is call_type for lb in data.labels) == expected


def test_identifiers_are_never_real(data: Dataset) -> None:
    for p in data.participants:
        assert SAFE_SSN_RE.match(p.ssn), p.ssn
        assert SAFE_EMAIL_RE.match(p.email), p.email
    for s in data.sponsors:
        assert SAFE_EIN_RE.match(s.ein)
        for c in [s.primary_contact, *s.secondary_contacts]:
            assert SAFE_EMAIL_RE.match(c.email), c.email
            assert SAFE_PHONE_RE.match(c.phone), c.phone


def test_every_pii_span_appears_in_its_ticket(data: Dataset) -> None:
    tickets = {t.ticket_id: t for t in data.tickets}
    for label in data.labels:
        text = f"{tickets[label.ticket_id].subject}\n{tickets[label.ticket_id].body}"
        for span in label.pii:
            assert span.value in text, (label.ticket_id, span)


def test_references_resolve(data: Dataset) -> None:
    sponsors = {s.sponsor_id for s in data.sponsors}
    files = {f.file_id: f for f in data.payroll_files}
    people = {p.participant_id: p for p in data.participants}
    tickets = {t.ticket_id: t for t in data.tickets}
    for label in data.labels:
        sponsor_id = tickets[label.ticket_id].sponsor_id
        assert sponsor_id in sponsors
        for file_id in label.related_file_ids:
            assert files[file_id].sponsor_id == sponsor_id
        for pid in label.related_participant_ids:
            assert people[pid].sponsor_id == sponsor_id


def test_labels_follow_routing_policy(data: Dataset) -> None:
    for label in data.labels:
        assert label.team is route(label.call_type, label.root_cause, escalation=label.escalation)
        if label.escalation:
            assert label.team is Team.RELATIONSHIP_MANAGEMENT


def test_file_status_matches_injected_severity(data: Dataset) -> None:
    by_file: dict[str, set[Severity]] = {}
    for e in data.injected_errors:
        by_file.setdefault(e.file_id, set()).add(e.severity)
    for f in data.payroll_files:
        severities = by_file.get(f.file_id, set())
        if Severity.CRITICAL in severities:
            assert f.status is PayrollFileStatus.REJECTED
        elif f.status is PayrollFileStatus.REJECTED:
            pytest.fail(f"{f.file_id} rejected without a critical error")


def test_injected_errors_are_observable_in_csv(data: Dataset) -> None:
    files = {f.file_id: f for f in data.payroll_files}
    for e in data.injected_errors:
        text = data.payroll_csv[e.file_id]
        reader = csv.reader(io.StringIO(text))
        header = next(reader)
        rows = [dict(zip(header, r, strict=True)) for r in reader]
        if e.kind is PayrollErrorKind.HEADER_MISMATCH:
            assert (
                "pretax_deferral" not in header
                or "employer_match" not in header
                or "loan_repayment" not in header
                or "gross_pay" not in header
            )
            continue
        assert e.row_number is not None
        row = rows[e.row_number - 1]
        assert row["employee_id"] == e.employee_id
        if e.kind is PayrollErrorKind.MISSING_SSN:
            assert row["ssn"] == ""
        elif e.kind is PayrollErrorKind.INVALID_SSN_FORMAT:
            assert len(row["ssn"].replace("-", "")) == 8
        elif e.kind is PayrollErrorKind.PAY_DATE_MISMATCH:
            assert row["pay_date"] != files[e.file_id].pay_date.isoformat()


def test_no_label_data_in_tickets(data: Dataset) -> None:
    for t in data.tickets:
        dumped = t.model_dump_json()
        assert "call_type" not in dumped
        assert "root_cause" not in dumped
