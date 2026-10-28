"""Domain records shared by the generator, the mock API, and the MCP servers.

Money is always ``Decimal`` and serializes to a string in JSON so that no
float rounding creeps into contribution math.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from sponsordesk_core.taxonomy import CallType, Channel, Priority, RootCause, Team

Money = Annotated[Decimal, Field(max_digits=14, decimal_places=2)]
Percent = Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)]


class Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- Sponsors and plans --------------------------------------------------------


class PayrollFrequency(StrEnum):
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    SEMIMONTHLY = "semimonthly"
    MONTHLY = "monthly"


class Contact(Record):
    name: str
    title: str
    email: str
    phone: str


class Sponsor(Record):
    sponsor_id: str
    legal_name: str
    ein: str
    industry: str
    state: str
    employee_count: int
    payroll_frequency: PayrollFrequency
    payroll_provider: str
    service_tier: str
    plan_id: str
    onboarded_on: date
    primary_contact: Contact
    secondary_contacts: list[Contact]
    relationship_manager: str


class PlanType(StrEnum):
    TRADITIONAL_401K = "401k"
    SAFE_HARBOR_401K = "safe_harbor_401k"
    PLAN_403B = "403b"


class EntryFrequency(StrEnum):
    IMMEDIATE = "immediate"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMIANNUAL = "semiannual"


class Eligibility(Record):
    min_age: int
    service_months: int
    entry: EntryFrequency


class AutoEnrollment(Record):
    enabled: bool
    default_rate_pct: Percent
    auto_escalate: bool
    escalate_step_pct: Percent
    escalate_cap_pct: Percent


class MatchTier(Record):
    """Employer matches ``match_rate_pct`` of each dollar deferred within the band."""

    match_rate_pct: Percent
    band_from_pct: Percent
    band_to_pct: Percent


class LoanRules(Record):
    allowed: bool
    max_outstanding: int
    min_amount: Money
    max_amount: Money
    max_pct_of_vested: Percent
    origination_fee: Money
    general_purpose_max_years: int
    residential_max_years: int


class TestingStatus(StrEnum):
    PASSED = "passed"
    PENDING = "pending"
    FAILED_ADP = "failed_adp"
    FAILED_ACP = "failed_acp"
    EXEMPT_SAFE_HARBOR = "exempt_safe_harbor"


class PlanRecord(Record):
    plan_id: str
    sponsor_id: str
    plan_name: str
    plan_type: PlanType
    plan_number: str
    effective_date: date
    plan_year_end: str
    eligibility: Eligibility
    auto_enrollment: AutoEnrollment
    roth_allowed: bool
    employer_match: list[MatchTier]
    match_true_up: bool
    vesting_schedule: str
    loans: LoanRules
    hardship_withdrawals_allowed: bool
    in_service_age: int | None
    testing_status: TestingStatus
    form_5500_due: date
    annual_deferral_limit: Money
    catch_up_limit: Money


# --- Participants and loans ----------------------------------------------------


class EmploymentStatus(StrEnum):
    ACTIVE = "active"
    TERMINATED = "terminated"
    LEAVE = "leave"


class Participant(Record):
    participant_id: str
    sponsor_id: str
    employee_id: str
    ssn: str
    first_name: str
    last_name: str
    dob: date
    email: str
    hire_date: date
    termination_date: date | None
    status: EmploymentStatus
    annual_salary: Money
    enrolled: bool
    enrollment_date: date | None
    pretax_rate_pct: Percent
    roth_rate_pct: Percent
    pending_rate_change_pct: Percent | None
    account_number: str
    balance: Money
    vested_balance: Money


class LoanStatus(StrEnum):
    ACTIVE = "active"
    PAID_OFF = "paid_off"
    DEFAULTED = "defaulted"


class Loan(Record):
    loan_id: str
    participant_id: str
    sponsor_id: str
    purpose: str
    principal: Money
    outstanding_balance: Money
    payment_per_period: Money
    issued_on: date
    maturity_date: date
    status: LoanStatus


# --- Payroll ---------------------------------------------------------------------


class PayrollFileStatus(StrEnum):
    ACCEPTED = "accepted"
    ACCEPTED_WITH_WARNINGS = "accepted_with_warnings"
    REJECTED = "rejected"
    PENDING_REVIEW = "pending_review"


class FundingStatus(StrEnum):
    RECEIVED = "received"
    PENDING = "pending"
    NOT_RECEIVED = "not_received"
    NOT_APPLICABLE = "not_applicable"


class PayrollErrorKind(StrEnum):
    MISSING_SSN = "missing_ssn"
    INVALID_SSN_FORMAT = "invalid_ssn_format"
    DUPLICATE_ROW = "duplicate_row"
    DEFERRAL_EXCEEDS_GROSS = "deferral_exceeds_gross"
    NEGATIVE_AMOUNT = "negative_amount"
    DEFERRAL_RATE_MISMATCH = "deferral_rate_mismatch"
    MATCH_MISCALCULATED = "match_miscalculated"
    MISSING_LOAN_REPAYMENT = "missing_loan_repayment"
    TERMINATED_WITH_CONTRIBUTION = "terminated_with_contribution"
    OVER_ANNUAL_LIMIT = "over_annual_limit"
    UNKNOWN_EMPLOYEE = "unknown_employee"
    PAY_DATE_MISMATCH = "pay_date_mismatch"
    HEADER_MISMATCH = "header_mismatch"


class Severity(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"


ERROR_SEVERITY: dict[PayrollErrorKind, Severity] = {
    PayrollErrorKind.MISSING_SSN: Severity.CRITICAL,
    PayrollErrorKind.INVALID_SSN_FORMAT: Severity.CRITICAL,
    PayrollErrorKind.DUPLICATE_ROW: Severity.CRITICAL,
    PayrollErrorKind.DEFERRAL_EXCEEDS_GROSS: Severity.CRITICAL,
    PayrollErrorKind.NEGATIVE_AMOUNT: Severity.CRITICAL,
    PayrollErrorKind.HEADER_MISMATCH: Severity.CRITICAL,
    PayrollErrorKind.UNKNOWN_EMPLOYEE: Severity.CRITICAL,
    PayrollErrorKind.PAY_DATE_MISMATCH: Severity.CRITICAL,
    PayrollErrorKind.DEFERRAL_RATE_MISMATCH: Severity.WARNING,
    PayrollErrorKind.MATCH_MISCALCULATED: Severity.WARNING,
    PayrollErrorKind.MISSING_LOAN_REPAYMENT: Severity.WARNING,
    PayrollErrorKind.TERMINATED_WITH_CONTRIBUTION: Severity.WARNING,
    PayrollErrorKind.OVER_ANNUAL_LIMIT: Severity.WARNING,
}


class PayrollFile(Record):
    file_id: str
    sponsor_id: str
    filename: str
    pay_date: date
    period_start: date
    period_end: date
    received_at: datetime
    status: PayrollFileStatus
    funding_status: FundingStatus
    row_count: int
    total_pretax: Money
    total_roth: Money
    total_match: Money
    total_loan_repayment: Money
    validation_messages: list[str]


class ExpectedPayroll(Record):
    """A pay date the sponsor's schedule says should exist, with or without a file."""

    sponsor_id: str
    pay_date: date
    file_id: str | None


# --- Tickets ---------------------------------------------------------------------


class TicketStatus(StrEnum):
    NEW = "new"
    TRIAGED = "triaged"
    PENDING_APPROVAL = "pending_approval"
    ROUTED = "routed"
    WAITING_ON_SPONSOR = "waiting_on_sponsor"
    RESOLVED = "resolved"
    CLOSED = "closed"


class Requester(Record):
    name: str
    email: str
    title: str


class Ticket(Record):
    ticket_id: str
    sponsor_id: str
    created_at: datetime
    channel: Channel
    priority: Priority
    status: TicketStatus
    subject: str
    body: str
    requester: Requester


class PiiKind(StrEnum):
    SSN = "ssn"
    SSN_LAST4 = "ssn_last4"
    ACCOUNT_NUMBER = "account_number"
    DOB = "dob"
    BALANCE = "balance"
    PERSON_NAME = "person_name"
    EMAIL = "email"
    PHONE = "phone"
    EIN = "ein"


class PiiSpan(Record):
    kind: PiiKind
    value: str


class Difficulty(StrEnum):
    STANDARD = "standard"
    AMBIGUOUS = "ambiguous"
    MULTI_ISSUE = "multi_issue"


class TicketLabel(Record):
    """Ground truth for a ticket. Never served by the API; used only by evals."""

    ticket_id: str
    call_type: CallType
    secondary_call_type: CallType | None
    team: Team
    root_cause: RootCause | None
    escalation: bool
    advice_request: bool
    difficulty: Difficulty
    related_file_ids: list[str]
    related_participant_ids: list[str]
    pii: list[PiiSpan]


class InjectedError(Record):
    """Ground truth for one defect deliberately placed in a payroll file."""

    file_id: str
    kind: PayrollErrorKind
    severity: Severity
    row_number: int | None
    employee_id: str | None
    detail: str
