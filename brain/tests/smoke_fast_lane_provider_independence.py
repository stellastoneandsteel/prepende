#!/usr/bin/env python3
"""Smoke: the continuity module and context-fast path stay model-free."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTINUITY = ROOT / "operations" / "continuity.py"
FAST_STATUS = ROOT / "operations" / "operational_status.py"


def _local_module(module: str, *, source: Path, level: int = 0) -> Path | None:
    if level:
        base = source.parent
        for _ in range(level - 1):
            base = base.parent
        candidate = base.joinpath(*module.split(".")) if module else base
    else:
        candidate = ROOT.joinpath(*module.split("."))
    file_candidate = candidate.with_suffix(".py")
    if file_candidate.is_file():
        return file_candidate.resolve()
    package_candidate = candidate / "__init__.py"
    if package_candidate.is_file():
        return package_candidate.resolve()
    return None


def _import_graph(entry: Path) -> set[Path]:
    seen: set[Path] = set()
    pending = [entry.resolve()]
    while pending:
        source = pending.pop()
        if source in seen:
            continue
        seen.add(source)
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            candidates: list[Path | None] = []
            if isinstance(node, ast.Import):
                candidates.extend(
                    _local_module(alias.name, source=source) for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                candidates.append(
                    _local_module(node.module or "", source=source, level=node.level)
                )
            pending.extend(path for path in candidates if path is not None and path not in seen)
    return seen


def _assert_no_models(paths: set[Path], label: str) -> None:
    violations = sorted(
        path.relative_to(ROOT).as_posix()
        for path in paths
        if path.relative_to(ROOT).parts[0] == "models"
    )
    assert not violations, f"{label} imports model modules: {violations}"


def _trace_context_fast(temp: Path) -> set[str]:
    trace_path = temp / "imports.txt"
    sitecustomize = temp / "sitecustomize.py"
    sitecustomize.write_text(
        "import os\n"
        "import sys\n"
        "trace = os.environ['PREPENDE_IMPORT_TRACE']\n"
        "def record(name):\n"
        "    with open(trace, 'a', encoding='utf-8') as handle:\n"
        "        handle.write(str(name) + '\\n')\n"
        "def audit(event, args):\n"
        "    if event == 'import' and args:\n"
        "        record(args[0])\n"
        "sys.addaudithook(audit)\n"
        "for loaded in tuple(sys.modules):\n"
        "    record(loaded)\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "PREPENDE_IMPORT_TRACE": str(trace_path),
            "PYTHONPATH": os.pathsep.join(
                value
                for value in (str(temp), str(ROOT), env.get("PYTHONPATH", ""))
                if value
            ),
            "MODEL_PROVIDER": "provider-must-not-load",
            "EMBEDDING_PROVIDER": "provider-must-not-load",
            "DATABASE_URL": "",
            "MEMORY_BACKEND": "sqlite",
            "MEMORY_DB": str(temp / "memory.db"),
            "RUNS_DB": str(temp / "runs.db"),
            "VAULT_PATH": str(temp / "vault"),
            "VAULT_INDEX_PATH": str(temp / "vault-index.db"),
            "GRAPHIFY_GRAPH": str(temp / "graph.json"),
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "bin" / "engram"),
            "context-fast",
            "Verify the fast lane import boundary",
            "--json",
            "--scope",
            "provider-independence-smoke",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=12,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["modelCall"] == "skipped", payload
    assert payload["status"]["fastLane"] == {
        "modelCall": False,
        "liveProviderCall": False,
    }, payload
    return set(trace_path.read_text(encoding="utf-8").splitlines())


def main() -> None:
    continuity_graph = _import_graph(CONTINUITY)
    assert len(continuity_graph) > 1, continuity_graph
    _assert_no_models(continuity_graph, "operations/continuity.py")
    fast_status_graph = _import_graph(FAST_STATUS)
    assert len(fast_status_graph) > 1, fast_status_graph
    _assert_no_models(fast_status_graph, "operations/operational_status.py")

    with tempfile.TemporaryDirectory(prefix="prepende_fast_lane_imports_") as raw:
        imports = _trace_context_fast(Path(raw))
    assert "operations.continuity" in imports, sorted(imports)
    assert "operations.operational_status" in imports, sorted(imports)
    model_imports = sorted(
        name for name in imports if name == "models" or name.startswith("models.")
    )
    assert not model_imports, f"context-fast imported model modules: {model_imports}"
    print("smoke_fast_lane_provider_independence OK")


if __name__ == "__main__":
    main()
