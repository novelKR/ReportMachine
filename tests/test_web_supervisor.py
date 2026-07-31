from __future__ import annotations

import signal
import os
import subprocess
import sys
import threading
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reportmachine_runner.execution_control import (
    SIGTERM_EXIT_CODE,
    read_owner_record,
    run_with_execution_control,
)
from reportmachine_runner.web.contracts import ActionRequest, build_runner_argv
from reportmachine_runner.web.state import (
    ExecutionReceipt,
    OperationState,
    StateError,
    StateStore,
    utc_now,
)
from reportmachine_runner.web.supervisor import (
    SingleSupervisor,
    SupervisorError,
    build_child_environment,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IDEMPOTENCY_KEY = "request:0001"


class FakeProcess:
    def __init__(self, pid: int = 4242, return_code: int = 0) -> None:
        self.pid = pid
        self.stdout = None
        self.return_code = return_code
        self.returncode: int | None = None

    def wait(self) -> int:
        self.returncode = self.return_code
        return self.return_code

    def poll(self) -> int | None:
        return self.returncode


class StaticOwner:
    def __init__(self, owner: dict[str, object] | None) -> None:
        self.owner = owner
        self.state_dir = None

    def read_safely(self) -> tuple[dict[str, object] | None, None]:
        return self.owner, None


class WebSupervisorTests(unittest.TestCase):
    def request(self, **overrides: object) -> ActionRequest:
        payload = {"action": "prepare", "mode": "industry", "run_id": "web_test"}
        payload.update(overrides)
        for name, value in overrides.items():
            if value is None:
                payload.pop(name, None)
        return ActionRequest.from_mapping(payload)

    def store(self, directory: str) -> StateStore:
        return StateStore(PROJECT_ROOT, environ={"REPORTMACHINE_STATE_DIR": directory})

    def supervisor(self, store: StateStore, **overrides: object) -> SingleSupervisor:
        overrides.setdefault("core_state_dir", store.directory.parents[2])
        return SingleSupervisor(
            PROJECT_ROOT,
            store=store,
            source_environment={
                "HOME": "/safe/home",
                "CODEX_HOME": "/safe/codex",
                "LANG": "C.UTF-8",
                "HTTP_PROXY": "http://proxy.invalid",
                "HTTPS_PROXY": "http://proxy.invalid",
                "OPENAI_API_KEY": "should-not-reach-child",
                "AUTHORIZATION": "Bearer should-not-reach-child",
            },
            controlled_path=("/safe/bin", "/usr/bin"),
            **overrides,
        )

    def test_child_environment_is_allowlisted_and_excludes_proxy_and_auth_secrets(self) -> None:
        environment = build_child_environment(
            {
                "HOME": "/safe/home",
                "LANG": "ko_KR.UTF-8",
                "HTTP_PROXY": "http://proxy.invalid",
                "ALL_PROXY": "socks://proxy.invalid",
                "OPENAI_API_KEY": "not-allowed",
                "AUTHORIZATION": "Bearer not-allowed",
            },
            controlled_path=("/controlled/bin", "/usr/bin"),
            web_execution_id="01234567-89ab-4def-8123-456789abcdef",
        )
        self.assertEqual(
            environment,
            {
                "HOME": "/safe/home",
                "LANG": "ko_KR.UTF-8",
                "PATH": "/controlled/bin:/usr/bin",
                "REPORTMACHINE_WEB_EXECUTION_ID": "01234567-89ab-4def-8123-456789abcdef",
            },
        )

    def test_single_active_execution_fixed_popen_and_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(temp_dir)
            execution_id = "01234567-89ab-4def-8123-456789abcdef"
            process = FakeProcess()
            with (
                patch("reportmachine_runner.web.supervisor.subprocess.Popen", return_value=process) as popen,
                patch(
                    "reportmachine_runner.web.supervisor.process_birth_hint",
                    return_value="ps:fixed-birth",
                ),
                patch("reportmachine_runner.web.supervisor.threading.Thread") as thread,
            ):
                supervisor = self.supervisor(store)
                started = supervisor.start(self.request(), idempotency_key=IDEMPOTENCY_KEY)
                repeated = supervisor.start(self.request(), idempotency_key=IDEMPOTENCY_KEY)
                duplicate_tab = supervisor.start(
                    self.request(),
                    idempotency_key="request:0002",
                )

                self.assertEqual(started.state, OperationState.RUNNING)
                self.assertEqual(repeated.execution_id, started.execution_id)
                self.assertEqual(duplicate_tab.execution_id, started.execution_id)
                self.assertEqual(started.requested_phase, "prepare")
                self.assertEqual(popen.call_count, 1)
                kwargs = popen.call_args.kwargs
                self.assertFalse(kwargs["shell"])
                self.assertTrue(kwargs["start_new_session"])
                self.assertEqual(kwargs["cwd"], PROJECT_ROOT)
                self.assertNotIn("HTTP_PROXY", kwargs["env"])
                self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
                self.assertNotIn("AUTHORIZATION", kwargs["env"])
                self.assertEqual(kwargs["env"]["PATH"], "/safe/bin:/usr/bin")
                self.assertIn("REPORTMACHINE_WEB_START_FD", kwargs["env"])
                self.assertEqual(
                    kwargs["pass_fds"],
                    (int(kwargs["env"]["REPORTMACHINE_WEB_START_FD"]),),
                )
                spawned_argv = popen.call_args.args[0]
                self.assertTrue(str(spawned_argv[1]).endswith("_launch_gate.py"))
                self.assertEqual(
                    tuple(spawned_argv[2:]),
                    build_runner_argv(
                        self.request(),
                        project_root=PROJECT_ROOT,
                        python_executable=sys.executable,
                    ),
                )
                thread.return_value.start.assert_called_once()

                with self.assertRaisesRegex(SupervisorError, "blocks new work"):
                    supervisor.start(self.request(run_id="web_test_2"), idempotency_key="request:0002")
                with self.assertRaisesRegex(SupervisorError, "different payload"):
                    supervisor.start(self.request(action="run_all"), idempotency_key=IDEMPOTENCY_KEY)

    def test_cancel_signals_only_owned_top_level_process_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(temp_dir)
            process = FakeProcess(pid=4343)
            with (
                patch("reportmachine_runner.web.supervisor.subprocess.Popen", return_value=process),
                patch(
                    "reportmachine_runner.web.supervisor.process_birth_hint",
                    return_value="ps:fixed-birth",
                ),
                patch("reportmachine_runner.web.supervisor.threading.Thread"),
                patch("reportmachine_runner.web.supervisor.os.kill") as kill,
            ):
                supervisor = self.supervisor(store)
                running = supervisor.start(self.request(), idempotency_key=IDEMPOTENCY_KEY)
                cancelling = supervisor.cancel(running.execution_id)
                repeated = supervisor.cancel(running.execution_id)

            self.assertEqual(cancelling.state, OperationState.CANCELLING)
            self.assertEqual(repeated.state, OperationState.CANCELLING)
            kill.assert_called_once_with(process.pid, signal.SIGTERM)
            self.assertEqual(store.load().state, OperationState.CANCELLING)

    def test_cancel_never_signals_a_process_already_reaped_by_the_watcher(self) -> None:
        class ReapedProcess(FakeProcess):
            def __init__(self) -> None:
                super().__init__(pid=4344, return_code=0)
                self.reaped = threading.Event()

            def wait(self) -> int:
                result = super().wait()
                self.reaped.set()
                return result

        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(temp_dir)
            process = ReapedProcess()
            with (
                patch("reportmachine_runner.web.supervisor.subprocess.Popen", return_value=process),
                patch(
                    "reportmachine_runner.web.supervisor.process_birth_hint",
                    return_value="ps:fixed-birth",
                ),
                patch("reportmachine_runner.web.supervisor.threading.Thread"),
            ):
                supervisor = self.supervisor(store)
                running = supervisor.start(
                    self.request(),
                    idempotency_key=IDEMPOTENCY_KEY,
                )

            with (
                patch(
                    "reportmachine_runner.web.supervisor.process_identity_is_live",
                    return_value=False,
                ),
                patch("reportmachine_runner.web.supervisor.os.kill") as kill,
            ):
                with supervisor._lock:
                    watcher = threading.Thread(
                        target=supervisor._watch_process,
                        args=(process, running.execution_id),
                    )
                    watcher.start()
                    self.assertTrue(process.reaped.wait(5), "watcher did not reap the process")
                    with self.assertRaisesRegex(SupervisorError, "already exited"):
                        supervisor.cancel(running.execution_id)
                watcher.join(5)

            self.assertFalse(watcher.is_alive())
            kill.assert_not_called()
            settled = store.load()
            assert settled is not None
            self.assertEqual(settled.state, OperationState.EXITED)
            self.assertEqual(settled.exit_code, 0)

    def test_cancel_late_natural_exit_zero_settles_as_exited(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(temp_dir)
            process = FakeProcess(pid=4345, return_code=0)
            with (
                patch("reportmachine_runner.web.supervisor.subprocess.Popen", return_value=process),
                patch(
                    "reportmachine_runner.web.supervisor.process_birth_hint",
                    return_value="ps:fixed-birth",
                ),
                patch("reportmachine_runner.web.supervisor.threading.Thread"),
            ):
                supervisor = self.supervisor(store)
                running = supervisor.start(
                    self.request(),
                    idempotency_key=IDEMPOTENCY_KEY,
                )

            def natural_exit_before_signal(_pid: int, _signum: int) -> None:
                process.returncode = 0
                raise ProcessLookupError

            with (
                patch(
                    "reportmachine_runner.web.supervisor.process_identity_is_live",
                    return_value=False,
                ),
                patch(
                    "reportmachine_runner.web.supervisor.os.kill",
                    side_effect=natural_exit_before_signal,
                ) as kill,
            ):
                cancelling = supervisor.cancel(running.execution_id)
                watcher = threading.Thread(
                    target=supervisor._watch_process,
                    args=(process, running.execution_id),
                )
                watcher.start()
                watcher.join(5)

            self.assertFalse(watcher.is_alive())
            self.assertEqual(cancelling.state, OperationState.CANCELLING)
            kill.assert_called_once_with(process.pid, signal.SIGTERM)
            settled = store.load()
            assert settled is not None
            self.assertEqual(settled.state, OperationState.EXITED)
            self.assertEqual(settled.exit_code, 0)
            self.assertIn("before cancellation took effect", settled.message)

    def test_cancelling_real_exit_zero_with_foreign_owner_stays_exited(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            release = base / "release"
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sys, time; from pathlib import Path; "
                        "release = Path(sys.argv[1]); "
                        "\nwhile not release.exists(): time.sleep(0.01)"
                    ),
                    str(release),
                ]
            )
            execution_id = "01234567-89ab-4def-8123-456789abcdef"
            now = utc_now()
            store = self.store(temp_dir)
            store.write(
                ExecutionReceipt(
                    execution_id=execution_id,
                    idempotency_key=IDEMPOTENCY_KEY,
                    payload_digest="a" * 64,
                    run_id="web_test",
                    action="run_all",
                    state=OperationState.CANCELLING,
                    created_at=now,
                    updated_at=now,
                    pid=process.pid,
                    started_at=now,
                    cancel_requested_at=now,
                )
            )
            foreign = StaticOwner(
                {
                    "pid": process.pid + 1,
                    "lease_id": "foreign",
                    "state": "active",
                    "web_execution_id": None,
                }
            )
            supervisor = SingleSupervisor(
                PROJECT_ROOT,
                store=store,
                owner_adapter=foreign,
                recover=False,
            )
            supervisor._process = process
            watcher = threading.Thread(
                target=supervisor._watch_process,
                args=(process, execution_id),
            )
            watcher.start()
            try:
                release.write_text("exit\n", encoding="utf-8")
                watcher.join(5)
                self.assertFalse(watcher.is_alive())
                self.assertEqual(process.returncode, 0)
                settled = store.load()
                assert settled is not None
                self.assertEqual(settled.state, OperationState.EXITED)
                self.assertEqual(settled.exit_code, 0)
                self.assertIn("before cancellation took effect", settled.message)
                self.assertIn("independent writer", settled.message)
                self.assertEqual(
                    settled.artifact_final["_observation"],
                    "not_observed_foreign_writer",
                )
            finally:
                release.write_text("exit\n", encoding="utf-8")
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)

    def test_terminal_same_action_requires_explicit_rerun_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(temp_dir)
            with (
                patch(
                    "reportmachine_runner.web.supervisor.subprocess.Popen",
                    side_effect=[FakeProcess(pid=4401), FakeProcess(pid=4402)],
                ) as popen,
                patch(
                    "reportmachine_runner.web.supervisor.process_birth_hint",
                    return_value="ps:fixed-birth",
                ),
                patch("reportmachine_runner.web.supervisor.threading.Thread"),
            ):
                supervisor = self.supervisor(store)
                first = supervisor.start(
                    self.request(),
                    idempotency_key="request:1001",
                )
                store.write(
                    first.transition(
                        OperationState.EXITED,
                        exit_code=0,
                        finished_at=utc_now(),
                    )
                )
                deduplicated = supervisor.start(
                    self.request(),
                    idempotency_key="request:1002",
                )
                rerun = supervisor.start(
                    self.request(confirm_rerun=True),
                    idempotency_key="request:1003",
                )

            self.assertEqual(deduplicated.execution_id, first.execution_id)
            self.assertNotEqual(rerun.execution_id, first.execution_id)
            self.assertEqual(popen.call_count, 2)

    def test_intervening_action_preserves_bounded_historical_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(temp_dir)
            with (
                patch(
                    "reportmachine_runner.web.supervisor.subprocess.Popen",
                    side_effect=[
                        FakeProcess(pid=4451),
                        FakeProcess(pid=4452),
                        FakeProcess(pid=4453),
                    ],
                ) as popen,
                patch(
                    "reportmachine_runner.web.supervisor.process_birth_hint",
                    return_value="ps:fixed-birth",
                ),
                patch("reportmachine_runner.web.supervisor.threading.Thread"),
            ):
                supervisor = self.supervisor(store)
                first = supervisor.start(
                    self.request(),
                    idempotency_key="request:history-a",
                )
                store.write(
                    first.transition(
                        OperationState.EXITED,
                        exit_code=0,
                        finished_at=utc_now(),
                    )
                )
                second = supervisor.start(
                    self.request(action="run_all"),
                    idempotency_key="request:history-b",
                )
                store.write(
                    second.transition(
                        OperationState.EXITED,
                        exit_code=0,
                        finished_at=utc_now(),
                    )
                )

                restarted = self.supervisor(store)
                with self.assertRaisesRegex(SupervisorError, "already accepted"):
                    restarted.start(
                        self.request(),
                        idempotency_key="request:history-a",
                    )
                with self.assertRaisesRegex(SupervisorError, "already accepted"):
                    restarted.start(
                        self.request(),
                        idempotency_key="request:history-c",
                    )
                with self.assertRaisesRegex(SupervisorError, "different payload"):
                    restarted.start(
                        self.request(topic="changed payload"),
                        idempotency_key="request:history-a",
                    )
                with self.assertRaisesRegex(SupervisorError, "already accepted"):
                    restarted.start(
                        self.request(confirm_rerun=True),
                        idempotency_key="request:history-a",
                    )
                rerun = restarted.start(
                    self.request(confirm_rerun=True),
                    idempotency_key="request:history-rerun",
                )

            self.assertEqual(popen.call_count, 3)
            self.assertNotEqual(rerun.execution_id, first.execution_id)
            persisted = store.load()
            assert persisted is not None
            self.assertEqual(
                [item.execution_id for item in persisted.accepted_requests[:2]],
                [second.execution_id, first.execution_id],
            )

    def test_spawn_failure_is_recorded_as_terminal_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(temp_dir)
            with patch(
                "reportmachine_runner.web.supervisor.subprocess.Popen",
                side_effect=OSError("missing executable"),
            ):
                supervisor = self.supervisor(store)
                with self.assertRaisesRegex(SupervisorError, "could not be started"):
                    supervisor.start(self.request(), idempotency_key=IDEMPOTENCY_KEY)

            receipt = store.load()
            self.assertIsNotNone(receipt)
            assert receipt is not None
            self.assertEqual(receipt.state, OperationState.EXITED)
            self.assertEqual(receipt.exit_code, 126)
            self.assertIn("OSError", receipt.message)

    def test_running_receipt_failure_denies_real_child_before_runner_entry(self) -> None:
        class FailRunningReceiptStore(StateStore):
            def write(self, receipt: ExecutionReceipt) -> None:
                if receipt.state is OperationState.RUNNING:
                    raise StateError("injected running receipt failure")
                super().write(receipt)

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            checkout = base / "checkout"
            checkout.mkdir()
            marker = base / "runner-entered"
            target = base / "runner_target.py"
            target.write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('entered', encoding='utf-8')\n",
                encoding="utf-8",
            )
            ordinary = StateStore(
                checkout,
                environ={"REPORTMACHINE_STATE_DIR": str(base / "state")},
            )
            store = FailRunningReceiptStore(
                checkout,
                state_directory=ordinary.directory,
            )
            supervisor = SingleSupervisor(
                checkout,
                store=store,
                source_environment={"HOME": str(base)},
                controlled_path=(
                    str(Path(sys.executable).resolve().parent),
                    "/usr/bin",
                    "/bin",
                ),
                core_state_dir=base / "state",
            )
            with patch(
                "reportmachine_runner.web.supervisor.build_runner_argv",
                return_value=(sys.executable, str(target)),
            ), patch(
                "reportmachine_runner.web.supervisor.process_birth_hint",
                return_value="ps:fixed-birth",
            ):
                with self.assertRaisesRegex(SupervisorError, "permit was denied"):
                    supervisor.start(self.request(), idempotency_key=IDEMPOTENCY_KEY)

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                receipt = supervisor.current_receipt()
                if receipt is not None and receipt.state is OperationState.RECOVERY_REQUIRED:
                    break
                time.sleep(0.02)
            else:
                self.fail("denied child did not settle")
            self.assertFalse(marker.exists())
            self.assertIn("start gate denied", store.read_log_tail())

    def test_real_parent_crash_before_permit_never_executes_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            marker = base / "runner-entered"
            target = base / "runner_target.py"
            target.write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('entered', encoding='utf-8')\n",
                encoding="utf-8",
            )
            gate = PROJECT_ROOT / "src" / "reportmachine_runner" / "web" / "_launch_gate.py"
            crash_parent = base / "crash_parent.py"
            crash_parent.write_text(
                "\n".join(
                    [
                        "import os, subprocess, sys",
                        "read_fd, write_fd = os.pipe()",
                        "environment = {",
                        "    'PATH': os.environ.get('PATH', '/usr/bin:/bin'),",
                        "    'REPORTMACHINE_WEB_EXECUTION_ID': '01234567-89ab-4def-8123-456789abcdef',",
                        "    'REPORTMACHINE_WEB_START_FD': str(read_fd),",
                        "}",
                        f"subprocess.Popen([sys.executable, {str(gate)!r}, sys.executable, {str(target)!r}], env=environment, stdin=subprocess.DEVNULL, close_fds=True, pass_fds=(read_fd,))",
                        "os.close(read_fd)",
                        "os._exit(91)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            crashed = subprocess.run(
                [sys.executable, str(crash_parent)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
                check=False,
            )

            self.assertEqual(crashed.returncode, 91)
            self.assertIn("start gate denied", crashed.stderr)
            self.assertFalse(marker.exists())

    def test_watcher_does_not_misattribute_a_foreign_cli_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(temp_dir)
            execution_id = "01234567-89ab-4def-8123-456789abcdef"
            now = utc_now()
            store.write(
                ExecutionReceipt(
                    execution_id=execution_id,
                    idempotency_key=IDEMPOTENCY_KEY,
                    payload_digest="a" * 64,
                    run_id="web_test",
                    action="run_all",
                    state=OperationState.RUNNING,
                    created_at=now,
                    updated_at=now,
                    pid=4242,
                    started_at=now,
                    artifact_baseline={"_observation": "stable"},
                )
            )
            foreign = StaticOwner(
                {
                    "pid": 5151,
                    "lease_id": "foreign",
                    "state": "active",
                    "web_execution_id": None,
                }
            )
            supervisor = SingleSupervisor(
                PROJECT_ROOT,
                store=store,
                owner_adapter=foreign,
                recover=False,
            )
            process = FakeProcess(pid=4242, return_code=75)
            supervisor._process = process
            with patch(
                "reportmachine_runner.web.supervisor.process_identity_is_live",
                return_value=False,
            ):
                supervisor._watch_process(process, execution_id)

            receipt = store.load()
            assert receipt is not None
            self.assertEqual(receipt.state, OperationState.EXITED)
            self.assertEqual(receipt.exit_code, 75)
            self.assertIn("independent writer", receipt.message)
            self.assertEqual(
                receipt.artifact_final["_observation"],
                "not_observed_foreign_writer",
            )

    def test_watcher_binds_an_unclean_fence_to_its_execution_id_and_pid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(temp_dir)
            execution_id = "01234567-89ab-4def-8123-456789abcdef"
            now = utc_now()
            store.write(
                ExecutionReceipt(
                    execution_id=execution_id,
                    idempotency_key=IDEMPOTENCY_KEY,
                    payload_digest="a" * 64,
                    run_id="web_test",
                    action="run_all",
                    state=OperationState.RUNNING,
                    created_at=now,
                    updated_at=now,
                    pid=4242,
                    started_at=now,
                )
            )
            owned = StaticOwner(
                {
                    "pid": 4242,
                    "lease_id": "owned",
                    "state": "recovery_required",
                    "web_execution_id": execution_id,
                }
            )
            supervisor = SingleSupervisor(
                PROJECT_ROOT,
                store=store,
                owner_adapter=owned,
                recover=False,
            )
            process = FakeProcess(pid=4242, return_code=143)
            with patch(
                "reportmachine_runner.web.supervisor.process_identity_is_live",
                return_value=False,
            ):
                supervisor._watch_process(process, execution_id)

            receipt = store.load()
            assert receipt is not None
            self.assertEqual(receipt.state, OperationState.RECOVERY_REQUIRED)
            self.assertEqual(
                receipt.artifact_final["_observation"],
                "stable_owned_recovery_fence",
            )

    def test_restart_marks_queued_receipt_as_recovery_required_without_retrying(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(temp_dir)
            now = utc_now()
            store.write(
                ExecutionReceipt(
                    execution_id="01234567-89ab-4def-8123-456789abcdef",
                    idempotency_key=IDEMPOTENCY_KEY,
                    payload_digest="a" * 64,
                    run_id="web_test",
                    action="prepare",
                    state=OperationState.QUEUED,
                    created_at=now,
                    updated_at=now,
                )
            )
            with patch("reportmachine_runner.web.supervisor.subprocess.Popen") as popen:
                supervisor = self.supervisor(store)

            recovered = supervisor.current_receipt()
            self.assertIsNotNone(recovered)
            assert recovered is not None
            self.assertEqual(recovered.state, OperationState.RECOVERY_REQUIRED)
            self.assertIn("spawn-window ambiguity", recovered.message)
            popen.assert_not_called()

    def test_restart_moves_dead_interrupted_receipt_to_recovery_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(temp_dir)
            now = utc_now()
            store.write(
                ExecutionReceipt(
                    execution_id="01234567-89ab-4def-8123-456789abcdef",
                    idempotency_key=IDEMPOTENCY_KEY,
                    payload_digest="a" * 64,
                    run_id="web_test",
                    action="prepare",
                    state=OperationState.INTERRUPTED_UNKNOWN,
                    created_at=now,
                    updated_at=now,
                    pid=4242,
                    process_birth_hint="ps:old-birth",
                )
            )
            with patch(
                "reportmachine_runner.web.supervisor.process_identity_is_live",
                return_value=False,
            ):
                supervisor = self.supervisor(store)

            recovered = supervisor.current_receipt()
            assert recovered is not None
            self.assertEqual(recovered.state, OperationState.RECOVERY_REQUIRED)
            self.assertIn("did not settle cleanly", recovered.message)

    def test_restart_preserves_existing_recovery_required_even_if_pid_looks_live(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(temp_dir)
            now = utc_now()
            original = ExecutionReceipt(
                execution_id="01234567-89ab-4def-8123-456789abcdef",
                idempotency_key=IDEMPOTENCY_KEY,
                payload_digest="a" * 64,
                run_id="web_test",
                action="prepare",
                state=OperationState.RECOVERY_REQUIRED,
                created_at=now,
                updated_at=now,
                pid=4242,
                process_birth_hint="ps:old-birth",
                message="operator inspection required",
            )
            store.write(original)
            with patch(
                "reportmachine_runner.web.supervisor.process_identity_is_live",
                return_value=True,
            ) as is_live:
                supervisor = self.supervisor(store)

            preserved = supervisor.current_receipt()
            self.assertEqual(preserved, original)
            is_live.assert_not_called()

    def test_explicit_recovery_checks_exact_owner_and_never_retries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(temp_dir)
            execution_id = "01234567-89ab-4def-8123-456789abcdef"

            def interrupt() -> int:
                os.kill(os.getpid(), signal.SIGTERM)
                return 99

            with patch.dict(
                os.environ,
                {"REPORTMACHINE_WEB_EXECUTION_ID": execution_id},
            ):
                self.assertEqual(
                    run_with_execution_control(
                        PROJECT_ROOT,
                        interrupt,
                        operation="article",
                        run_id="web_test",
                        state_dir=Path(temp_dir),
                    ),
                    SIGTERM_EXIT_CODE,
                )
            owner = read_owner_record(PROJECT_ROOT, state_dir=Path(temp_dir))
            assert owner is not None
            now = utc_now()
            store.write(
                ExecutionReceipt(
                    execution_id=execution_id,
                    idempotency_key=IDEMPOTENCY_KEY,
                    payload_digest="a" * 64,
                    run_id="web_test",
                    action="run_all",
                    state=OperationState.RECOVERY_REQUIRED,
                    created_at=now,
                    updated_at=now,
                    exit_code=143,
                    finished_at=now,
                    pid=int(owner["pid"]),
                )
            )
            supervisor = self.supervisor(store)
            supervisor._state_error = "transient durable state error"
            with patch(
                "reportmachine_runner.web.supervisor.process_identity_is_live",
                return_value=False,
            ):
                with self.assertRaisesRegex(SupervisorError, "exact owner_lease_id"):
                    supervisor.acknowledge_recovery(
                        execution_id,
                        owner_lease_id="0" * 32,
                    )
                recovered = supervisor.acknowledge_recovery(
                    execution_id,
                    owner_lease_id=str(owner["lease_id"]),
                )

            self.assertEqual(recovered.state, OperationState.RECOVERED)
            self.assertIsNone(
                read_owner_record(PROJECT_ROOT, state_dir=Path(temp_dir))
            )
            self.assertIn("not retried", recovered.message)
            self.assertIsNone(supervisor._state_error)

            supervisor._state_error = "later transient durable state error"
            repeated = supervisor.acknowledge_recovery(execution_id)
            self.assertEqual(repeated.state, OperationState.RECOVERED)
            self.assertIsNone(supervisor._state_error)

            with (
                patch(
                    "reportmachine_runner.web.supervisor.subprocess.Popen",
                    return_value=FakeProcess(pid=4404),
                ),
                patch(
                    "reportmachine_runner.web.supervisor.process_birth_hint",
                    return_value="ps:new-birth",
                ),
                patch("reportmachine_runner.web.supervisor.threading.Thread"),
            ):
                started = supervisor.start(
                    self.request(run_id="web_test_after_recovery"),
                    idempotency_key="request:after-recovery",
                )
            self.assertEqual(started.state, OperationState.RUNNING)

    def test_real_web_child_and_direct_cli_contend_on_the_same_os_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            checkout = base / "checkout"
            checkout.mkdir()
            state_root = base / "state"
            helper = base / "lock_holder.py"
            helper.write_text(
                "\n".join(
                    [
                        "import sys, time",
                        "from pathlib import Path",
                        f"sys.path.insert(0, {str(PROJECT_ROOT / 'src')!r})",
                        "from reportmachine_runner.execution_control import run_with_execution_control",
                        "root = Path(sys.argv[1])",
                        "state = Path(sys.argv[2])",
                        "raise SystemExit(run_with_execution_control(root, lambda: (time.sleep(1.5) or 0), operation='web-test', run_id='web_test', state_dir=state))",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            store = StateStore(
                checkout,
                environ={"REPORTMACHINE_STATE_DIR": str(state_root)},
            )
            supervisor = SingleSupervisor(
                checkout,
                store=store,
                source_environment={"HOME": str(base)},
                controlled_path=(str(Path(sys.executable).resolve().parent), "/usr/bin", "/bin"),
                core_state_dir=state_root,
            )
            command = (sys.executable, str(helper), str(checkout), str(state_root))
            with patch(
                "reportmachine_runner.web.supervisor.build_runner_argv",
                return_value=command,
            ), patch(
                "reportmachine_runner.web.supervisor.process_birth_hint",
                return_value="ps:fixed-birth",
            ):
                supervisor.start(self.request(), idempotency_key=IDEMPOTENCY_KEY)

            deadline = time.monotonic() + 5
            while (
                read_owner_record(checkout, state_dir=state_root) is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            self.assertIsNotNone(read_owner_record(checkout, state_dir=state_root))
            self.assertEqual(
                run_with_execution_control(
                    checkout,
                    lambda: 0,
                    operation="direct-cli-contender",
                    state_dir=state_root,
                ),
                75,
            )
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                receipt = supervisor.current_receipt()
                if receipt is not None and receipt.state is OperationState.EXITED:
                    break
                time.sleep(0.02)
            else:
                self.fail("Web child did not settle")
            assert receipt is not None
            self.assertEqual(receipt.exit_code, 0)
            self.assertIsNone(read_owner_record(checkout, state_dir=state_root))

    def test_real_cancel_stays_cancelling_until_runner_settles_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            checkout = base / "checkout"
            checkout.mkdir()
            state_root = base / "state"
            helper = base / "cancel_holder.py"
            helper.write_text(
                "\n".join(
                    [
                        "import signal, sys, time",
                        "from pathlib import Path",
                        f"sys.path.insert(0, {str(PROJECT_ROOT / 'src')!r})",
                        "from reportmachine_runner.execution_control import run_with_execution_control",
                        "class Stop(Exception): pass",
                        "def handle(_signum, _frame): raise Stop()",
                        "def callback():",
                        "    previous = signal.getsignal(signal.SIGTERM)",
                        "    signal.signal(signal.SIGTERM, handle)",
                        "    try:",
                        "        while True: time.sleep(0.05)",
                        "    except Stop:",
                        "        return 143",
                        "    finally:",
                        "        signal.signal(signal.SIGTERM, previous)",
                        "root = Path(sys.argv[1])",
                        "state = Path(sys.argv[2])",
                        "raise SystemExit(run_with_execution_control(root, callback, operation='web-cancel-test', run_id='web_test', state_dir=state))",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            store = StateStore(
                checkout,
                environ={"REPORTMACHINE_STATE_DIR": str(state_root)},
            )
            supervisor = SingleSupervisor(
                checkout,
                store=store,
                source_environment={"HOME": str(base)},
                controlled_path=(str(Path(sys.executable).resolve().parent), "/usr/bin", "/bin"),
                core_state_dir=state_root,
            )
            with patch(
                "reportmachine_runner.web.supervisor.build_runner_argv",
                return_value=(sys.executable, str(helper), str(checkout), str(state_root)),
            ), patch(
                "reportmachine_runner.web.supervisor.process_birth_hint",
                return_value="ps:fixed-birth",
            ):
                running = supervisor.start(
                    self.request(),
                    idempotency_key=IDEMPOTENCY_KEY,
                )
            deadline = time.monotonic() + 5
            while (
                read_owner_record(checkout, state_dir=state_root) is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            self.assertIsNotNone(read_owner_record(checkout, state_dir=state_root))

            cancelling = supervisor.cancel(running.execution_id)
            self.assertEqual(cancelling.state, OperationState.CANCELLING)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                receipt = supervisor.current_receipt()
                if receipt is not None and receipt.state is OperationState.CANCELLED:
                    break
                time.sleep(0.02)
            else:
                self.fail("cancelled runner did not settle")
            assert receipt is not None
            self.assertEqual(receipt.exit_code, 143)
            self.assertIsNone(read_owner_record(checkout, state_dir=state_root))


if __name__ == "__main__":
    unittest.main()
