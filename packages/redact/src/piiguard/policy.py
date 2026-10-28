"""Destination policy loaded from YAML."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from pathlib import Path

import yaml


class Action(StrEnum):
    TOKENIZE = "tokenize"
    MASK = "mask"
    ALLOW = "allow"


@dataclass(frozen=True)
class Policy:
    default_action: Action
    destinations: dict[str, dict[str, Action]]

    @classmethod
    def load(cls, path: Path | None = None) -> Policy:
        if path is None:
            text = resources.files("piiguard").joinpath("default_policy.yaml").read_text()
        else:
            text = path.read_text()
        raw = yaml.safe_load(text)
        return cls(
            default_action=Action(raw.get("default_action", "tokenize")),
            destinations={
                dest: {kind: Action(action) for kind, action in rules.items()}
                for dest, rules in raw["destinations"].items()
            },
        )

    def action(self, destination: str, kind: str) -> Action:
        if destination not in self.destinations:
            raise KeyError(f"unknown destination {destination!r}")
        # Unknown data classes fall back to the most restrictive default.
        return self.destinations[destination].get(kind, self.default_action)
