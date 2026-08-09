"""Core data shapes for the Goal Loop. Stdlib only."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class Goal:
    text: str
    id: str = field(default_factory=lambda: _id("goal"))
    created_at: float = field(default_factory=time.time)


@dataclass
class Candidate:
    text: str
    model: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class CandidateSet:
    candidates: list[Candidate] = field(default_factory=list)


@dataclass
class DecisiveResult:
    text: str
    confidence: float = 1.0
    rationale: str = ""
    tactic: str = ""
    model: str = ""


@dataclass
class Choice:
    """What the Strategist returns: how to pursue this goal."""
    tactic: Any
    resolver: Any
    budget: dict[str, Any] = field(default_factory=dict)
