"""Deterministic, owner-reviewed knowledge bundle import.

This is deliberately not an MCP tool. Product runtimes retain read/propose
capabilities only; an operator imports a graph-derived bundle from the host
after a separate approval manifest binds its bytes, identity, graph version,
and exact item IDs. Accepted items become provenance-rich Markdown in one
scoped vault, then that vault's disposable RAG projection is rebuilt.

The importer is product-neutral. A product-specific exporter supplies its
bundle schema as data and the approval manifest explicitly authorizes it.
"""

from __future__ import annotations

import datetime as _dt
import fcntl
import hashlib
import json
import math
import os
import re
import tempfile
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from knowledge.scoped import tenant_vault_path, validate_scope
from knowledge.vault import VaultKnowledge
from prepende_brain.private_fs import secure_directory, secure_file
from prepende_brain.identity import require_identity_namespace


APPROVAL_MANIFEST_SCHEMA = "prepende.reviewed_knowledge_approval.v1"
IMPORT_RECEIPT_SCHEMA = "prepende.reviewed_knowledge_import_receipt.v1"
APPROVED_STATUS = "source_policy_approved"
SCREENING_POLICY = "prepende.reviewed_bundle_screening.default_deny.v2"
SCREENING_CANONICALIZATION = "unicode_nfkc_strip_format_v1"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_MAX_CONTENT_CHARS = 1_000_000

_SCREEN_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("pii", "email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("pii", "ssn", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")),
    (
        "pii",
        "phone",
        re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)"),
    ),
    ("secret", "private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.I)),
    (
        "secret",
        "provider_key",
        re.compile(r"\b(?:sk-(?:proj-|ant-)?|gh[pousr]_|xox[baprs]-|AKIA|AIza)[A-Za-z0-9_-]{16,}"),
    ),
    ("secret", "bearer_credential", re.compile(r"\bBearer\s+[A-Za-z0-9._~-]{20,}", re.I)),
    (
        "secret",
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ),
    (
        "secret",
        "credentialed_url",
        re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:]+:[^\s@]+@", re.I),
    ),
    (
        "prompt_injection",
        "instruction_override",
        re.compile(r"\bignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|system|developer)\s+(?:instructions?|messages?|prompts?)\b", re.I),
    ),
    (
        "prompt_injection",
        "role_marker",
        re.compile(r"<\|(?:system|assistant|developer)\|>|\[INST\]|\b(?:system|developer)\s+(?:prompt|message)\s*:", re.I),
    ),
    (
        "prompt_injection",
        "secret_exfiltration",
        re.compile(r"\b(?:reveal|print|exfiltrate|leak)\b.{0,48}\b(?:system prompt|developer message|secret|credential|api key)\b", re.I | re.S),
    ),
    (
        "prompt_injection",
        "guardrail_bypass",
        re.compile(r"\b(?:bypass|disable|override)\b.{0,48}\b(?:guardrails?|safety|policy|authorization|approval)\b", re.I | re.S),
    ),
    (
        "prompt_injection",
        "command_execution",
        re.compile(r"\bexecute\s+(?:the\s+)?(?:following|next)\s+(?:command|tool|instruction)\b", re.I),
    ),
)
_PAYMENT_CANDIDATE_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_COMPACT_PHONE_RE = re.compile(r"(?<!\d)(?:1)?[2-9]\d{2}[2-9]\d{6}(?!\d)")


class ReviewedBundleError(ValueError):
    """The reviewed bundle contract failed before an import could be certified."""


def _luhn_valid(digits: str) -> bool:
    total = 0
    parity = len(digits) % 2
    for index, character in enumerate(digits):
        value = int(character)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _canonical_scalar(value: Any, path: str) -> str:
    """Return the canonical text inspected by the default-deny gate.

    JSON numbers must not evade a detector merely because they are not Python
    strings, while safe values retain their original type in the approved
    bundle and rendered metadata. NFKC folds full-width punctuation and digits;
    removing Unicode format controls closes zero-width and bidi-obfuscation
    gaps before matching. The canonical value is used only for screening.
    """

    if isinstance(value, str):
        text = value
    elif value is None:
        text = "null"
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ReviewedBundleError(
                f"reviewed import screening refused {path}: invalid/non_finite_number"
            )
        text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    else:
        raise ReviewedBundleError(
            f"reviewed import screening refused {path}: invalid/non_json_scalar"
        )
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Cf"
    )


def _scalar_fields(value: Any, path: str) -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for index, key in enumerate(sorted(value, key=str)):
            key_path = f"{path}.key[{index}]"
            value_path = f"{path}.value[{index}]"
            # Keys are untrusted data too. Screen their canonical form, but use
            # only an ordinal in paths so a refusal never echoes a sensitive or
            # instruction-shaped key into logs.
            yield key_path, _canonical_scalar(key, key_path)
            yield from _scalar_fields(value[key], value_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _scalar_fields(item, f"{path}[{index}]")
    else:
        yield path, _canonical_scalar(value, path)


def _screen_approved_items(
    documents: list[dict[str, Any]], relationships: list[dict[str, Any]]
) -> dict[str, Any]:
    """Fail closed on sensitive or instruction-shaped approved source text.

    Screening is deterministic, has no bypass flag, and runs before a scoped
    vault path, lock, page, or RAG index is created. Error messages identify
    only the detector and field path; matched private text is never echoed.
    """

    field_count = 0
    character_count = 0
    for item_type, values in (("document", documents), ("relationship", relationships)):
        for item_index, item in enumerate(values):
            for field_path, text in _scalar_fields(item, f"{item_type}[{item_index}]"):
                field_count += 1
                character_count += len(text)
                for category, detector, pattern in _SCREEN_PATTERNS:
                    if pattern.search(text):
                        raise ReviewedBundleError(
                            f"reviewed import screening refused {field_path}: {category}/{detector}"
                        )
                for candidate in _PAYMENT_CANDIDATE_RE.findall(text):
                    digits = re.sub(r"\D", "", candidate)
                    if 13 <= len(digits) <= 19 and _luhn_valid(digits):
                        raise ReviewedBundleError(
                            f"reviewed import screening refused {field_path}: pii/payment_card"
                        )
                if _COMPACT_PHONE_RE.search(text):
                    raise ReviewedBundleError(
                        f"reviewed import screening refused {field_path}: pii/compact_phone"
                    )
    return {
        "policy": SCREENING_POLICY,
        "status": "passed",
        "canonicalization": SCREENING_CANONICALIZATION,
        "categories": ["pii", "secret", "prompt_injection"],
        "itemCount": len(documents) + len(relationships),
        "fieldCount": field_count,
        "characterCount": character_count,
    }


@dataclass(frozen=True)
class _Page:
    item_id: str
    item_type: str
    page_id: str
    content: str
    source_document: str
    source_location: str

    @property
    def relative_path(self) -> str:
        return f"wiki/{self.page_id}.md"

    @property
    def sha256(self) -> str:
        return _sha256(self.content.encode("utf-8"))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _text(value: Any, label: str, *, limit: int = 500) -> str:
    text = str(value or "").strip()
    if not text:
        raise ReviewedBundleError(f"{label} is required")
    if len(text) > limit:
        raise ReviewedBundleError(f"{label} exceeds {limit} characters")
    return text


def _item_id(value: Any, label: str) -> str:
    item_id = _text(value, label, limit=160)
    if not _ID_RE.fullmatch(item_id):
        raise ReviewedBundleError(
            f"{label} must use only letters, numbers, dot, colon, underscore, or hyphen"
        )
    return item_id


def _load_object(path: str | Path, label: str) -> tuple[Path, bytes, dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise ReviewedBundleError(f"cannot read {label}: {source}") from exc
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewedBundleError(f"{label} is not valid JSON: {source}") from exc
    if not isinstance(value, dict):
        raise ReviewedBundleError(f"{label} must be a JSON object")
    return source, raw, value


def _approval_status(item: dict[str, Any]) -> str:
    approval = item.get("approval")
    if isinstance(approval, dict):
        return str(approval.get("status") or "").strip()
    return str(approval or "").strip()


def _runtime_contract(item: dict[str, Any], item_id: str) -> dict[str, Any]:
    contract = item.get("prepende") or item.get("engram")
    if not isinstance(contract, dict):
        raise ReviewedBundleError(f"item {item_id!r} is missing its runtime contract")
    return contract


def _source_fields(item: dict[str, Any]) -> tuple[str, str]:
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    provenance = (
        item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
    )
    document = str(
        provenance.get("sourceDocument")
        or source.get("sourceFile")
        or source.get("sourceDocument")
        or ""
    ).strip()
    location = str(
        provenance.get("sourceLocation") or source.get("sourceLocation") or ""
    ).strip()
    return document, location


def _graph_node_id(item: dict[str, Any]) -> str:
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    return str(item.get("graphNodeId") or source.get("graphNodeId") or "").strip()


def _page_id(item_id: str, item_type: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", item_id.lower()).strip("-") or "item"
    digest = _sha256(item_id.encode("utf-8"))[:8]
    prefix = "kb" if item_type == "document" else "rel"
    # VaultKnowledge normalizes wikilinks to at most 60 characters.
    return f"{prefix}-{slug[:42]}-{digest}"


def _yaml_scalar(value: Any) -> str:
    """JSON scalars are valid YAML and keep frontmatter parsing deterministic."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _frontmatter(fields: Iterable[tuple[str, Any]]) -> str:
    return "---\n" + "".join(f"{key}: {_yaml_scalar(value)}\n" for key, value in fields) + "---\n\n"


def _common_fields(
    *,
    item: dict[str, Any],
    item_id: str,
    item_type: str,
    tenant: str,
    workspace: str,
    scope: str,
    graph_version: str,
    bundle_sha: str,
    manifest_sha: str,
    receipt_id: str,
) -> tuple[list[tuple[str, Any]], str, str]:
    provenance = (
        item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
    )
    source_document, source_location = _source_fields(item)
    fields: list[tuple[str, Any]] = [
        ("type", "reviewed_knowledge" if item_type == "document" else "reviewed_relationship"),
        ("knowledge_id", item_id),
        ("tenant", tenant),
        ("workspace", workspace),
        ("scope", scope),
        ("graph_version", graph_version),
        ("bundle_sha256", bundle_sha),
        ("approval_manifest_sha256", manifest_sha),
        ("approval_status", APPROVED_STATUS),
        ("import_receipt_id", receipt_id),
        ("source_document", source_document),
        ("source_location", source_location),
        ("source_kind", str(provenance.get("sourceKind") or "")),
        ("confidence", provenance.get("confidence")),
        ("confidence_label", str(provenance.get("confidenceLabel") or "")),
    ]
    graph_node = _graph_node_id(item)
    if graph_node:
        fields.append(("graph_node_id", graph_node))
    return fields, source_document, source_location


def _relationship_endpoint(item: dict[str, Any], side: str) -> str:
    keys = (side, f"{side}Id", f"{side}GraphNodeId")
    for key in keys:
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get("graphNodeId") or value.get("id")
        if str(value or "").strip():
            return str(value).strip()
    return ""


def _validate_contract(
    item: dict[str, Any],
    *,
    item_id: str,
    tenant: str,
    workspace: str,
    physical_scope: str,
    graph_version: str,
) -> None:
    if _approval_status(item) != APPROVED_STATUS:
        raise ReviewedBundleError(
            f"item {item_id!r} is not {APPROVED_STATUS!r}"
        )
    runtime = _runtime_contract(item, item_id)
    item_scope = runtime.get("scope")
    if not isinstance(item_scope, dict):
        raise ReviewedBundleError(f"item {item_id!r} has no tenant/workspace scope")
    if item_scope.get("tenant") != tenant or item_scope.get("workspace") != workspace:
        raise ReviewedBundleError(f"item {item_id!r} has a mismatched tenant/workspace")
    if runtime.get("physicalScope") != physical_scope:
        raise ReviewedBundleError(
            f"item {item_id!r} physicalScope does not match the canonical "
            "tenant/workspace namespace"
        )
    allowed = runtime.get("allowedRuntimeUse")
    if not isinstance(allowed, list) or "knowledge_search" not in allowed:
        raise ReviewedBundleError(f"item {item_id!r} is not approved for knowledge_search")
    if runtime.get("memoryWrite") is not False:
        raise ReviewedBundleError(f"item {item_id!r} does not explicitly deny runtime memory writes")
    provenance = item.get("provenance")
    if not isinstance(provenance, dict):
        raise ReviewedBundleError(f"item {item_id!r} has no provenance")
    if str(provenance.get("graphVersion") or "").strip() != graph_version:
        raise ReviewedBundleError(f"item {item_id!r} has a mismatched graph version")
    source_document, _ = _source_fields(item)
    if not source_document:
        raise ReviewedBundleError(f"item {item_id!r} has no source document")


def _render_pages(
    *,
    documents: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    tenant: str,
    workspace: str,
    scope: str,
    graph_version: str,
    bundle_sha: str,
    manifest_sha: str,
    receipt_id: str,
) -> list[_Page]:
    node_pages: dict[str, str] = {}
    document_by_id: dict[str, dict[str, Any]] = {}
    for item in documents:
        item_id = _item_id(item.get("id"), "document id")
        graph_node = _text(_graph_node_id(item), f"document {item_id!r} graphNodeId")
        if graph_node in node_pages:
            raise ReviewedBundleError(f"duplicate graph node {graph_node!r}")
        node_pages[graph_node] = _page_id(item_id, "document")
        document_by_id[item_id] = item

    relation_links: dict[str, list[str]] = {node: [] for node in node_pages}
    relationship_specs: list[tuple[dict[str, Any], str, str, str, str]] = []
    for item in relationships:
        item_id = _item_id(item.get("id"), "relationship id")
        source = _relationship_endpoint(item, "source")
        target = _relationship_endpoint(item, "target")
        if source not in node_pages or target not in node_pages:
            raise ReviewedBundleError(
                f"relationship {item_id!r} endpoints must both be approved document graph nodes"
            )
        relation_page = _page_id(item_id, "relationship")
        relation_links[source].append(relation_page)
        relation_links[target].append(relation_page)
        relationship_specs.append((item, item_id, source, target, relation_page))

    pages: list[_Page] = []
    for item_id in sorted(document_by_id):
        item = document_by_id[item_id]
        fields, source_document, source_location = _common_fields(
            item=item, item_id=item_id, item_type="document", tenant=tenant,
            workspace=workspace, scope=scope, graph_version=graph_version,
            bundle_sha=bundle_sha, manifest_sha=manifest_sha, receipt_id=receipt_id,
        )
        graph_node = _text(_graph_node_id(item), f"document {item_id!r} graphNodeId")
        title = _text(item.get("title"), f"document {item_id!r} title", limit=300)
        body = _text(item.get("content"), f"document {item_id!r} content", limit=_MAX_CONTENT_CHARS)
        links = sorted(set(relation_links[graph_node]))
        relation_section = ""
        if links:
            relation_section = "\n\n## Reviewed relationships\n\n" + "\n".join(
                f"- [[{page}]]" for page in links
            )
        content = _frontmatter(fields) + f"# {title}\n\n{body}{relation_section}\n"
        pages.append(_Page(
            item_id=item_id, item_type="document", page_id=node_pages[graph_node],
            content=content, source_document=source_document,
            source_location=source_location,
        ))

    for item, item_id, source, target, page_id in sorted(
        relationship_specs, key=lambda value: value[1]
    ):
        fields, source_document, source_location = _common_fields(
            item=item, item_id=item_id, item_type="relationship", tenant=tenant,
            workspace=workspace, scope=scope, graph_version=graph_version,
            bundle_sha=bundle_sha, manifest_sha=manifest_sha, receipt_id=receipt_id,
        )
        relation = str(item.get("relation") or item.get("label") or "related_to").strip()
        fields.extend([
            ("relationship_source", source),
            ("relationship_target", target),
            ("relationship_type", relation),
        ])
        detail = _text(
            item.get("content") or item.get("description") or relation,
            f"relationship {item_id!r} content",
            limit=_MAX_CONTENT_CHARS,
        )
        content = (
            _frontmatter(fields)
            + f"# {relation}\n\n{detail}\n\n"
            + f"## Connected knowledge\n\n- [[{node_pages[source]}]]\n- [[{node_pages[target]}]]\n"
        )
        pages.append(_Page(
            item_id=item_id, item_type="relationship", page_id=page_id,
            content=content, source_document=source_document,
            source_location=source_location,
        ))
    return pages


def _atomic_write(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    secure_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _page_receipts(pages: list[_Page]) -> list[dict[str, Any]]:
    return [
        {
            "itemId": page.item_id,
            "itemType": page.item_type,
            "page": page.page_id,
            "path": page.relative_path,
            "sha256": page.sha256,
            "sourceDocument": page.source_document,
            "sourceLocation": page.source_location,
        }
        for page in sorted(pages, key=lambda value: value.item_id)
    ]


@contextmanager
def _import_lock(tenant_root: Path):
    """Serialize every import that can mutate one scoped vault."""

    secure_directory(tenant_root)
    receipts = tenant_root / "receipts"
    if receipts.is_symlink():
        raise ReviewedBundleError("scoped receipts directory must not be a symlink")
    secure_directory(receipts)
    lock_path = receipts / ".reviewed-knowledge-import.lock"
    if lock_path.is_symlink():
        raise ReviewedBundleError("reviewed import lock must not be a symlink")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    secure_file(lock_path, required=True)
    with os.fdopen(fd, "a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _validate_existing_receipt(
    receipt_path: Path,
    *,
    tenant_root: Path,
    expected_pages: list[_Page],
    receipt_id: str,
    tenant: str,
    workspace: str,
    scope: str,
    bundle_sha: str,
    bundle_schema: str,
    graph_version: str,
    manifest_sha: str,
    approved_ids: list[str],
    approved_by: str,
    approved_at: str,
    screening: dict[str, Any],
) -> dict[str, Any]:
    if receipt_path.is_symlink():
        raise ReviewedBundleError("immutable import receipt must not be a symlink")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewedBundleError("existing immutable import receipt is unreadable") from exc
    expected = {
        "schema": IMPORT_RECEIPT_SCHEMA,
        "receiptId": receipt_id,
        "immutable": True,
        "ingestMode": "owner_cli_only",
        "tenant": tenant,
        "workspace": workspace,
        "scope": scope,
        "bundleSchema": bundle_schema,
        "bundleSha256": bundle_sha,
        "graphVersion": graph_version,
        "approvalManifestSchema": APPROVAL_MANIFEST_SCHEMA,
        "approvalManifestSha256": manifest_sha,
        "approvedItemIds": sorted(approved_ids),
        "approvedBy": approved_by,
        "approvedAt": approved_at,
        "screening": screening,
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise ReviewedBundleError(f"existing immutable receipt disagrees on {key}")
    allowed_keys = set(expected) | {"pages"}
    if set(receipt) != allowed_keys:
        raise ReviewedBundleError("existing immutable receipt fields do not match the contract")
    pages = receipt.get("pages")
    if pages != _page_receipts(expected_pages):
        raise ReviewedBundleError(
            "existing immutable receipt does not match the deterministic page projection"
        )
    for page in pages:
        relative = str(page.get("path") or "")
        target = (tenant_root / relative).resolve()
        if tenant_root not in target.parents or not target.is_file() or (tenant_root / relative).is_symlink():
            raise ReviewedBundleError("an imported page named by the receipt is missing")
        if _sha256(target.read_bytes()) != page.get("sha256"):
            raise ReviewedBundleError(f"imported page {relative!r} no longer matches its receipt")
    return receipt


async def _commit_reviewed_import(
    *,
    tenant_root: Path,
    receipt_path: Path,
    pages: list[_Page],
    receipt_id: str,
    tenant: str,
    workspace: str,
    scope: str,
    bundle_schema: str,
    bundle_sha: str,
    graph_version: str,
    manifest_sha: str,
    approved_ids: list[str],
    approved_by: str,
    approved_at: str,
    screening: dict[str, Any],
    imported_at: str,
) -> dict[str, Any]:
    """Commit one fully validated import while the scoped lock is held."""

    if receipt_path.exists() or receipt_path.is_symlink():
        receipt = _validate_existing_receipt(
            receipt_path, tenant_root=tenant_root, expected_pages=pages,
            receipt_id=receipt_id, tenant=tenant, workspace=workspace, scope=scope,
            bundle_sha=bundle_sha, bundle_schema=bundle_schema,
            graph_version=graph_version, manifest_sha=manifest_sha,
            approved_ids=approved_ids, approved_by=approved_by, approved_at=approved_at,
            screening=screening,
        )
        knowledge = VaultKnowledge(str(tenant_root))
        await knowledge._reindex()
        current_rag = await knowledge.rag.rebuild()
        completed_at = imported_at.strip() or _dt.datetime.now(_dt.timezone.utc).isoformat()
        return {
            **receipt,
            "idempotent": True,
            "receiptPath": str(receipt_path),
            "operationCompletedAt": completed_at,
            "currentRagRebuild": current_rag,
        }

    wiki = tenant_root / "wiki"
    if wiki.is_symlink():
        raise ReviewedBundleError("scoped wiki directory must not be a symlink")
    secure_directory(wiki)
    rendered = {page.relative_path: page.content.encode("utf-8") for page in pages}
    for relative, data in rendered.items():
        target = tenant_root / relative
        if target.is_symlink():
            raise ReviewedBundleError(f"refusing symlink page target: {relative}")
        if target.exists() and target.read_bytes() != data:
            raise ReviewedBundleError(
                f"refusing to overwrite existing page with different content: {relative}"
            )

    created: list[Path] = []
    index_path = tenant_root / "index.md"
    if index_path.is_symlink():
        raise ReviewedBundleError("scoped index must not be a symlink")
    old_index = index_path.read_bytes() if index_path.exists() else None
    knowledge: VaultKnowledge | None = None
    try:
        for relative, data in rendered.items():
            target = tenant_root / relative
            if not target.exists():
                _atomic_write(target, data)
                created.append(target)
        knowledge = VaultKnowledge(str(tenant_root))
        await knowledge._reindex()
        rag = await knowledge.rag.rebuild()
        receipt = {
            "schema": IMPORT_RECEIPT_SCHEMA,
            "receiptId": receipt_id,
            "immutable": True,
            "ingestMode": "owner_cli_only",
            "tenant": tenant,
            "workspace": workspace,
            "scope": scope,
            "bundleSchema": bundle_schema,
            "bundleSha256": bundle_sha,
            "graphVersion": graph_version,
            "approvalManifestSchema": APPROVAL_MANIFEST_SCHEMA,
            "approvalManifestSha256": manifest_sha,
            "approvedItemIds": sorted(approved_ids),
            "approvedBy": approved_by,
            "approvedAt": approved_at,
            "screening": screening,
            "pages": _page_receipts(pages),
        }
        _atomic_write(
            receipt_path,
            (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            mode=0o400,
        )
    except Exception:
        for target in created:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
        if old_index is None:
            try:
                index_path.unlink()
            except FileNotFoundError:
                pass
        else:
            _atomic_write(index_path, old_index)
        if knowledge is not None:
            try:
                await knowledge.rag.rebuild()
            except Exception:
                pass
        raise
    return {
        **receipt,
        "idempotent": False,
        "receiptPath": str(receipt_path),
        "operationCompletedAt": (
            imported_at.strip() or _dt.datetime.now(_dt.timezone.utc).isoformat()
        ),
        "currentRagRebuild": rag,
    }


async def import_reviewed_bundle(
    *,
    bundle_path: str | Path,
    approval_manifest_path: str | Path,
    vault_base: str | Path,
    tenant: str,
    workspace: str,
    scope: str = "",
    imported_at: str = "",
) -> dict[str, Any]:
    """Validate, materialize, index, and receipt one reviewed bundle.

    The caller supplies expected identity out-of-band. Bundle and manifest
    identity must match it exactly; neither input may choose another vault.
    """

    tenant = validate_scope(tenant)
    workspace = validate_scope(workspace)
    try:
        scope = require_identity_namespace(tenant, workspace, scope)
    except ValueError as exc:
        raise ReviewedBundleError(str(exc)) from exc
    _bundle_file, bundle_raw, bundle = _load_object(bundle_path, "knowledge bundle")
    _manifest_file, manifest_raw, manifest = _load_object(
        approval_manifest_path, "approval manifest"
    )
    bundle_sha = _sha256(bundle_raw)
    manifest_sha = _sha256(manifest_raw)

    if manifest.get("schema") != APPROVAL_MANIFEST_SCHEMA:
        raise ReviewedBundleError("approval manifest schema is not supported")
    bundle_schema = _text(bundle.get("schema"), "bundle schema")
    if manifest.get("bundleSchema") != bundle_schema:
        raise ReviewedBundleError("approval manifest does not authorize this bundle schema")
    graph_version = _text(bundle.get("graphVersion"), "bundle graphVersion")
    for authority_key in (
        "prependeWriteAuthorized", "engramWriteAuthorized", "writeAuthorized"
    ):
        if authority_key in bundle and bundle.get(authority_key) is not False:
            raise ReviewedBundleError(
                f"bundle {authority_key} must remain false; only this owner CLI may import"
            )
    if str(manifest.get("graphVersion") or "").strip() != graph_version:
        raise ReviewedBundleError("bundle and approval manifest graph versions differ")
    if str(manifest.get("bundleSha256") or "").lower() != bundle_sha:
        raise ReviewedBundleError("approval manifest bundle hash does not match the bundle bytes")
    if manifest.get("tenant") != tenant or manifest.get("workspace") != workspace:
        raise ReviewedBundleError("approval manifest tenant/workspace does not match the requested identity")
    approved_by = _text(manifest.get("approvedBy"), "approval manifest approvedBy")
    approved_at = _text(manifest.get("approvedAt"), "approval manifest approvedAt")

    private_scope = (bundle.get("scopes") or {}).get("private")
    if not isinstance(private_scope, dict):
        raise ReviewedBundleError("bundle has no private tenant/workspace scope")
    if private_scope.get("tenant") != tenant or private_scope.get("workspace") != workspace:
        raise ReviewedBundleError("bundle tenant/workspace does not match the requested identity")

    documents = bundle.get("documents")
    relationships = bundle.get("relationships")
    if not isinstance(documents, list) or not isinstance(relationships, list):
        raise ReviewedBundleError("bundle documents and relationships must be arrays")
    typed_items: dict[str, tuple[str, dict[str, Any]]] = {}
    for item_type, values in (("document", documents), ("relationship", relationships)):
        for value in values:
            if not isinstance(value, dict):
                raise ReviewedBundleError(f"bundle {item_type} entries must be objects")
            item_id = _item_id(value.get("id"), f"{item_type} id")
            if item_id in typed_items:
                raise ReviewedBundleError(f"duplicate bundle item id {item_id!r}")
            typed_items[item_id] = (item_type, value)

    approved_values = manifest.get("approvedItemIds")
    if not isinstance(approved_values, list) or not approved_values:
        raise ReviewedBundleError("approval manifest must name at least one approved item id")
    approved_ids = [_item_id(value, "approved item id") for value in approved_values]
    if len(set(approved_ids)) != len(approved_ids):
        raise ReviewedBundleError("approval manifest contains duplicate item ids")
    missing = sorted(set(approved_ids) - set(typed_items))
    if missing:
        raise ReviewedBundleError(f"approval manifest names unknown item ids: {missing}")
    review_queue = bundle.get("reviewQueue", [])
    if not isinstance(review_queue, list):
        raise ReviewedBundleError("bundle reviewQueue must be an array")
    review_ids = {
        str(item.get("id") if isinstance(item, dict) else item or "").strip()
        for item in review_queue
    }
    overlap = sorted(set(approved_ids) & review_ids)
    if overlap:
        raise ReviewedBundleError(f"approval manifest includes review-queue items: {overlap}")

    approved_documents: list[dict[str, Any]] = []
    approved_relationships: list[dict[str, Any]] = []
    for item_id in sorted(approved_ids):
        item_type, item = typed_items[item_id]
        _validate_contract(
            item, item_id=item_id, tenant=tenant, workspace=workspace,
            physical_scope=scope, graph_version=graph_version,
        )
        (approved_documents if item_type == "document" else approved_relationships).append(item)

    # The upstream approval is necessary but not sufficient. Re-screen the
    # exact approved objects locally before deriving any vault path or index.
    screening = _screen_approved_items(approved_documents, approved_relationships)

    receipt_id = _sha256(
        f"{tenant}:{workspace}:{scope}:{bundle_sha}:{manifest_sha}".encode("utf-8")
    )[:24]
    pages = _render_pages(
        documents=approved_documents, relationships=approved_relationships,
        tenant=tenant, workspace=workspace, scope=scope,
        graph_version=graph_version, bundle_sha=bundle_sha,
        manifest_sha=manifest_sha, receipt_id=receipt_id,
    )
    if len({page.relative_path for page in pages}) != len(pages):
        raise ReviewedBundleError("approved item IDs collide on a deterministic page path")
    tenant_root = tenant_vault_path(vault_base, scope)
    receipt_path = tenant_root / "receipts" / f"knowledge-import-{receipt_id}.json"
    with _import_lock(tenant_root):
        return await _commit_reviewed_import(
            tenant_root=tenant_root, receipt_path=receipt_path, pages=pages,
            receipt_id=receipt_id, tenant=tenant, workspace=workspace, scope=scope,
            bundle_schema=bundle_schema, bundle_sha=bundle_sha,
            graph_version=graph_version, manifest_sha=manifest_sha,
            approved_ids=approved_ids, approved_by=approved_by,
            approved_at=approved_at, screening=screening, imported_at=imported_at,
        )
