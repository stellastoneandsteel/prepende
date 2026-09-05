"""Public-core privacy contract: status never queries unscoped run history."""
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kernel.core.introspect import brain_state


class Journal:
    rows = [
        {"scope": "reader", "workspace_id": "desk", "goal": "visible", "status": "done"},
        {"scope": "other", "workspace_id": "desk", "goal": "private account", "status": "done"},
        {"scope": "reader", "workspace_id": "other", "goal": "private workspace", "status": "done"},
    ]

    def recent(self, n, *, scope=None, workspace_id=None):
        return [r for r in self.rows if (scope is None or r["scope"] == scope)
                and (workspace_id is None or r["workspace_id"] == workspace_id)][:n]


async def main():
    loop = SimpleNamespace(gateway=SimpleNamespace(name="test"), runs=Journal(), workspace_id="desk")
    state = await brain_state(loop, "reader")
    assert state["runs"]["recent"] == [{"goal": "visible", "status": "done"}]
    assert (await brain_state(loop, "missing"))["runs"]["recent_count"] == 0
    class Legacy:
        def recent(self, n):
            raise AssertionError("unsafe compatibility query must never run")
    loop.runs = Legacy()
    assert "error" in (await brain_state(loop, "reader"))["runs"]
    print("SCOPED INTROSPECTION: PASS")


if __name__ == "__main__":
    asyncio.run(main())
