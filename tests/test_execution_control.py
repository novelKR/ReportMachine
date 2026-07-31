from __future__ import annotations

import multiprocessing
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from reportmachine_runner import cli
from reportmachine_runner.codex_exec import RoleCommand, run_command
from reportmachine_runner.execution_control import (
    BUSY_EXIT_CODE,
    RECOVERY_REQUIRED_EXIT_CODE,
    SIGTERM_EXIT_CODE,
    ExecutionControlFailure,
    RecoveryFenceRequired,
    active_writer_lock_fd,
    acknowledge_recovery_fence,
    read_owner_record,
    run_with_execution_control,
    state_paths,
)


def _hold_execution(
    root_text: str,
    state_text: str,
    ready: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
    statuses: multiprocessing.queues.Queue,
) -> None:
    def callback() -> int:
        ready.set()
        if not release.wait(15):
            return 2
        return 0

    statuses.put(
        run_with_execution_control(
            Path(root_text),
            callback,
            operation="test-holder",
            run_id="holder",
            state_dir=Path(state_text),
        )
    )


def _crash_execution(
    root_text: str,
    state_text: str,
    ready: multiprocessing.synchronize.Event,
) -> None:
    def callback() -> int:
        ready.set()
        os._exit(91)

    run_with_execution_control(
        Path(root_text),
        callback,
        operation="test-crash",
        run_id="crash",
        state_dir=Path(state_text),
    )


def _wait_for_sigterm(
    root_text: str,
    state_text: str,
    ready: multiprocessing.synchronize.Event,
    statuses: multiprocessing.queues.Queue,
) -> None:
    def callback() -> int:
        ready.set()
        while True:
            time.sleep(0.1)

    statuses.put(
        run_with_execution_control(
            Path(root_text),
            callback,
            operation="test-sigterm",
            run_id="sigterm",
            state_dir=Path(state_text),
        )
    )


def _run_role_child(
    root_text: str,
    state_text: str,
    ready_text: str,
    release_text: str,
    writes_text: str,
) -> None:
    child_program = "\n".join(
        [
            "import os, sys, time",
            "from pathlib import Path",
            "ready, release, writes = map(Path, sys.argv[1:4])",
            "ready.write_text(str(os.getpid()), encoding='utf-8')",
            "while not release.exists():",
            "    with writes.open('a', encoding='utf-8') as handle:",
            "        handle.write('write\\n')",
            "        handle.flush()",
            "    time.sleep(0.05)",
        ]
    )

    def callback() -> int:
        return run_command(
            RoleCommand(
                [
                    sys.executable,
                    "-c",
                    child_program,
                    ready_text,
                    release_text,
                    writes_text,
                ]
            ),
            cwd=Path(root_text),
        )

    run_with_execution_control(
        Path(root_text),
        callback,
        operation="test-role-child",
        run_id="role_child",
        state_dir=Path(state_text),
    )


def _run_generic_child(
    root_text: str,
    state_text: str,
    ready_text: str,
    release_text: str,
    writes_text: str,
) -> None:
    child_program = "\n".join(
        [
            "import os, sys, time",
            "from pathlib import Path",
            "ready, release, writes = map(Path, sys.argv[1:4])",
            "ready.write_text(str(os.getpid()), encoding='utf-8')",
            "while not release.exists():",
            "    with writes.open('a', encoding='utf-8') as handle:",
            "        handle.write('write\\n')",
            "        handle.flush()",
            "    time.sleep(0.05)",
        ]
    )

    def callback() -> int:
        return run_command(
            [
                sys.executable,
                "-c",
                child_program,
                ready_text,
                release_text,
                writes_text,
            ],
            cwd=Path(root_text),
        )

    run_with_execution_control(
        Path(root_text),
        callback,
        operation="test-summary-generic-child",
        run_id="summary_child",
        state_dir=Path(state_text),
    )


def _leave_recovery_with_live_child(
    root_text: str,
    state_text: str,
    ready_text: str,
    release_text: str,
    writes_text: str,
    statuses: multiprocessing.queues.Queue,
) -> None:
    child_program = "\n".join(
        [
            "import os, sys, time",
            "from pathlib import Path",
            "ready, release, writes = map(Path, sys.argv[1:4])",
            "ready.write_text(str(os.getpid()), encoding='utf-8')",
            "while not release.exists():",
            "    with writes.open('a', encoding='utf-8') as handle:",
            "        handle.write('write\\n')",
            "        handle.flush()",
            "    time.sleep(0.05)",
        ]
    )

    def callback() -> int:
        descriptor = active_writer_lock_fd()
        if descriptor is None:
            raise AssertionError("writer lease descriptor was not active")
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                child_program,
                ready_text,
                release_text,
                writes_text,
            ],
            cwd=Path(root_text),
            pass_fds=(descriptor,),
        )
        deadline = time.monotonic() + 10
        while not Path(ready_text).is_file() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not Path(ready_text).is_file():
            raise AssertionError("lease-sharing child did not start")
        raise RecoveryFenceRequired("injected cleanup uncertainty")

    statuses.put(
        run_with_execution_control(
            Path(root_text),
            callback,
            operation="test-live-child-recovery",
            run_id="live_child_recovery",
            state_dir=Path(state_text),
        )
    )


class ExecutionControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.root = base / "checkout"
        self.root.mkdir()
        self.state = base / "state"
        self.environment = patch(
            "reportmachine_runner.execution_control.default_state_root",
            return_value=self.state,
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def test_clean_release_uses_atomic_private_owner_record(self) -> None:
        observed: dict[str, object] = {}

        def callback() -> int:
            paths = state_paths(self.root)
            owner = read_owner_record(paths)
            self.assertIsNotNone(owner)
            assert owner is not None
            observed.update(owner)
            if os.name == "posix":
                self.assertEqual(
                    stat.S_IMODE(paths.owner_record.stat().st_mode),
                    0o600,
                )
            self.assertEqual(list(paths.checkout_dir.glob(".owner.json.*.tmp")), [])
            return 0

        status = run_with_execution_control(
            self.root,
            callback,
            operation="article",
            run_id="clean",
            mode="industry",
        )

        self.assertEqual(status, 0)
        self.assertEqual(observed["operation"], "article")
        self.assertEqual(observed["checkout_root"], str(self.root.resolve()))
        self.assertIsNone(read_owner_record(self.root))

    def test_ambient_state_override_cannot_split_the_production_lock(self) -> None:
        with patch.dict(
            os.environ,
            {"REPORTMACHINE_STATE_DIR": str(self.root / "different-lock-root")},
        ):
            paths = state_paths(self.root)
        self.assertEqual(paths.state_root, self.state.resolve())
        self.assertNotIn("different-lock-root", str(paths.lock_file))

    def test_catchable_failure_settles_cleanly(self) -> None:
        def fail() -> int:
            raise RuntimeError("expected failure")

        with self.assertRaisesRegex(RuntimeError, "expected failure"):
            run_with_execution_control(
                self.root,
                fail,
                operation="article",
            )
        self.assertIsNone(read_owner_record(self.root))

    def test_explicit_unsettled_mutation_retains_exit_76_recovery_fence(self) -> None:
        def fail_unsettled() -> int:
            raise RecoveryFenceRequired("injected mutation settlement failure")

        self.assertEqual(
            run_with_execution_control(
                self.root,
                fail_unsettled,
                operation="unsettled-test",
            ),
            RECOVERY_REQUIRED_EXIT_CODE,
        )
        owner = read_owner_record(self.root)
        self.assertIsNotNone(owner)
        assert owner is not None
        self.assertEqual(owner["state"], "recovery_required")
        self.assertEqual(owner["exit_code"], RECOVERY_REQUIRED_EXIT_CODE)
        self.assertIn("settlement failure", str(owner["message"]))

    def test_clean_role_style_143_return_does_not_leave_recovery_fence(self) -> None:
        status = run_with_execution_control(
            self.root,
            lambda: SIGTERM_EXIT_CODE,
            operation="article",
        )

        self.assertEqual(status, SIGTERM_EXIT_CODE)
        self.assertIsNone(read_owner_record(self.root))

    def test_two_real_processes_contend_nonblocking(self) -> None:
        context = multiprocessing.get_context("spawn")
        ready = context.Event()
        release = context.Event()
        statuses = context.Queue()
        process = context.Process(
            target=_hold_execution,
            args=(str(self.root), str(self.state), ready, release, statuses),
        )
        process.start()
        try:
            self.assertTrue(ready.wait(10), "holder did not acquire the writer lock")
            started = time.monotonic()
            status = run_with_execution_control(
                self.root,
                lambda: 0,
                operation="contender",
            )
            self.assertEqual(status, BUSY_EXIT_CODE)
            self.assertLess(time.monotonic() - started, 2.0)
        finally:
            release.set()
            process.join(15)
            if process.is_alive():
                process.kill()
                process.join(5)
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(statuses.get(timeout=2), 0)
        self.assertIsNone(read_owner_record(self.root))

    def test_missing_root_identity_is_stable_from_holder_through_contender(self) -> None:
        missing_root = Path(self.temporary.name) / "created-checkout"
        self.assertFalse(missing_root.exists())
        context = multiprocessing.get_context("spawn")
        ready = context.Event()
        release = context.Event()
        statuses = context.Queue()
        process = context.Process(
            target=_hold_execution,
            args=(str(missing_root), str(self.state), ready, release, statuses),
        )
        process.start()
        try:
            self.assertTrue(ready.wait(10), "missing-root holder did not acquire the lock")
            self.assertTrue(missing_root.is_dir())
            self.assertEqual(
                run_with_execution_control(
                    missing_root,
                    lambda: 0,
                    operation="missing-root-contender",
                    state_dir=self.state,
                ),
                BUSY_EXIT_CODE,
            )
        finally:
            release.set()
            process.join(15)
            if process.is_alive():
                process.kill()
                process.join(5)
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(statuses.get(timeout=2), 0)
        self.assertEqual(
            run_with_execution_control(
                missing_root,
                lambda: 0,
                operation="missing-root-after-holder",
                state_dir=self.state,
            ),
            0,
        )

    @unittest.skipUnless(sys.platform == "darwin", "macOS case-alias regression")
    def test_case_aliases_of_the_same_checkout_share_one_real_process_lock(self) -> None:
        alias = self.root.with_name(self.root.name.swapcase())
        try:
            same_checkout = alias.exists() and os.path.samefile(self.root, alias)
        except OSError:
            same_checkout = False
        if not same_checkout:
            self.skipTest("test volume does not expose a case alias for the checkout")
        self.assertEqual(state_paths(self.root).checkout_id, state_paths(alias).checkout_id)

        context = multiprocessing.get_context("spawn")
        ready = context.Event()
        release = context.Event()
        statuses = context.Queue()
        process = context.Process(
            target=_hold_execution,
            args=(str(self.root), str(self.state), ready, release, statuses),
        )
        process.start()
        try:
            self.assertTrue(ready.wait(10), "holder did not acquire the writer lock")
            self.assertEqual(
                run_with_execution_control(alias, lambda: 0, operation="alias-contender"),
                BUSY_EXIT_CODE,
            )
        finally:
            release.set()
            process.join(15)
            if process.is_alive():
                process.kill()
                process.join(5)
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(statuses.get(timeout=2), 0)

    @unittest.skipUnless(os.name == "posix", "role lock inheritance requires POSIX")
    def test_crashed_top_level_cannot_release_lock_while_role_child_is_alive(self) -> None:
        base = Path(self.temporary.name)
        ready = base / "role-ready"
        release = base / "role-release"
        writes = base / "role-writes"
        context = multiprocessing.get_context("spawn")
        process = context.Process(
            target=_run_role_child,
            args=(
                str(self.root),
                str(self.state),
                str(ready),
                str(release),
                str(writes),
            ),
        )
        process.start()
        role_pid: int | None = None
        try:
            deadline = time.monotonic() + 10
            while not ready.is_file() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(ready.is_file(), "role child did not start")
            role_pid = int(ready.read_text(encoding="utf-8"))
            owner = read_owner_record(self.root)
            self.assertIsNotNone(owner)
            assert owner is not None

            process.kill()
            process.join(10)
            self.assertFalse(process.is_alive())

            before = writes.stat().st_size
            time.sleep(0.15)
            self.assertGreater(writes.stat().st_size, before)
            self.assertEqual(
                run_with_execution_control(
                    self.root,
                    lambda: 0,
                    operation="new-writer-while-role-lives",
                ),
                BUSY_EXIT_CODE,
            )
            with self.assertRaises(ExecutionControlFailure) as raised:
                acknowledge_recovery_fence(
                    self.root,
                    expected_lease_id=str(owner["lease_id"]),
                )
            self.assertEqual(raised.exception.exit_code, BUSY_EXIT_CODE)

            release.write_text("stop\n", encoding="utf-8")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                status = run_with_execution_control(
                    self.root,
                    lambda: 0,
                    operation="after-role-exit",
                )
                if status != BUSY_EXIT_CODE:
                    break
                time.sleep(0.05)
            else:
                self.fail("role child did not release the inherited writer lock")
            self.assertEqual(status, RECOVERY_REQUIRED_EXIT_CODE)
            self.assertTrue(
                acknowledge_recovery_fence(
                    self.root,
                    expected_lease_id=str(owner["lease_id"]),
                )
            )
            self.assertEqual(
                run_with_execution_control(
                    self.root,
                    lambda: 0,
                    operation="post-recovery-writer",
                ),
                0,
            )
        finally:
            release.write_text("stop\n", encoding="utf-8")
            if process.is_alive():
                process.kill()
                process.join(5)
            if role_pid is not None:
                try:
                    os.kill(role_pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    try:
                        os.kill(role_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    @unittest.skipUnless(os.name == "posix", "recovery lease inheritance requires POSIX")
    def test_recovery_fence_keeps_shared_lease_until_live_child_exits(self) -> None:
        base = Path(self.temporary.name)
        ready = base / "recovery-child-ready"
        release = base / "recovery-child-release"
        writes = base / "recovery-child-writes"
        context = multiprocessing.get_context("spawn")
        statuses = context.Queue()
        process = context.Process(
            target=_leave_recovery_with_live_child,
            args=(
                str(self.root),
                str(self.state),
                str(ready),
                str(release),
                str(writes),
                statuses,
            ),
        )
        process.start()
        child_pid: int | None = None
        try:
            deadline = time.monotonic() + 10
            while not ready.is_file() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(ready.is_file(), "lease-sharing child did not start")
            child_pid = int(ready.read_text(encoding="utf-8"))
            process.join(10)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 0)
            self.assertEqual(statuses.get(timeout=2), RECOVERY_REQUIRED_EXIT_CODE)
            owner = read_owner_record(self.root)
            self.assertIsNotNone(owner)
            assert owner is not None

            before = writes.stat().st_size
            time.sleep(0.15)
            self.assertGreater(writes.stat().st_size, before)
            self.assertEqual(
                run_with_execution_control(
                    self.root,
                    lambda: 0,
                    operation="contender-during-recovery-child",
                ),
                BUSY_EXIT_CODE,
            )
            with self.assertRaises(ExecutionControlFailure) as raised:
                acknowledge_recovery_fence(
                    self.root,
                    expected_lease_id=str(owner["lease_id"]),
                )
            self.assertEqual(raised.exception.exit_code, BUSY_EXIT_CODE)

            release.write_text("stop\n", encoding="utf-8")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                status = run_with_execution_control(
                    self.root,
                    lambda: 0,
                    operation="after-recovery-child",
                )
                if status != BUSY_EXIT_CODE:
                    break
                time.sleep(0.05)
            else:
                self.fail("lease-sharing child did not release the writer lock")
            self.assertEqual(status, RECOVERY_REQUIRED_EXIT_CODE)
            self.assertTrue(
                acknowledge_recovery_fence(
                    self.root,
                    expected_lease_id=str(owner["lease_id"]),
                )
            )
            self.assertEqual(
                run_with_execution_control(
                    self.root,
                    lambda: 0,
                    operation="post-child-recovery-writer",
                ),
                0,
            )
        finally:
            release.write_text("stop\n", encoding="utf-8")
            if process.is_alive():
                process.kill()
                process.join(5)
            if child_pid is not None:
                try:
                    os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    @unittest.skipUnless(os.name == "posix", "summary child lock inheritance requires POSIX")
    def test_crashed_summary_generic_parent_keeps_lease_until_child_exits(self) -> None:
        base = Path(self.temporary.name)
        ready = base / "summary-child-ready"
        release = base / "summary-child-release"
        writes = base / "summary-child-writes"
        context = multiprocessing.get_context("spawn")
        process = context.Process(
            target=_run_generic_child,
            args=(
                str(self.root),
                str(self.state),
                str(ready),
                str(release),
                str(writes),
            ),
        )
        process.start()
        child_pid: int | None = None
        try:
            deadline = time.monotonic() + 10
            while not ready.is_file() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(ready.is_file(), "summary generic child did not start")
            child_pid = int(ready.read_text(encoding="utf-8"))
            owner = read_owner_record(self.root)
            self.assertIsNotNone(owner)
            assert owner is not None

            process.kill()
            process.join(10)
            self.assertFalse(process.is_alive())
            before = writes.stat().st_size
            time.sleep(0.15)
            self.assertGreater(writes.stat().st_size, before)
            self.assertEqual(
                run_with_execution_control(
                    self.root,
                    lambda: 0,
                    operation="contender-during-summary-child",
                ),
                BUSY_EXIT_CODE,
            )
            with self.assertRaises(ExecutionControlFailure) as raised:
                acknowledge_recovery_fence(
                    self.root,
                    expected_lease_id=str(owner["lease_id"]),
                )
            self.assertEqual(raised.exception.exit_code, BUSY_EXIT_CODE)

            release.write_text("stop\n", encoding="utf-8")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                status = run_with_execution_control(
                    self.root,
                    lambda: 0,
                    operation="after-summary-child",
                )
                if status != BUSY_EXIT_CODE:
                    break
                time.sleep(0.05)
            else:
                self.fail("summary child did not release its inherited writer lock")
            self.assertEqual(status, RECOVERY_REQUIRED_EXIT_CODE)
            self.assertTrue(
                acknowledge_recovery_fence(
                    self.root,
                    expected_lease_id=str(owner["lease_id"]),
                )
            )
            self.assertEqual(
                run_with_execution_control(
                    self.root,
                    lambda: run_command(
                        [sys.executable, "-c", "pass"],
                        cwd=self.root,
                    ),
                    operation="normal-summary-generic-child",
                ),
                0,
            )
            self.assertEqual(
                run_with_execution_control(
                    self.root,
                    lambda: 0,
                    operation="after-normal-summary-child",
                ),
                0,
            )
        finally:
            release.write_text("stop\n", encoding="utf-8")
            if process.is_alive():
                process.kill()
                process.join(5)
            if child_pid is not None:
                try:
                    os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    @unittest.skipUnless(os.name == "posix", "crash fencing requires POSIX process tests")
    def test_process_crash_leaves_recovery_fence(self) -> None:
        context = multiprocessing.get_context("spawn")
        ready = context.Event()
        process = context.Process(
            target=_crash_execution,
            args=(str(self.root), str(self.state), ready),
        )
        process.start()
        self.assertTrue(ready.wait(10), "crash process did not acquire the writer lock")
        process.join(10)
        self.assertEqual(process.exitcode, 91)

        owner = read_owner_record(self.root)
        self.assertIsNotNone(owner)
        assert owner is not None
        self.assertEqual(owner["state"], "active")
        self.assertEqual(
            run_with_execution_control(
                self.root,
                lambda: 0,
                operation="after-crash",
            ),
            RECOVERY_REQUIRED_EXIT_CODE,
        )

    @unittest.skipUnless(os.name == "posix", "SIGTERM fencing requires POSIX signals")
    def test_sigterm_outside_role_returns_143_and_marks_recovery(self) -> None:
        context = multiprocessing.get_context("spawn")
        ready = context.Event()
        statuses = context.Queue()
        process = context.Process(
            target=_wait_for_sigterm,
            args=(str(self.root), str(self.state), ready, statuses),
        )
        process.start()
        self.assertTrue(ready.wait(10), "SIGTERM process did not acquire the writer lock")
        os.kill(process.pid, signal.SIGTERM)
        process.join(10)
        if process.is_alive():
            process.kill()
            process.join(5)
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(statuses.get(timeout=2), SIGTERM_EXIT_CODE)
        owner = read_owner_record(self.root)
        self.assertIsNotNone(owner)
        assert owner is not None
        self.assertEqual(owner["state"], "recovery_required")
        self.assertEqual(owner["exit_code"], SIGTERM_EXIT_CODE)

    @unittest.skipUnless(os.name == "posix", "SIGTERM fencing requires POSIX signals")
    def test_cli_resolves_automatic_run_id_before_crash_fence_and_recovery(self) -> None:
        resolved = SimpleNamespace(
            root=self.root,
            run_id="automatic_20260731T000000Z",
            mode="industry",
        )

        def interrupt(_parser: object, _args: object, *, context: object) -> int:
            self.assertIs(context, resolved)
            os.kill(os.getpid(), signal.SIGTERM)
            return 99

        with (
            patch.object(cli, "build_run_context", return_value=resolved) as build_context,
            patch.object(cli, "run_article_workflow", side_effect=interrupt),
        ):
            status = cli.main(
                [
                    "--mode",
                    "industry",
                    "--project-root",
                    str(self.root),
                ]
            )

        self.assertEqual(status, SIGTERM_EXIT_CODE)
        build_context.assert_called_once()
        owner = read_owner_record(self.root)
        self.assertIsNotNone(owner)
        assert owner is not None
        self.assertEqual(owner["run_id"], resolved.run_id)
        self.assertEqual(
            cli.main(
                [
                    "--project-root",
                    str(self.root),
                    "--acknowledge-execution-recovery",
                    str(owner["lease_id"]),
                ]
            ),
            0,
        )
        self.assertIsNone(read_owner_record(self.root))

    def test_article_smoke_test_does_not_acquire_execution_control(self) -> None:
        with (
            patch.object(cli, "run_article_workflow", return_value=0) as workflow,
            patch.object(
                cli,
                "run_with_execution_control",
                side_effect=AssertionError("smoke test attempted to lock"),
            ),
        ):
            status = cli.main(
                [
                    "--mode",
                    "industry",
                    "--run-id",
                    "smoke",
                    "--project-root",
                    str(self.root),
                    "--smoke-test",
                ]
            )
        self.assertEqual(status, 0)
        workflow.assert_called_once()
        self.assertFalse(self.state.exists())

    def test_recovery_acknowledgement_is_exact_idempotent_and_does_not_retry(self) -> None:
        paths = state_paths(self.root)

        def leave_fence() -> int:
            owner = read_owner_record(paths)
            assert owner is not None
            os.kill(os.getpid(), signal.SIGTERM)
            return 99

        self.assertEqual(
            run_with_execution_control(
                self.root,
                leave_fence,
                operation="recoverable",
                run_id="recovery_run",
            ),
            SIGTERM_EXIT_CODE,
        )
        owner = read_owner_record(paths)
        assert owner is not None
        with self.assertRaises(ExecutionControlFailure):
            acknowledge_recovery_fence(
                self.root,
                expected_lease_id="wrong",
            )
        self.assertIsNotNone(read_owner_record(paths))
        with self.assertRaises(ExecutionControlFailure):
            acknowledge_recovery_fence(
                self.root,
                expected_lease_id=str(owner["lease_id"]),
                on_acknowledged=lambda: (_ for _ in ()).throw(
                    RuntimeError("receipt write failed")
                ),
            )
        restored = read_owner_record(paths)
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored["lease_id"], owner["lease_id"])
        self.assertEqual(
            cli.main(
                [
                    "--project-root",
                    str(self.root),
                    "--acknowledge-execution-recovery",
                    str(owner["lease_id"]),
                ]
            ),
            0,
        )
        self.assertFalse(
            acknowledge_recovery_fence(
                self.root,
                expected_lease_id=str(owner["lease_id"]),
            )
        )
        self.assertEqual(
            run_with_execution_control(
                self.root,
                lambda: 0,
                operation="after-recovery",
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
