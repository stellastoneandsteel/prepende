from __future__ import annotations

import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path

from prepende import LegacyLedger, Ledger, build_report, grouped_summaries
from prepende.canonical import canonical_bytes, digest
from prepende.contract import Contract, build_contract
from prepende.plot import reliability_svg

from helpers import Clock, evidence, lock_probability, new_ledger


ROOT = Path(__file__).resolve().parents[1]


class CompatibilityTests(unittest.TestCase):
    def test_public_v1_corpus_is_byte_stable_and_explicitly_unanchored(self):
        path = ROOT / "experiments" / "predictions.jsonl"
        self.assertEqual(
            hashlib.sha256(path.read_bytes()).hexdigest(),
            "27caf12de20951b2df40eade0c357702dcd5536a49194845eb0d3a1853eeb975",
        )
        ledger = LegacyLedger(path)
        records = ledger.records()
        self.assertEqual((len(records), sum(r is not None for _, r in records)), (26, 14))
        report = ledger.integrity()
        self.assertEqual(report["status"], "UNANCHORED")
        self.assertIn("does not hash resolutions", report["warnings"][0])
        unresolved = next(contract for contract, terminal in records if terminal is None)
        with self.assertRaises(RuntimeError):
            ledger.resolve(unresolved.cid, {"y": 1}, unresolved.eval_regime)

    def test_v2_legacy_import_commits_exact_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v2.jsonl"
            legacy = ROOT / "experiments" / "predictions.jsonl"
            ledger = Ledger.from_legacy(
                path,
                legacy_path=legacy,
                stream_id="prepende-public-v2",
                registered_predictor="prepende",
                git_commit="d92e631",
                _clock=Clock(),
            )
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            imported = rows[1]["event"]
            self.assertEqual(imported["source_rows"], 40)
            self.assertEqual(imported["source_bytes"], len(legacy.read_bytes()))
            self.assertEqual(imported["classification"], "legacy-self-attested")
            self.assertEqual(ledger.verify().status, "UNANCHORED")
            duplicate = dict(imported)
            duplicate["source_digest"] = "sha256:" + "2" * 64
            with self.assertRaisesRegex(Exception, "single cutover event"):
                ledger._append_event("legacy_import", duplicate)

    def test_legacy_import_rejects_malformed_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, _, _ = new_ledger(directory)
            with self.assertRaisesRegex(Exception, "git_commit"):
                ledger._append_event("legacy_import", {
                    "source_protocol": "prepende/1",
                    "source_digest": "sha256:" + "1" * 64,
                    "source_bytes": 1,
                    "source_rows": 1,
                    "git_commit": True,
                    "classification": "legacy-self-attested",
                    "imported_at": "2023-11-14T22:13:20.000000Z",
                })

    def test_golden_vectors_match(self):
        vectors = json.loads((ROOT / "docs" / "golden-vectors-v2.json").read_text())
        canonical = vectors["canonical"]
        self.assertEqual(canonical_bytes(canonical["input"]).decode(), canonical["json"])
        self.assertEqual(digest("golden", canonical["input"]), canonical["golden_domain_digest"])
        row = Ledger._row("genesis", vectors["genesis_row"]["event"], seq=0, prev_hash=None)
        self.assertEqual(row, vectors["genesis_row"])

    def test_all_v2_rows_validate_against_json_schema(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema test extra is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            ledger, _, path = new_ledger(directory)
            lock_probability(ledger)
            schema = json.loads((ROOT / "schemas" / "protocol-v2-row.schema.json").read_text())
            for line in path.read_text().splitlines():
                jsonschema.validate(json.loads(line), schema)


class ReportingTests(unittest.TestCase):
    def test_public_site_contains_no_below_floor_curve_or_skill_headline(self):
        html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        fragment = html.split("<!--LEDGER:rel-->", 1)[1].split("<!--/LEDGER:rel-->", 1)[0]
        self.assertIn("INSUFFICIENT EVIDENCE", fragment)
        self.assertNotIn("polyline", fragment)
        self.assertNotIn("Brier", fragment)
        self.assertNotIn("skill", fragment)
        public_copy = "\n".join(
            path.read_text(encoding="utf-8") for path in (ROOT / "docs").rglob("*.html")
        ).lower()
        for stale_claim in (
            "calibration-scored", "calibration signal", "honest calibration",
            "running brier", "tamper-evidence chain",
        ):
            self.assertNotIn(stale_claim, public_copy)

    def test_calibration_is_suppressed_below_floor(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, _, _ = new_ledger(directory)
            for index in range(5):
                contract = lock_probability(ledger, event_id=f"small-{index}")
                ledger.resolve(contract.contract_id, evidence=evidence(index % 2 == 0))
            report = build_report(ledger)
            self.assertIn("INSUFFICIENT_EVIDENCE", report)
            self.assertNotIn("skill=", report)
            groups = grouped_summaries(ledger.records())
            self.assertEqual(groups[0]["evidence_status"], "INSUFFICIENT_EVIDENCE")
            lowered = grouped_summaries(ledger.records(), minimum_n=1)
            self.assertEqual(lowered[0]["evidence_status"], "INSUFFICIENT_EVIDENCE")

    def test_predictors_domains_and_provenance_are_not_pooled(self):
        with tempfile.TemporaryDirectory() as first_directory, tempfile.TemporaryDirectory() as second_directory:
            ledger, _, _ = new_ledger(first_directory)
            first = lock_probability(ledger, event_id="one")
            other, _, _ = new_ledger(second_directory, stream_id="other-stream")
            second = other.lock_prediction(
                predictor="agent-test", model_version="m2", domain="other-domain", event_id="two",
                question="q", kind="probability", claim={"p": "0.5"}, resolution_rule="rule",
                evaluator={"type": "binary_value", "version": "1", "parameters": {"evidence": "result", "field": "result"}},
                evaluation={"id": "e", "spec_digest": "sha256:" + "1" * 64, "artifacts": []},
                resolution_due_at="2023-11-16T00:00:00Z",
                resolver_policy={"mode": "self", "authorized_key_ids": []},
                nonresolution_policy={"action": "forfeit", "metric": "brier", "penalty": "1"},
                provenance="retrospective",
            )
            groups = grouped_summaries([(first, None), (second, None)])
            self.assertEqual(len(groups), 2)

    def test_identical_contracts_from_different_streams_never_reach_a_shared_floor(self):
        with tempfile.TemporaryDirectory() as first_directory, tempfile.TemporaryDirectory() as second_directory:
            first_ledger, _, _ = new_ledger(first_directory, stream_id="tenant-a")
            second_ledger, _, _ = new_ledger(second_directory, stream_id="tenant-b")
            records = []
            for index in range(15):
                first = lock_probability(first_ledger, event_id=f"shared-{index}")
                second = lock_probability(second_ledger, event_id=f"shared-{index}")
                records.append((first, first_ledger.resolve(first.contract_id, evidence=evidence(True))))
                records.append((second, second_ledger.resolve(second.contract_id, evidence=evidence(True))))
            groups = grouped_summaries(records)
            self.assertEqual(len(groups), 2)
            self.assertEqual({group["stream_id"] for group in groups}, {"tenant-a", "tenant-b"})
            self.assertTrue(all(group["n_prob"] == 15 for group in groups))
            self.assertTrue(all(group["evidence_status"] == "INSUFFICIENT_EVIDENCE" for group in groups))

    def test_same_regime_label_with_different_spec_digest_is_not_pooled(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, _, _ = new_ledger(directory)
            first = lock_probability(ledger, event_id="regime-one")
            second = ledger.lock_prediction(
                predictor="agent-test", model_version="model-v1", domain="unit-test", event_id="regime-two",
                question="q", kind="probability", claim={"p": "0.5"}, resolution_rule="rule",
                evaluator={"type": "binary_value", "version": "1", "parameters": {"evidence": "result", "field": "result"}},
                evaluation={"id": "test-harness-v1", "spec_digest": "sha256:" + "2" * 64, "artifacts": []},
                resolution_due_at="2023-11-16T00:00:00Z",
                resolver_policy={"mode": "self", "authorized_key_ids": []},
                nonresolution_policy={"action": "forfeit", "metric": "brier", "penalty": "1"},
            )
            self.assertEqual(len(grouped_summaries([(first, None), (second, None)])), 2)

    def test_same_spec_with_different_artifact_manifests_is_not_pooled(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, _, _ = new_ledger(directory)
            common = dict(
                predictor="agent-test", model_version="model-v1", domain="unit-test",
                question="q", kind="probability", claim={"p": "0.5"}, resolution_rule="rule",
                evaluator={"type": "binary_value", "version": "1", "parameters": {"evidence": "result", "field": "result"}},
                resolution_due_at="2023-11-16T00:00:00Z",
                resolver_policy={"mode": "self", "authorized_key_ids": []},
                nonresolution_policy={"action": "forfeit", "metric": "brier", "penalty": "1"},
            )
            first = ledger.lock_prediction(
                **common, event_id="artifact-one",
                evaluation={
                    "id": "same", "spec_digest": "sha256:" + "1" * 64,
                    "artifacts": [{"role": "dataset", "uri": "urn:one", "digest": "sha256:" + "2" * 64}],
                },
            )
            second = ledger.lock_prediction(
                **common, event_id="artifact-two",
                evaluation={
                    "id": "same", "spec_digest": "sha256:" + "1" * 64,
                    "artifacts": [{"role": "dataset", "uri": "urn:two", "digest": "sha256:" + "3" * 64}],
                },
            )
            self.assertEqual(len(grouped_summaries([(first, None), (second, None)])), 2)

    def test_different_evaluator_parameters_are_not_pooled(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, _, _ = new_ledger(directory)
            common = dict(
                predictor="agent-test", model_version="model-v1", domain="unit-test",
                question="q", kind="probability", claim={"p": "0.5"}, resolution_rule="threshold",
                evaluation={"id": "same", "spec_digest": "sha256:" + "1" * 64, "artifacts": []},
                resolution_due_at="2023-11-16T00:00:00Z",
                resolver_policy={"mode": "self", "authorized_key_ids": []},
                nonresolution_policy={"action": "forfeit", "metric": "brier", "penalty": "1"},
            )
            first = ledger.lock_prediction(
                **common, event_id="threshold-one",
                evaluator={
                    "type": "binary_threshold", "version": "1",
                    "parameters": {"evidence": "result", "field": "value", "operator": ">=", "threshold": "1"},
                },
            )
            second = ledger.lock_prediction(
                **common, event_id="threshold-two",
                evaluator={
                    "type": "binary_threshold", "version": "1",
                    "parameters": {"evidence": "result", "field": "value", "operator": ">=", "threshold": "2"},
                },
            )
            self.assertEqual(len(grouped_summaries([(first, None), (second, None)])), 2)

    def test_different_scoring_and_resolver_policies_are_not_pooled(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, _, _ = new_ledger(directory)
            first = lock_probability(ledger, event_id="policy-one")
            second = ledger.lock_prediction(
                predictor="agent-test", model_version="model-v1", domain="unit-test",
                event_id="policy-two", question="q", kind="probability", claim={"p": "0.7"},
                resolution_rule="rule",
                evaluator={"type": "binary_value", "version": "1", "parameters": {"evidence": "result", "field": "result"}},
                evaluation={"id": "test-harness-v1", "spec_digest": "sha256:" + "1" * 64, "artifacts": []},
                resolution_due_at="2023-11-16T00:00:00Z",
                resolver_policy={"mode": "signed", "authorized_key_ids": ["resolver-b"]},
                nonresolution_policy={"action": "forfeit", "metric": "brier", "penalty": "0.5"},
            )
            self.assertEqual(len(grouped_summaries([(first, None), (second, None)])), 2)

    def test_plot_refuses_to_pool_different_evaluation_specs(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, _, _ = new_ledger(directory)
            first = lock_probability(ledger, event_id="plot-one")
            second = ledger.lock_prediction(
                predictor="agent-test", model_version="model-v1", domain="unit-test", event_id="plot-two",
                question="q", kind="probability", claim={"p": "0.5"}, resolution_rule="rule",
                evaluator={"type": "binary_value", "version": "1", "parameters": {"evidence": "result", "field": "result"}},
                evaluation={"id": "test-harness-v1", "spec_digest": "sha256:" + "2" * 64, "artifacts": []},
                resolution_due_at="2023-11-16T00:00:00Z",
                resolver_policy={"mode": "self", "authorized_key_ids": []},
                nonresolution_policy={"action": "forfeit", "metric": "brier", "penalty": "1"},
            )
            with self.assertRaisesRegex(ValueError, "refusing to pool"):
                reliability_svg([(first, None), (second, None)], str(Path(directory) / "curve.svg"))

    def test_numeric_forfeit_uses_locked_absolute_error_penalty(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, clock, _ = new_ledger(directory)
            contract = ledger.lock_prediction(
                predictor="agent-test", model_version="m", domain="latency", event_id="numeric-forfeit",
                question="latency", kind="numeric",
                claim={"value": "100", "lo": "80", "hi": "120", "unit": "ms"},
                resolution_rule="read evidence.value",
                evaluator={"type": "numeric_field", "version": "1", "parameters": {"evidence": "latency", "field": "value"}},
                evaluation={"id": "latency-v1", "spec_digest": "sha256:" + "1" * 64, "artifacts": []},
                resolution_due_at="2023-11-14T23:00:00Z",
                resolver_policy={"mode": "self", "authorized_key_ids": []},
                nonresolution_policy={"action": "forfeit", "metric": "absolute_error", "penalty": "250"},
            )
            clock.advance(3601)
            terminal = ledger.forfeit(contract.contract_id)
            group = grouped_summaries([(contract, terminal)])[0]
            self.assertEqual(group["numeric_penalized"]["penalized_mae"], 250.0)

    def test_resolved_numeric_metrics_are_computed_inside_their_cohort(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, _, _ = new_ledger(directory)
            contract = ledger.lock_prediction(
                predictor="agent-test", model_version="m", domain="latency", event_id="numeric-resolved",
                question="latency", kind="numeric",
                claim={"value": "100", "lo": "80", "hi": "120", "unit": "ms"},
                resolution_rule="read evidence.value",
                evaluator={"type": "numeric_field", "version": "1", "parameters": {"evidence": "latency", "field": "value"}},
                evaluation={"id": "latency-v1", "spec_digest": "sha256:" + "1" * 64, "artifacts": []},
                resolution_due_at="2023-11-16T00:00:00Z",
                resolver_policy={"mode": "self", "authorized_key_ids": []},
                nonresolution_policy={"action": "forfeit", "metric": "absolute_error", "penalty": "250"},
            )
            terminal = ledger.resolve(
                contract.contract_id,
                evidence=[{"name": "latency", "uri": "urn:test:latency", "content": {"value": "110"}}],
            )
            group = grouped_summaries([(contract, terminal)])[0]
            self.assertEqual(group["numeric"], {"n_numeric": 1, "mae": 10.0, "ci_coverage": 1.0})

    def test_one_pass_scan_handles_large_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, _, path = new_ledger(directory)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            previous = rows[-1]["row_hash"]
            for index in range(10_000):
                contract = build_contract(
                    stream_id="test-stream", predictor="agent-test", model_version="load-test",
                    domain="performance", event_id=f"event-{index}", question="q",
                    kind="probability", claim={"p": "0.5"}, resolution_rule="rule",
                    evaluator={"type": "binary_value", "version": "1", "parameters": {"evidence": "result", "field": "result"}},
                    evaluation={"id": "load", "spec_digest": "sha256:" + "1" * 64, "artifacts": []},
                    issued_at="2023-11-14T22:13:20.000000Z",
                    resolution_due_at="2023-11-16T00:00:00Z",
                    resolver_policy={"mode": "self", "authorized_key_ids": []},
                    nonresolution_policy={"action": "forfeit", "metric": "brier", "penalty": "1"},
                )
                event = {field: getattr(contract, field) for field in Contract.__dataclass_fields__}
                row = Ledger._row("contract", event, seq=len(rows), prev_hash=previous)
                rows.append(row)
                previous = row["row_hash"]
            path.write_text("\n".join(canonical_bytes(row).decode() for row in rows) + "\n")
            started = time.monotonic()
            report = ledger.verify()
            elapsed = time.monotonic() - started
            self.assertTrue(report.internally_valid)
            self.assertLess(elapsed, 5.0)


if __name__ == "__main__":
    unittest.main()
