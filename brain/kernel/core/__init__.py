"""kernel.core — orchestration, routing, policy/guardrails, and the Goal Loop.

THE GOAL LOOP is the crown jewel and the north star: take a goal stated with
little or no context and figure out how to pursue it — decompose it, research
the world (market, trends, future implications), reason around corners, plan,
act (via Connectors), check itself, and repeat. This loop IS the product.

EARLY in the loop, the Strategist (kernel.contracts.Strategist) decides HOW to
think before doing the work: it picks an execution Tactic — solo, hierarchical
(manager-worker), council/debate, parallel-explore, or pipeline — and a
Resolver that collapses many agent outputs into ONE decisive result. Start
solo; escalate to heavier tactics only on low confidence or failure. This is
the "swarm decisiveness" layer, and it is OURS.

It is small and OURS on purpose: no agent framework owns it. Borrowed
primitives (LlamaIndex Workflows, OpenAI Agents SDK — both MIT) may be used as
libraries here, never as the spine. Long runs are durable and resumable (via
the DurableExecution port) so a goal can be pursued across hours and crashes.

SKELETON — the loop's skeleton lands in Phase 0; durability in Phase 1.
"""
