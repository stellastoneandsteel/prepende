"""Plain-terminal REPL — the guaranteed surface (stdlib only, no install).

Runs everywhere Python does. The full Textual TUI (tui/app.py) is the product
surface; this is the always-works fallback and a clean dev loop.
"""

from __future__ import annotations

import asyncio
import sys

from kernel.core.brain import build_brain
from models.factory import build_gateway

BANNER = r"""
  Prepende
  every session leaves a trace
"""


# Calm by default: just the answer, like ChatGPT. The machinery (which tactic,
# memory recall, tool calls, artifact paths) is hidden unless `verbose` is on —
# depth on demand, never in your face. Toggle with the `verbose`/`quiet` command.
_STATE = {"verbose": False}


async def _on_event(ev: dict) -> None:
    t = ev["type"]
    verbose = _STATE["verbose"]
    if t == "status":
        if verbose:
            print(f"  \033[2m· {ev['text']}\033[0m")  # dim, only when asked
    elif t == "token":
        sys.stdout.write(ev["text"])
        sys.stdout.flush()
    elif t == "artifact":
        if verbose:
            print(f"\n  \033[2m✎ {ev['text']}\033[0m")
    elif t == "error":
        print(f"\n  ✗ {ev['text']}")
    elif t == "done":
        print()


async def _amain() -> None:
    try:
        loop, cfg, gateway = build_brain(memory_policy="auto")  # interactive dev surface: auto memory writes
    except Exception as exc:
        print(f"\n  setup error: {exc}")
        print("  tip: copy .env.example to .env (or just run as-is; MODEL_PROVIDER defaults to 'echo').\n")
        return

    print(BANNER)
    print(f"  model: {getattr(gateway, 'name', '?')}   ·   type a goal, or 'exit'.")
    print("  \033[2m(calm by default — type 'verbose' to see how it thinks, 'brain' to see what it knows)\033[0m")
    if loop.runs is not None:
        intr = loop.runs.interrupted()
        if intr:
            print(f"  ⚠ {len(intr)} goal(s) interrupted by a previous crash. `runs` to see, `resume <id>` to continue.")
    print()

    while True:
        try:
            goal = await asyncio.to_thread(input, "goal› ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        goal = goal.strip()
        if goal.lower() in {"exit", "quit", ":q"}:
            break
        if not goal:
            continue
        if goal.lower() in {"verbose", "quiet", ":verbose", ":quiet"}:
            _STATE["verbose"] = goal.lower().endswith("verbose")
            print(f"  {'verbose — showing the machinery' if _STATE['verbose'] else 'quiet — just the answer'}\n")
            continue
        if goal.lower().split()[0] in {"workflow", "workflows", ":workflows"}:
            wf = getattr(loop, "workflows", None)
            parts = goal.split()
            if wf is None:
                print("  no workflow selector\n"); continue
            menu = wf.list()
            # Workflow execution is never direct from the REPL. The HTTP/API
            # approval lane owns staging, decision, and one-time execution.
            if len(parts) >= 3 and parts[1].lower() == "run":
                print(
                    "  ✗ direct workflow execution is disabled; stage this registered "
                    "workflow through /v1/workflows/run and approve its receipt\n"
                )
                continue
            if len(parts) >= 3 and parts[1].lower() == "select":
                pick = await wf.select(goal.split(None, 2)[2])
                print(f"  best match: {pick or '(none)'}\n"); continue
            print("\n  workflows (n8n — deterministic automations):")
            for w in menu:
                print(f"    - {w['name']}: {w['description']}")
            if not menu:
                print("    (none — add to workflows.json: {name, description, url})")
            print("    stage runs via /v1/workflows/run  ·  pick for a goal: `workflow select <goal>`\n")
            continue
        if goal.lower() in {"connectors", ":connectors"}:
            tools = await loop.connectors.list_tools(
                tenant_id=cfg.memory_scope, workspace_id=cfg.workspace_scope
            ) if loop.connectors else []
            print("\n  connectors (outbound — the brain's reach):")
            for t in tools:
                mark = "●" if t["ready"] else "○"
                print(f"    {mark} {t['id']:<28} ({t['kind']}, {'ready' if t['ready'] else 'add key in .env'})")
            print("    ● ready   ○ needs its own key in .env (SEPARATION.md)\n")
            continue
        if goal.lower().split()[0] in {"model", ":model"}:
            import shutil
            avail = ["echo", "local"]
            if cfg.anthropic_key:
                avail.append("anthropic")
            if cfg.openai_key:
                avail.append("openai")
            if cfg.google_key:
                avail.append("google")
            if cfg.openai_compat_base:
                avail.append("openai-compatible")
            subs = []
            if shutil.which("claude"):
                subs.append("cli-claude")
            if shutil.which("codex"):
                subs.append("cli-codex")
            parts = goal.split()
            if len(parts) == 1:
                print(f"\n  active : {getattr(loop.gateway, 'name', '?')} ({getattr(loop.gateway, 'model', '')})")
                print(f"  choose : {', '.join(avail)}")
                if subs:
                    print(f"  on your subscription (no API tokens): {', '.join(subs)}")
                print("  switch : model <name>   (e.g. `model openai`, `model cli-claude`)\n")
                continue
            target, prev = parts[1].lower(), cfg.provider
            cfg.provider = target
            try:
                newgw = build_gateway(cfg)
            except Exception as exc:
                cfg.provider = prev
                print(f"  ✗ {exc}\n")
                continue
            loop.gateway = newgw
            loop.strategist.gateway = newgw  # so the next goal uses the new model
            print(f"  → switched to {newgw.name} ({getattr(newgw, 'model', 'default')})\n")
            continue
        if goal.lower() in {"runs", ":runs"}:
            rs = loop.runs.recent(10) if loop.runs else []
            print("\n  recent runs:")
            for r in rs:
                flag = "⏳ interrupted" if r["status"] == "running" else r["status"]
                print(f"    {r['goal_id']}  [{flag}]  {r['goal'][:48]}")
            print("    resume one with: resume <goal_id>\n")
            continue
        if goal.lower().split()[0] in {"resume", ":resume"}:
            parts = goal.split()
            if loop.runs is None:
                print("  no run journal\n")
                continue
            target = loop.runs.get(parts[1]) if len(parts) > 1 else (loop.runs.interrupted() or [None])[0]
            if not target:
                print("  nothing to resume — give a goal_id: `resume <id>` (see `runs`)\n")
                continue
            print(f"\n  resuming {target['goal_id']}: {target['goal']}\n")
            await loop.run(target["goal"], _on_event)
            loop.runs.finish(target["goal_id"], "(resumed in a later run)")  # clear the interrupted flag
            print()
            continue
        if goal.lower().split()[0] in {"ingest", ":ingest"} and loop.knowledge:
            src = goal.split(None, 1)[1].strip() if len(goal.split(None, 1)) > 1 else ""
            if not src:
                print("  usage: ingest <text to add to the wiki>\n")
                continue
            print("  · compiling into the wiki …")
            pages = await loop.knowledge.ingest(src)
            print(f"  ✎ wiki page: {', '.join(pages)}  (in {loop.knowledge.root}/wiki/)\n")
            continue
        if goal.lower().split()[0] in {"research", ":research"} and getattr(loop, "scout", None):
            topic = goal.split(None, 1)[1].strip() if len(goal.split(None, 1)) > 1 else ""
            if not topic:
                print("  usage: research <topic>\n"); continue
            print(f"  · scouting '{topic}' (gathering + verifying) …")
            r = await loop.scout.research(topic)
            print(f"  ✎ pending review: {r['item_id']}  ({r.get('sources',0)} sources, verdict: {r.get('verdict','?')})")
            print(f"    {r.get('summary','')[:200]}")
            print(f"    review it: `review`  ·  accept: `accept {r['item_id']}`\n")
            continue
        if goal.lower() in {"review", ":review"} and getattr(loop, "scout", None):
            pend = loop.scout.pending()
            print(f"\n  pending review ({len(pend)}):")
            for it in pend:
                print(f"    {it['id']}  [{it.get('confidence',0):.0%}]  {it.get('title','')[:60]}")
            if not pend:
                print("    (none — `research <topic>` to gather some)")
            print("    accept <id> · reject <id>\n")
            continue
        if goal.lower().split()[0] in {"accept", "reject"} and getattr(loop, "scout", None):
            parts = goal.split()
            if len(parts) < 2:
                print(f"  usage: {parts[0]} <item_id>\n"); continue
            if parts[0].lower() == "accept":
                res = await loop.scout.accept(parts[1])
                print(f"  {'✓ accepted → memory + vault' if res.get('ok') else '✗ '+str(res.get('error'))}\n")
            else:
                res = loop.scout.reject(parts[1])
                print(f"  {'✓ rejected' if res.get('ok') else '✗ '+str(res.get('error'))}\n")
            continue
        if goal.lower() in {"wiki", ":wiki"} and loop.knowledge:
            pages = list(await loop.knowledge.list_pages())
            print("\n  wiki pages:")
            for p in pages:
                print(f"    - {p}")
            if not pages:
                print("    (empty — `ingest <text>` to add one)")
            print()
            continue
        if goal.lower() in {"lint", ":lint"} and loop.knowledge:
            issues = list(await loop.knowledge.lint())
            print(f"\n  lint: {len(issues)} issue(s)")
            for i in issues[:12]:
                print(f"    - {i}")
            print()
            continue

        print()
        await loop.run(goal, _on_event)
        print()


def run() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    run()
