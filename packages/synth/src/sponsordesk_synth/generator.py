"""Orchestrates generation and writes the dataset to disk."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from faker import Faker
from pydantic import BaseModel
from sponsordesk_core.models import (
    ExpectedPayroll,
    InjectedError,
    Loan,
    Participant,
    PayrollFile,
    PlanRecord,
    Sponsor,
    Ticket,
    TicketLabel,
)
from sponsordesk_core.taxonomy import CallType

from sponsordesk_synth.entities import build_loans, build_participants, build_plan, build_sponsors
from sponsordesk_synth.identifiers import IdentifierFactory
from sponsordesk_synth.payroll import PayrollBook, build_payroll_book, finalize, render_csv
from sponsordesk_synth.tickets import TICKET_MIX, TicketWriter, World

DEFAULT_AS_OF = date(2026, 10, 26)


@dataclass
class Dataset:
    seed: int
    as_of: date
    sponsors: list[Sponsor]
    plans: list[PlanRecord]
    participants: list[Participant]
    loans: list[Loan]
    payroll_files: list[PayrollFile]
    expected_payroll: list[ExpectedPayroll]
    payroll_csv: dict[str, str]
    injected_errors: list[InjectedError]
    tickets: list[Ticket]
    labels: list[TicketLabel]


def generate(
    seed: int = 42,
    *,
    sponsors: int = 30,
    as_of: date = DEFAULT_AS_OF,
    mix: dict[CallType, int] | None = None,
) -> Dataset:
    rng = random.Random(seed)
    fake = Faker("en_US")
    fake.seed_instance(seed)
    ids = IdentifierFactory(random.Random(seed + 1))

    sponsor_list = build_sponsors(rng, fake, ids, sponsors, as_of)
    plans = {s.sponsor_id: build_plan(rng, ids, s) for s in sponsor_list}
    people = {
        s.sponsor_id: build_participants(rng, fake, ids, s, plans[s.sponsor_id], as_of)
        for s in sponsor_list
    }
    loans = {
        s.sponsor_id: build_loans(rng, s, plans[s.sponsor_id], people[s.sponsor_id], as_of)
        for s in sponsor_list
    }
    book: PayrollBook = build_payroll_book(
        rng, sponsor_list, plans, people, loans, ids,
        window_start=date(as_of.year, 7, 1), as_of=as_of,
    )
    from sponsordesk_synth.payroll import ErrorInjector

    world = World(sponsor_list, plans, people, loans, book,
                  ErrorInjector(rng, people, loans, ids), as_of)
    tickets, labels = TicketWriter(rng, world).build(mix or TICKET_MIX)

    return Dataset(
        seed=seed,
        as_of=as_of,
        sponsors=sponsor_list,
        plans=list(plans.values()),
        participants=[p for s in sponsor_list for p in people[s.sponsor_id]],
        loans=[ln for s in sponsor_list for ln in loans[s.sponsor_id]],
        payroll_files=[finalize(d, as_of) for d in book.drafts],
        expected_payroll=book.expected,
        payroll_csv={d.file_id: render_csv(d) for d in book.drafts},
        injected_errors=[e for d in book.drafts for e in d.errors],
        tickets=tickets,
        labels=labels,
    )


def _dump(records: list[Any]) -> str:
    return json.dumps(
        [r.model_dump(mode="json") if isinstance(r, BaseModel) else r for r in records],
        indent=2, sort_keys=True,
    ) + "\n"


def _jsonl(records: list[BaseModel]) -> str:
    return "".join(json.dumps(r.model_dump(mode="json"), sort_keys=True) + "\n" for r in records)


def write(dataset: Dataset, out: Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {
        "sponsors.json": _dump(dataset.sponsors),
        "plans.json": _dump(dataset.plans),
        "participants.json": _dump(dataset.participants),
        "loans.json": _dump(dataset.loans),
        "payroll_files.json": _dump(dataset.payroll_files),
        "expected_payroll.json": _dump(dataset.expected_payroll),
        "tickets.jsonl": _jsonl(list(dataset.tickets)),
        "ground_truth/ticket_labels.jsonl": _jsonl(list(dataset.labels)),
        "ground_truth/payroll_errors.json": _dump(dataset.injected_errors),
    }
    by_id = {f.file_id: f for f in dataset.payroll_files}
    for file_id, text in dataset.payroll_csv.items():
        meta = by_id[file_id]
        files[f"payroll/{meta.sponsor_id}/{meta.filename}"] = text

    digests: dict[str, str] = {}
    for rel, text in sorted(files.items()):
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        digests[rel] = hashlib.sha256(text.encode()).hexdigest()

    counts: dict[str, int] = {}
    for label in dataset.labels:
        counts[label.call_type.value] = counts.get(label.call_type.value, 0) + 1
    manifest = {
        "seed": dataset.seed,
        "as_of": dataset.as_of.isoformat(),
        "counts": {
            "sponsors": len(dataset.sponsors),
            "participants": len(dataset.participants),
            "loans": len(dataset.loans),
            "payroll_files": len(dataset.payroll_files),
            "missing_payroll_files": sum(1 for e in dataset.expected_payroll if e.file_id is None),
            "injected_errors": len(dataset.injected_errors),
            "tickets": len(dataset.tickets),
            "tickets_by_call_type": dict(sorted(counts.items())),
        },
        "sha256": digests,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
