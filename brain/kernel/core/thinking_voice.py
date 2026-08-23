"""Thinking Voice maps real Prepende states to short surface status lines.

It is deliberately small and state-bound: callers provide a concrete state id,
and this module returns one display line for that state. It never estimates
percent complete or invents work that is not represented by the current event.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from prepende_brain.env import brand_env


SERIOUS_CONTEXTS = frozenset({
    "auth",
    "authentication",
    "authorization",
    "billing",
    "incident",
    "legal",
    "medical",
    "payment",
    "payments",
    "production",
    "security",
})


@dataclass(frozen=True)
class VoiceLine:
    text: str
    reference: str | None = None


@dataclass(frozen=True)
class ThinkingVoiceConfig:
    mode: str = "witty"
    allow_references: bool = False
    preferred_style: str = "dry"

    @classmethod
    def from_env(cls) -> "ThinkingVoiceConfig":
        return cls(
            mode=brand_env("THINKING_VOICE_MODE", "witty").lower(),
            allow_references=_truthy(brand_env("THINKING_VOICE_REFERENCES")),
            preferred_style=brand_env("THINKING_VOICE_STYLE", "dry").lower(),
        )


ORIGINAL_LINES: dict[str, tuple[VoiceLine, ...]] = {
    "goal.received": (
        VoiceLine("I have the brief. Opening the workspace."),
        VoiceLine("Goal received. Setting the table before touching anything."),
        VoiceLine("I have the ask. First, a clean place to think."),
    ),
    "memory.searching": (
        VoiceLine("Checking what the brain already knows."),
        VoiceLine("Looking for prior receipts before making new claims."),
        VoiceLine("Peeking into memory, politely."),
    ),
    "memory.recalled": (
        VoiceLine("Found relevant memory. Keeping it in the room."),
        VoiceLine("Memory has receipts. Folding them into the run."),
        VoiceLine("Some useful context came back from the shelves."),
    ),
    "strategy.choosing": (
        VoiceLine("Choosing the route before spending tokens."),
        VoiceLine("Picking the thinking shape for this one."),
        VoiceLine("Finding the right lane for the work."),
    ),
    "strategy.chosen": (
        VoiceLine("Route selected. Now doing the actual work."),
        VoiceLine("Tactic chosen. Time to make it real."),
        VoiceLine("The plan has a shape. Moving."),
    ),
    "tactic.running": (
        VoiceLine("Working the selected tactic now."),
        VoiceLine("The thinking engine is in motion."),
        VoiceLine("Turning the prompt into a result, one real step at a time."),
    ),
    "artifact.writing": (
        VoiceLine("Writing the deliverable to the workspace."),
        VoiceLine("Saving the result where it can be inspected."),
        VoiceLine("Putting the answer somewhere durable."),
    ),
    "memory.writing": (
        VoiceLine("Saving the useful bits for next time."),
        VoiceLine("Writing memory only after the result exists."),
        VoiceLine("Compounding the brain, quietly."),
    ),
    "run.done": (
        VoiceLine("Done. The run has a real artifact."),
        VoiceLine("Finished, with something on disk to show for it."),
        VoiceLine("Complete. No victory lap until the receipt exists."),
    ),
    "run.error": (
        VoiceLine("Something failed. Reporting the actual error."),
        VoiceLine("The run hit a real blocker. No pretending."),
        VoiceLine("Stopped on an error. Keeping the receipt honest."),
    ),
    "approval.required": (
        VoiceLine("Approval required before any external action."),
        VoiceLine("This needs a human yes before it leaves the room."),
        VoiceLine("Staged only. External action waits for approval."),
    ),
}


SERIOUS_LINES: dict[str, VoiceLine] = {
    "goal.received": VoiceLine("Request received. Preparing a scoped run."),
    "memory.searching": VoiceLine("Checking approved memory for relevant context."),
    "memory.recalled": VoiceLine("Relevant approved memory found."),
    "strategy.choosing": VoiceLine("Selecting an execution path."),
    "strategy.chosen": VoiceLine("Execution path selected."),
    "tactic.running": VoiceLine("Working on the request."),
    "artifact.writing": VoiceLine("Recording the result."),
    "memory.writing": VoiceLine("Saving approved memory context."),
    "run.done": VoiceLine("Run complete."),
    "run.error": VoiceLine("Run failed. Reporting the error."),
    "approval.required": VoiceLine("Approval required before external action."),
}


REFERENCE_LINES: dict[str, tuple[VoiceLine, ...]] = {
    "goal.received": (
        VoiceLine("Brief acquired. The map is not the territory.", "Alfred Korzybski allusion"),
    ),
    "strategy.choosing": (
        VoiceLine("Choosing a path. No ring required.", "The Lord of the Rings allusion"),
    ),
    "strategy.chosen": (
        VoiceLine("Tactic chosen. Engage, but with receipts.", "Star Trek allusion"),
    ),
    "tactic.running": (
        VoiceLine("Working. The spice is verification.", "Dune allusion"),
    ),
    "artifact.writing": (
        VoiceLine("Writing it down so it is not just vibes.", "Internet culture allusion"),
    ),
    "run.done": (
        VoiceLine("Done. The answer abides.", "The Big Lebowski allusion"),
    ),
}


def render_thinking_voice(
    state: str,
    *,
    context: str | None = None,
    config: ThinkingVoiceConfig | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a status-line payload for one real state.

    Unknown states are intentionally quiet instead of guessed. The `profile`
    argument is the personalization seam: persisted user/workspace preferences
    can override mode, reference allowance, and style without changing callers.
    """
    cfg = _merge_profile(config or ThinkingVoiceConfig.from_env(), profile or {})
    normalized = state.strip().lower()
    if normalized not in ORIGINAL_LINES:
        return {
            "state": normalized,
            "text": "",
            "mode": "quiet",
            "reference": None,
            "truth": "unknown_state_quiet",
        }

    mode = _mode_for(normalized, context, cfg)
    if mode == "quiet":
        line = VoiceLine("")
    elif mode == "serious":
        line = SERIOUS_LINES.get(normalized, VoiceLine("Working."))
    else:
        candidates = ORIGINAL_LINES[normalized]
        if cfg.allow_references and normalized in REFERENCE_LINES:
            candidates = candidates + REFERENCE_LINES[normalized]
        line = _stable_pick(candidates, normalized, context or "", cfg.preferred_style)

    return {
        "state": normalized,
        "text": line.text,
        "mode": mode,
        "reference": line.reference,
        "truth": "state_mapped",
    }


def status_event(state: str, text: str, *, context: str | None = None, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    event = {"type": "status", "text": text, "state": state}
    voice = render_thinking_voice(state, context=context or text, profile=profile)
    if voice["text"]:
        event["thinkingVoice"] = voice
    return event


def _mode_for(state: str, context: str | None, cfg: ThinkingVoiceConfig) -> str:
    requested = cfg.mode if cfg.mode in {"witty", "serious", "quiet"} else "witty"
    if requested == "quiet":
        return "quiet"
    if requested == "serious" or state in {"run.error", "approval.required"}:
        return "serious"
    words = set((context or "").lower().replace("/", " ").replace("_", " ").split())
    if words & SERIOUS_CONTEXTS:
        return "serious"
    return "witty"


def _merge_profile(cfg: ThinkingVoiceConfig, profile: dict[str, Any]) -> ThinkingVoiceConfig:
    prefs = profile.get("thinkingVoice") if isinstance(profile.get("thinkingVoice"), dict) else profile
    mode = str(prefs.get("mode") or cfg.mode).strip().lower()
    style = str(prefs.get("preferredStyle") or prefs.get("style") or cfg.preferred_style).strip().lower()
    allow_refs = prefs.get("allowReferences")
    return ThinkingVoiceConfig(
        mode=mode,
        allow_references=cfg.allow_references if allow_refs is None else bool(allow_refs),
        preferred_style=style,
    )


def _stable_pick(candidates: tuple[VoiceLine, ...], *parts: str) -> VoiceLine:
    key = "|".join(parts).encode("utf-8", "ignore")
    digest = hashlib.sha256(key).hexdigest()
    return candidates[int(digest[:8], 16) % len(candidates)]


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}
