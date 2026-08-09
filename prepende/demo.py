"""Dependency-free Protocol v2 demonstration."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .ledger import Ledger
from .plot import reliability_svg
from .report import build_report


class _Clock:
    def __init__(self):
        self.value = 1_700_000_000.0

    def __call__(self):
        return self.value

    def tick(self):
        self.value += 1


def seed(directory: str) -> Ledger:
    clock = _Clock()
    ledger = Ledger.create(
        Path(directory) / "demo-ledger-v2.jsonl",
        stream_id="prepende-demo",
        registered_predictor="agent-demo",
        _clock=clock,
    )
    for index in range(30):
        probability = "0.7" if index < 20 else "0.3"
        contract = ledger.lock_prediction(
            predictor="agent-demo",
            model_version="demo-v1",
            domain="held-out-demo",
            event_id=f"forecast-{index}",
            question=f"Will held-out event {index} occur?",
            kind="probability",
            claim={"p": probability},
            resolution_rule="y=1 if evidence.occurred is true",
            evaluator={
                "type": "binary_value", "version": "1",
                "parameters": {"evidence": "result", "field": "occurred"},
            },
            evaluation={"id": "demo-harness-v1", "spec_digest": "sha256:" + "1" * 64, "artifacts": []},
            resolution_due_at="2023-11-16T00:00:00Z",
            resolver_policy={"mode": "self", "authorized_key_ids": []},
            nonresolution_policy={"action": "forfeit", "metric": "brier", "penalty": "1"},
        )
        clock.tick()
        occurred = index % 10 < (7 if index < 20 else 3)
        ledger.resolve(
            contract.contract_id,
            evidence=[{"name": "result", "uri": f"urn:demo:{index}", "content": {"occurred": occurred}}],
        )
        clock.tick()
    return ledger


def main() -> None:
    directory = tempfile.mkdtemp(prefix="prepende-demo-")
    ledger = seed(directory)
    print(build_report(ledger))
    svg = Path(directory) / "calibration.svg"
    reliability_svg(ledger.records(), str(svg), nbins=5)
    print(f"\nwrote demo artifacts under {directory}")

    rows = [json.loads(line) for line in Path(ledger.path).read_text().splitlines()]
    resolution = next(row for row in rows if row["event_type"] == "resolution")
    resolution["event"]["outcome"]["y"] = 1 - resolution["event"]["outcome"]["y"]
    Path(ledger.path).write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )
    print("tamper test status:", ledger.verify().status)


if __name__ == "__main__":
    main()
