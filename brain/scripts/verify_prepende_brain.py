#!/usr/bin/env python3
"""Focused launch suite for Prepende's reusable brain and knowledge core."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASELINE_SMOKES = (
    "smoke_scoped_introspect.py",
    "smoke_accepted_work.py",
    "smoke_phase0.py",
    "smoke_phase1.py",
    "smoke_phase1_durable.py",
    "smoke_phase2.py",
    "smoke_kernel_cli.py",
    "smoke_cli_gateway.py",
    "smoke_cli_arguments.py",
    "smoke_context_fast.py",
    # Private-overlay smokes: the proprietary loop the public core does not
    # carry, so they are absent from the public copy of this registry. Here they
    # must run; resolve_smoke_suite turns them into reviewed exclusions in the
    # public-core profile only.
    "smoke_adaptive_routing.py",
    "smoke_human_routing_feedback.py",
    "smoke_orchestrator_workers.py",
    "smoke_evaluator_optimizer.py",
    "smoke_production_topologies.py",
    "smoke_live_react_proof.py",
    "smoke_loop_benchmark.py",
    "smoke_production_loop_benchmark.py",
    "smoke_evidence_packet.py",
    "smoke_ledger.py",
    "smoke_strategist_registry.py",
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
    # Registered 2026-08-25. These ran nowhere -- not this gate, not an npm
    # script, not CI -- and each was verified to pass in the gate sandbox
    # (echo providers, SQLite, no DATABASE_URL) before being added.
    "smoke_agents.py",
    "smoke_approval_executor.py",
    "smoke_approvals.py",
    "smoke_article_grounding.py",
    "smoke_article_pipeline.py",
    "smoke_autonomous_scout.py",
    "smoke_bias_meter.py",
    "smoke_brain_update.py",
    "smoke_brand_env.py",
    "smoke_candidate_provenance.py",
    "smoke_compound.py",
    "smoke_connector_readiness.py",
    "smoke_connectors.py",
    "smoke_context.py",
    "smoke_context_compaction.py",
    "smoke_conversations.py",
    "smoke_copy_edit.py",
    "smoke_embedding_config.py",
    "smoke_embedding_worker.py",
    "smoke_embeddings.py",
    "smoke_engram_api.py",
    "smoke_engram_wrapper.py",
    "smoke_feed_learning.py",
    "smoke_frontier_daily.py",
    "smoke_frontier_memory_process.py",
    "smoke_frontier_postgres_contract.py",
    "smoke_frontier_watchdog.py",
    "smoke_frontier_workspace.py",
    "smoke_hermes_processing_artifacts.py",
    "smoke_http.py",
    "smoke_ingestion_integrity.py",
    "smoke_intake_gate.py",
    "smoke_keyvault.py",
    "smoke_knowledge.py",
    "smoke_learning_core.py",
    "smoke_local.py",
    "smoke_marketing_content.py",
    "smoke_marketing_desk.py",
    "smoke_marketing_email.py",
    "smoke_marketing_growth.py",
    "smoke_marketing_growth_run.py",
    "smoke_marketing_handoff.py",
    "smoke_marketing_metrics.py",
    "smoke_marketing_seo.py",
    "smoke_marketing_voice.py",
    "smoke_marketing_wiring.py",
    "smoke_mcp_client.py",
    "smoke_memories.py",
    "smoke_memory_assess_gate.py",
    "smoke_memory_consolidate.py",
    "smoke_memory_context.py",
    "smoke_memory_eval.py",
    "smoke_memory_grant.py",
    "smoke_memory_maintenance.py",
    "smoke_memory_postgres.py",
    "smoke_memory_promotion.py",
    "smoke_memory_topic_consolidate.py",
    "smoke_memory_triage.py",
    "smoke_model_auto_route.py",
    "smoke_news.py",
    "smoke_news_connector.py",
    "smoke_pg_approval_store.py",
    "smoke_pg_candidate_queue.py",
    "smoke_pg_candidate_queue_safety.py",
    "smoke_prepende_lost_machine_drill.py",
    "smoke_public_article_mode.py",
    "smoke_registry.py",
    "smoke_restore_drill.py",
    "smoke_seed_tenant.py",
    "smoke_self_organize.py",
    "smoke_selfimprove.py",
    "smoke_selfimprove_runner.py",
    "smoke_setup.py",
    "smoke_stella_loop_benchmark.py",
    "smoke_stella_seed.py",
    "smoke_store_concurrency.py",
    "smoke_supabase_backup_monitor.py",
    "smoke_support_http.py",
    "smoke_surface_parity.py",
    "smoke_swap_matrix.py",
    "smoke_swarm.py",
    "smoke_tooluse.py",
    "smoke_v1_marketing_routes.py",
    "smoke_vault_graph.py",
    "smoke_widget.py",
    "smoke_workflows.py",
    "smoke_worktree_reaper.py",
    "smoke_x_post.py",
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
    "smoke_adaptive_routing.py": (
        "Reviewed public-core exclusion: this smoke exercises the proprietary loop (adaptive routing, orchestration, evaluator-optimizer, evidence, the live ReAct proof), which is private-overlay by manifest, so it is absent from a public-core export and cannot run there."
    ),
    "smoke_human_routing_feedback.py": (
        "Reviewed public-core exclusion: this smoke exercises the proprietary loop (adaptive routing, orchestration, evaluator-optimizer, evidence, the live ReAct proof), which is private-overlay by manifest, so it is absent from a public-core export and cannot run there."
    ),
    "smoke_orchestrator_workers.py": (
        "Reviewed public-core exclusion: this smoke exercises the proprietary loop (adaptive routing, orchestration, evaluator-optimizer, evidence, the live ReAct proof), which is private-overlay by manifest, so it is absent from a public-core export and cannot run there."
    ),
    "smoke_evaluator_optimizer.py": (
        "Reviewed public-core exclusion: this smoke exercises the proprietary loop (adaptive routing, orchestration, evaluator-optimizer, evidence, the live ReAct proof), which is private-overlay by manifest, so it is absent from a public-core export and cannot run there."
    ),
    "smoke_production_topologies.py": (
        "Reviewed public-core exclusion: this smoke exercises the proprietary loop (adaptive routing, orchestration, evaluator-optimizer, evidence, the live ReAct proof), which is private-overlay by manifest, so it is absent from a public-core export and cannot run there."
    ),
    "smoke_live_react_proof.py": (
        "Reviewed public-core exclusion: this smoke exercises the proprietary loop (adaptive routing, orchestration, evaluator-optimizer, evidence, the live ReAct proof), which is private-overlay by manifest, so it is absent from a public-core export and cannot run there."
    ),
    "smoke_loop_benchmark.py": (
        "Reviewed public-core exclusion: this smoke exercises the proprietary loop (adaptive routing, orchestration, evaluator-optimizer, evidence, the live ReAct proof), which is private-overlay by manifest, so it is absent from a public-core export and cannot run there."
    ),
    "smoke_production_loop_benchmark.py": (
        "Reviewed public-core exclusion: this smoke exercises the proprietary loop (adaptive routing, orchestration, evaluator-optimizer, evidence, the live ReAct proof), which is private-overlay by manifest, so it is absent from a public-core export and cannot run there."
    ),
    "smoke_evidence_packet.py": (
        "Reviewed public-core exclusion: this smoke exercises the proprietary loop (adaptive routing, orchestration, evaluator-optimizer, evidence, the live ReAct proof), which is private-overlay by manifest, so it is absent from a public-core export and cannot run there."
    ),
    "smoke_ledger.py": (
        "Reviewed public-core exclusion: this smoke exercises the proprietary loop (adaptive routing, orchestration, evaluator-optimizer, evidence, the live ReAct proof), which is private-overlay by manifest, so it is absent from a public-core export and cannot run there."
    ),
    "smoke_strategist_registry.py": (
        "Reviewed public-core exclusion: this smoke exercises the proprietary loop (adaptive routing, orchestration, evaluator-optimizer, evidence, the live ReAct proof), which is private-overlay by manifest, so it is absent from a public-core export and cannot run there."
    ),
    "smoke_design_loop_evolution.py": (
        "Reviewed exclusion: requires numpy, which is not a runtime dependency of this repository -- the core is stdlib-only and requirements-api.txt does not carry it. Register it here only if numpy becomes a declared dependency."
    ),
    "smoke_heartbeat.py": (
        "Reviewed exclusion: requires PREPENDE_WATCHED_SITES to be populated. With it empty the smoke reports {'skipped': 'PREPENDE_WATCHED_SITES empty'} and fails, so it is a configured-environment check, not a sandbox check."
    ),
    "smoke_launchd_environment_isolation.py": (
        "Reviewed exclusion: asserts against installed macOS launchd plists under scripts/launchd/. It describes a configured host, not this repository, and cannot pass on a Linux CI runner."
    ),
    "smoke_supabase_single_snapshot_postgres17.py": (
        "Reviewed exclusion: starts an ephemeral PostgreSQL 17 server. It needs a Postgres 17 binary on PATH, which the gate sandbox does not provide (DATABASE_URL is deliberately empty here)."
    ),
    "smoke_tenant_tokens.py": (
        "Reviewed exclusion: requires real PREPENDE_TENANT_TOKENS / ENGRAM_API_TOKEN values; without them every request is {'error': 'unauthorized'}. It verifies a credentialed deployment, not the sandbox."
    ),
    "smoke_v1_api.py": (
        "KNOWN FAILING, not environmental. It asserts a planned chat engages the goal loop (loop.used is True, mode 'goal_loop'), and the router now classifies the same prompt as 'fast_chat' / 'conversational_turn'. Either the classifier regressed or the assertion is stale; that is a product decision, so it is recorded here rather than silently registered or quietly deleted."
    ),
    "smoke_verifier_panel.py": (
        "KNOWN FAILING, not environmental. The run ends resultStatus 'run_failed' with RuntimeError 'ReAct tactic omitted its server safety receipt' (kernel/core/loop.py:1081) even though the panel fixture selects the solo tactic. Either the fixture or the receipt contract is wrong; that needs a decision, so it is recorded rather than hidden."
    ),
    "smoke_autonomy_grants.py": (
        "Reviewed exclusion: owned by a product gate, not the brain gate. It is run "
        "by `npm run verify:prepende:auto-code`. Running it here as well would double the cost and "
        "split ownership of the failure."
    ),
    "smoke_prepende_chatgpt_plugin.py": (
        "Reviewed exclusion: owned by a product gate, not the brain gate. It is run "
        "by `npm run verify:prepende:chatgpt-plugin`. Running it here as well would double the cost and "
        "split ownership of the failure."
    ),
    "smoke_prepende_cockpit.py": (
        "Reviewed exclusion: owned by a product gate, not the brain gate. It is run "
        "by `npm run verify:prepende:article-learning`. Running it here as well would double the cost and "
        "split ownership of the failure."
    ),
    "smoke_prepende_mcp_lifecycle.py": (
        "Reviewed exclusion: owned by a product gate, not the brain gate. It is run "
        "by `npm run verify:prepende:chatgpt-plugin`. Running it here as well would double the cost and "
        "split ownership of the failure."
    ),
    "smoke_prepende_private_runtime.py": (
        "Reviewed exclusion: owned by a product gate, not the brain gate. It is run "
        "by `npm run verify:prepende:private-runtime`. Running it here as well would double the cost and "
        "split ownership of the failure."
    ),
    "smoke_recursive_evolution.py": (
        "Reviewed exclusion: owned by a product gate, not the brain gate. It is run "
        "by `npm run verify:prepende:auto-code`. Running it here as well would double the cost and "
        "split ownership of the failure."
    ),
    "smoke_paiper_desk.py": (
        "KNOWN FAILING on the echo lane, deterministically. It calls run_prepende_loop on the full desk prompt and expects a headline line plus a body; the echo provider returns that prompt back as a single line, so split_generated yields an empty body. It needs a configured model provider, which the gate sandbox deliberately does not give it (MODEL_PROVIDER=echo). Either it should assert only what the echo lane can produce, or it belongs behind a provider-backed gate; that is a decision, so it is recorded here."
    ),
    "smoke_thinking_voice.py": (
        "KNOWN FAILING, not environmental. It asserts a specific thinkingVoice state sequence and the emitted sequence no longer matches. Either the voice states changed intentionally or this regressed; that needs a decision, so it is recorded rather than registered green or quietly deleted."
    ),
    "smoke_recovery_backup_v2.py": (
        "Reviewed exclusion: requires the `age` CLI (age, age-keygen) to exercise the encrypted backup lane. The CI runner does not install it, so the smoke dies with FileNotFoundError before asserting anything. It resolves the tools on PATH rather than a Homebrew prefix, so registering it here is a matter of installing age in the workflow, not of changing the smoke."
    ),
    "smoke_supabase_logical_backup.py": (
        "Reviewed exclusion: requires the `age` CLI and PostgreSQL client binaries (pg_dump, pg_restore). The CI runner installs none of them. Same as smoke_recovery_backup_v2: the smoke resolves tools on PATH, so this is about what the workflow installs."
    ),
    "smoke_setup_endpoint.py": (
        "KNOWN FAILING on the CI runner, not locally. It asserts that a loopback request to /v1/setup/apply with invalid input returns 400; on the runner it returns 403, so the request is not being treated as loopback there. It passes 5/5 locally, including under a deliberately bare environment, so the difference is the runner rather than shell state. Recorded rather than registered red or quietly deleted: either loopback detection is wrong on that host or the assertion is."
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


def _private_registry() -> tuple[tuple[str, ...], dict[str, str]]:
    """Load the private-overlay registry entries, if this tree has them.

    Smokes named after a private product cannot appear in this file: the
    public-core export privacy-scans every exported file and refuses one that
    names a private product. They live in scripts/_private_smoke_registry.py,
    which is private-overlay by manifest. A public-core export has neither the
    module nor those smokes, so nothing there goes unaccounted for.
    """

    path = Path(__file__).resolve().parent / "_private_smoke_registry.py"
    if not path.is_file():
        return (), {}
    spec = importlib.util.spec_from_file_location("_private_smoke_registry", path)
    if spec is None or spec.loader is None:
        return (), {}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return (
        tuple(getattr(module, "PRIVATE_SMOKES", ())),
        dict(getattr(module, "PRIVATE_EXCLUSION_REASONS", {})),
    )


def _published_test_files(root: Path, manifest: str) -> set[str]:
    """Test files the given export manifest promises to carry."""

    path = root / manifest
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {
        str(entry)
        for entry in policy.get("includeFiles", [])
        if str(entry).startswith("tests/")
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
    reason = str(
        _EXCLUSION_REASONS.get(name) or _private_registry()[1].get(name) or ""
    ).strip()
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

    executable.extend(_private_registry()[0])
    executable = list(dict.fromkeys(executable))
    available = discover_smoke_files(root)
    available_set = set(available)
    # A public-core export carries only the public suite. Smokes that exercise
    # a private-overlay surface are absent there by design, so classify them as
    # reviewed exclusions instead of reporting them missing -- but only in that
    # profile, and only with a literal reason, so a genuinely missing smoke in
    # any other tree still fails closed.
    # In an EXPORT -- customer clone or public core -- a registered smoke may be
    # absent, and the manifest is what says whether that was reviewed. If the
    # manifest does not carry `tests/<name>`, its absence IS the decision, and it
    # is checked against the manifest rather than asserted in prose. If the
    # manifest DOES carry it and it is still gone, the export dropped a file it
    # promised: that is allowed only with a literal reason, and is otherwise
    # reported missing, which is fatal.
    #
    # The private source checkout has every file, so none of this applies there
    # and an unregistered smoke still stops the gate.
    manifest = (
        "prepende-export-manifest.json" if private_clone
        else "prepende-public-core-manifest.json"
    )
    if any(name not in available_set for name in executable):
        published = _published_test_files(root, manifest)
        for name in list(executable):
            if name in available_set:
                continue
            literal = _EXCLUSION_REASONS.get(name) or _private_registry()[1].get(name)
            if f"tests/{name}" in published:
                if literal:
                    exclusions[name] = literal
                continue
            exclusions[name] = literal or (
                f"Reviewed export exclusion: {manifest} does not carry "
                f"tests/{name}, so it cannot exist in this export. The manifest "
                "is the reviewed decision."
            )
        executable = [name for name in executable if name not in exclusions]
    executable_set = set(executable)
    missing = [name for name in executable if name not in available_set]
    # A discovered smoke that is not executable may still be accounted for, but
    # only by a literal reason written in _EXCLUSION_REASONS. There is no
    # blanket rule and no default string: reviewed_exclusion refuses a name it
    # has no reason for, so anything left over is genuinely unregistered.
    for name in available:
        if name in executable_set or name in exclusions:
            continue
        if _EXCLUSION_REASONS.get(name) or _private_registry()[1].get(name):
            exclusions[name] = reviewed_exclusion(name)
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


def smoke_environment(base: dict[str, str], state: Path) -> dict[str, str]:
    """The sandbox one smoke runs in: echo providers, SQLite, no live database."""

    env = dict(base)
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
        # Everything else that otherwise defaults to ./.engram/ in the
        # repository. Leaving any of these unset makes the suite write runtime
        # state into the working tree, where it survives to the next run: the
        # gate passed from a clean checkout and failed on the second
        # consecutive run, because the adaptive router had accumulated
        # observations and started exploring a different tactic.
        "EXPERIENCE_LEDGER_DB": str(state / "experience_ledger.db"),
        "MEMORY_CANDIDATES_DB": str(state / "memory_candidates.db"),
        "PREPENDE_EVOLUTION_DB": str(state / "evolution.db"),
        "PREPENDE_DAILY_RECEIPT_DIR": str(state / "evolution-receipts"),
        "APPROVALS_LOG": str(state / "approvals.jsonl"),
        "ENGRAM_APPROVALS_LOG": str(state / "approvals.jsonl"),
        "PREPENDE_DATA_DIR": str(state / "prepende-data"),
        "PREPENDE_SANDBOX_ROOT": str(state / "sandbox"),
        # Deterministic routing. With exploration on, the bandit picks a tactic
        # at random, and on the echo lane the evaluator_optimizer tactic cannot
        # verify anything -- its three judges return judge_error:ValueError, the
        # run ends human_review_required, and the candidate is blocked. Any
        # smoke that expected a normal answer then fails, at whatever rate the
        # bandit happens to explore. That is one cause behind several unrelated
        # intermittents: smoke_surface_parity 500s, smoke_http empty result,
        # smoke_ledger narrative, smoke_v1_api mode. A gate should not be a
        # slot machine; smokes that mean to exercise exploration turn it back on
        # themselves.
        "PREPENDE_ROUTING_EXPLORATION": "false",
    })
    return env


def run_smoke_suite(root: Path, smokes: list[str], *, env: dict[str, str] | None = None) -> int:
    # One state directory PER SMOKE, not one for the suite.
    #
    # Sharing it made results depend on execution order: a smoke could read rows
    # a previous one wrote to the same MEMORY_DB / RUNS_DB / SELF_IMPROVE_DB.
    # That is not a theoretical hazard -- it is why this suite had intermittent
    # failures that passed on re-run and passed standalone, which is the worst
    # possible signal from a gate: it teaches people to re-run instead of look.
    #
    # A smoke that needs state from a previous smoke is expressing a dependency
    # it should own itself, so isolation is also the honest contract.
    base = dict(os.environ if env is None else env)

    # The suite aborts on the first failure, so a completed run has passed every
    # executable smoke; reporting a failure count here could only ever print zero.
    passed = 0
    for name in smokes:
        path = root / "tests" / name
        if not path.is_file():
            print(f"[brain] MISSING {name}", file=sys.stderr)
            return 1
        print(f"[brain] {name}", flush=True)
        state = Path(tempfile.mkdtemp(prefix="prepende_brain_verify_"))
        result = subprocess.run(
            [sys.executable, str(path)], cwd=root, env=smoke_environment(base, state)
        )
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
        # Fatal everywhere now. This was report-only in the private checkout
        # while 107 discovered smokes ran nowhere -- failing would have blocked
        # every change behind triaging them, and a blanket exclusion would have
        # been a rubber stamp. That backlog is closed: every smoke is either
        # registered here, carries a reviewed exclusion with a specific reason,
        # or is run by a named `npm run verify:*` gate. `npm run
        # audit:smoke-registry` reports the split and should read zero orphans.
        #
        # So a new unregistered smoke is now a real gap, and it stops the gate.
        print(
            f"[brain] Unregistered smokes: {', '.join(suite['unknown'])}",
            file=sys.stderr,
        )
        print(
            "[brain] Add it to BASELINE_SMOKES, or give it a reviewed exclusion "
            "with a reason specific to why it cannot run here.",
            file=sys.stderr,
        )
        return 1

    excluded = suite["excluded"]
    if excluded:
        print("[brain] Reviewed exclusions:")
        for name, reason in sorted(excluded.items()):
            print(f"[brain]  - {name}: {reason}")

    return run_smoke_suite(ROOT, suite["executable"], env=os.environ.copy())


if __name__ == "__main__":
    raise SystemExit(main())
