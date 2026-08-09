"""The kernel's ports — the stable, typed interfaces every other part plugs into.

These are the only things meant to last. Implementations behind them are
swappable (models, memory engines, durable backends); the ports are not.
A port changes only by deliberate, versioned decision.

See docs/ARCHITECTURE.md §2 for the full table.
"""

from .model import ModelGateway
from .memory import MemoryStore
from .knowledge import Knowledge
from .prompts import PromptRegistry
from .durable import DurableExecution
from .connectors import Connectors
from .workspace import Workspace
from .tactics import Strategist, Tactic, Resolver
from .registry import Registry, RegistryEntry, READINESS_VALUES, KINDS
from .meditation import (
    AsyncMeditationPolicy,
    CommitIntent,
    EvidenceDigest,
    EvidenceDigestEntry,
    IntentCandidate,
    MeditationInput,
    MeditationPolicy,
    MeditationReceipt,
    MeditationResolution,
)

__all__ = [
    "ModelGateway",
    "MemoryStore",
    "Knowledge",
    "PromptRegistry",
    "DurableExecution",
    "Connectors",
    "Workspace",
    "Strategist",
    "Tactic",
    "Resolver",
    "Registry",
    "RegistryEntry",
    "READINESS_VALUES",
    "KINDS",
    "CommitIntent",
    "AsyncMeditationPolicy",
    "EvidenceDigest",
    "EvidenceDigestEntry",
    "IntentCandidate",
    "MeditationInput",
    "MeditationPolicy",
    "MeditationReceipt",
    "MeditationResolution",
]
