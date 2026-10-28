from __future__ import annotations

from pathlib import Path

import pytest
from piiguard import Policy, Redactor

TEXT = (
    "Hi, Maria Lopez (SSN 912-34-5678, DOB 03/14/1988) has acct RK1234567890 and a balance "
    "of $41,220.10. Reach me at maria.lopez@acme.example or (212) 555-0142. Our EIN is "
    "00-1234567. Another employee, SSN ending in 4321, is fine."
)


@pytest.fixture
def redactor() -> Redactor:
    return Redactor(known_names=["Maria Lopez"])


def test_llm_destination_removes_every_value(redactor: Redactor) -> None:
    out = redactor.redact(TEXT, "llm").text
    for raw in (
        "912-34-5678",
        "03/14/1988",
        "RK1234567890",
        "$41,220.10",
        "Maria Lopez",
        "maria.lopez@acme.example",
        "(212) 555-0142",
        "00-1234567",
        "4321",
    ):
        assert raw not in out, raw


def test_tokens_are_stable_and_reversible(redactor: Redactor) -> None:
    vault: dict[str, str] = {}
    first = redactor.redact("Maria Lopez called.", "llm", vault)
    second = redactor.redact("Please call Maria Lopez back.", "llm", vault)
    assert first.text == "[PERSON_NAME_1] called."
    assert "[PERSON_NAME_1]" in second.text
    assert second.restore("Reply to [PERSON_NAME_1].") == "Reply to Maria Lopez."


def test_reviewer_sees_masked_ssn(redactor: Redactor) -> None:
    out = redactor.redact(TEXT, "human_reviewer").text
    assert "***-**-5678" in out
    assert "Maria Lopez" in out


def test_unknown_destination_is_refused(redactor: Redactor) -> None:
    with pytest.raises(KeyError):
        redactor.redact(TEXT, "slack")


def test_unknown_class_falls_back_to_tokenize(tmp_path: Path) -> None:
    policy_file = tmp_path / "p.yaml"
    policy_file.write_text("version: 1\ndefault_action: tokenize\ndestinations:\n  llm: {}\n")
    out = Redactor(Policy.load(policy_file)).redact("SSN 912-34-5678", "llm").text
    assert out == "SSN [SSN_1]"


def test_pay_dates_are_not_mistaken_for_birth_dates(redactor: Redactor) -> None:
    assert (
        redactor.redact("The 7/10/2026 file was late.", "llm").text
        == "The 7/10/2026 file was late."
    )
