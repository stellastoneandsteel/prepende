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

from kernel.contracts.retrieval import (  # noqa: E402
    RetrievalIdentity,
    RetrievalPlan,
    RetrievalQueryNode,
    RetrievalSearchResult,
    canonical_hash,
)
from kernel.core.retrieval_graph import (  # noqa: E402
    GatewayRetrievalPlanner,
    RetrievalGraphExecutor,
    RetrievalPlannerError,
    build_retrieval_plan,
    should_expand_retrieval,
)
from knowledge.scoped import bind_retrieval_scope  # noqa: E402


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


def retrieval_identity(
    scope: str = "tenant-a",
    *,
    workspace: str | None = None,
    physical: str | None = None,
    scope_id: str | None = None,
) -> RetrievalIdentity:
    physical = physical or scope
    return RetrievalIdentity(
        tenant_id=scope,
        workspace_id=workspace or scope.replace("tenant", "workspace"),
        scope_id=scope_id or scope,
        corpus_root_hash=canonical_hash({"corpus": physical}),
        index_path_hash=canonical_hash({"index": physical}),
        index_revision=canonical_hash({"revision": physical}),
        source_files=2,
        chunks=4,
    )


class ConcurrentSearch:
    def __init__(
        self,
        responses: dict[str, Any],
        *,
        identity: RetrievalIdentity | None = None,
    ) -> None:
        self.responses = responses
        self.identity = identity
        self.active = 0
        self.max_active = 0
        self.calls: list[str] = []
        self.requested_k: list[int] = []

    async def __call__(self, query: str, k: int):
        self.calls.append(query)
        self.requested_k.append(k)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            response = self.responses[query]
            if isinstance(response, BaseException):
                raise response
            if self.identity is not None:
                return RetrievalSearchResult(tuple(response), self.identity)
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


class SlowPlannerGateway(PlannerGateway):
    async def complete(self, _messages, **options):
        self.calls += 1
        self.options = options
        await asyncio.sleep(0.05)
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
    def __init__(
        self,
        responses: dict[str, Any],
        *,
        identity: RetrievalIdentity | None = None,
    ) -> None:
        self.rag = FakeRag()
        self.searcher = ConcurrentSearch(responses)
        self.prepare_calls = 0
        self.identity = identity or retrieval_identity("tenant-a")
        bind_retrieval_scope(
            self,
            tenant_id=self.identity.tenant_id,
            workspace_id=self.identity.workspace_id,
            scope_id=self.identity.scope_id,
        )

    def _physical_identity(self) -> dict[str, Any]:
        value = self.identity.as_dict()
        return {
            key: value[key]
            for key in (
                "corpusRootHash",
                "indexPathHash",
                "indexRevision",
                "sourceFiles",
                "chunks",
            )
        }

    async def prepare_search(self) -> None:
        self.prepare_calls += 1

    async def search_prepared(self, query: str, k: int = 8):
        return await self.searcher(query, k)

    async def search_prepared_with_identity(self, query: str, k: int = 8):
        return await self.searcher(query, k), self._physical_identity()

    async def retrieval_identity(self) -> dict[str, Any]:
        return self._physical_identity()

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
    assert skipped_receipt["roundsUsed"] == 1, skipped_receipt
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

    # Global relevance cannot let an early, high-volume branch consume the
    # whole result budget before a successful recovery branch contributes.
    fair_plan = RetrievalPlan(
        plan_id="fair",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        nodes=(
            RetrievalQueryNode("alpha", "alpha flood", k=2),
            RetrievalQueryNode("beta", "beta empty", k=2),
            RetrievalQueryNode(
                "fallback",
                "critical fallback",
                depends_on=("alpha", "beta"),
                run_if="any_dependency_empty",
                k=2,
            ),
        ),
        max_parallelism=2,
        max_rounds=2,
        max_hits=2,
    )
    fair_search = ConcurrentSearch({
        "alpha flood": [
            hit("alpha-1", "alpha strongest", 0.99),
            hit("alpha-2", "alpha second", 0.98),
            hit("alpha-3", "alpha over k", 0.97),
        ],
        "beta empty": [],
        "critical fallback": [hit("fallback", "critical recovery", 0.1)],
    })
    fair = await RetrievalGraphExecutor(fair_search).run(fair_plan)
    fair_receipt = fair.receipt.as_dict()
    assert [item["page"] for item in fair.hits] == ["alpha-1", "fallback"], fair.hits
    assert fair_receipt["branchesCovered"] == ["alpha", "fallback"], fair_receipt
    assert fair_receipt["branchesStarved"] == [], fair_receipt
    alpha_receipt = next(
        item for item in fair_receipt["nodes"] if item["nodeId"] == "alpha"
    )
    assert alpha_receipt["requestedK"] == 2, alpha_receipt
    assert alpha_receipt["accepted"] == 2 and alpha_receipt["budgetRejected"] == 1

    bounded_plan = RetrievalPlan(
        plan_id="aggregate-bounds",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        nodes=(RetrievalQueryNode("bounded", "bounded query", k=2),),
        max_rounds=1,
        max_hits=2,
        max_content_chars=15,
        max_metadata_bytes=64_000,
    )
    bounded = await RetrievalGraphExecutor(ConcurrentSearch({
        "bounded query": [
            hit("one", "1234567890", 0.9),
            hit("two", "abcdefghij", 0.8),
            hit("three", "not inspected", 0.7),
        ],
    })).run(bounded_plan)
    bounded_receipt = bounded.receipt.as_dict()
    assert len(bounded.hits) == 1, bounded.hits
    assert bounded_receipt["returnedContentChars"] == 10, bounded_receipt
    assert bounded_receipt["budgetDropped"] == 1, bounded_receipt
    print(
        "OK runtime: parallel recovery, fair global selection, per-node and aggregate bounds"
    )


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

    bounded_goal = "A" * 8_001
    try:
        await planner.plan_queries(bounded_goal)
    except RetrievalPlannerError as exc:
        assert "exceeds" in str(exc)
    else:
        raise AssertionError("oversized planner input was accepted")
    assert planner.last_model_calls == 0, planner.last_model_calls
    assert gateway.calls == 1, gateway.calls
    timed = GatewayRetrievalPlanner(
        SlowPlannerGateway('{"queries": [{"query": "late"}]}'), timeout_ms=5
    )
    try:
        await timed.plan_queries("bounded timeout goal")
    except RetrievalPlannerError as exc:
        assert "timed out" in str(exc)
    else:
        raise AssertionError("planner timeout was not enforced")
    assert timed.last_model_calls == 1, timed.last_model_calls

    long_goal = f"{'A' * 2_100}? {'B' * 2_100}?"
    long_plan = build_retrieval_plan(
        long_goal,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
    )
    fallback = next(node for node in long_plan.nodes if node.node_id == "query-fallback")
    assert len(fallback.query) == 2_000, len(fallback.query)
    print("OK schemas: bounded planner, timeout, and consistent fallback truncation")


async def identity_and_provenance_checks() -> None:
    identity_a = retrieval_identity("tenant-a")
    identity_b = retrieval_identity("tenant-b", physical="tenant-b")
    plan = RetrievalPlan(
        plan_id="identity",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        nodes=(RetrievalQueryNode("root", "identity query"),),
        max_rounds=1,
        identity=identity_a,
    )

    correct_search = ConcurrentSearch(
        {"identity query": [hit("alpha", "same-scope fact")]},
        identity=identity_a,
    )
    correct = await RetrievalGraphExecutor(
        correct_search, search_identity=identity_a
    ).run(plan)
    correct_receipt = correct.receipt.as_dict()
    assert correct_receipt["identityVerified"] is True, correct_receipt
    assert correct_receipt["retrievalIdentity"] == identity_a.as_dict()
    assert correct.hits[0]["retrievalProvenance"] == identity_a.as_dict()

    # The actual corpus handle is tenant B. A tenant-A plan must stop before a
    # search call, with zero rounds and no foreign hit material admitted.
    foreign_search = ConcurrentSearch(
        {"identity query": [hit("foreign", "tenant-b secret")]},
        identity=identity_b,
    )
    foreign = await RetrievalGraphExecutor(
        foreign_search, search_identity=identity_b
    ).run(plan)
    foreign_receipt = foreign.receipt.as_dict()
    assert foreign.hits == (), foreign.hits
    assert foreign_search.calls == [], foreign_search.calls
    assert foreign_receipt["stopReason"] == "identity_mismatch", foreign_receipt
    assert foreign_receipt["roundsUsed"] == 0, foreign_receipt
    assert foreign_receipt["maxObservedParallelism"] == 0, foreign_receipt

    # Even with a correct response-level snapshot, one adversarial hit claiming
    # another tenant invalidates and withholds the complete graph response.
    poisoned_hit = {
        **hit("poison", "cross-scope claim"),
        "tenantId": "tenant-a",
        "tenant": "tenant-b",
    }
    poisoned_search = ConcurrentSearch(
        {"identity query": [poisoned_hit]}, identity=identity_a
    )
    poisoned = await RetrievalGraphExecutor(
        poisoned_search, search_identity=identity_a
    ).run(plan)
    poisoned_receipt = poisoned.receipt.as_dict()
    assert poisoned.hits == (), poisoned.hits
    assert poisoned_receipt["stopReason"] == "identity_mismatch", poisoned_receipt
    node = poisoned_receipt["nodes"][0]
    assert node["provenanceRejected"] == 1 and node["accepted"] == 0, node

    conflicting_reserved = identity_a.as_dict()
    conflicting_reserved["tenant"] = "tenant-b"
    top_level_reserved = {
        **hit("top-reserved-poison", "conflicting reserved top-level claim"),
        "retrievalProvenance": conflicting_reserved,
    }
    top_level_search = ConcurrentSearch(
        {"identity query": [top_level_reserved]}, identity=identity_a
    )
    top_level = await RetrievalGraphExecutor(
        top_level_search, search_identity=identity_a
    ).run(plan)
    assert top_level.receipt.stop_reason == "identity_mismatch", (
        top_level.receipt.as_dict()
    )
    assert top_level.hits == ()

    nested_poison = {
        **hit("nested-poison", "conflicting reserved claim"),
        "metadata": {"retrievalProvenance": conflicting_reserved},
    }
    nested_search = ConcurrentSearch(
        {"identity query": [nested_poison]}, identity=identity_a
    )
    nested = await RetrievalGraphExecutor(
        nested_search, search_identity=identity_a
    ).run(plan)
    assert nested.receipt.stop_reason == "identity_mismatch", nested.receipt.as_dict()
    assert nested.hits == ()

    # A foreign sibling invalidates the graph, but valid hits withheld for that
    # security reason must not be mislabeled as budget drops or starvation.
    mixed_plan = RetrievalPlan(
        plan_id="mixed-identity",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        nodes=(
            RetrievalQueryNode("valid", "valid identity query"),
            RetrievalQueryNode("foreign", "foreign identity query"),
        ),
        max_parallelism=2,
        max_rounds=1,
        identity=identity_a,
    )

    async def mixed_search(query: str, _k: int) -> RetrievalSearchResult:
        if query == "valid identity query":
            return RetrievalSearchResult(
                (hit("valid", "valid same-scope evidence"),), identity_a
            )
        return RetrievalSearchResult(
            (hit("foreign", "foreign tenant evidence"),), identity_b
        )

    mixed = await RetrievalGraphExecutor(
        mixed_search, search_identity=identity_a
    ).run(mixed_plan)
    mixed_receipt = mixed.receipt.as_dict()
    assert mixed.hits == (), mixed.hits
    assert mixed_receipt["stopReason"] == "identity_mismatch", mixed_receipt
    assert mixed_receipt["budgetDropped"] == 0, mixed_receipt
    assert mixed_receipt["branchesStarved"] == [], mixed_receipt
    print("OK identity: two-scope preflight and adversarial hit provenance fail closed")


async def recall_integration_checks() -> None:
    from kernel.core.recall import unified_recall

    planner = GatewayRetrievalPlanner(PlannerGateway(json.dumps({
        "queries": [{"query": "alpha evidence"}, {"query": "beta evidence"}],
    })))
    knowledge = FakeKnowledge(
        {
            "alpha evidence": [hit("alpha", "alpha fact")],
            "beta evidence": [hit("beta", "beta fact")],
        },
        identity=retrieval_identity("tenant-a", scope_id="memory-scope"),
    )
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
    assert graph["identityVerified"] is True, graph
    assert graph["retrievalIdentity"]["scopeId"] == "memory-scope", graph
    assert graph["parallelGroups"] == [["query-1", "query-2"]], graph
    assert graph["planning"]["status"] == "planned"
    assert recalled["sources"]["vault"] == 2, recalled

    fallback_goal = "What supports alpha? What contradicts beta?"
    fallback_knowledge = FakeKnowledge(
        {
            "What supports alpha?": [hit("alpha", "alpha support")],
            "What contradicts beta?": [hit("beta", "beta contradiction")],
        },
        identity=retrieval_identity("tenant-a", scope_id="memory-scope"),
    )
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

    foreign_knowledge = FakeKnowledge(
        {"alpha evidence": [hit("foreign", "foreign tenant fact")]},
        identity=retrieval_identity("tenant-b", scope_id="memory-scope"),
    )
    foreign = await unified_recall(
        "alpha evidence",
        knowledge=foreign_knowledge,
        scope="memory-scope",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        vault=True,
        query_graph=True,
        query_planner=GatewayRetrievalPlanner(PlannerGateway(json.dumps({
            "queries": [{"query": "alpha evidence"}],
        }))),
    )
    foreign_graph = foreign["selection"]["queryGraph"]
    assert foreign_graph["stopReason"] == "identity_mismatch", foreign_graph
    assert foreign_graph["roundsUsed"] == 0, foreign_graph
    assert foreign_knowledge.searcher.calls == [], foreign_knowledge.searcher.calls
    assert foreign["sources"]["vault"] == 0, foreign
    print("OK recall: atomic scoped snapshots, deterministic fallback, two-scope rejection")


async def goal_loop_fail_closed_check() -> None:
    from kernel.core.loop import GoalLoop

    goal = "What supports alpha? What contradicts beta?"
    gateway = PlannerGateway(json.dumps({
        "queries": [{"query": "alpha support"}, {"query": "beta contradiction"}],
    }))
    knowledge = FakeKnowledge(
        {
            "alpha support": [],
            "beta contradiction": [],
            goal: [],
        },
        identity=retrieval_identity("tenant-a", scope_id="memory-scope"),
    )
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
    await identity_and_provenance_checks()
    if PRIVATE_INTEGRATION:
        await recall_integration_checks()
        await goal_loop_fail_closed_check()
    else:
        print("OK public boundary: private recall and GoalLoop wiring excluded")
    print("\nQUERY EVIDENCE GRAPH SMOKE: OK")


if __name__ == "__main__":
    asyncio.run(main())
