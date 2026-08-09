#!/usr/bin/env python3
"""Seed a tenant brain from a template pack — the provisioning lane.

A pack (packs/*.json) carries a tenant's OPERATING DISCIPLINE: typed, sourced
memories (procedural rules, workflow shape) plus the persona it should speak
with. It deliberately carries NO business facts — those arrive through the
brain_update intake after seeding, with intake recorded as their source. This
keeps packs generic and repo-shippable (SEPARATION: no client names in the
substrate) while a freshly provisioned tenant still arrives knowing how to
behave.

Operator-approved seeding: running this script IS the approval (deliberate
operator action, provenance recorded on every row). Idempotent — a marker
memory keyed to the pack id guards double-seeding; --force reseeds.

The store comes from the composition root, so writes embed through the
configured embedder, and embed_backfill() runs afterward — a mid-seed embedder
outage still converges to fully vectored rows instead of leaving the new
tenant lexically ranked (hard-won: vector-less rows lose hybrid recall to any
old chatty memory).

Usage:
    python3 scripts/seed_tenant.py --pack packs/researcher.json --scope acme-lab
    python3 scripts/seed_tenant.py --pack packs/small-business.json --scope shopname --force
    python3 scripts/seed_tenant.py --pack packs/researcher.json --scope acme-lab --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_KINDS = ("semantic", "procedural", "episodic")


def load_pack(path: str) -> dict[str, Any]:
    """Parse and validate a pack file. Raises ValueError with the actual
    problem — a malformed pack must fail loudly before any write."""
    with open(path) as f:
        pack = json.load(f)
    for field in ("pack", "name", "persona", "memories"):
        if not pack.get(field):
            raise ValueError(f"pack missing required field: {field}")
    for i, m in enumerate(pack["memories"]):
        if m.get("kind") not in _KINDS:
            raise ValueError(f"memories[{i}]: kind must be one of {_KINDS}")
        if not (m.get("content") or "").strip():
            raise ValueError(f"memories[{i}]: empty content")
        if not (m.get("source") or "").strip():
            raise ValueError(f"memories[{i}]: missing source — every seeded fact carries provenance")
    return pack


def _marker(pack: dict[str, Any]) -> str:
    return f"SEED-MARKER pack {pack['pack']}"


async def seed_tenant(store: Any, pack: dict[str, Any], scope: str, *,
                      force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """Write a pack's memories into `scope` and return a truthful receipt:
    {scope, pack, persona, written: {kind: n}, skipped, embedding: {...},
     verified: [...]}. Never writes on dry_run; no-op when the marker exists
    (unless force)."""
    scope = (scope or "").strip()
    if not scope:
        raise ValueError("scope is required")
    receipt: dict[str, Any] = {"scope": scope, "pack": pack["pack"],
                               "persona": pack["persona"], "written": {},
                               "skipped": False, "dryRun": dry_run}

    hits = await store.search(_marker(pack), scope=scope, k=3)
    if any(_marker(pack) in h["content"] for h in hits) and not force:
        receipt["skipped"] = True
        return receipt

    if dry_run:
        for m in pack["memories"]:
            receipt["written"][m["kind"]] = receipt["written"].get(m["kind"], 0) + 1
        return receipt

    for m in pack["memories"]:
        await store.write(m["content"], scope=scope, metadata={
            "kind": m["kind"], "source": m["source"],
            "seed": pack["pack"],
            "provenance": "operator-approved pack seed (scripts/seed_tenant.py)",
        })
        receipt["written"][m["kind"]] = receipt["written"].get(m["kind"], 0) + 1
    await store.write(_marker(pack), scope=scope, metadata={
        "kind": "episodic", "seed": pack["pack"], "source": "scripts/seed_tenant.py",
        "provenance": "operator-approved pack seed (scripts/seed_tenant.py)",
    })

    # Converge embeddings: write() embeds once, so any row that hit an embedder
    # hiccup would stay vector-less and lose hybrid ranking forever.
    if hasattr(store, "embed_backfill"):
        receipt["embedding"] = await store.embed_backfill(scope=scope)

    # Data-driven verification from the pack: each rule must actually recall.
    verified = []
    for check in pack.get("verify", []):
        got = await store.search(check["query"], scope=scope, k=5)
        ok = any(check["expect"] in h["content"] for h in got)
        verified.append({"query": check["query"], "ok": ok})
        if not ok:
            raise AssertionError(f"seed verify failed: {check['query']!r} "
                                 f"did not recall {check['expect']!r} in scope {scope}")
    receipt["verified"] = verified
    return receipt


async def main() -> None:
    ap = argparse.ArgumentParser(description="Provision a tenant brain from a template pack")
    ap.add_argument("--pack", required=True, help="path to packs/*.json")
    ap.add_argument("--scope", required=True, help="tenant scope to seed")
    ap.add_argument("--force", action="store_true", help="reseed even if the marker exists")
    ap.add_argument("--dry-run", action="store_true", help="report what would be written")
    args = ap.parse_args()

    pack = load_pack(args.pack)
    # The composition root wires the configured embedder onto the store —
    # seeding through a bare build_memory() would write vector-less rows.
    from kernel.core.brain import build_brain
    loop, cfg, _gw = build_brain()
    store = loop.memory

    receipt = await seed_tenant(store, pack, args.scope, force=args.force, dry_run=args.dry_run)

    print(json.dumps(receipt, indent=2))
    if receipt["skipped"]:
        print(f"\nalready seeded (marker found) — use --force to reseed", file=sys.stderr)
    elif not args.dry_run:
        persona = pack["persona"]
        if persona != "default":
            print(f"\nhosted persona mapping: add '{args.scope}={persona}' to "
                  f"ENGRAM_PERSONA_SCOPES on the deployment", file=sys.stderr)
        print("next: gather the owner's actual facts through intake (brain_update) — "
              "the pack seeds discipline, not business facts", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
