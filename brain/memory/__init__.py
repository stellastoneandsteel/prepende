"""MemoryStore implementation over Postgres (pgvector + Apache AGE + RLS).

One substrate for memory, RAG vectors, and the knowledge graph. RLS is the
shared-but-scoped boundary ("one brain, many agents, scoped correctly").
Mem0 (Apache-2.0) is the validated reference impl that can drop behind the
same interface; Qdrant (Apache-2.0) is the escape hatch past ~tens of
millions of vectors.

SKELETON — Phase 1.
"""
