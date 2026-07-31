from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reportmachine_runner.web.contracts import (
    Action,
    ActionRequest,
    ContractError,
    build_runner_argv,
    validate_idempotency_key,
)
from reportmachine_runner.web.security import SecurityError, validate_browser_boundary
from reportmachine_runner.web.state import ExecutionReceipt, OperationState, StateStore, utc_now


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class WebContractTests(unittest.TestCase):
    def request(self, **overrides: object) -> ActionRequest:
        payload = {
            "action": "run_all",
            "mode": "industry",
            "run_id": "web_test",
            "topic": "반도체 공급망",
        }
        payload.update(overrides)
        for name, value in overrides.items():
            if value is None:
                payload.pop(name, None)
        return ActionRequest.from_mapping(payload)

    def test_request_contract_rejects_unknown_forbidden_and_irrelevant_fields(self) -> None:
        cases = (
            ({"action": "run_all", "mode": "industry", "run_id": "web_test", "argv": "x"}, "forbidden"),
            ({"action": "run_all", "mode": "industry", "run_id": "web_test", "surprise": "x"}, "unknown"),
            ({"action": "audit", "mode": "industry", "run_id": "web_test", "topic": "x"}, "not valid"),
            ({"action": "allowed_phase", "mode": "industry", "run_id": "web_test", "phase": "all"}, "phase must"),
            ({"action": "prepare", "mode": "industry", "run_id": "latest"}, "reserved"),
            ({"action": "cancel", "execution_id": "01234567-89ab-4def-8123-456789abcdef", "mode": "industry"}, "not valid"),
            ({"action": "recover", "execution_id": "01234567-89ab-4def-8123-456789abcdef", "owner_lease_id": "A" * 32}, "lowercase"),
            ({"action": "approve_scope", "mode": "industry", "run_id": "web_test", "scope_sha256": "A" * 64}, "lowercase"),
            ({"action": "prepare", "mode": "industry", "run_id": "web_test", "confirm_rerun": "yes"}, "boolean"),
        )
        for payload, error in cases:
            with self.subTest(payload=payload), self.assertRaisesRegex(ContractError, error):
                ActionRequest.from_mapping(payload)

        with self.assertRaisesRegex(ContractError, "surrounding whitespace"):
            ActionRequest.from_mapping(
                {"action": "prepare", "mode": "industry", "run_id": "web_test", "topic": " topic"}
            )
        with self.assertRaisesRegex(ContractError, "control character"):
            ActionRequest.from_mapping(
                {"action": "prepare", "mode": "industry", "run_id": "web_test", "topic": "bad\x01topic"}
            )

    def test_fixed_runner_argv_for_each_server_owned_action(self) -> None:
        script = str(PROJECT_ROOT / "scripts" / "run_article.py")
        prefix = (
            "/usr/bin/python3",
            script,
            "--mode",
            "industry",
            "--run-id",
            "web_test",
        )
        cases = (
            (self.request(action="smoke"), ("--intake-topic", "반도체 공급망", "--intake-original-request", "반도체 공급망", "--smoke-test")),
            (self.request(action="prepare"), ("--intake-topic", "반도체 공급망", "--intake-original-request", "반도체 공급망", "--prepare-only")),
            (self.request(), ("--intake-topic", "반도체 공급망", "--intake-original-request", "반도체 공급망", "--phase", "all")),
            (
                self.request(action="allowed_phase", phase="research", topic=None),
                ("--phase", "research"),
            ),
            (self.request(action="audit", topic=None), ("--phase", "audit")),
            (
                self.request(action="approve_scope", topic=None, scope_sha256="a" * 64),
                ("--intake-approve-scope-only", "--intake-approve-scope-sha256", "a" * 64),
            ),
        )
        for request, suffix in cases:
            with self.subTest(action=request.action):
                self.assertEqual(
                    build_runner_argv(
                        request,
                        project_root=PROJECT_ROOT,
                        python_executable="/usr/bin/python3",
                    ),
                    prefix + suffix,
                )

        cancel = ActionRequest.from_mapping(
            {"action": "cancel", "execution_id": "01234567-89ab-4def-8123-456789abcdef"}
        )
        with self.assertRaisesRegex(ContractError, "no runner argv"):
            build_runner_argv(cancel, project_root=PROJECT_ROOT, python_executable="/usr/bin/python3")
        recovery = ActionRequest.from_mapping(
            {
                "action": "recover",
                "execution_id": "01234567-89ab-4def-8123-456789abcdef",
                "owner_lease_id": "b" * 32,
            }
        )
        with self.assertRaisesRegex(ContractError, "no runner argv"):
            build_runner_argv(
                recovery,
                project_root=PROJECT_ROOT,
                python_executable="/usr/bin/python3",
            )
        confirmed = ActionRequest.from_mapping(
            {
                "action": "allowed_phase",
                "mode": "industry",
                "run_id": "web_test",
                "phase": "review",
                "confirm_rerun": True,
            }
        )
        self.assertTrue(confirmed.confirm_rerun)
        self.assertEqual(confirmed.requested_phase(), "review")

    def test_idempotency_key_is_required_and_closed_format(self) -> None:
        self.assertEqual(validate_idempotency_key("request:0001"), "request:0001")
        for value in (None, "short", "contains space", "x" * 129):
            with self.subTest(value=value), self.assertRaises(ContractError):
                validate_idempotency_key(value)

    def test_state_receipt_is_replaced_atomically_mode_0600_and_log_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = StateStore(PROJECT_ROOT, state_directory=Path(temp_dir) / "web-state")
            now = utc_now()
            queued = ExecutionReceipt(
                execution_id="01234567-89ab-4def-8123-456789abcdef",
                idempotency_key="request:0001",
                payload_digest="a" * 64,
                run_id="web_test",
                action="run_all",
                state=OperationState.QUEUED,
                created_at=now,
                updated_at=now,
            )
            with patch("reportmachine_runner.web.state.os.replace", wraps=os.replace) as replace:
                store.write(queued)
            replace.assert_called_once()
            store.write(queued.transition(OperationState.EXITED, exit_code=0, finished_at=utc_now()))

            self.assertEqual(store.load().state, OperationState.EXITED)
            self.assertEqual(stat.S_IMODE(store.receipt_path.stat().st_mode), 0o600)
            self.assertEqual(json.loads(store.receipt_path.read_text(encoding="utf-8"))["state"], "exited")
            self.assertFalse(list(store.directory.glob(".receipt.*.tmp")))

            secrets = (
                "\x1b[31mAuthorization: Bearer secret-token\x1b[0m "
                "api_key=sk_123456789012 "
                '{"password":"hunter2","access_token":"abc123"} '
                "OPENAI_API_KEY=sk-proj-abcdefghijklmnop "
                "sk-standalonesecretvalue\n"
            )
            store.append_log(secrets)
            log = store.read_log_tail()
            raw_log = store.log_path.read_text(encoding="utf-8")
            self.assertIn("[REDACTED]", log)
            for secret in (
                "secret-token",
                "sk_123456789012",
                "hunter2",
                "abc123",
                "sk-proj-abcdefghijklmnop",
                "sk-standalonesecretvalue",
            ):
                self.assertNotIn(secret, raw_log)
                self.assertNotIn(secret, log)
            self.assertNotIn("\x1b", log)
            self.assertEqual(stat.S_IMODE(store.log_path.stat().st_mode), 0o600)

    def test_mutating_browser_requests_require_exact_loopback_origin(self) -> None:
        headers = {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"}
        validate_browser_boundary(
            headers,
            expected_host="127.0.0.1:8765",
            expected_origin="http://127.0.0.1:8765",
            mutation=True,
        )
        with self.assertRaisesRegex(SecurityError, "invalid Origin"):
            validate_browser_boundary(
                {"Host": "127.0.0.1:8765", "Origin": "http://evil.example"},
                expected_host="127.0.0.1:8765",
                expected_origin="http://127.0.0.1:8765",
                mutation=True,
            )
        with self.assertRaisesRegex(SecurityError, "invalid Host"):
            validate_browser_boundary(
                {"Host": "localhost:8765", "Origin": "http://127.0.0.1:8765"},
                expected_host="127.0.0.1:8765",
                expected_origin="http://127.0.0.1:8765",
                mutation=True,
            )


if __name__ == "__main__":
    unittest.main()
