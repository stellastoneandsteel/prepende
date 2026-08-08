"""Read-compatible implementation of the immutable Prepende v1 ledger format.

New predictions must use :class:`prepende.Ledger`. This module exists so the public
v1 corpus remains inspectable without pretending it acquired v2 guarantees.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


_CANON = ["predictor", "question", "kind", "claim", "resolution_rule", "eval_regime", "created_at"]


@dataclass
class LegacyContract:
    predictor: str
    question: str
    kind: str
    claim: Dict[str, Any]
    resolution_rule: str
    eval_regime: str
    created_at: float
    cid: str = ""
    lock_hash: str = ""

    def _canonical(self) -> str:
        return json.dumps({k: getattr(self, k) for k in _CANON}, sort_keys=True, separators=(",", ":"))

    def compute_hash(self) -> str:
        return hashlib.sha256(self._canonical().encode("utf-8")).hexdigest()

    def lock(self) -> "LegacyContract":
        self.lock_hash = self.compute_hash()
        self.cid = self.lock_hash[:12]
        return self

    def verify(self) -> bool:
        return bool(self.lock_hash) and self.compute_hash() == self.lock_hash

    def to_row(self) -> Dict[str, Any]:
        row = {k: getattr(self, k) for k in _CANON}
        row.update({"type": "contract", "cid": self.cid, "lock_hash": self.lock_hash})
        return row

    @staticmethod
    def from_row(row: Dict[str, Any]) -> "LegacyContract":
        return LegacyContract(
            predictor=row["predictor"], question=row["question"], kind=row["kind"],
            claim=row["claim"], resolution_rule=row["resolution_rule"],
            eval_regime=row["eval_regime"], created_at=row["created_at"],
            cid=row.get("cid", ""), lock_hash=row.get("lock_hash", ""),
        )


@dataclass
class LegacyResolution:
    cid: str
    lock_hash: str
    outcome: Dict[str, Any]
    eval_regime: str
    resolved_at: float
    note: str = ""

    @staticmethod
    def from_row(row: Dict[str, Any]) -> "LegacyResolution":
        return LegacyResolution(
            cid=row["cid"], lock_hash=row["lock_hash"], outcome=row["outcome"],
            eval_regime=row["eval_regime"], resolved_at=row["resolved_at"], note=row.get("note", ""),
        )


def legacy_lock_prediction(predictor: str, question: str, kind: str, claim: Dict[str, Any],
                           resolution_rule: str, eval_regime: str,
                           created_at: float | None = None) -> LegacyContract:
    if created_at is None:
        created_at = time.time()
    return LegacyContract(
        predictor, question, kind, claim, resolution_rule, eval_regime, created_at
    ).lock()


class LegacyLedger:
    """Read-only v1 reader for the corpus frozen at the Protocol v2 cutover."""

    def __init__(self, path: str):
        self.path = path

    def _rows(self) -> List[Dict[str, Any]]:
        with open(self.path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def records(self) -> List[Tuple[LegacyContract, Optional[LegacyResolution]]]:
        rows = self._rows()
        resolutions = {
            row["cid"]: LegacyResolution.from_row(row)
            for row in rows if row.get("type") == "resolution"
        }
        contracts = [LegacyContract.from_row(row) for row in rows if row.get("type") == "contract"]
        return [(contract, resolutions.get(contract.cid)) for contract in contracts]

    def integrity(self) -> dict[str, Any]:
        bad = [
            contract.cid or "<unknown>"
            for contract, _ in self.records()
            if not contract.verify()
        ]
        return {
            "protocol": "prepende/1",
            "status": "TAMPERED" if bad else "UNANCHORED",
            "internally_valid": not bad,
            "anchored": False,
            "complete_through": None,
            "independently_resolved": False,
            "errors": [f"contract hash mismatch: {cid}" for cid in bad],
            "warnings": [
                "legacy v1 does not hash resolutions or commit row order/completeness"
            ],
        }

    def lock(self, contract: LegacyContract) -> LegacyContract:
        raise RuntimeError(
            "the public v1 stream is frozen for new contracts; create a Protocol v2 stream"
        )

    def resolve(self, cid: str, outcome: Dict[str, Any], eval_regime: str,
                note: str = "") -> LegacyResolution:
        raise RuntimeError(
            "the public v1 stream is frozen at the v2 import; record future work in Protocol v2"
        )
