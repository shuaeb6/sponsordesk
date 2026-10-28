"""Payroll contribution files with deliberately injected defects.

Files are built as mutable drafts so that ticket scenarios can plant the exact
defect a ticket describes (for example, a missing loan repayment for the
participant the sponsor is asking about). ``finalize`` then derives status,
funding, totals, and the recordkeeper's validation messages from what is
actually in the rows, so the data and the ground truth cannot drift apart.
"""

from __future__ import annotations

import csv
import io
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sponsordesk_core.models import (
    ERROR_SEVERITY,
    EmploymentStatus,
    ExpectedPayroll,
    FundingStatus,
    InjectedError,
    Loan,
    LoanStatus,
    Participant,
    PayrollErrorKind,
    PayrollFile,
    PayrollFileStatus,
    PayrollFrequency,
    PlanRecord,
    Severity,
    Sponsor,
)

from sponsordesk_synth.entities import PERIODS_PER_YEAR, money
from sponsordesk_synth.identifiers import IdentifierFactory

COLUMNS = (
    "employee_id",
    "ssn",
    "first_name",
    "last_name",
    "dob",
    "hire_date",
    "termination_date",
    "status_code",
    "pay_date",
    "hours",
    "gross_pay",
    "pretax_deferral",
    "roth_deferral",
    "employer_match",
    "loan_repayment",
    "ytd_pretax",
)

HEADER_ALIASES = {
    "pretax_deferral": "pre_tax_def",
    "employer_match": "er_match",
    "loan_repayment": "loan_pmt",
    "gross_pay": "gross",
}

STATUS_CODES = {
    EmploymentStatus.ACTIVE: "A",
    EmploymentStatus.TERMINATED: "T",
    EmploymentStatus.LEAVE: "L",
}

ROW_LEVEL_KINDS = tuple(k for k in PayrollErrorKind if k is not PayrollErrorKind.HEADER_MISMATCH)


@dataclass
class DraftFile:
    file_id: str
    sponsor: Sponsor
    plan: PlanRecord
    pay_date: date
    period_start: date
    period_end: date
    received_at: datetime
    rows: list[dict[str, str]]
    header: list[str] = field(default_factory=lambda: list(COLUMNS))
    errors: list[InjectedError] = field(default_factory=list)
    pending_review: bool = False
    funding_override: FundingStatus | None = None

    @property
    def filename(self) -> str:
        return f"{self.sponsor.sponsor_id}_contrib_{self.pay_date:%Y%m%d}.csv"

    def row_for(self, employee_id: str) -> dict[str, str] | None:
        return next((r for r in self.rows if r["employee_id"] == employee_id), None)

    def has_kind(self, kind: PayrollErrorKind) -> bool:
        return any(e.kind is kind for e in self.errors)

    def severity(self) -> Severity | None:
        if any(e.severity is Severity.CRITICAL for e in self.errors):
            return Severity.CRITICAL
        return Severity.WARNING if self.errors else None


@dataclass
class PayrollBook:
    drafts: list[DraftFile]
    expected: list[ExpectedPayroll]

    def for_sponsor(self, sponsor_id: str) -> list[DraftFile]:
        return [d for d in self.drafts if d.sponsor.sponsor_id == sponsor_id]

    def missing_for(self, sponsor_id: str) -> list[ExpectedPayroll]:
        return [e for e in self.expected if e.sponsor_id == sponsor_id and e.file_id is None]


# --- Schedules -------------------------------------------------------------------


def _business_day_on_or_before(day: date) -> date:
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def pay_dates(
    frequency: PayrollFrequency, start: date, end: date, rng: random.Random
) -> list[date]:
    dates: list[date] = []
    if frequency in (PayrollFrequency.WEEKLY, PayrollFrequency.BIWEEKLY):
        step = 7 if frequency is PayrollFrequency.WEEKLY else 14
        day = start + timedelta(days=(4 - start.weekday()) % 7)  # first Friday
        day += timedelta(days=7 * rng.randint(0, 1)) if step == 14 else timedelta(0)
        while day <= end:
            dates.append(day)
            day += timedelta(days=step)
    else:
        cursor = date(start.year, start.month, 1)
        while cursor <= end:
            next_month = date(cursor.year + cursor.month // 12, cursor.month % 12 + 1, 1)
            last = next_month - timedelta(days=1)
            candidates = (
                [last] if frequency is PayrollFrequency.MONTHLY else [cursor.replace(day=15), last]
            )
            dates.extend(
                d for d in (_business_day_on_or_before(c) for c in candidates) if start <= d <= end
            )
            cursor = next_month
    return dates


def _periods_elapsed_this_year(frequency: PayrollFrequency, pay_date: date) -> int:
    fraction = (pay_date - date(pay_date.year, 1, 1)).days / 365.25
    return max(1, round(fraction * PERIODS_PER_YEAR[frequency]))


# --- Row math --------------------------------------------------------------------


def match_for(plan: PlanRecord, gross: Decimal, deferral_pct: Decimal) -> Decimal:
    total = Decimal(0)
    for tier in plan.employer_match:
        band = max(Decimal(0), min(deferral_pct, tier.band_to_pct) - tier.band_from_pct)
        total += gross * band / 100 * tier.match_rate_pct / 100
    return money(total)


def _row(
    sponsor: Sponsor,
    plan: PlanRecord,
    person: Participant,
    loan: Loan | None,
    pay_date: date,
    rng: random.Random,
) -> dict[str, str]:
    periods = PERIODS_PER_YEAR[sponsor.payroll_frequency]
    on_leave = person.status is EmploymentStatus.LEAVE
    gross = (
        money(0)
        if on_leave
        else money(float(person.annual_salary) / periods * rng.uniform(0.97, 1.05))
    )
    pretax = money(gross * person.pretax_rate_pct / 100)
    roth = money(gross * person.roth_rate_pct / 100)
    ytd = min(
        money(pretax * _periods_elapsed_this_year(sponsor.payroll_frequency, pay_date)),
        plan.annual_deferral_limit,
    )
    loan_payment = loan.payment_per_period if loan and not on_leave else money(0)
    hours = {
        PayrollFrequency.WEEKLY: 40,
        PayrollFrequency.BIWEEKLY: 80,
        PayrollFrequency.SEMIMONTHLY: 86.67,
        PayrollFrequency.MONTHLY: 173.33,
    }
    return {
        "employee_id": person.employee_id,
        "ssn": person.ssn,
        "first_name": person.first_name,
        "last_name": person.last_name,
        "dob": person.dob.isoformat(),
        "hire_date": person.hire_date.isoformat(),
        "termination_date": person.termination_date.isoformat() if person.termination_date else "",
        "status_code": STATUS_CODES[person.status],
        "pay_date": pay_date.isoformat(),
        "hours": "0.00" if on_leave else f"{hours[sponsor.payroll_frequency]:.2f}",
        "gross_pay": str(gross),
        "pretax_deferral": str(pretax),
        "roth_deferral": str(roth),
        "employer_match": str(
            match_for(plan, gross, person.pretax_rate_pct + person.roth_rate_pct)
        ),
        "loan_repayment": str(loan_payment),
        "ytd_pretax": str(ytd),
    }


# --- Building --------------------------------------------------------------------


def build_payroll_book(
    rng: random.Random,
    sponsors: list[Sponsor],
    plans: dict[str, PlanRecord],
    participants: dict[str, list[Participant]],
    loans: dict[str, list[Loan]],
    ids: IdentifierFactory,
    *,
    window_start: date,
    as_of: date,
) -> PayrollBook:
    drafts: list[DraftFile] = []
    expected: list[ExpectedPayroll] = []
    counter = 0
    for sponsor in sponsors:
        plan = plans[sponsor.sponsor_id]
        people = participants[sponsor.sponsor_id]
        loan_by_person = {
            ln.participant_id: ln
            for ln in loans[sponsor.sponsor_id]
            if ln.status is LoanStatus.ACTIVE
        }
        dates = pay_dates(sponsor.payroll_frequency, window_start, as_of - timedelta(days=1), rng)
        step = timedelta(days=365 // PERIODS_PER_YEAR[sponsor.payroll_frequency])
        for position, pay_date in enumerate(dates):
            is_latest = position == len(dates) - 1
            if not is_latest and rng.random() < 0.03:
                expected.append(
                    ExpectedPayroll(sponsor_id=sponsor.sponsor_id, pay_date=pay_date, file_id=None)
                )
                continue
            counter += 1
            file_id = f"PF-{counter:05d}"
            period_end = pay_date - timedelta(days=3)
            period_start = period_end - step + timedelta(days=1)
            rows = [
                _row(sponsor, plan, p, loan_by_person.get(p.participant_id), pay_date, rng)
                for p in people
                if p.hire_date <= period_end
                and (p.termination_date is None or p.termination_date >= period_start)
            ]
            late = rng.random() < 0.08
            received = datetime.combine(
                pay_date + timedelta(days=rng.randint(1, 3) if late else -rng.randint(0, 2)),
                time(rng.randint(7, 19), rng.randint(0, 59)),
            )
            draft = DraftFile(
                file_id=file_id,
                sponsor=sponsor,
                plan=plan,
                pay_date=pay_date,
                period_start=period_start,
                period_end=period_end,
                received_at=received,
                rows=rows,
                pending_review=is_latest and rng.random() < 0.25,
            )
            drafts.append(draft)
            expected.append(
                ExpectedPayroll(sponsor_id=sponsor.sponsor_id, pay_date=pay_date, file_id=file_id)
            )

    injector = ErrorInjector(rng, participants, loans, ids)
    for draft in drafts:
        if draft.pending_review or rng.random() > 0.26:
            continue
        for kind in rng.sample(ROW_LEVEL_KINDS, k=rng.choices((1, 2, 3), weights=(65, 25, 10))[0]):
            injector.inject(draft, kind)
        if rng.random() < 0.08:
            injector.inject(draft, PayrollErrorKind.HEADER_MISMATCH)
    return PayrollBook(drafts=drafts, expected=expected)


# --- Defect injection ------------------------------------------------------------


class ErrorInjector:
    """Plants a single defect in a draft and records it as ground truth."""

    def __init__(
        self,
        rng: random.Random,
        participants: dict[str, list[Participant]],
        loans: dict[str, list[Loan]],
        ids: IdentifierFactory,
    ) -> None:
        self._rng = rng
        self._participants = participants
        self._loans = loans
        self._ids = ids
        self._touched: dict[str, set[str]] = {}

    def inject(
        self, draft: DraftFile, kind: PayrollErrorKind, employee_id: str | None = None
    ) -> InjectedError | None:
        handler = getattr(self, f"_inject_{kind.value}")
        result: tuple[int | None, str | None, str] | None = handler(draft, employee_id)
        if result is None:
            return None
        row_number, emp, detail = result
        error = InjectedError(
            file_id=draft.file_id,
            kind=kind,
            severity=ERROR_SEVERITY[kind],
            row_number=row_number,
            employee_id=emp,
            detail=detail,
        )
        draft.errors.append(error)
        if emp:
            self._touched.setdefault(draft.file_id, set()).add(emp)
        return error

    # Each handler returns (1-based data row number, employee_id, detail) or None.

    def _pick(
        self, draft: DraftFile, employee_id: str | None, predicate: object = None
    ) -> tuple[int, dict[str, str]] | None:
        touched = self._touched.get(draft.file_id, set())
        candidates = [
            (i, r)
            for i, r in enumerate(draft.rows)
            if (employee_id is None or r["employee_id"] == employee_id)
            and (employee_id is not None or r["employee_id"] not in touched)
            and (predicate is None or (callable(predicate) and predicate(r)))
        ]
        return self._rng.choice(candidates) if candidates else None

    def _inject_missing_ssn(self, draft: DraftFile, emp: str | None) -> tuple[int, str, str] | None:
        picked = self._pick(draft, emp)
        if not picked:
            return None
        i, row = picked
        row["ssn"] = ""
        return i + 1, row["employee_id"], "SSN field is blank"

    def _inject_invalid_ssn_format(
        self, draft: DraftFile, emp: str | None
    ) -> tuple[int, str, str] | None:
        picked = self._pick(draft, emp, lambda r: bool(r["ssn"]))
        if not picked:
            return None
        i, row = picked
        row["ssn"] = row["ssn"][:-1]
        return i + 1, row["employee_id"], "SSN has 8 digits"

    def _inject_duplicate_row(
        self, draft: DraftFile, emp: str | None
    ) -> tuple[int, str, str] | None:
        picked = self._pick(draft, emp)
        if not picked:
            return None
        i, row = picked
        # Append rather than insert so row numbers recorded for earlier defects stay valid.
        draft.rows.append(dict(row))
        return len(draft.rows), row["employee_id"], f"duplicates data row {i + 1}"

    def _inject_deferral_exceeds_gross(
        self, draft: DraftFile, emp: str | None
    ) -> tuple[int, str, str] | None:
        picked = self._pick(draft, emp, lambda r: Decimal(r["gross_pay"]) > 0)
        if not picked:
            return None
        i, row = picked
        row["pretax_deferral"] = str(money(Decimal(row["gross_pay"]) * Decimal("1.35")))
        return i + 1, row["employee_id"], "pre-tax deferral is greater than gross pay"

    def _inject_negative_amount(
        self, draft: DraftFile, emp: str | None
    ) -> tuple[int, str, str] | None:
        picked = self._pick(draft, emp, lambda r: Decimal(r["employer_match"]) > 0)
        if not picked:
            return None
        i, row = picked
        row["employer_match"] = f"-{row['employer_match']}"
        return i + 1, row["employee_id"], "employer match is negative"

    def _inject_deferral_rate_mismatch(
        self, draft: DraftFile, emp: str | None
    ) -> tuple[int, str, str] | None:
        picked = self._pick(draft, emp, lambda r: Decimal(r["pretax_deferral"]) > 0)
        if not picked:
            return None
        i, row = picked
        person = self._person(draft, row["employee_id"])
        elected = person.pending_rate_change_pct or person.pretax_rate_pct
        stale = (
            person.pretax_rate_pct
            if person.pending_rate_change_pct
            else max(Decimal(1), person.pretax_rate_pct - self._rng.choice((1, 2, 3)))
        )
        row["pretax_deferral"] = str(money(Decimal(row["gross_pay"]) * stale / 100))
        return i + 1, row["employee_id"], f"deferral reflects {stale}% but election is {elected}%"

    def _inject_match_miscalculated(
        self, draft: DraftFile, emp: str | None
    ) -> tuple[int, str, str] | None:
        if not draft.plan.employer_match:
            return None
        picked = self._pick(draft, emp, lambda r: Decimal(r["employer_match"]) > 0)
        if not picked:
            return None
        i, row = picked
        row["employer_match"] = str(money(Decimal(row["employer_match"]) / 2))
        return i + 1, row["employee_id"], "employer match is half of the plan formula"

    def _inject_missing_loan_repayment(
        self, draft: DraftFile, emp: str | None
    ) -> tuple[int, str, str] | None:
        picked = self._pick(draft, emp, lambda r: Decimal(r["loan_repayment"]) > 0)
        if not picked:
            return None
        i, row = picked
        row["loan_repayment"] = "0.00"
        return i + 1, row["employee_id"], "active loan but no repayment withheld"

    def _inject_terminated_with_contribution(
        self, draft: DraftFile, emp: str | None
    ) -> tuple[int, str, str] | None:
        termed = [
            p
            for p in self._participants[draft.sponsor.sponsor_id]
            if p.termination_date
            and p.termination_date < draft.period_start
            and draft.row_for(p.employee_id) is None
            and (emp is None or p.employee_id == emp)
        ]
        if not termed:
            return None
        person = self._rng.choice(termed)
        template = dict(self._rng.choice(draft.rows)) if draft.rows else None
        if template is None:
            return None
        gross = money(person.annual_salary / 26)
        template.update(
            {
                "employee_id": person.employee_id,
                "ssn": person.ssn,
                "first_name": person.first_name,
                "last_name": person.last_name,
                "dob": person.dob.isoformat(),
                "hire_date": person.hire_date.isoformat(),
                "termination_date": person.termination_date.isoformat()
                if person.termination_date
                else "",
                "status_code": "T",
                "gross_pay": str(gross),
                "pretax_deferral": str(money(gross * Decimal("0.05"))),
                "roth_deferral": "0.00",
                "employer_match": str(match_for(draft.plan, gross, Decimal(5))),
                "loan_repayment": "0.00",
            }
        )
        draft.rows.append(template)
        return (
            len(draft.rows),
            person.employee_id,
            "contribution for employee terminated before the pay period",
        )

    def _inject_over_annual_limit(
        self, draft: DraftFile, emp: str | None
    ) -> tuple[int, str, str] | None:
        picked = self._pick(draft, emp, lambda r: Decimal(r["pretax_deferral"]) > 0)
        if not picked:
            return None
        i, row = picked
        over = draft.plan.annual_deferral_limit + money(self._rng.uniform(150, 2400))
        row["ytd_pretax"] = str(over)
        return i + 1, row["employee_id"], "year-to-date pre-tax deferrals exceed the annual limit"

    def _inject_unknown_employee(
        self, draft: DraftFile, emp: str | None
    ) -> tuple[int, str, str] | None:
        if emp is not None or not draft.rows:
            return None
        row = dict(self._rng.choice(draft.rows))
        known = {p.employee_id for p in self._participants[draft.sponsor.sponsor_id]}
        new_id = row["employee_id"]
        while new_id in known:
            new_id = f"E{self._rng.randint(10000, 99999)}"
        row.update(
            {
                "employee_id": new_id,
                "ssn": self._ids.ssn(),
                "hire_date": (draft.period_end - timedelta(days=5)).isoformat(),
            }
        )
        draft.rows.append(row)
        return len(draft.rows), new_id, "employee is not on the plan census"

    def _inject_pay_date_mismatch(
        self, draft: DraftFile, emp: str | None
    ) -> tuple[int, str, str] | None:
        picked = self._pick(draft, emp)
        if not picked:
            return None
        i, row = picked
        row["pay_date"] = (draft.pay_date - timedelta(days=14)).isoformat()
        return i + 1, row["employee_id"], "row pay date does not match the file pay date"

    def _inject_header_mismatch(
        self, draft: DraftFile, emp: str | None
    ) -> tuple[None, None, str] | None:
        if draft.has_kind(PayrollErrorKind.HEADER_MISMATCH):
            return None
        canonical = self._rng.choice(sorted(HEADER_ALIASES))
        draft.header[draft.header.index(canonical)] = HEADER_ALIASES[canonical]
        return None, None, f"column '{canonical}' sent as '{HEADER_ALIASES[canonical]}'"

    def _person(self, draft: DraftFile, employee_id: str) -> Participant:
        return next(
            p for p in self._participants[draft.sponsor.sponsor_id] if p.employee_id == employee_id
        )


# --- Finalizing --------------------------------------------------------------------


_CRITICAL_MESSAGES = {
    PayrollErrorKind.MISSING_SSN: "Row {row}: SSN is required (employee {emp})",
    PayrollErrorKind.INVALID_SSN_FORMAT: "Row {row}: SSN is not 9 digits (employee {emp})",
    PayrollErrorKind.DUPLICATE_ROW: "Row {row}: duplicate record for employee {emp}",
    PayrollErrorKind.DEFERRAL_EXCEEDS_GROSS: "Row {row}: deferral exceeds gross pay (employee {emp})",
    PayrollErrorKind.NEGATIVE_AMOUNT: "Row {row}: negative contribution amount (employee {emp})",
    PayrollErrorKind.UNKNOWN_EMPLOYEE: "Row {row}: employee {emp} not found on census",
    PayrollErrorKind.PAY_DATE_MISMATCH: "Row {row}: pay date does not match file header",
    PayrollErrorKind.HEADER_MISMATCH: "File layout: unrecognized column header",
}

_WARNING_SUMMARIES = {
    PayrollErrorKind.DEFERRAL_RATE_MISMATCH: "deferral amounts differ from elections on file",
    PayrollErrorKind.MATCH_MISCALCULATED: "employer match differs from plan formula",
    PayrollErrorKind.MISSING_LOAN_REPAYMENT: "expected loan repayments not received",
    PayrollErrorKind.TERMINATED_WITH_CONTRIBUTION: "contributions for terminated employees",
    PayrollErrorKind.OVER_ANNUAL_LIMIT: "year-to-date deferrals over the annual limit",
}


def finalize(draft: DraftFile, as_of: date) -> PayrollFile:
    severity = draft.severity()
    if severity is Severity.CRITICAL:
        status = PayrollFileStatus.REJECTED
    elif draft.pending_review:
        status = PayrollFileStatus.PENDING_REVIEW
    elif severity is Severity.WARNING:
        status = PayrollFileStatus.ACCEPTED_WITH_WARNINGS
    else:
        status = PayrollFileStatus.ACCEPTED

    if status is PayrollFileStatus.REJECTED:
        funding = FundingStatus.NOT_APPLICABLE
    elif draft.funding_override is not None:
        funding = draft.funding_override
    elif status is PayrollFileStatus.PENDING_REVIEW or (as_of - draft.pay_date).days < 3:
        funding = FundingStatus.PENDING
    else:
        funding = FundingStatus.RECEIVED

    messages: list[str] = []
    for error in sorted(draft.errors, key=lambda e: (e.row_number or 0, e.kind.value)):
        template = _CRITICAL_MESSAGES.get(error.kind)
        if template:
            messages.append(template.format(row=error.row_number, emp=error.employee_id))
    warning_counts: dict[PayrollErrorKind, int] = {}
    for error in draft.errors:
        if error.kind in _WARNING_SUMMARIES:
            warning_counts[error.kind] = warning_counts.get(error.kind, 0) + 1
    for kind, count in sorted(warning_counts.items(), key=lambda kv: kv[0].value):
        messages.append(f"Warning: {count} record(s) with {_WARNING_SUMMARIES[kind]}")

    def total(column: str) -> Decimal:
        return money(sum((Decimal(r[column]) for r in draft.rows), Decimal(0)))

    return PayrollFile(
        file_id=draft.file_id,
        sponsor_id=draft.sponsor.sponsor_id,
        filename=draft.filename,
        pay_date=draft.pay_date,
        period_start=draft.period_start,
        period_end=draft.period_end,
        received_at=draft.received_at,
        status=status,
        funding_status=funding,
        row_count=len(draft.rows),
        total_pretax=total("pretax_deferral"),
        total_roth=total("roth_deferral"),
        total_match=total("employer_match"),
        total_loan_repayment=total("loan_repayment"),
        validation_messages=messages,
    )


def render_csv(draft: DraftFile) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(draft.header)
    for row in draft.rows:
        writer.writerow([row[column] for column in COLUMNS])
    return buffer.getvalue()
