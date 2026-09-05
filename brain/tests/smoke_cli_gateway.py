#!/usr/bin/env python3
"""Smoke: subscription CLIs run as plain models, not repo agents.

The Prepende `cli-codex` lane shells out to `codex exec`. If that subprocess runs
inside this repo, Codex can load AGENTS.md and recursively call Prepende again.
This smoke monkeypatches the subprocess boundary and proves the adapter uses an
ephemeral temp workspace, read-only sandbox, no user config/rules, and the final
message file.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import tempfile
import time
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import models.cli as cli_mod  # noqa: E402
from models.cli import CliGateway  # noqa: E402


class _Proc:
    returncode = 0
    stdout = "noisy transcript"
    stderr = ""


async def cancellation_contract() -> None:
    # Real OS processes, no model/provider call: a cancelled request must reap
    # the fake CLI and its descendant before returning to the MCP transport.
    with tempfile.TemporaryDirectory(prefix="prepende-cli-cancel-") as tmp:
        script = Path(tmp) / "fake_cli.py"
        script.write_text(
            "import json,os,subprocess,sys,time\n"
            "from pathlib import Path\n"
            "marker=Path(sys.argv[1])\n"
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
            "marker.write_text(json.dumps([os.getpid(),child.pid]))\n"
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        for mode in ("cancel", "timeout"):
            marker = Path(tmp) / (mode + ".json")
            gateway = CliGateway([sys.executable, str(script), str(marker)])
            gateway._record_resolution("earlier-success")
            task = asyncio.create_task(gateway.complete([], timeout=1 if mode == "timeout" else 30))
            deadline = time.monotonic() + 5
            while not marker.exists():
                assert time.monotonic() < deadline, "fake CLI did not launch"
                await asyncio.sleep(0.01)
            pids = json.loads(marker.read_text())
            cancellation_started = time.monotonic()
            if mode == "cancel":
                task.cancel()
                asyncio.get_running_loop().call_later(0.01, task.cancel)
            try:
                await task
                raise AssertionError("interrupted inference returned success")
            except asyncio.CancelledError:
                assert mode == "cancel"
            except subprocess.TimeoutExpired:
                assert mode == "timeout"
            assert time.monotonic() - cancellation_started < 3
            assert gateway._last_resolved_model is None
            deadline = time.monotonic() + 3
            while True:
                live = []
                for pid in pids:
                    check = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
                    if check.returncode == 0 and not check.stdout.strip().startswith("Z"):
                        live.append(pid)
                if not live:
                    break
                assert time.monotonic() < deadline, (mode, "orphan CLI processes", live)
                await asyncio.sleep(0.02)
        # A dedicated outer model process owns its full descendant boundary.
        # The nested CLI must inherit that group instead of escaping into a new
        # session. This proof uses only local Python fixtures.
        marker = Path(tmp) / "outer.json"
        wrapper = Path(tmp) / "outer.py"
        wrapper.write_text(
            "import asyncio,sys\n"
            f"sys.path.insert(0,{str(ROOT)!r})\n"
            "from models.cli import CliGateway,cli_in_parent_process_group\n"
            "async def run():\n"
            "    with cli_in_parent_process_group():\n"
            f"        await CliGateway({[sys.executable,str(script),str(marker)]!r}).complete([],timeout=30)\n"
            "asyncio.run(run())\n",
        )
        outer = subprocess.Popen([sys.executable,str(wrapper)],start_new_session=True)
        pids = []
        try:
            deadline=time.monotonic()+5
            while not marker.exists():
                assert time.monotonic()<deadline and outer.poll() is None
                await asyncio.sleep(.01)
            pids=json.loads(marker.read_text())
            for pid in pids:
                assert os.getpgid(pid)==outer.pid, "nested CLI escaped outer kill boundary"
            os.killpg(outer.pid,signal.SIGKILL)
            outer.wait(timeout=3)
            await asyncio.sleep(.1)
            for pid in pids:
                state=subprocess.run(["ps","-p",str(pid),"-o","stat="],capture_output=True,text=True)
                assert state.returncode!=0 or state.stdout.strip().startswith("Z")
        finally:
            if outer.poll() is None:
                os.killpg(outer.pid,signal.SIGKILL)
                outer.wait(timeout=3)
            for pid in pids:
                try:os.kill(pid,signal.SIGKILL)
                except ProcessLookupError:pass

        # An interrupted request must not poison the next independent call.
        gateway = CliGateway([sys.executable, "-c", "print('after cancellation')"])
        assert await gateway.complete([], timeout=2) == "after cancellation"


def main() -> None:
    calls = []
    old_run = cli_mod._run_process
    old_which = cli_mod.shutil.which

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        out_idx = cmd.index("--output-last-message") + 1
        Path(cmd[out_idx]).write_text("FINAL ANSWER\n", encoding="utf-8")
        return _Proc()

    try:
        cli_mod.shutil.which = lambda exe: "/usr/bin/%s" % exe
        cli_mod._run_process = fake_run
        answer = CliGateway(["codex", "exec"], "codex-sub")._run("answer me", timeout=12)
    finally:
        cli_mod._run_process = old_run
        cli_mod.shutil.which = old_which

    # Account/auth failures are terminal for the provider lane. They must not
    # masquerade as a model-availability failure and retry the whole catalog.
    auth_calls = []

    class AuthFailureProc:
        returncode = 1
        stdout = ""
        stderr = "API Error: 401 Invalid authentication credentials"

    def auth_failure_run(cmd, **kwargs):
        auth_calls.append(cmd[cmd.index("--model") + 1])
        return AuthFailureProc()

    try:
        cli_mod.shutil.which = lambda exe: "/usr/bin/%s" % exe
        cli_mod._run_process = auth_failure_run
        gateway = CliGateway(
            ["claude", "-p"],
            "claude-sub",
            "claude-fable-5",
            ("claude-opus-4-8", "claude-sonnet-5"),
        )
        try:
            gateway._run("auth", timeout=12)
            raise AssertionError("expected authentication failure")
        except RuntimeError as exc:
            assert "401" in str(exc), exc
        assert auth_calls == ["claude-fable-5"], auth_calls
    finally:
        cli_mod._run_process = old_run
        cli_mod.shutil.which = old_which

    assert answer == "FINAL ANSWER", answer
    assert len(calls) == 1, calls
    cmd, kwargs = calls[0]
    for flag in (
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--output-last-message",
    ):
        assert flag in cmd, cmd
    cwd = Path(cmd[cmd.index("-C") + 1]).resolve()
    assert ROOT not in (cwd, *cwd.parents), cwd
    assert cmd[-1] == "answer me", cmd
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["timeout"] == 12
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert "input" not in kwargs

    # Async port parity: system instructions survive the CLI boundary and
    # structured output uses each vendor's native schema flag. Thought Bus also
    # requests tool_policy=none; Claude can enforce that directly.
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    }
    structured_calls = []

    class StructuredProc:
        returncode = 0
        stdout = '{"ok":true}'
        stderr = ""

    def structured_run(cmd, **kwargs):
        captured = {"cmd": list(cmd), "kwargs": kwargs}
        if "--output-schema" in cmd:
            schema_path = Path(cmd[cmd.index("--output-schema") + 1])
            captured["schema"] = json.loads(schema_path.read_text(encoding="utf-8"))
        if "--output-last-message" in cmd:
            out_idx = cmd.index("--output-last-message") + 1
            Path(cmd[out_idx]).write_text('{"ok":true}\n', encoding="utf-8")
        structured_calls.append(captured)
        return StructuredProc()

    try:
        cli_mod.shutil.which = lambda exe: "/usr/bin/%s" % exe
        cli_mod._run_process = structured_run
        codex = CliGateway(["codex", "exec"], "codex-sub", "gpt-test")
        answer = asyncio.run(codex.complete(
            [{"role": "user", "content": "PAYLOAD SENTINEL"}],
            system="SYSTEM SENTINEL", output_schema=schema, tool_policy="none", reasoning_effort="max",
        ))
        assert answer == '{"ok":true}'
        assert codex.resolved_model == "gpt-test", codex.resolved_model
        codex_call = structured_calls[-1]
        assert codex_call["schema"] == schema
        assert "--output-schema" in codex_call["cmd"]
        assert codex_call["cmd"][codex_call["cmd"].index("--sandbox") + 1] == "read-only"
        codex_pairs = [
            codex_call["cmd"][index:index + 2]
            for index in range(len(codex_call["cmd"]) - 1)
        ]
        assert ["--disable", "shell_tool"] in codex_pairs
        assert ["--disable", "unified_exec"] in codex_pairs
        assert ["-c", 'model_reasoning_effort="max"'] in codex_pairs
        # A later default call must not inherit an earlier call's effort.
        asyncio.run(codex.complete([{"role": "user", "content": "default"}]))
        assert not any("model_reasoning_effort" in part for part in structured_calls[-1]["cmd"])
        assert "SYSTEM SENTINEL" in codex_call["cmd"][-1]
        assert "PAYLOAD SENTINEL" in codex_call["cmd"][-1]

        claude = CliGateway(["claude", "-p"], "claude-sub", "claude-test")
        answer = asyncio.run(claude.complete(
            [{"role": "user", "content": "PAYLOAD SENTINEL"}],
            system="SYSTEM SENTINEL", output_schema=schema, tool_policy="none",
        ))
        assert answer == '{"ok":true}'
        claude_cmd = structured_calls[-1]["cmd"]
        claude_kwargs = structured_calls[-1]["kwargs"]
        assert claude_cmd[claude_cmd.index("--system-prompt") + 1] == "SYSTEM SENTINEL"
        assert json.loads(claude_cmd[claude_cmd.index("--json-schema") + 1]) == schema
        assert claude_cmd[claude_cmd.index("--tools") + 1] == ""
        assert "--no-session-persistence" in claude_cmd
        assert "--disable-slash-commands" in claude_cmd
        assert claude_cmd[claude_cmd.index("--setting-sources") + 1] == ""
        settings = json.loads(claude_cmd[claude_cmd.index("--settings") + 1])
        assert settings == {"permissions": {"allow": [], "deny": []}}
        assert "--strict-mcp-config" in claude_cmd
        mcp_arg = next(part for part in claude_cmd if part.startswith("--mcp-config="))
        assert json.loads(mcp_arg.split("=", 1)[1]) == {"mcpServers": {}}
        assert "PAYLOAD SENTINEL" in claude_cmd[-1]
        claude_cwd = Path(claude_kwargs["cwd"]).resolve()
        assert ROOT not in (claude_cwd, *claude_cwd.parents), claude_cwd
    finally:
        cli_mod._run_process = old_run
        cli_mod.shutil.which = old_which

    # Codex prints `model: ...` in its banner even for an account-wide usage
    # cap. The banner must not trigger Sol -> Terra -> Luna retries; preserve
    # Sol as the requested lane and surface the real reset blocker.
    usage_calls = []

    class UsageLimitProc:
        returncode = 1
        stdout = ""
        stderr = (
            "OpenAI Codex v0.144.1\nmodel: gpt-5.6-sol\n"
            "ERROR: You've hit your usage limit. Purchase more credits or try again later."
        )

    def usage_limit_run(cmd, **kwargs):
        usage_calls.append(cmd[cmd.index("-m") + 1])
        return UsageLimitProc()

    try:
        cli_mod.shutil.which = lambda exe: "/usr/bin/%s" % exe
        cli_mod._run_process = usage_limit_run
        gateway = CliGateway(
            ["codex", "exec"], "codex-sub", "gpt-5.6-sol",
            ("gpt-5.6-terra", "gpt-5.6-luna"),
        )
        try:
            gateway._run("usage", timeout=12)
            raise AssertionError("expected usage limit")
        except RuntimeError as exc:
            assert "usage limit" in str(exc).lower(), exc
        assert usage_calls == ["gpt-5.6-sol"], usage_calls
        assert gateway.resolved_model is None
    finally:
        cli_mod._run_process = old_run
        cli_mod.shutil.which = old_which

    # Non-zero subprocess failures should surface a compact error.
    class BadProc:
        returncode = 2
        stdout = ""
        stderr = "codex failed loudly"

    try:
        cli_mod.shutil.which = lambda exe: "/usr/bin/%s" % exe
        cli_mod._run_process = lambda *a, **kw: BadProc()
        try:
            CliGateway(["codex", "exec"], "codex-sub")._run("bad", timeout=12)
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            assert "codex failed loudly" in str(exc)
    finally:
        cli_mod._run_process = old_run
        cli_mod.shutil.which = old_which

    # An unavailable preferred model advances to the next provider-local CLI
    # model, and the retry carries the replacement flag.
    fallback_calls = []

    class ModelFallbackProc:
        def __init__(self, model: str) -> None:
            self.returncode = 0 if model == "gpt-5.6-terra" else 2
            self.stdout = ""
            self.stderr = "" if self.returncode == 0 else "model unavailable"

    def fallback_run(cmd, **kwargs):
        model = cmd[cmd.index("-m") + 1]
        fallback_calls.append(model)
        if model == "gpt-5.6-terra":
            out_idx = cmd.index("--output-last-message") + 1
            Path(cmd[out_idx]).write_text("FALLBACK ANSWER\n", encoding="utf-8")
        return ModelFallbackProc(model)

    try:
        cli_mod.shutil.which = lambda exe: "/usr/bin/%s" % exe
        cli_mod._run_process = fallback_run
        gateway = CliGateway(
            ["codex", "exec"],
            "codex-sub",
            "gpt-5.6-sol",
            ("gpt-5.6-terra",),
        )
        assert gateway._run("fallback", timeout=12) == "FALLBACK ANSWER"
        assert fallback_calls == ["gpt-5.6-sol", "gpt-5.6-terra"], fallback_calls
        assert gateway.resolved_model == "gpt-5.6-terra"
    finally:
        cli_mod._run_process = old_run
        cli_mod.shutil.which = old_which

    asyncio.run(cancellation_contract())
    print("smoke_cli_gateway OK")
    print("  cancellation   : real CLI + descendants reaped; next call succeeds")
    print("  system prompts : Codex envelope + Claude native flag")
    print("  schemas        : Codex --output-schema + Claude --json-schema")
    print("  no-tools       : Claude tools empty; Codex shell/unified tools disabled")
    print("  auth/usage caps: terminal; no false model fallback retries")


if __name__ == "__main__":
    main()
