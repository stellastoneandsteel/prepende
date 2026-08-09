#!/usr/bin/env python3
"""Smoke: context-fast status imports must not include provider inference dependencies."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PREFIXES = (
    "anthropic",
    "cohere",
    "google.generativeai",
    "httpx",
    "llama_cpp",
    "openai",
    "replicate",
    "requests",
    "sentence_transformers",
    "tensorflow",
    "torch",
    "transformers",
    "urllib3",
    "vllm",
    "jax",
    "litellm",
)


def _is_forbidden(module_name: str) -> bool:
    return any(
        module_name == prefix or module_name.startswith(prefix + ".")
        for prefix in FORBIDDEN_PREFIXES
    )


def _build_runner_script() -> str:
    return """\nimport json\nimport os\nimport runpy\nimport traceback\nimport sys\n\nrecords = []\ntrace_path = os.environ[\"PREPENDE_CONTEXT_FAST_IMPORT_TRACE\"]\n\n\ndef _audit(event, args):\n    if event != \"import\":\n        return\n    if not args or not isinstance(args[0], str):\n        return\n    module_name = args[0]\n    if not module_name:\n        return\n\n    importer = \"unknown\"\n    for frame in reversed(traceback.extract_stack()[:-3]):\n        if not frame.filename or frame.filename.startswith(\"<\"):\n            continue\n        lower = frame.filename.replace(\"\\\\\", \"/\").lower()\n        if \"importlib\" in lower or \"runpy\" in lower or \"runner_trace\" in lower:\n            continue\n        importer = f\"{frame.filename}:{frame.lineno}:{frame.name}\"\n        break\n\n    records.append({\"module\": module_name, \"importer\": importer})\n\n\nsys.path.insert(0, os.getcwd())\nsys.addaudithook(_audit)\nsys.argv = [\"-m\", \"kernel\", \"--status\", \"--context-fast\", \"--scope\", \"prepende\"]\ntry:\n    runpy.run_module(\"kernel.__main__\", run_name=\"__main__\")\nexcept SystemExit as exc:\n    if exc.code not in (None, 0):\n        raise\n\nwith open(trace_path, \"w\", encoding=\"utf-8\") as handle:\n    json.dump(records, handle, indent=2, sort_keys=True)\n"""


def _run_context_fast_with_import_trace() -> tuple[dict[str, object], list[dict[str, str]], str]:
    with tempfile.TemporaryDirectory(prefix="prepende-context-fast-trace-") as directory:
        root = Path(directory)
        trace = root / "imports.json"
        runner = root / "runner_trace.py"
        runner.write_text(_build_runner_script(), encoding="utf-8")

        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PREPENDE_CONTEXT_FAST_IMPORT_TRACE"] = str(trace)
        process = subprocess.run(
            [sys.executable, str(runner)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
        )
        if process.returncode:
            raise AssertionError(
                f"context-fast trace runner failed ({process.returncode}): {process.stderr!r}",
            )
        payload = json.loads(process.stdout)
        if not trace.exists():
            raise AssertionError("missing context-fast import trace output")
        records = json.loads(trace.read_text(encoding="utf-8"))
        prohibited = [item for item in records if _is_forbidden(item["module"])]
        return payload, prohibited, process.stderr


def main() -> None:
    payload, forbidden, stderr = _run_context_fast_with_import_trace()
    assert payload["model"] == "context-fast", payload
    assert payload["scope"] == "prepende", payload
    assert isinstance(payload["knowledge"], dict), payload
    assert not forbidden, (
        "Prohibited dependency in context-fast import graph: "
        + ", ".join(f"{item['module']} via {item['importer']}" for item in forbidden[:20])
    )
    assert stderr is not None
    print("smoke_context_fast_import_graph OK")


if __name__ == "__main__":
    main()
