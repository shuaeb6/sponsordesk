"""Sponsors, plan records, participants, and loans."""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from faker import Faker
from sponsordesk_core.models import (
    AutoEnrollment,
    Contact,
    Eligibility,
    EmploymentStatus,
    EntryFrequency,
    Loan,
    LoanRules,
    LoanStatus,
    MatchTier,
    Participant,
    PayrollFrequency,
    PlanRecord,
    PlanType,
    Sponsor,
    TestingStatus,
)

from sponsordesk_synth import vocab
from sponsordesk_synth.identifiers import IdentifierFactory, email_for, slugify

CENT = Decimal("0.01")

# 2026 IRS limits for elective deferrals and age 50+ catch-up contributions.
DEFERRAL_LIMIT_2026 = Decimal("24500.00")
CATCH_UP_LIMIT_2026 = Decimal("8000.00")

PERIODS_PER_YEAR = {
    PayrollFrequency.WEEKLY: 52,
    PayrollFrequency.BIWEEKLY: 26,
    PayrollFrequency.SEMIMONTHLY: 24,
    PayrollFrequency.MONTHLY: 12,
}

MATCH_FORMULAS: tuple[tuple[tuple[int, int, int], ...], ...] = (
    ((100, 0, 3), (50, 3, 5)),  # safe harbor basic
    ((100, 0, 4),),  # safe harbor enhanced
    ((50, 0, 6),),
    ((100, 0, 6),),
    ((25, 0, 6),),
)


def money(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def pct(value: float | int) -> Decimal:
    return Decimal(str(value)).quantize(CENT)


def _random_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, max((end - start).days, 0)))


def build_sponsors(
    rng: random.Random, fake: Faker, ids: IdentifierFactory, count: int, as_of: date
) -> list[Sponsor]:
    prefixes = rng.sample(vocab.COMPANY_PREFIXES, count)
    industries = list(vocab.INDUSTRIES)
    sponsors: list[Sponsor] = []
    for index, prefix in enumerate(prefixes):
        industry = industries[index % len(industries)]
        legal_name = (
            f"{prefix} {rng.choice(vocab.INDUSTRIES[industry])} {rng.choice(vocab.LEGAL_SUFFIXES)}"
        )
        domain = f"{slugify(prefix)}.example"
        contacts = [_contact(rng, fake, ids, domain) for _ in range(rng.randint(2, 4))]
        sponsor_id = f"SP-{1001 + index}"
        sponsors.append(
            Sponsor(
                sponsor_id=sponsor_id,
                legal_name=legal_name,
                ein=ids.ein(),
                industry=industry,
                state=rng.choice(vocab.STATES),
                employee_count=rng.choice((24, 38, 55, 80, 120, 175, 260, 410, 640, 950)),
                payroll_frequency=rng.choices(list(PayrollFrequency), weights=(15, 50, 25, 10))[0],
                payroll_provider=rng.choice(vocab.PAYROLL_PROVIDERS),
                service_tier=rng.choice(vocab.SERVICE_TIERS),
                plan_id=f"PL-{2001 + index}",
                onboarded_on=_random_date(rng, date(2016, 1, 1), as_of - timedelta(days=400)),
                primary_contact=contacts[0],
                secondary_contacts=contacts[1:],
                relationship_manager=rng.choice(vocab.RELATIONSHIP_MANAGERS),
            )
        )
    return sponsors


def _contact(rng: random.Random, fake: Faker, ids: IdentifierFactory, domain: str) -> Contact:
    first, last = fake.first_name(), fake.last_name()
    return Contact(
        name=f"{first} {last}",
        title=rng.choice(vocab.SPONSOR_CONTACT_TITLES),
        email=email_for(first, last, domain),
        phone=ids.phone(),
    )


def build_plan(rng: random.Random, ids: IdentifierFactory, sponsor: Sponsor) -> PlanRecord:
    if sponsor.industry in ("Education", "Nonprofit"):
        plan_type = PlanType.PLAN_403B
    elif rng.random() < 0.35:
        plan_type = PlanType.SAFE_HARBOR_401K
    else:
        plan_type = PlanType.TRADITIONAL_401K

    safe_harbor = plan_type is PlanType.SAFE_HARBOR_401K
    if safe_harbor:
        formula = MATCH_FORMULAS[rng.randint(0, 1)]
    elif sponsor.industry == "Nonprofit" and rng.random() < 0.4:
        formula = ()
    else:
        formula = MATCH_FORMULAS[rng.randint(2, len(MATCH_FORMULAS) - 1)]

    auto_enabled = rng.random() < 0.6
    escalate = auto_enabled and rng.random() < 0.5
    loans_allowed = rng.random() < 0.8

    if safe_harbor:
        testing = TestingStatus.EXEMPT_SAFE_HARBOR
    else:
        testing = rng.choices(
            (
                TestingStatus.PASSED,
                TestingStatus.PENDING,
                TestingStatus.FAILED_ADP,
                TestingStatus.FAILED_ACP,
            ),
            weights=(55, 25, 12, 8),
        )[0]

    short_name = sponsor.legal_name.rsplit(" ", 1)[0]
    plan_label = {"403b": "403(b)", "401k": "401(k)", "safe_harbor_401k": "Safe Harbor 401(k)"}
    return PlanRecord(
        plan_id=sponsor.plan_id,
        sponsor_id=sponsor.sponsor_id,
        plan_name=f"{short_name} {plan_label[plan_type.value]} Plan",
        plan_type=plan_type,
        plan_number=ids.plan_number(),
        effective_date=sponsor.onboarded_on.replace(day=1),
        plan_year_end="12-31",
        eligibility=Eligibility(
            min_age=rng.choice((18, 21, 21)),
            service_months=rng.choice((0, 0, 3, 6, 12)),
            entry=rng.choice(list(EntryFrequency)),
        ),
        auto_enrollment=AutoEnrollment(
            enabled=auto_enabled,
            default_rate_pct=pct(rng.choice((3, 4, 5, 6))) if auto_enabled else pct(0),
            auto_escalate=escalate,
            escalate_step_pct=pct(1) if escalate else pct(0),
            escalate_cap_pct=pct(rng.choice((10, 12, 15))) if escalate else pct(0),
        ),
        roth_allowed=rng.random() < 0.75,
        employer_match=[
            MatchTier(match_rate_pct=pct(r), band_from_pct=pct(a), band_to_pct=pct(b))
            for r, a, b in formula
        ],
        match_true_up=bool(formula) and rng.random() < 0.5,
        vesting_schedule=(
            "immediate"
            if safe_harbor
            else rng.choice(("immediate", "3-year cliff", "6-year graded", "2-6 year graded"))
        ),
        loans=LoanRules(
            allowed=loans_allowed,
            max_outstanding=rng.choice((1, 2)) if loans_allowed else 0,
            min_amount=money(1000),
            max_amount=money(50000),
            max_pct_of_vested=pct(50),
            origination_fee=money(rng.choice((50, 75, 100))),
            general_purpose_max_years=5,
            residential_max_years=15,
        ),
        hardship_withdrawals_allowed=rng.random() < 0.75,
        in_service_age=rng.choice((59, None)),
        testing_status=testing,
        form_5500_due=date(2027, 7, 31),
        annual_deferral_limit=DEFERRAL_LIMIT_2026,
        catch_up_limit=CATCH_UP_LIMIT_2026,
    )


_SALARY_BANDS = {
    "Technology": (70_000, 240_000),
    "Professional Services": (55_000, 210_000),
    "Healthcare": (38_000, 190_000),
    "Hospitality": (28_000, 85_000),
    "Retail": (29_000, 90_000),
    "Education": (36_000, 110_000),
    "Nonprofit": (34_000, 105_000),
    "Construction": (42_000, 140_000),
    "Manufacturing": (38_000, 125_000),
    "Logistics": (36_000, 120_000),
}


def build_participants(
    rng: random.Random,
    fake: Faker,
    ids: IdentifierFactory,
    sponsor: Sponsor,
    plan: PlanRecord,
    as_of: date,
) -> list[Participant]:
    count = min(sponsor.employee_count, rng.randint(14, 36))
    domain = sponsor.primary_contact.email.split("@", 1)[1]
    low, high = _SALARY_BANDS[sponsor.industry]
    people: list[Participant] = []
    used_names: set[tuple[str, str]] = set()
    for index in range(count):
        first, last = fake.first_name(), fake.last_name()
        while (first, last) in used_names:
            first, last = fake.first_name(), fake.last_name()
        used_names.add((first, last))

        # A handful of recent hires per sponsor feeds enrollment scenarios.
        if index < 3:
            hire = _random_date(rng, as_of - timedelta(days=120), as_of - timedelta(days=10))
        else:
            hire = _random_date(rng, date(2009, 1, 1), as_of - timedelta(days=150))
        dob = _random_date(rng, date(1960, 1, 1), date(2004, 12, 31))

        roll = rng.random()
        status = (
            EmploymentStatus.ACTIVE
            if roll < 0.88
            else EmploymentStatus.TERMINATED
            if roll < 0.97
            else EmploymentStatus.LEAVE
        )
        termination = (
            _random_date(rng, max(hire, as_of - timedelta(days=180)), as_of - timedelta(days=5))
            if status is EmploymentStatus.TERMINATED
            else None
        )

        recent = (as_of - hire).days < 130
        if recent:
            enrolled = plan.auto_enrollment.enabled and rng.random() < 0.5
        elif plan.auto_enrollment.enabled:
            enrolled = rng.random() < 0.94
        else:
            enrolled = rng.random() < 0.74

        if enrolled:
            pretax = pct(
                rng.choices(
                    (1, 2, 3, 4, 5, 6, 8, 10, 12, 15), weights=(3, 4, 12, 10, 14, 16, 12, 10, 6, 3)
                )[0]
            )
            roth = (
                pct(rng.choice((1, 2, 3, 5)))
                if plan.roth_allowed and rng.random() < 0.25
                else pct(0)
            )
        else:
            pretax = roth = pct(0)

        salary = money(round(rng.uniform(low, high), -2))
        tenure_years = max((as_of - hire).days / 365.25, 0.05)
        contribution_rate = float(pretax + roth) / 100 + 0.03
        balance = money(float(salary) * contribution_rate * tenure_years * rng.uniform(0.9, 1.6))
        vest_factor = _vesting_factor(plan.vesting_schedule, tenure_years)
        vested = money(float(balance) * (0.75 + 0.25 * vest_factor))

        people.append(
            Participant(
                participant_id=f"PT-{sponsor.sponsor_id[3:]}-{index + 1:03d}",
                sponsor_id=sponsor.sponsor_id,
                employee_id=f"E{rng.randint(10000, 99999)}",
                ssn=ids.ssn(),
                first_name=first,
                last_name=last,
                dob=dob,
                email=email_for(first, last, domain),
                hire_date=hire,
                termination_date=termination,
                status=status,
                annual_salary=salary,
                enrolled=enrolled,
                enrollment_date=(hire + timedelta(days=rng.randint(0, 90))) if enrolled else None,
                pretax_rate_pct=pretax,
                roth_rate_pct=roth,
                pending_rate_change_pct=(
                    pct(rng.choice((4, 5, 6, 8, 10)))
                    if enrolled and status is EmploymentStatus.ACTIVE and rng.random() < 0.1
                    else None
                ),
                account_number=ids.account_number(),
                balance=balance if enrolled or not recent else money(0),
                vested_balance=vested if enrolled or not recent else money(0),
            )
        )
    _dedupe_employee_ids(rng, people)
    return people


def _dedupe_employee_ids(rng: random.Random, people: list[Participant]) -> None:
    seen: set[str] = set()
    for i, person in enumerate(people):
        employee_id = person.employee_id
        while employee_id in seen:
            employee_id = f"E{rng.randint(10000, 99999)}"
        seen.add(employee_id)
        if employee_id != person.employee_id:
            people[i] = person.model_copy(update={"employee_id": employee_id})


def _vesting_factor(schedule: str, tenure_years: float) -> float:
    if schedule == "immediate":
        return 1.0
    if schedule == "3-year cliff":
        return 1.0 if tenure_years >= 3 else 0.0
    if schedule == "6-year graded":
        return min(max((int(tenure_years) - 1) * 0.2, 0.0), 1.0)
    return min(max((int(tenure_years) - 1) * 0.2, 0.0), 1.0) if tenure_years >= 2 else 0.0


def build_loans(
    rng: random.Random,
    sponsor: Sponsor,
    plan: PlanRecord,
    participants: list[Participant],
    as_of: date,
) -> list[Loan]:
    if not plan.loans.allowed:
        return []
    periods = PERIODS_PER_YEAR[sponsor.payroll_frequency]
    loans: list[Loan] = []
    for person in participants:
        eligible = (
            person.enrolled
            and person.status is EmploymentStatus.ACTIVE
            and person.vested_balance >= Decimal(4000)
        )
        if not eligible or rng.random() > 0.16:
            continue
        cap = min(plan.loans.max_amount, person.vested_balance * plan.loans.max_pct_of_vested / 100)
        principal = money(min(float(cap), rng.uniform(1500, 22000)))
        if principal < plan.loans.min_amount:
            continue
        years = plan.loans.general_purpose_max_years
        issued = _random_date(rng, as_of - timedelta(days=900), as_of - timedelta(days=60))
        payment = _amortized_payment(principal, Decimal("0.085"), periods, years)
        elapsed = int((as_of - issued).days / 365.25 * periods)
        outstanding = max(money(float(principal) - float(payment) * elapsed * 0.82), money(0))
        loans.append(
            Loan(
                loan_id=f"LN-{person.participant_id[3:]}-{len(loans) + 1}",
                participant_id=person.participant_id,
                sponsor_id=sponsor.sponsor_id,
                purpose=rng.choices(("general", "residential"), weights=(85, 15))[0],
                principal=principal,
                outstanding_balance=outstanding,
                payment_per_period=payment,
                issued_on=issued,
                maturity_date=issued + timedelta(days=int(365.25 * years)),
                status=LoanStatus.ACTIVE if outstanding > 0 else LoanStatus.PAID_OFF,
            )
        )
    return loans


def _amortized_payment(
    principal: Decimal, annual_rate: Decimal, periods: int, years: int
) -> Decimal:
    r = float(annual_rate) / periods
    n = periods * years
    return money(float(principal) * r / (1 - (1 + r) ** -n))
