"""Append-only ledger of locked predictions and their resolutions.

Stored as JSONL so it can be committed to git — which gives an external, tamper-evident
timestamp that the lock preceded the outcome (the strongest cheap credibility anchor).
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from .contract import Contract, Resolution


class RetrofitError(Exception):
    """Raised when a resolution tries to change the locked terms (goalpost-moving)."""


class Ledger:
    def __init__(self, path: str):
        self.path = path
        if not os.path.exists(path):
            open(path, "a").close()

    def _append(self, row: Dict[str, Any]) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    def _rows(self) -> List[Dict[str, Any]]:
        rows = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    def lock(self, contract: Contract) -> Contract:
        if not contract.verify():
            raise ValueError("contract must be locked and verified before it is appended")
        self._append(contract.to_row())
        return contract

    def _contract(self, cid: str) -> Optional[Contract]:
        for r in self._rows():
            if r.get("type") == "contract" and r.get("cid") == cid:
                return Contract.from_row(r)
        return None

    def _resolution(self, cid: str) -> Optional[Resolution]:
        for r in self._rows():
            if r.get("type") == "resolution" and r.get("cid") == cid:
                return Resolution.from_row(r)
        return None

    def resolve(self, cid: str, outcome: Dict[str, Any], eval_regime: str,
                note: str = "", resolved_at: float = None) -> Resolution:
        c = self._contract(cid)
        if c is None:
            raise ValueError("no such contract: " + cid)
        if not c.verify():
            raise RetrofitError("locked contract " + cid + " has been tampered with (hash mismatch)")
        if self._resolution(cid) is not None:
            raise RetrofitError("contract " + cid + " is already resolved; resolutions are immutable")
        if eval_regime.strip() != c.eval_regime.strip():
            raise RetrofitError(
                "eval regime at resolution does not match the locked regime — goalpost-moving refused."
                "\n  locked:   " + c.eval_regime + "\n  provided: " + eval_regime)
        if resolved_at is None:
            resolved_at = time.time()
        res = Resolution(cid=cid, lock_hash=c.lock_hash, outcome=outcome,
                         eval_regime=eval_regime, resolved_at=resolved_at, note=note)
        self._append(res.to_row())
        return res

    def records(self) -> List[Tuple[Contract, Optional[Resolution]]]:
        contracts = [Contract.from_row(r) for r in self._rows() if r.get("type") == "contract"]
        return [(c, self._resolution(c.cid)) for c in contracts]

    def integrity(self) -> List[str]:
        """Return cids whose content no longer matches their locked hash (tamper-evidence)."""
        bad = []
        for r in self._rows():
            if r.get("type") == "contract":
                c = Contract.from_row(r)
                if not c.verify():
                    bad.append(c.cid or "<unknown>")
        return bad
