"""Append-only, chained Prepende Protocol v2 ledger."""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional

from .canonical import PROTOCOL, canonical_bytes, digest, file_digest, require_digest
from .contract import (
    Contract,
    ContractValidationError,
    Disposition,
    Resolution,
    build_contract,
    parse_timestamp,
    timestamp_now,
)
from .evaluators import EvaluationError, evaluate, normalize_evidence, require_evaluation_artifacts
from .signing import SignatureError, SignatureUnavailable, TrustedKey, verify_detached

try:  # POSIX is the supported reference implementation target.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


STATUS_OK = "OK"
STATUS_UNANCHORED = "UNANCHORED"
STATUS_INCOMPLETE = "INCOMPLETE"
STATUS_TAMPERED = "TAMPERED"
_STREAM_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,199}$")


class RetrofitError(ValueError):
    """Raised when an append would alter a locked rule or terminal state."""


class LedgerIntegrityError(RuntimeError):
    """Raised when a ledger cannot safely accept or return records."""


@dataclass
class IntegrityReport:
    protocol: str = PROTOCOL
    status: str = STATUS_UNANCHORED
    internally_valid: bool = False
    anchored: bool = False
    complete_through: int | None = None
    independently_resolved: bool = False
    stream_id: str | None = None
    head_hash: str | None = None
    row_count: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)
    overdue: list[str] = field(default_factory=list)
    unanchored_contracts: list[str] = field(default_factory=list)
    unwitnessed_terminals: list[str] = field(default_factory=list)
    untrusted_resolutions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


@dataclass
class _Scan:
    rows: list[dict[str, Any]] = field(default_factory=list)
    contracts: dict[str, Contract] = field(default_factory=dict)
    contract_seq: dict[str, int] = field(default_factory=dict)
    logical_keys: set[tuple[str, str, str]] = field(default_factory=set)
    terminals: dict[str, Resolution | Disposition] = field(default_factory=dict)
    terminal_seq: dict[str, int] = field(default_factory=dict)
    checkpoints: dict[str, tuple[dict[str, Any], dict[str, Any]]] = field(default_factory=dict)
    anchors: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stream_id: str | None = None
    registered_predictor: str | None = None
    anchor_policy: str | None = None
    latest_event_at: datetime | None = None


def _trusted_key(mapping: Mapping[str, Any] | None, key_id: str) -> TrustedKey | None:
    if not mapping or key_id not in mapping:
        return None
    return TrustedKey.from_value(key_id, mapping[key_id])


class Ledger:
    """Protocol v2 stream.

    Construction opens an existing v2 stream. Use :meth:`create` or
    :meth:`from_legacy` to initialize a new stream. The private ``_clock`` seam exists
    only for deterministic tests; public lock/resolve methods do not accept timestamps.
    """

    def __init__(self, path: str | os.PathLike[str], *, _clock: Callable[[], float] = time.time):
        self.path = str(path)
        self._clock = _clock
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"v2 ledger does not exist; call Ledger.create({self.path!r}, ...)")

    @classmethod
    def create(cls, path: str | os.PathLike[str], *, stream_id: str,
               registered_predictor: str, anchor_policy: str = "required",
               _clock: Callable[[], float] = time.time) -> "Ledger":
        path = str(path)
        if not _STREAM_RE.fullmatch(stream_id):
            raise ValueError("stream_id must be a stable lowercase protocol identifier")
        predictor = unicodedata.normalize("NFC", str(registered_predictor or "").strip())
        if not predictor or len(predictor) > 200:
            raise ValueError("registered_predictor is required and must be at most 200 characters")
        if anchor_policy not in {"required", "optional"}:
            raise ValueError("anchor_policy must be required or optional")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "x", encoding="utf-8"):
            pass
        ledger = cls(path, _clock=_clock)
        ledger._append_event("genesis", {
            "protocol": PROTOCOL,
            "stream_id": stream_id,
            "registered_predictor": predictor,
            "anchor_policy": anchor_policy,
            "created_at": timestamp_now(_clock),
        })
        return ledger

    @classmethod
    def from_legacy(cls, path: str | os.PathLike[str], *, legacy_path: str | os.PathLike[str],
                    stream_id: str, registered_predictor: str, git_commit: str,
                    anchor_policy: str = "required",
                    _clock: Callable[[], float] = time.time) -> "Ledger":
        ledger = cls.create(
            path,
            stream_id=stream_id,
            registered_predictor=registered_predictor,
            anchor_policy=anchor_policy,
            _clock=_clock,
        )
        data = Path(legacy_path).read_bytes()
        rows = [line for line in data.splitlines() if line.strip()]
        ledger._append_event("legacy_import", {
            "source_protocol": "prepende/1",
            "source_digest": file_digest(data),
            "source_bytes": len(data),
            "source_rows": len(rows),
            "git_commit": str(git_commit),
            "classification": "legacy-self-attested",
            "imported_at": timestamp_now(_clock),
        })
        return ledger

    @contextmanager
    def _locked(self, *, exclusive: bool) -> Iterator[Any]:
        mode = "r+" if exclusive else "r"
        with open(self.path, mode, encoding="utf-8", newline="") as handle:
            if fcntl is None:  # pragma: no cover
                raise RuntimeError("Prepende reference ledger requires POSIX advisory file locking")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield handle
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _load(handle: Any) -> tuple[list[dict[str, Any]], list[str]]:
        handle.seek(0)
        try:
            text = handle.read()
        except UnicodeDecodeError as exc:
            return [], [f"ledger is not valid UTF-8: {exc}"]
        errors: list[str] = []
        if text and not text.endswith("\n"):
            errors.append("partial final row: ledger must end with a newline")
        rows: list[dict[str, Any]] = []

        def object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            normalized: set[str] = set()
            for key, item in pairs:
                normalized_key = unicodedata.normalize("NFC", key)
                if normalized_key in normalized:
                    raise ValueError(f"duplicate JSON object key: {normalized_key!r}")
                normalized.add(normalized_key)
                value[key] = item
            return value

        physical_lines = text.split("\n")
        if physical_lines and physical_lines[-1] == "":
            physical_lines.pop()
        for line_number, line in enumerate(physical_lines, 1):
            if not line.strip():
                errors.append(f"line {line_number}: blank rows are forbidden")
                continue
            try:
                value = json.loads(line, object_pairs_hook=object_without_duplicates)
            except (json.JSONDecodeError, ValueError) as exc:
                errors.append(f"line {line_number}: invalid JSON: {getattr(exc, 'msg', str(exc))}")
                continue
            if not isinstance(value, dict):
                errors.append(f"line {line_number}: row must be an object")
                continue
            try:
                if line != canonical_bytes(value).decode("utf-8"):
                    errors.append(f"line {line_number}: non-canonical row encoding")
            except Exception as exc:
                errors.append(f"line {line_number}: non-canonical content: {exc}")
            rows.append(value)
        return rows, errors

    @staticmethod
    def _row(event_type: str, event: dict[str, Any], *, seq: int,
             prev_hash: str | None) -> dict[str, Any]:
        base = {
            "protocol": PROTOCOL,
            "seq": seq,
            "prev_hash": prev_hash,
            "event_type": event_type,
            "event": event,
            "event_hash": digest(f"event/{event_type}", event),
        }
        return {**base, "row_hash": digest("row", base)}

    @staticmethod
    def _semantic_time(event_type: str, event: dict[str, Any]) -> datetime | None:
        """Return the protocol time asserted by an event, when it has one."""
        fields = {
            "genesis": ("created_at", "genesis.created_at"),
            "legacy_import": ("imported_at", "legacy_import.imported_at"),
            "contract": ("issued_at", "contract.issued_at"),
            "resolution": ("resolved_at", "resolution.resolved_at"),
            "forfeit": ("at", "forfeit.at"),
            "void": ("at", "void.at"),
            "checkpoint": ("created_at", "checkpoint.created_at"),
        }
        if event_type == "anchor":
            statement = event.get("statement")
            if not isinstance(statement, dict):
                return None
            return parse_timestamp(statement.get("anchored_at"), "anchor.anchored_at")
        field_info = fields.get(event_type)
        if field_info is None:
            return None
        field_name, label = field_info
        return parse_timestamp(event.get(field_name), label)

    @staticmethod
    def _note(value: Any, label: str = "note") -> str:
        if not isinstance(value, str):
            raise ValueError(f"{label} must be a string")
        normalized = unicodedata.normalize("NFC", value)
        if len(normalized) > 8000:
            raise ValueError(f"{label} must be at most 8000 characters")
        return normalized

    def _append_event(self, event_type: str, event: dict[str, Any]) -> dict[str, Any]:
        with self._locked(exclusive=True) as handle:
            rows, parse_errors = self._load(handle)
            scan = self._scan(rows, initial_errors=parse_errors)
            if scan.errors:
                raise LedgerIntegrityError("refusing append to invalid ledger: " + scan.errors[0])
            if not rows and event_type != "genesis":
                raise LedgerIntegrityError("first v2 row must be genesis")
            if rows and event_type == "genesis":
                raise LedgerIntegrityError("genesis already exists")
            row = self._row(
                event_type,
                event,
                seq=len(rows),
                prev_hash=rows[-1]["row_hash"] if rows else None,
            )
            candidate = self._scan(rows + [row])
            if candidate.errors:
                raise LedgerIntegrityError("refusing invalid event: " + candidate.errors[0])
            handle.seek(0, os.SEEK_END)
            handle.write(canonical_bytes(row).decode("utf-8") + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            return row

    def _append_factory(self, event_type: str,
                        factory: Callable[[_Scan], dict[str, Any]]) -> dict[str, Any]:
        with self._locked(exclusive=True) as handle:
            rows, parse_errors = self._load(handle)
            scan = self._scan(rows, initial_errors=parse_errors)
            if scan.errors:
                raise LedgerIntegrityError("refusing append to invalid ledger: " + scan.errors[0])
            event = factory(scan)
            row = self._row(
                event_type,
                event,
                seq=len(rows),
                prev_hash=rows[-1]["row_hash"] if rows else None,
            )
            candidate = self._scan(rows + [row])
            if candidate.errors:
                raise LedgerIntegrityError("refusing invalid event: " + candidate.errors[0])
            handle.seek(0, os.SEEK_END)
            handle.write(canonical_bytes(row).decode("utf-8") + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            return row

    @classmethod
    def _scan(cls, rows: list[dict[str, Any]], *,
              initial_errors: list[str] | None = None) -> _Scan:
        scan = _Scan(rows=rows, errors=list(initial_errors or []))
        previous: str | None = None
        terminal_seen: set[str] = set()
        for index, row in enumerate(rows):
            label = f"row {index}"
            required = {"protocol", "seq", "prev_hash", "event_type", "event", "event_hash", "row_hash"}
            if set(row) != required:
                scan.errors.append(f"{label}: row fields do not match protocol schema")
                continue
            if (
                row.get("protocol") != PROTOCOL
                or type(row.get("seq")) is not int
                or row.get("seq") != index
            ):
                scan.errors.append(f"{label}: protocol or sequence mismatch")
            if row.get("prev_hash") != previous:
                scan.errors.append(f"{label}: previous-row hash mismatch")
            event_type = row.get("event_type")
            event = row.get("event")
            if not isinstance(event_type, str) or not isinstance(event, dict):
                scan.errors.append(f"{label}: event type and event object are required")
                previous = row.get("row_hash")
                continue
            try:
                expected_event_hash = digest(f"event/{event_type}", event)
                base = {key: row[key] for key in required if key != "row_hash"}
                expected_row_hash = digest("row", base)
            except Exception as exc:
                scan.errors.append(f"{label}: non-canonical content: {exc}")
                previous = row.get("row_hash")
                continue
            if row.get("event_hash") != expected_event_hash:
                scan.errors.append(f"{label}: event hash mismatch")
            if row.get("row_hash") != expected_row_hash:
                scan.errors.append(f"{label}: row hash mismatch")
            try:
                event_at = cls._semantic_time(event_type, event)
            except Exception:
                event_at = None  # The event-specific validator reports timestamp errors.
            if (
                event_at is not None
                and scan.latest_event_at is not None
                and event_at < scan.latest_event_at
            ):
                scan.errors.append(f"{label}: event timestamp moves backward")
            if index == 0:
                if event_type != "genesis":
                    scan.errors.append("row 0: first event must be genesis")
                else:
                    expected_fields = {"protocol", "stream_id", "registered_predictor", "anchor_policy", "created_at"}
                    if set(event) != expected_fields or event.get("protocol") != PROTOCOL:
                        scan.errors.append("row 0: invalid genesis schema")
                    stream_id = event.get("stream_id")
                    if not isinstance(stream_id, str) or not _STREAM_RE.fullmatch(stream_id):
                        scan.errors.append("row 0: invalid stream id")
                    else:
                        scan.stream_id = stream_id
                    registered = event.get("registered_predictor")
                    if (
                        not isinstance(registered, str)
                        or not registered.strip()
                        or len(registered) > 200
                        or registered != unicodedata.normalize("NFC", registered.strip())
                    ):
                        scan.errors.append("row 0: registered predictor is required")
                    else:
                        scan.registered_predictor = registered
                    if event.get("anchor_policy") not in {"required", "optional"}:
                        scan.errors.append("row 0: invalid anchor policy")
                    scan.anchor_policy = event.get("anchor_policy")
                    try:
                        parse_timestamp(event.get("created_at"), "genesis.created_at")
                    except Exception as exc:
                        scan.errors.append(f"row 0: {exc}")
            elif event_type == "genesis":
                scan.errors.append(f"{label}: duplicate genesis")
            elif event_type == "legacy_import":
                cls._validate_legacy_import(event, index, label, scan)
            elif event_type == "contract":
                cls._validate_contract(event, index, label, scan)
            elif event_type == "resolution":
                cls._validate_resolution(event, index, label, scan, terminal_seen)
            elif event_type in {"forfeit", "void"}:
                cls._validate_disposition(event, event_type, index, label, scan, terminal_seen)
            elif event_type == "checkpoint":
                cls._validate_checkpoint(event, row, index, label, scan)
            elif event_type == "anchor":
                cls._validate_anchor(event, label, scan)
            else:
                scan.errors.append(f"{label}: unknown event type {event_type!r}")
            if event_at is not None and (
                scan.latest_event_at is None or event_at > scan.latest_event_at
            ):
                scan.latest_event_at = event_at
            previous = row.get("row_hash")
        return scan

    @staticmethod
    def _validate_legacy_import(event: dict[str, Any], seq: int, label: str, scan: _Scan) -> None:
        expected = {
            "source_protocol", "source_digest", "source_bytes", "source_rows",
            "git_commit", "classification", "imported_at",
        }
        try:
            if set(event) != expected or event["source_protocol"] != "prepende/1":
                raise ValueError("invalid legacy import schema")
            if seq != 1:
                raise ValueError("legacy import is an optional single cutover event at sequence 1")
            require_digest(event["source_digest"], "legacy source digest")
            if type(event["source_bytes"]) is not int or event["source_bytes"] < 0:
                raise ValueError("source_bytes must be a non-negative integer")
            if type(event["source_rows"]) is not int or event["source_rows"] < 0:
                raise ValueError("source_rows must be a non-negative integer")
            if event["classification"] != "legacy-self-attested":
                raise ValueError("legacy import classification must remain explicit")
            if (
                not isinstance(event["git_commit"], str)
                or not event["git_commit"]
                or len(event["git_commit"]) > 200
                or event["git_commit"] != unicodedata.normalize("NFC", event["git_commit"])
            ):
                raise ValueError("legacy git_commit must be normalized non-empty text")
            parse_timestamp(event["imported_at"], "legacy_import.imported_at")
        except Exception as exc:
            scan.errors.append(f"{label}: {exc}")

    @staticmethod
    def _validate_contract(event: dict[str, Any], seq: int, label: str, scan: _Scan) -> None:
        try:
            expected = set(Contract.__dataclass_fields__)
            if set(event) != expected:
                raise ContractValidationError("contract fields do not match protocol schema")
            contract = build_contract(**{
                field: event[field]
                for field in expected if field != "contract_id"
            })
            normalized_event = {
                field: getattr(contract, field) for field in Contract.__dataclass_fields__
            }
            if canonical_bytes(event) != canonical_bytes(normalized_event):
                raise ContractValidationError(
                    "contract event is not the exact normalized protocol representation"
                )
            if scan.stream_id is None:
                raise ContractValidationError("contract precedes valid genesis")
            if contract.stream_id != scan.stream_id:
                raise ContractValidationError("contract stream does not match genesis")
            if contract.predictor != scan.registered_predictor:
                raise ContractValidationError(
                    "contract predictor does not match the stream's registered predictor"
                )
            logical_key = (contract.predictor, contract.domain, contract.event_id)
            if contract.contract_id in scan.contracts:
                raise ContractValidationError("duplicate contract id")
            if logical_key in scan.logical_keys:
                raise ContractValidationError("duplicate logical event id for predictor/domain")
            scan.contracts[contract.contract_id] = contract
            scan.contract_seq[contract.contract_id] = seq
            scan.logical_keys.add(logical_key)
        except Exception as exc:
            scan.errors.append(f"{label}: {exc}")

    @staticmethod
    def _validate_resolution(event: dict[str, Any], seq: int, label: str, scan: _Scan,
                             terminal_seen: set[str]) -> None:
        try:
            expected = set(Resolution.__dataclass_fields__)
            if set(event) != expected:
                raise ValueError("resolution fields do not match protocol schema")
            resolution = Resolution.from_event(event)
            contract = scan.contracts.get(resolution.contract_id)
            if contract is None:
                raise ValueError("resolution references an unknown contract")
            if resolution.contract_id in terminal_seen:
                raise ValueError("contract already has a terminal event")
            if resolution.stream_id != scan.stream_id or not resolution.verify_id():
                raise ValueError("resolution stream or id mismatch")
            if not isinstance(resolution.outcome, dict):
                raise ValueError("resolution outcome must be an object")
            if not isinstance(resolution.evidence, list):
                raise ValueError("resolution evidence must be a list")
            if not isinstance(resolution.note, str) or resolution.note != Ledger._note(
                resolution.note, "resolution.note"
            ):
                raise ValueError("resolution note is not normalized protocol text")
            issued = parse_timestamp(contract.issued_at, "issued_at")
            resolved = parse_timestamp(resolution.resolved_at, "resolved_at")
            due = parse_timestamp(contract.resolution_due_at, "resolution_due_at")
            if resolved < issued or resolved > due:
                raise ValueError("resolution timestamp falls outside the locked window")
            evidence = normalize_evidence(resolution.evidence)
            if canonical_bytes(resolution.evidence) != canonical_bytes(evidence):
                raise ValueError("resolution evidence is not the normalized evidence representation")
            require_evaluation_artifacts(contract.evaluation, contract.evaluator, evidence)
            expected_outcome = evaluate(contract.kind, contract.evaluator, evidence)
            if canonical_bytes(resolution.outcome) != canonical_bytes(expected_outcome):
                raise ValueError("resolution outcome does not match deterministic evaluator")
            mode = contract.resolver_policy["mode"]
            if mode == "self":
                if resolution.resolver_key_id is not None or resolution.signature is not None:
                    raise ValueError("self resolution cannot carry a signing identity")
            else:
                if resolution.resolver_key_id not in contract.resolver_policy["authorized_key_ids"]:
                    raise ValueError("resolver key is not authorized by the contract")
                if not isinstance(resolution.signature, str) or not resolution.signature:
                    raise ValueError("signed resolution requires a detached signature")
            terminal_seen.add(resolution.contract_id)
            scan.terminals[resolution.contract_id] = resolution
            scan.terminal_seq[resolution.contract_id] = seq
        except Exception as exc:
            scan.errors.append(f"{label}: {exc}")

    @staticmethod
    def _validate_disposition(event: dict[str, Any], event_type: str, seq: int, label: str,
                              scan: _Scan, terminal_seen: set[str]) -> None:
        try:
            expected = set(Disposition.__dataclass_fields__)
            if set(event) != expected:
                raise ValueError(f"{event_type} fields do not match protocol schema")
            disposition = Disposition.from_event(event)
            contract = scan.contracts.get(disposition.contract_id)
            if contract is None or disposition.contract_id in terminal_seen:
                raise ValueError("disposition references an unknown or terminal contract")
            if disposition.stream_id != scan.stream_id or disposition.disposition != event_type:
                raise ValueError("disposition stream or type mismatch")
            if not disposition.verify_id():
                raise ValueError("disposition id mismatch")
            if (
                not isinstance(disposition.reason_code, str)
                or not isinstance(disposition.note, str)
                or disposition.note != Ledger._note(disposition.note, f"{event_type}.note")
            ):
                raise ValueError(f"{event_type} reason and note must be normalized strings")
            at = parse_timestamp(disposition.at, f"{event_type}.at")
            if event_type == "forfeit":
                if at < parse_timestamp(contract.resolution_due_at, "resolution_due_at"):
                    raise ValueError("forfeit cannot be recorded before the deadline")
                if disposition.reason_code != "deadline_expired":
                    raise ValueError("forfeit reason must be deadline_expired")
                if disposition.resolver_key_id is not None or disposition.signature is not None:
                    raise ValueError("forfeit is a deterministic stream disposition, not a signed resolution")
            else:
                issued = parse_timestamp(contract.issued_at, "issued_at")
                due = parse_timestamp(contract.resolution_due_at, "resolution_due_at")
                if at < issued or at > due:
                    raise ValueError("void timestamp falls outside the locked window")
                if disposition.reason_code not in contract.void_policy["allowed_reason_codes"]:
                    raise ValueError("void reason was not locked in the contract")
                if contract.resolver_policy["mode"] != "signed":
                    raise ValueError("void requires a signed resolver policy")
                if disposition.resolver_key_id not in contract.resolver_policy["authorized_key_ids"]:
                    raise ValueError("void resolver key is not authorized")
                if not isinstance(disposition.signature, str) or not disposition.signature:
                    raise ValueError("void requires a detached signature")
            terminal_seen.add(disposition.contract_id)
            scan.terminals[disposition.contract_id] = disposition
            scan.terminal_seq[disposition.contract_id] = seq
        except Exception as exc:
            scan.errors.append(f"{label}: {exc}")

    @staticmethod
    def _validate_checkpoint(event: dict[str, Any], row: dict[str, Any], seq: int,
                             label: str, scan: _Scan) -> None:
        expected = {
            "checkpoint_id", "stream_id", "covered_through", "covered_head",
            "row_count", "created_at",
        }
        try:
            if set(event) != expected or event["stream_id"] != scan.stream_id:
                raise ValueError("invalid checkpoint schema or stream")
            body = {key: value for key, value in event.items() if key != "checkpoint_id"}
            if event["checkpoint_id"] != digest("checkpoint", body):
                raise ValueError("checkpoint id mismatch")
            if event["checkpoint_id"] in scan.checkpoints:
                raise ValueError("duplicate checkpoint id")
            if (
                type(event["covered_through"]) is not int
                or type(event["row_count"]) is not int
                or event["covered_through"] != seq - 1
                or event["row_count"] != seq
            ):
                raise ValueError("checkpoint row-count commitment mismatch")
            if event["covered_head"] != row["prev_hash"]:
                raise ValueError("checkpoint covered head mismatch")
            created_at = parse_timestamp(event["created_at"], "checkpoint.created_at")
            if scan.latest_event_at is not None and created_at < scan.latest_event_at:
                raise ValueError("checkpoint time precedes a covered event timestamp")
            scan.checkpoints[event["checkpoint_id"]] = (event, row)
        except Exception as exc:
            scan.errors.append(f"{label}: {exc}")

    @staticmethod
    def _validate_anchor(event: dict[str, Any], label: str, scan: _Scan) -> None:
        expected = {"anchor_id", "statement", "signature"}
        try:
            if set(event) != expected or not isinstance(event["statement"], dict):
                raise ValueError("invalid anchor schema")
            statement = event["statement"]
            statement_fields = {
                "protocol", "stream_id", "checkpoint_id", "checkpoint_row_hash",
                "covered_through", "covered_head", "row_count", "anchored_at", "key_id",
            }
            if set(statement) != statement_fields:
                raise ValueError("anchor statement fields do not match protocol schema")
            if event["anchor_id"] != digest("anchor", statement):
                raise ValueError("anchor id mismatch")
            if any(item["anchor_id"] == event["anchor_id"] for item in scan.anchors):
                raise ValueError("duplicate anchor id")
            checkpoint = scan.checkpoints.get(statement["checkpoint_id"])
            if checkpoint is None:
                raise ValueError("anchor references an unknown checkpoint")
            checkpoint_event, checkpoint_row = checkpoint
            if statement["protocol"] != PROTOCOL or statement["stream_id"] != scan.stream_id:
                raise ValueError("anchor protocol or stream mismatch")
            if (
                type(statement["covered_through"]) is not int
                or type(statement["row_count"]) is not int
            ):
                raise ValueError("anchor coverage and row count must be integers")
            comparisons = {
                "checkpoint_row_hash": checkpoint_row["row_hash"],
                "covered_through": checkpoint_event["covered_through"],
                "covered_head": checkpoint_event["covered_head"],
                "row_count": checkpoint_event["row_count"],
            }
            if any(statement[key] != value for key, value in comparisons.items()):
                raise ValueError("anchor statement does not match checkpoint commitment")
            parse_timestamp(statement["anchored_at"], "anchor.anchored_at")
            if parse_timestamp(statement["anchored_at"], "anchor.anchored_at") < parse_timestamp(
                checkpoint_event["created_at"], "checkpoint.created_at"
            ):
                raise ValueError("anchor authority time precedes the checkpoint")
            if not isinstance(statement["key_id"], str) or not statement["key_id"]:
                raise ValueError("anchor key_id is required")
            if not isinstance(event["signature"], str) or not event["signature"]:
                raise ValueError("anchor signature is required")
            scan.anchors.append(event)
        except Exception as exc:
            scan.errors.append(f"{label}: {exc}")

    def _read_scan(self) -> _Scan:
        with self._locked(exclusive=False) as handle:
            rows, errors = self._load(handle)
        return self._scan(rows, initial_errors=errors)

    def lock_prediction(self, *, predictor: str, model_version: str, domain: str,
                        event_id: str, question: str, kind: str, claim: dict[str, Any],
                        resolution_rule: str, evaluator: dict[str, Any],
                        evaluation: dict[str, Any], resolution_due_at: str,
                        resolver_policy: dict[str, Any], nonresolution_policy: dict[str, Any],
                        void_policy: dict[str, Any] | None = None,
                        provenance: str = "forward") -> Contract:
        def factory(scan: _Scan) -> dict[str, Any]:
            contract = build_contract(
                stream_id=scan.stream_id,
                predictor=predictor,
                model_version=model_version,
                domain=domain,
                event_id=event_id,
                question=question,
                kind=kind,
                claim=claim,
                resolution_rule=resolution_rule,
                evaluator=evaluator,
                evaluation=evaluation,
                issued_at=timestamp_now(self._clock),
                resolution_due_at=resolution_due_at,
                resolver_policy=resolver_policy,
                nonresolution_policy=nonresolution_policy,
                void_policy=void_policy,
                provenance=provenance,
            )
            logical_key = (contract.predictor, contract.domain, contract.event_id)
            if logical_key in scan.logical_keys:
                raise RetrofitError("logical prediction event already exists in this stream")
            return {field: getattr(contract, field) for field in Contract.__dataclass_fields__}

        return Contract.from_event(self._append_factory("contract", factory)["event"])

    def _resolution_statement(self, contract_id: str, evidence: Any, note: str,
                              resolver_key_id: str | None) -> dict[str, Any]:
        scan = self._read_scan()
        if scan.errors:
            raise LedgerIntegrityError(scan.errors[0])
        contract = scan.contracts.get(contract_id)
        if contract is None:
            raise ValueError("no such contract: " + contract_id)
        if contract_id in scan.terminals:
            raise RetrofitError("contract is already terminal")
        normalized_evidence = normalize_evidence(evidence)
        require_evaluation_artifacts(contract.evaluation, contract.evaluator, normalized_evidence)
        outcome = evaluate(contract.kind, contract.evaluator, normalized_evidence)
        statement = {
            "protocol": PROTOCOL,
            "stream_id": scan.stream_id,
            "contract_id": contract_id,
            "outcome": outcome,
            "evidence": normalized_evidence,
            "resolved_at": timestamp_now(self._clock),
            "resolver_key_id": resolver_key_id,
            "note": self._note(note, "resolution.note"),
        }
        if parse_timestamp(statement["resolved_at"], "resolved_at") > parse_timestamp(
            contract.resolution_due_at, "resolution_due_at"
        ):
            raise RetrofitError("resolution deadline passed; record a forfeit")
        return statement

    def prepare_resolution(self, contract_id: str, *, evidence: Any, resolver_key_id: str,
                           note: str = "") -> dict[str, Any]:
        scan = self._read_scan()
        contract = scan.contracts.get(contract_id)
        if contract is None:
            raise ValueError("no such contract: " + contract_id)
        if contract.resolver_policy["mode"] != "signed":
            raise RetrofitError("contract does not use a signed resolver")
        if resolver_key_id not in contract.resolver_policy["authorized_key_ids"]:
            raise RetrofitError("resolver key is not authorized")
        return self._resolution_statement(contract_id, evidence, note, resolver_key_id)

    def _append_resolution(self, statement: dict[str, Any], signature: str | None) -> Resolution:
        def factory(scan: _Scan) -> dict[str, Any]:
            contract = scan.contracts.get(statement.get("contract_id"))
            if contract is None:
                raise ValueError("resolution references an unknown contract")
            if contract.contract_id in scan.terminals:
                raise RetrofitError("contract is already terminal")
            appended_at = parse_timestamp(timestamp_now(self._clock), "append time")
            resolved_at = parse_timestamp(statement.get("resolved_at"), "resolved_at")
            due = parse_timestamp(contract.resolution_due_at, "resolution_due_at")
            if resolved_at > appended_at:
                raise RetrofitError("resolution timestamp cannot be later than append time")
            if appended_at > due:
                raise RetrofitError("resolution deadline passed; record a forfeit")
            resolution_id = digest("resolution", statement)
            return {
                "resolution_id": resolution_id,
                "stream_id": statement["stream_id"],
                "contract_id": statement["contract_id"],
                "outcome": statement["outcome"],
                "evidence": statement["evidence"],
                "resolved_at": statement["resolved_at"],
                "resolver_key_id": statement["resolver_key_id"],
                "signature": signature,
                "note": statement["note"],
            }
        return Resolution.from_event(self._append_factory("resolution", factory)["event"])

    def resolve(self, contract_id: str, *, evidence: Any, note: str = "") -> Resolution:
        scan = self._read_scan()
        contract = scan.contracts.get(contract_id)
        if contract is None:
            raise ValueError("no such contract: " + contract_id)
        if contract.resolver_policy["mode"] != "self":
            raise RetrofitError("signed resolver required; use prepare_resolution/resolve_signed")
        statement = self._resolution_statement(contract_id, evidence, note, None)
        return self._append_resolution(statement, None)

    def resolve_signed(self, statement: dict[str, Any], signature: str) -> Resolution:
        if not isinstance(statement, dict) or not isinstance(signature, str) or not signature:
            raise ValueError("signed resolution requires a prepared statement and signature")
        return self._append_resolution(statement, signature)

    def forfeit(self, contract_id: str, *, note: str = "") -> Disposition:
        def factory(scan: _Scan) -> dict[str, Any]:
            contract = scan.contracts.get(contract_id)
            if contract is None:
                raise ValueError("no such contract: " + contract_id)
            if contract_id in scan.terminals:
                raise RetrofitError("contract is already terminal")
            at = timestamp_now(self._clock)
            if parse_timestamp(at, "forfeit.at") < parse_timestamp(
                contract.resolution_due_at, "resolution_due_at"
            ):
                raise RetrofitError("contract deadline has not passed")
            statement = {
                "protocol": PROTOCOL,
                "stream_id": scan.stream_id,
                "contract_id": contract_id,
                "disposition": "forfeit",
                "at": at,
                "reason_code": "deadline_expired",
                "note": self._note(note, "forfeit.note"),
                "resolver_key_id": None,
            }
            return {
                "disposition_id": digest("forfeit", statement),
                **{key: value for key, value in statement.items() if key != "protocol"},
                "signature": None,
            }
        return Disposition.from_event(self._append_factory("forfeit", factory)["event"])

    def prepare_void(self, contract_id: str, *, reason_code: str, resolver_key_id: str,
                     note: str = "") -> dict[str, Any]:
        scan = self._read_scan()
        contract = scan.contracts.get(contract_id)
        if contract is None:
            raise ValueError("no such contract: " + contract_id)
        if contract_id in scan.terminals:
            raise RetrofitError("contract is already terminal")
        if contract.resolver_policy["mode"] != "signed":
            raise RetrofitError("void requires a signed resolver policy")
        if resolver_key_id not in contract.resolver_policy["authorized_key_ids"]:
            raise RetrofitError("resolver key is not authorized")
        if reason_code not in contract.void_policy["allowed_reason_codes"]:
            raise RetrofitError("void reason was not locked in the contract")
        at = timestamp_now(self._clock)
        if parse_timestamp(at, "void.at") > parse_timestamp(
            contract.resolution_due_at, "resolution_due_at"
        ):
            raise RetrofitError("void deadline passed; record a forfeit")
        return {
            "protocol": PROTOCOL,
            "stream_id": scan.stream_id,
            "contract_id": contract_id,
            "disposition": "void",
            "at": at,
            "reason_code": reason_code,
            "note": self._note(note, "void.note"),
            "resolver_key_id": resolver_key_id,
        }

    def void_signed(self, statement: dict[str, Any], signature: str) -> Disposition:
        if not isinstance(statement, dict) or not isinstance(signature, str) or not signature:
            raise ValueError("signed void requires a prepared statement and signature")

        def factory(scan: _Scan) -> dict[str, Any]:
            contract = scan.contracts.get(statement.get("contract_id"))
            if contract is None:
                raise ValueError("void references an unknown contract")
            if contract.contract_id in scan.terminals:
                raise RetrofitError("contract is already terminal")
            appended_at = parse_timestamp(timestamp_now(self._clock), "append time")
            void_at = parse_timestamp(statement.get("at"), "void.at")
            due = parse_timestamp(contract.resolution_due_at, "resolution_due_at")
            if void_at > appended_at:
                raise RetrofitError("void timestamp cannot be later than append time")
            if appended_at > due:
                raise RetrofitError("void deadline passed; record a forfeit")
            return {
                "disposition_id": digest("void", statement),
                **{key: value for key, value in statement.items() if key != "protocol"},
                "signature": signature,
            }
        return Disposition.from_event(self._append_factory("void", factory)["event"])

    def checkpoint(self) -> dict[str, Any]:
        def factory(scan: _Scan) -> dict[str, Any]:
            if not scan.rows:
                raise LedgerIntegrityError("cannot checkpoint an empty stream")
            body = {
                "stream_id": scan.stream_id,
                "covered_through": len(scan.rows) - 1,
                "covered_head": scan.rows[-1]["row_hash"],
                "row_count": len(scan.rows),
                "created_at": timestamp_now(self._clock),
            }
            return {"checkpoint_id": digest("checkpoint", body), **body}
        return self._append_factory("checkpoint", factory)["event"]

    def anchor_request(self, checkpoint_id: str) -> dict[str, Any]:
        scan = self._read_scan()
        checkpoint = scan.checkpoints.get(checkpoint_id)
        if checkpoint is None:
            raise ValueError("no such checkpoint: " + checkpoint_id)
        event, row = checkpoint
        return {
            "protocol": PROTOCOL,
            "stream_id": scan.stream_id,
            "checkpoint_id": checkpoint_id,
            "checkpoint_row_hash": row["row_hash"],
            "covered_through": event["covered_through"],
            "covered_head": event["covered_head"],
            "row_count": event["row_count"],
        }

    def add_anchor(self, statement: dict[str, Any], signature: str) -> dict[str, Any]:
        event = {
            "anchor_id": digest("anchor", statement),
            "statement": statement,
            "signature": signature,
        }
        return self._append_event("anchor", event)["event"]

    def records(self) -> list[tuple[Contract, Optional[Resolution | Disposition]]]:
        scan = self._read_scan()
        if scan.errors:
            raise LedgerIntegrityError("cannot return records from invalid ledger: " + scan.errors[0])
        return [(contract, scan.terminals.get(contract_id)) for contract_id, contract in scan.contracts.items()]

    def verify(self, *, trusted_anchor_keys: Mapping[str, Any] | None = None,
               trusted_resolver_keys: Mapping[str, Any] | None = None,
               external_anchor_receipts: list[dict[str, Any]] | None = None) -> IntegrityReport:
        scan = self._read_scan()
        if not scan.errors:
            known = {item.get("anchor_id"): item for item in scan.anchors}
            for index, receipt in enumerate(external_anchor_receipts or []):
                if not isinstance(receipt, dict):
                    scan.errors.append(f"external anchor receipt {index}: receipt must be an object")
                    continue
                existing = known.get(receipt.get("anchor_id"))
                if existing is not None:
                    try:
                        identical = canonical_bytes(receipt) == canonical_bytes(existing)
                    except Exception as exc:
                        scan.errors.append(
                            f"external anchor receipt {index}: non-canonical content: {exc}"
                        )
                        continue
                    if identical:
                        continue
                    scan.errors.append(
                        f"external anchor receipt {index}: conflicts with known anchor id"
                    )
                    continue
                before = len(scan.errors)
                self._validate_anchor(receipt, f"external anchor receipt {index}", scan)
                if len(scan.errors) == before:
                    known[receipt.get("anchor_id")] = receipt
        counts = {
            "contracts": len(scan.contracts),
            "resolved": sum(isinstance(value, Resolution) for value in scan.terminals.values()),
            "forfeited": sum(isinstance(value, Disposition) and value.disposition == "forfeit" for value in scan.terminals.values()),
            "void": sum(isinstance(value, Disposition) and value.disposition == "void" for value in scan.terminals.values()),
        }
        report = IntegrityReport(
            internally_valid=not scan.errors,
            stream_id=scan.stream_id,
            head_hash=scan.rows[-1].get("row_hash") if scan.rows else None,
            row_count=len(scan.rows),
            counts=counts,
            errors=list(scan.errors),
            warnings=list(scan.warnings),
        )
        if scan.errors:
            report.status = STATUS_TAMPERED
            return report

        instant = parse_timestamp(timestamp_now(self._clock), "now")
        report.unresolved = [cid for cid in scan.contracts if cid not in scan.terminals]
        report.overdue = [
            cid for cid in report.unresolved
            if parse_timestamp(scan.contracts[cid].resolution_due_at, "resolution_due_at") < instant
        ]

        valid_anchor_coverages: list[tuple[int, str]] = []
        for anchor in scan.anchors:
            statement = anchor["statement"]
            try:
                key = _trusted_key(trusted_anchor_keys, statement["key_id"])
            except SignatureError as exc:
                report.warnings.append(f"invalid anchor trust record: {exc}")
                continue
            if key is None:
                report.warnings.append(f"untrusted anchor key: {statement['key_id']}")
                continue
            try:
                verify_detached(statement, anchor["signature"], key, at=statement["anchored_at"])
            except SignatureUnavailable as exc:
                report.warnings.append(str(exc))
                continue
            except SignatureError as exc:
                report.errors.append(str(exc))
                report.status = STATUS_TAMPERED
                report.internally_valid = False
                return report
            valid_anchor_coverages.append((statement["covered_through"], statement["anchored_at"]))

        if valid_anchor_coverages:
            report.complete_through = max(value[0] for value in valid_anchor_coverages)
        for contract_id, seq in scan.contract_seq.items():
            contract = scan.contracts[contract_id]
            terminal = scan.terminals.get(contract_id)
            latest_lock_time = contract.resolution_due_at
            if isinstance(terminal, Resolution):
                latest_lock_time = terminal.resolved_at
            elif isinstance(terminal, Disposition) and terminal.disposition == "void":
                latest_lock_time = terminal.at
            eligible = []
            for covered, anchored_at in valid_anchor_coverages:
                if covered < seq:
                    continue
                if parse_timestamp(anchored_at, "anchored_at") < parse_timestamp(contract.issued_at, "issued_at"):
                    continue
                if parse_timestamp(anchored_at, "anchored_at") >= parse_timestamp(
                    latest_lock_time, "latest_lock_time"
                ):
                    continue
                eligible.append((covered, anchored_at))
            if not eligible:
                report.unanchored_contracts.append(contract_id)
        report.anchored = bool(scan.contracts) and not report.unanchored_contracts

        terminal_witness_times: dict[str, list[str]] = {}
        for contract_id, terminal in scan.terminals.items():
            if isinstance(terminal, Resolution):
                terminal_at = terminal.resolved_at
            elif isinstance(terminal, Disposition) and terminal.disposition == "void":
                terminal_at = terminal.at
            else:
                continue
            due_at = scan.contracts[contract_id].resolution_due_at
            witnesses = [
                anchored_at
                for covered, anchored_at in valid_anchor_coverages
                if covered >= scan.terminal_seq[contract_id]
                and parse_timestamp(anchored_at, "terminal witness time")
                >= parse_timestamp(terminal_at, "terminal time")
                and parse_timestamp(anchored_at, "terminal witness time")
                <= parse_timestamp(due_at, "resolution_due_at")
            ]
            terminal_witness_times[contract_id] = witnesses
            if not witnesses:
                report.unwitnessed_terminals.append(contract_id)

        independently_resolvable_count = 0
        trusted_signed_count = 0
        for contract_id, terminal in scan.terminals.items():
            if isinstance(terminal, Resolution):
                independently_resolvable_count += 1
            if isinstance(terminal, Resolution) and terminal.resolver_key_id is not None:
                try:
                    key = _trusted_key(trusted_resolver_keys, terminal.resolver_key_id)
                except SignatureError as exc:
                    report.warnings.append(f"invalid resolver trust record: {exc}")
                    report.untrusted_resolutions.append(contract_id)
                    continue
                if key is None:
                    report.untrusted_resolutions.append(contract_id)
                    continue
                try:
                    verify_detached(
                        terminal.statement(), terminal.signature or "", key,
                        at=terminal.resolved_at,
                    )
                except SignatureUnavailable as exc:
                    report.warnings.append(str(exc))
                    report.untrusted_resolutions.append(contract_id)
                    continue
                except SignatureError as exc:
                    report.errors.append(str(exc))
                    report.status = STATUS_TAMPERED
                    report.internally_valid = False
                    return report
                witness_times = terminal_witness_times.get(contract_id, [])
                if not witness_times:
                    report.warnings.append(
                        f"signed resolution lacks a trusted external witness: {contract_id}"
                    )
                    report.untrusted_resolutions.append(contract_id)
                    continue
                witnessed_at = min(
                    witness_times,
                    key=lambda value: parse_timestamp(value, "resolver witness time"),
                )
                try:
                    verify_detached(terminal.statement(), terminal.signature or "", key, at=witnessed_at)
                except SignatureUnavailable as exc:
                    report.warnings.append(str(exc))
                    report.untrusted_resolutions.append(contract_id)
                except SignatureError as exc:
                    report.errors.append(str(exc))
                    report.status = STATUS_TAMPERED
                    report.internally_valid = False
                    return report
                else:
                    trusted_signed_count += 1
            elif isinstance(terminal, Disposition) and terminal.disposition == "void":
                independently_resolvable_count += 1
                try:
                    key = _trusted_key(trusted_resolver_keys, terminal.resolver_key_id or "")
                except SignatureError as exc:
                    report.warnings.append(f"invalid resolver trust record: {exc}")
                    report.untrusted_resolutions.append(contract_id)
                    continue
                if key is None:
                    report.untrusted_resolutions.append(contract_id)
                    continue
                try:
                    verify_detached(terminal.statement(), terminal.signature or "", key, at=terminal.at)
                except (SignatureUnavailable, SignatureError) as exc:
                    if isinstance(exc, SignatureError):
                        report.errors.append(str(exc))
                        report.status = STATUS_TAMPERED
                        report.internally_valid = False
                        return report
                    report.warnings.append(str(exc))
                    report.untrusted_resolutions.append(contract_id)
                    continue
                witness_times = terminal_witness_times.get(contract_id, [])
                if not witness_times:
                    report.warnings.append(
                        f"signed void lacks a trusted external witness: {contract_id}"
                    )
                    report.untrusted_resolutions.append(contract_id)
                    continue
                witnessed_at = min(
                    witness_times,
                    key=lambda value: parse_timestamp(value, "resolver witness time"),
                )
                try:
                    verify_detached(terminal.statement(), terminal.signature or "", key, at=witnessed_at)
                except (SignatureUnavailable, SignatureError) as exc:
                    if isinstance(exc, SignatureError):
                        report.errors.append(str(exc))
                        report.status = STATUS_TAMPERED
                        report.internally_valid = False
                        return report
                    report.warnings.append(str(exc))
                    report.untrusted_resolutions.append(contract_id)
                else:
                    trusted_signed_count += 1
        report.independently_resolved = (
            independently_resolvable_count > 0
            and independently_resolvable_count == trusted_signed_count
        )

        if report.overdue or report.untrusted_resolutions or report.unwitnessed_terminals:
            report.status = STATUS_INCOMPLETE
        elif not report.anchored:
            report.status = STATUS_UNANCHORED
        else:
            latest_business_seq = max(
                (row["seq"] for row in scan.rows if row["event_type"] in {
                    "contract", "resolution", "forfeit", "void", "legacy_import"
                }),
                default=0,
            )
            if report.complete_through is None or report.complete_through < latest_business_seq:
                report.status = STATUS_INCOMPLETE
            else:
                report.status = STATUS_OK
        return report

    def integrity(self, **kwargs: Any) -> IntegrityReport:
        return self.verify(**kwargs)
