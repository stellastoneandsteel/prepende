"""Small declarative evaluator registry for hash-pinned evidence documents."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Callable
import unicodedata

from .canonical import canonical_bytes, decimal_string, digest, require_digest


class EvaluationError(ValueError):
    """Raised when an evaluator or evidence bundle is invalid."""


def _text(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or ""))


def _field(document: Any, path: str) -> Any:
    current = document
    if not path or any(part == "" for part in path.split(".")):
        raise EvaluationError("evaluator field must be a non-empty dot path")
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise EvaluationError(f"evidence field not found: {path}")
        current = current[part]
    return current


def _number(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise EvaluationError("boolean is not numeric evidence")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise EvaluationError("evidence value is not a decimal number") from exc
    if not number.is_finite():
        raise EvaluationError("evidence number must be finite")
    return number


def validate_evaluator(kind: str, evaluator: Any) -> dict[str, Any]:
    if not isinstance(evaluator, dict):
        raise EvaluationError("evaluator must be an object")
    evaluator_type = _text(evaluator.get("type"))
    version = _text(evaluator.get("version"))
    params = evaluator.get("parameters")
    if version != "1" or not isinstance(params, dict):
        raise EvaluationError("evaluator version must be '1' and parameters must be an object")
    allowed = {
        "probability": {"binary_value", "binary_threshold"},
        "numeric": {"numeric_field"},
        "categorical": {"categorical_field"},
    }
    if evaluator_type not in allowed.get(kind, set()):
        raise EvaluationError(f"evaluator {evaluator_type!r} is not valid for kind {kind!r}")
    field = _text(params.get("field"))
    evidence_name = _text(params.get("evidence"))
    if not field or not evidence_name:
        raise EvaluationError("evaluator parameters.field and parameters.evidence are required")
    normalized: dict[str, Any] = {
        "type": evaluator_type,
        "version": "1",
        "parameters": {"evidence": evidence_name, "field": field},
    }
    if evaluator_type == "binary_threshold":
        operator = _text(params.get("operator"))
        if operator not in {">", ">=", "<", "<=", "=="}:
            raise EvaluationError("binary_threshold operator must be >, >=, <, <=, or ==")
        normalized["parameters"].update({
            "operator": operator,
            "threshold": decimal_string(params.get("threshold")),
        })
    return normalized


def normalize_evidence(evidence: Any) -> list[dict[str, Any]]:
    if not isinstance(evidence, list) or not evidence:
        raise EvaluationError("at least one inline evidence document is required")
    names: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for item in evidence:
        if not isinstance(item, dict):
            raise EvaluationError("evidence entries must be objects")
        name = _text(item.get("name"))
        uri = _text(item.get("uri"))
        if not name or name in names or not uri:
            raise EvaluationError("evidence names must be non-empty and unique; uri is required")
        if "content" not in item:
            raise EvaluationError("inline evidence content is required for deterministic verification")
        content = item["content"]
        canonical_bytes(content)
        expected = digest("evidence", content)
        supplied = item.get("digest", expected)
        require_digest(supplied, "evidence digest")
        if supplied != expected:
            raise EvaluationError(f"evidence digest mismatch for {name}")
        names.add(name)
        normalized.append({"name": name, "uri": uri, "digest": expected, "content": content})
    return normalized


def require_evaluation_artifacts(evaluation: dict[str, Any],
                                 evaluator: dict[str, Any],
                                 evidence: list[dict[str, Any]]) -> None:
    """Require each hash-pinned evaluation artifact in the resolution bundle.

    Artifact roles are stable evidence names. This keeps dataset, harness, and
    environment digests from becoming decorative contract metadata.
    """
    by_name = {item["name"]: item for item in evidence}
    roles = {artifact["role"] for artifact in evaluation.get("artifacts", [])}
    selected = validate_evaluator_for_evidence(evaluator)
    if roles and selected not in roles:
        raise EvaluationError("the evaluator-selected evidence is not hash-pinned")
    for artifact in evaluation.get("artifacts", []):
        item = by_name.get(artifact["role"])
        if item is None:
            raise EvaluationError(
                f"required evaluation artifact is absent from evidence: {artifact['role']}"
            )
        if item["uri"] != artifact["uri"] or item["digest"] != artifact["digest"]:
            raise EvaluationError(
                f"evaluation artifact binding mismatch: {artifact['role']}"
            )


def validate_evaluator_for_evidence(evaluator: dict[str, Any]) -> str:
    parameters = evaluator.get("parameters") if isinstance(evaluator, dict) else None
    selected = _text(parameters.get("evidence")) if isinstance(parameters, dict) else ""
    if not selected:
        raise EvaluationError("evaluator parameters.evidence is required")
    return selected


def evaluate(kind: str, evaluator: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    evaluator = validate_evaluator(kind, evaluator)
    evidence = normalize_evidence(evidence)
    params = evaluator["parameters"]
    selected = next((item for item in evidence if item["name"] == params["evidence"]), None)
    if selected is None:
        raise EvaluationError(f"evaluator evidence not found: {params['evidence']}")
    document = selected["content"]
    value = _field(document, params["field"])
    evaluator_type = evaluator["type"]
    if evaluator_type == "binary_value":
        if isinstance(value, bool):
            y = int(value)
        elif value in (0, 1, "0", "1"):
            y = int(value)
        else:
            raise EvaluationError("binary_value evidence must be 0, 1, true, or false")
        return {"y": y}
    if evaluator_type == "binary_threshold":
        observed = _number(value)
        threshold = Decimal(params["threshold"])
        operations: dict[str, Callable[[Decimal, Decimal], bool]] = {
            ">": lambda a, b: a > b,
            ">=": lambda a, b: a >= b,
            "<": lambda a, b: a < b,
            "<=": lambda a, b: a <= b,
            "==": lambda a, b: a == b,
        }
        return {"y": int(operations[params["operator"]](observed, threshold))}
    if evaluator_type == "numeric_field":
        return {"value": decimal_string(value)}
    if evaluator_type == "categorical_field":
        if not isinstance(value, str):
            raise EvaluationError("categorical evidence label must be a string")
        label = unicodedata.normalize("NFC", value)
        if not label:
            raise EvaluationError("categorical evidence label must not be empty")
        return {"label": label}
    raise EvaluationError(f"unsupported evaluator: {evaluator_type}")
