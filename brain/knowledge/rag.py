"""VaultRagIndex — the disposable RAG projection of the vault.

The vault's plain markdown (wiki/ + raw/) is the source of truth; this index
is a rebuildable projection of it (per vault/SCHEMA.md). It chunks pages by
heading section, stores chunks + optional embeddings in sqlite, and serves
hybrid search: keyword overlap + vector cosine when an embedder is wired,
degrading fail-safe to lexical when not (cosine mapped to [0, 1] exactly as
memory/_scoring does, though the blend weights here are the vault's own).

Obsidian is only a *viewer* over these files. The brain reads the vault
through this index, so nothing depends on Obsidian being open, installed,
or ever used — and if the index is lost or corrupted, rebuild() restores it
entirely from the markdown. Stdlib-only.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import uuid
import weakref
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from prepende_brain.private_fs import prepare_private_sqlite

_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_HEADING = re.compile(r"^#{1,4} ")
_MAX_CHUNK = 1600
# refresh() runs before every search (vault.py), so embedding backfill must be
# bounded — a large backlog is repaired across successive refreshes instead of
# stalling the first query after an embedder outage.
_EMBED_BACKFILL_CAP = 64
_SQLITE_BUSY_TIMEOUT_MS = 15_000
_SOURCE_RETRY_LIMIT = 3
_USE_CONFIGURED_EMBEDDER = object()

_PROVENANCE_FIELDS = {
    "knowledge_id": "sourceId",
    "tenant": "tenant",
    "workspace": "workspace",
    "scope": "scope",
    "graph_version": "graphVersion",
    "bundle_sha256": "bundleSha256",
    "approval_manifest_sha256": "approvalManifestSha256",
    "approval_status": "approvalStatus",
    "import_receipt_id": "importReceiptId",
    "source_document": "sourceDocument",
    "source_location": "sourceLocation",
    "source_kind": "sourceKind",
    "confidence": "confidence",
    "confidence_label": "confidenceLabel",
    "graph_node_id": "graphNodeId",
    "relationship_source": "relationshipSource",
    "relationship_target": "relationshipTarget",
    "relationship_type": "relationshipType",
}


class _SourceChanged(RuntimeError):
    """A markdown source could not be read as one stable byte snapshot."""


@dataclass(frozen=True)
class _SourceSnapshot:
    absolute_path: Path
    relative_path: str
    page: str
    mtime: float
    mtime_ns: int
    size: int
    content_hash: str
    metadata_json: str
    chunks: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _PreparedSource:
    source: _SourceSnapshot
    vectors: tuple[list[float] | None, ...]


# Rebuild/refresh can be reached through more than one VaultKnowledge instance.
# Keep the expensive prepare + commit sequence single-writer per resolved index
# path within an event loop, and keep every synchronous SQLite mutation guarded
# across loops/threads.  The latter is safe because it is never held across an
# await.
_LOCKS_GUARD = threading.Lock()
_COMMIT_LOCKS: dict[str, threading.RLock] = {}
_ASYNC_WRITER_LOCKS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[str, asyncio.Lock]
] = weakref.WeakKeyDictionary()


def _commit_lock(path: str) -> threading.RLock:
    with _LOCKS_GUARD:
        return _COMMIT_LOCKS.setdefault(path, threading.RLock())


def _async_writer_lock(path: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    with _LOCKS_GUARD:
        by_path = _ASYNC_WRITER_LOCKS.setdefault(loop, {})
        return by_path.setdefault(path, asyncio.Lock())


def _default_index_path(vault: Path) -> str:
    """Return a stable index path that cannot be shared across vaults.

    The configured operator vault keeps the legacy ``vault_index.db`` location.
    Every tenant/custom vault gets a deterministic path-derived file so two
    scopes can never delete or replace one another's RAG rows.
    """
    memory_db = (os.environ.get("MEMORY_DB", "./.engram/memory.db")
                 or "./.engram/memory.db").strip()
    state_dir = Path(memory_db).expanduser().resolve().parent
    resolved_vault = vault.expanduser().resolve()
    configured_vault = Path(
        (os.environ.get("VAULT_PATH", "./vault") or "./vault").strip()
    ).expanduser().resolve()
    if resolved_vault == configured_vault:
        override = (os.environ.get("VAULT_INDEX_PATH") or "").strip()
        return str(Path(override).expanduser()) if override else str(state_dir / "vault_index.db")
    digest = hashlib.sha256(str(resolved_vault).encode("utf-8")).hexdigest()[:16]
    return str(state_dir / "vault_indexes" / f"{digest}.db")


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity mapped to [0, 1] — mirrors memory/_scoring.cosine so
    the blend stays well-behaved: a negative raw cosine must never go below
    zero and erase a chunk's lexical match (results are filtered on score>0)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if not na or not nb:
        return 0.0
    return max(0.0, min(1.0, (dot / (na * nb) + 1.0) / 2.0))


def _heading_starts(body: str) -> list[int]:
    """Offsets of heading lines, ignoring any inside ```/~~~ fences — a
    '# comment' line in a fenced code block is code, not a section boundary,
    and splitting there would cut the block in half under a bogus title."""
    starts: list[int] = []
    fence: str | None = None  # the marker that opened the current fence, if any
    pos = 0
    for line in body.splitlines(keepends=True):
        stripped = line.lstrip()
        if fence is None and (stripped.startswith("```") or stripped.startswith("~~~")):
            fence = stripped[:3]
        elif fence is not None and stripped.startswith(fence):
            fence = None
        elif fence is None and _HEADING.match(line):
            starts.append(pos)
        pos += len(line)
    return starts


def _chunk_page(text: str) -> list[tuple[str, str]]:
    """Split a page into (section_title, content) chunks at headings; long
    sections split again at paragraph boundaries."""
    body = _FRONTMATTER.sub("", text).strip()
    if not body:
        return []
    pieces: list[tuple[str, str]] = []
    starts = _heading_starts(body) or [0]
    if starts[0] != 0:
        starts.insert(0, 0)
    starts.append(len(body))
    for i in range(len(starts) - 1):
        seg = body[starts[i]:starts[i + 1]].strip()
        if not seg:
            continue
        first_line = seg.splitlines()[0]
        title = first_line.lstrip("# ").strip() if first_line.startswith("#") else ""
        while len(seg) > _MAX_CHUNK:
            cut = seg.rfind("\n\n", 0, _MAX_CHUNK)
            cut = cut if cut > 200 else _MAX_CHUNK
            pieces.append((title, seg[:cut].strip()))
            seg = seg[cut:].strip()
        if seg:
            pieces.append((title, seg))
    return pieces


def _frontmatter_provenance(text: str) -> dict[str, Any]:
    """Expose only reviewed provenance scalars from a Markdown page.

    The values are captured from the same stable byte snapshot as the indexed
    content. Search therefore cannot pair an older chunk with provenance read
    later from a changed page; normal refresh rules update both together.
    """

    match = _FRONTMATTER.match(text)
    if match is None:
        return {}
    lines = match.group(0).splitlines()[1:-1]
    result: dict[str, Any] = {}
    for line in lines:
        if ":" not in line or line[:1].isspace():
            continue
        key, raw = line.split(":", 1)
        output_key = _PROVENANCE_FIELDS.get(key.strip())
        if output_key is None:
            continue
        value_text = raw.strip()
        try:
            value = json.loads(value_text)
        except (TypeError, ValueError, json.JSONDecodeError):
            value = value_text.strip("\"'")
        if value not in (None, ""):
            result[output_key] = value
    if "sourceId" in result:
        result["knowledgeSourceId"] = result["sourceId"]
    return result


class VaultRagIndex:
    def __init__(self, vault_path: str = "./vault", index_path: str | None = None) -> None:
        self.vault = Path(vault_path)
        self.path = str(prepare_private_sqlite(index_path or _default_index_path(self.vault)))
        self._lock_path = str(Path(self.path).expanduser().resolve())
        self._embedder = None
        self._embedding_profile = ""
        self._expected_dimension: int | None = None
        with _commit_lock(self._lock_path):
            with closing(self._conn()) as c, c:
                # WAL lets readers proceed during the short commit phase. The
                # embeddings are prepared before this connection enters a
                # write transaction, so provider latency never holds a SQLite
                # lock.
                c.execute("PRAGMA journal_mode=WAL")
                c.execute(
                    """CREATE TABLE IF NOT EXISTS chunks (
                        id TEXT PRIMARY KEY,
                        path TEXT NOT NULL,
                        page TEXT NOT NULL,
                        section TEXT,
                        content TEXT NOT NULL,
                        mtime REAL NOT NULL,
                        embedding TEXT,
                        metadata TEXT NOT NULL DEFAULT '{}'
                    )"""
                )
                columns = {
                    str(row["name"]) for row in c.execute("PRAGMA table_info(chunks)").fetchall()
                }
                if "metadata" not in columns:
                    c.execute(
                        "ALTER TABLE chunks ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'"
                    )
                c.execute("CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path)")
                c.execute(
                    "CREATE TABLE IF NOT EXISTS index_meta ("
                    "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                c.execute(
                    """CREATE TABLE IF NOT EXISTS source_files (
                        path TEXT PRIMARY KEY,
                        mtime_ns INTEGER NOT NULL,
                        size INTEGER NOT NULL,
                        content_hash TEXT NOT NULL,
                        chunk_count INTEGER NOT NULL
                    )"""
                )
                self._migrate_legacy_source_metadata(c)

    @property
    def embedding_profile(self) -> str:
        return self._embedding_profile

    @property
    def expected_dimension(self) -> int | None:
        return self._expected_dimension

    @staticmethod
    def _dimension_value(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, float) and (
            not math.isfinite(value) or not value.is_integer()
        ):
            return None
        try:
            dimension = int(value)
        except (TypeError, ValueError):
            return None
        return dimension if dimension > 0 else None

    def set_embedder(
        self,
        embedder: Any,
        *,
        profile: str = "",
        expected_dimension: int | None = None,
    ) -> dict[str, Any]:
        """Wire the embedder and invalidate vectors when its space changes.

        Equal-dimensional models are not necessarily comparable. Persisting a
        provider/model/dimension fingerprint prevents mixed vector spaces while
        retaining lexical recall until bounded backfill completes.
        """
        self._embedder = embedder
        normalized = str(profile or "").strip()
        self._embedding_profile = normalized
        if expected_dimension is not None:
            configured_dimension = self._dimension_value(expected_dimension)
            if configured_dimension is None:
                raise ValueError("expected_dimension must be a positive integer")
        else:
            configured_dimension = None
        with _commit_lock(self._lock_path):
            with closing(self._conn()) as c, c:
                row = c.execute(
                    "SELECT value FROM index_meta WHERE key='embedding_profile'"
                ).fetchone()
                previous = str(row["value"]) if row is not None else ""
                previous_dimension = self._persisted_dimension(c)
                # A caller that omits the optional dimension inherits it only
                # for the same durable profile. A new profile must infer its
                # own space from its first valid provider response.
                if configured_dimension is None and previous == normalized:
                    configured_dimension = previous_dimension
                self._expected_dimension = configured_dimension
                profile_changed = previous != normalized
                dimension_changed = (
                    previous_dimension is not None
                    and configured_dimension is not None
                    and previous_dimension != configured_dimension
                )
                invalidated = 0
                if profile_changed or dimension_changed:
                    invalidated = int(c.execute(
                        "SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL"
                    ).fetchone()[0])
                    c.execute("UPDATE chunks SET embedding=NULL WHERE embedding IS NOT NULL")
                c.execute(
                    "INSERT INTO index_meta(key,value) VALUES('embedding_profile',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (normalized,),
                )
                if configured_dimension is None:
                    if profile_changed:
                        c.execute(
                            "DELETE FROM index_meta WHERE key='embedding_dimension'"
                        )
                else:
                    c.execute(
                        "INSERT INTO index_meta(key,value) "
                        "VALUES('embedding_dimension',?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (str(configured_dimension),),
                    )
        return {
            "changed": bool(profile_changed or dimension_changed),
            "invalidated": invalidated,
        }

    def _conn(self) -> sqlite3.Connection:
        prepare_private_sqlite(self.path)
        conn = sqlite3.connect(
            self.path,
            timeout=_SQLITE_BUSY_TIMEOUT_MS / 1000,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA synchronous=NORMAL")
        prepare_private_sqlite(self.path)
        return conn

    @staticmethod
    def _metadata(source: _SourceSnapshot) -> tuple[int, int, str]:
        return source.mtime_ns, source.size, source.content_hash

    def _capture_source(self, path: Path) -> _SourceSnapshot:
        """Read one source as a stable byte/stat/hash snapshot.

        A double-read closes the same-size/same-mtime hole: an editor can
        replace bytes and restore the timestamp while an embedding request is
        in flight.  We only certify bytes observed twice under one stat
        signature, and later re-check the digest before committing.
        """
        for _ in range(_SOURCE_RETRY_LIMIT):
            try:
                before = path.stat()
                first = path.read_bytes()
                middle = path.stat()
                second = path.read_bytes()
                after = path.stat()
            except OSError:
                continue
            before_sig = (
                getattr(before, "st_dev", 0), getattr(before, "st_ino", 0),
                before.st_size, before.st_mtime_ns,
            )
            middle_sig = (
                getattr(middle, "st_dev", 0), getattr(middle, "st_ino", 0),
                middle.st_size, middle.st_mtime_ns,
            )
            after_sig = (
                getattr(after, "st_dev", 0), getattr(after, "st_ino", 0),
                after.st_size, after.st_mtime_ns,
            )
            if (before_sig != middle_sig or middle_sig != after_sig
                    or first != second or len(second) != after.st_size):
                continue
            text = second.decode("utf-8", errors="replace")
            return _SourceSnapshot(
                absolute_path=path,
                relative_path=str(path.relative_to(self.vault)),
                page=path.stem,
                mtime=float(after.st_mtime),
                mtime_ns=int(after.st_mtime_ns),
                size=int(after.st_size),
                content_hash=hashlib.sha256(second).hexdigest(),
                metadata_json=json.dumps(
                    _frontmatter_provenance(text), sort_keys=True,
                    separators=(",", ":"),
                ),
                chunks=tuple(_chunk_page(text)),
            )
        raise _SourceChanged(f"source changed while being read: {path}")

    def _capture_inventory(self) -> dict[str, _SourceSnapshot]:
        return {
            source.relative_path: source
            for source in (self._capture_source(path) for path in self._source_files())
        }

    def _inventory_matches(self, expected: dict[str, _SourceSnapshot]) -> bool:
        try:
            current = self._capture_inventory()
        except _SourceChanged:
            return False
        return (
            set(current) == set(expected)
            and all(self._metadata(current[path]) == self._metadata(source)
                    for path, source in expected.items())
        )

    def _migrate_legacy_source_metadata(self, c: sqlite3.Connection) -> None:
        """Adopt old rows and backfill provenance without discarding vectors.

        A path is changed only when its current markdown projection exactly
        matches the stored chunks. Existing ``source_files`` metadata must also
        match the current byte snapshot. This lets an upgraded index attach
        reviewed provenance to legacy chunks while preserving embeddings; any
        ambiguous path stays stale for the normal refresh path.
        """
        paths = c.execute("SELECT DISTINCT path FROM chunks").fetchall()
        for row in paths:
            relative = str(row["path"])
            source_path = self.vault / relative
            if not source_path.is_file():
                continue
            try:
                source = self._capture_source(source_path)
            except _SourceChanged:
                continue
            projected = Counter(source.chunks)
            stored = Counter(
                (str(item["section"] or ""), str(item["content"]))
                for item in c.execute(
                    "SELECT section,content FROM chunks WHERE path=?", (relative,)
                ).fetchall()
            )
            if projected != stored:
                continue
            source_metadata = self._metadata(source)
            existing = c.execute(
                "SELECT mtime_ns,size,content_hash,chunk_count "
                "FROM source_files WHERE path=?",
                (relative,),
            ).fetchone()
            if existing is None:
                c.execute(
                    "INSERT INTO source_files"
                    "(path,mtime_ns,size,content_hash,chunk_count) VALUES(?,?,?,?,?)",
                    (relative, source.mtime_ns, source.size, source.content_hash,
                     len(source.chunks)),
                )
            elif (
                int(existing["mtime_ns"]), int(existing["size"]),
                str(existing["content_hash"]),
            ) != source_metadata or int(existing["chunk_count"]) != len(source.chunks):
                continue
            c.execute(
                "UPDATE chunks SET metadata=? "
                "WHERE path=? AND COALESCE(metadata,'')<>?",
                (source.metadata_json, relative, source.metadata_json),
            )

    async def _embed_many(
        self,
        texts: Sequence[str],
        *,
        embedder: Any = _USE_CONFIGURED_EMBEDDER,
        expected_dimension: int | None = None,
    ) -> list[list[float] | None]:
        """Embed a batch while preserving lexical-only failure semantics.

        Rebuild used to make one HTTP request per chunk.  The local Ollama lane
        accepts a list, as do the hosted embedding adapters, so batching by file
        (and by refresh cap) makes a complete 500+ chunk rebuild practical.  A
        malformed or partial provider response invalidates only that batch;
        the markdown remains searchable lexically and status reports the gap.
        """
        values = list(texts)
        active_embedder = (
            self._embedder if embedder is _USE_CONFIGURED_EMBEDDER else embedder
        )
        if active_embedder is None or not values:
            return [None for _ in values]
        try:
            out = list(await active_embedder(values))
            if len(out) != len(values):
                return [None for _ in values]
            dimension = expected_dimension or self._expected_dimension
            validated: list[list[float] | None] = []
            for raw_vector in out:
                vector = self._validate_vector(raw_vector)
                if vector is None:
                    validated.append(None)
                    continue
                if dimension is None:
                    dimension = len(vector)
                    self._expected_dimension = dimension
                if len(vector) != dimension:
                    validated.append(None)
                    continue
                validated.append(vector)
            return validated
        except Exception:
            return [None for _ in values]  # fail-safe: lexical recall still works

    @staticmethod
    def _validate_vector(value: Any) -> list[float] | None:
        if value is None or isinstance(value, (str, bytes, bytearray, dict)):
            return None
        try:
            raw = list(value)
        except (TypeError, ValueError):
            return None
        if not raw:
            return None
        vector: list[float] = []
        for coordinate in raw:
            if isinstance(coordinate, (bool, str, bytes, bytearray)):
                return None
            try:
                number = float(coordinate)
            except (TypeError, ValueError, OverflowError):
                return None
            if not math.isfinite(number):
                return None
            vector.append(number)
        return vector

    @classmethod
    def _decode_vector(cls, value: Any) -> list[float] | None:
        if value is None:
            return None
        try:
            decoded = json.loads(str(value))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return cls._validate_vector(decoded)

    async def _embed(
        self,
        text: str,
        *,
        expected_dimension: int | None = None,
    ) -> list[float] | None:
        return (
            await self._embed_many(
                [text], expected_dimension=expected_dimension
            )
        )[0]

    def _source_files(self) -> list[Path]:
        files: list[Path] = []
        for sub in ("wiki", "raw"):
            d = self.vault / sub
            if d.is_dir():
                files.extend(sorted(d.glob("*.md")))
        return files

    async def _prepare_source(
        self,
        source: _SourceSnapshot,
        *,
        embedder: Any | None,
    ) -> _PreparedSource:
        vectors = await self._embed_many(
            [content for _, content in source.chunks], embedder=embedder
        )
        return _PreparedSource(source=source, vectors=tuple(vectors))

    @staticmethod
    def _replace_source(
        c: sqlite3.Connection,
        prepared: _PreparedSource,
        *,
        include_vectors: bool,
    ) -> int:
        source = prepared.source
        c.execute("DELETE FROM chunks WHERE path = ?", (source.relative_path,))
        for (section, content), vec in zip(source.chunks, prepared.vectors):
            c.execute(
                "INSERT INTO chunks "
                "(id, path, page, section, content, mtime, embedding, metadata)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (f"vchk_{uuid.uuid4().hex[:12]}", source.relative_path,
                 source.page, section, content, source.mtime,
                 json.dumps(vec) if include_vectors and vec else None,
                 source.metadata_json),
            )
        c.execute(
            "INSERT INTO source_files(path,mtime_ns,size,content_hash,chunk_count) "
            "VALUES(?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
            "mtime_ns=excluded.mtime_ns,size=excluded.size,"
            "content_hash=excluded.content_hash,chunk_count=excluded.chunk_count",
            (source.relative_path, source.mtime_ns, source.size,
             source.content_hash, len(source.chunks)),
        )
        return len(source.chunks)

    def _persisted_profile(self, c: sqlite3.Connection) -> str:
        row = c.execute(
            "SELECT value FROM index_meta WHERE key='embedding_profile'"
        ).fetchone()
        return str(row["value"]) if row is not None else ""

    def _persisted_dimension(self, c: sqlite3.Connection) -> int | None:
        row = c.execute(
            "SELECT value FROM index_meta WHERE key='embedding_dimension'"
        ).fetchone()
        return self._dimension_value(row["value"]) if row is not None else None

    @staticmethod
    def _profiles_match(profile: str, persisted_profile: str) -> bool:
        # Two blank profiles retain the legacy unnamed-embedder behavior.
        return profile == persisted_profile

    def _space_can_accept_vectors(
        self,
        c: sqlite3.Connection,
        *,
        profile: str,
    ) -> bool:
        if not self._profiles_match(profile, self._persisted_profile(c)):
            return False
        persisted_dimension = self._persisted_dimension(c)
        expected_dimension = self._expected_dimension
        if (persisted_dimension is not None and expected_dimension is not None
                and persisted_dimension != expected_dimension):
            return False
        if expected_dimension is not None and persisted_dimension is None:
            c.execute(
                "INSERT INTO index_meta(key,value) VALUES('embedding_dimension',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(expected_dimension),),
            )
        return expected_dimension is not None

    async def rebuild(self) -> dict[str, int]:
        """Full reindex from the markdown. The index is disposable; this is
        the proof."""
        async with _async_writer_lock(self._lock_path):
            for _ in range(_SOURCE_RETRY_LIMIT):
                try:
                    sources = self._capture_inventory()
                except _SourceChanged:
                    continue
                embedder = self._embedder
                profile = self._embedding_profile
                prepared = [
                    await self._prepare_source(source, embedder=embedder)
                    for source in sources.values()
                ]
                if not self._inventory_matches(sources):
                    continue
                committed = False
                with _commit_lock(self._lock_path):
                    if (self._embedder is embedder
                            and self._embedding_profile == profile
                            and self._inventory_matches(sources)):
                        with closing(self._conn()) as c, c:
                            include_vectors = self._space_can_accept_vectors(
                                c, profile=profile
                            )
                            c.execute("DELETE FROM chunks")
                            c.execute("DELETE FROM source_files")
                            for item in prepared:
                                self._replace_source(
                                    c, item, include_vectors=include_vectors
                                )
                        committed = True
                if not committed:
                    continue
                status = self.status()
                if status["stale"]:
                    continue
                return {
                    "files": len(sources),
                    "chunks": int(status["chunks"]),
                    "embedded": int(status["embedded_chunks"]),
                    "missing": int(status["missing_embeddings"]),
                }
        raise RuntimeError(
            "vault sources did not stabilize during rebuild; index was not certified current"
        )

    async def refresh(self) -> dict[str, int]:
        """Re-index only files whose mtime changed (or are new); drop chunks
        for files that disappeared. Also backfills missing embeddings: chunks
        indexed while the embedder was absent/failing store embedding=NULL,
        and mtime alone would never repair them — they'd score vec=0 forever."""
        async with _async_writer_lock(self._lock_path):
            for _ in range(_SOURCE_RETRY_LIMIT):
                try:
                    sources = self._capture_inventory()
                except _SourceChanged:
                    continue
                with closing(self._conn()) as c:
                    c.execute("BEGIN")
                    try:
                        stored = {
                            str(row["path"]): (
                                int(row["mtime_ns"]), int(row["size"]),
                                str(row["content_hash"]),
                            )
                            for row in c.execute(
                                "SELECT path,mtime_ns,size,content_hash FROM source_files"
                            ).fetchall()
                        }
                        all_rows = c.execute(
                            "SELECT id,path,content,embedding FROM chunks"
                        ).fetchall()
                        chunk_paths = {str(row["path"]) for row in all_rows}
                        persisted_profile = self._persisted_profile(c)
                        persisted_dimension = self._persisted_dimension(c)
                    finally:
                        c.rollback()
                changed_sources = {
                    path: source for path, source in sources.items()
                    if stored.get(path) != self._metadata(source)
                }
                # Legacy rows predate source_files. They still participate in
                # freshness: a deleted legacy source must purge its chunks,
                # and a present one without metadata must be re-indexed.
                gone = (set(stored) | chunk_paths) - set(sources)
                embedder = self._embedder
                profile = self._embedding_profile
                space_matches = (
                    self._profiles_match(profile, persisted_profile)
                    and not (
                        self._expected_dimension is not None
                        and persisted_dimension is not None
                        and self._expected_dimension != persisted_dimension
                    )
                )
                operation_embedder = embedder if space_matches else None
                prepared = [
                    await self._prepare_source(source, embedder=operation_embedder)
                    for source in changed_sources.values()
                ]
                # Rows from a file about to be replaced cannot be useful
                # backfill candidates and would consume the bounded budget.
                expected_dimension = self._expected_dimension or persisted_dimension
                rows = [
                    row for row in all_rows
                    if str(row["path"]) not in changed_sources
                    and str(row["path"]) not in gone
                    and (
                        row["embedding"] is None
                        or (
                            expected_dimension is not None
                            and (
                                (decoded := self._decode_vector(row["embedding"])) is None
                                or len(decoded) != expected_dimension
                            )
                        )
                    )
                ][:_EMBED_BACKFILL_CAP]
                vectors = await self._embed_many(
                    [str(row["content"]) for row in rows],
                    embedder=operation_embedder,
                    expected_dimension=expected_dimension,
                ) if operation_embedder is not None else [None for _ in rows]
                if not self._inventory_matches(sources):
                    continue
                committed = False
                backfilled = 0
                with _commit_lock(self._lock_path):
                    if (self._embedder is embedder
                            and self._embedding_profile == profile
                            and self._inventory_matches(sources)):
                        with closing(self._conn()) as c, c:
                            include_vectors = self._space_can_accept_vectors(
                                c, profile=profile
                            )
                            for path in gone:
                                c.execute("DELETE FROM chunks WHERE path=?", (path,))
                                c.execute("DELETE FROM source_files WHERE path=?", (path,))
                            for item in prepared:
                                self._replace_source(
                                    c, item, include_vectors=include_vectors
                                )
                            if include_vectors:
                                for row, vector in zip(rows, vectors):
                                    if vector is None:
                                        continue
                                    updated = c.execute(
                                        "UPDATE chunks SET embedding=? "
                                        "WHERE id=? AND content=? AND embedding IS ?",
                                        (json.dumps(vector), row["id"], row["content"],
                                         row["embedding"]),
                                    ).rowcount
                                    backfilled += max(0, int(updated))
                        committed = True
                if not committed:
                    continue
                if self.status()["stale"]:
                    continue
                return {
                    "files": len(sources),
                    "reindexed": len(changed_sources),
                    "backfilled": backfilled,
                }
        raise RuntimeError(
            "vault sources did not stabilize during refresh; index was not certified current"
        )

    def status(self) -> dict[str, Any]:
        """Truthful readiness receipt for this disposable projection."""
        files = self._source_files()
        current: dict[str, tuple[int, int, str]] = {}
        unstable = False
        for path in files:
            try:
                source = self._capture_source(path)
                current[source.relative_path] = self._metadata(source)
            except _SourceChanged:
                unstable = True
        with closing(self._conn()) as c:
            c.execute("BEGIN")
            try:
                row = c.execute("SELECT COUNT(*) AS chunks FROM chunks").fetchone()
                indexed_sources = {
                    str(r["path"]): (
                        int(r["mtime_ns"]), int(r["size"]), str(r["content_hash"])
                    )
                    for r in c.execute(
                        "SELECT path,mtime_ns,size,content_hash FROM source_files"
                    ).fetchall()
                }
                chunk_paths = {
                    str(r["path"])
                    for r in c.execute("SELECT DISTINCT path FROM chunks").fetchall()
                }
                embedding_rows = c.execute(
                    "SELECT embedding FROM chunks WHERE embedding IS NOT NULL"
                ).fetchall()
                persisted_profile = self._persisted_profile(c)
                persisted_dimension = self._persisted_dimension(c)
            finally:
                c.rollback()
        chunks = int(row["chunks"] or 0)
        expected_dimension = self._expected_dimension or persisted_dimension
        stored_dimensions: set[int] = set()
        valid_embedded = 0
        invalid_embeddings = 0
        for embedding_row in embedding_rows:
            vector = self._decode_vector(embedding_row["embedding"])
            if vector is None:
                invalid_embeddings += 1
                continue
            stored_dimensions.add(len(vector))
            if expected_dimension is not None and len(vector) == expected_dimension:
                valid_embedded += 1
            else:
                invalid_embeddings += 1
        actual_dimension = (
            next(iter(stored_dimensions)) if len(stored_dimensions) == 1 else None
        )
        orphan_chunk_paths = chunk_paths - set(indexed_sources)
        stale = (
            unstable or bool(orphan_chunk_paths)
            or set(current) != set(indexed_sources)
            or any(
                indexed_sources.get(path) != metadata
                for path, metadata in current.items()
            )
        )
        profile_mismatch = not self._profiles_match(
            self._embedding_profile, persisted_profile
        )
        configured_dimension_mismatch = (
            self._expected_dimension is not None
            and persisted_dimension is not None
            and self._expected_dimension != persisted_dimension
        )
        dimension_ready = (
            expected_dimension is not None
            and persisted_dimension == expected_dimension
            and invalid_embeddings == 0
            and (not embedding_rows or stored_dimensions == {expected_dimension})
        )
        return {
            "vault_path": str(self.vault.expanduser().resolve()),
            "index_path": str(Path(self.path).expanduser().resolve()),
            "source_files": len(files),
            "indexed_files": len(indexed_sources),
            "chunks": chunks,
            "embedded_chunks": valid_embedded,
            "stored_embedding_chunks": len(embedding_rows),
            "missing_embeddings": max(0, chunks - valid_embedded),
            "invalid_embeddings": invalid_embeddings,
            "configured_profile": self._embedding_profile,
            "persisted_profile": persisted_profile,
            "embedding_profile_mismatch": profile_mismatch,
            "configured_dimension": self._expected_dimension,
            "persisted_dimension": persisted_dimension,
            "expected_dimension": expected_dimension,
            "stored_dimensions": sorted(stored_dimensions),
            "actual_dimension": actual_dimension,
            "embedding_dimension_mismatch": (
                configured_dimension_mismatch
                or (bool(embedding_rows) and not dimension_ready)
            ),
            "orphan_chunk_paths": len(orphan_chunk_paths),
            "lexical_ready": chunks > 0 and not stale,
            "semantic_ready": (
                self._embedder is not None
                and not profile_mismatch
                and dimension_ready
                and chunks > 0 and valid_embedded == chunks and not stale
            ),
            "stale": stale,
        }

    async def backfill_all(self, *, max_rounds: int = 100) -> dict[str, Any]:
        """Converge missing vectors or return a precise non-convergence receipt."""
        before = self.status()
        if before["embedding_profile_mismatch"]:
            return {
                "complete": False,
                "reason": "embedding_profile_mismatch",
                "rounds": 0,
                "backfilled": 0,
                "status": before,
            }
        if self._embedder is None:
            return {
                "complete": False,
                "reason": "embedding_provider_not_configured",
                "rounds": 0,
                "backfilled": 0,
                "status": before,
            }
        rounds = 0
        backfilled = 0
        previous_missing: int | None = None
        while rounds < max(1, max_rounds):
            current = self.status()
            if current["embedding_profile_mismatch"]:
                return {
                    "complete": False,
                    "reason": "embedding_profile_mismatch",
                    "rounds": rounds,
                    "backfilled": backfilled,
                    "status": current,
                }
            if current["semantic_ready"]:
                return {
                    "complete": True,
                    "reason": "converged",
                    "rounds": rounds,
                    "backfilled": backfilled,
                    "status": current,
                }
            receipt = await self.refresh()
            rounds += 1
            backfilled += int(receipt.get("backfilled", 0))
            updated = self.status()
            missing = int(updated["missing_embeddings"])
            if (missing == previous_missing and not receipt.get("reindexed")
                    and not receipt.get("backfilled")):
                return {
                    "complete": False,
                    "reason": "embedding_provider_made_no_progress",
                    "rounds": rounds,
                    "backfilled": backfilled,
                    "status": updated,
                }
            previous_missing = missing
        final = self.status()
        return {
            "complete": bool(final["semantic_ready"]),
            "reason": "converged" if final["semantic_ready"] else "max_rounds_exceeded",
            "rounds": rounds,
            "backfilled": backfilled,
            "status": final,
        }

    async def search(self, query: str, k: int = 8) -> list[dict[str, Any]]:
        # Alphanumeric tokenization: raw split() kept trailing punctuation
        # ('prepende?' never substring-matches), and >2 dropped acronyms like
        # 'AI'/'ML' entirely — a lexical-only query of short terms returned
        # nothing. Keep >=2 so acronyms score; 1-char tokens are still noise.
        terms = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) >= 2]
        # Profile metadata and chunks must come from one SQLite read snapshot.
        # Otherwise a concurrent profile switch can pair an old query vector
        # with rows from a new vector space.
        with closing(self._conn()) as c:
            c.execute("BEGIN")
            try:
                persisted_profile = self._persisted_profile(c)
                persisted_dimension = self._persisted_dimension(c)
                rows = c.execute("SELECT * FROM chunks").fetchall()
            finally:
                c.rollback()
        if not rows:
            return []
        expected_dimension = self._expected_dimension or persisted_dimension
        stored_space_valid = expected_dimension is not None
        if stored_space_valid:
            for row in rows:
                if row["embedding"] is None:
                    continue
                stored_vector = self._decode_vector(row["embedding"])
                if stored_vector is None or len(stored_vector) != expected_dimension:
                    stored_space_valid = False
                    break
        semantic_allowed = (
            self._embedder is not None
            and self._profiles_match(self._embedding_profile, persisted_profile)
            and persisted_dimension == expected_dimension
            and stored_space_valid
        )
        qvec = (
            await self._embed(query, expected_dimension=expected_dimension)
            if semantic_allowed else None
        )

        def kw(r: sqlite3.Row) -> float:
            if not terms:
                return 0.0
            text = (r["content"] + " " + r["page"] + " " + (r["section"] or "")).lower()
            return sum(t in text for t in terms) / len(terms)

        def vec(r: sqlite3.Row) -> float:
            if qvec is None or not r["embedding"]:
                return 0.0
            stored_vector = self._decode_vector(r["embedding"])
            if stored_vector is None:
                return 0.0
            return _cosine(qvec, stored_vector)

        semantic = qvec is not None
        def blend(r: sqlite3.Row) -> float:
            return 0.6 * vec(r) + 0.4 * kw(r) if semantic else kw(r)

        scored = [(blend(r), r) for r in rows]
        scored.sort(key=lambda p: p[0], reverse=True)

        def result(score: float, row: sqlite3.Row) -> dict[str, Any]:
            relative = str(row["path"])
            try:
                provenance = json.loads(str(row["metadata"] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                provenance = {}
            if not isinstance(provenance, dict):
                provenance = {}
            hit = {
                "page": row["page"], "section": row["section"], "path": relative,
                "content": row["content"], "score": round(score, 4),
            }
            if provenance:
                hit.update(provenance)
                hit["metadata"] = dict(provenance)
            return hit

        return [result(s, r) for s, r in scored[:k] if s > 0]
