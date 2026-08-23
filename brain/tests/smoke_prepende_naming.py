#!/usr/bin/env python3
"""Prepende is canonical while Engram remains a bounded compatibility alias."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from interface import engram_api, prepende_api  # noqa: E402
from kernel.core.persona import PERSONA, RESEARCH_PERSONA  # noqa: E402
from prepende_brain import env as brand  # noqa: E402


def main() -> None:
    brand._WARNED_CONFLICTS.clear()
    fake = {
        "PREPENDE_API_TOKEN": "canonical-test-value",
        "ENGRAM_API_TOKEN": "legacy-test-value",
    }
    warnings = io.StringIO()
    with contextlib.redirect_stderr(warnings):
        assert brand.brand_env("API_TOKEN", env=fake) == "canonical-test-value"
        brand.mirror_brand_environment(fake)
    emitted = warnings.getvalue()
    assert "PREPENDE_API_TOKEN" in emitted and "ENGRAM_API_TOKEN" in emitted
    assert "canonical-test-value" not in emitted and "legacy-test-value" not in emitted
    assert fake["ENGRAM_API_TOKEN"] == "canonical-test-value"

    brand._WARNED_CONFLICTS.clear()
    explicit_empty = {"PREPENDE_WIDGET_KEYS": "", "ENGRAM_WIDGET_KEYS": "legacy-test-value"}
    with contextlib.redirect_stderr(io.StringIO()):
        assert brand.brand_env("WIDGET_KEYS", env=explicit_empty) == ""

    assert PERSONA.startswith("You are Prepende")
    assert RESEARCH_PERSONA.startswith("You are Prepende Researcher & Editor")
    assert prepende_api.Handler is engram_api.Handler
    for legacy, handler in engram_api._LEGACY_POST_ROUTES.items():
        canonical = legacy.replace("/api/engram/", "/api/prepende/", 1)
        assert engram_api._POST_ROUTES[canonical] is handler
        assert engram_api._POST_ROUTES[legacy] is handler

    canonical_schema_path = ROOT / "openapi" / "prepende-actions.yaml"
    if canonical_schema_path.is_file():
        canonical_schema = canonical_schema_path.read_text(encoding="utf-8")
        assert (ROOT / "openapi" / "engram-actions.yaml").is_file()
        assert "  /api/prepende/health:" in canonical_schema
        assert "operationId: checkPrependeHealth" in canonical_schema
        assert "operationId: orchestratePrepende" in canonical_schema
        assert "  /api/engram/health:" not in canonical_schema

    v1_path = ROOT / "interface" / "v1_api.py"
    if v1_path.is_file():
        v1_source = v1_path.read_text(encoding="utf-8")
        assert 'self.headers.get("X-Prepende-Action-Key"' in v1_source
        assert 'self.headers.get("X-Engram-Action-Key"' in v1_source
    assert (ROOT / "bin" / "engram").is_file()
    config_source = (ROOT / "kernel" / "core" / "config.py").read_text(encoding="utf-8")
    assert "./.engram/memory.db" in config_source
    migrations = ROOT / "supabase" / "migrations"
    if migrations.is_dir():
        assert (migrations / "019_engram_kernel_memory.sql").is_file()
    print("PREPENDE NAMING COMPATIBILITY: OK")


if __name__ == "__main__":
    main()
