#!/usr/bin/env python3
"""Smoke: context-fast returns a receipt without invoking a model lane."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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

    print("smoke_context_fast OK")


if __name__ == "__main__":
    main()
