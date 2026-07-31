from __future__ import annotations

import json
from hashlib import sha256
import os
import signal
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reportmachine_runner.codex_exec import (
    LATEST_OPTIONAL_OUTPUTS,
    LATEST_REQUIRED_OUTPUTS,
)
from reportmachine_runner.final_sync import run_final_sync_audit, write_final_sync_report
from reportmachine_runner.run_outputs import IMAGE_LEDGER_HEADER
from reportmachine_runner.execution_control import SIGTERM_EXIT_CODE, run_with_execution_control
from reportmachine_runner.web.artifacts import ArtifactBroker, ArtifactError, ArtifactName
from reportmachine_runner.web.state import ExecutionReceipt, OperationState, utc_now
from reportmachine_runner.web.status import StatusReader
from reportmachine_runner.web.supervisor import OwnerStateAdapter
from tests.cli_harness import PIPELINE_SCHEMAS, CliHarness


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _ReceiptReader:
    def __init__(self, receipt: ExecutionReceipt | None) -> None:
        self.receipt = receipt

    def current_receipt(self) -> ExecutionReceipt | None:
        return self.receipt


class _ChangingReceiptReader:
    def __init__(self, first: ExecutionReceipt | None, second: ExecutionReceipt | None) -> None:
        self.values = [first, second]

    def current_receipt(self) -> ExecutionReceipt | None:
        if len(self.values) > 1:
            return self.values.pop(0)
        return self.values[0]


class _UnlockedOwner:
    def read_safely(self) -> tuple[None, None]:
        return None, None


class ArtifactBrokerTests(CliHarness):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.run = self.root / "outputs" / "safe_run"
        self.run.mkdir(parents=True)
        schema_dir = self.root / "schemas"
        schema_dir.mkdir()
        for name in (
            "article_result.schema.json",
            "preflight_result.schema.json",
            "final_sync_report.schema.json",
            "phase_provenance.schema.json",
            "intent_contract.schema.json",
        ):
            shutil.copy2(PROJECT_ROOT / "schemas" / name, schema_dir / name)
        self.broker = ArtifactBroker(self.root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_reads_only_closed_logical_artifact_with_safe_metadata(self) -> None:
        (self.run / "article.md").write_bytes(b"# article\n")

        payload = self.broker.read("safe_run", ArtifactName.ARTICLE)

        self.assertEqual(payload.data, b"# article\n")
        self.assertEqual(payload.filename, "article.md")
        self.assertEqual(payload.media_type, "text/markdown; charset=utf-8")
        self.assertEqual(payload.content_disposition, 'attachment; filename="article.md"')
        self.assertEqual(payload.metadata()["artifact"], "article")

    def test_rejects_unknown_logical_artifact_and_invalid_run_id(self) -> None:
        with self.assertRaises(ArtifactError):
            self.broker.read("safe_run", "../../secret")
        with self.assertRaises(ArtifactError):
            self.broker.read("../safe_run", ArtifactName.ARTICLE)
        with self.assertRaises(ArtifactError):
            self.broker.read("latest", ArtifactName.ARTICLE)

    def test_rejects_symlink_parent_and_leaf(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "article.md").write_text("outside", encoding="utf-8")
        (self.root / "outputs").unlink() if (self.root / "outputs").is_symlink() else None
        # A run directory link must not be followed.
        (self.root / "outputs" / "linked_run").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ArtifactError, "symlink"):
            self.broker.read("linked_run", ArtifactName.ARTICLE)

        leaf = self.run / "article.md"
        leaf.symlink_to(outside / "article.md")
        with self.assertRaisesRegex(ArtifactError, "symlink"):
            self.broker.read("safe_run", ArtifactName.ARTICLE)

    def test_rejects_nonregular_file(self) -> None:
        target = self.run / "article.md"
        target.mkdir()
        with self.assertRaisesRegex(ArtifactError, "regular"):
            self.broker.read("safe_run", ArtifactName.ARTICLE)

    def test_latest_membership_requires_every_mirrored_file_to_match_current_run(self) -> None:
        names = {
            "article_result": "article_result.json",
            "article": "article.md",
            "source_ledger": "source_ledger.csv",
            "image_ledger": "image_ledger.csv",
            "claim_ledger": "claim_ledger.json",
            "preflight_result": "preflight_result.json",
            "run_manifest": "run_manifest.json",
            "earnings_delta_matrix": "earnings_delta_matrix.json",
            "earnings_delta_matrix_md": "earnings_delta_matrix.md",
        }
        required_keys = tuple(key for key, _field in LATEST_REQUIRED_OUTPUTS)
        optional_keys = tuple(key for key, _field in LATEST_OPTIONAL_OUTPUTS)
        paths = {
            key: f"outputs/safe_run/{names[key]}"
            for key in (*required_keys, *optional_keys)
        }
        for key in required_keys:
            if key == "run_manifest":
                continue
            (self.run / names[key]).write_bytes(f"{key}\n".encode("utf-8"))
        self.write_json(
            self.run / "run_manifest.json",
            {"run_id": "safe_run", "paths": paths},
        )
        latest = self.root / "outputs" / "latest"
        latest.mkdir()
        shutil.copy2(self.run / "run_manifest.json", latest / "run_manifest.json")
        reader = StatusReader(
            self.root,
            owner_adapter=_UnlockedOwner(),
            broker=self.broker,
        )

        self.assertFalse(reader._latest_membership("safe_run")["member"])
        for key in required_keys:
            shutil.copy2(self.run / names[key], latest / names[key])
        self.assertTrue(reader._latest_membership("safe_run")["member"])

        (latest / "article_result.json").unlink()
        self.assertFalse(reader._latest_membership("safe_run")["member"])
        shutil.copy2(self.run / "article_result.json", latest / "article_result.json")
        (latest / "article.md").write_text("stale\n", encoding="utf-8")
        self.assertFalse(reader._latest_membership("safe_run")["member"])

    def test_status_never_uses_existing_artifacts_while_operation_is_active(self) -> None:
        (self.run / "article.md").write_text("# stale article\n", encoding="utf-8")
        now = utc_now()
        receipt = ExecutionReceipt(
            execution_id="01234567-89ab-4def-8123-456789abcdef",
            idempotency_key="request:0001",
            payload_digest="a" * 64,
            run_id="safe_run",
            action="run_all",
            state=OperationState.RUNNING,
            created_at=now,
            updated_at=now,
            pid=123,
            started_at=now,
        )

        with patch(
            "reportmachine_runner.execution_control._open_lock_file",
            side_effect=AssertionError("status reader attempted to preempt the writer lock"),
        ):
            status = StatusReader(
                self.root,
                supervisor=_ReceiptReader(receipt),
                owner_adapter=_UnlockedOwner(),
                broker=self.broker,
            ).read("safe_run")

        self.assertEqual(status.operation["state"], "running")
        self.assertEqual(status.article["state"], "not_checked")
        self.assertEqual(status.latest["member"], None)

    def test_preflight_fail_is_not_valid_article_even_when_result_hash_matches(self) -> None:
        article = b"# article\n"
        digest = sha256(article).hexdigest()
        (self.run / "article.md").write_bytes(article)
        (self.run / "article_result.json").write_text(
            json.dumps(
                {
                    "article_type": "industry",
                    "article_sha256": digest,
                    "title": "Preflight failed",
                    "subtitle": "Required materials are unavailable.",
                    "one_sentence_insight": "",
                    "scorecard": None,
                    "output_files": {
                        "article_md": "outputs/safe_run/article.md",
                        "source_ledger_csv": "outputs/safe_run/source_ledger.csv",
                        "image_ledger_csv": "outputs/safe_run/image_ledger.csv",
                        "claim_ledger_json": "outputs/safe_run/claim_ledger.json",
                        "preflight_result_json": "outputs/safe_run/preflight_result.json",
                        "run_manifest_json": "outputs/safe_run/run_manifest.json",
                    },
                    "critical_warnings": ["required source missing"],
                    "checklist_status": "fail",
                }
            ),
            encoding="utf-8",
        )
        (self.run / "preflight_result.json").write_text(
            json.dumps(
                {
                    "status": "fail",
                    "article_type": "industry",
                    "run_id": "safe_run",
                    "required_sources": {"official": "fail"},
                    "warnings": [],
                    "blocking_errors": ["required source missing"],
                }
            ),
            encoding="utf-8",
        )

        now = utc_now()
        receipt = ExecutionReceipt(
            execution_id="01234567-89ab-4def-8123-456789abcdef",
            idempotency_key="request:0001",
            payload_digest="a" * 64,
            run_id="safe_run",
            action="run_all",
            state=OperationState.EXITED,
            created_at=now,
            updated_at=now,
            started_at=now,
            finished_at=now,
            exit_code=0,
            artifact_baseline={"_observation": "stable"},
            artifact_final={"_observation": "stable"},
        )

        status = StatusReader(
            self.root,
            supervisor=_ReceiptReader(receipt),
            owner_adapter=_UnlockedOwner(),
            broker=self.broker,
        ).read("safe_run")

        self.assertEqual(status.operation["exit_code"], 0)
        self.assertEqual(
            status.operation["process_outcome"],
            "exit_zero_requires_artifact_validation",
        )
        self.assertEqual(status.article["state"], "invalid")
        self.assertFalse(status.article["valid"])
        self.assertEqual(status.artifacts["preflight_status"], "fail")
        self.assertEqual(status.artifacts["schema_validation"]["preflight"], [])
        self.assertEqual(status.artifacts["schema_validation"]["article_result"], [])

    def test_status_separates_exit_zero_from_fresh_artifact_observation(self) -> None:
        now = utc_now()
        receipt = ExecutionReceipt(
            execution_id="01234567-89ab-4def-8123-456789abcdef",
            idempotency_key="request:0001",
            payload_digest="a" * 64,
            run_id="safe_run",
            action="run_all",
            state=OperationState.EXITED,
            created_at=now,
            updated_at=now,
            started_at=now,
            finished_at=now,
            exit_code=0,
            artifact_baseline={
                "_observation": "stable",
                "article": "missing",
                "preflight": "old",
            },
            artifact_final={
                "_observation": "stable",
                "article": "new",
                "preflight": "new",
            },
        )

        status = StatusReader(
            self.root,
            supervisor=_ReceiptReader(receipt),
            owner_adapter=_UnlockedOwner(),
            broker=self.broker,
        ).read("safe_run")

        self.assertEqual(
            status.operation["process_outcome"],
            "exit_zero_requires_artifact_validation",
        )
        self.assertEqual(
            status.operation["changed_artifacts_since_start"],
            ["article", "preflight"],
        )
        self.assertTrue(
            status.article["operation_relationship"][
                "article_evidence_changed_since_start"
            ]
        )
        self.assertNotEqual(status.article["state"], "valid")

    def test_valid_status_rechecks_full_finalize_provenance_and_final_sync_hashes(self) -> None:
        self.copy_schema(self.root, "schemas/claim_ledger.schema.json")
        for rel in PIPELINE_SCHEMAS:
            self.copy_schema(self.root, rel)
        self.seed_pipeline_artifacts(
            self.root,
            run_id="safe_run",
            include_images=False,
        )
        self.write_valid_article_outputs(self.root, run_id="safe_run")
        (self.run / "image_ledger.csv").write_text(
            ",".join(IMAGE_LEDGER_HEADER) + "\n",
            encoding="utf-8",
        )
        self.write_json(
            self.run / "preflight_result.json",
            {
                "status": "pass",
                "article_type": "industry",
                "run_id": "safe_run",
                "required_sources": {"official": "pass"},
                "warnings": [],
                "blocking_errors": [],
            },
        )
        self.write_json(
            self.run / "run_manifest.json",
            {
                "version": 1,
                "run_id": "safe_run",
                "article_type": "industry",
                "phase": "all",
                "paths": {
                    "topic": "inputs/topic.md",
                    "sources": "inputs/sources.csv",
                    "work_run_dir": "work/runs/safe_run",
                    "article_result": "outputs/safe_run/article_result.json",
                    "article": "outputs/safe_run/article.md",
                    "source_ledger": "outputs/safe_run/source_ledger.csv",
                    "image_ledger": "outputs/safe_run/image_ledger.csv",
                    "claim_ledger": "outputs/safe_run/claim_ledger.json",
                    "run_manifest": "outputs/safe_run/run_manifest.json",
                },
            },
        )
        self.seed_provenance_checkpoints(
            self.root,
            run_id="safe_run",
            include_images=False,
            through="finalize",
        )
        provenance = self.provenance_context(
            self.root,
            run_id="safe_run",
            include_images=False,
        )
        sync = run_final_sync_audit(
            root=self.root,
            run_id="safe_run",
            artifacts=provenance.artifacts,
            require_evidence_cards=True,
        )
        self.assertEqual(sync.errors, [])
        self.assertEqual(
            sync.payload["artifact_hashes"]["article_result_json"],
            sha256((self.run / "article_result.json").read_bytes()).hexdigest(),
        )
        write_final_sync_report(
            self.root,
            provenance.artifacts.final_sync_report,
            sync.payload,
        )
        self.write_json(
            self.root / "logs" / "safe_run" / "run_summary.json",
            {
                "run_id": "safe_run",
                "status": "completed",
                "phase": "all",
                "article_result": "outputs/safe_run/article_result.json",
                "preflight_status": "pass",
            },
        )

        reader = StatusReader(
            self.root,
            owner_adapter=_UnlockedOwner(),
            broker=self.broker,
        )
        status = reader.read("safe_run")
        self.assertEqual(status.article["state"], "valid")
        self.assertTrue(
            status.artifacts["finalize_provenance"]["current_checkpoint_valid"]
        )
        self.assertTrue(status.artifacts["final_sync_artifact_hashes_match"])

        article_result = self.run / "article_result.json"
        original_result = article_result.read_bytes()
        article_result.write_bytes(original_result + b"\n")
        result_tampered = reader.read("safe_run")
        self.assertFalse(
            result_tampered.artifacts["final_sync_artifact_hashes_match"]
        )
        article_result.write_bytes(original_result)

        with (self.run / "source_ledger.csv").open("a", encoding="utf-8") as handle:
            handle.write("tampered,https://example.invalid,,,blocked,,changed after audit\n")
        tampered = reader.read("safe_run")
        self.assertEqual(tampered.article["state"], "invalid")
        self.assertFalse(
            tampered.artifacts["finalize_provenance"]["current_checkpoint_valid"]
        )
        self.assertFalse(tampered.artifacts["final_sync_artifact_hashes_match"])

    def test_recovery_fence_allows_stable_read_but_remains_visible(self) -> None:
        state_root = self.root / "external-state"
        def interrupt() -> int:
            os.kill(os.getpid(), signal.SIGTERM)
            return 99

        self.assertEqual(
            run_with_execution_control(
                self.root,
                interrupt,
                operation="article",
                run_id="safe_run",
                state_dir=state_root,
            ),
            SIGTERM_EXIT_CODE,
        )
        now = utc_now()
        receipt = ExecutionReceipt(
            execution_id="01234567-89ab-4def-8123-456789abcdef",
            idempotency_key="request:0001",
            payload_digest="a" * 64,
            run_id="safe_run",
            action="run_all",
            state=OperationState.RECOVERY_REQUIRED,
            created_at=now,
            updated_at=now,
            finished_at=now,
            exit_code=143,
        )
        status = StatusReader(
            self.root,
            supervisor=_ReceiptReader(receipt),
            owner_adapter=OwnerStateAdapter(self.root, state_dir=state_root),
            broker=self.broker,
        ).read("safe_run")

        self.assertTrue(status.writer["stable_for_inspection"])
        self.assertEqual(status.writer["state"], "recovery_required")
        self.assertNotEqual(status.article["state"], "not_checked")

    def test_status_discards_snapshot_when_operation_is_queued_mid_read(self) -> None:
        (self.run / "article.md").write_text("# old article\n", encoding="utf-8")
        now = utc_now()
        queued = ExecutionReceipt(
            execution_id="01234567-89ab-4def-8123-456789abcdef",
            idempotency_key="request:0001",
            payload_digest="a" * 64,
            run_id="safe_run",
            action="run_all",
            state=OperationState.QUEUED,
            created_at=now,
            updated_at=now,
        )
        status = StatusReader(
            self.root,
            supervisor=_ChangingReceiptReader(None, queued),
            owner_adapter=_UnlockedOwner(),
            broker=self.broker,
        ).read("safe_run")

        self.assertEqual(status.operation["state"], "queued")
        self.assertEqual(status.article["state"], "not_checked")


if __name__ == "__main__":
    unittest.main()
