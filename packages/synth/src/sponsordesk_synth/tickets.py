"""Support tickets written the way plan sponsors actually write them.

Every ticket is generated from a scenario that is grounded in the generated
records: if a ticket says a file was rejected, that file exists and carries the
defect. Every PII value placed into ticket text is recorded as a ``PiiSpan`` so
redaction can be measured against exact ground truth.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sponsordesk_core.models import (
    Difficulty,
    EmploymentStatus,
    FundingStatus,
    Loan,
    LoanStatus,
    Participant,
    PayrollErrorKind,
    PiiKind,
    PiiSpan,
    PlanRecord,
    Requester,
    Severity,
    Sponsor,
    Ticket,
    TicketLabel,
    TicketStatus,
)
from sponsordesk_core.taxonomy import CallType, Channel, Priority, RootCause, route

from sponsordesk_synth.payroll import DraftFile, ErrorInjector, PayrollBook

TICKET_MIX: dict[CallType, int] = {
    CallType.PAYROLL_FILE_ERROR: 90,
    CallType.MISSED_CONTRIBUTION: 80,
    CallType.ENROLLMENT_ELIGIBILITY: 70,
    CallType.DEFERRAL_CHANGE: 55,
    CallType.LOAN_SERVICING: 60,
    CallType.DISTRIBUTION_REQUEST: 60,
    CallType.COMPLIANCE_TESTING: 40,
    CallType.PORTAL_ACCESS: 45,
}

GREETINGS = ("Hi team,", "Hello,", "Good morning,", "Hi there,", "Hi all,", "Hello support,", "")
SIGNOFFS = ("Thanks,", "Thank you,", "Best,", "Regards,", "Appreciate the help,", "Thanks in advance,")


@dataclass
class Draft:
    """A ticket under construction, accumulating text and ground truth together."""

    sponsor: Sponsor
    call_type: CallType
    root_cause: RootCause | None
    subject: str = ""
    lines: list[str] = field(default_factory=list)
    pii: list[PiiSpan] = field(default_factory=list)
    file_ids: list[str] = field(default_factory=list)
    participant_ids: list[str] = field(default_factory=list)
    difficulty: Difficulty = Difficulty.STANDARD
    advice_request: bool = False
    not_before: datetime | None = None

    def note(self, kind: PiiKind, value: str) -> str:
        self.pii.append(PiiSpan(kind=kind, value=value))
        return value


@dataclass
class World:
    sponsors: list[Sponsor]
    plans: dict[str, PlanRecord]
    participants: dict[str, list[Participant]]
    loans: dict[str, list[Loan]]
    book: PayrollBook
    injector: ErrorInjector
    as_of: date


class TicketWriter:
    def __init__(self, rng: random.Random, world: World) -> None:
        self.rng = rng
        self.w = world
        self.scenarios: dict[CallType, Callable[[], Draft | None]] = {
            CallType.PAYROLL_FILE_ERROR: self._payroll_file_error,
            CallType.MISSED_CONTRIBUTION: self._missed_contribution,
            CallType.ENROLLMENT_ELIGIBILITY: self._enrollment,
            CallType.DEFERRAL_CHANGE: self._deferral_change,
            CallType.LOAN_SERVICING: self._loan,
            CallType.DISTRIBUTION_REQUEST: self._distribution,
            CallType.COMPLIANCE_TESTING: self._compliance,
            CallType.PORTAL_ACCESS: self._portal,
        }

    # --- helpers -------------------------------------------------------------------

    def _sponsor(self) -> Sponsor:
        return self.rng.choice(self.w.sponsors)

    def _person_ref(self, d: Draft, p: Participant) -> str:
        d.participant_ids.append(p.participant_id)
        name = d.note(PiiKind.PERSON_NAME, f"{p.first_name} {p.last_name}")
        style = self.rng.randint(0, 5)
        if style == 0:
            return f"{name} (SSN {d.note(PiiKind.SSN, p.ssn)})"
        if style == 1:
            return f"{name}, SSN ending in {d.note(PiiKind.SSN_LAST4, p.ssn[-4:])}"
        if style == 2:
            return f"{name} (acct {d.note(PiiKind.ACCOUNT_NUMBER, p.account_number)})"
        if style == 3:
            return f"{name}, DOB {d.note(PiiKind.DOB, p.dob.strftime('%m/%d/%Y'))}"
        if style == 4:
            return f"{name} (employee ID {p.employee_id}, SSN {d.note(PiiKind.SSN, p.ssn)})"
        return name

    def _balance(self, d: Draft, p: Participant) -> str:
        return d.note(PiiKind.BALANCE, f"${p.balance:,.2f}")

    def _people(self, sponsor: Sponsor, pred: Callable[[Participant], bool]) -> list[Participant]:
        return [p for p in self.w.participants[sponsor.sponsor_id] if pred(p)]

    def _latest_draft(self, sponsor: Sponsor, pred: Callable[[DraftFile], bool]) -> DraftFile | None:
        files = [f for f in self.w.book.for_sponsor(sponsor.sponsor_id) if pred(f)]
        return max(files, key=lambda f: f.pay_date) if files else None

    def _file_date(self, f: DraftFile) -> str:
        return self.rng.choice((f.pay_date.strftime("%m/%d"), f.pay_date.strftime("%B %-d"),
                                f.pay_date.strftime("%-m/%-d/%Y")))

    # --- scenarios -----------------------------------------------------------------

    def _payroll_file_error(self) -> Draft | None:
        s = self._sponsor()
        rejected = [f for f in self.w.book.for_sponsor(s.sponsor_id)
                    if f.severity() is Severity.CRITICAL]
        warned = [f for f in self.w.book.for_sponsor(s.sponsor_id)
                  if f.severity() is Severity.WARNING and not f.pending_review]
        use_rejected = rejected and (not warned or self.rng.random() < 0.7)
        pool = rejected if use_rejected else warned
        if not pool:
            return None
        f = self.rng.choice(pool)
        cause = RootCause.PAYROLL_FILE_REJECTED if use_rejected else RootCause.PAYROLL_FILE_WARNINGS
        d = Draft(s, CallType.PAYROLL_FILE_ERROR, cause, file_ids=[f.file_id])
        d.not_before = f.received_at
        when = self._file_date(f)
        if use_rejected:
            d.subject = self.rng.choice((
                f"Payroll file rejected - {when}", f"Contribution file error for {when} payroll",
                "Upload failed again", f"Why was our {when} file kicked back?",
                f"{f.filename} rejected",
            ))
            body = self.rng.choice((
                f"We uploaded our contribution file for the {when} pay date and got an error "
                "saying it was rejected. Our payroll vendor says the file looks fine on their "
                "end. Can you tell us exactly which rows are the problem?",
                f"The file for {when} was rejected in the portal. We need to get these "
                "contributions in on time. What do we need to fix?",
                f"Our {s.payroll_provider} export for {when} won't go through. The portal just "
                "says validation failed. Please advise on what failed so we can resubmit.",
            ))
            err = next((e for e in f.errors if e.severity is Severity.CRITICAL and e.employee_id),
                       None)
            if err and self.rng.random() < 0.55:
                p = next((x for x in self.w.participants[s.sponsor_id]
                          if x.employee_id == err.employee_id), None)
                if p:
                    body += f" I think it might be the row for {self._person_ref(d, p)}."
        else:
            d.subject = self.rng.choice((
                f"Warnings on {when} payroll file", "File accepted with warnings - what does it mean?",
                f"Question about file warnings ({when})",
            ))
            body = self.rng.choice((
                f"Our {when} file went through but shows warnings. Do we need to do anything or "
                "is it okay as is?",
                f"The confirmation for {when} says 'accepted with warnings'. Our controller wants "
                "to know if any contributions are wrong.",
            ))
        d.lines.append(body)
        return d

    def _missed_contribution(self) -> Draft | None:
        s = self._sponsor()
        plan = self.w.plans[s.sponsor_id]
        options: list[RootCause] = [RootCause.FUNDING_NOT_RECEIVED]
        if any(f.severity() is Severity.CRITICAL for f in self.w.book.for_sponsor(s.sponsor_id)):
            options.append(RootCause.PAYROLL_FILE_REJECTED)
        if self.w.book.missing_for(s.sponsor_id):
            options.append(RootCause.PAYROLL_FILE_NOT_RECEIVED)
        if plan.match_true_up:
            options.append(RootCause.MATCH_TRUE_UP_TIMING)
        cause = self.rng.choice(options)
        d = Draft(s, CallType.MISSED_CONTRIBUTION, cause)
        enrolled = self._people(s, lambda p: p.enrolled and p.status is EmploymentStatus.ACTIVE)
        if not enrolled:
            return None
        p = self.rng.choice(enrolled)
        ref = self._person_ref(d, p)

        if cause is RootCause.PAYROLL_FILE_NOT_RECEIVED:
            missing = self.rng.choice(self.w.book.missing_for(s.sponsor_id))
            when = missing.pay_date.strftime("%m/%d")
            d.subject = self.rng.choice((f"Contributions missing for {when}",
                                         "Employees asking where their money is",
                                         f"{when} deferrals not showing"))
            d.lines.append(self.rng.choice((
                f"Several employees, including {ref}, say their {when} contributions never "
                "showed up in their accounts. Payroll definitely ran that day.",
                f"{ref} called HR because the {when} deferral is not in the account. Can you "
                "check what happened?",
            )))
            d.not_before = datetime.combine(missing.pay_date + timedelta(days=3), time(9))
        elif cause is RootCause.PAYROLL_FILE_REJECTED:
            f = self.rng.choice([x for x in self.w.book.for_sponsor(s.sponsor_id)
                                 if x.severity() is Severity.CRITICAL])
            d.file_ids.append(f.file_id)
            when = self._file_date(f)
            d.subject = self.rng.choice((f"Missing contributions - {when} payroll",
                                         "Deferrals not posted", "Money not in accounts"))
            d.lines.append(self.rng.choice((
                f"The {when} contributions haven't posted for anyone. {ref} noticed first. "
                "We sent the file like always.",
                f"Our employees can't see their {when} contributions. For example {ref} has "
                f"nothing for that pay date. Current balance shows {self._balance(d, p)}.",
            )))
            d.not_before = f.received_at
        elif cause is RootCause.FUNDING_NOT_RECEIVED:
            f = self._latest_draft(s, lambda x: x.severity() is not Severity.CRITICAL
                                   and not x.pending_review)
            if f is None:
                return None
            f.funding_override = FundingStatus.NOT_RECEIVED
            d.file_ids.append(f.file_id)
            when = self._file_date(f)
            d.subject = self.rng.choice((f"{when} contributions still pending",
                                         "File accepted but money not invested",
                                         "Contributions stuck?"))
            d.lines.append(self.rng.choice((
                f"Our {when} file was accepted but the contributions still aren't in employee "
                f"accounts. {ref} is asking. What's holding it up?",
                f"File for {when} shows accepted on our side, but participants like {ref} "
                "don't see the deposit yet.",
            )))
            d.not_before = f.received_at
        else:
            d.subject = self.rng.choice(("Employer match missing", "Match looks short this year",
                                         "Question about match true-up"))
            d.lines.append(self.rng.choice((
                f"{ref} says the employer match in the account is lower than expected for this "
                "year. They front-loaded their contributions early in the year.",
                f"An employee ({ref}) maxed out early and the match stopped. Is the match going "
                "to be made up?",
            )))
        return d

    def _enrollment(self) -> Draft | None:
        s = self._sponsor()
        plan = self.w.plans[s.sponsor_id]
        recent = self._people(s, lambda p: (self.w.as_of - p.hire_date).days < 130
                              and not p.enrolled)
        if not recent:
            return None
        p = self.rng.choice(recent)
        months_employed = (self.w.as_of - p.hire_date).days / 30.4
        not_eligible = months_employed < plan.eligibility.service_months or \
            plan.eligibility.entry.value != "immediate"
        if plan.auto_enrollment.enabled and not not_eligible:
            cause = RootCause.AUTO_ENROLL_NOT_APPLIED
        else:
            cause = RootCause.NOT_YET_ELIGIBLE
        d = Draft(s, CallType.ENROLLMENT_ELIGIBILITY, cause)
        ref = self._person_ref(d, p)
        hired = p.hire_date.strftime("%m/%d")
        if cause is RootCause.AUTO_ENROLL_NOT_APPLIED:
            d.subject = self.rng.choice(("New hire not auto-enrolled", "Auto enrollment didn't happen",
                                         "Enrollment question for new employee"))
            d.lines.append(self.rng.choice((
                f"We hired {ref} on {hired} and expected them to be auto-enrolled by now, but "
                "nothing is coming out of their paycheck. Can you look into it?",
                f"{ref} started {hired}. Our plan has auto enrollment, but they're not showing "
                "as enrolled. Did we miss a step?",
            )))
        else:
            d.subject = self.rng.choice(("When is new employee eligible?", "Eligibility question",
                                         "Employee wants to enroll"))
            d.lines.append(self.rng.choice((
                f"{ref} was hired {hired} and wants to start contributing. They can't enroll on "
                "the website. When are they eligible?",
                f"New team member {ref} (start date {hired}) says the enrollment site won't let "
                "them in. Is something wrong with their record?",
            )))
            if self.rng.random() < 0.3:
                d.difficulty = Difficulty.AMBIGUOUS
        return d

    def _deferral_change(self) -> Draft | None:
        s = self._sponsor()
        pending = self._people(s, lambda p: p.pending_rate_change_pct is not None)
        if pending and self.rng.random() < 0.65:
            p = self.rng.choice(pending)
            f = self._latest_draft(s, lambda x: x.row_for(p.employee_id) is not None
                                   and x.severity() is not Severity.CRITICAL
                                   and not x.pending_review)
            if f is None:
                return None
            if not any(e.employee_id == p.employee_id
                       and e.kind is PayrollErrorKind.DEFERRAL_RATE_MISMATCH for e in f.errors):
                if self.w.injector.inject(f, PayrollErrorKind.DEFERRAL_RATE_MISMATCH,
                                          p.employee_id) is None:
                    return None
            d = Draft(s, CallType.DEFERRAL_CHANGE, RootCause.DEFERRAL_NOT_UPDATED_IN_PAYROLL,
                      file_ids=[f.file_id])
            d.not_before = f.received_at
            ref = self._person_ref(d, p)
            new = p.pending_rate_change_pct
            d.subject = self.rng.choice(("Deferral change not reflected", "Rate change didn't take",
                                         f"Employee changed to {new}% - still old amount"))
            d.lines.append(self.rng.choice((
                f"{ref} changed their contribution to {new}% online a few weeks ago, but the last "
                "paycheck still took the old amount. Who needs to fix this?",
                f"Something looks off with {ref}'s contributions. They say they went to {new}% "
                "but the deduction didn't change.",
            )))
            if "looks off" in d.lines[-1]:
                d.difficulty = Difficulty.AMBIGUOUS
            return d
        people = self._people(s, lambda p: p.enrolled and p.status is EmploymentStatus.ACTIVE)
        if not people:
            return None
        p = self.rng.choice(people)
        d = Draft(s, CallType.DEFERRAL_CHANGE, RootCause.ELECTION_PROCESSING)
        ref = self._person_ref(d, p)
        rate = self.rng.choice((3, 6, 8, 10, 15))
        d.subject = self.rng.choice(("How do employees change their deferral?",
                                     "Contribution rate change request", "Stop contributions"))
        d.lines.append(self.rng.choice((
            f"{ref} would like to change their contribution to {rate}%. Can we do this for them "
            "or do they need to do it themselves? And which payroll would it start?",
            f"{ref} wants to stop contributing for a few months. What's the process and how fast "
            "does it take effect?",
        )))
        return d

    def _loan(self) -> Draft | None:
        s = self._sponsor()
        plan = self.w.plans[s.sponsor_id]
        if not plan.loans.allowed:
            return None
        active = [ln for ln in self.w.loans[s.sponsor_id] if ln.status is LoanStatus.ACTIVE]
        by_id = {p.participant_id: p for p in self.w.participants[s.sponsor_id]}
        roll = self.rng.random()
        if active and roll < 0.4:
            loan = self.rng.choice(active)
            p = by_id[loan.participant_id]
            f = self._latest_draft(s, lambda x: x.row_for(p.employee_id) is not None
                                   and x.severity() is not Severity.CRITICAL
                                   and not x.pending_review)
            if f is None:
                return None
            row = f.row_for(p.employee_id)
            if row and Decimal(row["loan_repayment"]) > 0 and self.w.injector.inject(
                    f, PayrollErrorKind.MISSING_LOAN_REPAYMENT, p.employee_id) is None:
                return None
            d = Draft(s, CallType.LOAN_SERVICING, RootCause.LOAN_REPAYMENT_MISSING_IN_PAYROLL,
                      file_ids=[f.file_id])
            d.not_before = f.received_at
            ref = self._person_ref(d, p)
            d.subject = self.rng.choice(("Loan payment missed", "Loan repayment question",
                                         "Got a past-due loan notice"))
            d.lines.append(self.rng.choice((
                f"{ref} received a notice that a loan payment was missed. We thought payroll was "
                "deducting it every check. Can you confirm what you received?",
                f"Loan repayment for {ref} doesn't look like it came through on the last "
                f"payroll. Their payment should be ${loan.payment_per_period}.",
            )))
            return d
        maxed = [p for p in self.w.participants[s.sponsor_id]
                 if sum(1 for ln in active if ln.participant_id == p.participant_id)
                 >= plan.loans.max_outstanding]
        if maxed and roll < 0.65:
            p = self.rng.choice(maxed)
            d = Draft(s, CallType.LOAN_SERVICING, RootCause.LOAN_LIMIT_REACHED)
            ref = self._person_ref(d, p)
            d.subject = self.rng.choice(("Employee can't request a loan", "Second loan request",
                                         "Loan application blocked"))
            d.lines.append(f"{ref} is trying to take a loan for a home repair but the site won't "
                           f"let them apply. Their balance is {self._balance(d, p)}. Why is it "
                           "blocked?")
            return d
        people = self._people(s, lambda p: p.enrolled)
        if not people:
            return None
        p = self.rng.choice(people)
        d = Draft(s, CallType.LOAN_SERVICING, RootCause.LOAN_GENERAL_INQUIRY)
        ref = self._person_ref(d, p)
        d.subject = self.rng.choice(("Loan payoff amount", "Loan question", "Loan after termination"))
        d.lines.append(self.rng.choice((
            f"{ref} wants to pay off their loan early. How do they get a payoff amount and where "
            "do they send the money?",
            f"{ref} is leaving the company next month and has an outstanding loan. What happens "
            "to the loan when they leave?",
            f"Can you send us the loan rules for our plan? {ref} is asking how much they can borrow.",
        )))
        return d

    def _distribution(self) -> Draft | None:
        s = self._sponsor()
        termed = self._people(s, lambda p: p.status is EmploymentStatus.TERMINATED)
        pool = termed or self._people(s, lambda p: p.enrolled)
        if not pool:
            return None
        p = self.rng.choice(pool)
        d = Draft(s, CallType.DISTRIBUTION_REQUEST, RootCause.DISTRIBUTION_PROCESSING)
        ref = self._person_ref(d, p)
        if p.status is EmploymentStatus.TERMINATED:
            d.subject = self.rng.choice(("Former employee wants their money", "Termination payout",
                                         "Rollover request from ex-employee"))
            body = self.rng.choice((
                f"{ref} left us on {p.termination_date:%m/%d} and wants to take out their "
                f"{self._balance(d, p)} balance. What forms do they need?",
                f"Former employee {ref} is asking how to roll their account to a new employer's plan.",
            ))
        else:
            d.subject = self.rng.choice(("Hardship withdrawal question", "Employee needs money",
                                         "Can employee withdraw?"))
            body = (f"{ref} is facing a medical bill and asked whether they can withdraw from "
                    "their 401(k). What are the options?")
        d.lines.append(body)
        if self.rng.random() < 0.35:
            d.advice_request = True
            d.lines.append(self.rng.choice((
                "Also, should they roll it into an IRA or just cash it out? What would you "
                "recommend?",
                "They also asked which funds they should move into before taking it out.",
                "Honestly, is it smarter for them to take a loan instead? What's your advice?",
            )))
        return d

    def _compliance(self) -> Draft | None:
        s = self._sponsor()
        plan = self.w.plans[s.sponsor_id]
        d = Draft(s, CallType.COMPLIANCE_TESTING, RootCause.TESTING_INQUIRY)
        if plan.testing_status.value.startswith("failed"):
            test = "ADP" if plan.testing_status.value.endswith("adp") else "ACP"
            d.subject = self.rng.choice((f"Failed {test} test - next steps?",
                                         "Nondiscrimination testing results",
                                         "Refunds to highly compensated employees?"))
            d.lines.append(f"We got a notice that our plan failed the {test} test. What are our "
                           "options and what's the deadline for corrective distributions?")
        else:
            d.subject = self.rng.choice(("Form 5500 timing", "Census data request",
                                         "Year-end testing questions"))
            d.lines.append(self.rng.choice((
                "When will the Form 5500 be ready for signature, and what do you need from us?",
                "We received the year-end census request. Which compensation definition should we "
                "use, and is the EIN on file correct? Ours is "
                f"{d.note(PiiKind.EIN, s.ein)}.",
                "Our new CFO wants to understand how the nondiscrimination tests work and when "
                "results will be ready.",
            )))
        return d

    def _portal(self) -> Draft | None:
        s = self._sponsor()
        d = Draft(s, CallType.PORTAL_ACCESS, RootCause.ACCESS_ISSUE)
        contact = self.rng.choice(s.secondary_contacts or [s.primary_contact])
        name = d.note(PiiKind.PERSON_NAME, contact.name)
        email = d.note(PiiKind.EMAIL, contact.email)
        d.subject = self.rng.choice(("Locked out of sponsor portal", "Add new admin user",
                                     "MFA reset needed", "Can't log in"))
        d.lines.append(self.rng.choice((
            f"I'm locked out of the plan sponsor website after too many attempts. My login is "
            f"{email}. Please unlock it, we need to upload payroll today.",
            f"Please add {name} ({email}) as an administrator on our plan. They're taking over "
            "payroll uploads.",
            f"{name} got a new phone and can't get the MFA code anymore. Can you reset it? "
            f"Their email is {email}.",
            f"Please remove access for {name}; they no longer work here.",
        )))
        return d

    # --- assembly ------------------------------------------------------------------

    def _overlays(self, d: Draft) -> tuple[bool, CallType | None]:
        escalation = self.rng.random() < 0.06
        if escalation:
            d.lines.append(self.rng.choice((
                "This is the third time we've raised this. If it isn't fixed this week we will "
                "be reviewing our relationship with your firm.",
                "I've copied our CEO. This is unacceptable and our employees are frustrated.",
                "Our attorney has asked us to document every contact about this issue.",
            )))
        secondary: CallType | None = None
        if not escalation and self.rng.random() < 0.1:
            extras = {
                CallType.PORTAL_ACCESS: "Separately, can you reset portal access for our new "
                                        "payroll clerk?",
                CallType.COMPLIANCE_TESTING: "Unrelated, but when are the testing results due "
                                             "this year?",
                CallType.LOAN_SERVICING: "Also, someone asked me how loans work in our plan.",
                CallType.ENROLLMENT_ELIGIBILITY: "And one more thing: when do new hires become "
                                                 "eligible?",
            }
            secondary = self.rng.choice([k for k in extras if k is not d.call_type])
            d.lines.append(extras[secondary])
            d.difficulty = Difficulty.MULTI_ISSUE
        return escalation, secondary

    def _render(self, d: Draft, channel: Channel) -> tuple[str, Requester]:
        contact = d.sponsor.primary_contact
        requester = Requester(name=contact.name, email=contact.email, title=contact.title)
        if channel is Channel.PHONE_NOTE:
            body = (f"Call note: {d.note(PiiKind.PERSON_NAME, contact.name)} ({contact.title}) "
                    f"called. Caller says: {d.lines[0]}")
            rest = " ".join(d.lines[1:])
            return (body + (" " + rest if rest else "")).strip(), requester
        parts: list[str] = []
        greeting = self.rng.choice(GREETINGS)
        if channel is Channel.EMAIL and greeting:
            parts.append(greeting)
        parts.append(" ".join(d.lines))
        if channel is Channel.EMAIL:
            sig = [self.rng.choice(SIGNOFFS), d.note(PiiKind.PERSON_NAME, contact.name),
                   contact.title, d.sponsor.legal_name]
            if self.rng.random() < 0.6:
                sig.append(d.note(PiiKind.PHONE, contact.phone))
            parts.append("\n".join(sig))
        return "\n\n".join(parts), requester

    def build(self, mix: dict[CallType, int]) -> tuple[list[Ticket], list[TicketLabel]]:
        drafts: list[tuple[Draft, bool, CallType | None]] = []
        for call_type, target in mix.items():
            made, attempts = 0, 0
            while made < target:
                attempts += 1
                if attempts > target * 50:
                    raise RuntimeError(f"could not generate enough {call_type} tickets")
                d = self.scenarios[call_type]()
                if d is None:
                    continue
                escalation, secondary = self._overlays(d)
                drafts.append((d, escalation, secondary))
                made += 1
        self.rng.shuffle(drafts)

        tickets: list[Ticket] = []
        labels: list[TicketLabel] = []
        window_start = datetime.combine(self.w.as_of - timedelta(days=45), time(8))
        window_end = datetime.combine(self.w.as_of, time(17))
        for index, (d, escalation, secondary) in enumerate(drafts):
            channel = self.rng.choices(list(Channel), weights=(60, 28, 12))[0]
            body, requester = self._render(d, channel)
            lo = max(window_start, d.not_before or window_start)
            span = max(int((window_end - lo).total_seconds()), 3600)
            created = min(lo + timedelta(seconds=self.rng.randint(3600, span)), window_end)
            ticket_id = f"TKT-{100001 + index}"
            priority = (Priority.URGENT if escalation else
                        self.rng.choices((Priority.LOW, Priority.NORMAL, Priority.HIGH),
                                         weights=(20, 60, 20))[0])
            tickets.append(Ticket(
                ticket_id=ticket_id, sponsor_id=d.sponsor.sponsor_id,
                created_at=created.replace(microsecond=0), channel=channel, priority=priority,
                status=TicketStatus.NEW, subject=d.subject, body=body, requester=requester,
            ))
            labels.append(TicketLabel(
                ticket_id=ticket_id, call_type=d.call_type, secondary_call_type=secondary,
                team=route(d.call_type, d.root_cause, escalation=escalation),
                root_cause=d.root_cause, escalation=escalation, advice_request=d.advice_request,
                difficulty=d.difficulty, related_file_ids=sorted(set(d.file_ids)),
                related_participant_ids=sorted(set(d.participant_ids)),
                # Template variants are built eagerly, so keep only spans that made it in.
                pii=[sp for sp in _dedupe(d.pii) if sp.value in f"{d.subject}\n{body}"],
            ))
        order = sorted(range(len(tickets)), key=lambda i: (tickets[i].created_at, i))
        ids = [f"TKT-{100001 + n}" for n in range(len(order))]
        return (
            [tickets[i].model_copy(update={"ticket_id": t}) for t, i in zip(ids, order, strict=True)],
            [labels[i].model_copy(update={"ticket_id": t}) for t, i in zip(ids, order, strict=True)],
        )


def _dedupe(spans: list[PiiSpan]) -> list[PiiSpan]:
    seen: set[tuple[str, str]] = set()
    out: list[PiiSpan] = []
    for span in spans:
        key = (span.kind.value, span.value)
        if key not in seen:
            seen.add(key)
            out.append(span)
    return out
