#!/usr/bin/env python3
"""Export a reviewed, history-free Prepende source tree from Git's index.

The private operator checkout is not a customer distribution artifact. Its Git
history may contain an owner vault. The v2 export contract therefore combines
a broad default-deny policy with an exact reviewed output-path/blob inventory.
Every copied byte comes from the current Git index, must match that inventory,
must be UTF-8 text, and must pass the normalized privacy scan.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_ROOTS = {
    ".git",
    ".engram",
    ".netlify",
    ".venv",
    ".workspaces",
    "graphify-out",
    "node_modules",
    "output",
    "outreach",
    "prepende-data",
    "secrets",
    "tmp",
    "vault",
}
FORBIDDEN_FILES = {".env", ".deploy-token.txt", "mcp_servers.json", "workflows.json"}
POLICY_PATH = "prepende-export-manifest.json"
INVENTORY_PATH = "prepende-export-reviewed-inventory.json"
OVERRIDES = {
    ".env.example": "distribution/prepende/.env.example",
    "README.md": "distribution/prepende/README.md",
    "package.json": "distribution/prepende/package.json",
    "pyproject.toml": "distribution/prepende/pyproject.toml",
    "requirements-prepende.in": "distribution/prepende/requirements-prepende.in",
    "requirements-prepende.lock": "distribution/prepende/requirements-prepende.lock",
}
SUPPORTED_MODES = {0o100644, 0o100755}


class ExportRefusal(RuntimeError):
    """Raised when a customer-safe export cannot be proven."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ExportRefusal("reviewed inventory contains an unsafe path")
    return path.as_posix()


def _run_git(*args: str, binary: bool = False) -> bytes | str:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=not binary,
        check=False,
    )
    if proc.returncode:
        error = proc.stderr if isinstance(proc.stderr, str) else proc.stderr.decode("utf-8", "replace")
        raise ExportRefusal(error.strip() or "Git index operation failed")
    return proc.stdout


@functools.lru_cache(maxsize=1)
def _git_index_prefix() -> str:
    """Return this runtime's path inside its containing Git repository."""

    value = str(_run_git("rev-parse", "--show-prefix")).strip()
    return f"{value.rstrip('/')}/" if value else ""


def _index_entries() -> Iterable[tuple[int, str, str]]:
    raw = _run_git("ls-files", "--stage", "-z", binary=True)
    assert isinstance(raw, bytes)
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, encoded_path = record.split(b"\t", 1)
        fields = metadata.split(b" ")
        if len(fields) != 3 or fields[2] != b"0":
            raise ExportRefusal("Git index contains unresolved merge stages")
        try:
            relative = encoded_path.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ExportRefusal("Git index contains a non-UTF-8 path") from exc
        yield int(fields[0], 8), fields[1].decode("ascii"), _safe_path(relative)


def _index_blob(relative: str) -> bytes:
    payload = _run_git("show", f":{_git_index_prefix()}{relative}", binary=True)
    assert isinstance(payload, bytes)
    return payload


def _index_tree() -> str:
    full_tree = str(_run_git("write-tree")).strip()
    prefix = _git_index_prefix().rstrip("/")
    if not prefix:
        return full_tree
    return str(_run_git("rev-parse", f"{full_tree}:{prefix}")).strip()


def _load_policy() -> dict[str, object]:
    try:
        policy = json.loads(_index_blob(POLICY_PATH))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportRefusal("clean-export policy is missing or invalid in the Git index") from exc
    if int(policy.get("schemaVersion") or 0) != 2:
        raise ExportRefusal("unsupported clean-export policy schema")
    if policy.get("reviewedInventoryFile") != INVENTORY_PATH:
        raise ExportRefusal("clean-export policy does not bind the reviewed inventory")
    return policy


def _load_inventory() -> tuple[dict[str, object], bytes]:
    payload = _index_blob(INVENTORY_PATH)
    try:
        inventory = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportRefusal("reviewed inventory is missing or invalid in the Git index") from exc
    if int(inventory.get("schemaVersion") or 0) != 2:
        raise ExportRefusal("unsupported reviewed-inventory schema")
    if inventory.get("format") != "prepende-reviewed-source-inventory-v2":
        raise ExportRefusal("unsupported reviewed-inventory format")
    if inventory.get("selfDescribingControlFile") != INVENTORY_PATH:
        raise ExportRefusal("reviewed inventory self-description is invalid")
    return inventory, payload


def _allowed(relative: str, policy: dict[str, object]) -> bool:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return False
    if path.parts[0] in FORBIDDEN_ROOTS:
        return False
    if relative in FORBIDDEN_FILES or (
        relative.startswith(".env.") and relative != ".env.example"
    ):
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


def _normalized_text(payload: bytes, relative: str) -> str:
    if b"\0" in payload:
        raise ExportRefusal(f"text-only export rejected NUL content: {relative}")
    try:
        decoded = payload.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise ExportRefusal(f"text-only export rejected non-UTF-8 content: {relative}") from exc
    # Compatibility normalization defeats full-width/confusable punctuation;
    # format controls are removed before every privacy comparison.
    normalized = unicodedata.normalize("NFKC", decoded)
    for char in normalized:
        category = unicodedata.category(char)
        if category in {"Cs", "Co"} or (category == "Cc" and char not in "\n\r\t\f"):
            raise ExportRefusal(f"text-only export rejected binary/control content: {relative}")
    return "".join(" " if unicodedata.category(char) == "Cf" else char for char in normalized)


def _luhn_valid(candidate: str) -> bool:
    digits = [int(char) for char in candidate if char.isdigit()]
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


_ENV_CREDENTIAL_KEY = (
    r"(?:[A-Z][A-Z0-9_]*_)?"
    r"(?:API_KEY|ACCESS_KEY|SECRET_KEY|TOKEN|SECRET|PASSWORD|PASSWD|DATABASE_URL)"
)
_CREDENTIAL_KEY = (
    r"(?:"
    + _ENV_CREDENTIAL_KEY + r"|"
    r"api_key|access_key|secret_key|private_key|signing_key|access_token|auth_token|token|"
    r"secret|client_secret|webhook_secret|password|passwd|database_url|"
    r"apiKey|accessKey|secretKey|privateKey|signingKey|accessToken|authToken|"
    r"clientSecret|webhookSecret"
    r")"
)
_QUOTED_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?mx)"
    r"(?<![A-Za-z0-9_.-])"
    r"(?P<key_quote>[\"']?)(?P<key>" + _CREDENTIAL_KEY + r")(?P=key_quote)"
    r"[ \t]*[:=][ \t]*"
    r"(?P<value_quote>[\"'])(?P<value>[^\"'\r\n]+)(?P=value_quote)"
)
_BARE_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?m)^[ \t]*(?:export[ \t]+)?"
    r"(?P<key>" + _ENV_CREDENTIAL_KEY + r")"
    r"[ \t]*=[ \t]*(?P<value>[^\r\n#]+)"
)


def _is_sanitized_credential_fixture(value: str, relative: str) -> bool:
    """Return true only for conspicuous placeholders and isolated test values."""

    normalized = value.strip().lower()
    if not normalized or normalized in {
        "...", "none", "null", "false", "true", "redacted", "unset",
    }:
        return True
    if (
        (normalized.startswith("<") and normalized.endswith(">"))
        or (normalized.startswith("${") and normalized.endswith("}"))
        or (normalized.startswith("{{") and normalized.endswith("}}"))
    ):
        return True
    fixture_path = relative.startswith("tests/") or "/fixtures/" in f"/{relative}"
    fixture_markers = (
        "fixture", "test-only", "test_", "test-", "mock", "dummy",
        "must-not-be-written", "must-not-change",
    )
    return fixture_path and any(marker in normalized for marker in fixture_markers)


def _has_populated_credential_assignment(text_value: str, relative: str) -> bool:
    """Detect literal credential assignments without rejecting code references."""

    for pattern in (_QUOTED_CREDENTIAL_ASSIGNMENT, _BARE_CREDENTIAL_ASSIGNMENT):
        for match in pattern.finditer(text_value):
            if not _is_sanitized_credential_fixture(match.group("value"), relative):
                return True
    return False


def _privacy_scan(root: Path) -> dict[str, object]:
    """Reject credentials, PII, private identities, and instruction attacks.

    Refusals disclose only a stable category and relative file path. Matched
    content, keys, identities, and capture groups never enter the error.
    """

    forbidden_patterns = {
        "private identity": (
            r"\bry" + r"an\b|ame" + r"rio|stel" + r"lastoneandsteel|"
            r"\bliving" + r"ston\b|stel" + r"la[- ]stone"
        ),
        "private product": (
            r"\bmorning[ _-]?pai" + r"per\b|\bstel" + r"la\b|\bmim" + r"i\b|"
            r"morning[-_]pai" + r"per[-_]alpha|usemimiai\.com|"
            r"(?:morningpai" + r"per|the-engram)\.com"
        ),
        "machine path": (
            r"/(?:Users|home)/[^/\s]+/|[A-Za-z]:\\Users\\|/var/" + r"folders/"
        ),
        "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        "provider credential": (
            r"\b(?:sk-(?:proj-|ant-)?|gh[pousr]_|github_pat_|xox[baprs]-|AIza)"
            r"[A-Za-z0-9_\-]{16,}|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"
        ),
        "financial credential": r"\b(?:pk|sk|rk)_live_[A-Za-z0-9]{16,}",
        "JWT credential": (
            r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{5,}\."
            r"[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
        ),
        "bearer credential": r"Bearer\s+[A-Za-z0-9._-]{24,}",
        "credentialed database": r"postgres(?:ql)?://[^\s:/]+:[^\s@]+@",
        "phone number": (
            r"(?<![\w\d])(?:\+?1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]"
            r"\d{3}[ .-]\d{4}(?![\w\d])|"
            r"(?<![A-Za-z0-9])(?:\+?1)?[2-9]\d{9}(?![A-Za-z0-9])|"
            r"(?<![A-Za-z0-9])\+[1-9](?:[ .()/-]*\d){7,14}(?![A-Za-z0-9])"
        ),
        "social security number": r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)",
        "prompt injection": (
            r"(?:ignore|disregard|override) (?:all |any |the )?"
            r"(?:previous|prior|above) instructions|"
            r"(?:reveal|print|show) (?:the )?(?:system|developer) (?:prompt|message)|"
            r"you are now (?:in |the )?(?:developer|system|admin)|"
            r"follow (?:my|these) instructions instead|"
            r"begin (?:system|developer) (?:prompt|message)|"
            r"<\|(?:system|developer)\|>|\bjailbreak\b"
        ),
    }
    email_pattern = re.compile(r"[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})", re.I)
    # Alphanumeric boundaries avoid interpreting a long numeric run inside a
    # SHA-256 or Git object identifier as a payment card.
    card_pattern = re.compile(r"(?<![A-Za-z0-9])(?:\d[ -]?){12,18}\d(?![A-Za-z0-9])")
    scanned = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ExportRefusal(f"text-only export rejected a symbolic link: {path.relative_to(root)}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        text_value = _normalized_text(path.read_bytes(), relative)
        normalized_relative = unicodedata.normalize("NFKC", relative)
        scanned += 1
        for label, pattern in forbidden_patterns.items():
            flags = 0 if label == "machine path" else re.I
            if re.search(pattern, normalized_relative, flags):
                raise ExportRefusal(f"privacy scan rejected {label}: [path redacted]")
            if re.search(pattern, text_value, flags):
                raise ExportRefusal(f"privacy scan rejected {label}: {relative}")
        if _has_populated_credential_assignment(normalized_relative, relative):
            raise ExportRefusal("privacy scan rejected populated credential assignment: [path redacted]")
        if _has_populated_credential_assignment(text_value, relative):
            raise ExportRefusal(f"privacy scan rejected populated credential assignment: {relative}")
        for match in email_pattern.finditer(normalized_relative):
            domain = match.group(1).lower()
            if (
                domain == "example.com"
                or domain.endswith(".example.com")
                or domain.endswith(".example")
                or domain.endswith(".test")
            ):
                continue
            raise ExportRefusal("privacy scan rejected non-fixture email: [path redacted]")
        for match in email_pattern.finditer(text_value):
            domain = match.group(1).lower()
            if (
                domain == "example.com"
                or domain.endswith(".example.com")
                or domain.endswith(".example")
                or domain.endswith(".test")
            ):
                continue
            raise ExportRefusal(f"privacy scan rejected non-fixture email: {relative}")
        if any(_luhn_valid(match.group(0)) for match in card_pattern.finditer(text_value)):
            raise ExportRefusal(f"privacy scan rejected payment-card data: {relative}")

    env_example = root / ".env.example"
    if not env_example.is_file():
        raise ExportRefusal("clean export is missing .env.example")
    env_text = _normalized_text(env_example.read_bytes(), ".env.example")
    for raw in env_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if " #" in value:
            value = value.split(" #", 1)[0].strip()
        elif value.startswith("#"):
            value = ""
        if re.search(r"(?:API_KEY|TOKEN|SECRET|PASSWORD|DATABASE_URL)$", key) and value:
            raise ExportRefusal("privacy scan rejected populated credential placeholder: .env.example")
    return {"ok": True, "textFilesScanned": scanned, "policy": "default-deny-v2"}


def _inventory_entries(inventory: dict[str, object]) -> list[dict[str, object]]:
    raw_entries = inventory.get("files")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ExportRefusal("reviewed inventory has no file entries")
    entries: list[dict[str, object]] = []
    outputs: set[str] = set()
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise ExportRefusal("reviewed inventory contains an invalid entry")
        output = _safe_path(str(raw.get("outputPath") or ""))
        sources_raw = raw.get("sourcePaths")
        if not isinstance(sources_raw, list) or not sources_raw:
            raise ExportRefusal("reviewed inventory contains an entry without source paths")
        sources = [_safe_path(str(value)) for value in sources_raw]
        mode = str(raw.get("mode") or "")
        digest = str(raw.get("sha256") or "")
        if mode not in {"100644", "100755"} or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ExportRefusal("reviewed inventory contains invalid mode or hash metadata")
        if output in outputs or output == "PREPENDE_CLONE_MANIFEST.json":
            raise ExportRefusal("reviewed inventory contains an output collision")
        outputs.add(output)
        entries.append({"outputPath": output, "sourcePaths": sources, "mode": mode, "sha256": digest})
    if entries != sorted(entries, key=lambda item: str(item["outputPath"])):
        raise ExportRefusal("reviewed inventory entries are not deterministically sorted")
    return entries


def _ignored_source_blobs(inventory: dict[str, object]) -> list[dict[str, str]]:
    raw_ignored = inventory.get("ignoredSourceBlobs", [])
    if not isinstance(raw_ignored, list):
        raise ExportRefusal("reviewed inventory ignored-source metadata is invalid")
    ignored: list[dict[str, str]] = []
    paths: set[str] = set()
    for raw in raw_ignored:
        if not isinstance(raw, dict):
            raise ExportRefusal("reviewed inventory contains invalid ignored-source metadata")
        path = _safe_path(str(raw.get("path") or ""))
        mode = str(raw.get("mode") or "")
        digest = str(raw.get("sha256") or "")
        reason = str(raw.get("reason") or "")
        if (
            path in paths
            or mode not in {"100644", "100755"}
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or reason != "shadowed-by-reviewed-distribution-override"
        ):
            raise ExportRefusal("reviewed inventory contains invalid ignored-source metadata")
        paths.add(path)
        ignored.append({"path": path, "mode": mode, "sha256": digest, "reason": reason})
    if ignored != sorted(ignored, key=lambda item: item["path"]):
        raise ExportRefusal("reviewed ignored-source entries are not deterministically sorted")
    return ignored


def _select_reviewed_files(
    policy: dict[str, object], inventory: dict[str, object]
) -> list[dict[str, object]]:
    indexed = {relative: (mode, oid) for mode, oid, relative in _index_entries()}
    entries = _inventory_entries(inventory)
    ignored = _ignored_source_blobs(inventory)
    for entry in entries:
        output = str(entry["outputPath"])
        if not (_allowed(output, policy) or output in OVERRIDES):
            raise ExportRefusal("reviewed inventory contains an output outside the export policy")
        for source in entry["sourcePaths"]:  # type: ignore[index]
            if _allowed(source, policy):
                continue
            if source != output and OVERRIDES.get(output) != source:
                raise ExportRefusal("reviewed inventory contains a source outside the export policy")
    for item in ignored:
        if not (_allowed(item["path"], policy) or item["path"] in OVERRIDES):
            raise ExportRefusal("reviewed inventory contains an ignored source outside the export policy")
    recognized_sources = {
        source
        for entry in entries
        for source in entry["sourcePaths"]  # type: ignore[index]
    }
    recognized_sources.add(INVENTORY_PATH)
    recognized_sources.update(item["path"] for item in ignored)

    # A new file under a broad reviewed prefix cannot silently enter the
    # export. Its absence from the exact inventory is an immediate refusal.
    for relative in sorted(indexed):
        if _allowed(relative, policy) and relative not in recognized_sources:
            raise ExportRefusal("reviewed inventory does not authorize an indexed source path")

    for item in ignored:
        indexed_value = indexed.get(item["path"])
        if indexed_value is None:
            raise ExportRefusal(f"reviewed ignored source is absent: {item['path']}")
        mode, _oid = indexed_value
        payload = _index_blob(item["path"])
        if mode != int(item["mode"], 8) or _sha256(payload) != item["sha256"]:
            raise ExportRefusal(f"reviewed ignored-source blob mismatch: {item['path']}")
        _normalized_text(payload, item["path"])

    selected: list[dict[str, object]] = []
    for entry in entries:
        source_paths = entry["sourcePaths"]
        assert isinstance(source_paths, list)
        source = next((value for value in source_paths if value in indexed), None)
        if source is None:
            raise ExportRefusal(f"reviewed source is absent for output: {entry['outputPath']}")
        mode, oid = indexed[source]
        expected_mode = int(str(entry["mode"]), 8)
        payload = _index_blob(source)
        if mode not in SUPPORTED_MODES or mode != expected_mode:
            raise ExportRefusal(f"reviewed mode mismatch for output: {entry['outputPath']}")
        if _sha256(payload) != entry["sha256"]:
            raise ExportRefusal(f"reviewed blob mismatch for output: {entry['outputPath']}")
        _normalized_text(payload, str(entry["outputPath"]))
        selected.append({**entry, "sourcePath": source, "gitObject": oid, "payload": payload})
    return selected


def _file_receipts(root: Path) -> list[dict[str, object]]:
    receipts: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "PREPENDE_CLONE_MANIFEST.json":
            continue
        relative = path.relative_to(root).as_posix()
        mode = path.stat().st_mode & 0o777
        receipts.append({
            "path": relative,
            "mode": f"{mode:04o}",
            "sha256": _sha256(path.read_bytes()),
        })
    return receipts


def _source_tree_digest(files: Sequence[dict[str, object]]) -> str:
    framed = b"".join(
        f"{item['path']}\0{item['mode']}\0{item['sha256']}\n".encode("utf-8")
        for item in files
    )
    return _sha256(framed)


def export_index(destination: Path) -> dict[str, object]:
    destination = destination.expanduser().resolve(strict=False)
    root = ROOT.resolve()
    if destination == root or root in destination.parents:
        raise ExportRefusal("destination must be outside the private source checkout")
    if destination.exists():
        raise ExportRefusal("destination already exists")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=destination.parent))
    try:
        policy = _load_policy()
        inventory, inventory_payload = _load_inventory()
        selected = _select_reviewed_files(policy, inventory)
        for entry in selected:
            target = staging / str(entry["outputPath"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(entry["payload"])  # type: ignore[arg-type]
            target.chmod(0o755 if str(entry["mode"]) == "100755" else 0o644)

        # Normalize source paths to the exported layout. This makes the
        # reviewed inventory self-contained in a history-free clone without
        # accepting a fallback blob: the next export must match these exact
        # output-path bytes. The source inventory hash is retained separately.
        normalized_inventory = {
            "format": "prepende-reviewed-source-inventory-v2",
            "schemaVersion": 2,
            "selfDescribingControlFile": INVENTORY_PATH,
            "selfHashNote": (
                "This control file cannot contain its own byte hash. The v2 export receipt "
                "binds its exact Git-index bytes as inventorySha256; every payload is listed below."
            ),
            "ignoredSourceBlobs": [],
            "files": [
                {
                    "mode": entry["mode"],
                    "outputPath": entry["outputPath"],
                    "sha256": entry["sha256"],
                    "sourcePaths": [entry["outputPath"]],
                }
                for entry in selected
            ],
        }
        normalized_inventory_payload = (
            json.dumps(normalized_inventory, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        )
        inventory_target = staging / INVENTORY_PATH
        inventory_target.write_bytes(normalized_inventory_payload)
        inventory_target.chmod(0o644)

        required = (
            ".env.example",
            "README.md",
            "bin/prepende",
            "vault-template/index.md",
            POLICY_PATH,
            INVENTORY_PATH,
        )
        missing = [name for name in required if not (staging / name).is_file()]
        if missing:
            raise ExportRefusal("reviewed inventory is missing required clean-source files")
        for forbidden in FORBIDDEN_ROOTS:
            if (staging / forbidden).exists():
                raise ExportRefusal(f"private root escaped export filter: {forbidden}")
        if (staging / ".git").exists() or (staging / ".env").exists():
            raise ExportRefusal("history or local environment escaped export filter")

        privacy = _privacy_scan(staging)
        file_receipts = _file_receipts(staging)
        source_tree_sha256 = _source_tree_digest(file_receipts)
        revision = str(_run_git("rev-parse", "HEAD")).strip()
        index_tree = _index_tree()
        manifest = {
            "format": "prepende-clean-source-v2",
            "sourceRevision": revision,
            "sourceIndexTree": index_tree,
            "sourceSnapshot": "git-index",
            "historyIncluded": False,
            "ownerVaultIncluded": False,
            "runtimeStateIncluded": False,
            "graphifyOutputIncluded": False,
            "credentialsIncluded": False,
            "operatorPathsIncluded": False,
            "fileCount": len(file_receipts),
            "files": file_receipts,
            "reviewedInventorySha256": _sha256(inventory_payload),
            "inventorySha256": _sha256(normalized_inventory_payload),
            "sourceTreeSha256": source_tree_sha256,
            "privacyScan": privacy,
        }
        (staging / "PREPENDE_CLONE_MANIFEST.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, destination)
        return {"ok": True, "destination": str(destination), **manifest}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export a reviewed history-free Prepende tree without private owner data"
    )
    parser.add_argument("--output", required=True, help="new directory to create outside this checkout")
    parser.add_argument("--json", action="store_true", help="print the export receipt as JSON")
    args = parser.parse_args()
    try:
        receipt = export_index(Path(args.output))
    except ExportRefusal as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        print(
            f"Prepende clean source exported to {receipt['destination']} "
            f"({receipt['fileCount']} reviewed text files; no history or owner vault)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
