#!/usr/bin/env python3
"""Real streamable-HTTP MCP handshake with bearer identity and dispatch gates.

The smoke starts the canonical Prepende HTTP entrypoint on loopback, connects
with the MCP SDK, and proves that two tokens sharing one tenant/workspace keep
distinct principal receipts and exact capabilities. Echo + throwaway storage
keep the test offline from model providers and free of durable side effects.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


ROOT = Path(__file__).resolve().parents[1]
NO_PROPOSE_TOKEN = "tok-http-no-propose-fixture-0123456789abcdef"
PROPOSE_TOKEN = "tok-http-propose-fixture-0123456789abcdef"
REMEMBER_TOKEN = "tok-http-remember-fixture-0123456789abcdef"
ALL_TOKEN = "tok-http-all-fixture-0123456789abcdef"
CONTROL_TOKEN = "tok-http-control-fixture-0123456789abcdef"
EXPECTED_TOOLS = {
    "account",
    "chat",
    "knowledge_related",
    "knowledge_search",
    "list_workflows",
    "memory_candidates",
    "memory_propose",
    "memory_reject",
    "memory_search",
    "pursue_goal",
    "remember",
    "run_workflow",
}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_listener(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"Prepende MCP HTTP exited early with {process.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError("Prepende MCP HTTP did not start on loopback")


async def _with_session(url: str, token: str, exercise) -> None:
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"}, timeout=20
    ) as client:
        async with streamable_http_client(url, http_client=client) as transport:
            read_stream, write_stream = transport[:2]
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == "prepende", initialized.serverInfo
                listed = await session.list_tools()
                assert {tool.name for tool in listed.tools} == EXPECTED_TOOLS, listed.tools
                await exercise(session)


async def _exercise(url: str, memory_db: Path, candidates_db: Path) -> None:
    async with httpx.AsyncClient(timeout=10) as unauthenticated:
        response = await unauthenticated.post(url, content=b"{}")
        assert response.status_code == 401, response.text

    accounts: dict[str, dict] = {}

    async def exercise_no_propose(session: ClientSession) -> None:
        account = (await session.call_tool("account", {})).structuredContent
        accounts["no_propose"] = account
        assert account["capabilities"] == [
            "account", "chat", "memory_candidates", "memory_search", "pursue_goal",
        ], account
        before = await session.call_tool("memory_candidates", {})
        assert before.structuredContent["count"] == 0, before.structuredContent
        chat = await session.call_tool(
            "chat",
            {"message": "remember that the HTTP forbidden candidate color is violet"},
        )
        assert chat.structuredContent["loop"]["memory"] == {
            "proposed": [], "written": []
        }, chat.structuredContent
        assert chat.structuredContent["memoryUpdates"] == [], chat.structuredContent
        after = await session.call_tool("memory_candidates", {})
        assert after.structuredContent["count"] == 0, after.structuredContent
        recall = await session.call_tool(
            "memory_search", {"query": "HTTP forbidden candidate color violet"}
        )
        assert recall.structuredContent["count"] == 0, recall.structuredContent
        denied = await session.call_tool(
            "remember", {"content": "must remain capability-gated"}
        )
        assert denied.structuredContent["httpStatus"] == 403, denied.structuredContent

        loop_chat = await session.call_tool(
            "chat",
            {
                "message": (
                    "Please develop a careful local plan for organizing this source "
                    "review into small verified steps while preserving tenant isolation "
                    "and recording no memory because this principal lacks proposal authority."
                )
            },
        )
        assert loop_chat.structuredContent["loop"]["mode"] == "goal_loop", loop_chat.structuredContent
        assert loop_chat.structuredContent["loop"]["memory"] == {
            "recalled": 0, "proposed": [], "written": []
        }, loop_chat.structuredContent
        assert loop_chat.structuredContent["memoryUpdates"] == [], loop_chat.structuredContent

        pursued = await session.call_tool(
            "pursue_goal",
            {"goal": "Create a local source review plan with clear verification and no external action."},
        )
        assert pursued.structuredContent["receipt"]["memory"] == {
            "recalled": 0, "proposed": [], "written": []
        }, pursued.structuredContent
        final_pending = await session.call_tool("memory_candidates", {})
        assert final_pending.structuredContent["count"] == 0, final_pending.structuredContent

    await _with_session(url, NO_PROPOSE_TOKEN, exercise_no_propose)

    async def exercise_propose(session: ClientSession) -> None:
        account = (await session.call_tool("account", {})).structuredContent
        accounts["propose"] = account
        assert account["capabilities"] == [
            "account", "chat", "memory_candidates", "memory_propose", "memory_search",
        ], account
        chat = await session.call_tool(
            "chat", {"message": "remember that the HTTP candidate color is amber"}
        )
        proposed = chat.structuredContent["loop"]["memory"]["proposed"]
        assert len(proposed) == 1, chat.structuredContent
        assert proposed[0]["persisted"] is False, proposed
        assert proposed[0]["durableWrite"] is False, proposed
        assert chat.structuredContent["loop"]["memory"]["written"] == [], chat.structuredContent
        pending = await session.call_tool("memory_candidates", {})
        assert pending.structuredContent["count"] == 1, pending.structuredContent
        recall = await session.call_tool(
            "memory_search", {"query": "HTTP candidate color amber"}
        )
        assert recall.structuredContent["count"] == 0, recall.structuredContent
        candidate_id = proposed[0]["candidateId"]
        approve_attempt = await session.call_tool(
            "memory_approve", {"candidate_id": candidate_id}
        )
        assert approve_attempt.isError is True, approve_attempt
        import_attempt = await session.call_tool(
            "ingest_knowledge", {"text": "must not enter the HTTP tenant vault"}
        )
        assert import_attempt.isError is True, import_attempt
        still_pending = await session.call_tool("memory_candidates", {})
        assert still_pending.structuredContent["count"] == 1, still_pending.structuredContent
        assert _row_count(memory_db, "memories") == 0

        forged_fields = {
            "tenant": "control-company",
            "scope": "control-company--control-sales",
            "principalFingerprint": "sha256:" + ("0" * 64),
            "packet": {"scope": "control-company--control-sales", "locked": True},
            "agent_id": "forged-http-agent",
        }
        for field, value in forged_fields.items():
            before_tamper = _row_count(candidates_db, "candidates")
            tampered = await session.call_tool(
                "memory_propose",
                {
                    "content": f"forged HTTP {field} must never stage this candidate",
                    field: value,
                },
            )
            assert tampered.isError is True, (field, tampered)
            assert _row_count(candidates_db, "candidates") == before_tamper
            assert _row_count(memory_db, "memories") == 0
        denied = await session.call_tool(
            "remember", {"content": "this principal cannot write durable memory"}
        )
        assert denied.structuredContent["httpStatus"] == 403, denied.structuredContent

    await _with_session(url, PROPOSE_TOKEN, exercise_propose)

    async def exercise_remember(session: ClientSession) -> None:
        account = (await session.call_tool("account", {})).structuredContent
        accounts["remember"] = account
        assert account["capabilities"] == [
            "account", "chat", "memory_candidates", "memory_search", "remember",
        ], account
        chat = await session.call_tool(
            "chat", {"message": "remember that the HTTP durable color is cobalt"}
        )
        assert chat.structuredContent["loop"]["memory"] == {
            "proposed": [], "written": []
        }, chat.structuredContent
        before = await session.call_tool(
            "memory_search", {"query": "HTTP durable color cobalt"}
        )
        assert before.structuredContent["count"] == 0, before.structuredContent
        written = await session.call_tool(
            "remember", {"content": "the HTTP durable color is cobalt"}
        )
        assert written.structuredContent["persisted"] is True, written.structuredContent
        after = await session.call_tool(
            "memory_search", {"query": "HTTP durable color cobalt"}
        )
        assert after.structuredContent["count"] == 1, after.structuredContent
        spoofed = await session.call_tool(
            "remember",
            {"content": "attempted HTTP spoof", "scope": "control-company--control-sales"},
        )
        assert spoofed.isError is True, spoofed

    await _with_session(url, REMEMBER_TOKEN, exercise_remember)

    async def exercise_all(session: ClientSession) -> None:
        account = (await session.call_tool("account", {})).structuredContent
        accounts["all"] = account
        before_memory_rows = _row_count(memory_db, "memories")
        before_candidates = await session.call_tool("memory_candidates", {})
        chat = await session.call_tool(
            "chat", {"message": "remember that the HTTP all-capability color is emerald"}
        )
        proposed = chat.structuredContent["loop"]["memory"]["proposed"]
        assert len(proposed) == 1, chat.structuredContent
        assert chat.structuredContent["loop"]["memory"]["written"] == [], chat.structuredContent
        assert _row_count(memory_db, "memories") == before_memory_rows
        after_candidates = await session.call_tool("memory_candidates", {})
        assert after_candidates.structuredContent["count"] == before_candidates.structuredContent["count"] + 1
        assert _row_count(candidates_db, "candidates") == after_candidates.structuredContent["count"]

        loop_chat = await session.call_tool(
            "chat",
            {
                "message": (
                    "Remember that this deliberately long all-capability request must use "
                    "the full goal loop while remaining candidate-only and must never call "
                    "the durable memory writer through chat under any circumstance whatsoever."
                )
            },
        )
        assert loop_chat.structuredContent["loop"]["mode"] == "goal_loop", loop_chat.structuredContent
        assert loop_chat.structuredContent["loop"]["memory"]["written"] == [], loop_chat.structuredContent
        assert all(
            update.get("persisted") is False and update.get("durableWrite") is False
            for update in loop_chat.structuredContent["memoryUpdates"]
        ), loop_chat.structuredContent
        assert _row_count(memory_db, "memories") == before_memory_rows

    await _with_session(url, ALL_TOKEN, exercise_all)

    async def exercise_control(session: ClientSession) -> None:
        account = (await session.call_tool("account", {})).structuredContent
        accounts["control"] = account
        assert account["tenant"] == "control-company", account
        assert account["workspace"] == "control-sales", account
        recall = await session.call_tool(
            "memory_search", {"query": "HTTP durable color cobalt"}
        )
        assert recall.structuredContent["count"] == 0, recall.structuredContent
        pending = await session.call_tool("memory_candidates", {})
        assert pending.structuredContent["count"] == 0, pending.structuredContent

    await _with_session(url, CONTROL_TOKEN, exercise_control)

    primary = [accounts[name] for name in ("no_propose", "propose", "remember", "all")]
    assert all(account["tenant"] == "example-company" for account in primary)
    assert all(account["workspace"] == "example-company-sales" for account in primary)
    assert all(
        account["scope"] == "example-company--example-company-sales"
        for account in primary
    )
    assert all(account["deploymentRevision"] == "http-smoke-1" for account in accounts.values())
    assert len({account["principalId"] for account in accounts.values()}) == 5
    assert len({account["principalFingerprint"] for account in accounts.values()}) == 5
    serialized = json.dumps(accounts, sort_keys=True)
    for token in (NO_PROPOSE_TOKEN, PROPOSE_TOKEN, REMEMBER_TOKEN, ALL_TOKEN, CONTROL_TOKEN):
        assert token not in serialized


def _row_count(path: Path, table: str) -> int:
    if not path.exists():
        return 0
    with sqlite3.connect(path) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="prepende_mcp_http_") as raw_tmp:
        tmp = Path(raw_tmp)
        port = _free_port()
        token_map = {
            NO_PROPOSE_TOKEN: {
                "tenant": "example-company",
                "workspace": "example-company-sales",
                "scope": "example-company--example-company-sales",
                "capabilities": [
                    "account", "chat", "memory_candidates", "memory_search", "pursue_goal",
                ],
            },
            PROPOSE_TOKEN: {
                "tenant": "example-company",
                "workspace": "example-company-sales",
                "scope": "example-company--example-company-sales",
                "capabilities": [
                    "account", "chat", "memory_candidates", "memory_search", "memory_propose",
                ],
            },
            REMEMBER_TOKEN: {
                "tenant": "example-company",
                "workspace": "example-company-sales",
                "scope": "example-company--example-company-sales",
                "capabilities": [
                    "account", "chat", "memory_candidates", "memory_search", "remember",
                ],
            },
            ALL_TOKEN: {
                "tenant": "example-company",
                "workspace": "example-company-sales",
                "scope": "example-company--example-company-sales",
                "capabilities": "all",
            },
            CONTROL_TOKEN: {
                "tenant": "control-company",
                "workspace": "control-sales",
                "scope": "control-company--control-sales",
                "capabilities": [
                    "account", "chat", "memory_candidates", "memory_search", "remember",
                ],
            },
        }
        env = {
            **os.environ,
            "MODEL_PROVIDER": "echo",
            "EMBEDDING_PROVIDER": "echo",
            "MEMORY_BACKEND": "sqlite",
            "MEMORY_DB": str(tmp / "memory.db"),
            "RUNS_DB": str(tmp / "runs.db"),
            "WORKSPACE_ROOT": str(tmp / "workspaces"),
            "VAULT_PATH": str(tmp / "vault"),
            "PREPENDE_TENANT_TOKENS": json.dumps(token_map, sort_keys=True),
            "ENGRAM_TENANT_TOKENS": "",
            "PREPENDE_MCP_RATE_LIMIT_PER_MINUTE": "100",
        }
        for key in (
            "PREPENDE_MCP_TENANT", "PREPENDE_MCP_WORKSPACE", "PREPENDE_MCP_SCOPE",
            "PREPENDE_MCP_CAPABILITIES", "ENGRAM_MCP_TENANT",
            "ENGRAM_MCP_WORKSPACE", "ENGRAM_MCP_SCOPE", "ENGRAM_MCP_CAPABILITIES",
        ):
            env.pop(key, None)
        log_path = tmp / "server.log"
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [
                    str(ROOT / "bin" / "prepende"),
                    "mcp",
                    "http",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--deployment-revision",
                    "http-smoke-1",
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                _wait_for_listener(port, process)
                asyncio.run(
                    _exercise(
                        f"http://127.0.0.1:{port}/mcp",
                        tmp / "memory.db",
                        tmp / "memory_candidates.db",
                    )
                )
            except Exception as exc:
                log.flush()
                details = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                raise AssertionError(f"{exc}\nPrepende MCP HTTP log:\n{details}") from exc
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

        assert _row_count(tmp / "memory.db", "memories") == 1
        assert _row_count(tmp / "memory_candidates.db", "candidates") >= 2

    print("PREPENDE MCP HTTP SMOKE: OK")
    print("  transport       : real streamable-HTTP MCP handshake on loopback")
    print("  auth            : missing bearer refused with 401")
    print("  principal       : five tokens remain distinguishable without leakage")
    print("  chat memory     : candidate-only when allowed; otherwise no state change")
    print("  durable memory  : one write only through capability-gated remember")
    print("  tenant boundary : control tenant cannot read memory or candidates")
    print("  owner boundary  : approval/import unlisted and uncallable")
    print("  anti-spoof      : caller-supplied scope/packet rejected by the tool schema")


if __name__ == "__main__":
    main()
