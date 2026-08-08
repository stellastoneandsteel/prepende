from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from prepende import Ledger, build_anchor_statement
from prepende.contract import timestamp_now


SPEC_DIGEST = "sha256:" + "1" * 64


class Clock:
    def __init__(self, value: float = 1_700_000_000.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def new_ledger(directory: str | Path, *, clock: Clock | None = None,
               stream_id: str = "test-stream") -> tuple[Ledger, Clock, Path]:
    clock = clock or Clock()
    path = Path(directory) / "ledger-v2.jsonl"
    ledger = Ledger.create(
        path,
        stream_id=stream_id,
        registered_predictor="agent-test",
        _clock=clock,
    )
    return ledger, clock, path


def lock_probability(ledger: Ledger, *, event_id: str = "event-1", p: Any = "0.7",
                     due: str = "2023-11-16T00:00:00Z",
                     resolver_policy: dict[str, Any] | None = None,
                     void_reasons: list[str] | None = None):
    return ledger.lock_prediction(
        predictor="agent-test",
        model_version="model-v1",
        domain="unit-test",
        event_id=event_id,
        question=f"Will {event_id} occur?",
        kind="probability",
        claim={"p": p},
        resolution_rule="y=1 if evidence.result is true",
        evaluator={
            "type": "binary_value", "version": "1",
            "parameters": {"evidence": "result", "field": "result"},
        },
        evaluation={"id": "test-harness-v1", "spec_digest": SPEC_DIGEST, "artifacts": []},
        resolution_due_at=due,
        resolver_policy=resolver_policy or {"mode": "self", "authorized_key_ids": []},
        nonresolution_policy={"action": "forfeit", "metric": "brier", "penalty": "1"},
        void_policy={"allowed_reason_codes": void_reasons or []},
    )


def evidence(value: Any = True) -> list[dict[str, Any]]:
    return [{"name": "result", "uri": "urn:test:result", "content": {"result": value}}]


def keypair() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return base64.b64encode(private_raw).decode(), base64.b64encode(public_raw).decode()


def anchor(ledger: Ledger, *, key_id: str, private_key: str) -> dict[str, Any]:
    from prepende import sign_detached

    checkpoint = ledger.checkpoint()
    request = ledger.anchor_request(checkpoint["checkpoint_id"])
    statement = build_anchor_statement(
        request,
        key_id=key_id,
        anchored_at=timestamp_now(ledger._clock),
    )
    ledger.add_anchor(statement, sign_detached(statement, private_key))
    return statement
