#!/usr/bin/env python3
"""Create the clone-owned Python environment and install declared extras.

The script is intentionally self-contained so a history-free export can prove
its runtime dependencies without borrowing an interpreter or site-packages
from the trusted source checkout.
"""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
VENV_PYTHON = VENV / "bin" / "python3"
LOCK_CANDIDATES = (
    ROOT / "requirements-prepende.lock",  # history-free customer export
    ROOT / "distribution" / "prepende" / "requirements-prepende.lock",  # source review
)
PRIVATE_UMASK = 0o077


def _supported_python():
    """Find an installed Python 3.11+ even when macOS ``python3`` is older."""

    for name in ("python3.14", "python3.13", "python3.12", "python3.11", "python3"):
        candidate = shutil.which(name)
        if not candidate:
            continue
        probe = subprocess.run(
            [candidate, "-c", "import sys; raise SystemExit(sys.version_info < (3, 11))"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if probe.returncode == 0:
            return candidate
    return None


def _run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def _isolated_environment() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PIP_REQUIRE_VIRTUALENV"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return env


def main() -> int:
    if os.name == "posix":
        os.umask(PRIVATE_UMASK)
    if sys.version_info < (3, 11):
        supported = _supported_python()
        if not supported:
            raise SystemExit("Prepende requires an installed Python 3.11 or newer")
        os.execv(supported, [supported, str(Path(__file__).resolve()), *sys.argv[1:]])
    if not (ROOT / "pyproject.toml").is_file():
        raise SystemExit(f"Prepende pyproject.toml is missing from {ROOT}")
    lock_file = next((path for path in LOCK_CANDIDATES if path.is_file()), None)
    if lock_file is None:
        raise SystemExit(f"Prepende dependency lock is missing from {ROOT}")
    lock_sha256 = hashlib.sha256(lock_file.read_bytes()).hexdigest()
    created = not VENV_PYTHON.is_file()
    if created:
        _run([sys.executable, "-m", "venv", str(VENV)])
    env = _isolated_environment()
    _run([
        str(VENV_PYTHON),
        "-m",
        "pip",
        "install",
        "--no-input",
        "--require-hashes",
        "--only-binary=:all:",
        "--requirement",
        str(lock_file),
    ], env=env)
    _run([
        str(VENV_PYTHON),
        "-c",
        "import pathlib, sys; "
        f"sys.path.insert(0, {str(ROOT)!r}); "
        "import mcp, starlette, textual, uvicorn; "
        "from interface import mcp_server; "
        f"assert pathlib.Path(sys.prefix).resolve() == pathlib.Path({str(VENV)!r}).resolve(); "
        f"assert all(pathlib.Path(module.__file__).resolve().is_relative_to(pathlib.Path({str(VENV)!r}).resolve()) "
        "for module in (mcp, starlette, textual, uvicorn)); "
        f"assert pathlib.Path(mcp_server.__file__).resolve().is_relative_to(pathlib.Path({str(ROOT)!r}).resolve()); "
        "assert mcp_server.mcp.name == 'prepende'",
    ], env=env)
    print(json.dumps({
        "ok": True,
        "created": created,
        "environment": ".venv",
        "python": ".venv/bin/python3",
        "dependencyInstall": "pip --require-hashes --only-binary=:all:",
        "dependencyLock": str(lock_file.relative_to(ROOT)),
        "dependencyLockSha256": lock_sha256,
        "bootstrapPython": ".".join(str(part) for part in sys.version_info[:3]),
        "runtimeImports": ["mcp", "starlette", "textual", "uvicorn"],
        "source": "export-owned",
        "trustedCheckoutPackagesBorrowed": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
