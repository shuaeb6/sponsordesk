"""Triage pipeline: redact, classify, gather context, route, and draft.

The context and drafting steps here are deterministic stand-ins for the Claude
agent. They read the same records the agent's MCP tools expose, so the review
workflow, guard, and audit trail are identical when the agent is switched on.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any

from piiguard import Redactor
from sponsordesk_core.models import (
    FundingStatus,
    PayrollFile,
    PayrollFileStatus,
    PlanRecord,
    Sponsor,
    Ticket,
)
from sponsordesk_core.taxonomy import CallType, RootCause, Team, route
from sponsordesk_synth import Dataset

from sponsordesk_console.classifier import CallTypeClassifier, EvalReport

ESCALATION_RE = re.compile(
    r"(?i)\b(attorney|lawyer|unacceptable|ceo|reviewing our relationship|third time|regulator)\b"
)
ADVICE_RE = re.compile(
    r"(?i)(what would you recommend|your advice|should they roll|which funds|smarter for them)"
)

TEAM_LABELS = {
    Team.PAYROLL_INTEGRATIONS: "Payroll Integrations",
    Team.CONTRIBUTION_OPERATIONS: "Contribution Operations",
    Team.ENROLLMENT_SERVICES: "Enrollment Services",
    Team.LOAN_SERVICING: "Loan Servicing",
    Team.DISTRIBUTIONS: "Distributions",
    Team.COMPLIANCE_TESTING: "Compliance & Testing",
    Team.SPONSOR_SUPPORT: "Sponsor Support",
    Team.RELATIONSHIP_MANAGEMENT: "Relationship Management",
}

# Close guard: every item must be confirmed by a human before a ticket may close.
RESOLUTION_CRITERIA: dict[CallType, list[str]] = {
    CallType.PAYROLL_FILE_ERROR: [
        "Corrected file received and accepted",
        "Sponsor confirmed contributions posted",
    ],
    CallType.MISSED_CONTRIBUTION: [
        "Missing contributions located or funded",
        "Contributions posted to participant accounts",
    ],
    CallType.ENROLLMENT_ELIGIBILITY: [
        "Eligibility confirmed against plan rules",
        "Enrollment status updated or explained",
    ],
    CallType.DEFERRAL_CHANGE: [
        "Election on file matches requested rate",
        "Payroll deduction confirmed on next file",
    ],
    CallType.LOAN_SERVICING: [
        "Loan status confirmed with Loan Servicing",
        "Repayment schedule current or explained",
    ],
    CallType.DISTRIBUTION_REQUEST: [
        "Distribution paperwork sent to participant",
        "No investment advice given",
    ],
    CallType.COMPLIANCE_TESTING: ["Testing question answered by Compliance"],
    CallType.PORTAL_ACCESS: ["Identity verified out of band", "Access change completed"],
}


@dataclass
class Finding:
    label: str
    detail: str
    tone: str = "info"  # info | warn | critical


@dataclass
class Proposal:
    ticket_id: str
    call_type: str
    confidence: float
    alternatives: list[tuple[str, float]]
    root_cause: str | None
    team: str
    team_label: str
    reason: str
    escalation: bool
    advice_request: bool
    findings: list[Finding]
    redacted_subject: str
    redacted_body: str
    redaction_counts: dict[str, int]
    draft_reply: str
    resolution_criteria: list[str]
    model_input_preview: str = field(default="")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class Engine:
    def __init__(self, data: Dataset) -> None:
        self.data = data
        self.sponsors = {s.sponsor_id: s for s in data.sponsors}
        self.plans = {p.sponsor_id: p for p in data.plans}
        self.tickets = {t.ticket_id: t for t in data.tickets}
        self.labels = {lb.ticket_id: lb for lb in data.labels}
        self.files_by_sponsor: dict[str, list[PayrollFile]] = {}
        for f in data.payroll_files:
            self.files_by_sponsor.setdefault(f.sponsor_id, []).append(f)
        names = {f"{p.first_name} {p.last_name}" for p in data.participants}
        for s in data.sponsors:
            names.update(c.name for c in [s.primary_contact, *s.secondary_contacts])
        self.redactor = Redactor(known_names=names)
        self.classifier = CallTypeClassifier()
        ids = [t.ticket_id for t in data.tickets]
        texts = [self._model_text(t) for t in data.tickets]
        labels = [self.labels[i].call_type.value for i in ids]
        self.report: EvalReport = self.classifier.fit_and_evaluate(ids, texts, labels)

    # --- redaction -----------------------------------------------------------------

    def redact_ticket(self, t: Ticket, destination: str = "llm") -> tuple[str, str, dict[str, int]]:
        vault: dict[str, str] = {}
        subject = self.redactor.redact(t.subject, destination, vault)
        body = self.redactor.redact(t.body, destination, vault)
        counts: dict[str, int] = {}
        for r in (subject, body):
            for k, v in r.counts.items():
                counts[k] = counts.get(k, 0) + v
        return subject.text, body.text, counts

    def _model_text(self, t: Ticket) -> str:
        subject, body, _ = self.redact_ticket(t)
        return f"{subject}\n{body}"

    # --- context ---------------------------------------------------------------------

    def _recent_files(self, sponsor_id: str, t: Ticket) -> list[PayrollFile]:
        files = [
            f
            for f in self.files_by_sponsor.get(sponsor_id, [])
            if f.received_at <= t.created_at and f.received_at >= t.created_at - timedelta(days=75)
        ]
        return sorted(files, key=lambda f: f.pay_date, reverse=True)

    def _infer_cause(
        self,
        call_type: CallType,
        t: Ticket,
        plan: PlanRecord,
        files: list[PayrollFile],
        findings: list[Finding],
    ) -> RootCause | None:
        text = f"{t.subject} {t.body}".lower()
        rejected = [f for f in files if f.status is PayrollFileStatus.REJECTED]
        warned = [f for f in files if f.status is PayrollFileStatus.ACCEPTED_WITH_WARNINGS]
        unfunded = [f for f in files if f.funding_status is FundingStatus.NOT_RECEIVED]
        missing = [
            e
            for e in self.data.expected_payroll
            if e.sponsor_id == t.sponsor_id
            and e.file_id is None
            and e.pay_date <= t.created_at.date()
        ]

        for f in rejected[:2]:
            findings.append(
                Finding(f"{f.filename} rejected", "; ".join(f.validation_messages[:3]), "critical")
            )
        for f in warned[:2]:
            findings.append(
                Finding(
                    f"{f.filename} accepted with warnings",
                    "; ".join(f.validation_messages[:3]),
                    "warn",
                )
            )
        for f in unfunded[:1]:
            findings.append(
                Finding(
                    "Funding not received",
                    f"{f.filename} accepted; ACH funding not received",
                    "warn",
                )
            )
        for e in missing[:1]:
            findings.append(
                Finding(
                    "Payroll file not received",
                    f"No file on record for pay date {e.pay_date:%m/%d/%Y}",
                    "critical",
                )
            )

        if call_type is CallType.PAYROLL_FILE_ERROR:
            if rejected:
                return RootCause.PAYROLL_FILE_REJECTED
            return RootCause.PAYROLL_FILE_WARNINGS if warned else None
        if call_type is CallType.MISSED_CONTRIBUTION:
            if "match" in text and plan.match_true_up:
                findings.append(
                    Finding(
                        "Plan has annual match true-up",
                        "Match shortfall is restored after plan year end",
                    )
                )
                return RootCause.MATCH_TRUE_UP_TIMING
            if unfunded:
                return RootCause.FUNDING_NOT_RECEIVED
            if missing:
                return RootCause.PAYROLL_FILE_NOT_RECEIVED
            if rejected:
                return RootCause.PAYROLL_FILE_REJECTED
            return RootCause.FUNDING_NOT_RECEIVED
        if call_type is CallType.DEFERRAL_CHANGE:
            mismatch = [f for f in warned if any("elections" in m for m in f.validation_messages)]
            if mismatch:
                return RootCause.DEFERRAL_NOT_UPDATED_IN_PAYROLL
            return RootCause.ELECTION_PROCESSING
        if call_type is CallType.LOAN_SERVICING:
            if any(any("loan repayments" in m for m in f.validation_messages) for f in warned):
                return RootCause.LOAN_REPAYMENT_MISSING_IN_PAYROLL
            if re.search(r"won't let|blocked|second loan", text):
                findings.append(
                    Finding(
                        "Loan limit",
                        f"Plan allows {plan.loans.max_outstanding} outstanding loan(s)",
                    )
                )
                return RootCause.LOAN_LIMIT_REACHED
            return RootCause.LOAN_GENERAL_INQUIRY
        if call_type is CallType.ENROLLMENT_ELIGIBILITY:
            elig = plan.eligibility
            findings.append(
                Finding(
                    "Eligibility rules",
                    f"Age {elig.min_age}, {elig.service_months} months service, "
                    f"{elig.entry.value} entry; auto-enroll "
                    f"{'on' if plan.auto_enrollment.enabled else 'off'}",
                )
            )
            if plan.auto_enrollment.enabled and "auto" in text:
                return RootCause.AUTO_ENROLL_NOT_APPLIED
            return RootCause.NOT_YET_ELIGIBLE
        return {
            CallType.DISTRIBUTION_REQUEST: RootCause.DISTRIBUTION_PROCESSING,
            CallType.COMPLIANCE_TESTING: RootCause.TESTING_INQUIRY,
            CallType.PORTAL_ACCESS: RootCause.ACCESS_ISSUE,
        }.get(call_type)

    # --- pipeline --------------------------------------------------------------------

    def triage(self, ticket_id: str) -> Proposal:
        t = self.tickets[ticket_id]
        sponsor: Sponsor = self.sponsors[t.sponsor_id]
        plan = self.plans[t.sponsor_id]
        subject, body, counts = self.redact_ticket(t)
        prediction = self.classifier.predict(f"{subject}\n{body}")
        call_type = CallType(prediction.label)

        findings: list[Finding] = [
            Finding(
                "Plan",
                f"{plan.plan_name} · {plan.plan_type.value} · "
                f"{sponsor.payroll_frequency.value} payroll via "
                f"{sponsor.payroll_provider}",
            ),
        ]
        files = self._recent_files(t.sponsor_id, t)
        cause = self._infer_cause(call_type, t, plan, files, findings)
        escalation = bool(ESCALATION_RE.search(t.body))
        advice = bool(ADVICE_RE.search(t.body))
        if escalation:
            findings.append(
                Finding(
                    "Escalation language",
                    "Sponsor signals dissatisfaction or legal involvement",
                    "critical",
                )
            )
        if advice:
            findings.append(
                Finding(
                    "Advice request",
                    "Ticket asks for investment or distribution advice; reply must not provide it",
                    "warn",
                )
            )
        team = route(call_type, cause, escalation=escalation)
        reason = _reason(call_type, cause, team, escalation)
        draft = _draft(call_type, cause, files, plan, escalation, advice)
        return Proposal(
            ticket_id=ticket_id,
            call_type=call_type.value,
            confidence=round(prediction.confidence, 3),
            alternatives=[(k, round(v, 3)) for k, v in prediction.ranked[1:]],
            root_cause=cause.value if cause else None,
            team=team.value,
            team_label=TEAM_LABELS[team],
            reason=reason,
            escalation=escalation,
            advice_request=advice,
            findings=findings,
            redacted_subject=subject,
            redacted_body=body,
            redaction_counts=counts,
            draft_reply=draft,
            resolution_criteria=RESOLUTION_CRITERIA[call_type],
            model_input_preview=f"{subject}\n{body}",
        )


def _reason(call_type: CallType, cause: RootCause | None, team: Team, escalation: bool) -> str:
    if escalation:
        return "Escalation language detected; relationship management owns the response."
    if cause is not None and team.value != _default(call_type):
        return (
            f"Classified as {call_type.value.replace('_', ' ')}, but records show "
            f"{cause.value.replace('_', ' ')}, which {TEAM_LABELS[team]} resolves."
        )
    readable = cause.value.replace("_", " ") if cause else "no specific cause found"
    return f"{call_type.value.replace('_', ' ').capitalize()}: {readable}."


def _default(call_type: CallType) -> str:
    from sponsordesk_core.taxonomy import DEFAULT_TEAM

    return DEFAULT_TEAM[call_type].value


def _draft(
    call_type: CallType,
    cause: RootCause | None,
    files: list[PayrollFile],
    plan: PlanRecord,
    escalation: bool,
    advice: bool,
) -> str:
    rejected = next((f for f in files if f.status is PayrollFileStatus.REJECTED), None)
    lines = ["Hello,", "", "Thank you for reaching out."]
    if escalation:
        lines.append(
            "I'm sorry for the frustration this has caused. A senior member of our "
            "team is now handling this personally and will contact you today."
        )
    body = {
        RootCause.PAYROLL_FILE_REJECTED: (
            f"We reviewed {rejected.filename if rejected else 'your recent file'}. It was not "
            "processed because some rows did not pass validation"
            + (f": {'; '.join(rejected.validation_messages[:3])}." if rejected else ".")
            + " Once those rows are corrected, please upload the file again and we will "
            "process it the same day."
        ),
        RootCause.PAYROLL_FILE_WARNINGS: (
            "Your file was processed. The warnings mean a few amounts look different from what "
            "we expected. Our payroll team is checking them and will tell you if anything "
            "needs to change."
        ),
        RootCause.PAYROLL_FILE_NOT_RECEIVED: (
            "We don't have a contribution file on record for that pay date. Please upload it "
            "when you can. Contributions will post once it is processed."
        ),
        RootCause.FUNDING_NOT_RECEIVED: (
            "Your file was accepted, but we have not yet received the money for it. Please "
            "check with your bank or payroll provider that the payment was sent. Contributions "
            "will be invested as soon as it arrives."
        ),
        RootCause.MATCH_TRUE_UP_TIMING: (
            "Your plan includes an annual match true-up. Employees who contribute early in "
            "the year receive any missing match after the plan year ends."
        ),
        RootCause.NOT_YET_ELIGIBLE: (
            f"Under your plan's rules, employees can join after {plan.eligibility.service_months} "
            f"month(s) of service, on the next {plan.eligibility.entry.value} entry date. "
            "We'll confirm the exact date for this employee."
        ),
        RootCause.AUTO_ENROLL_NOT_APPLIED: (
            "This employee should have been enrolled automatically. Our enrollment team is "
            "reviewing the record and will confirm when deductions should start."
        ),
        RootCause.DEFERRAL_NOT_UPDATED_IN_PAYROLL: (
            "We have the new contribution rate on file, but the last payroll file still used the "
            "previous rate. Please update the rate in your payroll system before the next run."
        ),
        RootCause.ELECTION_PROCESSING: (
            "Employees can change their contribution rate on the participant website. Changes "
            "usually apply to the next payroll after they are submitted."
        ),
        RootCause.LOAN_REPAYMENT_MISSING_IN_PAYROLL: (
            "The last payroll file did not include this employee's loan repayment. Please add "
            "it to the next payroll so the loan stays current."
        ),
        RootCause.LOAN_LIMIT_REACHED: (
            f"Your plan allows {plan.loans.max_outstanding} outstanding loan(s) at a time. The "
            "employee can apply again once an existing loan is paid off."
        ),
        RootCause.LOAN_GENERAL_INQUIRY: (
            "Our loan team will send the details for this request, including the current "
            "payoff amount where relevant."
        ),
        RootCause.DISTRIBUTION_PROCESSING: (
            "The employee can request this through the participant website, or we can mail "
            "the forms. Our distributions team will follow up with the next steps."
        ),
        RootCause.TESTING_INQUIRY: (
            "Our compliance team will reply with the details and any deadlines that apply."
        ),
        RootCause.ACCESS_ISSUE: (
            "For security, we will verify this request by phone with an authorized plan "
            "contact before making any access change. We will never ask for a password."
        ),
    }
    lines.append(
        body.get(cause, "Our team is reviewing your request.")
        if cause
        else "Our team is reviewing your request."
    )
    if advice:
        lines.append(
            "We aren't able to give investment or tax advice. The employee may want "
            "to talk with a financial professional before deciding."
        )
    lines += ["", "Best regards,", "Plan Services Team"]
    return "\n".join(lines)
