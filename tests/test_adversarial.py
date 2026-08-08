from __future__ import annotations

import json
import multiprocessing
import tempfile
import unittest
from pathlib import Path

from prepende import (
    Ledger, STATUS_INCOMPLETE, STATUS_TAMPERED, STATUS_UNANCHORED,
    build_anchor_statement, sign_detached,
)
from prepende.contract import timestamp_now
from prepende.canonical import digest

from helpers import anchor, evidence, keypair, lock_probability, new_ledger


def _worker(path: str, event_id: str) -> None:
    ledger = Ledger(path, _clock=lambda: 1_700_000_000.0)
    lock_probability(ledger, event_id=event_id)


class AdversarialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger, self.clock, self.path = new_ledger(self.tmp.name)
        try:
            self.anchor_private, self.anchor_public = keypair()
        except ImportError:
            self.skipTest("cryptography extra is unavailable")

    def tearDown(self):
        self.tmp.cleanup()

    def _rows(self):
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]

    def _write(self, rows, *, newline=True):
        text = "\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows)
        self.path.write_text(text + ("\n" if newline else ""), encoding="utf-8")

    def _resolved_stream(self):
        contract = lock_probability(self.ledger)
        anchor(self.ledger, key_id="tsa", private_key=self.anchor_private)
        self.clock.advance(10)
        self.ledger.resolve(contract.contract_id, evidence=evidence(True), note="hit")
        return contract

    def test_flip_resolution_outcome_is_tampered(self):
        self._resolved_stream()
        rows = self._rows()
        row = next(item for item in rows if item["event_type"] == "resolution")
        row["event"]["outcome"]["y"] = 0
        self._write(rows)
        self.assertEqual(self.ledger.verify().status, STATUS_TAMPERED)

    def test_flip_resolution_note_is_tampered(self):
        self._resolved_stream()
        rows = self._rows()
        next(item for item in rows if item["event_type"] == "resolution")["event"]["note"] = "rewritten"
        self._write(rows)
        self.assertEqual(self.ledger.verify().status, STATUS_TAMPERED)

    def test_changed_locked_evaluation_digest_is_tampered(self):
        lock_probability(self.ledger)
        anchor(self.ledger, key_id="tsa", private_key=self.anchor_private)
        rows = self._rows()
        contract = next(item for item in rows if item["event_type"] == "contract")
        contract["event"]["evaluation"]["spec_digest"] = "sha256:" + "2" * 64
        self._write(rows)
        self.assertEqual(
            self.ledger.verify(trusted_anchor_keys={"tsa": self.anchor_public}).status,
            STATUS_TAMPERED,
        )

    def test_delete_contract_and_resolution_pair_is_not_clean(self):
        self._resolved_stream()
        rows = [row for row in self._rows() if row["event_type"] not in {"contract", "resolution"}]
        self._write(rows)
        self.assertIn(self.ledger.verify().status, {STATUS_TAMPERED, STATUS_UNANCHORED})

    def test_delete_interior_row_is_tampered(self):
        self._resolved_stream()
        rows = self._rows()
        del rows[1]
        self._write(rows)
        self.assertEqual(self.ledger.verify().status, STATUS_TAMPERED)

    def test_reorder_rows_is_tampered(self):
        self._resolved_stream()
        rows = self._rows()
        rows[1], rows[2] = rows[2], rows[1]
        self._write(rows)
        self.assertEqual(self.ledger.verify().status, STATUS_TAMPERED)

    def test_duplicate_sequence_is_tampered(self):
        self._resolved_stream()
        rows = self._rows()
        rows.insert(2, dict(rows[1]))
        self._write(rows)
        self.assertEqual(self.ledger.verify().status, STATUS_TAMPERED)

    def test_boolean_sequence_is_not_accepted_as_integer_zero(self):
        rows = self._rows()
        rows[0]["seq"] = False
        base = {key: rows[0][key] for key in rows[0] if key != "row_hash"}
        rows[0]["row_hash"] = digest("row", base)
        self._write(rows)
        self.assertEqual(self.ledger.verify().status, STATUS_TAMPERED)

    def test_partial_final_row_is_tampered(self):
        lock_probability(self.ledger)
        self.path.write_bytes(self.path.read_bytes()[:-1])
        self.assertEqual(self.ledger.verify().status, STATUS_TAMPERED)

    def test_removed_anchor_is_unanchored(self):
        lock_probability(self.ledger)
        anchor(self.ledger, key_id="tsa", private_key=self.anchor_private)
        rows = [row for row in self._rows() if row["event_type"] != "anchor"]
        self._write(rows)
        report = self.ledger.verify(trusted_anchor_keys={"tsa": self.anchor_public})
        self.assertEqual(report.status, STATUS_UNANCHORED)

    def test_duplicate_anchor_receipt_is_refused(self):
        lock_probability(self.ledger)
        checkpoint = self.ledger.checkpoint()
        request = self.ledger.anchor_request(checkpoint["checkpoint_id"])
        statement = build_anchor_statement(
            request, key_id="tsa", anchored_at=timestamp_now(self.ledger._clock)
        )
        signature = sign_detached(statement, self.anchor_private)
        self.ledger.add_anchor(statement, signature)
        with self.assertRaisesRegex(Exception, "duplicate anchor id"):
            self.ledger.add_anchor(statement, signature)

    def test_malformed_external_receipt_cannot_hide_behind_known_anchor_id(self):
        lock_probability(self.ledger)
        anchor(self.ledger, key_id="tsa", private_key=self.anchor_private)
        receipt = next(
            row["event"] for row in self._rows() if row["event_type"] == "anchor"
        )
        trusted = {"tsa": self.anchor_public}
        self.assertEqual(
            self.ledger.verify(
                trusted_anchor_keys=trusted,
                external_anchor_receipts=[receipt],
            ).status,
            "OK",
        )
        for malformed in (
            {"anchor_id": receipt["anchor_id"]},
            {
                "anchor_id": receipt["anchor_id"],
                "statement": {"evil": True},
                "signature": "not-base64",
            },
        ):
            with self.subTest(malformed=malformed):
                report = self.ledger.verify(
                    trusted_anchor_keys=trusted,
                    external_anchor_receipts=[malformed],
                )
                self.assertEqual(report.status, STATUS_TAMPERED)

    def test_external_receipt_detects_truncated_known_tail(self):
        lock_probability(self.ledger, event_id="first")
        anchor(self.ledger, key_id="tsa", private_key=self.anchor_private)
        first_prefix = self._rows()
        lock_probability(self.ledger, event_id="later")
        anchor(self.ledger, key_id="tsa", private_key=self.anchor_private)
        receipt = next(
            row["event"] for row in reversed(self._rows()) if row["event_type"] == "anchor"
        )
        self._write(first_prefix)
        without_external = self.ledger.verify(trusted_anchor_keys={"tsa": self.anchor_public})
        self.assertEqual(without_external.status, "OK")
        with_external = self.ledger.verify(
            trusted_anchor_keys={"tsa": self.anchor_public},
            external_anchor_receipts=[receipt],
        )
        self.assertEqual(with_external.status, STATUS_TAMPERED)

    def test_every_contract_requires_anchor_coverage(self):
        lock_probability(self.ledger, event_id="covered")
        anchor(self.ledger, key_id="tsa", private_key=self.anchor_private)
        uncovered = lock_probability(self.ledger, event_id="uncovered")
        report = self.ledger.verify(trusted_anchor_keys={"tsa": self.anchor_public})
        self.assertEqual(report.status, STATUS_UNANCHORED)
        self.assertEqual(report.unanchored_contracts, [uncovered.contract_id])

    def test_anchor_after_deadline_cannot_retroactively_lock_forfeit(self):
        contract = lock_probability(self.ledger, due="2023-11-14T22:14:00Z")
        self.clock.advance(61)
        self.ledger.forfeit(contract.contract_id)
        anchor(self.ledger, key_id="tsa", private_key=self.anchor_private)
        report = self.ledger.verify(trusted_anchor_keys={"tsa": self.anchor_public})
        self.assertEqual(report.status, STATUS_UNANCHORED)
        self.assertEqual(report.unanchored_contracts, [contract.contract_id])

    def test_business_tail_after_anchor_is_incomplete(self):
        self._resolved_stream()
        report = self.ledger.verify(trusted_anchor_keys={"tsa": self.anchor_public})
        self.assertEqual(report.status, STATUS_INCOMPLETE)

    def test_invalid_trusted_anchor_signature_is_tampered(self):
        lock_probability(self.ledger)
        checkpoint = self.ledger.checkpoint()
        request = self.ledger.anchor_request(checkpoint["checkpoint_id"])
        statement = build_anchor_statement(
            request, key_id="tsa", anchored_at=timestamp_now(self.ledger._clock)
        )
        self.ledger.add_anchor(statement, sign_detached(statement, self.anchor_private))
        rows = self._rows()
        next(row for row in rows if row["event_type"] == "anchor")["event"]["signature"] = "AAAA"
        anchor_row = next(row for row in rows if row["event_type"] == "anchor")
        from prepende.canonical import digest
        anchor_row["event_hash"] = digest("event/anchor", anchor_row["event"])
        base = {key: anchor_row[key] for key in anchor_row if key != "row_hash"}
        anchor_row["row_hash"] = digest("row", base)
        self._write(rows)
        self.assertEqual(
            self.ledger.verify(trusted_anchor_keys={"tsa": self.anchor_public}).status,
            STATUS_TAMPERED,
        )

    def test_revoked_anchor_key_cannot_validate_a_backdated_receipt(self):
        lock_probability(self.ledger)
        anchor(self.ledger, key_id="tsa", private_key=self.anchor_private)
        report = self.ledger.verify(trusted_anchor_keys={
            "tsa": {
                "public_key": self.anchor_public,
                "revoked_at": "2023-11-15T00:00:00Z",
            }
        })
        self.assertEqual(report.status, STATUS_TAMPERED)

    def test_authority_time_cannot_precede_checkpoint(self):
        lock_probability(self.ledger)
        checkpoint = self.ledger.checkpoint()
        request = self.ledger.anchor_request(checkpoint["checkpoint_id"])
        statement = build_anchor_statement(
            request, key_id="tsa", anchored_at="2023-11-14T22:00:00.000000Z"
        )
        with self.assertRaises(Exception):
            self.ledger.add_anchor(statement, sign_detached(statement, self.anchor_private))

    def test_checkpoint_clock_rollback_cannot_precede_covered_resolution(self):
        resolver_private, _ = keypair()
        contract = lock_probability(
            self.ledger,
            resolver_policy={"mode": "signed", "authorized_key_ids": ["resolver"]},
        )
        anchor(self.ledger, key_id="tsa", private_key=self.anchor_private)
        self.clock.advance(100)
        statement = self.ledger.prepare_resolution(
            contract.contract_id, evidence=evidence(True), resolver_key_id="resolver"
        )
        self.ledger.resolve_signed(statement, sign_detached(statement, resolver_private))
        self.clock.value -= 50
        with self.assertRaisesRegex(Exception, "timestamp moves backward|precedes a covered event"):
            self.ledger.checkpoint()

    def test_terminal_first_witnessed_after_due_is_incomplete(self):
        contract = lock_probability(self.ledger, due="2023-11-14T22:15:00Z")
        anchor(self.ledger, key_id="tsa", private_key=self.anchor_private)
        self.clock.advance(50)
        self.ledger.resolve(contract.contract_id, evidence=evidence(True))
        self.clock.advance(51)
        anchor(self.ledger, key_id="tsa", private_key=self.anchor_private)
        report = self.ledger.verify(trusted_anchor_keys={"tsa": self.anchor_public})
        self.assertEqual(report.status, STATUS_INCOMPLETE)
        self.assertEqual(report.unwitnessed_terminals, [contract.contract_id])

    def test_anchor_at_resolution_time_does_not_prove_prior_lock(self):
        contract = lock_probability(self.ledger)
        resolution = self.ledger.resolve(contract.contract_id, evidence=evidence(True))
        checkpoint = self.ledger.checkpoint()
        request = self.ledger.anchor_request(checkpoint["checkpoint_id"])
        statement = build_anchor_statement(
            request, key_id="tsa", anchored_at=resolution.resolved_at
        )
        self.ledger.add_anchor(statement, sign_detached(statement, self.anchor_private))
        report = self.ledger.verify(trusted_anchor_keys={"tsa": self.anchor_public})
        self.assertEqual(report.status, STATUS_UNANCHORED)
        self.assertEqual(report.unanchored_contracts, [contract.contract_id])

    def test_signed_resolution_requires_external_trust(self):
        resolver_private, resolver_public = keypair()
        contract = lock_probability(
            self.ledger,
            resolver_policy={"mode": "signed", "authorized_key_ids": ["resolver"]},
        )
        anchor(self.ledger, key_id="tsa", private_key=self.anchor_private)
        self.clock.advance(10)
        statement = self.ledger.prepare_resolution(
            contract.contract_id, evidence=evidence(True), resolver_key_id="resolver"
        )
        self.ledger.resolve_signed(statement, sign_detached(statement, resolver_private))
        anchor(self.ledger, key_id="tsa", private_key=self.anchor_private)
        without_resolver = self.ledger.verify(trusted_anchor_keys={"tsa": self.anchor_public})
        self.assertEqual(without_resolver.status, STATUS_INCOMPLETE)
        verified = self.ledger.verify(
            trusted_anchor_keys={"tsa": self.anchor_public},
            trusted_resolver_keys={"resolver": resolver_public},
        )
        self.assertEqual(verified.status, "OK")
        self.assertTrue(verified.independently_resolved)

    def test_signed_resolution_cannot_be_appended_after_deadline(self):
        resolver_private, _ = keypair()
        contract = lock_probability(
            self.ledger,
            due="2023-11-14T22:15:00Z",
            resolver_policy={"mode": "signed", "authorized_key_ids": ["resolver"]},
        )
        self.clock.advance(50)
        statement = self.ledger.prepare_resolution(
            contract.contract_id, evidence=evidence(True), resolver_key_id="resolver"
        )
        self.clock.advance(100)
        with self.assertRaisesRegex(Exception, "deadline passed"):
            self.ledger.resolve_signed(statement, sign_detached(statement, resolver_private))

    def test_signed_resolution_rejects_boolean_outcome_and_object_note(self):
        resolver_private, _ = keypair()
        contract = lock_probability(
            self.ledger,
            resolver_policy={"mode": "signed", "authorized_key_ids": ["resolver"]},
        )
        statement = self.ledger.prepare_resolution(
            contract.contract_id, evidence=evidence(True), resolver_key_id="resolver"
        )
        boolean_outcome = dict(statement)
        boolean_outcome["outcome"] = {"y": True}
        with self.assertRaises(Exception):
            self.ledger.resolve_signed(
                boolean_outcome, sign_detached(boolean_outcome, resolver_private)
            )
        object_note = dict(statement)
        object_note["note"] = {"not": "text"}
        with self.assertRaises(Exception):
            self.ledger.resolve_signed(object_note, sign_detached(object_note, resolver_private))

    def test_raw_contract_event_cannot_use_lossy_field_coercion(self):
        from prepende.contract import Contract, build_contract

        contract = build_contract(
            stream_id="test-stream", predictor="agent-test", model_version=7,
            domain="unit-test", event_id="raw-contract", question="q",
            kind="probability", claim={"p": "0.5"}, resolution_rule="rule",
            evaluator={"type": "binary_value", "version": "1", "parameters": {"evidence": "result", "field": "result"}},
            evaluation={"id": "e", "spec_digest": "sha256:" + "1" * 64, "artifacts": []},
            issued_at=timestamp_now(self.ledger._clock),
            resolution_due_at="2023-11-16T00:00:00Z",
            resolver_policy={"mode": "self", "authorized_key_ids": []},
            nonresolution_policy={"action": "forfeit", "metric": "brier", "penalty": "1"},
        )
        event = {field: getattr(contract, field) for field in Contract.__dataclass_fields__}
        event["model_version"] = 7
        with self.assertRaisesRegex(Exception, "exact normalized"):
            self.ledger._append_event("contract", event)

    def test_resolution_first_witnessed_after_key_revocation_is_not_independent(self):
        resolver_private, resolver_public = keypair()
        contract = lock_probability(
            self.ledger,
            resolver_policy={"mode": "signed", "authorized_key_ids": ["resolver"]},
        )
        anchor(self.ledger, key_id="tsa", private_key=self.anchor_private)
        statement = self.ledger.prepare_resolution(
            contract.contract_id, evidence=evidence(True), resolver_key_id="resolver"
        )
        self.ledger.resolve_signed(statement, sign_detached(statement, resolver_private))
        self.clock.advance(10)
        anchor(self.ledger, key_id="tsa", private_key=self.anchor_private)
        report = self.ledger.verify(
            trusted_anchor_keys={"tsa": self.anchor_public},
            trusted_resolver_keys={
                "resolver": {
                    "public_key": resolver_public,
                    "revoked_at": "2023-11-14T22:13:25Z",
                }
            },
        )
        self.assertEqual(report.status, STATUS_TAMPERED)
        self.assertFalse(report.independently_resolved)

    def test_mixed_self_and_signed_resolutions_are_not_called_independent(self):
        resolver_private, resolver_public = keypair()
        self_resolved = lock_probability(self.ledger, event_id="self")
        signed = lock_probability(
            self.ledger,
            event_id="signed",
            resolver_policy={"mode": "signed", "authorized_key_ids": ["resolver"]},
        )
        anchor(self.ledger, key_id="tsa", private_key=self.anchor_private)
        self.clock.advance(10)
        self.ledger.resolve(self_resolved.contract_id, evidence=evidence(True))
        statement = self.ledger.prepare_resolution(
            signed.contract_id, evidence=evidence(False), resolver_key_id="resolver"
        )
        self.ledger.resolve_signed(statement, sign_detached(statement, resolver_private))
        anchor(self.ledger, key_id="tsa", private_key=self.anchor_private)
        report = self.ledger.verify(
            trusted_anchor_keys={"tsa": self.anchor_public},
            trusted_resolver_keys={"resolver": resolver_public},
        )
        self.assertEqual(report.status, "OK")
        self.assertFalse(report.independently_resolved)

    def test_expired_resolver_key_is_not_accepted(self):
        resolver_private, resolver_public = keypair()
        contract = lock_probability(
            self.ledger,
            resolver_policy={"mode": "signed", "authorized_key_ids": ["resolver"]},
        )
        statement = self.ledger.prepare_resolution(
            contract.contract_id, evidence=evidence(), resolver_key_id="resolver"
        )
        self.ledger.resolve_signed(statement, sign_detached(statement, resolver_private))
        report = self.ledger.verify(trusted_resolver_keys={
            "resolver": {
                "public_key": resolver_public,
                "valid_until": "2023-11-14T22:00:00Z",
            }
        })
        self.assertEqual(report.status, STATUS_TAMPERED)

    def test_invalid_trusted_resolution_signature_is_tampered(self):
        resolver_private, resolver_public = keypair()
        contract = lock_probability(
            self.ledger,
            resolver_policy={"mode": "signed", "authorized_key_ids": ["resolver"]},
        )
        statement = self.ledger.prepare_resolution(
            contract.contract_id, evidence=evidence(), resolver_key_id="resolver"
        )
        self.ledger.resolve_signed(statement, sign_detached(statement, resolver_private)[:-4] + "AAAA")
        self.assertEqual(
            self.ledger.verify(trusted_resolver_keys={"resolver": resolver_public}).status,
            STATUS_TAMPERED,
        )

    def test_signed_void_must_use_locked_reason(self):
        resolver_private, resolver_public = keypair()
        contract = lock_probability(
            self.ledger,
            resolver_policy={"mode": "signed", "authorized_key_ids": ["resolver"]},
            void_reasons=["source_destroyed"],
        )
        with self.assertRaises(Exception):
            self.ledger.prepare_void(
                contract.contract_id, reason_code="unfavorable_result", resolver_key_id="resolver"
            )
        statement = self.ledger.prepare_void(
            contract.contract_id, reason_code="source_destroyed", resolver_key_id="resolver"
        )
        malformed = dict(statement)
        malformed["note"] = {"not": "text"}
        with self.assertRaises(Exception):
            self.ledger.void_signed(malformed, sign_detached(malformed, resolver_private))
        self.ledger.void_signed(statement, sign_detached(statement, resolver_private))
        report = self.ledger.verify(trusted_resolver_keys={"resolver": resolver_public})
        self.assertEqual(report.counts["void"], 1)
        self.assertNotEqual(report.status, STATUS_TAMPERED)

    def test_concurrent_append_chain_is_valid(self):
        processes = [
            multiprocessing.Process(target=_worker, args=(str(self.path), f"concurrent-{index}"))
            for index in range(8)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(15)
            self.assertEqual(process.exitcode, 0)
        report = self.ledger.verify()
        self.assertTrue(report.internally_valid)
        self.assertEqual(report.counts["contracts"], 8)


if __name__ == "__main__":
    unittest.main()
