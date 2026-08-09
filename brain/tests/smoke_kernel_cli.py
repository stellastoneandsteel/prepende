"""Smoke: python3 -m kernel — the one-shot brain CLI (the /engram command's engine).

Asserts the scriptable surface drives the same Goal Loop as every other surface:
a goal returns streamed text + a truthful receipt (Assess-gated memory, no
external actions), --json is machine-readable, and --status introspects.
Echo provider only; no network, no keys.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_cli(*args: str, workdir: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "MODEL_PROVIDER": "echo",
        # isolate all durable state under the temp dir so the smoke never
        # touches (or pollutes) a real brain
        "WORKSPACE_ROOT": os.path.join(workdir, "workspace"),
        "MEMORY_DB": os.path.join(workdir, "memory.db"),
        "RUNS_DB": os.path.join(workdir, "runs.db"),
        "KNOWLEDGE_DB": os.path.join(workdir, "knowledge.db"),
        "VAULT_PATH": os.path.join(workdir, "vault"),
        "VAULT_INDEX_PATH": os.path.join(workdir, "vault-index.db"),
        "GRAPHIFY_GRAPH_PATH": os.path.join(workdir, "graphify", "graph.json"),
    }
    return subprocess.run(
        [sys.executable, "-m", "kernel", *args],
        capture_output=True, text=True, timeout=120, cwd=ROOT, env=env,
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        # 1. Plain run: answer streams to stdout, exit 0.
        r = run_cli("smoke goal: say hello", workdir=tmp)
        assert r.returncode == 0, (r.returncode, r.stderr[-500:])
        assert "smoke goal: say hello" in r.stdout, r.stdout[-500:]
        print("OK plain run: streamed answer, exit 0")

        # 2. --json: machine-readable {text, receipt}; receipt is truthful.
        r = run_cli("--json", "smoke goal: receipts", workdir=tmp)
        assert r.returncode == 0, (r.returncode, r.stderr[-500:])
        out = json.loads(r.stdout)
        assert out["text"], out
        receipt = out["receipt"]
        assert receipt["loopUsed"] is True and receipt["tactic"], receipt
        assert receipt["model"] == "echo", receipt
        assert receipt["modelProvenance"]["provider"] == "echo", receipt
        assert receipt["modelProvenance"]["fallback_used"] is False, receipt
        assert receipt["actionExecuted"] is False, receipt
        assert receipt["externalActions"] == [], receipt
        # default policy is candidate: memory PROPOSED, never silently written
        assert receipt["memory"]["proposed"], receipt["memory"]
        assert receipt["memory"]["written"] == [], receipt["memory"]
        print("OK --json: text + receipt; Assess gate holds (proposed, not written)")

        # 3. --status: read-only introspection JSON.
        r = run_cli("--status", workdir=tmp)
        assert r.returncode == 0, (r.returncode, r.stderr[-500:])
        state = json.loads(r.stdout)
        assert state["model"] == "echo" and "memory" in state, state
        assert state["knowledge"]["rag"]["lexical_ready"] is False, state
        assert "vault_path" not in state["knowledge"]["rag"], state
        assert "index_path" not in state["knowledge"]["rag"], state
        assert state["knowledge"]["graphify"]["ready"] is False, state
        assert "path" not in state["knowledge"]["graphify"], state
        assert state["knowledge"]["graphify"]["reason"] == "graph_missing", state
        print("OK --status: brain snapshot")

        # 4. No goal, no --status -> usage error, non-zero exit.
        r = run_cli(workdir=tmp)
        assert r.returncode != 0, r.stdout
        print("OK no-goal: usage error, non-zero exit")

    print("\nsmoke_kernel_cli OK")


if __name__ == "__main__":
    main()
