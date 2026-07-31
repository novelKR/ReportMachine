from __future__ import annotations

import asyncio
import inspect
import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import Mock, patch

import reportmachine_runner.codex_exec as codex_exec_module
from reportmachine_runner.artifacts import ArtifactSyncConfig
from reportmachine_runner.codex_exec import (
    CodexExecConfig,
    RoleCommand,
    RoleProcessCleanupError,
    make_role_write_snapshot,
    prepare_role_invocation,
    run_command,
    validate_runner_side_effects,
    validate_role_write_snapshot,
)
from reportmachine_runner.phase_contracts import RunArtifacts, build_run_artifacts
from reportmachine_runner.phases import PhaseRunner
from reportmachine_runner.source_ingest_paths import build_source_ingest_paths


@dataclass(frozen=True)
class BoundaryFixture:
    root: Path
    artifacts: RunArtifacts
    config: CodexExecConfig
    run_contract: dict[str, object]

    def runner(self, callback, *, image_inputs: tuple[str, ...] = ()) -> PhaseRunner:
        return PhaseRunner(
            root=self.root,
            run_id="test_run",
            mode="industry",
            artifacts=self.artifacts,
            exec_config=replace(self.config, image_inputs=image_inputs),
            run_contract=self.run_contract,
            run_codex=callback,
        )


def make_fixture(root: Path) -> BoundaryFixture:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "README.md").write_text("protected\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)

    work = "work/runs/test_run"
    outputs = "outputs/test_run"
    artifacts = build_run_artifacts(
        work_run_dir=work,
        outputs_dir=outputs,
        article=f"{outputs}/article.md",
        source_ledger=f"{outputs}/source_ledger.csv",
        image_ledger=f"{outputs}/image_ledger.csv",
        claim_ledger=f"{outputs}/claim_ledger.json",
        article_result=f"{outputs}/article_result.json",
    )
    ingest = build_source_ingest_paths(work)
    (root / "inputs").mkdir()
    (root / "inputs/topic.md").write_text("topic\n", encoding="utf-8")
    (root / "inputs/sources.csv").write_text("source\n", encoding="utf-8")
    original = root / artifacts.original_request
    original.parent.mkdir(parents=True)
    original.write_text("request\n", encoding="utf-8")

    config = CodexExecConfig(
        root=root,
        codex_bin="codex",
        run_id="test_run",
        phase="scope",
        schema="schemas/article_result.schema.json",
        output=artifacts.article_result,
        article_output=artifacts.article,
        source_ledger_output=artifacts.source_ledger,
        image_ledger_output=artifacts.image_ledger,
        claim_ledger_output=artifacts.claim_ledger,
        preflight_result_output=f"{outputs}/preflight_result.json",
        run_manifest_output=f"{outputs}/run_manifest.json",
        earnings_delta_matrix_output=f"{outputs}/earnings_delta_matrix.json",
        earnings_delta_matrix_md=f"{outputs}/earnings_delta_matrix.md",
        work_run_dir=work,
        draft_output=artifacts.draft_article,
        source_ingest_manifest=ingest.manifest,
        source_candidate_ledger=ingest.source_candidate_ledger,
        pdf_index=ingest.pdf_index,
        rendered_images_ledger=ingest.rendered_images_ledger,
        latest_dir="outputs/latest",
        web_search="disabled",
        allow_shell_network=False,
        image_inputs=(),
        structured_output=False,
        json_events=False,
        events_log="logs/test_run/events.jsonl",
        stderr_log="logs/test_run/stderr.log",
        artifact_sync=ArtifactSyncConfig(),
        prompt="",
    )
    return BoundaryFixture(
        root=root,
        artifacts=artifacts,
        config=config,
        run_contract={
            "run_id": "test_run",
            "work_run_dir": work,
            "topic_path": "inputs/topic.md",
            "sources_path": "inputs/sources.csv",
            "rendered_images_ledger": ingest.rendered_images_ledger,
        },
    )


class RoleWriteBoundaryTests(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        return temporary, make_fixture(root)

    @staticmethod
    def invoke_scope(fixture: BoundaryFixture, callback):
        return fixture.runner(callback).invoke_role(
            role="scope_guardian",
            phase="scope",
            task_instructions="write the declared contract",
            input_artifacts=[fixture.artifacts.original_request],
            output_artifacts=[fixture.artifacts.intent_contract],
        )

    @staticmethod
    def attempt_payload(fixture: BoundaryFixture, role: str = "scope_guardian"):
        attempts = sorted(
            (fixture.root / fixture.artifacts.agents_dir / role).rglob("attempt_*.json")
        )
        assert attempts
        return json.loads(attempts[-1].read_text(encoding="utf-8"))

    @staticmethod
    def descendant_command(root: Path, *, exit_code: int | None) -> tuple[RoleCommand, Path, Path]:
        child_pid = root / "descendant.pid"
        late_write = root / "descendant-late.txt"
        child_script = "\n".join(
            [
                "import os, signal, time",
                "from pathlib import Path",
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
                f"Path({str(child_pid)!r}).write_text(str(os.getpid()), encoding='utf-8')",
                "time.sleep(30)",
                f"Path({str(late_write)!r}).write_text('late', encoding='utf-8')",
            ]
        )
        tail = "time.sleep(30)" if exit_code is None else f"raise SystemExit({exit_code})"
        parent_script = "\n".join(
            [
                "import subprocess, sys, time",
                "from pathlib import Path",
                f"pid_path = Path({str(child_pid)!r})",
                f"subprocess.Popen([{sys.executable!r}, '-c', {child_script!r}])",
                "deadline = time.monotonic() + 5",
                "while not pid_path.exists() and time.monotonic() < deadline:",
                "    time.sleep(0.01)",
                "if not pid_path.exists():",
                "    raise SystemExit(98)",
                tail,
            ]
        )
        return RoleCommand([sys.executable, "-c", parent_script]), child_pid, late_write

    def test_exact_output_parent_is_runner_created_and_sibling_write_fails(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        output = fixture.root / fixture.artifacts.intent_contract
        sibling = output.with_name("undeclared.json")

        def callback(_config, _command) -> int:
            self.assertTrue(output.parent.is_dir())
            output.write_text("{}\n", encoding="utf-8")
            sibling.write_text("not allowed\n", encoding="utf-8")
            return 0

        outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(outcome.status, 2)
        self.assertIn("write_scope_violation", outcome.label)
        self.assertIn("outside exact output allowlist", "\n".join(outcome.messages))
        self.assertTrue(sibling.is_file(), "upstream/undeclared writes are detected, not rolled back")
        attempt = self.attempt_payload(fixture)
        self.assertEqual(attempt["status"], "failed")
        self.assertTrue(any("undeclared.json" in error for error in attempt["errors"]))

    def test_git_metadata_is_outside_the_role_allowlist(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        output = fixture.root / fixture.artifacts.intent_contract
        git_config = fixture.root / ".git/config"

        def callback(_config, _command) -> int:
            output.write_text("{}\n", encoding="utf-8")
            git_config.write_text("[tampered]\n", encoding="utf-8")
            return 0

        outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(outcome.status, 2)
        self.assertIn(".git/config", "\n".join(outcome.messages))

    def test_read_only_git_status_does_not_refresh_the_protected_index(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        config = replace(
            fixture.config,
            role="scope_guardian",
            role_output_artifacts=(fixture.artifacts.intent_contract,),
        )
        self.assertEqual(prepare_role_invocation(config), [])
        snapshot = make_role_write_snapshot(config)

        status = run_command(
            ["git", "status", "--porcelain"],
            cwd=fixture.root,
        )

        self.assertEqual(status, 0)
        self.assertEqual(validate_role_write_snapshot(snapshot), [])

    def test_same_bytes_atomic_replacement_of_protected_file_is_detected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        output = fixture.root / fixture.artifacts.intent_contract
        protected = fixture.root / "README.md"
        original = protected.stat()

        def callback(_config, _command) -> int:
            output.write_text("{}\n", encoding="utf-8")
            replacement = fixture.root / "README.replacement"
            replacement.write_bytes(protected.read_bytes())
            os.chmod(replacement, original.st_mode)
            os.utime(
                replacement,
                ns=(original.st_atime_ns, original.st_mtime_ns),
            )
            os.replace(replacement, protected)
            return 0

        outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(outcome.status, 2)
        self.assertIn("README.md", "\n".join(outcome.messages))

    def test_role_created_attempt_symlink_is_atomically_replaced_without_touching_latest(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        latest = fixture.root / "outputs/latest"
        latest.mkdir(parents=True)
        latest_article = latest / "article.md"
        latest_article.write_text("verified\n", encoding="utf-8")
        output = fixture.root / fixture.artifacts.intent_contract
        attempt = (
            fixture.root
            / fixture.artifacts.agents_dir
            / "scope_guardian"
            / "attempt_001.json"
        )

        def callback(_config, _command) -> int:
            output.write_text("{}\n", encoding="utf-8")
            attempt.unlink()
            attempt.symlink_to(latest_article)
            return 0

        outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(outcome.status, 2)
        self.assertEqual(latest_article.read_text(encoding="utf-8"), "verified\n")
        self.assertTrue(attempt.is_file())
        self.assertFalse(attempt.is_symlink())
        payload = json.loads(attempt.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "failed")
        self.assertTrue(any("attempt_001.json" in error for error in payload["errors"]))

    def test_runner_log_symlink_is_rejected_post_call(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        fixture = replace(fixture, config=replace(fixture.config, json_events=True))
        output = fixture.root / fixture.artifacts.intent_contract
        events = fixture.root / fixture.config.events_log

        def callback(_config, _command) -> int:
            output.write_text("{}\n", encoding="utf-8")
            events.unlink()
            events.symlink_to(fixture.root / "README.md")
            return 0

        outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(outcome.status, 2)
        self.assertIn("runner log file must be a regular file", "\n".join(outcome.messages))
        self.assertEqual(self.attempt_payload(fixture)["status"], "failed")

    def test_runner_log_symlink_is_rejected_before_any_open(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        config = replace(
            fixture.config,
            role="scope_guardian",
            json_events=True,
            role_output_artifacts=(fixture.artifacts.intent_contract,),
        )
        self.assertEqual(prepare_role_invocation(config), [])
        snapshot = make_role_write_snapshot(config)
        events_rel = config.events_log
        events = fixture.root / events_rel
        events.unlink()
        events.symlink_to(fixture.root / "README.md")
        snapshot = replace(
            snapshot,
            runner_logs={events_rel: snapshot.runner_logs[events_rel]},
        )

        with patch(
            "reportmachine_runner.codex_exec.os.open",
            side_effect=AssertionError("symlink path must not be opened"),
        ) as open_file:
            errors = validate_runner_side_effects(snapshot)

        open_file.assert_not_called()
        self.assertIn("must be a regular file", "\n".join(errors))

    def test_runner_log_fifo_is_rejected_without_blocking_latest_or_attempt_cleanup(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        fixture = replace(fixture, config=replace(fixture.config, json_events=True))
        latest = fixture.root / "outputs/latest"
        latest.mkdir(parents=True)
        latest_article = latest / "article.md"
        latest_article.write_text("verified\n", encoding="utf-8")
        output = fixture.root / fixture.artifacts.intent_contract
        events = fixture.root / fixture.config.events_log

        def callback(_config, _command) -> int:
            output.write_text("{}\n", encoding="utf-8")
            events.unlink()
            os.mkfifo(events)
            latest_article.write_text("tampered\n", encoding="utf-8")
            return 0

        outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(outcome.status, 2)
        self.assertIn("runner log file must be a regular file", "\n".join(outcome.messages))
        self.assertEqual(latest_article.read_text(encoding="utf-8"), "verified\n")
        self.assertEqual(self.attempt_payload(fixture)["status"], "failed")

    def test_enabled_runner_logs_cannot_be_created_then_deleted(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        fixture = replace(fixture, config=replace(fixture.config, json_events=True))
        output = fixture.root / fixture.artifacts.intent_contract
        log_paths = [
            fixture.root / fixture.config.events_log,
            fixture.root / fixture.config.stderr_log,
        ]

        def callback(_config, _command) -> int:
            output.write_text("{}\n", encoding="utf-8")
            for path in log_paths:
                path.write_text("runner output\n", encoding="utf-8")
                path.unlink()
            return 0

        outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(outcome.status, 2)
        self.assertIn("runner log validation failed", "\n".join(outcome.messages))

    def test_runner_logs_preserve_identity_and_allow_append(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        fixture = replace(fixture, config=replace(fixture.config, json_events=True))
        output = fixture.root / fixture.artifacts.intent_contract
        log_paths = [
            fixture.root / fixture.config.events_log,
            fixture.root / fixture.config.stderr_log,
        ]
        for path in log_paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"before\x00")
        identities = [(path.stat().st_dev, path.stat().st_ino) for path in log_paths]

        def callback(_config, _command) -> int:
            output.write_text("{}\n", encoding="utf-8")
            for path in log_paths:
                with path.open("ab") as handle:
                    handle.write(b"after\n")
            return 0

        outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(outcome.status, 0, outcome.messages)
        for path, identity in zip(log_paths, identities):
            self.assertEqual((path.stat().st_dev, path.stat().st_ino), identity)
            self.assertEqual(path.read_bytes(), b"before\x00after\n")

    def test_runner_log_truncate_and_rewrite_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        fixture = replace(fixture, config=replace(fixture.config, json_events=True))
        output = fixture.root / fixture.artifacts.intent_contract
        events = fixture.root / fixture.config.events_log
        stderr = fixture.root / fixture.config.stderr_log
        for path in (events, stderr):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"before\n")

        def callback(_config, _command) -> int:
            output.write_text("{}\n", encoding="utf-8")
            events.write_bytes(b"rewritten\n")
            with stderr.open("ab") as handle:
                handle.write(b"after\n")
            return 0

        outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(outcome.status, 2)
        self.assertIn("did not preserve its pre-call byte prefix", "\n".join(outcome.messages))

    def test_runner_log_inode_replacement_is_rejected_even_with_valid_prefix(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        fixture = replace(fixture, config=replace(fixture.config, json_events=True))
        output = fixture.root / fixture.artifacts.intent_contract
        events = fixture.root / fixture.config.events_log
        stderr = fixture.root / fixture.config.stderr_log
        for path in (events, stderr):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"before\n")

        def callback(_config, _command) -> int:
            output.write_text("{}\n", encoding="utf-8")
            replacement = events.with_name("events.replacement")
            replacement.write_bytes(b"before\nafter\n")
            os.replace(replacement, events)
            with stderr.open("ab") as handle:
                handle.write(b"after\n")
            return 0

        outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(outcome.status, 2)
        self.assertIn("identity changed", "\n".join(outcome.messages))

    def test_runner_log_hardlink_to_declared_output_is_rejected_post_call(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        fixture = replace(fixture, config=replace(fixture.config, json_events=True))
        output = fixture.root / fixture.artifacts.intent_contract
        events = fixture.root / fixture.config.events_log

        def callback(_config, _command) -> int:
            os.link(events, output)
            with output.open("ab") as handle:
                handle.write(b"role output\n")
            return 0

        outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(outcome.status, 2)
        self.assertIn("write_scope_violation", outcome.label)
        self.assertIn("exactly one hard link", "\n".join(outcome.messages))
        self.assertEqual(self.attempt_payload(fixture)["status"], "failed")

    def test_preexisting_runner_log_hardlink_fails_before_callback(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        fixture = replace(fixture, config=replace(fixture.config, json_events=True))
        output = fixture.root / fixture.artifacts.intent_contract
        events = fixture.root / fixture.config.events_log
        events.parent.mkdir(parents=True, exist_ok=True)
        events.write_bytes(b"")
        os.link(events, output)
        called = False

        def callback(_config, _command) -> int:
            nonlocal called
            called = True
            return 0

        outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(outcome.status, 2)
        self.assertIn("boundary_preflight_fail", outcome.label)
        self.assertFalse(called)
        self.assertIn("exactly one hard link", "\n".join(outcome.messages))
        self.assertEqual(self.attempt_payload(fixture)["status"], "failed")

    def test_attempt_record_failure_is_hard_status_two(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        output = fixture.root / fixture.artifacts.intent_contract

        def callback(_config, _command) -> int:
            output.write_text("{}\n", encoding="utf-8")
            return 0

        with patch(
            "reportmachine_runner.phases.finish_attempt",
            side_effect=OSError("injected attempt failure"),
        ):
            outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(outcome.status, 2)
        self.assertIn("attempt_record_fail", outcome.label)
        self.assertIn("failed to record", "\n".join(outcome.messages))

    def test_interrupt_before_role_snapshot_still_finishes_attempt(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        runner = fixture.runner(lambda _config, _command: self.fail("callback must not run"))

        with patch.object(
            PhaseRunner,
            "_output_signatures",
            side_effect=KeyboardInterrupt,
        ):
            with self.assertRaises(KeyboardInterrupt):
                runner.invoke_role(
                    role="scope_guardian",
                    phase="scope",
                    task_instructions="write output",
                    input_artifacts=[],
                    output_artifacts=[fixture.artifacts.intent_contract],
                )

        attempt = self.attempt_payload(fixture)
        self.assertEqual(attempt["status"], "failed")
        self.assertTrue(any("KeyboardInterrupt" in error for error in attempt["errors"]))

    def test_cancellation_during_boundary_validation_returns_failed_outcome(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        output = fixture.root / fixture.artifacts.intent_contract

        def callback(_config, _command) -> int:
            output.write_text("{}\n", encoding="utf-8")
            return 0

        with patch(
            "reportmachine_runner.phases.validate_role_write_snapshot",
            side_effect=asyncio.CancelledError,
        ):
            outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(outcome.status, 2)
        self.assertIn("write_scope_violation", outcome.label)
        attempt = self.attempt_payload(fixture)
        self.assertEqual(attempt["status"], "failed")
        self.assertTrue(any("CancelledError" in error for error in attempt["errors"]))

    def test_cancellation_during_attempt_finalization_retries_and_propagates(self) -> None:
        from reportmachine_runner.agent_attempts import finish_attempt as real_finish_attempt

        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        output = fixture.root / fixture.artifacts.intent_contract
        calls = 0

        def callback(_config, _command) -> int:
            output.write_text("{}\n", encoding="utf-8")
            return 0

        def interrupted_finish(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise asyncio.CancelledError
            return real_finish_attempt(**kwargs)

        with patch(
            "reportmachine_runner.phases.finish_attempt",
            side_effect=interrupted_finish,
        ):
            with self.assertRaises(asyncio.CancelledError):
                self.invoke_scope(fixture, callback)

        self.assertEqual(calls, 2)
        attempt = self.attempt_payload(fixture)
        self.assertEqual(attempt["status"], "failed")
        self.assertTrue(
            any("attempt finalization raised CancelledError" in error for error in attempt["errors"])
        )

    def test_declared_output_symlink_directory_and_fifo_fail_before_callback(self) -> None:
        makers = {
            "symlink": lambda path: path.symlink_to("target.json"),
            "directory": lambda path: path.mkdir(),
            "fifo": lambda path: os.mkfifo(path),
        }
        for kind, make_entry in makers.items():
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp_dir:
                fixture = make_fixture(Path(temp_dir))
                output = fixture.root / fixture.artifacts.intent_contract
                output.parent.mkdir(parents=True, exist_ok=True)
                make_entry(output)
                called = False

                def callback(_config, _command) -> int:
                    nonlocal called
                    called = True
                    return 0

                outcome = self.invoke_scope(fixture, callback)

                self.assertEqual(outcome.status, 2)
                self.assertFalse(called)
                self.assertIn("must be a regular file", "\n".join(outcome.messages))
                self.assertEqual(self.attempt_payload(fixture)["status"], "failed")

    def test_role_cannot_replace_declared_output_with_symlink(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        output = fixture.root / fixture.artifacts.intent_contract
        target = output.with_name("target.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}\n", encoding="utf-8")

        def callback(_config, _command) -> int:
            output.symlink_to(target.name)
            return 0

        outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(outcome.status, 2)
        self.assertIn("must be a regular file", "\n".join(outcome.messages))

    def test_structured_o_must_be_one_of_the_exact_role_outputs(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        called = False

        def callback(_config, _command) -> int:
            nonlocal called
            called = True
            return 0

        outcome = fixture.runner(callback).invoke_role(
            role="finalizer",
            phase="finalize",
            task_instructions="write output",
            input_artifacts=[],
            output_artifacts=[fixture.artifacts.article],
            structured_output=True,
        )

        self.assertEqual(outcome.status, 2)
        self.assertFalse(called)
        self.assertIn("structured -o output", "\n".join(outcome.messages))
        self.assertEqual(self.attempt_payload(fixture, "finalizer")["status"], "failed")

    def test_latest_and_its_descendants_cannot_be_declared_role_outputs(self) -> None:
        for declared in ("outputs/latest", "outputs/latest/role.json"):
            with self.subTest(declared=declared), tempfile.TemporaryDirectory() as temp_dir:
                fixture = make_fixture(Path(temp_dir))
                called = False

                def callback(_config, _command) -> int:
                    nonlocal called
                    called = True
                    return 0

                outcome = fixture.runner(callback).invoke_role(
                    role="scope_guardian",
                    phase="scope",
                    task_instructions="write an invalid latest output",
                    input_artifacts=[],
                    output_artifacts=[declared],
                )

                self.assertEqual(outcome.status, 2)
                self.assertFalse(called)
                self.assertIn("never writable by a role", "\n".join(outcome.messages))
                self.assertFalse((fixture.root / "outputs/latest").exists())

    def test_ancestor_and_descendant_outputs_fail_before_callback(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        called = False

        def callback(_config, _command) -> int:
            nonlocal called
            called = True
            return 0

        outcome = fixture.runner(callback).invoke_role(
            role="scope_guardian",
            phase="scope",
            task_instructions="write overlapping outputs",
            input_artifacts=[],
            output_artifacts=["scratch/result", "scratch/result/child.json"],
        )

        self.assertEqual(outcome.status, 2)
        self.assertFalse(called)
        self.assertIn("ancestor and descendant", "\n".join(outcome.messages))
        self.assertFalse((fixture.root / "scratch/result").exists())

    def test_output_and_runner_log_overlap_fails_before_callback(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        fixture = replace(
            fixture,
            config=replace(
                fixture.config,
                json_events=True,
                events_log="scratch/result/events.jsonl",
                stderr_log="logs/test_run/stderr.log",
            ),
        )
        called = False

        def callback(_config, _command) -> int:
            nonlocal called
            called = True
            return 0

        outcome = fixture.runner(callback).invoke_role(
            role="scope_guardian",
            phase="scope",
            task_instructions="write overlapping output and log",
            input_artifacts=[],
            output_artifacts=["scratch/result"],
        )

        self.assertEqual(outcome.status, 2)
        self.assertFalse(called)
        self.assertIn("ancestor and descendant", "\n".join(outcome.messages))
        self.assertFalse((fixture.root / "scratch/result").exists())

    def test_non_posix_role_fails_pre_spawn_and_records_failed_attempt(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        called = False

        def callback(_config, _command) -> int:
            nonlocal called
            called = True
            return 0

        with patch("reportmachine_runner.codex_exec.os", wraps=os) as platform_os:
            platform_os.name = "nt"
            outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(outcome.status, 2)
        self.assertFalse(called)
        self.assertIn("requires POSIX process-group isolation", "\n".join(outcome.messages))
        attempt = self.attempt_payload(fixture)
        self.assertEqual(attempt["status"], "failed")
        self.assertEqual(attempt["exit_code"], 2)

    def test_clean_nonzero_exit_is_recorded_and_returned(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)

        outcome = self.invoke_scope(fixture, lambda _config, _command: 7)

        self.assertEqual(outcome.status, 7)
        attempt = self.attempt_payload(fixture)
        self.assertEqual(attempt["status"], "failed")
        self.assertEqual(attempt["exit_code"], 7)
        self.assertTrue(any("exited with status 7" in error for error in attempt["errors"]))

    def assert_exception_is_recorded_and_latest_restored(self, exc: BaseException) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        latest = fixture.root / "outputs/latest"
        latest.mkdir(parents=True)
        (latest / "article.md").write_text("verified\n", encoding="utf-8")

        def callback(_config, _command) -> int:
            (latest / "article.md").write_text("tampered\n", encoding="utf-8")
            raise exc

        outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(outcome.status, 2)
        self.assertIn("write_scope_violation", outcome.label)
        self.assertEqual((latest / "article.md").read_text(encoding="utf-8"), "verified\n")
        attempt = self.attempt_payload(fixture)
        self.assertEqual(attempt["status"], "failed")
        self.assertEqual(attempt["exit_code"], 2)
        self.assertTrue(
            any(type(exc).__name__ in error for error in attempt["errors"]),
            attempt["errors"],
        )

    def test_clean_role_exception_is_propagated_after_attempt_finalization(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        output = fixture.root / fixture.artifacts.intent_contract

        def callback(_config, _command) -> int:
            output.write_text("{}\n", encoding="utf-8")
            raise RuntimeError("boom")

        with self.assertRaisesRegex(RuntimeError, "boom"):
            self.invoke_scope(fixture, callback)

        attempt = self.attempt_payload(fixture)
        self.assertEqual(attempt["status"], "failed")
        self.assertTrue(any("RuntimeError" in error for error in attempt["errors"]))

    def test_timeout_is_recorded_and_latest_is_restored(self) -> None:
        self.assert_exception_is_recorded_and_latest_restored(
            subprocess.TimeoutExpired("codex", 1)
        )

    def test_exception_is_recorded_and_latest_is_restored(self) -> None:
        self.assert_exception_is_recorded_and_latest_restored(RuntimeError("boom"))

    def test_keyboard_interrupt_is_recorded_and_latest_is_restored(self) -> None:
        self.assert_exception_is_recorded_and_latest_restored(KeyboardInterrupt())

    def test_cancellation_is_recorded_and_latest_is_restored(self) -> None:
        self.assert_exception_is_recorded_and_latest_restored(asyncio.CancelledError())

    def test_latest_nested_bytes_and_internal_symlink_are_restored_on_drift(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        latest = fixture.root / "outputs/latest"
        nested = latest / "nested"
        nested.mkdir(parents=True)
        (nested / "article.md").write_bytes(b"verified\x00bytes")
        (latest / "article-link").symlink_to("nested/article.md")
        output = fixture.root / fixture.artifacts.intent_contract

        def callback(_config, _command) -> int:
            shutil.rmtree(latest)
            latest.mkdir()
            (latest / "replacement.txt").write_text("bad", encoding="utf-8")
            output.write_text("{}\n", encoding="utf-8")
            return 0

        outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(outcome.status, 2)
        self.assertEqual((nested / "article.md").read_bytes(), b"verified\x00bytes")
        self.assertTrue((latest / "article-link").is_symlink())
        self.assertEqual(os.readlink(latest / "article-link"), "nested/article.md")
        self.assertFalse((latest / "replacement.txt").exists())

    def test_latest_created_as_symlink_is_removed_when_baseline_was_absent(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        target = fixture.root / "latest-target"
        target.mkdir()
        output = fixture.root / fixture.artifacts.intent_contract

        def callback(_config, _command) -> int:
            output.write_text("{}\n", encoding="utf-8")
            (fixture.root / "outputs").mkdir(exist_ok=True)
            (fixture.root / "outputs/latest").symlink_to(target, target_is_directory=True)
            return 0

        outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(outcome.status, 2)
        self.assertFalse((fixture.root / "outputs/latest").exists())
        self.assertFalse((fixture.root / "outputs/latest").is_symlink())
        self.assertTrue(target.is_dir())

    def test_invalid_latest_root_fails_before_callback(self) -> None:
        makers = {
            "file": lambda path: path.write_text("bad", encoding="utf-8"),
            "symlink": lambda path: path.symlink_to("elsewhere"),
            "fifo": lambda path: os.mkfifo(path),
        }
        for kind, make_entry in makers.items():
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp_dir:
                fixture = make_fixture(Path(temp_dir))
                latest = fixture.root / "outputs/latest"
                latest.parent.mkdir(parents=True)
                make_entry(latest)
                called = False

                def callback(_config, _command) -> int:
                    nonlocal called
                    called = True
                    return 0

                outcome = self.invoke_scope(fixture, callback)

                self.assertEqual(outcome.status, 2)
                self.assertFalse(called)
                self.assertIn("must be absent or a real directory", "\n".join(outcome.messages))

    def test_restore_failure_is_hard_status_two(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        latest = fixture.root / "outputs/latest"
        latest.mkdir(parents=True)
        (latest / "article.md").write_text("verified", encoding="utf-8")

        def callback(_config, _command) -> int:
            (latest / "article.md").write_text("tampered", encoding="utf-8")
            return 9

        with patch(
            "reportmachine_runner.phases.restore_latest_tree",
            return_value=["outputs/latest restore failed: injected"],
        ):
            outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(outcome.status, 2)
        self.assertIn("latest_restore_fail", outcome.label)
        self.assertIn("restore failed", "\n".join(outcome.messages))

    def test_process_cleanup_failure_skips_latest_restore_and_is_status_two(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        latest = fixture.root / "outputs/latest"
        latest.mkdir(parents=True)
        article = latest / "article.md"
        article.write_text("verified", encoding="utf-8")

        def callback(_config, _command) -> int:
            article.write_text("still changing", encoding="utf-8")
            raise RoleProcessCleanupError("pgid 123 survived cleanup")

        with (
            patch("reportmachine_runner.phases.validate_role_write_snapshot") as scope_validate,
            patch("reportmachine_runner.phases.validate_role_outputs") as output_validate,
            patch("reportmachine_runner.phases.validate_runner_side_effects") as log_validate,
            patch("reportmachine_runner.phases.validate_latest_tree") as latest_validate,
            patch.object(PhaseRunner, "_output_freshness_errors") as freshness_validate,
            patch("reportmachine_runner.phases.restore_latest_tree") as restore,
        ):
            outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(outcome.status, 2)
        self.assertIn("process_cleanup_fail", outcome.label)
        self.assertTrue(outcome.recovery_required)
        for validator in (
            scope_validate,
            output_validate,
            log_validate,
            latest_validate,
            freshness_validate,
            restore,
        ):
            validator.assert_not_called()
        self.assertEqual(article.read_text(encoding="utf-8"), "still changing")
        self.assertIn("validation and outputs/latest restore skipped", "\n".join(outcome.messages))
        self.assertEqual(self.attempt_payload(fixture)["status"], "failed")

    def test_process_cleanup_failure_outweighs_snapshot_and_log_close_errors(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        process = Mock(pid=123)
        process.poll.side_effect = RuntimeError("poll failed")
        log_handle = Mock()
        log_handle.close.side_effect = OSError("close failed")
        log_path = Mock()
        log_path.open.return_value = log_handle
        snapshot_calls = 0

        def failing_snapshot() -> None:
            nonlocal snapshot_calls
            snapshot_calls += 1
            raise ValueError("snapshot failed")

        def callback(_config, command) -> int:
            with (
                patch(
                    "reportmachine_runner.codex_exec.subprocess.Popen",
                    return_value=process,
                ) as popen,
                patch(
                    "reportmachine_runner.codex_exec._cleanup_role_process_group",
                    side_effect=RoleProcessCleanupError("pgid 123 survived cleanup"),
                ),
            ):
                try:
                    return run_command(
                        command,
                        cwd=fixture.root,
                        snapshot_callback=failing_snapshot,
                        snapshot_interval_seconds=1,
                        stdout_path=log_path,
                    )
                finally:
                    self.assertTrue(popen.call_args.kwargs["start_new_session"])

        with (
            patch("reportmachine_runner.phases.validate_role_write_snapshot") as scope_validate,
            patch("reportmachine_runner.phases.validate_role_outputs") as output_validate,
            patch("reportmachine_runner.phases.validate_runner_side_effects") as log_validate,
            patch("reportmachine_runner.phases.validate_latest_tree") as latest_validate,
            patch.object(PhaseRunner, "_output_freshness_errors") as freshness_validate,
            patch("reportmachine_runner.phases.restore_latest_tree") as restore,
        ):
            outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(outcome.status, 2)
        self.assertIn("process_cleanup_fail", outcome.label)
        messages = "\n".join(outcome.messages)
        self.assertIn("RoleProcessCleanupError", messages)
        self.assertIn("snapshot failed", messages)
        self.assertIn("close failed", messages)
        self.assertEqual(snapshot_calls, 1)
        log_handle.close.assert_called_once_with()
        for validator in (
            scope_validate,
            output_validate,
            log_validate,
            latest_validate,
            freshness_validate,
            restore,
        ):
            validator.assert_not_called()
        self.assertEqual(self.attempt_payload(fixture)["status"], "failed")

    @unittest.skipUnless(os.name == "posix", "role SIGTERM guard requires POSIX")
    def test_runner_sigterm_cleans_descendants_restores_latest_and_returns_143(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        latest = fixture.root / "outputs/latest"
        latest.mkdir(parents=True)
        latest_article = latest / "article.md"
        latest_article.write_text("verified\n", encoding="utf-8")
        process_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(process_temporary.cleanup)
        process_state = Path(process_temporary.name)
        child_pid_path = process_state / "sigterm-descendant.pid"
        ready_path = process_state / "sigterm-role-ready"
        child_script = "\n".join(
            [
                "import os, signal, time",
                "from pathlib import Path",
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
                f"Path({str(child_pid_path)!r}).write_text(str(os.getpid()), encoding='utf-8')",
                "time.sleep(2)",
                f"Path({str(latest_article)!r}).write_text('late\\n', encoding='utf-8')",
            ]
        )
        parent_script = "\n".join(
            [
                "import subprocess, sys, time",
                "from pathlib import Path",
                f"pid_path = Path({str(child_pid_path)!r})",
                f"subprocess.Popen([{sys.executable!r}, '-c', {child_script!r}])",
                "deadline = time.monotonic() + 5",
                "while not pid_path.exists() and time.monotonic() < deadline:",
                "    time.sleep(0.01)",
                f"Path({str(ready_path)!r}).write_text('ready', encoding='utf-8')",
                "time.sleep(30)",
            ]
        )
        command = RoleCommand([sys.executable, "-c", parent_script])
        original_handler = signal.getsignal(signal.SIGTERM)
        previous_calls: list[int] = []

        def previous_handler(signum, _frame) -> None:
            previous_calls.append(signum)

        signal.signal(signal.SIGTERM, previous_handler)
        self.addCleanup(signal.signal, signal.SIGTERM, original_handler)
        ready_seen: list[bool] = []
        sender: threading.Thread | None = None

        def callback(_config, _role_command) -> int:
            nonlocal sender

            def send_sigterm() -> None:
                deadline = time.monotonic() + 5
                while not ready_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                ready_seen.append(ready_path.exists())
                os.kill(os.getpid(), signal.SIGTERM)

            sender = threading.Thread(target=send_sigterm, daemon=True)
            sender.start()
            return run_command(command, cwd=fixture.root)

        with (
            patch(
                "reportmachine_runner.codex_exec._ROLE_GROUP_TERM_TIMEOUT_SECONDS",
                0.05,
            ),
            patch(
                "reportmachine_runner.codex_exec._ROLE_GROUP_KILL_TIMEOUT_SECONDS",
                2.0,
            ),
        ):
            outcome = self.invoke_scope(fixture, callback)

        assert sender is not None
        sender.join(timeout=5)
        self.assertFalse(sender.is_alive())
        self.assertEqual(ready_seen, [True])
        self.assertEqual(outcome.status, 128 + signal.SIGTERM, outcome.messages)
        self.assertEqual(latest_article.read_text(encoding="utf-8"), "verified\n")
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid, 0)
        self.assertIs(signal.getsignal(signal.SIGTERM), previous_handler)
        self.assertEqual(previous_calls, [])
        attempt = self.attempt_payload(fixture)
        self.assertEqual(attempt["status"], "failed")
        self.assertEqual(attempt["exit_code"], 128 + signal.SIGTERM)

    @unittest.skipUnless(os.name == "posix", "role SIGTERM guard requires POSIX")
    def test_two_sigterms_keep_guard_through_boundary_attempt_and_latest_stable(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        latest = fixture.root / "outputs/latest"
        latest.mkdir(parents=True)
        latest_article = latest / "article.md"
        latest_article.write_text("verified\n", encoding="utf-8")
        process_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(process_temporary.cleanup)
        process_state = Path(process_temporary.name)
        child_pid_path = process_state / "two-sigterm-descendant.pid"
        ready_path = process_state / "two-sigterm-role-ready"
        late_write_path = process_state / "two-sigterm-late-write"
        child_script = "\n".join(
            [
                "import os, signal, time",
                "from pathlib import Path",
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
                f"Path({str(child_pid_path)!r}).write_text(str(os.getpid()), encoding='utf-8')",
                "time.sleep(0.5)",
                f"Path({str(latest_article)!r}).write_text('late\\n', encoding='utf-8')",
                f"Path({str(late_write_path)!r}).write_text('late', encoding='utf-8')",
                "time.sleep(30)",
            ]
        )
        parent_script = "\n".join(
            [
                "import subprocess, sys, time",
                "from pathlib import Path",
                f"pid_path = Path({str(child_pid_path)!r})",
                f"subprocess.Popen([{sys.executable!r}, '-c', {child_script!r}])",
                "deadline = time.monotonic() + 5",
                "while not pid_path.exists() and time.monotonic() < deadline:",
                "    time.sleep(0.01)",
                "if not pid_path.exists():",
                "    raise SystemExit(98)",
                f"Path({str(ready_path)!r}).write_text('ready', encoding='utf-8')",
                "time.sleep(30)",
            ]
        )
        replacement_command = [sys.executable, "-c", parent_script]
        original_handler = signal.getsignal(signal.SIGTERM)
        previous_calls: list[int] = []

        def previous_handler(signum, _frame) -> None:
            previous_calls.append(signum)

        signal.signal(signal.SIGTERM, previous_handler)
        self.addCleanup(signal.signal, signal.SIGTERM, original_handler)
        boundary_entered = threading.Event()
        second_sent = threading.Event()
        sender_failures: list[str] = []
        sender: threading.Thread | None = None

        def callback(_config, role_command) -> int:
            nonlocal sender
            self.assertIsInstance(role_command, RoleCommand)
            role_command[:] = replacement_command

            def send_sigterms() -> None:
                deadline = time.monotonic() + 5
                while not ready_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                if not ready_path.exists():
                    sender_failures.append("role process did not become ready")
                    return
                os.kill(os.getpid(), signal.SIGTERM)
                if not boundary_entered.wait(timeout=5):
                    sender_failures.append("role boundary was not reached")
                    return
                os.kill(os.getpid(), signal.SIGTERM)
                second_sent.set()

            sender = threading.Thread(target=send_sigterms, daemon=True)
            sender.start()
            return run_command(role_command, cwd=fixture.root)

        def validate_after_second_signal(snapshot):
            scope_errors = validate_role_write_snapshot(snapshot)
            boundary_entered.set()
            self.assertTrue(second_sent.wait(timeout=5), sender_failures)
            return scope_errors

        with (
            patch(
                "reportmachine_runner.codex_exec._ROLE_GROUP_TERM_TIMEOUT_SECONDS",
                0.05,
            ),
            patch(
                "reportmachine_runner.codex_exec._ROLE_GROUP_KILL_TIMEOUT_SECONDS",
                2.0,
            ),
            patch(
                "reportmachine_runner.phases.validate_role_write_snapshot",
                side_effect=validate_after_second_signal,
            ),
        ):
            outcome = self.invoke_scope(fixture, callback)

        assert sender is not None
        sender.join(timeout=5)
        self.assertFalse(sender.is_alive())
        self.assertEqual(sender_failures, [])
        self.assertEqual(previous_calls, [])
        self.assertEqual(outcome.status, 128 + signal.SIGTERM, outcome.messages)
        self.assertEqual(latest_article.read_text(encoding="utf-8"), "verified\n")
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        else:
            self.fail(f"SIGTERM-ignoring descendant {child_pid} survived role cleanup")
        time.sleep(0.6)
        self.assertFalse(late_write_path.exists())
        self.assertEqual(latest_article.read_text(encoding="utf-8"), "verified\n")
        self.assertIs(signal.getsignal(signal.SIGTERM), previous_handler)
        attempt = self.attempt_payload(fixture)
        self.assertEqual(attempt["status"], "failed")
        self.assertEqual(attempt["exit_code"], 128 + signal.SIGTERM)

    @unittest.skipUnless(os.name == "posix", "role SIGTERM guard requires POSIX")
    def test_first_sigterm_during_attempt_finalization_rewrites_attempt_to_143(self) -> None:
        from reportmachine_runner.agent_attempts import finish_attempt as real_finish_attempt

        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        output = fixture.root / fixture.artifacts.intent_contract
        replacement_command = [
            sys.executable,
            "-c",
            "from pathlib import Path; "
            f"Path({str(output)!r}).write_text('{{}}\\n', encoding='utf-8')",
        ]
        original_handler = signal.getsignal(signal.SIGTERM)
        previous_calls: list[int] = []
        finish_exit_codes: list[int] = []

        def previous_handler(signum, _frame) -> None:
            previous_calls.append(signum)

        signal.signal(signal.SIGTERM, previous_handler)
        self.addCleanup(signal.signal, signal.SIGTERM, original_handler)

        def callback(_config, role_command) -> int:
            self.assertIsInstance(role_command, RoleCommand)
            role_command[:] = replacement_command
            return run_command(role_command, cwd=fixture.root)

        def signal_during_first_finish(**kwargs):
            finish_exit_codes.append(kwargs["exit_code"])
            if len(finish_exit_codes) == 1:
                os.kill(os.getpid(), signal.SIGTERM)
            return real_finish_attempt(**kwargs)

        with patch(
            "reportmachine_runner.phases.finish_attempt",
            side_effect=signal_during_first_finish,
        ):
            outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(previous_calls, [])
        self.assertEqual(outcome.status, 128 + signal.SIGTERM, outcome.messages)
        self.assertGreaterEqual(len(finish_exit_codes), 2)
        self.assertEqual(finish_exit_codes[0], 0)
        self.assertEqual(finish_exit_codes[-1], 128 + signal.SIGTERM)
        self.assertIs(signal.getsignal(signal.SIGTERM), previous_handler)
        attempt = self.attempt_payload(fixture)
        self.assertEqual(attempt["status"], "failed")
        self.assertEqual(attempt["exit_code"], 128 + signal.SIGTERM)

    @unittest.skipUnless(os.name == "posix", "role SIGTERM guard requires POSIX")
    def test_first_sigterm_during_boundary_is_latched_until_attempt_finishes(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        output = fixture.root / fixture.artifacts.intent_contract
        original_handler = signal.getsignal(signal.SIGTERM)
        previous_calls: list[int] = []

        def previous_handler(signum, _frame) -> None:
            previous_calls.append(signum)

        signal.signal(signal.SIGTERM, previous_handler)
        self.addCleanup(signal.signal, signal.SIGTERM, original_handler)

        def callback(_config, _command) -> int:
            output.write_text("{}\n", encoding="utf-8")
            return 0

        def signal_during_boundary(snapshot):
            os.kill(os.getpid(), signal.SIGTERM)
            return validate_role_write_snapshot(snapshot)

        with patch(
            "reportmachine_runner.phases.validate_role_write_snapshot",
            side_effect=signal_during_boundary,
        ):
            outcome = self.invoke_scope(fixture, callback)

        self.assertEqual(previous_calls, [])
        self.assertEqual(outcome.status, 128 + signal.SIGTERM, outcome.messages)
        self.assertIs(signal.getsignal(signal.SIGTERM), previous_handler)
        attempt = self.attempt_payload(fixture)
        self.assertEqual(attempt["status"], "failed")
        self.assertEqual(attempt["exit_code"], 128 + signal.SIGTERM)

    @unittest.skipUnless(os.name == "posix", "role SIGTERM guard requires POSIX")
    def test_first_sigterm_at_guard_release_corrects_attempt_and_outcome(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        output = fixture.root / fixture.artifacts.intent_contract
        original_handler = signal.getsignal(signal.SIGTERM)
        previous_calls: list[int] = []
        delivered = False
        real_release = RoleCommand.release_sigterm_guard

        def previous_handler(signum, _frame) -> None:
            previous_calls.append(signum)

        signal.signal(signal.SIGTERM, previous_handler)
        self.addCleanup(signal.signal, signal.SIGTERM, original_handler)

        def callback(_config, _command) -> int:
            output.write_text("{}\n", encoding="utf-8")
            return 0

        def signal_then_release(command):
            nonlocal delivered
            delivered = True
            os.kill(os.getpid(), signal.SIGTERM)
            return real_release(command)

        with patch.object(
            RoleCommand,
            "release_sigterm_guard",
            signal_then_release,
        ):
            outcome = self.invoke_scope(fixture, callback)

        self.assertTrue(delivered)
        self.assertEqual(previous_calls, [])
        self.assertEqual(outcome.status, 128 + signal.SIGTERM, outcome.messages)
        self.assertIs(signal.getsignal(signal.SIGTERM), previous_handler)
        attempt = self.attempt_payload(fixture)
        self.assertEqual(attempt["status"], "failed")
        self.assertEqual(attempt["exit_code"], 128 + signal.SIGTERM)

    @unittest.skipUnless(os.name == "posix", "role SIGTERM guard requires POSIX")
    def test_repeated_sigterm_does_not_mask_process_cleanup_failure(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        process = Mock(pid=123)
        original_handler = signal.getsignal(signal.SIGTERM)

        def previous_handler(_signum, _frame) -> None:
            self.fail("previous SIGTERM handler must not run inside a role call")

        signal.signal(signal.SIGTERM, previous_handler)
        self.addCleanup(signal.signal, signal.SIGTERM, original_handler)

        def interrupted_wait() -> int:
            handler = signal.getsignal(signal.SIGTERM)
            assert callable(handler)
            handler(signal.SIGTERM, None)
            return 0

        def failed_cleanup(_process) -> None:
            handler = signal.getsignal(signal.SIGTERM)
            assert callable(handler)
            handler(signal.SIGTERM, None)
            raise RoleProcessCleanupError("pgid 123 survived cleanup")

        process.wait.side_effect = interrupted_wait
        with (
            patch("reportmachine_runner.codex_exec.subprocess.Popen", return_value=process),
            patch(
                "reportmachine_runner.codex_exec._cleanup_role_process_group",
                side_effect=failed_cleanup,
            ),
        ):
            with self.assertRaisesRegex(RoleProcessCleanupError, "survived cleanup"):
                run_command(RoleCommand(["codex", "exec"]), cwd=fixture.root)

        self.assertIs(signal.getsignal(signal.SIGTERM), previous_handler)

    @unittest.skipUnless(os.name == "posix", "role SIGTERM guard requires POSIX")
    def test_sigterm_at_cleanup_entry_still_runs_group_cleanup(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        process = Mock(pid=123)
        process.wait.return_value = 0
        original_handler = signal.getsignal(signal.SIGTERM)

        def previous_handler(_signum, _frame) -> None:
            self.fail("previous SIGTERM handler must not run inside a role call")

        signal.signal(signal.SIGTERM, previous_handler)
        self.addCleanup(signal.signal, signal.SIGTERM, original_handler)
        source_lines, start_line = inspect.getsourcelines(
            codex_exec_module._run_role_command
        )
        cleanup_entry_line = start_line + next(
            index
            for index, line in enumerate(source_lines)
            if line.strip() == "cleanup_role_once()"
        )
        delivered = False

        def trace_cleanup_entry(frame, event, _arg):
            nonlocal delivered
            if (
                not delivered
                and event == "line"
                and frame.f_code is codex_exec_module._run_role_command.__code__
                and frame.f_lineno == cleanup_entry_line
            ):
                delivered = True
                os.kill(os.getpid(), signal.SIGTERM)
            return trace_cleanup_entry

        with (
            patch("reportmachine_runner.codex_exec.subprocess.Popen", return_value=process),
            patch("reportmachine_runner.codex_exec._cleanup_role_process_group") as cleanup,
        ):
            sys.settrace(trace_cleanup_entry)
            try:
                status = run_command(RoleCommand(["codex", "exec"]), cwd=fixture.root)
            finally:
                sys.settrace(None)

        self.assertTrue(delivered)
        self.assertEqual(status, 128 + signal.SIGTERM)
        cleanup.assert_called_once_with(process)
        self.assertIs(signal.getsignal(signal.SIGTERM), previous_handler)

    @unittest.skipUnless(os.name == "posix", "role SIGTERM guard requires POSIX")
    def test_sigterm_during_popen_assignment_is_cleaned_after_process_ownership(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        process = Mock(pid=123)
        process.wait.return_value = 0
        original_handler = signal.getsignal(signal.SIGTERM)

        def previous_handler(_signum, _frame) -> None:
            self.fail("previous SIGTERM handler must not run inside a role call")

        signal.signal(signal.SIGTERM, previous_handler)
        self.addCleanup(signal.signal, signal.SIGTERM, original_handler)

        def spawn_and_deliver(*_args, **_kwargs):
            handler = signal.getsignal(signal.SIGTERM)
            assert callable(handler)
            handler(signal.SIGTERM, None)
            return process

        with (
            patch(
                "reportmachine_runner.codex_exec.subprocess.Popen",
                side_effect=spawn_and_deliver,
            ),
            patch("reportmachine_runner.codex_exec._cleanup_role_process_group") as cleanup,
        ):
            status = run_command(RoleCommand(["codex", "exec"]), cwd=fixture.root)

        self.assertEqual(status, 128 + signal.SIGTERM)
        cleanup.assert_called_once_with(process)
        self.assertIs(signal.getsignal(signal.SIGTERM), previous_handler)

    @unittest.skipUnless(os.name == "posix", "role SIGTERM guard requires POSIX")
    def test_sigterm_during_handler_installation_restores_previous_handler(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        original_handler = signal.getsignal(signal.SIGTERM)

        def previous_handler(_signum, _frame) -> None:
            self.fail("previous SIGTERM handler must not run after guard installation")

        signal.signal(signal.SIGTERM, previous_handler)
        self.addCleanup(signal.signal, signal.SIGTERM, original_handler)
        real_signal = signal.signal
        delivered = False

        def install_and_deliver(signum, handler):
            nonlocal delivered
            previous = real_signal(signum, handler)
            if not delivered and handler is not previous_handler:
                delivered = True
                assert callable(handler)
                handler(signal.SIGTERM, None)
            return previous

        with (
            patch(
                "reportmachine_runner.codex_exec.signal.signal",
                side_effect=install_and_deliver,
            ),
            patch("reportmachine_runner.codex_exec.subprocess.Popen") as popen,
        ):
            status = run_command(RoleCommand(["codex", "exec"]), cwd=fixture.root)

        self.assertTrue(delivered)
        self.assertEqual(status, 128 + signal.SIGTERM)
        popen.assert_not_called()
        self.assertIs(signal.getsignal(signal.SIGTERM), previous_handler)

    def test_only_image_auditor_receives_and_hashes_image_inputs(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        base_image_rel = "work/runs/test_run/images/rendered/base.png"
        ledger_image_rel = "work/runs/test_run/images/rendered/ledger.png"
        for rel, data in ((base_image_rel, b"base"), (ledger_image_rel, b"ledger")):
            path = fixture.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        ledger = fixture.root / fixture.config.rendered_images_ledger
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(
            "image_path,status\n" + f"{ledger_image_rel},ok\n",
            encoding="utf-8",
        )

        seen: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []

        def scope_callback(config, command) -> int:
            seen.append((config.role, config.image_inputs, tuple(command)))
            (fixture.root / fixture.artifacts.intent_contract).write_text(
                "{}\n", encoding="utf-8"
            )
            return 0

        scope_outcome = self.invoke_scope(
            replace(fixture, config=replace(fixture.config, image_inputs=(base_image_rel,))),
            scope_callback,
        )
        self.assertEqual(scope_outcome.status, 0)
        self.assertEqual(seen[-1][1], ())
        self.assertNotIn("--image", seen[-1][2])

        report = fixture.artifacts.image_audit_report

        def auditor_callback(config, command) -> int:
            seen.append((config.role, config.image_inputs, tuple(command)))
            path = fixture.root / report
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n", encoding="utf-8")
            return 0

        auditor_outcome = fixture.runner(
            auditor_callback,
            image_inputs=(base_image_rel,),
        ).invoke_role(
            role="image_slide_auditor",
            phase="review",
            task_instructions="audit images",
            input_artifacts=[],
            output_artifacts=[report],
        )
        self.assertEqual(auditor_outcome.status, 0)
        self.assertEqual(seen[-1][1], (base_image_rel, ledger_image_rel))
        self.assertIn("--image", seen[-1][2])
        attempt = self.attempt_payload(fixture, "image_slide_auditor")
        for rel in (base_image_rel, ledger_image_rel):
            self.assertIsNotNone(attempt["input_artifact_hashes"][rel])

    @unittest.skipUnless(os.name == "posix", "role process groups require POSIX")
    def test_role_commands_reap_descendants_after_normal_and_nonzero_exit(self) -> None:
        cases = ((False, 0), (True, 7))
        for periodic, exit_code in cases:
            with self.subTest(periodic=periodic, exit_code=exit_code), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                command, child_pid_path, late_write = self.descendant_command(
                    root,
                    exit_code=exit_code,
                )
                snapshots: list[str] = []
                kwargs = {}
                if periodic:
                    kwargs = {
                        "snapshot_callback": lambda: snapshots.append("snapshot"),
                        "snapshot_interval_seconds": 60,
                    }

                with (
                    patch(
                        "reportmachine_runner.codex_exec._ROLE_GROUP_TERM_TIMEOUT_SECONDS",
                        0.05,
                    ),
                    patch(
                        "reportmachine_runner.codex_exec._ROLE_GROUP_KILL_TIMEOUT_SECONDS",
                        2.0,
                    ),
                ):
                    status = run_command(command, cwd=root, **kwargs)

                self.assertEqual(status, exit_code)
                child_pid = int(child_pid_path.read_text(encoding="utf-8"))
                with self.assertRaises(ProcessLookupError):
                    os.kill(child_pid, 0)
                self.assertFalse(late_write.exists())
                self.assertEqual(snapshots, [])

    @unittest.skipUnless(os.name == "posix", "role process groups require POSIX")
    def test_nonperiodic_role_exceptions_reap_descendants_before_propagating(self) -> None:
        for exception in (RuntimeError("boom"), KeyboardInterrupt(), asyncio.CancelledError()):
            with self.subTest(exception=type(exception).__name__), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                command, child_pid_path, late_write = self.descendant_command(
                    root,
                    exit_code=None,
                )

                def interrupted_wait(_process, timeout=None):
                    self.assertIsNone(timeout)
                    deadline = time.monotonic() + 5
                    while not child_pid_path.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(child_pid_path.exists(), "descendant did not start")
                    raise exception

                with (
                    patch(
                        "reportmachine_runner.codex_exec._ROLE_GROUP_TERM_TIMEOUT_SECONDS",
                        0.05,
                    ),
                    patch(
                        "reportmachine_runner.codex_exec._ROLE_GROUP_KILL_TIMEOUT_SECONDS",
                        2.0,
                    ),
                    patch.object(subprocess.Popen, "wait", new=interrupted_wait),
                ):
                    with self.assertRaises(type(exception)):
                        run_command(command, cwd=root)

                child_pid = int(child_pid_path.read_text(encoding="utf-8"))
                with self.assertRaises(ProcessLookupError):
                    os.kill(child_pid, 0)
                self.assertFalse(late_write.exists())

    @unittest.skipUnless(os.name == "posix", "role process groups require POSIX")
    def test_periodic_role_exceptions_reap_descendants_before_propagating(self) -> None:
        for exception in (RuntimeError("boom"), KeyboardInterrupt(), asyncio.CancelledError()):
            with self.subTest(exception=type(exception).__name__), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                command, child_pid_path, late_write = self.descendant_command(
                    root,
                    exit_code=None,
                )
                snapshots = 0

                def interrupting_snapshot() -> None:
                    nonlocal snapshots
                    snapshots += 1
                    deadline = time.monotonic() + 5
                    while not child_pid_path.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(child_pid_path.exists(), "descendant did not start")
                    raise exception

                with (
                    patch(
                        "reportmachine_runner.codex_exec._ROLE_GROUP_TERM_TIMEOUT_SECONDS",
                        0.05,
                    ),
                    patch(
                        "reportmachine_runner.codex_exec._ROLE_GROUP_KILL_TIMEOUT_SECONDS",
                        2.0,
                    ),
                ):
                    with self.assertRaises(type(exception)):
                        run_command(
                            command,
                            cwd=root,
                            snapshot_callback=interrupting_snapshot,
                            snapshot_interval_seconds=0.01,
                        )

                child_pid = int(child_pid_path.read_text(encoding="utf-8"))
                with self.assertRaises(ProcessLookupError):
                    os.kill(child_pid, 0)
                self.assertFalse(late_write.exists())
                self.assertEqual(snapshots, 2)

    def test_periodic_command_interrupt_reaps_child_before_propagating(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("codex", 5), 0]
        snapshots: list[str] = []

        with (
            patch("reportmachine_runner.codex_exec.subprocess.Popen", return_value=process),
            patch("reportmachine_runner.codex_exec.time.monotonic", side_effect=[0.0, 0.1]),
            patch("reportmachine_runner.codex_exec.time.sleep", side_effect=KeyboardInterrupt),
        ):
            with self.assertRaises(KeyboardInterrupt):
                run_command(
                    ["codex", "exec"],
                    cwd=fixture.root,
                    snapshot_callback=lambda: snapshots.append("snapshot"),
                    snapshot_interval_seconds=10,
                )

        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)
        self.assertEqual(snapshots, ["snapshot"])

    def test_interrupt_during_first_periodic_clock_read_still_reaps_child(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        process = Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        snapshots: list[str] = []

        with (
            patch("reportmachine_runner.codex_exec.subprocess.Popen", return_value=process),
            patch(
                "reportmachine_runner.codex_exec.time.monotonic",
                side_effect=KeyboardInterrupt,
            ),
        ):
            with self.assertRaises(KeyboardInterrupt):
                run_command(
                    ["codex", "exec"],
                    cwd=fixture.root,
                    snapshot_callback=lambda: snapshots.append("snapshot"),
                    snapshot_interval_seconds=10,
                )

        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=5)
        self.assertEqual(snapshots, ["snapshot"])


if __name__ == "__main__":
    unittest.main()
