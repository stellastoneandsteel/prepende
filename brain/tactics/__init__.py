"""Tactics — the execution topologies the Strategist chooses among.

Implementations of kernel.contracts.Tactic + Resolver. The whole layer is
proprietary; permissive primitives (openai-agents-python MIT, agent-squad
Apache-2.0, agent-framework/Magentic MIT, MoA Apache-2.0) may be borrowed
*inside* a tactic, behind our interface.

Five starter tactics (lean, high-value; the interface makes more additive):
  1. solo            — one agent loop. The default; ~most goals land here.
  2. hierarchical    — manager-worker (Magentic task/progress-ledger pattern).
                       The workhorse for vague, open-ended goals.
  3. council_debate  — N independent answers + debate/vote -> decisive result.
                       For high-stakes correctness.
  4. parallel_explore— best-of-N attempts + scorer/judge. For generative work
                       with a verifier.
  5. pipeline        — fixed ordered stages. For known decompositions.

Resolvers (the decisiveness collapse): verifier > aggregator > judge/vote,
each emitting a confidence that can trigger escalation.

Later additions as new Tactic impls: Mixture-of-Agents, blackboard, market.

SKELETON — Strategist + solo + a basic resolver in Phase 0; richer tactics
(hierarchical, council, parallel-explore) in Phase 2, once durable execution
(Phase 1) and connectors (Phase 3) support parallel multi-agent runs.
"""
