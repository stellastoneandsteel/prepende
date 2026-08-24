#!/usr/bin/env python3
"""Smoke: context-fast returns a receipt without invoking a model lane."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from knowledge.rag import VaultRagIndex


def main() -> None:
    goal = "Verify Prepende timeout-resistant context preflight"
    started = time.monotonic()
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "bin" / "engram"),
            "context-fast",
            goal,
            "--json",
            "--scope",
            "prepende",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=12,
    )
    elapsed = time.monotonic() - started
    assert proc.returncode == 0, proc.stderr or proc.stdout
    payload = json.loads(proc.stdout)

    assert payload["ok"] is True
    assert payload["command"] == "context-fast"
    assert payload["scope"] == "prepende"
    assert payload["goal"] == goal
    assert payload["memoryPolicy"] == "candidate"
    assert payload["modelCall"] == "skipped"
    assert payload["modelLane"]["status"] == "skipped"
    assert payload["externalActions"] == []
    assert payload["actionExecuted"] is False
    assert isinstance(payload["status"], dict)
    assert payload["receipt"]["Verified"].startswith("No durable memory writes")

    stage_names = [stage["stage"] for stage in payload["receipt"]["stages"]]
    assert stage_names == ["Recalled", "Decided", "Proposed", "Blocked", "Verified", "Next"]
    assert elapsed < 12, elapsed

    # A recovery profile can have healthy continuity while its recovery proof
    # blocks planning. The human-readable receipt must follow planReady rather
    # than incorrectly announcing that work may proceed.
    with tempfile.TemporaryDirectory(prefix="prepende-context-fast-recovery-") as temp_dir:
        temp = Path(temp_dir)
        vault = temp / "vault"
        (vault / "wiki").mkdir(parents=True)
        (vault / "wiki" / "continuity.md").write_text(
            "# Continuity\n\nRecovery profile receipt regression fixture.\n",
            encoding="utf-8",
        )
        asyncio.run(
            VaultRagIndex(
                str(vault),
                index_path=str(temp / "vault_index.db"),
            ).rebuild()
        )
        recovery_env = os.environ.copy()
        recovery_env.update(
            {
                "VAULT_PATH": str(vault),
                "MEMORY_DB": str(temp / "memory.db"),
                "VAULT_INDEX_PATH": str(temp / "vault_index.db"),
                "PREPENDE_RECOVERY_MANIFEST": str(temp / "missing-recovery.json"),
                "EMBEDDING_PROVIDER": "",
            }
        )
        recovery_proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "bin" / "engram"),
                "context-fast",
                "Verify recovery blockers remain visible",
                "--json",
                "--scope",
                "prepende",
                "--profile",
                "recovery",
            ],
            cwd=ROOT,
            env=recovery_env,
            capture_output=True,
            text=True,
            timeout=12,
        )
        assert recovery_proc.returncode == 0, recovery_proc.stderr or recovery_proc.stdout
        recovery = json.loads(recovery_proc.stdout)
        assert recovery["verdict"]["continuityReady"] is True, recovery
        assert recovery["verdict"]["planReady"] is False, recovery
        assert recovery["receipt"]["Blocked"], recovery["receipt"]
        assert "planning prerequisites are not ready" in recovery["receipt"]["Blocked"]
        assert recovery["receipt"]["Proposed"].startswith("Use this packet only")
        assert recovery["receipt"]["Next"].startswith("Resolve the selected profile")
        blocked_stage = next(
            stage for stage in recovery["receipt"]["stages"] if stage["stage"] == "Blocked"
        )
        assert blocked_stage["status"] == "blocked", blocked_stage

    print("smoke_context_fast OK")


if __name__ == "__main__":
    main()
