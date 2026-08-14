"""Hermetic smoke for fail-closed tenant standup preflight.

The test copies the standup shell script and its small identity dependency into
a temporary skeleton, then stubs state-writing Python calls. It never reads a
local environment, database, vault, Graphify output, or customer state. The
call log proves whether seed/mint could run and which backend and workspace
identity each child received.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "standup_tenant.sh"
MIGRATIONS = (
    "019_engram_kernel_memory.sql",
    "020_engram_kernel_queues.sql",
    "021_kernel_scope_guards.sql",
)


def fixture(env_text: str, *, migrations: bool = True) -> tuple[Path, Path, dict[str, str]]:
    root = Path(tempfile.mkdtemp(prefix="prepende_standup_preflight_"))
    for relative in ("scripts", "packs", "supabase/migrations", "bin", "prepende_brain"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE, root / "scripts" / "standup_tenant.sh")
    (root / "scripts" / "standup_tenant.sh").chmod(0o755)
    (root / "prepende_brain" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy2(
        ROOT / "prepende_brain" / "identity.py",
        root / "prepende_brain" / "identity.py",
    )
    (root / "packs" / "small-business.json").write_text("{}\n", encoding="utf-8")
    (root / ".env").write_text(env_text, encoding="utf-8")
    if migrations:
        for name in MIGRATIONS:
            (root / "supabase" / "migrations" / name).write_text(
                "-- fixture\n", encoding="utf-8"
            )

    call_log = root / "calls.log"
    stub = root / "bin" / "python3"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"if [ \"${{1:-}}\" = \"-c\" ]; then exec {sys.executable!r} \"$@\"; fi\n"
        "printf 'args=%s|backend=%s|db=%s|workspace=%s\\n' \"$*\" "
        "\"${MEMORY_BACKEND:-unset}\" \"${DATABASE_URL:-unset}\" "
        "\"${WORKSPACE_SCOPE:-unset}\" >> \"$CALL_LOG\"\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env = {
        "HOME": str(root / "home"),
        "PATH": f"{root / 'bin'}:/usr/bin:/bin",
        "CALL_LOG": str(call_log),
        "PYTHON": "python3",
        "PYTHONPATH": str(root),
    }
    return root, call_log, env


def run(root: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(root / "scripts" / "standup_tenant.sh"), *args],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


def calls(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def output(proc: subprocess.CompletedProcess[str]) -> str:
    return proc.stdout + proc.stderr


def main() -> None:
    roots: list[Path] = []
    try:
        for flag, bad_value in (
            ("--scope", "../evil"),
            ("--scope", "UPPER"),
            ("--tenant", "a/b"),
            ("--workspace", "a" * 65),
        ):
            root, log, env = fixture(
                "DATABASE_URL=postgres://fixture.invalid/db\nMEMORY_BACKEND=postgres\n"
            )
            roots.append(root)
            args = ["--scope", "tenant-scope", flag, bad_value]
            proc = run(root, env, *args)
            assert proc.returncode == 2, (flag, bad_value, proc.returncode, output(proc))
            assert calls(log) == [], (flag, bad_value, calls(log))
            assert "no state was written" in output(proc)
        print("OK identity: invalid tenant/workspace/scope stops before seed or mint")

        root, log, env = fixture("MEMORY_BACKEND=auto\n")
        roots.append(root)
        proc = run(root, env, "--scope", "tenant-a")
        assert proc.returncode == 1 and calls(log) == [], output(proc)
        assert "DATABASE_URL is required" in output(proc)
        print("OK customer mode: missing DATABASE_URL refuses SQLite fallback")

        root, log, env = fixture(
            "DATABASE_URL=postgres://fixture.invalid/db\nMEMORY_BACKEND=SQLITE\n"
        )
        roots.append(root)
        proc = run(root, env, "--scope", "tenant-a")
        assert proc.returncode == 1 and calls(log) == [], output(proc)
        assert "MEMORY_BACKEND=sqlite is local-only" in output(proc)
        print("OK customer mode: explicit SQLite backend is refused")

        root, log, env = fixture("DATABASE_URL=https://not-postgres.example\n")
        roots.append(root)
        proc = run(root, env, "--scope", "tenant-a")
        assert proc.returncode == 1 and calls(log) == [], output(proc)
        assert "must be a Postgres URL" in output(proc)
        print("OK customer mode: non-Postgres URL is refused")

        root, log, env = fixture(
            "DATABASE_URL=postgres://fixture.invalid/db\n"
            "MEMORY_BACKEND=postgres\n"
            "WORKSPACE_SCOPE=other-workspace\n"
        )
        roots.append(root)
        proc = run(
            root, env, "--scope", "tenant-scope--tenant-workspace", "--tenant", "tenant-scope", "--workspace", "tenant-workspace"
        )
        assert proc.returncode == 2 and calls(log) == [], output(proc)
        assert "disagrees with --workspace" in output(proc)
        print("OK workspace: conflicting environment identity is refused")

        root, log, env = fixture(
            "DATABASE_URL=postgres://fixture.invalid/db\nMEMORY_BACKEND=postgres\n",
            migrations=False,
        )
        roots.append(root)
        proc = run(root, env, "--scope", "tenant-a")
        assert proc.returncode == 1 and calls(log) == [], output(proc)
        assert "missing source migration" in output(proc)
        print("OK source preflight: missing migration stops before seed or mint")

        root, log, env = fixture("MEMORY_BACKEND=sqlite\n")
        roots.append(root)
        proc = run(
            root, env, "--scope", "local-fixture", "--local-dev-sqlite", "--backfill"
        )
        assert proc.returncode == 2 and calls(log) == [], output(proc)
        assert "--backfill" in output(proc)
        print("OK local mode: Postgres backfill cannot be mislabeled as SQLite fixture work")

        root, log, env = fixture(
            "DATABASE_URL=postgres://must-not-be-used.invalid/db\nMEMORY_BACKEND=postgres\n"
        )
        roots.append(root)
        proc = run(
            root,
            env,
            "--scope", "local-tenant--local-workspace",
            "--tenant", "local-tenant",
            "--workspace", "local-workspace",
            "--local-dev-sqlite",
        )
        local_calls = calls(log)
        assert proc.returncode == 0 and len(local_calls) == 2, output(proc)
        assert all(
            "backend=sqlite" in line and "workspace=local-workspace" in line
            for line in local_calls
        ), local_calls
        assert "--tenant local-tenant --workspace local-workspace" in local_calls[1]
        assert "LOCAL DEVELOPMENT ONLY" in output(proc) and "NOT CUSTOMER-READY" in output(proc)
        print("OK local mode: SQLite is explicit, scoped, and NOT CUSTOMER-READY")

        root, log, env = fixture(
            "DATABASE_URL=postgres://fixture.invalid/db\nMEMORY_BACKEND=auto\n"
        )
        roots.append(root)
        proc = run(
            root,
            env,
            "--scope", "tenant-a--tenant-sales",
            "--tenant", "tenant-a",
            "--workspace", "tenant-sales",
        )
        customer_calls = calls(log)
        assert proc.returncode == 0 and len(customer_calls) == 2, output(proc)
        assert all(
            "backend=postgres|db=postgres://fixture.invalid/db|workspace=tenant-sales" in line
            for line in customer_calls
        ), customer_calls
        assert "--scope tenant-a--tenant-sales --tenant tenant-a --workspace tenant-sales" in customer_calls[1]
        assert "customer Postgres preflight passed" in output(proc)
        assert "deployment and handoff remain manual approval gates" in output(proc)
        print("OK customer mode: validated Postgres and rich identity reach seed/mint stubs")

        root, log, env = fixture(
            "DATABASE_URL=postgres://fixture.invalid/db\nMEMORY_BACKEND=postgres\n"
        )
        roots.append(root)
        proc = run(
            root, env,
            "--scope", "other--sales",
            "--tenant", "tenant-a",
            "--workspace", "sales",
        )
        assert proc.returncode == 2 and calls(log) == [], output(proc)
        assert "canonical tenant/workspace namespace" in output(proc)
        print("OK namespace: mismatched physical scope stops before seed or mint")

        print("\nSTANDUP TENANT PREFLIGHT SMOKE: OK")
    finally:
        for root in roots:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
