"""Local proof ledger for operator work performed through Prepende.

The ledger answers a narrow question: did an operator actually use Prepende for
this task, under which scope and execution lane, and what candidate-only
learning was staged afterward?  Receipts live under gitignored ``.engram/`` and
do not grant execution or durable-memory authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "prepende-operator-receipt-v1"
TERMINAL_STATES = {"succeeded", "blocked", "failed"}
LANES = {"direct", "sandbox"}
_SCOPE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_EXTERNAL_ACTION_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,79}$")


def _iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc).isoformat().replace("+00:00", "Z")


def _bounded(value: Any, label: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise ValueError(f"{label} is required and must be at most {maximum} characters")
    return text


def _scope(value: Any, label: str) -> str:
    text = _bounded(value, label, 64)
    if not _SCOPE_RE.fullmatch(text):
        raise ValueError(f"{label} must be a lowercase tenant slug")
    return text


def _string_list(values: list[str] | None, label: str, *, maximum_items: int = 32) -> list[str]:
    items = [_bounded(item, label, 500) for item in (values or [])]
    if len(items) > maximum_items:
        raise ValueError(f"{label} accepts at most {maximum_items} entries")
    return items


def normalize_external_actions(values: list[str] | None) -> list[str]:
    actions = [_bounded(item, "external action", 80) for item in (values or [])]
    if len(actions) > 32:
        raise ValueError("external action accepts at most 32 entries")
    if any(not _EXTERNAL_ACTION_RE.fullmatch(action) for action in actions):
        raise ValueError("external action must be a stable lowercase identifier")
    if len(actions) != len(set(actions)):
        raise ValueError("external actions must be unique")
    return actions


def validate_operator_finish(
    *,
    status: str,
    outcome: str,
    evidence: list[str] | None,
    checks: list[str] | None,
    external_actions: list[str] | None,
) -> dict[str, Any]:
    terminal_status = _bounded(status, "status", 20)
    if terminal_status not in TERMINAL_STATES:
        raise ValueError("status must be succeeded, blocked, or failed")
    evidence_items = _string_list(evidence, "evidence")
    executed_actions = normalize_external_actions(external_actions)
    if executed_actions and not evidence_items:
        raise ValueError("external actions require at least one evidence entry")
    return {
        "status": terminal_status,
        "outcome": _bounded(outcome, "outcome", 4000),
        "evidence": evidence_items,
        "checks": _string_list(checks, "check"),
        "externalActions": executed_actions,
        "actionExecuted": bool(executed_actions),
    }


class OperatorReceiptStore:
    def __init__(self, root: str | Path = "./.engram/operator-receipts") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root.parent / "operator-usage.jsonl"

    def _path(self, receipt_id: str) -> Path:
        clean = _bounded(receipt_id, "receipt id", 100)
        if not re.fullmatch(r"op_[a-z0-9]+", clean):
            raise ValueError("invalid operator receipt id")
        return self.root / f"{clean}.json"

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp-{os.getpid()}")
        tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def _event(self, receipt: dict[str, Any], event: str) -> None:
        item = {
            "schemaVersion": SCHEMA_VERSION,
            "event": event,
            "receiptId": receipt["receiptId"],
            "scope": receipt["scope"],
            "workspace": receipt["workspace"],
            "lane": receipt["lane"],
            "status": receipt["status"],
            "at": _iso(),
        }
        with self.index_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, sort_keys=True) + "\n")

    def start(
        self,
        *,
        goal: str,
        scope: str,
        workspace: str,
        lane: str,
        operator: str,
        preflight: dict[str, Any],
        cwd: str,
    ) -> dict[str, Any]:
        goal = _bounded(goal, "goal", 4000)
        scope = _scope(scope, "scope")
        workspace = _scope(workspace, "workspace")
        lane = _bounded(lane, "lane", 20)
        if lane not in LANES:
            raise ValueError("lane must be direct or sandbox")
        operator = _bounded(operator, "operator", 80)
        preflight_json = json.dumps(preflight, sort_keys=True, separators=(",", ":"), default=str)
        receipt_id = "op_" + uuid.uuid4().hex[:20]
        now = _iso()
        receipt = {
            "schemaVersion": SCHEMA_VERSION,
            "receiptId": receipt_id,
            "goal": goal,
            "goalHash": "sha256:" + hashlib.sha256(goal.encode("utf-8")).hexdigest(),
            "scope": scope,
            "workspace": workspace,
            "lane": lane,
            "operator": operator,
            "cwd": str(Path(cwd).resolve()),
            "status": "started",
            "startedAt": now,
            "completedAt": None,
            "preflight": {
                "command": "context-fast",
                "ok": bool(preflight.get("ok")),
                "scope": preflight.get("scope"),
                "receiptDigest": "sha256:" + hashlib.sha256(preflight_json.encode("utf-8")).hexdigest(),
                "externalActions": preflight.get("externalActions", []),
                "actionExecuted": bool(preflight.get("actionExecuted")),
            },
            "sandbox": None,
            "outcome": None,
            "evidence": [],
            "checks": [],
            "learning": {
                "candidateId": None,
                "status": "not_staged",
                "durableMemoryWrite": False,
                "promotionRequired": True,
            },
            "externalActions": [],
            "actionExecuted": False,
        }
        path = self._path(receipt_id)
        self._write_json(path, receipt)
        self._event(receipt, "started")
        return {**receipt, "receiptPath": str(path.resolve())}

    def get(self, receipt_id: str) -> dict[str, Any] | None:
        path = self._path(receipt_id)
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return {**value, "receiptPath": str(path.resolve())}

    def update_sandbox(self, receipt_id: str, sandbox: dict[str, Any]) -> dict[str, Any]:
        current = self.get(receipt_id)
        if current is None:
            raise ValueError("operator receipt not found")
        current.pop("receiptPath", None)
        current["sandbox"] = sandbox
        self._write_json(self._path(receipt_id), current)
        self._event(current, "sandbox_updated")
        return {**current, "receiptPath": str(self._path(receipt_id).resolve())}

    def finish(
        self,
        receipt_id: str,
        *,
        status: str,
        outcome: str,
        evidence: list[str] | None,
        checks: list[str] | None,
        learning: dict[str, Any],
        external_actions: list[str] | None = None,
    ) -> dict[str, Any]:
        current = self.get(receipt_id)
        if current is None:
            raise ValueError("operator receipt not found")
        current.pop("receiptPath", None)
        if current.get("status") in TERMINAL_STATES:
            return {**current, "receiptPath": str(self._path(receipt_id).resolve())}
        terminal = validate_operator_finish(
            status=status,
            outcome=outcome,
            evidence=evidence,
            checks=checks,
            external_actions=external_actions,
        )
        current.update({
            **terminal,
            "completedAt": _iso(),
            "learning": learning,
        })
        self._write_json(self._path(receipt_id), current)
        self._event(current, "finished")
        return {**current, "receiptPath": str(self._path(receipt_id).resolve())}

    def latest(self, limit: int = 10) -> list[dict[str, Any]]:
        files = sorted(
            (path for path in self.root.glob("op_*.json") if not path.name.endswith("-sandbox-output.json")),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        out = []
        for path in files[: max(1, min(int(limit), 50))]:
            value = json.loads(path.read_text(encoding="utf-8"))
            out.append({**value, "receiptPath": str(path.resolve())})
        return out
