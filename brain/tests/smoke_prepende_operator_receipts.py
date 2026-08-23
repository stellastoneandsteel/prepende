#!/usr/bin/env python3
"""Smoke the local Prepende operator proof ledger and candidate-only finish."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from scripts.prepende_operator import sandbox_message


def run(args: list[str], env: dict[str, str], expected_returncode: int = 0) -> dict:
    proc = subprocess.run(
        [str(ROOT / "bin" / "prepende"), "operator", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == expected_returncode, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


def main() -> None:
    message = sandbox_message("first line\nsecond line\r\nthird line")
    assert "\n" not in message and "\r" not in message
    assert "first line second line third line" in message

    with tempfile.TemporaryDirectory(prefix="prepende_operator_") as tmp:
        receipts = Path(tmp) / "receipts"
        candidates = Path(tmp) / "candidates.db"
        env = {
            **os.environ,
            "MODEL_PROVIDER": "echo",
            "MEMORY_BACKEND": "sqlite",
            "MEMORY_DB": str(Path(tmp) / "memory.db"),
            "RUNS_DB": str(Path(tmp) / "runs.db"),
            "WORKSPACE_ROOT": str(Path(tmp) / "workspaces"),
            "VAULT_PATH": str(Path(tmp) / "vault"),
            "PREPENDE_OPERATOR_CANDIDATES_DB": str(candidates),
        }
        started = run([
            "--receipts-dir", str(receipts),
            "start", "Verify operator proof",
            "--scope", "tenant-alpha",
            "--workspace", "tenant-alpha",
            "--operator", "smoke",
        ], env)
        assert started["status"] == "started"
        assert started["preflight"]["ok"] is True
        assert started["lane"] == "direct"
        assert started["learning"]["durableMemoryWrite"] is False

        finished = run([
            "--receipts-dir", str(receipts),
            "finish",
            "--receipt", started["receiptId"],
            "--status", "succeeded",
            "--outcome", "Operator receipt smoke passed.",
            "--learning", "Prepende operator receipts bind preflight, verification, and candidate-only learning.",
            "--evidence", "tests/smoke_prepende_operator_receipts.py",
            "--check", "smoke passed",
        ], env)
        assert finished["status"] == "succeeded"
        assert finished["learning"]["candidateId"].startswith("cand_")
        assert finished["learning"]["status"] == "pending_assessment"
        assert finished["learning"]["durableMemoryWrite"] is False
        assert finished["externalActions"] == []
        assert finished["actionExecuted"] is False
        assert candidates.exists()

        action_started = run([
            "--receipts-dir", str(receipts),
            "start", "Verify truthful external action proof",
            "--scope", "tenant-alpha",
            "--workspace", "tenant-alpha",
            "--operator", "smoke",
        ], env)
        action_finished = run([
            "--receipts-dir", str(receipts),
            "finish",
            "--receipt", action_started["receiptId"],
            "--status", "succeeded",
            "--outcome", "Operator action receipt smoke passed.",
            "--learning", "Executed external actions require explicit stable identifiers in the terminal receipt.",
            "--evidence", "release receipt with commit and deploy identifiers",
            "--external-action", "git_push",
            "--external-action", "pull_request_merge",
            "--external-action", "production_deploy",
        ], env)
        assert action_finished["externalActions"] == [
            "git_push",
            "pull_request_merge",
            "production_deploy",
        ]
        assert action_finished["actionExecuted"] is True

        repeated_finish = run([
            "--receipts-dir", str(receipts),
            "finish",
            "--receipt", action_started["receiptId"],
            "--status", "succeeded",
            "--outcome", "This idempotent replay must not replace the terminal receipt.",
            "--learning", "This replay must not stage another candidate.",
        ], env)
        assert repeated_finish["externalActions"] == action_finished["externalActions"]
        assert repeated_finish["learning"]["candidateId"] == action_finished["learning"]["candidateId"]

        invalid_started = run([
            "--receipts-dir", str(receipts),
            "start", "Reject unsafe external action labels",
            "--scope", "tenant-alpha",
            "--workspace", "tenant-alpha",
            "--operator", "smoke",
        ], env)
        with sqlite3.connect(candidates) as conn:
            candidate_count_before_invalid = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        invalid = run([
            "--receipts-dir", str(receipts),
            "finish",
            "--receipt", invalid_started["receiptId"],
            "--status", "failed",
            "--outcome", "Invalid action label must fail closed.",
            "--learning", "External action identifiers must be bounded and machine-readable.",
            "--external-action", "production deploy with spaces",
        ], env, expected_returncode=2)
        assert "stable lowercase identifier" in invalid["error"]
        with sqlite3.connect(candidates) as conn:
            candidate_count_after_invalid = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        assert candidate_count_after_invalid == candidate_count_before_invalid

        unsupported_started = run([
            "--receipts-dir", str(receipts),
            "start", "Reject unsupported action claims",
            "--scope", "tenant-alpha",
            "--workspace", "tenant-alpha",
            "--operator", "smoke",
        ], env)
        unsupported = run([
            "--receipts-dir", str(receipts),
            "finish",
            "--receipt", unsupported_started["receiptId"],
            "--status", "failed",
            "--outcome", "Unsupported action claim must fail closed.",
            "--learning", "External action claims require evidence.",
            "--external-action", "production_deploy",
        ], env, expected_returncode=2)
        assert "require at least one evidence entry" in unsupported["error"]
        with sqlite3.connect(candidates) as conn:
            assert conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == candidate_count_after_invalid

        proof = run([
            "--receipts-dir", str(receipts),
            "status", "--limit", "10",
        ], env)
        assert proof["receiptCount"] == 4
        proof_by_id = {item["receiptId"]: item for item in proof["latest"]}
        assert proof_by_id[started["receiptId"]]["candidateId"] == finished["learning"]["candidateId"]
        assert proof_by_id[started["receiptId"]]["actionExecuted"] is False
        assert proof_by_id[started["receiptId"]]["externalActions"] == []
        assert proof_by_id[action_started["receiptId"]]["actionExecuted"] is True
        assert proof_by_id[action_started["receiptId"]]["externalActions"] == action_finished["externalActions"]
        assert proof_by_id[invalid_started["receiptId"]]["status"] == "started"
        assert proof_by_id[unsupported_started["receiptId"]]["status"] == "started"

        # A sandbox output artifact must not be mistaken for another receipt.
        (receipts / f"{started['receiptId']}-sandbox-output.json").write_text("{}\n")
        proof_again = run([
            "--receipts-dir", str(receipts),
            "status", "--limit", "10",
        ], env)
        assert proof_again["receiptCount"] == 4

    print("PREPENDE OPERATOR RECEIPTS SMOKE: OK")
    print("  preflight : context-fast receipt bound")
    print("  proof     : direct/sandbox lane and terminal status recorded")
    print("  actions   : explicit executed-action ids derive truthful terminal fields")
    print("  learning  : candidate staged; durable memory write false")


if __name__ == "__main__":
    main()
