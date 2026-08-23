"""Self-healing + self-improvement — a scoped, gated propose->commit loop.

detect (watchdog: LLM-as-judge over recent traces)
  -> propose (offline DSPy optimize -> candidate prompt version, NOT activated)
  -> gate (Promptfoo eval in CI must beat current + structured human approval)
  -> deploy gradually -> rollback = one pointer flip.

Immutable guardrails (code, NOT agent-writable): the agent never edits its own
guardrails/approval policy; hard ceilings on token/cost/retry/tool-chain depth;
self-modification scoped to prompt artifacts, not arbitrary code. Every run,
candidate, approval, promotion request, and audit event carries immutable
tenant/workspace identity. Activation requires a winning evaluation plus an
explicit same-scope human approver; unscoped legacy versions are audit-only.

Implementation: ``self_improve/improver.py`` + ``self_improve/store.py``.
"""
