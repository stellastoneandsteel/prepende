#!/usr/bin/env python3
"""Fail closed if the public-core export admits private operational surfaces."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
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
        assert (destination / "prepende-public-core-manifest.json").is_file()
        assert not (destination / "prepende-export-manifest.json").exists()
        assert not (destination / "prepende-export-reviewed-inventory.json").exists()
        assert (destination / "operations" / "local_status.py").is_file()
        sys.dont_write_bytecode = True
        verifier_path = destination / "scripts" / "verify_prepende_brain.py"
        spec = importlib.util.spec_from_file_location(
            "exported_prepende_brain_verifier", verifier_path
        )
        assert spec is not None and spec.loader is not None
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        registry = verifier.summarize_registry(destination)
        assert registry["missing"] == [], registry
        assert registry["unknown"] == [], registry
        assert "smoke_public_core_export.py" in registry["executable"], registry
        assert "smoke_clone_privacy.py" not in registry["executable"], registry
        assert registry["excluded"]["smoke_clone_privacy.py"] == (
            verifier._EXCLUSION_REASONS["smoke_clone_privacy.py"]
        )
        for relative in FORBIDDEN:
            assert not (destination / relative).exists(), relative
        assert not any(destination.rglob("*.sql"))
        assert not any(destination.rglob("netlify*"))
    print("PREPENDE PUBLIC CORE EXPORT: OK")


if __name__ == "__main__":
    main()
