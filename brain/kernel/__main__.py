"""python3 -m kernel — the one-shot brain CLI. Give it a goal, get the answer.

This is the scriptable surface over the same composition root every other
surface uses (kernel.core.brain.build_brain -> GoalLoop). Nothing here is a
second brain: the TUI, the /v1 API, and this CLI all drive the identical loop,
so a goal run from a shell, a slash command, or a product surface compounds into
the same memory and the same vault.

Usage:
  python3 -m kernel "summarize what you know about X"   # run the Goal Loop
  python3 -m kernel --json "goal"        # machine-readable {text, receipt}
  python3 -m kernel --verbose "goal"     # show the machinery (stderr)
  python3 -m kernel --memory auto "goal" # opt in to durable memory writes
  python3 -m kernel --meditate "goal"    # meditation posture (sit, then the smallest true change)
  python3 -m kernel --status             # what the brain knows (read-only)

Model selection follows .env / MODEL_PROVIDER exactly like every other
surface; with no keys configured it degrades to the echo provider so the
plumbing is always exercisable. Stdlib only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from kernel.core.brain import build_brain


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python3 -m kernel",
        description="Run one goal through the Prepende brain (Goal Loop) and print the answer.",
    )
    p.add_argument("goal", nargs="*", help="the goal text (quoted or bare words)")
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="print {text, receipt} JSON instead of prose")
    p.add_argument("--verbose", action="store_true",
                   help="show status/artifact events on stderr")
    p.add_argument("--memory", choices=("candidate", "auto"), default="candidate",
                   help="memory policy: 'candidate' (default, Assess-gated — nothing durable "
                        "is written) or 'auto' (dev opt-in: the loop writes its own memory)")
    p.add_argument("--scope", default="", help="tenant scope override (default: config)")
    p.add_argument("--meditate", action="store_true",
                   help="meditation posture: ingest, sit, then return the smallest true "
                        "change (opt-in; pins the tactic to solo)")
    p.add_argument("--status", action="store_true",
                   help="print a read-only snapshot of what the brain knows, then exit")
    return p


async def _amain(args: argparse.Namespace) -> int:
    try:
        loop, cfg, gateway = build_brain(memory_policy=args.memory)
    except Exception as exc:
        print(f"setup error: {exc}", file=sys.stderr)
        print(
            "tip: run `install -m 600 .env.example .env`, or use MODEL_PROVIDER=echo.",
            file=sys.stderr,
        )
        return 2
    if args.scope:
        loop.scope = args.scope
    if args.meditate:
        # Opt this process into the meditation posture. The strategist reads the
        # flag at choose() time (pins to solo) and the solo seam appends the prior.
        from kernel.core import meditation
        meditation.activate()

    if args.status:
        from kernel.core.introspect import brain_state
        state = await brain_state(loop, scope=loop.scope)
        print(json.dumps(state, indent=2, default=str))
        return 0

    goal = " ".join(args.goal).strip()
    if not goal:
        _parser().print_usage(sys.stderr)
        print("give the brain a goal (or --status).", file=sys.stderr)
        return 2

    tokens: list[str] = []
    final = {"text": "", "receipt": None, "error": None}

    async def on_event(ev: dict) -> None:
        t = ev.get("type")
        if t == "token":
            tokens.append(str(ev.get("text", "")))
            if not args.as_json:
                sys.stdout.write(str(ev.get("text", "")))
                sys.stdout.flush()
        elif t == "done":
            final["text"] = str(ev.get("text", "")) or "".join(tokens)
        elif t == "error":
            final["error"] = str(ev.get("text", ""))
            if not args.as_json:
                print(f"\n✗ {ev.get('text', '')}", file=sys.stderr)
        elif args.verbose and t in ("status", "artifact"):
            print(f"· {ev.get('text', '')}", file=sys.stderr)

    receipt = await loop.run(goal, on_event)
    final["receipt"] = receipt

    if args.as_json:
        print(json.dumps(final, indent=2, default=str))
    else:
        if not final["text"] and not final["error"]:
            # tactics that don't stream still return their answer via the run journal
            print("".join(tokens))
        print()  # end the streamed line
        if args.verbose:
            print(f"receipt: {json.dumps(receipt, default=str)}", file=sys.stderr)
    return 1 if (final["error"] or receipt.get("error")) else 0


def main() -> None:
    args = _parser().parse_args()
    try:
        raise SystemExit(asyncio.run(_amain(args)))
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
