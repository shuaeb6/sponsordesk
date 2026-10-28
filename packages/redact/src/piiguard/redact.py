"""Apply a policy to detected PII and keep a reversible token vault."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from piiguard.detect import Detection, Detector
from piiguard.policy import Action, Policy

TOKEN_RE = re.compile(r"\[([A-Z_]+)_(\d+)\]")


@dataclass
class RedactionResult:
    text: str
    destination: str
    detections: list[Detection]
    vault: dict[str, str] = field(default_factory=dict)

    @property
    def counts(self) -> dict[str, int]:
        return dict(Counter(d.kind for d in self.detections))

    def restore(self, text: str) -> str:
        """Re-identify tokens in text produced downstream (for an approved human reply)."""
        return TOKEN_RE.sub(lambda m: self.vault.get(m.group(0), m.group(0)), text)


def _mask(value: str) -> str:
    keep = value[-4:]
    return re.sub(r"[A-Za-z0-9]", "*", value[:-4]) + keep


class Redactor:
    def __init__(self, policy: Policy | None = None, known_names: Iterable[str] = ()) -> None:
        self.policy = policy or Policy.load()
        self.detector = Detector(known_names)

    def redact(
        self, text: str, destination: str = "llm", vault: dict[str, str] | None = None
    ) -> RedactionResult:
        """Redact ``text`` for ``destination``.

        Passing a shared ``vault`` keeps tokens stable across several fields of the
        same ticket (subject and body), so ``[PERSON_1]`` means the same person in both.
        """
        vault = {} if vault is None else vault
        reverse = {v: k for k, v in vault.items()}
        detections = self.detector.detect(text)
        out: list[str] = []
        cursor = 0
        for d in detections:
            out.append(text[cursor : d.start])
            action = self.policy.action(destination, d.kind)
            if action is Action.ALLOW:
                out.append(d.value)
            elif action is Action.MASK:
                out.append(_mask(d.value))
            else:
                token = reverse.get(d.value)
                if token is None:
                    n = sum(1 for k in vault if k.rsplit("_", 1)[0] == f"[{d.kind.upper()}") + 1
                    token = f"[{d.kind.upper()}_{n}]"
                    vault[token] = d.value
                    reverse[d.value] = token
                out.append(token)
            cursor = d.end
        out.append(text[cursor:])
        return RedactionResult("".join(out), destination, detections, vault)
