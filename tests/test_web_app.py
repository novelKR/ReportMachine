from __future__ import annotations

import builtins
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reportmachine_runner.web.artifacts import ArtifactPayload, ArtifactName
from reportmachine_runner.web.contracts import ActionRequest
from reportmachine_runner.web.security import SecurityError, validate_browser_boundary
from reportmachine_runner.web.state import ExecutionReceipt, OperationState, utc_now


EXPECTED_HOST = "127.0.0.1:8765"
EXPECTED_ORIGIN = "http://127.0.0.1:8765"


class _FakeStore:
    def read_log_tail(self) -> str:
        return "safe log"


class _FakeOwner:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir

    def read_safely(self) -> tuple[None, None]:
        return None, None


class _FakeSupervisor:
    def __init__(self, state_dir: Path) -> None:
        self.store = _FakeStore()
        self.owner_adapter = _FakeOwner(state_dir)
        self.receipt: ExecutionReceipt | None = None
        self.started: ActionRequest | None = None
        self.recovered: tuple[str, str | None] | None = None

    def current_receipt(self) -> ExecutionReceipt | None:
        return self.receipt

    def start(self, request: ActionRequest, *, idempotency_key: str) -> ExecutionReceipt:
        self.started = request
        now = utc_now()
        self.receipt = ExecutionReceipt(
            execution_id="01234567-89ab-4def-8123-456789abcdef",
            idempotency_key=idempotency_key,
            payload_digest=request.payload_digest(),
            run_id=request.run_id or "missing",
            action=request.action.value,
            state=OperationState.RUNNING,
            created_at=now,
            updated_at=now,
            pid=4321,
            started_at=now,
        )
        return self.receipt

    def cancel(self, execution_id: str) -> ExecutionReceipt:
        assert self.receipt is not None and execution_id == self.receipt.execution_id
        self.receipt = self.receipt.transition(OperationState.CANCELLING)
        return self.receipt

    def acknowledge_recovery(
        self,
        execution_id: str,
        *,
        owner_lease_id: str | None = None,
    ) -> ExecutionReceipt:
        self.recovered = (execution_id, owner_lease_id)
        assert self.receipt is not None
        self.receipt = self.receipt.transition(OperationState.RECOVERED)
        return self.receipt


class _FakeStatus:
    def read(self, run_id: str) -> dict[str, object]:
        return {"run_id": run_id, "article": {"state": "not_checked"}}


class _FakeBroker:
    def read(self, run_id: str, artifact: str) -> ArtifactPayload:
        return ArtifactPayload(
            artifact=ArtifactName.ARTICLE,
            run_id=run_id,
            filename="article.md",
            media_type="text/markdown; charset=utf-8",
            content_disposition='attachment; filename="article.md"',
            size=5,
            sha256="a" * 64,
            data=b"safe\n",
        )


class WebSecurityTests(unittest.TestCase):
    def test_boundary_requires_exact_loopback_host_and_origin(self) -> None:
        validate_browser_boundary(
            {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"},
            expected_host="127.0.0.1:8765",
            expected_origin="http://127.0.0.1:8765",
            mutation=True,
        )
        with self.assertRaises(SecurityError):
            validate_browser_boundary(
                {"Host": "localhost:8765", "Origin": "http://127.0.0.1:8765"},
                expected_host="127.0.0.1:8765",
                expected_origin="http://127.0.0.1:8765",
                mutation=True,
            )
        with self.assertRaises(SecurityError):
            validate_browser_boundary(
                {"Host": "127.0.0.1:8765"},
                expected_host="127.0.0.1:8765",
                expected_origin="http://127.0.0.1:8765",
                mutation=True,
            )

    def test_create_app_reports_missing_optional_flask_dependency(self) -> None:
        app_module = importlib.import_module("reportmachine_runner.web.app")
        original_import = builtins.__import__

        def deny_flask(name, *args, **kwargs):
            if name == "flask":
                raise ImportError("blocked for test")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=deny_flask):
            with self.assertRaises(app_module.WebDependencyError):
                app_module.create_app()


class WebAppTests(unittest.TestCase):
    def test_non_loopback_bind_is_rejected_before_server_creation(self) -> None:
        app_module = importlib.import_module("reportmachine_runner.web.app")
        with self.assertRaises(ValueError):
            app_module._validate_bind("0.0.0.0", 8765)

    def test_real_flask_boundary_bootstrap_csrf_and_closed_action(self) -> None:
        app_module = importlib.import_module("reportmachine_runner.web.app")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            supervisor = _FakeSupervisor(root / "state")
            app = app_module.create_app(
                root,
                bootstrap_capability="one-time-capability",
                supervisor=supervisor,
                status_reader=_FakeStatus(),
                artifact_broker=_FakeBroker(),
            )
            client = app.test_client()

            denied = client.get("/", headers={"Host": "localhost:8765"})
            self.assertEqual(denied.status_code, 403)
            bootstrap = client.post(
                "/bootstrap",
                json={"capability": "one-time-capability"},
                headers={"Host": EXPECTED_HOST, "Origin": EXPECTED_ORIGIN},
            )
            self.assertEqual(bootstrap.status_code, 200)
            csrf = bootstrap.get_json()["csrf_token"]
            cookie = bootstrap.headers["Set-Cookie"]
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=Strict", cookie)
            self.assertNotIn("one-time-capability", cookie)
            self.assertNotIn("Access-Control-Allow-Origin", bootstrap.headers)

            repeated = client.post(
                "/bootstrap",
                json={"capability": "one-time-capability"},
                headers={"Host": EXPECTED_HOST, "Origin": EXPECTED_ORIGIN},
            )
            self.assertEqual(repeated.status_code, 403)
            wrong_origin = client.post(
                "/api/action",
                json={"action": "prepare", "mode": "industry", "run_id": "safe_run"},
                headers={
                    "Host": EXPECTED_HOST,
                    "Origin": "http://evil.invalid",
                    "X-CSRF-Token": csrf,
                    "Idempotency-Key": "request:0001",
                },
            )
            self.assertEqual(wrong_origin.status_code, 403)
            missing_csrf = client.post(
                "/api/action",
                json={"action": "prepare", "mode": "industry", "run_id": "safe_run"},
                headers={
                    "Host": EXPECTED_HOST,
                    "Origin": EXPECTED_ORIGIN,
                    "Idempotency-Key": "request:0001",
                },
            )
            self.assertEqual(missing_csrf.status_code, 403)
            raw_argv = client.post(
                "/api/action",
                json={
                    "action": "prepare",
                    "mode": "industry",
                    "run_id": "safe_run",
                    "argv": ["sh", "-c", "id"],
                },
                headers={
                    "Host": EXPECTED_HOST,
                    "Origin": EXPECTED_ORIGIN,
                    "X-CSRF-Token": csrf,
                    "Idempotency-Key": "request:0001",
                },
            )
            self.assertEqual(raw_argv.status_code, 400)
            accepted = client.post(
                "/api/action",
                json={"action": "prepare", "mode": "industry", "run_id": "safe_run"},
                headers={
                    "Host": EXPECTED_HOST,
                    "Origin": EXPECTED_ORIGIN,
                    "X-CSRF-Token": csrf,
                    "Idempotency-Key": "request:0001",
                },
            )
            self.assertEqual(accepted.status_code, 202)
            self.assertEqual(supervisor.started.run_id, "safe_run")

    def test_real_flask_recovery_and_artifact_routes_remain_closed(self) -> None:
        app_module = importlib.import_module("reportmachine_runner.web.app")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            supervisor = _FakeSupervisor(root / "state")
            now = utc_now()
            supervisor.receipt = ExecutionReceipt(
                execution_id="01234567-89ab-4def-8123-456789abcdef",
                idempotency_key="request:0001",
                payload_digest="a" * 64,
                run_id="safe_run",
                action="run_all",
                state=OperationState.RECOVERY_REQUIRED,
                created_at=now,
                updated_at=now,
            )
            app = app_module.create_app(
                root,
                bootstrap_capability="recovery-capability",
                supervisor=supervisor,
                status_reader=_FakeStatus(),
                artifact_broker=_FakeBroker(),
            )
            client = app.test_client()
            bootstrap = client.post(
                "/bootstrap",
                json={"capability": "recovery-capability"},
                headers={"Host": EXPECTED_HOST, "Origin": EXPECTED_ORIGIN},
            )
            csrf = bootstrap.get_json()["csrf_token"]
            headers = {
                "Host": EXPECTED_HOST,
                "Origin": EXPECTED_ORIGIN,
                "X-CSRF-Token": csrf,
            }
            confused = client.post(
                "/api/recovery",
                json={
                    "action": "cancel",
                    "execution_id": "01234567-89ab-4def-8123-456789abcdef",
                },
                headers=headers,
            )
            self.assertEqual(confused.status_code, 400)
            recovered = client.post(
                "/api/recovery",
                json={
                    "execution_id": "01234567-89ab-4def-8123-456789abcdef",
                    "owner_lease_id": "b" * 32,
                },
                headers=headers,
            )
            self.assertEqual(recovered.status_code, 200)
            self.assertEqual(
                supervisor.recovered,
                ("01234567-89ab-4def-8123-456789abcdef", "b" * 32),
            )
            artifact = client.get(
                "/api/artifacts/safe_run/article",
                headers={"Host": EXPECTED_HOST},
            )
            self.assertEqual(artifact.status_code, 200)
            self.assertEqual(artifact.headers["Content-Disposition"], 'attachment; filename="article.md"')
            traversal = client.get(
                "/api/artifacts/..%2Fsecret/article",
                headers={"Host": EXPECTED_HOST},
            )
            self.assertIn(traversal.status_code, {404, 405})
