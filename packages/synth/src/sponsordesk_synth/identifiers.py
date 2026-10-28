"""Identifier factories that can never collide with real people or organizations.

* SSNs use area numbers 900-999, which the Social Security Administration never
  issues as SSNs.
* EINs use the ``00`` prefix, which the IRS does not assign.
* Phone numbers use 555-0100 through 555-0199, reserved for fictional use.
* Email addresses use the ``.example`` TLD, reserved by RFC 2606.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable

SAFE_SSN_RE = re.compile(r"^9\d{2}-\d{2}-\d{4}$")
SAFE_EIN_RE = re.compile(r"^00-\d{7}$")
SAFE_PHONE_RE = re.compile(r"^\(\d{3}\) 555-01\d{2}$")
SAFE_EMAIL_RE = re.compile(r"^[a-z0-9._-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.example$")

_AREA_CODES = ("212", "347", "646", "718", "917", "201", "516", "914", "312", "415", "617")


class IdentifierFactory:
    """Issues unique fictional identifiers from a seeded RNG."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self._issued: set[str] = set()

    def _unique(self, make: Callable[[], str]) -> str:
        for _ in range(1000):
            value = make()
            if value not in self._issued:
                self._issued.add(value)
                return value
        raise RuntimeError("identifier space exhausted")

    def ssn(self) -> str:
        r = self._rng
        return self._unique(
            lambda: f"{r.randint(900, 999)}-{r.randint(1, 99):02d}-{r.randint(1, 9999):04d}"
        )

    def ein(self) -> str:
        r = self._rng
        return self._unique(lambda: f"00-{r.randint(0, 9_999_999):07d}")

    def account_number(self) -> str:
        r = self._rng
        return self._unique(lambda: f"RK{r.randint(10**9, 10**10 - 1)}")

    def plan_number(self) -> str:
        r = self._rng
        return self._unique(lambda: f"{r.randint(1, 3):03d}-{r.randint(10000, 99999)}")

    def phone(self) -> str:
        r = self._rng
        return f"({r.choice(_AREA_CODES)}) 555-01{r.randint(0, 99):02d}"


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def email_for(first: str, last: str, domain: str) -> str:
    local = re.sub(r"[^a-z0-9.]", "", f"{first}.{last}".lower())
    return f"{local}@{domain}"
