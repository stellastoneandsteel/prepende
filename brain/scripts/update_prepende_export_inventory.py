#!/usr/bin/env python3
"""Render or check Prepende's exact reviewed export inventory.

This operator-only maintenance command reads the working tree so a reviewer can
update the inventory before staging. The exporter itself never trusts working
tree bytes: it validates the committed/staged Git index against this result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "prepende-export-manifest.json"
INVENTORY_PATH = ROOT / "prepende-export-reviewed-inventory.json"
OVERRIDES = {
    ".env.example": "distribution/prepende/.env.example",
    "README.md": "distribution/prepende/README.md",
    "package.json": "distribution/prepende/package.json",
    "pyproject.toml": "distribution/prepende/pyproject.toml",
    "requirements-prepende.in": "distribution/prepende/requirements-prepende.in",
    "requirements-prepende.lock": "distribution/prepende/requirements-prepende.lock",
}


def _tracked_and_untracked() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    return sorted(
        item.decode("utf-8")
        for item in proc.stdout.split(b"\0")
        if item
    )


def _allowed(relative: str, policy: dict[str, object]) -> bool:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return False
    excluded = tuple(str(value) for value in policy.get("excludePrefixes", []))
    if any(relative.startswith(prefix) for prefix in excluded):
        return False
    if relative in {str(value) for value in policy.get("privateOverlayFiles", [])}:
        return False
    if relative in {str(value) for value in policy.get("excludeFiles", [])}:
        return False
    included_files = {str(value) for value in policy.get("includeFiles", [])}
    included_prefixes = tuple(str(value) for value in policy.get("includePrefixes", []))
    return relative in included_files or any(relative.startswith(prefix) for prefix in included_prefixes)


def _blob(path: str, selected: Path) -> dict[str, object]:
    payload = selected.read_bytes()
    mode = "100755" if selected.stat().st_mode & 0o111 else "100644"
    return {
        "mode": mode,
        "path": path,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _entry(output: str, sources: list[str], selected: Path) -> dict[str, object]:
    blob = _blob(sources[0], selected)
    return {
        "mode": blob["mode"],
        "outputPath": output,
        "sha256": blob["sha256"],
        "sourcePaths": sources,
    }


def render() -> dict[str, object]:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if policy.get("schemaVersion") != 2:
        raise SystemExit("Prepende export policy must use schemaVersion 2")
    paths = _tracked_and_untracked()
    path_set = set(paths)
    override_sources = set(OVERRIDES.values())
    override_outputs = set(OVERRIDES)
    files: list[dict[str, object]] = []
    ignored: list[dict[str, object]] = []

    for relative in paths:
        if relative == INVENTORY_PATH.name:
            continue
        if relative in override_sources or relative in override_outputs:
            continue
        if not _allowed(relative, policy):
            continue
        files.append(_entry(relative, [relative], ROOT / relative))

    for output, preferred in OVERRIDES.items():
        selected = preferred if preferred in path_set else output
        if selected not in path_set:
            raise SystemExit(f"missing reviewed override source for {output}")
        sources = [selected]
        files.append(_entry(output, sources, ROOT / selected))
        if selected == preferred and output in path_set and output != preferred:
            ignored.append({
                **_blob(output, ROOT / output),
                "reason": "shadowed-by-reviewed-distribution-override",
            })

    files.sort(key=lambda value: str(value["outputPath"]))
    ignored.sort(key=lambda value: str(value["path"]))
    return {
        "format": "prepende-reviewed-source-inventory-v2",
        "schemaVersion": 2,
        "selfDescribingControlFile": INVENTORY_PATH.name,
        "selfHashNote": (
            "This control file cannot contain its own byte hash. The v2 export receipt "
            "binds its exact Git-index bytes as inventorySha256; every payload is listed below."
        ),
        "ignoredSourceBlobs": ignored,
        "files": files,
    }


def serialized(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Update or check the reviewed Prepende export inventory")
    parser.add_argument("--check", action="store_true", help="fail unless the working-tree inventory is current")
    args = parser.parse_args()
    expected = serialized(render())
    current = INVENTORY_PATH.read_text(encoding="utf-8") if INVENTORY_PATH.exists() else ""
    if args.check:
        if current != expected:
            print("Prepende reviewed export inventory is stale")
            return 1
        print("PREPENDE EXPORT INVENTORY: OK")
        return 0
    old_umask = os.umask(0o077)
    try:
        INVENTORY_PATH.write_text(expected, encoding="utf-8")
        INVENTORY_PATH.chmod(0o644)
    finally:
        os.umask(old_umask)
    print(f"wrote {INVENTORY_PATH.relative_to(ROOT)} with {len(render()['files'])} reviewed files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
