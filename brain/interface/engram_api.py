"""ChatGPT Actions / dashboard bridge for the Prepende orchestrator.

This is deliberately thin: it validates the public request shape, scopes every
call to an allowed workspace, logs request/response metadata, and then delegates
to the existing Prepende brain. External execution is gated behind approval
requests; this bridge never fires paid, destructive, external, or publishing
actions directly.

Run:  python -m interface.engram_api
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from interface.action_gate import looks_like_action_request
from kernel.core.brain import build_brain
from kernel.core.loop import GoalLoop
from kernel.core.model_thought_bus import run_thought_bus_async
from kernel.core.semantic_meditation import SemanticMeditationPolicy
from kernel.core.thought_bus import run_thought_bus
from memory.candidates import default_queue
from routes.conversations import (
    handle_delete as handle_conversation_delete,
    handle_get as handle_conversation_get,
    handle_patch as handle_conversation_patch,
    handle_post as handle_conversation_post,
)
from routes.memories import (
    handle_delete as handle_memory_delete,
    handle_get as handle_memory_get,
    handle_post as handle_memory_post,
)
from services.learning_service import LearningService

# Allowed workspaces (memory scopes). Generic defaults only — NO product or client
# names baked into the source (SEPARATION.md). Add your own via the ENGRAM_WORKSPACES
# env var (comma-separated), which lives in .env and stays out of the repo.
_DEFAULT_WORKSPACES = {"default", "personal", "research", "marketing", "work"}
ALLOWED_WORKSPACES = set(
    w.strip() for w in os.environ.get("ENGRAM_WORKSPACES", "").split(",") if w.strip()
) or _DEFAULT_WORKSPACES

_loop = None
_cfg = None
_learning_service: LearningService | None = None
_rate_windows: dict[str, tuple[float, int]] = {}

_EXTERNAL_TERMS = (
    "send", "email", "publish", "post", "deploy", "delete", "remove",
    "charge", "purchase", "buy", "paid", "payment", "invoice",
    "external", "webhook", "workflow", "n8n", "figma",
    "connector", "api call", "run this", "execute",
)
_NO_LOOP_MODES = {"memory", "memory_context", "context", "recall"}
_REGULAR_CHAT_MODES = {"chat", "regular_chat", "conversation", "answer", "direct_answer"}
_IMAGE_HANDOFF_MODES = {"image", "images", "image_handoff", "image_prompt", "image_spec", "visual"}
_NO_LOOP_POLICIES = {"never", "no_loop", "no-loop", "memory", "memory_only", "memory-only", "context"}
_REGULAR_CHAT_POLICIES = {"chat", "regular_chat", "conversation", "answer"}
_FORCE_LOOP_POLICIES = {"always", "loop", "goal_loop", "goal-loop"}
_MEMORY_ONLY_PHRASES = (
    "what do you remember", "what does engram remember", "what is in memory",
    "what is in our brain", "search memory", "search our memory", "memory search",
    "memory context", "recall memory", "look up memory", "from memory",
    "reference memory", "reference our brain", "what have we saved",
)
_LOOP_WORK_PHRASES = (
    "build ", "implement", "fix ", "write ", "draft ", "make a plan",
    "create a plan", "plan for", "roadmap", "strategy", "research this",
    "decide", "compare", "evaluate", "review this", "summarize",
    "analyze", "create ", "make ", "generate",
)
_REGULAR_CHAT_PHRASES = (
    "hello", "hi ", "hey ", "thanks", "thank you", "what is ", "who is ",
    "how does ", "can you explain", "explain ", "talk to me",
)
_IMAGE_REQUEST_PHRASES = (
    "generate an image", "create an image", "make an image", "produce an image",
    "draw ", "illustrate", "image of", "picture of", "photo of", "logo",
    "poster", "thumbnail", "visual", "render",
)


def _brain():
    global _loop, _cfg
    if _loop is None:
        _loop, _cfg, _gw = build_brain()
    return _loop


def _learning() -> LearningService:
    global _learning_service
    if _learning_service is None:
        _learning_service = LearningService()
    return _learning_service


def _tenant_loop(workspace_id: str) -> GoalLoop:
    """Reuse the composed brain but isolate all state by workspace scope."""
    base = _brain()
    vaults = getattr(base, "scoped_vaults", None)
    knowledge = vaults.for_scope(workspace_id) if vaults is not None else None
    return GoalLoop(
        base.gateway,
        base.strategist,
        base.workspace,
        memory=base.memory,
        scope=workspace_id,
        workspace_id=workspace_id,
        connectors=ApprovalGatedConnectors(base.connectors, workspace_id),
        runs=base.runs,
        knowledge=knowledge,
        verifier=getattr(base, "verifier", None),
        # The public API surface never writes durable memory silently: the loop
        # only PROPOSES candidates; promotion goes through the approval path.
        # ("auto" is the TUI/dev policy — see kernel/core/loop.py.)
        memory_policy="candidate",
        vault_recall=knowledge is not None,
    )


class ApprovalGatedConnectors:
    """Expose connector metadata but never execute connector calls from the API."""

    def __init__(self, inner: Any, workspace_id: str) -> None:
        self.inner = inner
        self.workspace_id = workspace_id

    async def list_tools(
        self, *, scope: str | None = None,
        tenant_id: str | None = None, workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        tools = list(await self.inner.list_tools(
            tenant_id=self.workspace_id, workspace_id=self.workspace_id
        )) if self.inner else []
        gated = []
        for tool in tools:
            item = dict(tool)
            item["ready"] = False
            item["approvalRequired"] = True
            item["approvalPolicy"] = "external connector calls require approval through /api/engram/approvals/create"
            gated.append(item)
        return gated

    async def call(
        self, tool_id: str, args: dict[str, Any], *, scope: str | None = None,
        tenant_id: str | None = None, workspace_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "blocked": True,
            "approvalRequired": True,
            "toolId": tool_id,
            "workspaceId": self.workspace_id,
            "message": "External connector execution requires an approval request.",
        }


def _response(
    workspace_id: str,
    intent: str,
    result: Any = None,
    *,
    actions: list[dict[str, Any]] | None = None,
    memory_updates: list[dict[str, Any]] | None = None,
    approval_required: bool = False,
    approval_request: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    next_actions: list[dict[str, Any]] | None = None,
    loop_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "workspaceId": workspace_id,
        "intent": intent,
        "result": result,
        "actions": actions or [],
        "memoryUpdates": memory_updates or [],
        "approvalRequired": approval_required,
        "approvalRequest": approval_request,
        "warnings": warnings or [],
        "nextActions": next_actions or [],
    }
    if loop_decision is not None:
        payload["loopDecision"] = loop_decision
    return payload


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    n = int(handler.headers.get("content-length", 0) or 0)
    if not n:
        return {}
    data = handler.rfile.read(n)
    return json.loads(data or b"{}")


def _validate_workspace(data: dict[str, Any]) -> tuple[str, list[str]]:
    workspace_id = str(data.get("workspaceId") or "").strip()
    if not workspace_id:
        return "", ["workspaceId is required"]
    if workspace_id not in ALLOWED_WORKSPACES:
        return workspace_id, [f"workspaceId must be one of: {', '.join(sorted(ALLOWED_WORKSPACES))}"]
    return workspace_id, []


def _looks_external(text: str, data: dict[str, Any]) -> bool:
    explicit = data.get("externalAction") or data.get("requiresApproval")
    if explicit is not None:
        return bool(explicit)
    # Structured fields are explicit action descriptors (a workflow/tool name
    # like "send-invoice"), so a plain term scan stays right for them. Free
    # text goes through the shared gate: ASKING for an action gates; talking
    # about one must not (interface/action_gate.py).
    fields = " ".join(str(data.get(key) or "") for key in ("action", "tool", "workflow")).lower()
    if fields.strip() and any(term in fields for term in _EXTERNAL_TERMS):
        return True
    return looks_like_action_request(text)


def _looks_image_request(text: str, data: dict[str, Any]) -> bool:
    mode = str(data.get("mode") or "").strip().lower()
    if mode in _IMAGE_HANDOFF_MODES:
        return True
    blob = " ".join(str(x) for x in [text, data.get("message", ""), data.get("input", ""), data.get("intent", "")]).lower()
    return any(phrase in blob for phrase in _IMAGE_REQUEST_PHRASES)


def _looks_regular_chat(text: str, data: dict[str, Any]) -> bool:
    mode = str(data.get("mode") or "").strip().lower()
    policy = str(data.get("loopPolicy") or data.get("loop_policy") or "").strip().lower()
    if mode in _REGULAR_CHAT_MODES or policy in _REGULAR_CHAT_POLICIES or _truthy(data.get("chatOnly")):
        return True
    blob = " ".join(str(x) for x in [text, data.get("message", ""), data.get("input", ""), data.get("intent", "")]).strip().lower()
    if not blob:
        return False
    return any(blob.startswith(phrase) or phrase in blob for phrase in _REGULAR_CHAT_PHRASES) and not any(
        phrase in blob for phrase in _LOOP_WORK_PHRASES
    )


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _loop_decision(workspace_id: str, goal: str, data: dict[str, Any]) -> dict[str, Any]:
    """Decide before model/artifact work whether this request needs the loop.

    Memory can be referenced without entering GoalLoop. The default stays
    conservative: only clear memory-context requests bypass the loop; normal
    goals still use GoalLoop, and explicit Thought Bus mode is handled before
    this function.
    """
    mode = str(data.get("mode") or "").strip().lower()
    policy = str(data.get("loopPolicy") or data.get("loop_policy") or "auto").strip().lower() or "auto"
    memory_query = str(data.get("memoryQuery") or data.get("query") or "").strip()
    text = " ".join(str(x) for x in [goal, memory_query, data.get("intent", "")]).lower()

    decision = {
        "workspaceId": workspace_id,
        "policy": policy,
        "mode": mode or "default",
        "useLoop": True,
        "route": "goal_loop",
        "reason": "GoalLoop is appropriate for this request.",
        "memoryReferenced": False,
        "memoryQuery": memory_query or goal,
    }
    if mode in _NO_LOOP_MODES:
        decision.update({
            "useLoop": False,
            "route": "memory_context",
            "reason": f"mode:{mode} requests memory/context only.",
            "memoryReferenced": True,
        })
        return decision
    if _looks_image_request(goal, data):
        decision.update({
            "useLoop": False,
            "route": "image_handoff",
            "reason": "Prepende cannot generate images directly; return a prompt/spec handoff without claiming image output.",
            "memoryReferenced": bool(memory_query or data.get("memoryRefs")),
        })
        return decision
    if mode in _REGULAR_CHAT_MODES or policy in _REGULAR_CHAT_POLICIES or _truthy(data.get("chatOnly")):
        decision.update({
            "useLoop": False,
            "route": "regular_chat",
            "reason": "Caller requested ordinary chat; answer with the host chat model without GoalLoop or Thought Bus.",
            "memoryReferenced": bool(memory_query or data.get("memoryRefs")),
        })
        return decision
    if policy in _NO_LOOP_POLICIES or _truthy(data.get("memoryOnly")):
        decision.update({
            "useLoop": False,
            "route": "memory_context",
            "reason": "loopPolicy requests memory/context only.",
            "memoryReferenced": True,
        })
        return decision
    if policy in _FORCE_LOOP_POLICIES or _truthy(data.get("forceLoop")):
        decision.update({
            "useLoop": True,
            "route": "goal_loop",
            "reason": "loopPolicy forces GoalLoop.",
            "memoryReferenced": True,
        })
        return decision
    if _looks_regular_chat(goal, data):
        decision.update({
            "useLoop": False,
            "route": "regular_chat",
            "reason": "Request looks conversational and does not need GoalLoop, Thought Bus, artifact work, or memory writes.",
            "memoryReferenced": False,
        })
        return decision
    if any(phrase in text for phrase in _MEMORY_ONLY_PHRASES):
        if not any(phrase in text for phrase in _LOOP_WORK_PHRASES):
            decision.update({
                "useLoop": False,
                "route": "memory_context",
                "reason": "Request is a memory/context lookup, not a goal to pursue.",
                "memoryReferenced": True,
            })
            return decision
        decision.update({
            "useLoop": True,
            "route": "goal_loop",
            "reason": "Memory was requested, but the user also asked for work that needs GoalLoop.",
            "memoryReferenced": True,
        })
        return decision
    decision["memoryReferenced"] = bool(memory_query or data.get("memoryRefs"))
    return decision


def _log_path(env_name: str, default: str) -> Path:
    from prepende_brain.private_fs import secure_directory

    path = Path(os.environ.get(env_name, default))
    secure_directory(path.parent)
    return path


def _append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    from prepende_brain.private_fs import secure_file

    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, sort_keys=True) + "\n")
    secure_file(path, required=True)


def _summarize_for_log(value: Any) -> dict[str, Any]:
    text = str(value or "")
    return {
        "present": bool(text),
        "chars": len(text),
        "sha256": hashlib.sha256(text.encode()).hexdigest()[:16] if text else "",
    }


def _log_exchange(path: str, workspace_id: str, request: dict[str, Any], response: dict[str, Any], status: int) -> None:
    req = dict(request)
    for key in (
        "content", "goal", "message", "input", "query", "text", "entry",
        "journalText", "summary", "description", "sourceText", "finding",
        "reviewNotes", "claim",
    ):
        if key in req:
            req[key] = _summarize_for_log(req[key])
    req.pop("metadata", None)
    req.pop("action", None)
    _append_jsonl(_log_path("ENGRAM_API_LOG", "./.engram/api_requests.jsonl"), {
        "ts": time.time(),
        "path": path,
        "workspaceId": workspace_id,
        "status": status,
        "request": req,
        "response": {
            "approvalRequired": response.get("approvalRequired"),
            "warnings": response.get("warnings", []),
            "actionCount": len(response.get("actions", [])),
            "memoryUpdateCount": len(response.get("memoryUpdates", [])),
        },
    })


def _rate_limited(client_id: str) -> bool:
    limit = int(os.environ.get("ENGRAM_API_RATE_LIMIT_PER_MINUTE", "120"))
    now = time.time()
    start, count = _rate_windows.get(client_id, (now, 0))
    if now - start >= 60:
        _rate_windows[client_id] = (now, 1)
        return False
    if count >= limit:
        return True
    _rate_windows[client_id] = (start, count + 1)
    return False


def _create_approval(workspace_id: str, intent: str, action: dict[str, Any] | None, reason: str) -> dict[str, Any]:
    approval = {
        "id": f"apr_{uuid.uuid4().hex[:12]}",
        "workspaceId": workspace_id,
        "intent": intent,
        "action": action or {},
        "reason": reason,
        "status": "pending",
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _append_jsonl(_log_path("ENGRAM_APPROVALS_LOG", "./.engram/approvals.jsonl"), approval)
    return approval


def _public_memory_hit(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"content": str(item)}
    out = {
        "id": str(item.get("id") or ""),
        "content": str(item.get("content") or ""),
        "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
    }
    created = item.get("created_at") or item.get("createdAt")
    if created is not None:
        out["createdAt"] = str(created)
    return out


def _validate_memory_id(data: dict[str, Any]) -> tuple[str, list[str]]:
    memory_id = str(data.get("memoryId") or data.get("id") or "").strip()
    if not memory_id:
        return "", ["memoryId is required"]
    if "/" in memory_id or len(memory_id) > 120:
        return memory_id, ["memoryId is malformed"]
    return memory_id, []


def _run_orchestrator(workspace_id: str, goal: str) -> dict[str, Any]:
    loop = _tenant_loop(workspace_id)
    out: list[str] = []
    events: list[dict[str, Any]] = []
    memory_candidates: list[dict[str, Any]] = []
    box: dict[str, Any] = {"artifact": None, "error": None, "receipt": None}

    async def on_event(ev: dict[str, Any]) -> None:
        events.append(ev)
        if ev.get("type") == "token":
            out.append(str(ev.get("text", "")))
        elif ev.get("type") == "artifact":
            box["artifact"] = ev.get("text")
        elif ev.get("type") == "error":
            box["error"] = ev.get("text")
        elif ev.get("type") == "memory_candidate":
            memory_candidates.append(ev)
        elif ev.get("type") == "receipt":
            box["receipt"] = ev.get("receipt")

    asyncio.run(loop.run(goal, on_event))
    if memory_candidates:
        # Stage every ASSESS candidate in the durable queue so the approval
        # lane can actually promote it later; the receipt carries the id.
        async def _stage() -> None:
            queue = default_queue()
            for ev in memory_candidates:
                text = str(ev.get("text", "")).strip()
                if not text:
                    continue
                staged = await queue.propose(
                    text,
                    scope=workspace_id,
                    kind=str(ev.get("kind", "semantic")),
                    source="goal_loop.assess",
                    metadata={"scores": ev.get("scores", {})},
                )
                ev["candidateId"] = staged["id"]
        asyncio.run(_stage())
    return {
        "text": "".join(out).strip(),
        "artifact": box["artifact"],
        "error": box["error"],
        "model": getattr(loop.gateway, "name", "?"),
        "events": events,
        "memoryCandidates": memory_candidates,
        "receipt": box["receipt"],
    }


def _goal_loop_receipt(r: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Truthful receipt for a GoalLoop run, derived from the kernel's own run receipt.

    The kernel emits {"type": "receipt", ...} with mode, tactic, verifier verdict
    (or "skipped"), and memory proposed vs written. The API surfaces those facts
    instead of restating them, so this receipt cannot drift from what the loop
    actually did. Under memory_policy="candidate" (always, on this surface) the
    written list stays empty and candidates require ASSESS + approval.
    """
    kernel = r.get("receipt") or {}
    kernel_memory = kernel.get("memory") or {}
    written = list(kernel_memory.get("written") or [])
    candidates = list(r.get("memoryCandidates") or [])
    provenance = candidates[0].get("provenance", {}) if candidates else {}
    memory_updates = [{
        "content": str(c.get("text", "")),
        "candidateId": c.get("candidateId"),
        "status": str(c.get("status", "pending_assessment")),
        "decision": str(c.get("decision", "stage_for_review")),
        "scores": c.get("scores", {}),
        "promotionReady": bool(c.get("promotionReady", False)),
        "promotionBlockedBy": list(c.get("promotionBlockedBy", [])),
        "requiresAssess": True,
        "durableWrite": False,
        "persisted": bool(c.get("persisted", False)),
        "source": "goal_loop.assess",
    } for c in candidates]
    external_actions = list(kernel.get("externalActions") or [])
    result = {
        "mode": str(kernel.get("mode") or "goal_loop"),
        "loopExecuted": bool(kernel.get("loopUsed", True)),
        "text": r["text"],
        "artifact": r["artifact"],
        "model": r["model"],
        "error": r["error"],
        "tactic": str(kernel.get("tactic") or provenance.get("tactic") or ""),
        "agentsInvoked": list(kernel.get("agentsInvoked") or []),
        "verifier": dict(kernel.get("verifier") or {"status": "skipped"}),
        "memoryProposed": len(memory_updates),
        "memoryWritten": len(written),
        "externalActions": external_actions or "none",
        "actionExecuted": bool(kernel.get("actionExecuted", False)),
    }
    return result, memory_updates


def _run_thought_bus(workspace_id: str, goal: str, data: dict[str, Any], intent: str) -> dict[str, Any]:
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]

    try:
        max_depth = max(0, min(int(data.get("maxDepth") or 2), 4))
    except (TypeError, ValueError):
        max_depth = 2
    try:
        budget = max(1, min(int(data.get("budget") or 4), 12))
    except (TypeError, ValueError):
        budget = 4
    meditate_raw = data.get("meditate", False)
    meditate = meditate_raw is True or str(meditate_raw).strip().lower() in {"1", "true", "yes", "on"}
    semantic_raw = data.get("semanticMeditation", data.get("semantic_meditation", False))
    semantic_meditation = (
        semantic_raw is True
        or str(semantic_raw).strip().lower() in {"1", "true", "yes", "on"}
    )
    agent_mode = str(data.get("agentMode", data.get("agent_mode")) or "").strip().lower()
    model_raw = data.get("modelBacked", data.get("model_backed", False))
    model_backed = (
        semantic_meditation
        or agent_mode == "model"
        or model_raw is True
        or str(model_raw).strip().lower() in {"1", "true", "yes", "on"}
    )
    common = {
        "workspace_id": workspace_id,
        "goal": goal,
        "memory_refs": _string_list(data.get("memoryRefs")),
        "source_refs": _string_list(data.get("sourceRefs")),
        "max_depth": max_depth,
        "budget": budget,
        "constraints": _string_list(data.get("constraints")) or None,
    }
    if model_backed:
        gateway = _brain().gateway

        def _bounded_int(name: str, default: int, low: int, high: int) -> int:
            try:
                return max(low, min(int(data.get(name) or default), high))
            except (TypeError, ValueError):
                return default

        result = asyncio.run(run_thought_bus_async(
            **common,
            gateway=gateway,
            meditate=meditate and not semantic_meditation,
            semantic_policy=SemanticMeditationPolicy(gateway) if semantic_meditation else None,
            model_call_budget=_bounded_int("modelCallBudget", 5, 1, 16),
            max_concurrency=_bounded_int("maxConcurrency", 4, 1, 4),
            agent_timeout_seconds=_bounded_int("agentTimeoutMs", 45_000, 100, 120_000) / 1000,
            total_timeout_seconds=_bounded_int("totalTimeoutMs", 60_000, 100, 180_000) / 1000,
        ))
    else:
        result = run_thought_bus(**common, meditate=meditate)
    needs_approval = bool(result.get("approval_required"))
    approval_required = needs_approval and not result.get("model_backed", False)
    approval_request = None
    # The model-backed lane is deliberately proposal-only for this task.  It
    # may return a blocked fusion, but it must not create a durable approval
    # record.  Preserve the legacy deterministic API approval behavior until a
    # separate ledger integration is explicitly approved.
    if approval_required:
        approval_request = _create_approval(
            workspace_id,
            intent,
            {
                "type": "thought_bus_external_action",
                "goal": goal,
                "runId": result["run_id"],
                "fusionStatus": result.get("fusion_decision", {}).get("status"),
            },
            "Thought Bus blocked because an external action or blocker requires human approval.",
        )
    warnings = ["No external action was executed."]
    if needs_approval and result.get("model_backed", False):
        warnings.append("Model-backed Thought Bus is blocked; no approval was staged.")
    unavailable = [receipt for receipt in result.get("thought_receipts", []) if not receipt.get("available", True)]
    if unavailable:
        warnings.append(f"{len(unavailable)} model-backed agent role(s) were unavailable; inspect their receipts.")
    memory_updates = list(result.get("memory_updates") or [])
    next_actions = [{"type": "review_fusion_decision", "runId": result["run_id"]}]
    meditation_receipt = result.get("meditation_receipt")
    commit_intent = result.get("commit_intent")
    if commit_intent:
        next_actions.insert(0, {
            "type": "review_commit_intent",
            "commitIntentId": commit_intent["id"],
            "runId": result["run_id"],
        })
    if approval_request:
        next_actions.insert(0, {"type": "review_approval", "approvalRequestId": approval_request["id"]})
    return {
        "workspaceId": workspace_id,
        "intent": intent,
        "runId": result["run_id"],
        "mode": "thought_bus",
        "status": result["status"],
        "result": {
            "goal": result["goal"],
            "depth": result["depth"],
            "maxDepth": result["max_depth"],
            "elapsedMs": result.get("elapsedMs", 0),
            "meditationActive": meditate or semantic_meditation,
            "agentMode": result.get("agent_mode", "deterministic"),
            "modelBacked": bool(result.get("model_backed", False)),
            "semanticMeditation": bool(result.get("semantic_meditation", False)),
            "packetHash": result.get("packet_hash", ""),
            "modelCalls": int(result.get("model_calls", 0)),
            "modelCallBudget": int(result.get("model_call_budget", 0)),
            "actionExecuted": False,
        },
        "thoughtReceipts": result["thought_receipts"],
        "fusionDecision": result["fusion_decision"],
        "evidenceDigest": result.get("evidence_digest"),
        "meditationReceipt": meditation_receipt,
        "commitIntent": commit_intent,
        "actions": [],
        "memoryUpdates": memory_updates,
        "approvalRequired": approval_required,
        "approvalRequest": approval_request,
        "warnings": warnings,
        "nextActions": next_actions,
        "externalActions": "none",
        "actionExecuted": False,
    }


def _memory_context_orchestration(workspace_id: str, intent: str, decision: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    try:
        k = max(1, min(int(data.get("k") or 10), 25))
    except (TypeError, ValueError):
        k = 10
    query = str(decision.get("memoryQuery") or data.get("query") or data.get("goal") or data.get("message") or "").strip()
    mem = _brain().memory
    hits = asyncio.run(mem.search(query, scope=workspace_id, k=k)) if mem and query else []
    memories = [_public_memory_hit(hit) for hit in hits]
    context = "\n".join(f"- {item['content']}" for item in memories if item.get("content"))
    warnings = ["GoalLoop was not executed; this response only references memory/context."]
    if not memories:
        warnings.append("No memory found.")
    return _response(
        workspace_id,
        intent,
        result={
            "mode": "memory_context",
            "loopExecuted": False,
            "context": context,
            "memories": memories,
            "count": len(memories),
        },
        warnings=warnings,
        next_actions=[{"type": "resubmit_with_loopPolicy_always", "label": "Use GoalLoop if you want Prepende to pursue work, not just recall context."}],
        loop_decision=decision,
    )


def _regular_chat_orchestration(workspace_id: str, intent: str, decision: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    message = str(data.get("message") or data.get("input") or data.get("goal") or data.get("query") or "").strip()
    return _response(
        workspace_id,
        intent,
        result={
            "mode": "regular_chat",
            "loopExecuted": False,
            "answerMode": "host_chat_model",
            "message": message,
            "instruction": "Answer normally in the host ChatGPT conversation. Do not call GoalLoop, Thought Bus, tools, memory write, or external actions for this request.",
            "externalActions": "none",
            "actionExecuted": False,
        },
        warnings=["GoalLoop was not executed; this is ordinary chat routing."],
        next_actions=[{"type": "answer_in_host_chat", "label": "Use the regular chat model for the response."}],
        loop_decision=decision,
    )


def _image_handoff_prompt(goal: str, data: dict[str, Any]) -> str:
    style = str(data.get("style") or data.get("imageStyle") or "").strip()
    aspect = str(data.get("aspectRatio") or data.get("aspect") or "").strip()
    parts = [goal]
    if style:
        parts.append(f"Style: {style}.")
    if aspect:
        parts.append(f"Aspect ratio: {aspect}.")
    parts.append("Avoid claiming the image already exists unless a separate image-generation tool returns an actual asset.")
    return " ".join(part for part in parts if part)


def _image_handoff_orchestration(workspace_id: str, intent: str, decision: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    goal = str(data.get("goal") or data.get("message") or data.get("input") or data.get("query") or "").strip()
    return _response(
        workspace_id,
        intent,
        result={
            "mode": "image_handoff",
            "loopExecuted": False,
            "imageGeneration": "unavailable_in_engram",
            "canDraftPrompt": True,
            "prompt": _image_handoff_prompt(goal, data),
            "handoffTargets": ["native_chatgpt_image_tool", "approved_image_generation_connector"],
            "externalActions": "none",
            "actionExecuted": False,
        },
        warnings=["Prepende did not generate an image. It returned a prompt/spec handoff only."],
        next_actions=[
            {"type": "use_native_image_generation_if_available", "label": "Generate with the host image tool outside Prepende."},
            {"type": "request_approval_for_external_image_connector", "label": "Ask for approval before using an external image connector."},
        ],
        loop_decision=decision,
    )


def _approval_gate_orchestration(workspace_id: str, intent: str, goal: str, decision: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    decision.update({
        "useLoop": False,
        "route": "approval_gate",
        "reason": "Request appears to involve an external, paid, destructive, or publishing action; approval is required before any loop or execution.",
    })
    approval = _create_approval(
        workspace_id,
        intent,
        {"type": "external_action", "goal": goal, "requested": data.get("action") or data.get("tool") or data.get("workflow")},
        "Request appears to involve an external, paid, destructive, or publishing action.",
    )
    return _response(
        workspace_id,
        intent,
        result="Approval required before Prepende can execute this external action.",
        approval_required=True,
        approval_request=approval,
        warnings=["No external action was executed."],
        next_actions=[{"type": "review_approval", "approvalRequestId": approval["id"]}],
        loop_decision=decision,
    )


def orchestrate(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "orchestrate").strip() or "orchestrate"
    mode = str(data.get("mode") or "").strip().lower()
    goal = str(data.get("goal") or data.get("message") or data.get("input") or data.get("query") or "").strip()
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    if not goal:
        return 400, _response(workspace_id, intent, warnings=["goal, message, or input is required"])
    if mode == "thought_bus":
        response = _run_thought_bus(workspace_id, goal, data, intent)
        response["loopDecision"] = {
            "workspaceId": workspace_id,
            "policy": str(data.get("loopPolicy") or data.get("loop_policy") or "auto"),
            "mode": "thought_bus",
            "useLoop": True,
            "route": "thought_bus",
            "reason": "mode:thought_bus explicitly requests the recursive Thought Bus.",
            "memoryReferenced": bool(data.get("memoryRefs")),
            "memoryQuery": goal,
        }
        return 200, response
    loop_decision = _loop_decision(workspace_id, goal, data)
    if _looks_external(goal, data):
        return 200, _approval_gate_orchestration(workspace_id, intent, goal, loop_decision, data)
    if not loop_decision["useLoop"]:
        if loop_decision["route"] == "memory_context":
            return 200, _memory_context_orchestration(workspace_id, intent, loop_decision, data)
        if loop_decision["route"] == "regular_chat":
            return 200, _regular_chat_orchestration(workspace_id, intent, loop_decision, data)
        if loop_decision["route"] == "image_handoff":
            return 200, _image_handoff_orchestration(workspace_id, intent, loop_decision, data)
    r = _run_orchestrator(workspace_id, goal)
    status = 500 if r.get("error") else 200
    result, memory_updates = _goal_loop_receipt(r)
    return status, _response(
        workspace_id,
        intent,
        result=result,
        memory_updates=memory_updates,
        warnings=["Orchestrator returned an error."] if r.get("error") else [],
        loop_decision=loop_decision,
    )


def memory_search(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "memory.search")
    query = str(data.get("query") or "").strip()
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    if not query:
        return 400, _response(workspace_id, intent, warnings=["query is required"])
    try:
        k = max(1, min(int(data.get("k") or 10), 25))
    except (TypeError, ValueError):
        return 400, _response(workspace_id, intent, warnings=["k must be an integer from 1 to 25"])
    mem = _brain().memory
    hits = asyncio.run(mem.search(query, scope=workspace_id, k=k)) if mem else []
    memories = [_public_memory_hit(hit) for hit in hits]
    warnings = ["No memory found."] if not memories else []
    return 200, _response(workspace_id, intent, result={"memories": memories, "count": len(memories)}, warnings=warnings)


def memory_context(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "memory.context")
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    try:
        k = max(1, min(int(data.get("k") or 10), 25))
    except (TypeError, ValueError):
        return 400, _response(workspace_id, intent, warnings=["k must be an integer from 1 to 25"])
    query = str(data.get("query") or data.get("message") or "").strip()
    mem = _brain().memory
    hits = asyncio.run(mem.search(query, scope=workspace_id, k=k)) if mem else []
    memories = [_public_memory_hit(hit) for hit in hits]
    context = "\n".join(f"- {item['content']}" for item in memories if item.get("content"))
    return 200, _response(
        workspace_id,
        intent,
        result={"context": context, "memories": memories, "count": len(memories)},
        warnings=["No memory found."] if not memories else [],
    )


def memory_write(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "memory.write")
    content = str(data.get("content") or "").strip()
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    if not content:
        return 400, _response(workspace_id, intent, warnings=["content is required"])
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    mem = _brain().memory
    if mem is None:
        return 503, _response(workspace_id, intent, warnings=["memory store is not configured"])
    memory_id = asyncio.run(mem.write(content, scope=workspace_id, metadata=metadata))
    update = {"id": memory_id, "workspaceId": workspace_id, "metadata": metadata}
    return 200, _response(workspace_id, intent, result={"id": memory_id}, memory_updates=[update])


def memory_propose(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Stage a memory candidate (the Assess gate). Persists to the candidate
    queue only — NEVER to the MemoryStore — so it cannot enter recall until
    an explicit approval promotes it."""
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "memory.propose")
    content = str(data.get("content") or "").strip()
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    if not content:
        return 400, _response(workspace_id, intent, warnings=["content is required"])
    kind = str(data.get("kind") or "semantic").strip().lower()
    candidate = asyncio.run(default_queue().propose(
        content,
        scope=workspace_id,
        kind=kind,
        source=str(data.get("source") or "actions_bridge.propose"),
    ))
    return 200, _response(
        workspace_id,
        intent,
        result={
            "candidate": candidate,
            "status": "pending_assessment",
            "persisted": False,
            "durableWrite": False,
        },
        warnings=["Nothing was written to memory. Approval is required to promote this candidate."],
        next_actions=[{"type": "approve_memory_candidate", "candidateId": candidate["id"]}],
    )


def memory_candidates(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "memory.candidates")
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    pending = asyncio.run(default_queue().list_pending(scope=workspace_id))
    return 200, _response(workspace_id, intent, result={"pending": pending, "count": len(pending)})


def memory_approve_candidate(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """The explicit promotion door: pending candidate -> durable memory, with
    provenance on the written row."""
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "memory.approve_candidate")
    candidate_id = str(data.get("candidateId") or "").strip()
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    if not candidate_id:
        return 400, _response(workspace_id, intent, warnings=["candidateId is required"])
    mem = _brain().memory
    if mem is None:
        return 503, _response(workspace_id, intent, warnings=["memory store is not configured"])
    approved = asyncio.run(default_queue().approve(candidate_id, scope=workspace_id, store=mem))
    if approved is None:
        return 404, _response(workspace_id, intent, result={"candidateId": candidate_id},
                              warnings=["Pending candidate not found in this workspace."])
    update = {"id": approved["memoryId"], "workspaceId": workspace_id,
              "candidateId": approved["id"], "approval": "assess_approved"}
    return 200, _response(workspace_id, intent,
                          result={"candidate": approved, "memoryId": approved["memoryId"]},
                          memory_updates=[update])


def memory_reject_candidate(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "memory.reject_candidate")
    candidate_id = str(data.get("candidateId") or "").strip()
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    if not candidate_id:
        return 400, _response(workspace_id, intent, warnings=["candidateId is required"])
    rejected = asyncio.run(default_queue().reject(
        candidate_id, scope=workspace_id, reason=str(data.get("reason") or "")))
    if rejected is None:
        return 404, _response(workspace_id, intent, result={"candidateId": candidate_id},
                              warnings=["Pending candidate not found in this workspace."])
    return 200, _response(workspace_id, intent, result={"candidate": rejected})


def memory_update(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "memory.update")
    memory_id, id_warnings = _validate_memory_id(data)
    warnings.extend(id_warnings)
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)

    has_content = "content" in data
    has_metadata = isinstance(data.get("metadata"), dict)
    content = str(data.get("content") or "").strip() if has_content else None
    if has_content and not content:
        return 400, _response(workspace_id, intent, warnings=["content cannot be blank"])
    if content and len(content) > 5000:
        return 400, _response(workspace_id, intent, warnings=["content must be 5000 characters or fewer"])
    if not has_content and not has_metadata:
        return 400, _response(workspace_id, intent, warnings=["content or metadata is required"])

    mem = _brain().memory
    if mem is None:
        return 503, _response(workspace_id, intent, warnings=["memory store is not configured"])
    updated = asyncio.run(mem.update(memory_id, scope=workspace_id, content=content, metadata=data.get("metadata") if has_metadata else None))
    if updated is None:
        return 404, _response(workspace_id, intent, result={"memoryId": memory_id}, warnings=["Memory not found."])
    item = _public_memory_hit(updated)
    return 200, _response(workspace_id, intent, result={"memory": item}, memory_updates=[item])


def memory_delete(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "memory.delete")
    memory_id, id_warnings = _validate_memory_id(data)
    warnings.extend(id_warnings)
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    mem = _brain().memory
    if mem is None:
        return 503, _response(workspace_id, intent, warnings=["memory store is not configured"])
    deleted = asyncio.run(mem.delete(memory_id, scope=workspace_id))
    if not deleted:
        return 404, _response(workspace_id, intent, result={"memoryId": memory_id}, warnings=["Memory not found."])
    return 200, _response(workspace_id, intent, result={"memoryId": memory_id, "deleted": True})


def workflows_plan(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "workflows.plan")
    goal = str(data.get("goal") or data.get("intentText") or "").strip()
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    if not goal:
        return 400, _response(workspace_id, intent, warnings=["goal is required"])
    wf = getattr(_brain(), "workflows", None)
    menu = wf.list() if wf else []
    selected = asyncio.run(wf.select(goal)) if wf else None
    actions: list[dict[str, Any]] = []
    approval = None
    if selected:
        action = {"type": "workflow", "name": selected, "status": "planned", "requiresApproval": True}
        actions.append(action)
        approval = _create_approval(workspace_id, intent, action, "Workflow execution is an external action.")
    return 200, _response(
        workspace_id,
        intent,
        result={"selectedWorkflow": selected, "availableWorkflows": menu},
        actions=actions,
        approval_required=bool(selected),
        approval_request=approval,
        warnings=["No workflow was executed."] if selected else [],
        next_actions=[{"type": "review_approval", "approvalRequestId": approval["id"]}] if approval else [],
    )


def approvals_create(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "approvals.create").strip() or "approvals.create"
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    action = data.get("action") if isinstance(data.get("action"), dict) else {}
    if not action:
        return 400, _response(workspace_id, intent, warnings=["action object is required"])
    approval = _create_approval(workspace_id, intent, action, str(data.get("reason") or "Approval requested by API caller."))
    return 200, _response(
        workspace_id,
        intent,
        result={"approvalRequestId": approval["id"], "status": approval["status"]},
        approval_required=True,
        approval_request=approval,
        next_actions=[{"type": "review_approval", "approvalRequestId": approval["id"]}],
    )


def learning_extract_entities(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "entities.extract")
    text = str(data.get("text") or data.get("content") or data.get("input") or "").strip()
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    if not text:
        return 400, _response(workspace_id, intent, warnings=["text is required"])
    result = _learning().extract_entities(text)
    return 200, _response(workspace_id, intent, result=result)


def learning_write_journal(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "journal.write")
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    try:
        result = _learning().write_journal_entry(workspace_id, data)
    except ValueError as exc:
        return 400, _response(workspace_id, intent, warnings=[str(exc)])
    memory_updates = []
    mem = _brain().memory
    if mem is None:
        warnings.append("memory store is not configured")
    else:
        for durable in result["extracted"].get("durableMemories", []):
            memory_id = asyncio.run(mem.write(
                durable,
                scope=workspace_id,
                metadata={
                    "source": "journal",
                    "journalEntryId": result["journalEntry"]["id"],
                    "topics": result["extracted"].get("topics", []),
                },
            ))
            memory_updates.append({
                "id": memory_id,
                "journalEntryId": result["journalEntry"]["id"],
                "metadata": {"source": "journal"},
            })
    result["durableMemoryIds"] = [item["id"] for item in memory_updates]
    return 200, _response(workspace_id, intent, result=result, memory_updates=memory_updates, warnings=warnings)


def learning_upsert_person(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "people.upsert")
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    try:
        person = _learning().upsert_person(workspace_id, data)
    except ValueError as exc:
        return 400, _response(workspace_id, intent, warnings=[str(exc)])
    return 200, _response(workspace_id, intent, result={"person": person})


def learning_update_relationship_graph(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "relationships.update_graph")
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    try:
        edge = _learning().update_relationship_graph(workspace_id, data)
    except ValueError as exc:
        return 400, _response(workspace_id, intent, warnings=[str(exc)])
    return 200, _response(workspace_id, intent, result={"relationshipEdge": edge})


def learning_relationship_context(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "relationships.context")
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    result = _learning().get_relationship_context(workspace_id, data)
    return 200, _response(workspace_id, intent, result=result, warnings=result.get("warnings", []))


def learning_upsert_project(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "projects.upsert")
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    try:
        project = _learning().upsert_project(workspace_id, data)
    except ValueError as exc:
        return 400, _response(workspace_id, intent, warnings=[str(exc)])
    return 200, _response(workspace_id, intent, result={"project": project})


def learning_link_memory_to_project(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "projects.link_memory")
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    try:
        result = _learning().link_memory_to_project(workspace_id, data)
    except LookupError as exc:
        return 404, _response(workspace_id, intent, warnings=[str(exc)])
    except ValueError as exc:
        return 400, _response(workspace_id, intent, warnings=[str(exc)])
    return 200, _response(workspace_id, intent, result=result)


def learning_project_context(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "projects.context")
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    result = _learning().get_project_context(workspace_id, data)
    return 200, _response(workspace_id, intent, result=result, warnings=result.get("warnings", []))


def learning_ingest_knowledge(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "knowledge.ingest")
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    try:
        result = _learning().ingest_knowledge(workspace_id, data)
    except ValueError as exc:
        return 400, _response(workspace_id, intent, warnings=[str(exc)])
    approval = result.get("approval")
    return 200, _response(
        workspace_id,
        intent,
        result=result,
        approval_required=bool(approval),
        approval_request=approval,
        next_actions=[{"type": "review_knowledge_update", "approvalRequestId": approval["id"]}] if approval else [],
    )


def learning_search_knowledge(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "knowledge.search")
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    try:
        result = _learning().search_knowledge(workspace_id, data)
    except ValueError as exc:
        return 400, _response(workspace_id, intent, warnings=[str(exc)])
    warnings = ["No knowledge found."] if result["count"] == 0 else []
    return 200, _response(workspace_id, intent, result=result, warnings=warnings)


def learning_update_knowledge_graph(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "knowledge.update_graph")
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    try:
        item = _learning().update_knowledge_graph(workspace_id, data)
    except LookupError as exc:
        return 404, _response(workspace_id, intent, warnings=[str(exc)])
    except ValueError as exc:
        return 400, _response(workspace_id, intent, warnings=[str(exc)])
    return 200, _response(workspace_id, intent, result={"knowledgeItem": item})


def learning_create_research_agent(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "research_agents.create")
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    try:
        agent = _learning().create_research_agent(workspace_id, data)
    except ValueError as exc:
        return 400, _response(workspace_id, intent, warnings=[str(exc)])
    return 200, _response(workspace_id, intent, result={"researchAgent": agent})


def learning_run_research_agent(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "research_agents.run")
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    try:
        result = _learning().run_research_agent(workspace_id, data)
    except LookupError as exc:
        return 404, _response(workspace_id, intent, warnings=[str(exc)])
    except ValueError as exc:
        return 400, _response(workspace_id, intent, warnings=[str(exc)])
    approval = result.get("approval")
    return 200, _response(
        workspace_id,
        intent,
        result=result,
        approval_required=True,
        approval_request=approval,
        warnings=["Research findings are pending review; durable knowledge was not updated."],
        next_actions=[{"type": "review_agent_finding", "findingId": result["finding"]["id"]}],
    )


def learning_review_agent_findings(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "research_agents.review_findings")
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    try:
        finding = _learning().review_agent_findings(workspace_id, data)
    except LookupError as exc:
        return 404, _response(workspace_id, intent, warnings=[str(exc)])
    except ValueError as exc:
        return 400, _response(workspace_id, intent, warnings=[str(exc)])
    return 200, _response(workspace_id, intent, result={"finding": finding})


def learning_approve_knowledge_update(data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    workspace_id, warnings = _validate_workspace(data)
    intent = str(data.get("intent") or "knowledge.approve_update")
    if warnings:
        return 400, _response(workspace_id, intent, warnings=warnings)
    try:
        result = _learning().approve_knowledge_update(workspace_id, data)
    except LookupError as exc:
        return 404, _response(workspace_id, intent, warnings=[str(exc)])
    except ValueError as exc:
        return 400, _response(workspace_id, intent, warnings=[str(exc)])
    return 200, _response(workspace_id, intent, result=result, memory_updates=[result["knowledgeItem"]])


_POST_ROUTES = {
    "/api/engram/orchestrate": orchestrate,
    "/api/engram/memory/context": memory_context,
    "/api/engram/memory/search": memory_search,
    "/api/engram/memory/write": memory_write,
    "/api/engram/memory/propose": memory_propose,
    "/api/engram/memory/candidates": memory_candidates,
    "/api/engram/memory/approve-candidate": memory_approve_candidate,
    "/api/engram/memory/reject-candidate": memory_reject_candidate,
    "/api/engram/memory/update": memory_update,
    "/api/engram/memory/delete": memory_delete,
    "/api/engram/workflows/plan": workflows_plan,
    "/api/engram/approvals/create": approvals_create,
    "/api/engram/entities/extract": learning_extract_entities,
    "/api/engram/journal/write": learning_write_journal,
    "/api/engram/people/upsert": learning_upsert_person,
    "/api/engram/relationships/update": learning_update_relationship_graph,
    "/api/engram/relationships/context": learning_relationship_context,
    "/api/engram/projects/upsert": learning_upsert_project,
    "/api/engram/projects/link-memory": learning_link_memory_to_project,
    "/api/engram/projects/context": learning_project_context,
    "/api/engram/knowledge/ingest": learning_ingest_knowledge,
    "/api/engram/knowledge/search": learning_search_knowledge,
    "/api/engram/knowledge/update-graph": learning_update_knowledge_graph,
    "/api/engram/research-agents/create": learning_create_research_agent,
    "/api/engram/research-agents/run": learning_run_research_agent,
    "/api/engram/research-agents/review-findings": learning_review_agent_findings,
    "/api/engram/knowledge/approve-update": learning_approve_knowledge_update,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _auth_error(self) -> tuple[int, str] | None:
        token = os.environ.get("ENGRAM_API_TOKEN", "").strip()
        if not token:
            return None
        header = self.headers.get("Authorization", "")
        if not header:
            return 401, "missing auth"
        if header != f"Bearer {token}":
            return 401, "invalid auth"
        return None

    def _client_id(self) -> str:
        auth = self.headers.get("Authorization", "")
        return auth or self.client_address[0]

    def _send(self, code: int, obj: dict[str, Any]) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/engram/health":
            return self._send(200, {"ok": True, "model": getattr(_brain().gateway, "name", "?")})
        if path == "/api/engram/auth/test":
            auth_error = self._auth_error()
            if auth_error:
                status, message = auth_error
                obj = _response("", "auth.test", warnings=[message])
                _log_exchange(path, "", {}, obj, status)
                return self._send(status, obj)
            obj = _response("", "auth.test", result={"ok": True})
            _log_exchange(path, "", {}, obj, 200)
            return self._send(200, obj)
        if path == "/conversations" or path.startswith("/conversations/"):
            status, obj = handle_conversation_get(path, self.headers)
            _log_exchange(path, "", {}, obj, status)
            return self._send(status, obj)
        if path == "/memories" or path == "/memories/pending":
            status, obj = handle_memory_get(path, self.headers)
            _log_exchange(path, "", {}, obj, status)
            return self._send(status, obj)
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/conversations" or (path.startswith("/conversations/") and path.endswith("/messages")):
            if _rate_limited(self._client_id()):
                obj = {"error": "rate limit exceeded"}
                _log_exchange(path, "", {}, obj, 429)
                return self._send(429, obj)
            try:
                data = _read_json(self)
            except Exception as exc:
                obj = {"error": f"bad json: {exc}"}
                _log_exchange(path, "", {}, obj, 400)
                return self._send(400, obj)
            status, obj = handle_conversation_post(path, data, self.headers)
            _log_exchange(path, "", data, obj, status)
            return self._send(status, obj)
        if path.startswith("/memories/pending/"):
            if _rate_limited(self._client_id()):
                obj = {"error": "rate limit exceeded"}
                _log_exchange(path, "", {}, obj, 429)
                return self._send(429, obj)
            try:
                data = _read_json(self)
            except Exception as exc:
                obj = {"error": f"bad json: {exc}"}
                _log_exchange(path, "", {}, obj, 400)
                return self._send(400, obj)
            status, obj = handle_memory_post(path, data, self.headers)
            _log_exchange(path, "", data, obj, status)
            return self._send(status, obj)
        if path not in _POST_ROUTES:
            return self._send(404, {"error": "not found"})
        auth_error = self._auth_error()
        if auth_error:
            status, message = auth_error
            obj = _response("", "auth", warnings=[message])
            _log_exchange(path, "", {}, obj, status)
            return self._send(status, obj)
        if _rate_limited(self._client_id()):
            obj = _response("", "rate_limit", warnings=["rate limit exceeded"])
            _log_exchange(path, "", {}, obj, 429)
            return self._send(429, obj)
        try:
            data = _read_json(self)
        except Exception as exc:
            obj = _response("", "bad_json", warnings=[f"bad json: {exc}"])
            _log_exchange(path, "", {}, obj, 400)
            return self._send(400, obj)
        try:
            status, obj = _POST_ROUTES[path](data)
        except Exception as exc:
            status, obj = 503, _response(
                str(data.get("workspaceId") or ""),
                str(data.get("intent") or path),
                warnings=["backend unavailable"],
                result={"errorType": type(exc).__name__},
            )
        _log_exchange(path, obj.get("workspaceId", ""), data, obj, status)
        return self._send(status, obj)

    def do_PATCH(self):
        path = urlparse(self.path).path
        if path.startswith("/conversations/"):
            if _rate_limited(self._client_id()):
                obj = {"error": "rate limit exceeded"}
                _log_exchange(path, "", {}, obj, 429)
                return self._send(429, obj)
            try:
                data = _read_json(self)
            except Exception as exc:
                obj = {"error": f"bad json: {exc}"}
                _log_exchange(path, "", {}, obj, 400)
                return self._send(400, obj)
            status, obj = handle_conversation_patch(path, data, self.headers)
            _log_exchange(path, "", data, obj, status)
            return self._send(status, obj)
        return self._send(404, {"error": "not found"})

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/conversations/"):
            status, obj = handle_conversation_delete(path, self.headers)
            _log_exchange(path, "", {}, obj, status)
            return self._send(status, obj)
        if path.startswith("/memories/") and path != "/memories/pending":
            status, obj = handle_memory_delete(path, self.headers)
            _log_exchange(path, "", {}, obj, status)
            return self._send(status, obj)
        return self._send(404, {"error": "not found"})


def serve(host: str = "127.0.0.1", port: int | None = None) -> None:
    port = port or int(os.environ.get("ENGRAM_ACTIONS_PORT") or os.environ.get("ENGRAM_HTTP_PORT", "8088"))
    _brain()
    srv = ThreadingHTTPServer((host, port), Handler)
    auth = "bearer token required" if os.environ.get("ENGRAM_API_TOKEN", "").strip() else "OPEN (set ENGRAM_API_TOKEN)"
    print(f"Prepende Actions API on http://{host}:{port}")
    print("  GET/POST /conversations · GET/PATCH/DELETE /conversations/{id} · GET/POST /conversations/{id}/messages")
    print("  GET /memories/pending · POST /memories/pending/{id}/approve · POST /memories/pending/{id}/reject")
    print("  GET /api/engram/health · /api/engram/auth/test")
    print("  POST /api/engram/orchestrate · /api/engram/memory/context · /api/engram/memory/search")
    print("  POST /api/engram/memory/write · /api/engram/memory/update · /api/engram/memory/delete")
    print("  POST /api/engram/workflows/plan · /api/engram/approvals/create")
    print("  POST /api/engram/journal/write · /api/engram/entities/extract · /api/engram/people/upsert")
    print("  POST /api/engram/projects/* · /api/engram/knowledge/* · /api/engram/research-agents/*")
    print(f"  workspaceId required · auth: {auth}")
    srv.serve_forever()


if __name__ == "__main__":
    serve()
