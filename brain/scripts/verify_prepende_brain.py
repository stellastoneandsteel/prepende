#!/usr/bin/env python3
"""Focused launch suite for Prepende's reusable brain and knowledge core."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASELINE_SMOKES = (
    "smoke_phase0.py",
    "smoke_phase1.py",
    "smoke_phase1_durable.py",
    "smoke_phase2.py",
    "smoke_kernel_cli.py",
    "smoke_cli_gateway.py",
    "smoke_context_fast.py",
    "smoke_context_fast_import_graph.py",
    "smoke_fast_lane_provider_independence.py",
    "smoke_smoke_gate_completeness.py",
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
    "smoke_mcp.py",
    "smoke_prepende_mcp_http.py",
    "smoke_mcp_auth_core.py",
    "smoke_mcp_capabilities.py",
    "smoke_mcp_http_auth.py",
    "smoke_mcp_scope_isolation.py",
    "smoke_prepende_mcp_stdio.py",
    "smoke_mint_tenant_token.py",
    "smoke_prepende_operator_receipts.py",
    "smoke_operational_status.py",
    "smoke_recovery_receipt_pipeline.py",
    "smoke_recovery_verifier.py",
    "smoke_reviewed_knowledge_bundle.py",
    "smoke_prepende_dependency_lock.py",
    "smoke_query_evidence_graph.py",
    "smoke_clone_env.py",
    "smoke_clone_bootstrap.py",
    "smoke_support_loop.py",
)

_EXCLUSION_REASONS: dict[str, str] = {
    "smoke_phase1_durable.py": (
        "Reviewed public-core exclusion: this smoke exercises a private-overlay surface that the public core intentionally does not carry, so it is absent from a public-core export and cannot run there."
    ),
    "smoke_kernel_cli.py": (
        "Reviewed public-core exclusion: this smoke exercises a private-overlay surface that the public core intentionally does not carry, so it is absent from a public-core export and cannot run there."
    ),
    "smoke_model_route.py": (
        "Reviewed public-core exclusion: this smoke exercises a private-overlay surface that the public core intentionally does not carry, so it is absent from a public-core export and cannot run there."
    ),
    "smoke_meditation_tooluse.py": (
        "Reviewed public-core exclusion: this smoke exercises a private-overlay surface that the public core intentionally does not carry, so it is absent from a public-core export and cannot run there."
    ),
    "smoke_loop_receipts.py": (
        "Reviewed public-core exclusion: this smoke exercises a private-overlay surface that the public core intentionally does not carry, so it is absent from a public-core export and cannot run there."
    ),
    "smoke_embedding_opt_in.py": (
        "Reviewed public-core exclusion: this smoke exercises a private-overlay surface that the public core intentionally does not carry, so it is absent from a public-core export and cannot run there."
    ),
    "smoke_mcp.py": (
        "Reviewed public-core exclusion: this smoke exercises a private-overlay surface that the public core intentionally does not carry, so it is absent from a public-core export and cannot run there."
    ),
    "smoke_prepende_mcp_http.py": (
        "Reviewed public-core exclusion: this smoke exercises a private-overlay surface that the public core intentionally does not carry, so it is absent from a public-core export and cannot run there."
    ),
    "smoke_prepende_mcp_stdio.py": (
        "Reviewed public-core exclusion: this smoke exercises a private-overlay surface that the public core intentionally does not carry, so it is absent from a public-core export and cannot run there."
    ),
    "smoke_standup_tenant_preflight.py": (
        "Reviewed conditional: this smoke validates an optional tenant preflight setup and "
        "runs only when the file exists."
    ),
    "smoke_clone_privacy.py": (
        "Reviewed public-core exclusion: private/customer clone verification requires "
        "`prepende-export-manifest.json`, which is intentionally not exported here."
    ),
    "smoke_public_core_export.py": (
        "Reviewed private-clone exclusion: public-core export verification is replaced by "
        "the private/customer clone privacy gate when that reviewed policy is present."
    ),
}


def discover_smoke_files(root: Path) -> list[str]:
    """Return all deterministic `smoke_*.py` files discovered under the repository.

    Determinism is important here: missing a file or changing execution order must
    not be possible by filesystem reordering.
    """
    tests_root = root / "tests"
    return sorted(
        path.relative_to(tests_root).as_posix()
        for path in tests_root.rglob("smoke_*.py")
        if path.is_file()
    )


def reviewed_exclusion(name: str) -> str:
    """Return the source-reviewed reason for skipping ``name``, or refuse to skip it.

    Every exclusion must be justified by a literal in `_EXCLUSION_REASONS`. This is
    the only way a discovered smoke may go unexecuted, so it fails closed rather
    than inventing a reason at the call site.
    """
    reason = str(_EXCLUSION_REASONS.get(name, "")).strip()
    if not reason:
        raise ValueError(f"unreviewed smoke exclusion: {name}")
    return reason


def resolve_smoke_suite(root: Path) -> tuple[list[str], list[str], list[str], dict[str, str]]:
    """Resolve an executable smoke suite and classify every discovered smoke file.

    Returns:
      - executable smoke list in deterministic execution order
      - missing required smoke files
      - unregistered discovered smoke files
      - explicitly excluded smoke files with reviewed reasons
    """
    executable = list(BASELINE_SMOKES)
    exclusions: dict[str, str] = {}
    if (root / "tests" / "smoke_standup_tenant_preflight.py").is_file():
        executable.append("smoke_standup_tenant_preflight.py")
    else:
        exclusions["smoke_standup_tenant_preflight.py"] = reviewed_exclusion(
            "smoke_standup_tenant_preflight.py"
        )

    private_clone = (root / "prepende-export-manifest.json").is_file()
    public_core = (root / "prepende-public-core-manifest.json").is_file()
    # The private source checkout intentionally contains both policies. Its
    # stricter customer-clone profile takes precedence; a public-core export
    # separately proves that the private policy was not exported.
    if private_clone:
        executable.append("smoke_clone_privacy.py")
        exclusions["smoke_public_core_export.py"] = reviewed_exclusion(
            "smoke_public_core_export.py"
        )
    elif public_core:
        executable.append("smoke_public_core_export.py")
        exclusions["smoke_clone_privacy.py"] = reviewed_exclusion("smoke_clone_privacy.py")
    else:
        raise ValueError("no reviewed smoke profile manifest is present")

    executable = list(dict.fromkeys(executable))
    available = discover_smoke_files(root)
    available_set = set(available)
    # A public-core export carries only the public suite. Smokes that exercise
    # a private-overlay surface are absent there by design, so classify them as
    # reviewed exclusions instead of reporting them missing -- but only in that
    # profile, and only with a literal reason, so a genuinely missing smoke in
    # any other tree still fails closed.
    if public_core and not private_clone:
        for name in list(executable):
            if name not in available_set:
                exclusions[name] = reviewed_exclusion(name)
        executable = [name for name in executable if name in available_set]
    executable_set = set(executable)
    missing = [name for name in executable if name not in available_set]
    unregistered = [
        name
        for name in available
        if name not in executable_set and name not in exclusions
    ]
    return executable, missing, unregistered, exclusions


def summarize_registry(root: Path) -> dict[str, Any]:
    discovered = discover_smoke_files(root)
    executable, missing, unregistered, reviewed_exclusions = resolve_smoke_suite(
        root,
    )
    return {
        "discovered": discovered,
        "executable": executable,
        "missing": missing,
        "unknown": unregistered,
        "excluded": reviewed_exclusions,
    }


def run_smoke_suite(root: Path, smokes: list[str], *, env: dict[str, str] | None = None) -> int:
    state = Path(tempfile.mkdtemp(prefix="prepende_brain_verify_"))
    env = dict(os.environ if env is None else env)
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

    # The suite aborts on the first failure, so a completed run has passed every
    # executable smoke; reporting a failure count here could only ever print zero.
    passed = 0
    for name in smokes:
        path = root / "tests" / name
        if not path.is_file():
            print(f"[brain] MISSING {name}", file=sys.stderr)
            return 1
        print(f"[brain] {name}", flush=True)
        result = subprocess.run([sys.executable, str(path)], cwd=root, env=env)
        if result.returncode:
            print(f"[brain] FAIL {name} ({result.returncode})", file=sys.stderr)
            return result.returncode
        passed += 1

    print(
        "PREPENDE BRAIN VERIFY: OK "
        f"(passed={passed}; "
        f"discovered={len(discover_smoke_files(root))}, "
        f"executable={len(smokes)})"
    )
    return 0


def main() -> int:
    suite = summarize_registry(ROOT)
    print(
        "PREPENDE BRAIN VERIFY REGISTRY: "
        f"discovered={len(suite['discovered'])} "
        f"executable={len(suite['executable'])} "
        f"excluded={len(suite['excluded'])} "
        f"unknown={len(suite['unknown'])}"
    )
    if suite["missing"]:
        print(f"[brain] Missing registered smokes: {', '.join(suite['missing'])}", file=sys.stderr)
        return 1
    if suite["unknown"]:
        print(f"[brain] Unregistered smokes detected: {', '.join(suite['unknown'])}", file=sys.stderr)
        return 1

    excluded = suite["excluded"]
    if excluded:
        print("[brain] Reviewed exclusions:")
        for name, reason in sorted(excluded.items()):
            print(f"[brain]  - {name}: {reason}")

    return run_smoke_suite(ROOT, suite["executable"], env=os.environ.copy())


if __name__ == "__main__":
    raise SystemExit(main())
