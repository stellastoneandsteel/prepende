"""Prepende Protocol v2 contracts and terminal records."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import re
import unicodedata

from .canonical import PROTOCOL, canonical_bytes, decimal_string, digest, require_digest
from .evaluators import validate_evaluator


KINDS = {"probability", "numeric", "categorical"}
PROVENANCE = {"forward", "retrospective"}
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)


class ContractValidationError(ValueError):
    """Raised when a contract fails the protocol schema."""


def timestamp_now(clock) -> str:
    return datetime.fromtimestamp(float(clock()), timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP_RE.fullmatch(value):
        raise ContractValidationError(
            f"{label} must be an RFC3339 UTC timestamp in YYYY-MM-DDTHH:MM:SS[.ffffff]Z form"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractValidationError(f"{label} must be an RFC3339 UTC timestamp") from exc
    if parsed.tzinfo is None:
        raise ContractValidationError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _text(value: Any, label: str, maximum: int = 4096) -> str:
    text = unicodedata.normalize("NFC", str(value or "").strip())
    if not text or len(text) > maximum:
        raise ContractValidationError(f"{label} is required and must be at most {maximum} characters")
    return text


def normalize_claim(kind: str, claim: Any) -> dict[str, Any]:
    if kind not in KINDS or not isinstance(claim, dict):
        raise ContractValidationError("kind must be probability, numeric, or categorical; claim must be an object")
    if kind == "probability":
        return {"p": decimal_string(claim.get("p"), minimum=Decimal("0"), maximum=Decimal("1"))}
    if kind == "numeric":
        value = decimal_string(claim.get("value"))
        lo = decimal_string(claim.get("lo"))
        hi = decimal_string(claim.get("hi"))
        if Decimal(lo) > Decimal(value) or Decimal(value) > Decimal(hi):
            raise ContractValidationError("numeric claim must satisfy lo <= value <= hi")
        return {"value": value, "lo": lo, "hi": hi, "unit": _text(claim.get("unit"), "claim.unit", 80)}
    return {
        "label": _text(claim.get("label"), "claim.label", 500),
        "p": decimal_string(claim.get("p"), minimum=Decimal("0"), maximum=Decimal("1")),
    }


def normalize_evaluation(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractValidationError("evaluation must be an object")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list):
        raise ContractValidationError("evaluation.artifacts must be a list")
    normalized_artifacts: list[dict[str, str]] = []
    roles: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict):
            raise ContractValidationError("evaluation artifacts must be objects")
        role = _text(item.get("role"), "evaluation artifact role", 80)
        if role in roles:
            raise ContractValidationError("evaluation artifact roles must be unique")
        roles.add(role)
        normalized_artifacts.append({
            "role": role,
            "uri": _text(item.get("uri"), "evaluation artifact uri", 1000),
            "digest": require_digest(item.get("digest"), "evaluation artifact digest"),
        })
    return {
        "id": _text(value.get("id"), "evaluation.id", 200),
        "spec_digest": require_digest(value.get("spec_digest"), "evaluation.spec_digest"),
        "artifacts": normalized_artifacts,
    }


def normalize_resolver_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractValidationError("resolver_policy must be an object")
    mode = str(value.get("mode") or "")
    keys = value.get("authorized_key_ids", [])
    if mode not in {"self", "signed"} or not isinstance(keys, list):
        raise ContractValidationError("resolver policy mode must be self or signed")
    normalized = [_text(item, "resolver key id", 200) for item in keys]
    if len(normalized) != len(set(normalized)):
        raise ContractValidationError("resolver key ids must be unique")
    if mode == "signed" and not normalized:
        raise ContractValidationError("signed resolver policy requires authorized_key_ids")
    if mode == "self" and normalized:
        raise ContractValidationError("self resolver policy cannot authorize signing keys")
    return {"mode": mode, "authorized_key_ids": normalized}


def normalize_nonresolution_policy(kind: str, value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or value.get("action") != "forfeit":
        raise ContractValidationError("nonresolution_policy.action must be forfeit")
    expected = "absolute_error" if kind == "numeric" else "brier"
    metric = str(value.get("metric") or expected)
    if metric != expected:
        raise ContractValidationError(f"nonresolution metric for {kind} must be {expected}")
    penalty = decimal_string(value.get("penalty", "1"), minimum=Decimal("0"))
    return {"action": "forfeit", "metric": metric, "penalty": penalty}


def normalize_void_policy(value: Any) -> dict[str, list[str]]:
    if value is None:
        return {"allowed_reason_codes": []}
    if not isinstance(value, dict) or not isinstance(value.get("allowed_reason_codes", []), list):
        raise ContractValidationError("void_policy.allowed_reason_codes must be a list")
    reasons = [_text(item, "void reason code", 80) for item in value.get("allowed_reason_codes", [])]
    if len(reasons) != len(set(reasons)):
        raise ContractValidationError("void reason codes must be unique")
    return {"allowed_reason_codes": reasons}


@dataclass(frozen=True)
class Contract:
    contract_id: str
    stream_id: str
    predictor: str
    model_version: str
    domain: str
    event_id: str
    question: str
    kind: str
    claim: dict[str, Any]
    resolution_rule: str
    evaluator: dict[str, Any]
    evaluation: dict[str, Any]
    issued_at: str
    resolution_due_at: str
    resolver_policy: dict[str, Any]
    nonresolution_policy: dict[str, Any]
    void_policy: dict[str, Any]
    provenance: str

    @property
    def cid(self) -> str:
        return self.contract_id

    @property
    def short_id(self) -> str:
        return self.contract_id.removeprefix("sha256:")[:16]

    @property
    def eval_regime(self) -> str:
        return self.evaluation["id"]

    @property
    def created_at(self) -> float:
        return parse_timestamp(self.issued_at, "issued_at").timestamp()

    @staticmethod
    def from_event(event: dict[str, Any]) -> "Contract":
        return Contract(**{field: event[field] for field in Contract.__dataclass_fields__})

    def body(self) -> dict[str, Any]:
        return {
            field: getattr(self, field)
            for field in Contract.__dataclass_fields__
            if field != "contract_id"
        }

    def verify(self) -> bool:
        return self.contract_id == digest("contract", self.body())


@dataclass(frozen=True)
class Resolution:
    resolution_id: str
    stream_id: str
    contract_id: str
    outcome: dict[str, Any]
    evidence: list[dict[str, Any]]
    resolved_at: str
    resolver_key_id: str | None
    signature: str | None
    note: str

    @property
    def cid(self) -> str:
        return self.contract_id

    @property
    def resolved_at_unix(self) -> float:
        return parse_timestamp(self.resolved_at, "resolved_at").timestamp()

    def statement(self) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL,
            "stream_id": self.stream_id,
            "contract_id": self.contract_id,
            "outcome": self.outcome,
            "evidence": self.evidence,
            "resolved_at": self.resolved_at,
            "resolver_key_id": self.resolver_key_id,
            "note": self.note,
        }

    def verify_id(self) -> bool:
        return self.resolution_id == digest("resolution", self.statement())

    @staticmethod
    def from_event(event: dict[str, Any]) -> "Resolution":
        return Resolution(**{field: event.get(field) for field in Resolution.__dataclass_fields__})


@dataclass(frozen=True)
class Disposition:
    disposition_id: str
    stream_id: str
    contract_id: str
    disposition: str
    at: str
    reason_code: str
    note: str
    resolver_key_id: str | None = None
    signature: str | None = None

    @property
    def cid(self) -> str:
        return self.contract_id

    @property
    def outcome(self) -> None:
        return None

    def statement(self) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL,
            "stream_id": self.stream_id,
            "contract_id": self.contract_id,
            "disposition": self.disposition,
            "at": self.at,
            "reason_code": self.reason_code,
            "note": self.note,
            "resolver_key_id": self.resolver_key_id,
        }

    def verify_id(self) -> bool:
        return self.disposition_id == digest(self.disposition, self.statement())

    @staticmethod
    def from_event(event: dict[str, Any]) -> "Disposition":
        return Disposition(**{field: event.get(field) for field in Disposition.__dataclass_fields__})


def build_contract(*, stream_id: Any, predictor: Any, model_version: Any, domain: Any, event_id: Any,
                   question: Any, kind: str, claim: Any, resolution_rule: Any,
                   evaluator: Any, evaluation: Any, issued_at: str,
                   resolution_due_at: Any, resolver_policy: Any,
                   nonresolution_policy: Any, void_policy: Any = None,
                   provenance: str = "forward") -> Contract:
    if kind not in KINDS:
        raise ContractValidationError("kind must be probability, numeric, or categorical")
    if provenance not in PROVENANCE:
        raise ContractValidationError("provenance must be forward or retrospective")
    issued = parse_timestamp(issued_at, "issued_at")
    due_text = str(resolution_due_at or "")
    due = parse_timestamp(due_text, "resolution_due_at")
    if due <= issued:
        raise ContractValidationError("resolution_due_at must be after issued_at")
    body = {
        "stream_id": _text(stream_id, "stream_id", 200),
        "predictor": _text(predictor, "predictor", 200),
        "model_version": _text(model_version, "model_version", 200),
        "domain": _text(domain, "domain", 200),
        "event_id": _text(event_id, "event_id", 500),
        "question": _text(question, "question", 8000),
        "kind": kind,
        "claim": normalize_claim(kind, claim),
        "resolution_rule": _text(resolution_rule, "resolution_rule", 8000),
        "evaluator": validate_evaluator(kind, evaluator),
        "evaluation": normalize_evaluation(evaluation),
        "issued_at": issued_at,
        "resolution_due_at": due_text,
        "resolver_policy": normalize_resolver_policy(resolver_policy),
        "nonresolution_policy": normalize_nonresolution_policy(kind, nonresolution_policy),
        "void_policy": normalize_void_policy(void_policy),
        "provenance": provenance,
    }
    canonical_bytes(body)
    return Contract(contract_id=digest("contract", body), **body)
