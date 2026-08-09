"""agents — the knowledge-gathering scout layer.

Autonomous agents that gather, verify, distill, and propose external information
for the user's knowledge base — never silently writing it. Everything they
produce lands in the KnowledgeItemStore as `pending_review`; only explicit human
approval promotes it into durable memory + the vault.

Agents (this pass):
  - ResearchAgent      — gather + summarize + extract claims/confidence on a topic
  - SourceVerifyAgent  — score credibility (source/recency/authority/evidence)
  - (Watchtower, Distill, GraphIntegration scaffolded; see KNOWLEDGE-AGENTS.md)

Separation: agents take generic topics/projects/entities the USER supplies; no
product or client names are baked into the code (SEPARATION.md).
"""
