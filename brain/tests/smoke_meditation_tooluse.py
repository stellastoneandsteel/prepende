"""Smoke — meditation suppresses tools and connector awaits stay bounded.

smoke_meditation.py builds SoloTactic with connectors=None, so it never touches
the connector path — the exact surface that hung. This proves, all without a
live model:

  1. (posture, not tools) SoloTactic under --meditate with a ready connector
     SUPPRESSES connectors: it takes the single streamed completion, never the
     non-streaming, up-to-5-call tool loop that emitted nothing until it finished
     (and could hang on a stuck connector). The posture still rides the prompt.
  2. (tools still work off-meditation) SoloTactic with the SAME ready connector
     and meditation OFF does enter the tool loop and call it — suppression is
     scoped to the posture, it did not break normal solo tool use.
  3. (await guard) run_with_tools times out a cancellable async connector and
     still produces an answer. Blocking workers need their own transport limit.
  4. (mcp offload) McpConnector.call runs its blocking transport off the event
     loop, so it can't stall the loop (which is what defeated any timeout).

Run from the repo root:  MODEL_PROVIDER=echo python tests/smoke_meditation_tooluse.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MODEL_PROVIDER"] = "echo"
os.environ.pop("PREPENDE_MEDITATE", None)  # hermetic: never inherit an operator opt-in
os.environ.pop("ENGRAM_MEDITATE", None)

from connectors.mcp_connector import McpConnector  # noqa: E402
from kernel.core import meditation  # noqa: E402
from kernel.core.tooluse import run_with_tools  # noqa: E402
from kernel.core.types import Goal  # noqa: E402
from tactics.solo import SoloTactic  # noqa: E402


class DualGateway:
    """Streams a canned reply (plain path) and/or serves scripted completions
    (tool path). Records every system prompt it was handed."""
    name = "dual"

    def __init__(self, stream_reply="streamed answer.", complete_replies=None):
        self.stream_reply = stream_reply
        self._replies = list(complete_replies or [])
        self.systems: list[str] = []
        self.stream_calls = 0
        self.complete_calls = 0

    async def complete(self, messages, **opts):
        self.complete_calls += 1
        self.systems.append(opts.get("system", ""))
        return self._replies.pop(0) if self._replies else "done."

    async def stream(self, messages, system=None, **opts):
        self.stream_calls += 1
        self.systems.append(system or "")
        for word in self.stream_reply.split(" "):
            yield word + " "


class ReadyHub:
    """One ready tool; records the calls the loop actually made."""

    def __init__(self):
        self.called = []

    async def list_tools(self, **_scope):
        return [{"id": "fake.do", "connector": "fake", "tool": "do", "kind": "http", "ready": True}]

    async def call(self, tool_id, args, **_scope):
        self.called.append((tool_id, args))
        return {"ok": True, "result": "did the thing"}


class HangHub:
    """A ready tool whose call never returns — the failure that froze the run."""

    async def list_tools(self, **_scope):
        return [{"id": "slow.op", "connector": "slow", "tool": "op", "kind": "http", "ready": True}]

    async def call(self, tool_id, args, **_scope):
        await asyncio.sleep(30)  # unbounded stall; the guard must cut this
        return {"ok": True, "result": "never reached"}


async def _run_solo(gw, hub, *, meditate: bool):
    if meditate:
        meditation.activate()
    else:
        meditation.deactivate()

    async def _emit(_kind, _data):
        return None

    try:
        return await SoloTactic(gw).run(
            Goal(text="do the small true thing"),
            {"emit": _emit, "memory": [], "connectors": hub, "history": []},
        )
    finally:
        meditation.deactivate()


async def _part1_meditation_suppresses_tools() -> None:
    gw = DualGateway(stream_reply="Sitting with it, the answer is ready.")
    hub = ReadyHub()
    result = await _run_solo(gw, hub, meditate=True)

    assert hub.called == [], f"meditation must suppress connectors, but it called: {hub.called}"
    assert gw.stream_calls == 1 and gw.complete_calls == 0, \
        f"meditation must take the streamed plain path (stream={gw.stream_calls}, complete={gw.complete_calls})"
    assert any(meditation.MEDITATION_PRIOR in s for s in gw.systems), "posture missing from the system prompt"
    assert "ready" in result.candidates[0].text.lower(), f"answer not returned: {result.candidates[0].text!r}"


async def _part2_tools_work_off_meditation() -> None:
    gw = DualGateway(complete_replies=['Consider it.\nTOOL: fake.do {"q": 1}', "The work is complete."])
    hub = ReadyHub()
    result = await _run_solo(gw, hub, meditate=False)

    assert hub.called == [("fake.do", {"q": 1})], f"off-meditation tool loop not exercised: {hub.called}"
    assert gw.complete_calls == 2 and gw.stream_calls == 0, \
        f"off-meditation must take the tool loop (complete={gw.complete_calls}, stream={gw.stream_calls})"
    assert "complete" in result.candidates[0].text.lower(), f"answer not returned: {result.candidates[0].text!r}"


async def _part3_await_guard() -> None:
    gw = DualGateway(complete_replies=['TOOL: slow.op {}', "Answered without the stuck tool."])
    start = time.monotonic()
    out = await run_with_tools(gw, HangHub(), "go", tool_timeout=0.3)
    elapsed = time.monotonic() - start
    assert "answered" in out.lower(), f"run did not recover after the timeout: {out!r}"
    assert elapsed < 5.0, f"run did not return promptly (took {elapsed:.1f}s vs a 30s stall)"


async def _part4_mcp_offload() -> None:
    conn = McpConnector("fake", "http://127.0.0.1:1/mcp")
    # Stand in for a slow-but-blocking transport (connect + rpc). If call() ran it
    # inline it would freeze the loop; offloaded, the loop stays free.
    conn._call_sync = lambda tool, args: (time.sleep(0.4) or {"ok": True, "result": "x"})

    ticks = [0]

    async def _ticker():
        while True:
            await asyncio.sleep(0.02)
            ticks[0] += 1

    t = asyncio.create_task(_ticker())
    try:
        res = await conn.call("do", {})
    finally:
        t.cancel()
    assert res.get("ok") is True, f"offloaded call lost its result: {res}"
    assert ticks[0] >= 3, f"event loop was blocked during the call (ticks={ticks[0]})"


async def main() -> None:
    await _part1_meditation_suppresses_tools()
    await _part2_tools_work_off_meditation()
    await _part3_await_guard()
    await _part4_mcp_offload()

    meditation.deactivate()  # leave the process clean for any downstream import

    print("MEDITATION TOOL-PATH SMOKE: OK")
    print("  meditation      : suppresses connectors -> single streamed completion, posture carried")
    print("  off-meditation  : ready connector still drives the tool loop (suppression is scoped)")
    print("  await guard     : a cancellable async connector times out; run still answers")
    print("  mcp offload     : blocking transport leaves the event loop responsive")


if __name__ == "__main__":
    asyncio.run(main())
