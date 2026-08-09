#!/usr/bin/env python3
"""brain_heal.py — the scheduled "brain healing" pass, honestly.

Runs the maintenance the architecture calls "self-organization is scheduled
jobs, not magic": refresh the RAG projection of the vault, run memory
consolidation, and snapshot the brain to backup. Each step reports what it
actually did — including when a step is a Phase-1 no-op — so the cadence never
overstates.

Run:  MODEL_PROVIDER=echo python3 scripts/brain_heal.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from prepende_brain.private_fs import append_private_text, enforce_private_umask  # noqa: E402
from prepende_brain.env import brand_env  # noqa: E402

enforce_private_umask()


def _write_consolidation_receipts(path: Path, receipts: list[dict]) -> None:
    """Append reversible consolidation receipts through a private descriptor."""

    if not receipts:
        return
    append_private_text(
        path,
        "".join(json.dumps(receipt) + "\n" for receipt in receipts),
        repair_parent=path.parent.name == ".engram",
    )


async def heal() -> dict:
    report: dict = {"steps": []}

    # 1) RAG refresh — real: re-indexes changed/new vault pages into vault_index.db
    try:
        from knowledge.rag import VaultRagIndex
        idx = VaultRagIndex(str(ROOT / "vault"))
        try:
            from kernel.core.config import Config
            from kernel.core.brain import _configured_embedder, _embedding_profile
            from models.factory import build_gateway
            cfg = Config()
            gw = build_gateway(cfg)
            embed_gateway = gw
            if cfg.embedding_provider:
                embed_gateway = build_gateway(
                    cfg, provider=cfg.embedding_provider, model=cfg.embedding_model or None)
            profile = _embedding_profile(cfg, embed_gateway)
            profile_change = idx.set_embedder(
                _configured_embedder(embed_gateway, cfg.embedding_model),
                profile=profile,
            )
            embedder = "wired"
        except Exception as exc:  # noqa: BLE001
            embedder = f"none ({exc.__class__.__name__}) — lexical index only"
        stats = await idx.refresh()
        report["steps"].append({
            "step": "rag_refresh",
            "ok": True,
            "embedder": embedder,
            "embeddingProfile": locals().get("profile", ""),
            "profileChange": locals().get("profile_change"),
            "stats": stats,
            "readiness": idx.status(),
        })
    except Exception as exc:  # noqa: BLE001
        report["steps"].append({"step": "rag_refresh", "ok": False, "error": str(exc)})

    # 2) Memory consolidation — real but opt-in because it mutates durable memory.
    try:
        from kernel.core.config import Config
        from memory.factory import build_memory
        cfg = Config()
        mem = build_memory(cfg)
        scopes = ["default"]
        try:
            import sqlite3
            con = sqlite3.connect(f"file:{ROOT/'.engram'/'memory.db'}?mode=ro", uri=True)
            scopes = sorted({r[0] for r in con.execute("SELECT DISTINCT scope FROM memories")}) or ["default"]
            con.close()
        except Exception:  # noqa: BLE001
            pass
        # Consolidation MUTATES live memory. It is OPT-IN, so the scheduled heal
        # never touches the real brain unless the operator sets PREPENDE_CONSOLIDATE=1.
        #   PREPENDE_CONSOLIDATE_MODE=dedup (default) — deterministic near-duplicate
        #     dedup, no model calls.
        #   PREPENDE_CONSOLIDATE_MODE=topic — model-driven topic summarization (merges
        #     same-topic distinct facts). More powerful, riskier; writes a reversible
        #     receipt. Preview it first with scripts/consolidate_brain.py (dry run).
        #   PREPENDE_CONSOLIDATE_SCOPES=a,b — restrict to these scopes (default: all).
        if brand_env("CONSOLIDATE").strip() in ("1", "true", "yes"):
            mode = brand_env("CONSOLIDATE_MODE", "dedup").strip().lower()
            only = brand_env("CONSOLIDATE_SCOPES").strip()
            run_scopes = [s.strip() for s in only.split(",") if s.strip()] or scopes
            sc_obj = None
            if mode == "topic":
                from kernel.core.brain import _configured_embedder
                from models.factory import build_gateway
                from memory.consolidator import SemanticConsolidator
                gw2 = build_gateway(cfg)
                embed_gateway = gw2
                if cfg.embedding_provider:
                    embed_gateway = build_gateway(
                        cfg, provider=cfg.embedding_provider, model=cfg.embedding_model or None)
                if hasattr(mem, "set_embedder"):
                    mem.set_embedder(_configured_embedder(embed_gateway, cfg.embedding_model))
                sc_obj = SemanticConsolidator(gw2)
            merged = superseded = 0
            per_scope = {}
            receipts = []
            for sc in run_scopes:
                rep = await (sc_obj.consolidate(mem, scope=sc) if mode == "topic" else mem.consolidate(scope=sc))
                if isinstance(rep, dict):
                    merged += rep.get("clusters_merged", 0)
                    superseded += rep.get("superseded", 0)
                    if rep.get("superseded"):
                        per_scope[sc] = {"merged": rep["clusters_merged"], "superseded": rep["superseded"],
                                         "before": rep["before"], "after": rep["after"], "method": rep.get("method")}
                        receipts.append({"scope": sc, "mode": mode, "merges": rep.get("merges", [])})
            # reversible receipt of every supersede -> canonical
            if receipts:
                rpath = ROOT / ".engram" / "consolidate-receipts.jsonl"
                _write_consolidation_receipts(rpath, receipts)
            report["steps"].append({
                "step": "memory_consolidate", "ok": True, "scopes": run_scopes, "mode": mode,
                "clusters_merged": merged, "superseded": superseded, "per_scope": per_scope,
                "note": f"{mode}: merged {merged} cluster(s), superseded {superseded} memory(ies) across "
                        f"{len(run_scopes)} scope(s)" + (" — receipt written" if receipts else ""),
            })
        else:
            report["steps"].append({
                "step": "memory_consolidate", "ok": True, "scopes": scopes, "skipped": True,
                "note": "skipped — opt-in (PREPENDE_CONSOLIDATE=1; MODE=dedup|topic). Preview with scripts/consolidate_brain.py before enabling.",
            })
    except Exception as exc:  # noqa: BLE001
        report["steps"].append({"step": "memory_consolidate", "ok": False, "error": str(exc)})

    # 3) Backup — real: snapshots vault + .engram outside the repo
    try:
        proc = subprocess.run(["bash", str(ROOT / "scripts" / "backup_brain.sh")],
                              capture_output=True, text=True, timeout=120)
        receipt = (proc.stdout or proc.stderr).strip().splitlines()
        report["steps"].append({"step": "backup", "ok": proc.returncode == 0,
                                "receipt": receipt[-3:] if receipt else []})
    except Exception as exc:  # noqa: BLE001
        report["steps"].append({"step": "backup", "ok": False, "error": str(exc)})

    return report


def main() -> int:
    rep = asyncio.run(heal())
    for s in rep["steps"]:
        flag = "ok" if s.get("ok") else "FAIL"
        extra = s.get("stats") or s.get("note") or s.get("error") or ""
        print(f"[heal] {s['step']}: {flag} {extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
