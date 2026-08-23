"""Bounded query-evidence DAG: public core plus private GoalLoop wiring.

All dependencies are deterministic fakes.  No network, provider, memory write,
connector, approval, or external action is available to this smoke.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.contracts.retrieval import RetrievalPlan, RetrievalQueryNode  # noqa: E402
from kernel.core.retrieval_graph import (  # noqa: E402
    GatewayRetrievalPlanner,
    RetrievalGraphExecutor,
    RetrievalPlannerError,
    build_retrieval_plan,
    should_expand_retrieval,
)


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_INTEGRATION = (ROOT / "prepende-export-manifest.json").is_file()


def hit(page: str, content: str, score: float = 0.9) -> dict[str, Any]:
    return {
        "page": page,
        "section": "Evidence",
        "path": f"wiki/{page}.md",
        "content": content,
        "score": score,
    }


class ConcurrentSearch:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.active = 0
        self.max_active = 0
        self.calls: list[str] = []

    async def __call__(self, query: str, _k: int):
        self.calls.append(query)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            response = self.responses[query]
            if isinstance(response, BaseException):
                raise response
            return response
        finally:
            self.active -= 1


class PlannerGateway:
    name = "planner-fake"

    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls = 0
        self.options: dict[str, Any] = {}

    async def complete(self, _messages, **options):
        self.calls += 1
        self.options = options
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class FakeRag:
    def status(self) -> dict[str, Any]:
        return {
            "source_files": 2,
            "indexed_files": 2,
            "chunks": 4,
            "lexical_ready": True,
            "semantic_ready": True,
            "stale": False,
        }


class FakeKnowledge:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.rag = FakeRag()
        self.searcher = ConcurrentSearch(responses)
        self.prepare_calls = 0

    async def prepare_search(self) -> None:
        self.prepare_calls += 1

    async def search_prepared(self, query: str, k: int = 8):
        return await self.searcher(query, k)

    async def search(self, query: str, k: int = 8):
        raise AssertionError("parallel graph must use one prepared index snapshot")

    async def related(self, _page: str, depth: int = 1):
        return []

    async def read_page(self, _page: str):
        return ""


class FakeWorkspace:
    def __init__(self, root: str) -> None:
        self.root = root

    async def open(self, _goal_id: str) -> None:
        return None

    async def progress(self, _goal_id: str, _message: str) -> None:
        return None


class ShouldNotRoute:
    async def choose(self, *_args, **_kwargs):
        raise AssertionError("missing required evidence must block before routing")


async def contract_checks() -> None:
    try:
        RetrievalPlan(
            plan_id="cycle",
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            nodes=(
                RetrievalQueryNode("a", "alpha evidence", depends_on=("b",)),
                RetrievalQueryNode("b", "beta evidence", depends_on=("a",)),
            ),
        )
    except ValueError as exc:
        assert "acyclic" in str(exc)
    else:
        raise AssertionError("cyclic retrieval graph was accepted")

    try:
        RetrievalPlan(
            plan_id="deep",
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            nodes=(
                RetrievalQueryNode("a", "alpha evidence"),
                RetrievalQueryNode("b", "beta evidence", depends_on=("a",)),
                RetrievalQueryNode("c", "gamma evidence", depends_on=("b",)),
            ),
            max_rounds=2,
        )
    except ValueError as exc:
        assert "max_rounds" in str(exc)
    else:
        raise AssertionError("unbounded retrieval depth was accepted")

    assert should_expand_retrieval(
        "What supports alpha? What contradicts beta?",
        require_evidence=False,
    ) is True
    assert should_expand_retrieval(
        "What supports alpha?",
        require_evidence=False,
    ) is False
    assert should_expand_retrieval(
        "What supports alpha?",
        require_evidence=True,
    ) is True
    print("OK contracts: acyclic graph, two-round ceiling, structural routing")


async def runtime_checks() -> None:
    plan = RetrievalPlan(
        plan_id="runtime",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        nodes=(
            RetrievalQueryNode("alpha", "alpha query"),
            RetrievalQueryNode("beta", "beta query"),
            RetrievalQueryNode(
                "fallback",
                "fallback query",
                depends_on=("alpha", "beta"),
                run_if="any_dependency_empty",
            ),
        ),
        max_parallelism=2,
        max_rounds=2,
        max_hits=4,
    )
    search = ConcurrentSearch({
        "alpha query": [hit("alpha", "shared fact")],
        "beta query": [],
        "fallback query": [
            hit("duplicate", "shared fact", 0.8),
            hit("gamma", "recovered fact", 0.7),
        ],
    })
    run = await RetrievalGraphExecutor(search).run(plan)
    receipt = run.receipt.as_dict()
    assert search.max_active == 2, search.max_active
    assert receipt["parallelGroups"] == [["alpha", "beta"], ["fallback"]], receipt
    assert receipt["maxObservedParallelism"] == 2, receipt
    assert receipt["roundsUsed"] == 2 and receipt["stopReason"] == "pass", receipt
    assert receipt["duplicatesDropped"] == 1 and len(run.hits) == 2, run
    assert run.hits[0]["retrievalNodeIds"] == ["alpha", "fallback"], run.hits
    assert receipt["externalActions"] == [] and receipt["actionExecuted"] is False
    assert receipt["durableMemoryWrite"] is False

    skip_search = ConcurrentSearch({
        "alpha query": [hit("alpha", "alpha fact")],
        "beta query": [hit("beta", "beta fact")],
    })
    skipped = await RetrievalGraphExecutor(skip_search).run(plan)
    skipped_receipt = skipped.receipt.as_dict()
    fallback = next(item for item in skipped_receipt["nodes"] if item["nodeId"] == "fallback")
    assert fallback["status"] == "skipped", fallback
    assert skipped_receipt["parallelGroups"] == [["alpha", "beta"]]
    assert "fallback query" not in skip_search.calls

    isolated_failure = ConcurrentSearch({
        "alpha query": RuntimeError("sensitive backend detail"),
        "beta query": [hit("beta", "beta fact")],
        "fallback query": [hit("recovery", "recovered alpha fact")],
    })
    partial = await RetrievalGraphExecutor(isolated_failure).run(plan)
    partial_receipt = partial.receipt.as_dict()
    alpha = next(item for item in partial_receipt["nodes"] if item["nodeId"] == "alpha")
    assert partial_receipt["stopReason"] == "partial", partial_receipt
    assert partial_receipt["parallelGroups"] == [["alpha", "beta"], ["fallback"]]
    assert alpha["status"] == "failed" and alpha["error"] == "retrieval_error:RuntimeError"
    assert "sensitive backend detail" not in json.dumps(partial_receipt)
    assert {item["page"] for item in partial.hits} == {"beta", "recovery"}
    print("OK runtime: real parallelism, conditional recovery, failure isolation, dedupe")


async def schema_and_planner_checks() -> None:
    required = RetrievalPlan(
        plan_id="schema",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        nodes=(RetrievalQueryNode("required", "required query", required=True),),
        max_rounds=1,
    )
    invalid = ConcurrentSearch({"required query": {"not": "an array"}})
    failed = await RetrievalGraphExecutor(invalid).run(required)
    receipt = failed.receipt.as_dict()
    assert receipt["stopReason"] == "required_node_failed", receipt
    assert receipt["nodes"][0]["status"] == "failed", receipt

    gateway = PlannerGateway(json.dumps({
        "queries": [{"query": "alpha evidence"}, {"query": "beta contradiction"}],
    }))
    planner = GatewayRetrievalPlanner(gateway)
    queries = await planner.plan_queries("Compare alpha and beta")
    assert queries == ("alpha evidence", "beta contradiction"), queries
    assert gateway.calls == 1 and gateway.options["tool_policy"] == "none"
    assert gateway.options["output_schema"]["additionalProperties"] is False

    malformed = GatewayRetrievalPlanner(PlannerGateway('{"queries": [{"query": "x", "tool": "bad"}]}'))
    try:
        await malformed.plan_queries("goal")
    except RetrievalPlannerError:
        pass
    else:
        raise AssertionError("planner authority expansion was accepted")
    print("OK schemas: invalid search output fails; planner is query-only and tool-less")


async def recall_integration_checks() -> None:
    from kernel.core.recall import unified_recall

    planner = GatewayRetrievalPlanner(PlannerGateway(json.dumps({
        "queries": [{"query": "alpha evidence"}, {"query": "beta evidence"}],
    })))
    knowledge = FakeKnowledge({
        "alpha evidence": [hit("alpha", "alpha fact")],
        "beta evidence": [hit("beta", "beta fact")],
    })
    recalled = await unified_recall(
        "Compare alpha? Compare beta?",
        knowledge=knowledge,
        scope="memory-scope",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        vault=True,
        query_graph=True,
        query_planner=planner,
    )
    graph = recalled["selection"]["queryGraph"]
    assert knowledge.prepare_calls == 1, knowledge.prepare_calls
    assert knowledge.searcher.max_active == 2, knowledge.searcher.max_active
    assert graph["tenantId"] == "tenant-a" and graph["workspaceId"] == "workspace-a"
    assert graph["parallelGroups"] == [["query-1", "query-2"]], graph
    assert graph["planning"]["status"] == "planned"
    assert recalled["sources"]["vault"] == 2, recalled

    fallback_goal = "What supports alpha? What contradicts beta?"
    fallback_knowledge = FakeKnowledge({
        "What supports alpha?": [hit("alpha", "alpha support")],
        "What contradicts beta?": [hit("beta", "beta contradiction")],
    })
    fallback = await unified_recall(
        fallback_goal,
        knowledge=fallback_knowledge,
        scope="memory-scope",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        vault=True,
        query_graph=True,
        query_planner=GatewayRetrievalPlanner(PlannerGateway("not-json")),
    )
    fallback_graph = fallback["selection"]["queryGraph"]
    assert fallback_graph["planning"]["status"] == "fallback", fallback_graph
    assert fallback_graph["parallelGroups"] == [["query-1", "query-2"]], fallback_graph
    assert fallback_knowledge.prepare_calls == 1
    print("OK recall: one refreshed index, scoped parallel graph, deterministic fallback")


async def goal_loop_fail_closed_check() -> None:
    from kernel.core.loop import GoalLoop

    goal = "What supports alpha? What contradicts beta?"
    gateway = PlannerGateway(json.dumps({
        "queries": [{"query": "alpha support"}, {"query": "beta contradiction"}],
    }))
    knowledge = FakeKnowledge({
        "alpha support": [],
        "beta contradiction": [],
        goal: [],
    })
    with tempfile.TemporaryDirectory(prefix="prepende_query_graph_") as tmp:
        loop = GoalLoop(
            gateway,
            ShouldNotRoute(),
            FakeWorkspace(tmp),
            scope="memory-scope",
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            knowledge=knowledge,
            vault_recall=True,
        )
        events: list[dict[str, Any]] = []

        async def on_event(event):
            events.append(event)

        receipt = await loop.run(goal, on_event, require_evidence=True)
    assert receipt["resultStatus"] == "blocked_missing_evidence", receipt
    graph = receipt["recall"]["selection"]["queryGraph"]
    assert graph["parallelGroups"] == [
        ["query-1", "query-2"], ["query-fallback"],
    ], graph
    assert graph["stopReason"] == "no_evidence", graph
    assert receipt["externalActions"] == [] and receipt["actionExecuted"] is False
    assert any(event.get("type") == "error" for event in events)
    print("OK GoalLoop: empty parallel fleet blocks before tactic or synthesis")


async def main() -> None:
    await contract_checks()
    await runtime_checks()
    await schema_and_planner_checks()
    if PRIVATE_INTEGRATION:
        await recall_integration_checks()
        await goal_loop_fail_closed_check()
    else:
        print("OK public boundary: private recall and GoalLoop wiring excluded")
    print("\nQUERY EVIDENCE GRAPH SMOKE: OK")


if __name__ == "__main__":
    asyncio.run(main())
