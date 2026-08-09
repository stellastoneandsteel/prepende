#!/usr/bin/env python3
"""Fail closed if the public-core export admits private operational surfaces."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = (
    ".git",
    ".engram",
    ".netlify",
    "graphify-out",
    "netlify.prepende.toml",
    "n8n",
    "operations/receipts",
    "recovery",
    "sites",
    "supabase",
    "vault",
)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="prepende-public-core-test-") as parent:
        destination = Path(parent) / "export"
        proc = subprocess.run(
            [
                "python3",
                "scripts/export_prepende_public_core.py",
                "--output",
                str(destination),
                "--json",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        receipt = json.loads(proc.stdout)
        assert receipt["ok"] is True, receipt
        assert receipt["historyIncluded"] is False, receipt
        assert receipt["ownerVaultIncluded"] is False, receipt
        assert receipt["runtimeStateIncluded"] is False, receipt
        assert receipt["privacyScan"]["ok"] is True, receipt
        assert (destination / "pyproject.toml").is_file()
        assert 'name = "prepende-brain-runtime"' in (
            destination / "pyproject.toml"
        ).read_text(encoding="utf-8")
        for relative in FORBIDDEN:
            assert not (destination / relative).exists(), relative
        assert not any(destination.rglob("*.sql"))
        assert not any(destination.rglob("netlify*"))
    print("PREPENDE PUBLIC CORE EXPORT: OK")


if __name__ == "__main__":
    main()
