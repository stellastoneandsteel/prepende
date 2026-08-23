#!/usr/bin/env python3
"""Focused launch suite for Prepende's reusable brain and knowledge core."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMOKES = (
    "smoke_phase0.py",
    "smoke_phase1.py",
    "smoke_phase1_durable.py",
    "smoke_phase2.py",
    "smoke_kernel_cli.py",
    "smoke_cli_gateway.py",
    "smoke_context_fast.py",
    "smoke_fast_lane_provider_independence.py",
    "smoke_continuity_v2.py",
    "smoke_protocol_v2_boundary.py",
    "smoke_prepende_naming.py",
    "smoke_model_defaults.py",
    "smoke_model_route.py",
    "smoke_model_thought_bus.py",
    "smoke_thought_bus.py",
    "smoke_meditation.py",
    "smoke_meditation_bridge.py",
    "smoke_meditation_tooluse.py",
    "smoke_loop_receipts.py",
    "smoke_memory_hybrid.py",
    "smoke_embedding_opt_in.py",
    "smoke_vault_rag.py",
    "smoke_rag_writer_integrity.py",
    "smoke_knowledge_scoped.py",
    "smoke_recall_graph.py",
    "smoke_knowledge_converge.py",
    "smoke_tenant_chat_rag.py",
    "smoke_graphify_recall.py",
    "smoke_mcp_auth_core.py",
    "smoke_mcp_capabilities.py",
    "smoke_mcp.py",
    "smoke_mcp_http_auth.py",
    "smoke_mcp_scope_isolation.py",
    "smoke_mint_tenant_token.py",
    "smoke_prepende_mcp_http.py",
    "smoke_prepende_mcp_stdio.py",
    "smoke_prepende_operator_receipts.py",
    "smoke_operational_status.py",
    "smoke_prepende_dependency_lock.py",
    "smoke_query_evidence_graph.py",
    "smoke_recovery_receipt_pipeline.py",
    "smoke_recovery_verifier.py",
    "smoke_reviewed_knowledge_bundle.py",
    "smoke_clone_env.py",
    "smoke_clone_bootstrap.py",
    "smoke_support_loop.py",
)

if (ROOT / "tests" / "smoke_standup_tenant_preflight.py").is_file():
    SMOKES += ("smoke_standup_tenant_preflight.py",)

if (ROOT / "prepende-export-manifest.json").is_file():
    SMOKES += ("smoke_clone_privacy.py",)
else:
    SMOKES += ("smoke_public_core_export.py",)


def main() -> int:
    state = Path(tempfile.mkdtemp(prefix="prepende_brain_verify_"))
    env = os.environ.copy()
    env.update({
        "MODEL_PROVIDER": "echo",
        "EMBEDDING_PROVIDER": "echo",
        "MEMORY_BACKEND": "sqlite",
        "DATABASE_URL": "",
        "PYTHONDONTWRITEBYTECODE": "1",
        "MEMORY_DB": str(state / "memory.db"),
        "RUNS_DB": str(state / "runs.db"),
        "SELF_IMPROVE_DB": str(state / "self-improvement.db"),
        "CONNECTOR_READINESS_DB": str(state / "connector-readiness.db"),
        "KNOWLEDGE_DB": str(state / "knowledge.db"),
        "WORKSPACE_ROOT": str(state / "workspaces"),
        "VAULT_PATH": str(state / "vault"),
        "VAULT_INDEX_PATH": str(state / "vault-index.db"),
        "GRAPHIFY_GRAPH_PATH": str(state / "graphify" / "graph.json"),
    })
    passed = 0
    for name in SMOKES:
        path = ROOT / "tests" / name
        if not path.is_file():
            print(f"[brain] MISSING {name}", file=sys.stderr)
            return 1
        print(f"[brain] {name}", flush=True)
        result = subprocess.run([sys.executable, str(path)], cwd=ROOT, env=env)
        if result.returncode:
            print(f"[brain] FAIL {name} ({result.returncode})", file=sys.stderr)
            return result.returncode
        passed += 1
    print(f"PREPENDE BRAIN VERIFY: OK ({passed}/{len(SMOKES)} smokes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
