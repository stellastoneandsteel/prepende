"""Thin retrieval over the same Postgres substrate.

Retrieval is commoditized; owning a thin RAG (embed -> vector search ->
optional hybrid/rerank -> assemble) keeps the core free of framework churn.
The index is a disposable projection of the vault — rebuildable any time.
LlamaIndex (MIT) is an optional adapter for heavy ingestion only, never a
core dependency.

SKELETON — Phase 1 (index built from the vault in Phase 2).
"""
