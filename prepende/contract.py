"""The locked prediction contract.

A Contract hashes the fields that must be fixed at prediction time — including the
resolution_rule and eval_regime — so the terms are tamper-evident. Resolutions are
separate, append-only records that must reference the contract's hash and match its
locked regime.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Dict

# Fields LOCKED at prediction time. resolution_rule + eval_regime are included on
# purpose: the anti-retrofit guarantee is that you cannot change how/where it is scored.
_CANON = ["predictor", "question", "kind", "claim", "resolution_rule", "eval_regime", "created_at"]


@dataclass
class Contract:
    predictor: str            # who/what made it, e.g. "gpt-x" or "human:alice"
    question: str             # the question being forecast
    kind: str                 # "probability" | "numeric" | "categorical"
    claim: Dict[str, Any]     # the locked prediction (e.g. {"p":0.7})
    resolution_rule: str      # exactly how this resolves to an outcome
    eval_regime: str          # the fixed conditions under which it will be scored
    created_at: float         # unix ts at lock time
    cid: str = ""
    lock_hash: str = ""

    def _canonical(self) -> str:
        return json.dumps({k: getattr(self, k) for k in _CANON}, sort_keys=True, separators=(",", ":"))

    def compute_hash(self) -> str:
        return hashlib.sha256(self._canonical().encode("utf-8")).hexdigest()

    def lock(self) -> "Contract":
        self.lock_hash = self.compute_hash()
        self.cid = self.lock_hash[:12]
        return self

    def verify(self) -> bool:
        """True iff the content still matches the hash committed at lock time."""
        return bool(self.lock_hash) and self.compute_hash() == self.lock_hash

    def to_row(self) -> Dict[str, Any]:
        row = {k: getattr(self, k) for k in _CANON}
        row.update({"type": "contract", "cid": self.cid, "lock_hash": self.lock_hash})
        return row

    @staticmethod
    def from_row(row: Dict[str, Any]) -> "Contract":
        return Contract(
            predictor=row["predictor"], question=row["question"], kind=row["kind"],
            claim=row["claim"], resolution_rule=row["resolution_rule"],
            eval_regime=row["eval_regime"], created_at=row["created_at"],
            cid=row.get("cid", ""), lock_hash=row.get("lock_hash", ""),
        )


@dataclass
class Resolution:
    cid: str
    lock_hash: str
    outcome: Dict[str, Any]
    eval_regime: str
    resolved_at: float
    note: str = ""

    def to_row(self) -> Dict[str, Any]:
        row = dict(self.__dict__)
        row["type"] = "resolution"
        return row

    @staticmethod
    def from_row(row: Dict[str, Any]) -> "Resolution":
        return Resolution(
            cid=row["cid"], lock_hash=row["lock_hash"], outcome=row["outcome"],
            eval_regime=row["eval_regime"], resolved_at=row["resolved_at"], note=row.get("note", ""),
        )


def lock_prediction(predictor: str, question: str, kind: str, claim: Dict[str, Any],
                    resolution_rule: str, eval_regime: str, created_at: float = None) -> Contract:
    """Create and lock a prediction in one call."""
    if created_at is None:
        created_at = time.time()
    return Contract(predictor, question, kind, claim, resolution_rule, eval_regime, created_at).lock()
