from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from reportmachine_runner import cli
from reportmachine_runner import phase_provenance
from reportmachine_runner import workflow
from reportmachine_runner.execution_control import (
    RECOVERY_REQUIRED_EXIT_CODE,
    read_owner_record,
)
from reportmachine_runner.intent_contract import (
    build_intent_contract,
    scope_delta_fingerprint,
    write_intent_contract,
)


@unittest.skipUnless(
    importlib.util.find_spec("jsonschema") is not None,
    "scope approval requires JSON Schema validation",
)
class ScopeApprovalControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.root = base / "checkout"
        self.root.mkdir()
        self.state = base / "state"
        schema_target = self.root / "schemas" / "intent_contract.schema.json"
        schema_target.parent.mkdir(parents=True)
        shutil.copy2(
            Path(__file__).resolve().parents[1]
            / "schemas"
            / "intent_contract.schema.json",
            schema_target,
        )
        self.run_id = "scope_cas"
        self.intent_path = (
            self.root
            / "work"
            / "runs"
            / self.run_id
            / "intake"
            / "intent_contract.json"
        )
        self.environment = patch(
            "reportmachine_runner.execution_control.default_state_root",
            return_value=self.state,
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def _payload(self, *, conflicting: bool = False) -> dict[str, object]:
        payload = build_intent_contract(
            run_id=self.run_id,
            mode="industry",
            original_request="원본 요청",
            primary_question="무엇이 바뀌었나?",
            analysis_modes=["industry_structure"],
            depth="standard",
            approved=False,
        )
        payload["scope_status"] = "conflicting" if conflicting else "approval_required"
        payload["scope_delta"] = [
            {
                "change": "reframed",
                "detail": "시장 반응을 산업 구조 변화로 재구성",
                "material": True,
            }
        ]
        return payload

    def _write(self, payload: dict[str, object]) -> None:
        write_intent_contract(
            self.root,
            f"work/runs/{self.run_id}/intake/intent_contract.json",
            payload,
        )

    def _run(self, fingerprint: str) -> int:
        with (
            patch.object(
                workflow,
                "build_run_configs",
                side_effect=AssertionError("scope-only built workflow configs"),
            ),
            patch.object(
                workflow,
                "_run_source_ingest",
                side_effect=AssertionError("scope-only ran source ingest"),
            ),
            patch.object(
                workflow,
                "_write_preflight_and_manifests",
                side_effect=AssertionError("scope-only ran preflight"),
            ),
            patch.object(
                workflow,
                "run_codex_command",
                side_effect=AssertionError("scope-only ran a model"),
            ),
        ):
            return cli.main(
                [
                    "--project-root",
                    str(self.root),
                    "--run-id",
                    self.run_id,
                    "--mode",
                    "industry",
                    "--intake-approve-scope-only",
                    "--intake-approve-scope-sha256",
                    fingerprint,
                ]
            )

    def _repo_snapshot(self) -> dict[str, bytes]:
        return {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def _seed_checkpoints(self) -> tuple[str, dict[str, bytes]]:
        intent_rel = f"work/runs/{self.run_id}/intake/intent_contract.json"
        intent_sha256 = sha256(self.intent_path.read_bytes()).hexdigest()
        originals: dict[str, bytes] = {}
        for phase in ("scope", "research-plan"):
            target = (
                self.root
                / "work"
                / "runs"
                / self.run_id
                / "provenance"
                / f"{phase}.json"
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(
                    {"artifact_hashes": {intent_rel: intent_sha256}},
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            originals[phase] = target.read_bytes()
        return intent_sha256, originals

    def test_current_fingerprint_approves_without_workflow_execution(self) -> None:
        payload = self._payload()
        self._write(payload)
        fingerprint = scope_delta_fingerprint(payload)

        self.assertEqual(self._run(fingerprint), 0)

        approved = json.loads(self.intent_path.read_text(encoding="utf-8"))
        self.assertEqual(approved["approval_status"], "approved")
        self.assertEqual(approved["approved_scope_delta_sha256"], fingerprint)
        self.assertIsNotNone(approved["approved_at"])
        self.assertFalse((self.root / "outputs").exists())

    def test_stale_fingerprint_changes_no_repository_file(self) -> None:
        payload = self._payload()
        self._write(payload)
        before = self._repo_snapshot()

        self.assertEqual(self._run("0" * 64), 2)

        self.assertEqual(self._repo_snapshot(), before)

    def test_conflicting_scope_cannot_be_approved(self) -> None:
        payload = self._payload(conflicting=True)
        self._write(payload)
        before = self.intent_path.read_bytes()

        self.assertEqual(self._run(scope_delta_fingerprint(payload)), 2)

        self.assertEqual(self.intent_path.read_bytes(), before)

    def test_same_current_approval_is_idempotent_without_replacement(self) -> None:
        payload = self._payload()
        self._write(payload)
        fingerprint = scope_delta_fingerprint(payload)
        self.assertEqual(self._run(fingerprint), 0)
        before = self.intent_path.stat()
        before_bytes = self.intent_path.read_bytes()

        self.assertEqual(self._run(fingerprint), 0)

        after = self.intent_path.stat()
        self.assertEqual(self.intent_path.read_bytes(), before_bytes)
        self.assertEqual(after.st_ino, before.st_ino)
        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)

    def test_checkpoint_write_failure_rolls_back_exactly_and_retry_converges(self) -> None:
        payload = self._payload()
        self._write(payload)
        fingerprint = scope_delta_fingerprint(payload)
        old_sha256, checkpoint_bytes = self._seed_checkpoints()
        contract_bytes = self.intent_path.read_bytes()
        real_replace = phase_provenance._replace_checkpoint_bytes
        failed = False

        def fail_second_checkpoint(target: Path, data: bytes) -> None:
            nonlocal failed
            if target.name == "research-plan.json" and not failed:
                failed = True
                raise OSError("injected second checkpoint write failure")
            real_replace(target, data)

        with patch.object(
            phase_provenance,
            "_replace_checkpoint_bytes",
            side_effect=fail_second_checkpoint,
        ):
            self.assertEqual(self._run(fingerprint), 2)

        self.assertEqual(self.intent_path.read_bytes(), contract_bytes)
        for phase, expected in checkpoint_bytes.items():
            target = (
                self.root
                / "work"
                / "runs"
                / self.run_id
                / "provenance"
                / f"{phase}.json"
            )
            self.assertEqual(target.read_bytes(), expected)
        self.assertIsNone(read_owner_record(self.root))

        self.assertEqual(self._run(fingerprint), 0)
        approved_sha256 = sha256(self.intent_path.read_bytes()).hexdigest()
        self.assertNotEqual(approved_sha256, old_sha256)
        for phase in ("scope", "research-plan"):
            checkpoint = json.loads(
                (
                    self.root
                    / "work"
                    / "runs"
                    / self.run_id
                    / "provenance"
                    / f"{phase}.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                checkpoint["artifact_hashes"][
                    f"work/runs/{self.run_id}/intake/intent_contract.json"
                ],
                approved_sha256,
            )

    def test_already_approved_mixed_checkpoint_state_fails_without_laundering(self) -> None:
        payload = self._payload()
        self._write(payload)
        fingerprint = scope_delta_fingerprint(payload)
        old_sha256, _checkpoint_bytes = self._seed_checkpoints()
        self.assertEqual(self._run(fingerprint), 0)

        research = (
            self.root
            / "work"
            / "runs"
            / self.run_id
            / "provenance"
            / "research-plan.json"
        )
        mixed = json.loads(research.read_text(encoding="utf-8"))
        mixed["artifact_hashes"][
            f"work/runs/{self.run_id}/intake/intent_contract.json"
        ] = old_sha256
        research.write_text(
            json.dumps(mixed, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        before = self._repo_snapshot()

        self.assertEqual(self._run(fingerprint), 2)
        self.assertEqual(self._repo_snapshot(), before)

    def test_checkpoint_rollback_failure_retains_explicit_recovery_fence(self) -> None:
        payload = self._payload()
        self._write(payload)
        fingerprint = scope_delta_fingerprint(payload)
        self._seed_checkpoints()
        real_replace = phase_provenance._replace_checkpoint_bytes
        call_count = 0

        def fail_update_and_rollback(target: Path, data: bytes) -> None:
            nonlocal call_count
            call_count += 1
            if call_count in {2, 3}:
                raise OSError("injected update/rollback failure")
            real_replace(target, data)

        with patch.object(
            phase_provenance,
            "_replace_checkpoint_bytes",
            side_effect=fail_update_and_rollback,
        ):
            self.assertEqual(
                self._run(fingerprint),
                RECOVERY_REQUIRED_EXIT_CODE,
            )

        owner = read_owner_record(self.root)
        self.assertIsNotNone(owner)
        assert owner is not None
        self.assertEqual(owner["state"], "recovery_required")
        self.assertEqual(owner["exit_code"], RECOVERY_REQUIRED_EXIT_CODE)

    def test_wrong_run_identity_changes_nothing(self) -> None:
        payload = self._payload()
        payload["run_id"] = "another_run"
        self._write(payload)
        before = self.intent_path.read_bytes()

        self.assertEqual(self._run(scope_delta_fingerprint(payload)), 2)

        self.assertEqual(self.intent_path.read_bytes(), before)

    def test_invalid_contract_is_rejected_before_cas(self) -> None:
        payload = self._payload()
        payload["approval_status"] = "not-a-schema-value"
        self._write(payload)
        before = self.intent_path.read_bytes()

        self.assertEqual(self._run(scope_delta_fingerprint(payload)), 2)

        self.assertEqual(self.intent_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
