#!/usr/bin/env python3
"""Smoke: verify registry completeness fail-fast and temporary-fixture cleanup behavior."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "scripts" / "verify_prepende_brain.py"
TEMP_SMOKE = ROOT / "tests" / "smoke_temp_gate_fixture.py"


def _run_verify() -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PREPENDE_SMOKE_GATE_TEST_SKIP"] = "smoke_smoke_gate_completeness.py"
    return subprocess.run(
        [sys.executable, str(VERIFY)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )


def main() -> None:
    TEMP_SMOKE.write_text(
        "\n".join(
            [
                "# test fixture created by smoke_smoke_gate_completeness.py",
                "print('temporary smoke fixture')",
            ]
        ),
        encoding="utf-8",
    )
    try:
        failed = _run_verify()
        combined = f"{failed.stdout}\n{failed.stderr}"
        assert failed.returncode != 0, (failed.returncode, combined)
        assert "Unregistered smokes detected" in combined, combined
        assert "smoke_temp_gate_fixture.py" in combined, combined
    finally:
        if TEMP_SMOKE.exists():
            TEMP_SMOKE.unlink()

    passed = _run_verify()
    assert passed.returncode == 0, (passed.returncode, f"{passed.stdout}\n{passed.stderr}")
    print("smoke_smoke_gate_completeness OK")


if __name__ == "__main__":
    main()
