"""Service desk taxonomy: call types, teams, and the default routing table.

Routing is a function of call type *and* root cause. The default team handles a
call type unless a root cause discovered in the plan record or payroll data points
elsewhere (for example, a "missed contribution" caused by a rejected payroll file
belongs to Payroll Integrations, not Contribution Operations).
"""

from __future__ import annotations

from enum import StrEnum


class CallType(StrEnum):
    PAYROLL_FILE_ERROR = "payroll_file_error"
    MISSED_CONTRIBUTION = "missed_contribution"
    ENROLLMENT_ELIGIBILITY = "enrollment_eligibility"
    DEFERRAL_CHANGE = "deferral_change"
    LOAN_SERVICING = "loan_servicing"
    DISTRIBUTION_REQUEST = "distribution_request"
    COMPLIANCE_TESTING = "compliance_testing"
    PORTAL_ACCESS = "portal_access"


class Team(StrEnum):
    PAYROLL_INTEGRATIONS = "payroll_integrations"
    CONTRIBUTION_OPERATIONS = "contribution_operations"
    ENROLLMENT_SERVICES = "enrollment_services"
    LOAN_SERVICING = "loan_servicing"
    DISTRIBUTIONS = "distributions"
    COMPLIANCE_TESTING = "compliance_testing"
    SPONSOR_SUPPORT = "sponsor_support"
    RELATIONSHIP_MANAGEMENT = "relationship_management"


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class Channel(StrEnum):
    EMAIL = "email"
    PORTAL = "portal"
    PHONE_NOTE = "phone_note"


class RootCause(StrEnum):
    """Machine-checkable causes the agent can confirm from records."""

    PAYROLL_FILE_REJECTED = "payroll_file_rejected"
    PAYROLL_FILE_NOT_RECEIVED = "payroll_file_not_received"
    PAYROLL_FILE_WARNINGS = "payroll_file_warnings"
    FUNDING_NOT_RECEIVED = "funding_not_received"
    MATCH_TRUE_UP_TIMING = "match_true_up_timing"
    NOT_YET_ELIGIBLE = "not_yet_eligible"
    AUTO_ENROLL_NOT_APPLIED = "auto_enroll_not_applied"
    DEFERRAL_NOT_UPDATED_IN_PAYROLL = "deferral_not_updated_in_payroll"
    ELECTION_PROCESSING = "election_processing"
    LOAN_REPAYMENT_MISSING_IN_PAYROLL = "loan_repayment_missing_in_payroll"
    LOAN_LIMIT_REACHED = "loan_limit_reached"
    LOAN_GENERAL_INQUIRY = "loan_general_inquiry"
    DISTRIBUTION_PROCESSING = "distribution_processing"
    TESTING_INQUIRY = "testing_inquiry"
    ACCESS_ISSUE = "access_issue"


DEFAULT_TEAM: dict[CallType, Team] = {
    CallType.PAYROLL_FILE_ERROR: Team.PAYROLL_INTEGRATIONS,
    CallType.MISSED_CONTRIBUTION: Team.CONTRIBUTION_OPERATIONS,
    CallType.ENROLLMENT_ELIGIBILITY: Team.ENROLLMENT_SERVICES,
    CallType.DEFERRAL_CHANGE: Team.CONTRIBUTION_OPERATIONS,
    CallType.LOAN_SERVICING: Team.LOAN_SERVICING,
    CallType.DISTRIBUTION_REQUEST: Team.DISTRIBUTIONS,
    CallType.COMPLIANCE_TESTING: Team.COMPLIANCE_TESTING,
    CallType.PORTAL_ACCESS: Team.SPONSOR_SUPPORT,
}

# Root causes that move a ticket away from its call type's default team.
ROOT_CAUSE_OVERRIDES: dict[RootCause, Team] = {
    RootCause.PAYROLL_FILE_REJECTED: Team.PAYROLL_INTEGRATIONS,
    RootCause.PAYROLL_FILE_NOT_RECEIVED: Team.PAYROLL_INTEGRATIONS,
    RootCause.DEFERRAL_NOT_UPDATED_IN_PAYROLL: Team.PAYROLL_INTEGRATIONS,
    RootCause.LOAN_REPAYMENT_MISSING_IN_PAYROLL: Team.PAYROLL_INTEGRATIONS,
}


def route(call_type: CallType, root_cause: RootCause | None, *, escalation: bool) -> Team:
    """Reference routing policy. Escalations always go to relationship management."""
    if escalation:
        return Team.RELATIONSHIP_MANAGEMENT
    if root_cause is not None and root_cause in ROOT_CAUSE_OVERRIDES:
        return ROOT_CAUSE_OVERRIDES[root_cause]
    return DEFAULT_TEAM[call_type]
