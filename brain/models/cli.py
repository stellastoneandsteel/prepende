"""CliGateway — use a locally-installed, subscription-authed vendor CLI as the backend.

This is "Nate's trick" generalized: instead of paying API tokens, Prepende shells
out to a CLI you already have installed and logged into your subscription —
Claude Code (`claude`) on a Claude Pro/Max plan, or Codex (`codex`) on a ChatGPT
plan. Inference bills to YOUR flat subscription, not API tokens. And because it's
just one more adapter behind ModelGateway, Prepende stays vendor-neutral — you can
flip to it or away from it with `model cli-claude` / `model openai` / `model local`.

HONEST CAVEATS:
- Personal use, opt-in. Do NOT ship this in the product you sell: it requires the
  user to have the vendor's CLI installed + logged in, and using a vendor's client
  as a generic backend sits in a gray area of their terms. For customers, you
  provide the inference (API keys) — see SEPARATION.md / the business model.
- Direct API credentials and alternate provider routes are removed from the
  subprocess environment. Persisted vendor-CLI auth remains a separate boundary;
  subscription-only callers must preflight it before any model dispatch.
- Requires the CLI on PATH; degrades with a clear message if it isn't.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator, Sequence

from prepende_brain.env import brand_env

from kernel.contracts import ModelGateway


_UNSET = object()


class CliGateway(ModelGateway):
    def __init__(
        self,
        command: list[str],
        name: str = "cli",
        requested_model: str = "",
        fallback_models: Sequence[str] = (),
    ) -> None:
        self.command = list(command)  # e.g. ["claude", "-p"] or ["codex", "exec"]
        self.name = name
        self.requested_model = requested_model or "cli-managed"
        self.fallback_models = tuple(fallback_models)
        self._last_resolved_model: str | None = None
        self._resolved_context: contextvars.ContextVar[object] = contextvars.ContextVar(
            f"prepende_cli_resolved_{id(self)}",
            default=_UNSET,
        )
        if requested_model:
            if self.command[:2] == ["codex", "exec"] and "-m" not in self.command:
                self.command.extend(["-m", requested_model])
            elif self.command and self.command[0] == "claude" and "--model" not in self.command:
                self.command.extend(["--model", requested_model])
        # Subscription CLIs may route models internally. Do not claim that the
        # executable name is the model that actually answered.
        self.model = None

    @property
    def resolved_model(self) -> str | None:
        value = self._resolved_context.get()
        if value is _UNSET:
            return self._last_resolved_model
        return value if isinstance(value, str) else None

    def _record_resolution(self, value: str | None) -> None:
        self._last_resolved_model = value
        self._resolved_context.set(value)

    @staticmethod
    def _subscription_env() -> dict[str, str]:
        """Keep CLI login state while preventing accidental API-key billing."""
        env = dict(os.environ)
        blocked_exact = {
            "CLAUDE_API_KEY",
            "CLAUDE_CODE_API_BASE_URL",
            "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR",
            "CLAUDE_CODE_CUSTOM_OAUTH_URL",
            "CLAUDE_CODE_GB_BASE_URL",
            "CLAUDE_CODE_HFI_BEARER_TOKEN",
            "CLAUDE_CODE_HOST_AUTH_ENV_VAR",
            "CLOUD_ML_REGION",
            "CODEX_API_KEY",
            "CODEX_AUTH_API_BASE_URL",
            "CODEX_OSS_BASE_URL",
            "CODEX_URL",
        }
        blocked_prefixes = (
            "ANTHROPIC_",
            "OPENAI_",
            "AWS_",
            "AZURE_",
            "GOOGLE_",
            "GROK_",
            "XAI_",
            "OPENROUTER_",
            "CLAUDE_CODE_USE_",
            "CLAUDE_CODE_SKIP_BEDROCK_",
            "CLAUDE_CODE_SKIP_FOUNDRY_",
            "CLAUDE_CODE_SKIP_MANTLE_",
            "CLAUDE_CODE_SKIP_VERTEX_",
        )
        for key in tuple(env):
            if key in blocked_exact or key.startswith(blocked_prefixes):
                env.pop(key, None)
        return env

    def _command_for_model(self, model: str) -> list[str]:
        """Return the CLI command with exactly one model flag when needed."""

        command: list[str] = []
        skip_next = False
        for part in self.command:
            if skip_next:
                skip_next = False
                continue
            if part in {"-m", "--model"}:
                skip_next = True
                continue
            command.append(part)
        if model and command[:2] == ["codex", "exec"]:
            command.extend(["-m", model])
        elif model and command and command[0] == "claude":
            command.extend(["--model", model])
        return command

    @staticmethod
    def _should_fallback(exc: Exception) -> bool:
        detail = str(exc).lower()
        # Authentication, installation, and policy failures apply to the whole
        # provider lane. Retrying them under a different model only hides the
        # actual blocker and burns time.
        terminal_markers = (
            "authentication",
            "credential",
            "unauthorized",
            "forbidden",
            "api key",
            "log in",
            "login",
            "not found on path",
            "permission denied",
            "you've hit your usage limit",
            "usage limit",
            "purchase more credits",
            "insufficient_quota",
        )
        if any(marker in detail for marker in terminal_markers):
            return False
        return bool(
            re.search(
                r"unsupported(?: model)?|model (?:unavailable|not available|not found|"
                r"does not exist)|unknown model|invalid model|rate.?limit|"
                r"overloaded|capacity",
                detail,
            )
        )

    @staticmethod
    def _compact_error(output: str, fallback: str) -> str:
        """Prefer the actual CLI error over its model/session banner.

        Codex prints a banner containing ``model: ...`` before account errors.
        Truncating the first 300 characters hid the real usage-limit line and
        made the fallback classifier mistake the banner for a model failure,
        silently walking Sol -> Terra -> Luna. Keep bounded error lines instead.
        """
        raw = str(output or "").strip()
        if not raw:
            return fallback
        markers = (
            "error", "usage limit", "purchase more credits", "unauthorized",
            "forbidden", "authentication", "rate limit", "unavailable",
            "unknown model", "invalid model", "does not exist", "overloaded",
        )
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        relevant = [line for line in lines if any(m in line.lower() for m in markers)]
        selected = relevant[-4:] if relevant else lines[-8:]
        selected = list(dict.fromkeys(selected))
        return "\n".join(selected)[-1200:]

    def _run_once(
        self,
        prompt: str,
        timeout: int,
        model: str = "",
        output_schema: dict[str, Any] | None = None,
        system_prompt: str = "",
        tool_policy: str = "",
    ) -> str:
        command = self._command_for_model(model)
        exe = command[0]
        if not shutil.which(exe):
            raise RuntimeError(
                f"'{exe}' not found on PATH — install it and log into your subscription, "
                f"or switch model (e.g. `model local`)."
            )
        if exe == "codex" and len(self.command) >= 2 and self.command[1] == "exec":
            if system_prompt:
                prompt = (
                    "<SYSTEM_INSTRUCTIONS>\n"
                    + system_prompt
                    + "\n</SYSTEM_INSTRUCTIONS>\n\n"
                    + prompt
                )
            return self._run_codex_exec(
                prompt, timeout, command, output_schema=output_schema
            )
        if exe == "claude":
            if system_prompt:
                command.extend(["--system-prompt", system_prompt])
            if output_schema is not None:
                if not isinstance(output_schema, dict):
                    raise ValueError("output_schema must be a JSON object")
                command.extend([
                    "--json-schema",
                    json.dumps(output_schema, sort_keys=True, separators=(",", ":")),
                ])
            if tool_policy == "none":
                command.extend(["--tools", ""])
            elif not any(part.startswith("--allowedTools") for part in command):
                # A subscription-backed model call is not an agent session.
                # Tools stay off unless the operator explicitly granted a
                # bounded set through PREPENDE_CLI_ALLOWED_TOOLS.
                command.extend(["--tools", ""])
            return self._run_claude_print(prompt, timeout, command)
        elif system_prompt:
            prompt = (
                "<SYSTEM_INSTRUCTIONS>\n"
                + system_prompt
                + "\n</SYSTEM_INSTRUCTIONS>\n\n"
                + prompt
            )
        proc = subprocess.run(
            command + [prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env=self._subscription_env(),
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or "").strip()[:300] or f"{exe} exited {proc.returncode}")
        return (proc.stdout or "").strip()

    def _run_claude_print(
        self, prompt: str, timeout: int, command: list[str]
    ) -> str:
        """Run Claude print mode as an ephemeral model outside the repo.

        Unlike ``--bare``, this preserves subscription/OAuth auth. The empty
        temporary cwd prevents project CLAUDE.md discovery; explicit system,
        schema, and tool flags remain on ``command``.
        """
        with tempfile.TemporaryDirectory(prefix="prepende-claude-") as tmp:
            cmd = [
                *command,
                "--no-session-persistence",
                "--disable-slash-commands",
                "--setting-sources",
                "",
                "--settings",
                '{"permissions":{"allow":[],"deny":[]}}',
                "--strict-mcp-config",
                '--mcp-config={"mcpServers":{}}',
                prompt,
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                cwd=tmp,
                env=self._subscription_env(),
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    self._compact_error(
                        proc.stderr or proc.stdout or "",
                        f"claude exited {proc.returncode}",
                    )
                )
            return (proc.stdout or "").strip()

    def _run_with_resolution(
        self,
        prompt: str,
        timeout: int,
        output_schema: dict[str, Any] | None = None,
        system_prompt: str = "",
        tool_policy: str = "",
    ) -> tuple[str, str | None]:
        candidates = tuple(model for model in (self.requested_model, *self.fallback_models) if model != "cli-managed")
        if not candidates:
            candidates = ("",)
        last_error: Exception | None = None
        for index, candidate in enumerate(candidates):
            try:
                answer = self._run_once(
                    prompt, timeout, candidate, output_schema=output_schema,
                    system_prompt=system_prompt, tool_policy=tool_policy,
                )
                # The candidate is placed explicitly on the CLI command line,
                # so a successful call proves the actual model even when it is
                # the preferred model. Reserve None for a genuinely
                # CLI-managed/unknown selection.
                resolved = candidate or None
                return answer, resolved
            except Exception as exc:
                last_error = exc
                if not self._should_fallback(exc) or index == len(candidates) - 1:
                    raise
        raise last_error or RuntimeError("CLI model fallback failed")

    def _run(self, prompt: str, timeout: int) -> str:
        answer, resolved = self._run_with_resolution(prompt, timeout)
        self._record_resolution(resolved)
        return answer

    def _run_codex_exec(
        self,
        prompt: str,
        timeout: int,
        command: list[str] | None = None,
        *,
        output_schema: dict[str, Any] | None = None,
    ) -> str:
        """Run Codex as a plain subscription-backed model, not as a repo agent.

        If `codex exec` runs from this repository, it loads AGENTS.md and can
        follow the local rule to ask Prepende first, causing Prepende -> Codex ->
        Prepende recursion. Keep the subscription lane isolated in a temporary
        read-only workspace and read only the final message.
        """
        with tempfile.TemporaryDirectory(prefix="prepende-codex-") as tmp:
            out_path = Path(tmp) / "last-message.txt"
            schema_args: list[str] = []
            if output_schema is not None:
                if not isinstance(output_schema, dict):
                    raise ValueError("output_schema must be a JSON object")
                schema_path = Path(tmp) / "output-schema.json"
                schema_path.write_text(
                    json.dumps(output_schema, sort_keys=True), encoding="utf-8"
                )
                schema_args = ["--output-schema", str(schema_path)]
            cmd = [
                *(command or self.command),
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "-C",
                tmp,
                "--sandbox",
                "read-only",
                "--disable",
                "shell_tool",
                "--disable",
                "unified_exec",
                "--output-last-message",
                str(out_path),
                *schema_args,
                prompt,
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                env=self._subscription_env(),
            )
            if proc.returncode != 0:
                detail = self._compact_error(
                    proc.stderr or proc.stdout or "",
                    "codex exited %s" % proc.returncode,
                )
                raise RuntimeError(detail)
            if out_path.exists():
                text = out_path.read_text(encoding="utf-8").strip()
                if text:
                    return text
            return (proc.stdout or "").strip()

    async def complete(self, messages: Sequence[dict[str, Any]], **opts: Any) -> str:
        # Preserve message roles in the one prompt argument used by subscription
        # CLIs. System instructions are passed separately below: native
        # --system-prompt for Claude, and an explicit protected envelope for
        # Codex (whose exec command has no system-prompt flag).
        # Default per-call timeout is env-tunable (PREPENDE_CLI_TIMEOUT, with
        # ENGRAM_CLI_TIMEOUT kept as an alias):
        # hierarchical runs with tool use (e.g. WebSearch research) routinely need
        # more than the old fixed 300s — a too-small cap kills the FINAL step of a
        # long run after all the work is done. Explicit opts still win.
        default_timeout = int(brand_env("CLI_TIMEOUT", "600") or "600")
        prompt_parts: list[str] = []
        for message in messages or []:
            role = str(message.get("role") or "user").strip().upper()
            content = str(message.get("content") or "")
            prompt_parts.append(f"<{role}>\n{content}\n</{role}>")
        prompt = "\n\n".join(prompt_parts)
        output_schema = opts.get("output_schema")
        system = str(opts.get("system") or "").strip()
        tool_policy = str(opts.get("tool_policy") or "").strip().lower()
        answer, resolved = await asyncio.to_thread(
            self._run_with_resolution,
            prompt,
            int(opts.get("timeout", default_timeout)),
            output_schema,
            system,
            tool_policy,
        )
        self._record_resolution(resolved)
        return answer

    async def stream(self, messages: Sequence[dict[str, Any]], **opts: Any) -> AsyncIterator[str]:
        text = await self.complete(messages, **opts)
        for word in text.split(" "):
            yield word + " "

    async def embed(self, texts: Sequence[str], **opts: Any) -> Sequence[Sequence[float]]:
        raise NotImplementedError("embeddings arrive in Phase 1 (memory)")
