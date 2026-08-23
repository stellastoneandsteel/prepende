#!/usr/bin/env python3
"""Standalone Prepende installs only a complete, hash-locked dependency graph."""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "distribution" / "prepende"
SOURCE_CHECKOUT = DIST.is_dir()
INPUT = (DIST / "requirements-prepende.in") if SOURCE_CHECKOUT else (ROOT / "requirements-prepende.in")
LOCK = (DIST / "requirements-prepende.lock") if SOURCE_CHECKOUT else (ROOT / "requirements-prepende.lock")
PG_SMOKE_LOCK = DIST / "requirements-candidate-pg-smoke.lock"
BOOTSTRAP = ROOT / "scripts" / "bootstrap_prepende_clone.py"
PYPROJECT = (DIST / "pyproject.toml") if SOURCE_CHECKOUT else (ROOT / "pyproject.toml")
DOCKERFILE = (DIST / "Dockerfile.mcp") if SOURCE_CHECKOUT else (ROOT / "Dockerfile.mcp")
SOURCE_RELEASE_GATE = ROOT / ".github" / "workflows" / "prepende-source-release-gate.yml"


def logical_requirements(text: str) -> list[str]:
    rows: list[str] = []
    current = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        current += (" " if current else "") + stripped.removesuffix("\\").strip()
        if not stripped.endswith("\\"):
            rows.append(current)
            current = ""
    assert not current, "unterminated requirement continuation"
    return rows


def build_probe_wheel(path: Path) -> str:
    metadata = "Metadata-Version: 2.1\nName: hash-lock-probe\nVersion: 1.0\n"
    wheel = "Wheel-Version: 1.0\nGenerator: Prepende test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("hash_lock_probe/__init__.py", "__version__ = '1.0'\n")
        archive.writestr("hash_lock_probe-1.0.dist-info/METADATA", metadata)
        archive.writestr("hash_lock_probe-1.0.dist-info/WHEEL", wheel)
        archive.writestr("hash_lock_probe-1.0.dist-info/RECORD", "")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pip_download(requirement: Path, destination: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--disable-pip-version-check",
            "--no-input",
            "--no-deps",
            "--require-hashes",
            "--requirement",
            str(requirement),
            "--dest",
            str(destination),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def main() -> None:
    direct = {
        line.split("==", 1)[0]: line.split("==", 1)[1]
        for line in logical_requirements(INPUT.read_text(encoding="utf-8"))
    }
    assert direct == {"mcp": "1.28.1", "textual": "6.11.0", "uvicorn": "0.51.0"}

    locked = logical_requirements(LOCK.read_text(encoding="utf-8"))
    assert len(locked) >= 3, "transitive lock is unexpectedly empty"
    for requirement in locked:
        assert re.match(r"^[A-Za-z0-9_.-]+==[^ ]+", requirement), requirement
        assert "--hash=sha256:" in requirement, requirement
    for package, version in direct.items():
        assert any(row.startswith(f"{package}=={version} ") for row in locked), package

    # These files govern the source-release CI environment and intentionally
    # are not included in a history-free customer export. Keep their contract
    # strict in a source checkout without making exported runtime checks depend
    # on source-only distribution or workflow files.
    if SOURCE_CHECKOUT:
        pg_smoke_lock_text = PG_SMOKE_LOCK.read_text(encoding="utf-8")
        assert "Intentionally single-platform: CPython 3.11 on Linux x86_64" in pg_smoke_lock_text
        assert logical_requirements(pg_smoke_lock_text) == [
            "asyncpg==0.31.0 "
            "--hash=sha256:c0807be46c32c963ae40d329b3a686356e417f674c976c07fa49f1b30303f109"
        ]

        source_release_gate = SOURCE_RELEASE_GATE.read_text(encoding="utf-8")
        pg_smoke_step = source_release_gate.split(
            "- name: Verify candidate queue on disposable PostgreSQL", 1
        )[1].split("- name:", 1)[0]
        assert "--require-hashes" in pg_smoke_step
        assert "--only-binary=:all:" in pg_smoke_step
        assert "distribution/prepende/requirements-candidate-pg-smoke.lock" in pg_smoke_step
        assert "requirements-api.txt" not in pg_smoke_step
        assert "cryptography" not in pg_smoke_lock_text.lower()

    pyproject = PYPROJECT.read_text(encoding="utf-8")
    for package, version in direct.items():
        assert f'"{package}=={version}"' in pyproject

    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    for contract in (
        '"--require-hashes"',
        '"--only-binary=:all:"',
        'env.pop("PYTHONPATH", None)',
        'env["PYTHONNOUSERSITE"] = "1"',
        '"trustedCheckoutPackagesBorrowed": False',
    ):
        assert contract in bootstrap, contract
    assert '"-e"' not in bootstrap and "INSTALL_SPEC" not in bootstrap

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY requirements-prepende.lock" in dockerfile
    assert "--require-hashes" in dockerfile and "--only-binary=:all:" in dockerfile
    assert "requirements-api.txt" not in dockerfile
    assert "requirements-mcp.txt" not in dockerfile
    assert "pip install --upgrade" not in dockerfile

    # Prove pip's exact enforcement locally without network access: the same
    # --require-hashes mode accepts the real local-wheel digest and rejects a
    # forged digest before installation.
    with tempfile.TemporaryDirectory(prefix="prepende-hash-proof-") as raw:
        root = Path(raw)
        wheel_path = root / "hash_lock_probe-1.0-py3-none-any.whl"
        digest = build_probe_wheel(wheel_path)
        good = root / "good.txt"
        bad = root / "bad.txt"
        good.write_text(f"hash-lock-probe @ {wheel_path.as_uri()} --hash=sha256:{digest}\n")
        bad.write_text(f"hash-lock-probe @ {wheel_path.as_uri()} --hash=sha256:{'0' * 64}\n")
        ok = pip_download(good, root / "good-download")
        assert ok.returncode == 0, ok.stdout
        refused = pip_download(bad, root / "bad-download")
        assert refused.returncode != 0, refused.stdout
        assert "hash" in refused.stdout.lower(), refused.stdout

    print("smoke_prepende_dependency_lock: ALL OK")


if __name__ == "__main__":
    main()
