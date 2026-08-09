"""Real stdio-client smoke for the canonical Prepende MCP entrypoint.

This launches ``bin/prepende`` as another process, performs the MCP handshake,
lists the full contract, and exercises safe capability enforcement. It uses the
echo provider and throwaway storage, so it has no spend or durable side effects.

The `mcp` package needs python >= 3.10; on python 3.9 this re-execs itself
under the repo .venv if present (else fails with the install hint).
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:
    venv_py = ROOT / ".venv" / "bin" / "python3"
    if venv_py.exists() and os.environ.get("PREPENDE_MCP_STDIO_REEXEC") != "1":
        os.environ["PREPENDE_MCP_STDIO_REEXEC"] = "1"
        os.execv(str(venv_py), [str(venv_py), os.path.abspath(__file__)])
    raise SystemExit("mcp package missing: python3.10+ and `.venv/bin/pip install mcp` required")

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


async def _with_session(env: dict[str, str], capabilities: str, exercise) -> None:
    params = StdioServerParameters(
        command=str(ROOT / "bin" / "prepende"),
        args=[
            "mcp",
            "stdio",
            "--tenant",
            "prepende-capability-test",
            "--workspace",
            "prepende-capability-test",
            "--scope",
            "prepende-capability-test",
            "--deployment-revision",
            "stdio-smoke-1",
            "--capabilities",
            capabilities,
        ],
        env=env,
        cwd=str(ROOT),
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "prepende", initialized.serverInfo
            listed = await session.list_tools()
            assert {tool.name for tool in listed.tools} == EXPECTED_TOOLS, listed.tools
            await exercise(session)


def _row_count(path: Path, table: str) -> int:
    if not path.exists():
        return 0
    with sqlite3.connect(path) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="prepende_mcp_stdio_") as tmp:
        tmp_path = Path(tmp)
        env = {
            **os.environ,
            "MODEL_PROVIDER": "echo",
            "MEMORY_BACKEND": "sqlite",
            "MEMORY_DB": str(tmp_path / "memory.db"),
            "RUNS_DB": str(tmp_path / "runs.db"),
            "WORKSPACE_ROOT": str(tmp_path / "workspaces"),
            "VAULT_PATH": str(tmp_path / "vault"),
        }
        for key in (
            "PREPENDE_MCP_TENANT", "PREPENDE_MCP_WORKSPACE", "PREPENDE_MCP_SCOPE",
            "ENGRAM_MCP_TENANT", "ENGRAM_MCP_WORKSPACE", "ENGRAM_MCP_SCOPE",
            "PREPENDE_TENANT_TOKENS", "ENGRAM_TENANT_TOKENS",
        ):
            env.pop(key, None)
        async def exercise_safe(session: ClientSession) -> None:
            account = await session.call_tool("account", {})
            assert account.structuredContent == {
                    "ok": True,
                    "tenant": "prepende-capability-test",
                    "tenantId": "prepende-capability-test",
                    "workspace": "prepende-capability-test",
                    "workspaceId": "prepende-capability-test",
                    "scope": "prepende-capability-test",
                    "principalId": account.structuredContent["principalId"],
                    "principalFingerprint": account.structuredContent["principalFingerprint"],
                    "capabilities": sorted({
                        "account", "chat", "knowledge_related", "knowledge_search",
                        "memory_candidates", "memory_propose", "memory_search", "pursue_goal",
                    }),
                    "identity": "mcp",
                    "model": "echo",
                    "deploymentRevision": "stdio-smoke-1",
                    "deploymentRevisionConfigured": True,
                    "memoryPolicy": "candidate",
                    "externalActions": "approval_required",
            }, account.structuredContent
            assert account.structuredContent["principalId"].startswith("mcp-stdio:sha256:")
            assert account.structuredContent["principalFingerprint"].startswith("sha256:")

            before = await session.call_tool("memory_candidates", {})
            assert before.structuredContent["count"] == 0, before.structuredContent
            chat = await session.call_tool(
                "chat",
                {"message": "remember that the stdio candidate color is amber"},
            )
            proposed = chat.structuredContent["loop"]["memory"]["proposed"]
            assert len(proposed) == 1, chat.structuredContent
            assert proposed[0]["persisted"] is False, proposed
            assert proposed[0]["durableWrite"] is False, proposed
            assert chat.structuredContent["loop"]["memory"]["written"] == [], chat.structuredContent
            after = await session.call_tool("memory_candidates", {})
            assert after.structuredContent["count"] == 1, after.structuredContent
            candidate_id = proposed[0]["candidateId"]
            recall = await session.call_tool(
                "memory_search", {"query": "stdio candidate color amber"}
            )
            assert recall.structuredContent["count"] == 0, recall.structuredContent

            # Neither candidate approval nor knowledge import exists on the
            # customer MCP surface, even for a caller that knows the old name.
            approve_attempt = await session.call_tool(
                "memory_approve", {"candidate_id": candidate_id}
            )
            assert approve_attempt.isError is True, approve_attempt
            import_attempt = await session.call_tool(
                "ingest_knowledge", {"text": "must not enter the vault"}
            )
            assert import_attempt.isError is True, import_attempt
            still_pending = await session.call_tool("memory_candidates", {})
            assert still_pending.structuredContent["count"] == 1, still_pending.structuredContent
            assert _row_count(tmp_path / "memory.db", "memories") == 0

            # Every caller-controlled identity/provenance/packet field is
            # rejected by the schema before the tool body runs.
            forged_fields = {
                "tenant": "another-tenant",
                "scope": "another-tenant--another-workspace",
                "principalFingerprint": "sha256:" + ("0" * 64),
                "packet": {"tenant": "another-tenant", "locked": True},
                "agent_id": "forged-agent",
            }
            for field, value in forged_fields.items():
                before_tamper = _row_count(
                    tmp_path / "memory_candidates.db", "candidates"
                )
                tampered = await session.call_tool(
                    "memory_propose",
                    {
                        "content": f"forged {field} must never stage this candidate",
                        field: value,
                    },
                )
                assert tampered.isError is True, (field, tampered)
                assert _row_count(
                    tmp_path / "memory_candidates.db", "candidates"
                ) == before_tamper
                assert _row_count(tmp_path / "memory.db", "memories") == 0

            denied = await session.call_tool(
                "remember", {"content": "this write must be capability-gated"}
            )
            assert denied.structuredContent["httpStatus"] == 403, denied.structuredContent

            gated = await session.call_tool(
                "chat", {"message": "send the invoice to the client now"}
            )
            assert gated.structuredContent["approvalRequired"] is True, gated.structuredContent
            assert gated.structuredContent["actionExecuted"] is False, gated.structuredContent

        await _with_session(env, "safe", exercise_safe)

        async def exercise_no_propose(session: ClientSession) -> None:
            before = await session.call_tool("memory_candidates", {})
            assert before.structuredContent["count"] == 1, before.structuredContent
            chat = await session.call_tool(
                "chat",
                {"message": "remember that the stdio forbidden candidate color is violet"},
            )
            assert chat.structuredContent["loop"]["memory"] == {
                "proposed": [], "written": []
            }, chat.structuredContent
            assert chat.structuredContent["memoryUpdates"] == [], chat.structuredContent
            after = await session.call_tool("memory_candidates", {})
            assert after.structuredContent["count"] == 1, after.structuredContent
            recall = await session.call_tool(
                "memory_search", {"query": "stdio forbidden candidate color violet"}
            )
            assert recall.structuredContent["count"] == 0, recall.structuredContent
            denied = await session.call_tool(
                "remember", {"content": "this write remains denied without remember"}
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
            assert final_pending.structuredContent["count"] == 1, final_pending.structuredContent

        await _with_session(
            env,
            "account,chat,pursue_goal,memory_candidates,memory_search",
            exercise_no_propose,
        )

        async def exercise_remember(session: ClientSession) -> None:
            chat = await session.call_tool(
                "chat",
                {"message": "remember that the stdio durable color is cobalt"},
            )
            assert chat.structuredContent["loop"]["memory"] == {
                "proposed": [], "written": []
            }, chat.structuredContent
            before = await session.call_tool(
                "memory_search", {"query": "stdio durable color cobalt"}
            )
            assert before.structuredContent["count"] == 0, before.structuredContent
            written = await session.call_tool(
                "remember", {"content": "the stdio durable color is cobalt"}
            )
            assert written.structuredContent["persisted"] is True, written.structuredContent
            after = await session.call_tool(
                "memory_search", {"query": "stdio durable color cobalt"}
            )
            assert after.structuredContent["count"] == 1, after.structuredContent

            spoofed = await session.call_tool(
                "remember",
                {"content": "attempted spoof", "scope": "another-tenant"},
            )
            assert spoofed.isError is True, spoofed

        await _with_session(
            env,
            "account,chat,memory_candidates,memory_search,remember",
            exercise_remember,
        )

        async def exercise_all_capabilities(session: ClientSession) -> None:
            before_memory = _row_count(tmp_path / "memory.db", "memories")
            before_candidates = _row_count(tmp_path / "memory_candidates.db", "candidates")
            chat = await session.call_tool(
                "chat",
                {"message": "remember that the all-capability chat color is emerald"},
            )
            proposed = chat.structuredContent["loop"]["memory"]["proposed"]
            assert len(proposed) == 1, chat.structuredContent
            assert chat.structuredContent["loop"]["memory"]["written"] == [], chat.structuredContent
            assert _row_count(tmp_path / "memory.db", "memories") == before_memory
            assert _row_count(tmp_path / "memory_candidates.db", "candidates") == before_candidates + 1

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
            assert _row_count(tmp_path / "memory.db", "memories") == before_memory

        await _with_session(env, "all", exercise_all_capabilities)

        assert _row_count(tmp_path / "memory.db", "memories") == 1
        assert _row_count(tmp_path / "memory_candidates.db", "candidates") >= 2

    print("PREPENDE MCP STDIO SMOKE: OK")
    print("  handshake       : serverInfo.name=prepende")
    print("  tools           : 12 listed; approval/import absent")
    print("  chat memory     : candidate-only when allowed; otherwise no state change")
    print("  durable memory  : one write only through capability-gated remember")
    print("  owner boundary  : approval/import unlisted and uncallable")
    print("  anti-spoof      : caller-supplied scope/packet rejected by the tool schema")
    print("  external action : approval required, nothing executed")


if __name__ == "__main__":
    asyncio.run(main())
