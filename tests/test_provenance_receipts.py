from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from reportmachine_runner.phase_contracts import build_run_artifacts, phase_output_contracts
from reportmachine_runner.phase_provenance import (
    PhaseMutationGuard,
    ProvenanceContext,
    RunnerMutationViolation,
    capture_ingest_receipt_baseline,
    ingest_receipt_rel,
    invalidate_from_phase,
    receipt_row_producer,
    write_ingest_receipt,
    write_phase_checkpoint,
)
from reportmachine_runner.source_ingest import run_research_candidate_ingest, run_source_ingest
from reportmachine_runner.source_ingest_paths import SourceIngestConfig, build_source_ingest_paths
from reportmachine_runner.source_ledgers import (
    RENDERED_IMAGES_HEADER,
    SOURCE_LEDGER_CANDIDATE_HEADER,
)


class ProvenanceReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.artifacts = build_run_artifacts(
            work_run_dir="work/runs/test_run",
            outputs_dir="outputs/test_run",
            article="outputs/test_run/article.md",
            source_ledger="outputs/test_run/source_ledger.csv",
            image_ledger="outputs/test_run/image_ledger.csv",
            claim_ledger="outputs/test_run/claim_ledger.json",
            article_result="outputs/test_run/article_result.json",
        )
        self.context = ProvenanceContext(
            root=self.root,
            run_id="test_run",
            artifacts=self.artifacts,
            topic_path="inputs/topic.md",
            sources_path="inputs/sources.csv",
        )
        self.ingest_paths = build_source_ingest_paths(self.artifacts.work_run_dir)
        (self.root / "inputs").mkdir(parents=True)
        (self.root / "inputs" / "topic.md").write_text("topic\n", encoding="utf-8")
        (self.root / "inputs" / "sources.csv").write_text(
            "source_id,url\nS1,https://example.com/s1\n",
            encoding="utf-8",
        )
        (self.root / self.artifacts.work_run_dir / "research").mkdir(
            parents=True,
            exist_ok=True,
        )
        (self.root / self.artifacts.outputs_dir).mkdir(parents=True, exist_ok=True)
        self.write_ledgers([], [])

    def source_row(
        self,
        source_id: str,
        *,
        digest: str | None = None,
        extracted_text_path: str = "",
    ) -> dict[str, str]:
        row = {column: "" for column in SOURCE_LEDGER_CANDIDATE_HEADER}
        row.update(
            {
                "source_id": source_id,
                "sha256": digest or f"hash-{source_id}",
                "ingest_status": "registered_local",
                "extracted_text_path": extracted_text_path,
            }
        )
        return row

    def image_row(self, image_path: str, *, digest: str | None = None) -> dict[str, str]:
        row = {column: "" for column in RENDERED_IMAGES_HEADER}
        row.update(
            {
                "image_path": image_path,
                "source_doc": "work/runs/test_run/research/sources/S1.pdf",
                "source_id": "S1",
                "page": "1",
                "source_hash": "hash-S1",
                "image_hash": digest or f"hash-{Path(image_path).name}",
                "status": "ok",
            }
        )
        return row

    def write_csv(self, rel: str, header: list[str], rows: list[dict[str, str]]) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=header)
            writer.writeheader()
            writer.writerows(rows)

    def write_ledgers(
        self,
        source_rows: list[dict[str, str]],
        image_rows: list[dict[str, str]],
    ) -> None:
        self.write_csv(
            self.ingest_paths.source_candidate_ledger,
            SOURCE_LEDGER_CANDIDATE_HEADER,
            source_rows,
        )
        self.write_csv(
            self.ingest_paths.rendered_images_ledger,
            RENDERED_IMAGES_HEADER,
            image_rows,
        )

    def receipt(self, stage: str) -> dict:
        path = self.root / ingest_receipt_rel(self.artifacts, stage)
        return json.loads(path.read_text(encoding="utf-8"))

    def seed_prepare(
        self,
        source_rows: list[dict[str, str]],
        image_rows: list[dict[str, str]] | None = None,
    ) -> None:
        baseline = capture_ingest_receipt_baseline(self.context)
        rendered = image_rows or []
        self.write_ledgers(source_rows, rendered)
        write_ingest_receipt(
            self.context,
            "prepare",
            baseline=baseline,
            input_paths=("inputs/sources.csv",),
            written_source_ids=tuple(row["source_id"] for row in source_rows),
            written_image_paths=tuple(row["image_path"] for row in rendered),
        )

    def test_two_evidence_ingests_are_cumulative_and_rebind_mutated_input(self) -> None:
        self.seed_prepare([self.source_row("S1")])
        candidate = "work/runs/test_run/research/source_candidates.primary.jsonl"
        candidate_path = self.root / candidate
        candidate_path.write_text('{"candidate_id":"c1"}\n', encoding="utf-8")

        first = capture_ingest_receipt_baseline(self.context)
        self.write_ledgers([self.source_row("S1"), self.source_row("S2")], [])
        write_ingest_receipt(
            self.context,
            "evidence",
            baseline=first,
            input_paths=("inputs/sources.csv", candidate),
            written_source_ids=("S2",),
            written_image_paths=(),
        )
        source_ledger = self.ingest_paths.source_candidate_ledger
        self.assertEqual(
            set(self.receipt("evidence")["ledger_row_hashes"][source_ledger]),
            {"S1", "S2"},
        )

        # Capture lineage while the first receipt still matches, then simulate the next
        # runner merge changing a receipted candidate input before ingest adds S3.
        second = capture_ingest_receipt_baseline(self.context)
        candidate_path.write_text('{"candidate_id":"c2"}\n', encoding="utf-8")
        self.write_ledgers(
            [self.source_row("S1"), self.source_row("S2"), self.source_row("S3")],
            [],
        )
        write_ingest_receipt(
            self.context,
            "evidence",
            baseline=second,
            input_paths=("inputs/sources.csv", candidate),
            written_source_ids=("S3",),
            written_image_paths=(),
        )

        receipt = self.receipt("evidence")
        self.assertEqual(
            set(receipt["ledger_row_hashes"][source_ledger]),
            {"S1", "S2", "S3"},
        )
        self.assertEqual(
            set(
                capture_ingest_receipt_baseline(self.context)
                .valid_receipt_rows[source_ledger]
            ),
            {"S1", "S2", "S3"},
        )

    def test_unreceipted_and_tampered_rows_are_not_laundered(self) -> None:
        self.seed_prepare([self.source_row("S1")])
        candidate = "work/runs/test_run/research/source_candidates.primary.jsonl"
        (self.root / candidate).write_text("{}\n", encoding="utf-8")
        source_ledger = self.ingest_paths.source_candidate_ledger

        # U was present before this ingest and has no valid receipt. Only the explicit S2
        # delta may join the carried S1 row.
        self.write_ledgers([self.source_row("S1"), self.source_row("U")], [])
        first = capture_ingest_receipt_baseline(self.context)
        self.write_ledgers(
            [self.source_row("S1"), self.source_row("U"), self.source_row("S2")],
            [],
        )
        write_ingest_receipt(
            self.context,
            "evidence",
            baseline=first,
            input_paths=("inputs/sources.csv", candidate),
            written_source_ids=("S2",),
            written_image_paths=(),
        )
        self.assertEqual(
            set(self.receipt("evidence")["ledger_row_hashes"][source_ledger]),
            {"S1", "S2"},
        )

        # Changing S1 after the baseline breaks carry eligibility. S2 remains identical,
        # S3 is the only owned delta, and U remains unreceipted.
        second = capture_ingest_receipt_baseline(self.context)
        self.write_ledgers(
            [
                self.source_row("S1", digest="tampered"),
                self.source_row("U"),
                self.source_row("S2"),
                self.source_row("S3"),
            ],
            [],
        )
        write_ingest_receipt(
            self.context,
            "evidence",
            baseline=second,
            input_paths=("inputs/sources.csv", candidate),
            written_source_ids=("S3",),
            written_image_paths=(),
        )
        self.assertEqual(
            set(self.receipt("evidence")["ledger_row_hashes"][source_ledger]),
            {"S2", "S3"},
        )

    def test_invalidation_preserves_receipt_but_changed_input_revokes_it(self) -> None:
        self.seed_prepare([self.source_row("S1")])
        candidate = "work/runs/test_run/research/source_candidates.primary.jsonl"
        candidate_path = self.root / candidate
        candidate_path.write_text("{}\n", encoding="utf-8")
        baseline = capture_ingest_receipt_baseline(self.context)
        self.write_ledgers([self.source_row("S1"), self.source_row("S2")], [])
        write_ingest_receipt(
            self.context,
            "evidence",
            baseline=baseline,
            input_paths=("inputs/sources.csv", candidate),
            written_source_ids=("S2",),
            written_image_paths=(),
        )
        write_phase_checkpoint(self.context, "research")
        write_phase_checkpoint(self.context, "evidence")

        receipt_path = self.root / ingest_receipt_rel(self.artifacts, "evidence")
        invalidate_from_phase(self.context, "research")
        self.assertTrue(receipt_path.is_file())
        self.assertFalse(
            (self.root / "work/runs/test_run/provenance/research.json").exists()
        )
        self.assertFalse(
            (self.root / "work/runs/test_run/provenance/evidence.json").exists()
        )

        candidate_path.write_text('{"changed":true}\n', encoding="utf-8")
        valid = capture_ingest_receipt_baseline(self.context).valid_receipt_rows[
            self.ingest_paths.source_candidate_ledger
        ]
        self.assertEqual(set(valid), {"S1"})

    def test_rendered_rows_are_carried_and_extended(self) -> None:
        image_one = "work/runs/test_run/images/rendered/S1_page_001.png"
        image_two = "work/runs/test_run/images/rendered/S2_page_001.png"
        self.seed_prepare([self.source_row("S1")], [self.image_row(image_one)])
        candidate = "work/runs/test_run/research/source_candidates.primary.jsonl"
        (self.root / candidate).write_text("{}\n", encoding="utf-8")
        baseline = capture_ingest_receipt_baseline(self.context)
        self.write_ledgers(
            [self.source_row("S1"), self.source_row("S2")],
            [self.image_row(image_one), self.image_row(image_two)],
        )
        write_ingest_receipt(
            self.context,
            "evidence",
            baseline=baseline,
            input_paths=("inputs/sources.csv", candidate),
            written_source_ids=("S2",),
            written_image_paths=(image_two,),
        )

        rows = self.receipt("evidence")["ledger_row_hashes"]
        self.assertEqual(
            set(rows[self.ingest_paths.source_candidate_ledger]),
            {"S1", "S2"},
        )
        self.assertEqual(
            set(rows[self.ingest_paths.rendered_images_ledger]),
            {image_one, image_two},
        )

    def test_producer_is_unknown_when_only_a_rebound_cumulative_receipt_is_valid(self) -> None:
        self.seed_prepare([self.source_row("S1")])
        candidate = "work/runs/test_run/research/source_candidates.primary.jsonl"
        candidate_path = self.root / candidate
        candidate_path.write_text('{"round":1}\n', encoding="utf-8")
        research_baseline = capture_ingest_receipt_baseline(self.context)
        self.write_ledgers([self.source_row("S1"), self.source_row("S2")], [])
        write_ingest_receipt(
            self.context,
            "research",
            baseline=research_baseline,
            input_paths=("inputs/sources.csv", candidate),
            written_source_ids=("S2",),
            written_image_paths=(),
        )
        source_ledger = self.ingest_paths.source_candidate_ledger
        digest = capture_ingest_receipt_baseline(self.context).before_rows[source_ledger]["S2"]
        self.assertEqual(
            receipt_row_producer(
                self.context,
                source_ledger,
                "S2",
                row_digest=digest,
            ),
            "research",
        )

        # Evidence captured the valid research lineage before merge and rebound it to the
        # new input. With no persistent origin field, the old now-invalid research envelope
        # makes producer attribution ambiguous even though the evidence receipt is valid.
        evidence_baseline = capture_ingest_receipt_baseline(self.context)
        candidate_path.write_text('{"round":2}\n', encoding="utf-8")
        write_ingest_receipt(
            self.context,
            "evidence",
            baseline=evidence_baseline,
            input_paths=("inputs/sources.csv", candidate),
            written_source_ids=(),
            written_image_paths=(),
        )
        self.assertIsNone(
            receipt_row_producer(
                self.context,
                source_ledger,
                "S2",
                row_digest=digest,
            )
        )

    def test_runner_mutation_rebases_only_a_successful_exact_change(self) -> None:
        primary = self.artifacts.primary_candidates
        primary_path = self.root / primary
        primary_path.write_text("old\n", encoding="utf-8")
        owned = tuple(
            contract.path
            for contract in phase_output_contracts("evidence", self.artifacts)
        )
        guard = PhaseMutationGuard.create(
            self.context,
            "evidence",
            owned_paths=owned,
        )

        result = guard.run_runner_mutation(
            "merge",
            lambda: (
                primary_path.write_text("merged\n", encoding="utf-8"),
                SimpleNamespace(errors=()),
            )[1],
            exact_files=(primary,),
            error_getter=lambda value: value.errors,
        )
        self.assertEqual(result.errors, ())
        self.assertEqual(guard.violations(), [])

        failed = guard.run_runner_mutation(
            "ingest",
            lambda: (
                primary_path.write_text("failed\n", encoding="utf-8"),
                SimpleNamespace(errors=("ingest failed",)),
            )[1],
            exact_files=(primary,),
            error_getter=lambda value: value.errors,
        )
        self.assertEqual(failed.errors, ("ingest failed",))
        self.assertTrue(guard.violations())

    def test_guard_covers_canonical_ledgers_and_their_derived_files(self) -> None:
        extracted = "work/runs/test_run/research/extracted_text/S1.txt"
        rendered = "work/runs/test_run/images/rendered/S1_page_001.png"
        for rel, content in ((extracted, b"text"), (rendered, b"image")):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.write_ledgers(
            [self.source_row("S1", extracted_text_path=extracted)],
            [self.image_row(rendered)],
        )
        owned = tuple(
            contract.path
            for contract in phase_output_contracts("evidence", self.artifacts)
        )
        guard = PhaseMutationGuard.create(
            self.context,
            "evidence",
            owned_paths=owned,
        )

        (self.root / extracted).write_text("changed", encoding="utf-8")
        (self.root / rendered).write_bytes(b"changed")
        self.write_ledgers(
            [self.source_row("S1", digest="changed", extracted_text_path=extracted)],
            [self.image_row(rendered)],
        )
        errors = "\n".join(guard.violations())
        self.assertIn(self.ingest_paths.source_candidate_ledger, errors)
        self.assertIn(extracted, errors)
        self.assertIn(rendered, errors)

    def test_scope_guard_covers_normalized_inputs_before_first_checkpoint(self) -> None:
        guard = PhaseMutationGuard.create(
            self.context,
            "scope",
            owned_paths=(self.artifacts.intent_contract,),
        )
        (self.root / "inputs/topic.md").write_text("changed\n", encoding="utf-8")
        self.assertIn("inputs/topic.md", "\n".join(guard.violations()))

    def test_runner_mutation_rejects_an_unexpected_run_file(self) -> None:
        owned = tuple(
            contract.path
            for contract in phase_output_contracts("evidence", self.artifacts)
        )
        guard = PhaseMutationGuard.create(
            self.context,
            "evidence",
            owned_paths=owned,
        )
        unexpected = self.root / self.artifacts.work_run_dir / "unexpected.txt"

        with self.assertRaises(RunnerMutationViolation) as raised:
            guard.run_runner_mutation(
                "merge",
                lambda: unexpected.write_text("bad\n", encoding="utf-8"),
                exact_files=(self.artifacts.primary_candidates,),
            )
        self.assertIn("outside its capability", str(raised.exception))

    def test_source_ingest_result_reports_only_invocation_written_keys(self) -> None:
        config = SourceIngestConfig(
            root=self.root,
            mode="industry",
            run_id="test_run",
            sources_path="inputs/sources.csv",
            work_run_dir=self.artifacts.work_run_dir,
            allow_downloads=False,
        )
        base = run_source_ingest(config)
        self.assertEqual(base.written_source_ids, ("S1",))
        self.assertEqual(base.written_image_paths, ())

        candidate = self.artifacts.primary_candidates
        (self.root / candidate).write_text(
            '{"candidate_id":"S2","title":"new","url":null,"local_path":null}\n',
            encoding="utf-8",
        )
        first = run_research_candidate_ingest(config, candidate_paths=(candidate,))
        self.assertEqual(first.errors, ())
        self.assertEqual(first.written_source_ids, ("S2",))

        repeated = run_research_candidate_ingest(config, candidate_paths=(candidate,))
        self.assertEqual(repeated.errors, ())
        self.assertEqual(repeated.written_source_ids, ())


if __name__ == "__main__":
    unittest.main()
