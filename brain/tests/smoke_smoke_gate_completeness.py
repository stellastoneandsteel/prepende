#!/usr/bin/env python3
"""Smoke: exercise the authoritative registry resolver without running the suite."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "scripts" / "verify_prepende_brain.py"


def _load_verifier():
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("prepende_brain_verifier", VERIFY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _repository_snapshot() -> dict[str, tuple[int, int, int, str]]:
    snapshot: dict[str, tuple[int, int, int, str]] = {}
    for directory, names, files in os.walk(ROOT):
        names[:] = [
            name for name in names if name not in {".git", ".venv", "__pycache__"}
        ]
        base = Path(directory)
        for name in sorted(files):
            path = base / name
            if not path.is_file():
                continue
            info = path.stat()
            snapshot[path.relative_to(ROOT).as_posix()] = (
                stat.S_IMODE(info.st_mode),
                int(info.st_mtime_ns),
                int(info.st_size),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
    return snapshot


def main() -> None:
    before = _repository_snapshot()
    verifier = _load_verifier()
    with tempfile.TemporaryDirectory(prefix="prepende-smoke-registry-") as directory:
        fixture = Path(directory).resolve()
        try:
            fixture.relative_to(ROOT.resolve())
        except ValueError:
            pass
        else:
            raise AssertionError("registry fixture must be outside the repository")

        tests = fixture / "tests"
        tests.mkdir()
        (fixture / "prepende-public-core-manifest.json").write_text(
            '{"schemaVersion": 2}\n', encoding="utf-8"
        )
        fixture_smokes = set(verifier.BASELINE_SMOKES) | {
            "smoke_clone_privacy.py",
            "smoke_public_core_export.py",
        }
        for name in sorted(fixture_smokes):
            (tests / name).write_text("# isolated registry fixture\n", encoding="utf-8")

        calls: list[tuple[Path, list[str]]] = []

        def bounded_runner(root: Path, smokes: list[str], *, env=None) -> int:
            del env
            calls.append((root, list(smokes)))
            return 0

        original_root = verifier.ROOT
        original_runner = verifier.run_smoke_suite
        verifier.ROOT = fixture
        verifier.run_smoke_suite = bounded_runner
        try:
            unknown = tests / "smoke_unreviewed.py"
            unknown.write_text("# must fail closed\n", encoding="utf-8")
            assert verifier.main() == 1
            assert calls == [], "unknown smoke reached the suite runner"
            unknown.unlink()

            assert verifier.main() == 0
            assert len(calls) == 1, "registry validation recursively ran the suite"
            assert calls[0][0] == fixture
            assert "smoke_phase0.py" in calls[0][1]

            registry = verifier.summarize_registry(fixture)
            assert registry["missing"] == [], registry
            assert registry["unknown"] == [], registry
            assert "smoke_public_core_export.py" in registry["executable"], registry
            assert registry["excluded"]["smoke_clone_privacy.py"] == (
                verifier._EXCLUSION_REASONS["smoke_clone_privacy.py"]
            )
        finally:
            verifier.ROOT = original_root
            verifier.run_smoke_suite = original_runner

    assert _repository_snapshot() == before, "registry smoke changed repository files"
    print("smoke_smoke_gate_completeness OK")


if __name__ == "__main__":
    main()
