"""Exercise the real CLI launchers without a brain, credentials, or workspace.

The disposable interpreter records every kernel dispatch. Help and invalid
arguments must finish before reaching it or changing the caller's .env.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    checks = 0
    with tempfile.TemporaryDirectory(prefix="prepende-cli-arguments-") as raw:
        root = Path(raw)
        for relative in (
            "bin/prepende", "bin/engram", "prepende_brain/__init__.py",
            "prepende_brain/env.py", "prepende_brain/identity.py", "prepende_brain/cockpit.py",
            "operations/__init__.py", "operations/continuity.py",
        ):
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        (root / "operations/operational_status.py").write_text(
            "import os\nfrom pathlib import Path\n"
            "Path(os.environ['STATUS_IMPORT_LOG']).touch()\n"
            "def build_fast_context_status(*,root,scope): return {'scope':scope}\n"
        )
        interpreter = root / ".venv/bin/python3"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "p=Path(os.environ['DISPATCH_LOG'])\n"
            "with p.open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
            "print(json.dumps({'text':'fixture answer','receipt':{'externalActions':[], 'actionExecuted':False}}))\n"
            "sys.exit(1 if sys.argv[-1]=='fixture failure' else 0)\n"
        )
        interpreter.chmod(0o700)
        log = root / "dispatch.jsonl"
        status_log = root / "status-imported"
        caller = root / "caller"
        caller.mkdir()
        environment = caller / ".env"
        environment.write_text("PRIVATE_CONTEXT_CANARY=never-read\n")
        base_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(caller),
            "PYTHONDONTWRITEBYTECODE": "1",
            "DISPATCH_LOG": str(log),
            "STATUS_IMPORT_LOG": str(status_log),
        }

        def run(entry: str, arguments: list[str], extra_env: dict | None = None):
            log.unlink(missing_ok=True)
            status_log.unlink(missing_ok=True)
            environment.chmod(0o644)
            result = subprocess.run(
                [sys.executable, str(root / "bin" / entry), *arguments],
                cwd=caller, env={**base_env, **(extra_env or {})},
                capture_output=True, text=True, timeout=10,
            )
            return result, [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

        for entry in ("prepende", "engram"):
            for command in ("ask", "context", "context-fast"):
                for arguments in (["--help"], ["-h"], ["draft", "--help"], ["--scope=tenant-a", "--help"]):
                    result, calls = run(entry, [command, *arguments])
                    assert result.returncode == 0 and "Usage:" in result.stderr, result
                    assert not calls and "PRIVATE_CONTEXT_CANARY" not in result.stdout + result.stderr
                    assert not status_log.exists()
                    assert stat.S_IMODE(environment.stat().st_mode) == 0o644
                    checks += 1
                invalid = (
                    ([], "requires a goal"),
                    (["draft", "--scpoe", "tenant-a"], "unknown"),
                    (["draft", "--scope"], "requires a value"),
                    (["draft", "--scope", "--json"], "requires a value"),
                    (["draft", "--scope", "--help"], "requires a value"),
                    (["draft", "--memory", "--help"], "requires a value"),
                    (["draft", "--scope="], "non-empty"),
                    (["draft", "--scope", " "], "non-empty"),
                    (["draft", "--scope=-tenant-a"], "non-empty"),
                    (["draft", "--scope", "../tenant-a"], "lowercase slug"),
                    (["draft", "--scope=tenant-a", "--scope=tenant-b"], "only once"),
                    (["draft", "--scope=tenant-a", "--scope=tenant-a"], "only once"),
                    (["draft", "--memory=auto"], "candidate only"),
                    (["draft", "--json=false"], "unknown"),
                    (["draft", "--status"], "unknown"),
                    (["--", ""], "requires a goal"),
                )
                if command == "context-fast":
                    invalid += ((["draft", "--profile=unknown"], "unsupported continuity profile"),)
                else:
                    invalid += ((["draft", "--profile=coding"], "unknown"),)
                for arguments, error in invalid:
                    result, calls = run(entry, [command, *arguments])
                    assert result.returncode == 2 and error in result.stderr, (arguments, result)
                    assert not calls and "Traceback" not in result.stderr, (arguments, result)
                    assert not status_log.exists()
                    assert stat.S_IMODE(environment.stat().st_mode) == 0o644
                    checks += 1

            for command in ("ask", "context"):
                for arguments, scope, goal, extra in (
                    (["a quoted --scope phrase", "--scope", "tenant-a"], "tenant-a", "a quoted --scope phrase", {}),
                    (["--scope=tenant-a", "bare", "words"], "tenant-a", "bare words", {}),
                    (["--scope=tenant-a", "--", "--help", "--scope=tenant-b"], "tenant-a", "--help --scope=tenant-b", {}),
                    (["--scope=tenant-a", "--", "--status"], "tenant-a", "--status", {}),
                    (["draft"], "default", "draft", {}),
                    (["draft"], "tenant-env", "draft", {"PREPENDE_SCOPE": "tenant-env", "ENGRAM_SCOPE": "legacy-env", "PREPENDE_MCP_SCOPE": "mcp-env"}),
                    (["draft"], "legacy-env", "draft", {"ENGRAM_SCOPE": "legacy-env", "PREPENDE_MCP_SCOPE": "mcp-env"}),
                    (["draft"], "mcp-env", "draft", {"PREPENDE_MCP_SCOPE": "mcp-env"}),
                    (["draft"], "legacy-mcp", "draft", {"ENGRAM_MCP_SCOPE": "legacy-mcp"}),
                    (["draft", "--scope=default"], "default", "draft", {"PREPENDE_SCOPE": "tenant-env"}),
                ):
                    result, calls = run(entry, [command, "--json", *arguments], extra)
                    assert result.returncode == 0 and len(calls) == 1, (arguments, result, calls)
                    call = calls[0]
                    assert call[:2] == ["-m", "kernel"] and call[-2:] == ["--", goal], call
                    assert call[call.index("--scope") + 1] == scope, call
                    assert call[call.index("--memory") + 1] == "candidate", call
                    assert stat.S_IMODE(environment.stat().st_mode) == 0o600
                    checks += 1

            # A legitimate failed run keeps the existing bounded status fallback
            # in exactly the selected scope; a parse failure never reaches it.
            result, calls = run(entry, ["context", "fixture failure", "--scope=tenant-a", "--json"])
            assert result.returncode == 1 and len(calls) == 2, (result, calls)
            assert calls[1] == ["-m", "kernel", "--status", "--scope", "tenant-a"], calls
            assert json.loads(result.stdout)["scope"] == "tenant-a"
            checks += 1

            # Empty optional environment values retain the existing default;
            # an explicitly empty --profile remains an argument error.
            result, calls = run(entry, ["context-fast", "draft", "--scope=tenant-a", "--json"], {"PREPENDE_CONTINUITY_PROFILE": ""})
            assert result.returncode == 0 and not calls and status_log.exists(), result
            packet = json.loads(result.stdout)
            assert packet['scope'] == 'tenant-a' and packet['profile'] == 'general' and packet['goal'] == 'draft', packet
            checks += 1

            # Runtime faults must retain their original error boundary rather
            # than being relabeled as malformed CLI input after dispatch.
            continuity = root / "operations/continuity.py"
            original = continuity.read_text()
            continuity.write_text(original + "\ndef build_continuity_packet(**kwargs): raise ValueError('fixture runtime fault')\n")
            result, calls = run(entry, ["context-fast", "draft", "--scope=tenant-a", "--json"])
            assert result.returncode != 0 and 'ValueError: fixture runtime fault' in result.stderr, result
            assert 'prepende: fixture runtime fault' not in result.stderr and not calls, result
            continuity.write_text(original)
            checks += 1

            interpreter.rename(interpreter.with_name("disabled"))
            result, calls = run(entry, ["context", "--help"])
            assert result.returncode == 0 and not calls, result
            result, calls = run(entry, ["context", "draft", "--scpoe=tenant-a"])
            assert result.returncode == 2 and "unknown" in result.stderr and not calls, result
            result, calls = run(entry, ["context", "draft", "--scope=tenant-a"])
            assert result.returncode == 2 and "missing repo virtualenv" in result.stderr and not calls, result
            assert stat.S_IMODE(environment.stat().st_mode) == 0o600
            interpreter.with_name("disabled").rename(interpreter)
            checks += 3
        assert environment.read_text() == "PRIVATE_CONTEXT_CANARY=never-read\n"
    print(f"smoke_cli_arguments OK ({checks} real-launcher cases)")


if __name__ == "__main__":
    main()
