"""The brain runtime must not ship or import the retired Protocol v0.2 copy."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from operations.operational_status import _collect_protocol  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    assert not (ROOT / "prepende").exists(), "embedded protocol package is still present"
    runtime = ROOT / "prepende_brain"
    assert runtime.is_dir(), "prepende_brain runtime package is missing"
    forbidden = {"contract.py", "ledger.py", "metrics.py", "report.py", "scoring.py"}
    assert not forbidden.intersection(path.name for path in runtime.iterdir()), runtime
    report = _collect_protocol(None, "unconfigured", ROOT)
    assert report["status"] == "notConfigured", report
    assert report["embedded"]["status"] == "notApplicable", report
    assert report["embedded"]["canSatisfyProtocolV2"] is False, report
    for path in ROOT.rglob("*.py"):
        if any(part in {".git", ".venv", "node_modules"} for part in path.parts):
            continue
        source = path.read_text(encoding="utf-8")
        assert "from prepende" + ".ledger" not in source, path
        assert "from prepende" + ".contract" not in source, path
    print("PROTOCOL V2 BOUNDARY SMOKE: OK")
    print("  embedded v0.2 absent; standalone Protocol v2 remains the only authority")


if __name__ == "__main__":
    main()
