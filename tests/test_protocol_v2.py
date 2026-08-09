from __future__ import annotations

import inspect
import json
import math
import tempfile
import unittest
from pathlib import Path

from prepende import Ledger, RetrofitError
from prepende.canonical import CanonicalizationError, canonical_bytes, decimal_string, digest
from prepende.contract import ContractValidationError

from helpers import Clock, evidence, keypair, lock_probability, new_ledger


class CanonicalTests(unittest.TestCase):
    def test_golden_canonical_value(self):
        value = {"z": "é", "a": "0.7", "n": 1, "flag": True}
        self.assertEqual(canonical_bytes(value).decode(), '{"a":"0.7","flag":true,"n":1,"z":"é"}')
        self.assertEqual(
            digest("golden", value),
            "sha256:add79011cc0ce20743a4986bc8e36e868c90e0450abee8fc82a1a161585fcce6",
        )

    def test_float_and_nonfinite_values_are_rejected(self):
        for value in (0.7, float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(CanonicalizationError):
                    canonical_bytes({"value": value})
        for value in (float("nan"), float("inf"), True):
            with self.assertRaises(CanonicalizationError):
                decimal_string(value)

    def test_noncanonical_and_duplicate_key_rows_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, _, path = new_ledger(directory)
            original = path.read_text(encoding="utf-8")
            path.write_text(original.replace("{\"event\":", "{ \"event\":", 1), encoding="utf-8")
            self.assertEqual(ledger.verify().status, "TAMPERED")

        with tempfile.TemporaryDirectory() as directory:
            ledger, _, path = new_ledger(directory)
            line = path.read_text(encoding="utf-8").rstrip("\n")
            path.write_text(
                '{"protocol":"prepende/2",' + line[1:] + "\n",
                encoding="utf-8",
            )
            report = ledger.verify()
            self.assertEqual(report.status, "TAMPERED")
            self.assertTrue(any("duplicate JSON object key" in item for item in report.errors))

        with tempfile.TemporaryDirectory() as directory:
            ledger, _, path = new_ledger(directory)
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
            self.assertEqual(ledger.verify().status, "TAMPERED")

        with tempfile.TemporaryDirectory() as directory:
            ledger, _, path = new_ledger(directory)
            path.write_bytes(path.read_bytes() + b"\xff\n")
            self.assertEqual(ledger.verify().status, "TAMPERED")

    def test_decimal_normalization(self):
        self.assertEqual(decimal_string("0.7000"), "0.7")
        self.assertEqual(decimal_string("-0.000"), "0")


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger, self.clock, self.path = new_ledger(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_lock_assigns_timestamp_and_full_id(self):
        contract = lock_probability(self.ledger)
        self.assertTrue(contract.contract_id.startswith("sha256:"))
        self.assertEqual(len(contract.contract_id), 71)
        self.assertEqual(contract.cid, contract.contract_id)
        self.assertTrue(contract.verify())
        self.assertNotIn("created_at", inspect.signature(self.ledger.lock_prediction).parameters)

    def test_public_timestamp_overrides_are_rejected(self):
        with self.assertRaises(TypeError):
            self.ledger.lock_prediction(created_at=1)  # type: ignore[call-arg]
        contract = lock_probability(self.ledger)
        with self.assertRaises(TypeError):
            self.ledger.resolve(contract.contract_id, evidence=evidence(), resolved_at=1)  # type: ignore[call-arg]
        self.assertNotIn("now", inspect.signature(self.ledger.verify).parameters)

    def test_probability_validation(self):
        for bad in ("1.5", "-0.1", True, float("nan"), float("inf")):
            with self.subTest(bad=bad):
                with self.assertRaises((CanonicalizationError, ContractValidationError)):
                    lock_probability(self.ledger, event_id=f"bad-{repr(bad)}", p=bad)

    def test_timestamp_requires_a_literal_rfc3339_t_separator(self):
        with self.assertRaises(ContractValidationError):
            lock_probability(self.ledger, due="2023-11-16Q00:00:00Z")

    def test_contract_timestamp_cannot_move_before_stream_genesis(self):
        self.clock.value -= 1
        with self.assertRaisesRegex(Exception, "timestamp moves backward"):
            lock_probability(self.ledger)
        self.clock.value += 1
        self.assertTrue(self.ledger.verify().internally_valid)

    def test_numeric_and_categorical_validation(self):
        common = dict(
            predictor="agent-test", model_version="m", domain="d", question="q",
            evaluation={"id": "e", "spec_digest": "sha256:" + "1" * 64, "artifacts": []},
            resolution_due_at="2023-11-16T00:00:00Z",
            resolver_policy={"mode": "self", "authorized_key_ids": []},
        )
        with self.assertRaises((ContractValidationError, CanonicalizationError)):
            self.ledger.lock_prediction(
                **common, event_id="numeric-bad", kind="numeric",
                claim={"value": "5", "lo": "10", "hi": "20", "unit": "ms"},
                resolution_rule="field", evaluator={"type": "numeric_field", "version": "1", "parameters": {"evidence": "measurement", "field": "value"}},
                nonresolution_policy={"action": "forfeit", "metric": "absolute_error", "penalty": "10"},
            )
        with self.assertRaises((ContractValidationError, CanonicalizationError)):
            self.ledger.lock_prediction(
                **common, event_id="category-bad", kind="categorical",
                claim={"label": "a", "p": "2"}, resolution_rule="field",
                evaluator={"type": "categorical_field", "version": "1", "parameters": {"evidence": "category", "field": "label"}},
                nonresolution_policy={"action": "forfeit", "metric": "brier", "penalty": "1"},
            )

        categorical = self.ledger.lock_prediction(
            **common, event_id="category-outcome", kind="categorical",
            claim={"label": "a", "p": "0.5"}, resolution_rule="field",
            evaluator={"type": "categorical_field", "version": "1", "parameters": {"evidence": "category", "field": "label"}},
            nonresolution_policy={"action": "forfeit", "metric": "brier", "penalty": "1"},
        )
        with self.assertRaisesRegex(Exception, "must be a string"):
            self.ledger.resolve(categorical.contract_id, evidence=[{
                "name": "category", "uri": "urn:test:category",
                "content": {"label": {"not": "a protocol label"}},
            }])

    def test_duplicate_logical_event_is_rejected(self):
        lock_probability(self.ledger, event_id="same")
        with self.assertRaises(RetrofitError) as caught:
            lock_probability(self.ledger, event_id="same")
        self.assertIn("logical", str(caught.exception))

    def test_unicode_equivalent_logical_event_is_rejected_without_poisoning_stream(self):
        lock_probability(self.ledger, event_id="caf\u00e9")
        with self.assertRaises(RetrofitError):
            lock_probability(self.ledger, event_id="cafe\u0301")
        self.assertTrue(self.ledger.verify().internally_valid)

    def test_unicode_line_separator_characters_roundtrip_inside_json_strings(self):
        expected = []
        for index, separator in enumerate(("\u0085", "\u2028", "\u2029")):
            question = f"valid{separator}question"
            expected.append(question)
            self.ledger.lock_prediction(
                predictor="agent-test", model_version="m", domain="unicode",
                event_id=f"separator-{index}", question=question, kind="probability",
                claim={"p": "0.5"}, resolution_rule="rule",
                evaluator={"type": "binary_value", "version": "1", "parameters": {"evidence": "result", "field": "result"}},
                evaluation={"id": "unicode", "spec_digest": "sha256:" + "1" * 64, "artifacts": []},
                resolution_due_at="2023-11-16T00:00:00Z",
                resolver_policy={"mode": "self", "authorized_key_ids": []},
                nonresolution_policy={"action": "forfeit", "metric": "brier", "penalty": "1"},
            )
        report = self.ledger.verify()
        self.assertTrue(report.internally_valid)
        self.assertEqual([contract.question for contract, _ in self.ledger.records()], expected)
        self.assertEqual(self.path.read_bytes().count(b"\n"), 1 + len(expected))

    def test_evaluation_artifact_digest_is_enforced_at_resolution(self):
        intended = {"result": True}
        artifact_digest = digest("evidence", intended)
        contract = self.ledger.lock_prediction(
            predictor="agent-test", model_version="model-v1", domain="unit-test", event_id="artifact",
            question="artifact-bound result", kind="probability", claim={"p": "0.5"},
            resolution_rule="read result",
            evaluator={"type": "binary_value", "version": "1", "parameters": {"evidence": "dataset", "field": "result"}},
            evaluation={
                "id": "artifact-v1", "spec_digest": "sha256:" + "1" * 64,
                "artifacts": [{"role": "dataset", "uri": "urn:test:dataset", "digest": artifact_digest}],
            },
            resolution_due_at="2023-11-16T00:00:00Z",
            resolver_policy={"mode": "self", "authorized_key_ids": []},
            nonresolution_policy={"action": "forfeit", "metric": "brier", "penalty": "1"},
        )
        with self.assertRaisesRegex(Exception, "artifact binding mismatch"):
            self.ledger.resolve(
                contract.contract_id,
                evidence=[{"name": "dataset", "uri": "urn:test:dataset", "content": {"result": False}}],
            )
        result = self.ledger.resolve(
            contract.contract_id,
            evidence=[{"name": "dataset", "uri": "urn:test:dataset", "content": intended}],
        )
        self.assertEqual(result.outcome, {"y": 1})

    def test_unbound_extra_evidence_cannot_drive_the_evaluator(self):
        pinned = {"result": False}
        contract = self.ledger.lock_prediction(
            predictor="agent-test", model_version="model-v1", domain="unit-test", event_id="selected-artifact",
            question="artifact-bound result", kind="probability", claim={"p": "0.5"},
            resolution_rule="read pinned result",
            evaluator={"type": "binary_value", "version": "1", "parameters": {"evidence": "dataset", "field": "result"}},
            evaluation={
                "id": "artifact-v1", "spec_digest": "sha256:" + "1" * 64,
                "artifacts": [{
                    "role": "dataset", "uri": "urn:test:dataset",
                    "digest": digest("evidence", pinned),
                }],
            },
            resolution_due_at="2023-11-16T00:00:00Z",
            resolver_policy={"mode": "self", "authorized_key_ids": []},
            nonresolution_policy={"action": "forfeit", "metric": "brier", "penalty": "1"},
        )
        result = self.ledger.resolve(contract.contract_id, evidence=[
            {"name": "attacker", "uri": "urn:test:attacker", "content": {"result": True}},
            {"name": "dataset", "uri": "urn:test:dataset", "content": pinned},
        ])
        self.assertEqual(result.outcome, {"y": 0})

    def test_contract_predictor_must_match_registered_stream(self):
        with self.assertRaises(Exception) as caught:
            self.ledger.lock_prediction(
                predictor="different-agent", model_version="m", domain="d", event_id="e",
                question="q", kind="probability", claim={"p": "0.5"}, resolution_rule="rule",
                evaluator={"type": "binary_value", "version": "1", "parameters": {"evidence": "result", "field": "result"}},
                evaluation={"id": "e", "spec_digest": "sha256:" + "1" * 64, "artifacts": []},
                resolution_due_at="2023-11-16T00:00:00Z",
                resolver_policy={"mode": "self", "authorized_key_ids": []},
                nonresolution_policy={"action": "forfeit", "metric": "brier", "penalty": "1"},
            )
        self.assertIn("registered predictor", str(caught.exception))

    def test_resolution_is_computed_from_evidence(self):
        contract = lock_probability(self.ledger)
        resolution = self.ledger.resolve(contract.contract_id, evidence=evidence(False), note="miss")
        self.assertEqual(resolution.outcome, {"y": 0})
        self.assertTrue(resolution.verify_id())
        with self.assertRaises(RetrofitError):
            self.ledger.resolve(contract.contract_id, evidence=evidence(True))

    def test_bad_evidence_digest_is_rejected(self):
        contract = lock_probability(self.ledger)
        bad = evidence(True)
        bad[0]["digest"] = "sha256:" + "0" * 64
        with self.assertRaises(Exception):
            self.ledger.resolve(contract.contract_id, evidence=bad)

    def test_forfeit_only_after_deadline(self):
        contract = lock_probability(self.ledger, due="2023-11-14T23:00:00Z")
        with self.assertRaises(RetrofitError):
            self.ledger.forfeit(contract.contract_id)
        self.clock.advance(3600)
        disposition = self.ledger.forfeit(contract.contract_id)
        self.assertEqual(disposition.disposition, "forfeit")
        self.assertEqual(self.ledger.verify().counts["forfeited"], 1)

    def test_late_resolution_is_refused_in_favor_of_forfeit(self):
        contract = lock_probability(self.ledger, due="2023-11-14T23:00:00Z")
        self.clock.advance(3601)
        with self.assertRaises(RetrofitError):
            self.ledger.resolve(contract.contract_id, evidence=evidence())

    def test_overdue_open_contract_is_incomplete(self):
        contract = lock_probability(self.ledger, due="2023-11-14T23:00:00Z")
        self.clock.advance(3601)
        report = self.ledger.verify()
        self.assertEqual(report.status, "INCOMPLETE")
        self.assertEqual(report.overdue, [contract.contract_id])


if __name__ == "__main__":
    unittest.main()
