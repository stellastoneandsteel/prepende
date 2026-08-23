"""PromptRegistry implementation — git-versioned prompt files + a tiny registry.

Prompt artifacts live here as versioned files (git = versioning, diff, audit,
rollback for free). The registry resolves the active version with a per-tenant
runtime override row in Postgres. BAML (Apache-2.0) for typed structured-output
prompts. Langfuse (MIT core) optional and OFF the request path.

SKELETON — Phase 4.
"""
