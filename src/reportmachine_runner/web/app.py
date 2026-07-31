"""Small server-rendered localhost control surface.

Flask is intentionally imported only by :func:`create_app`.  Importing the runner's
contracts, state store, or this module therefore never makes the optional Web stack a
core dependency.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from .contracts import ActionRequest, ContractError
from .security import BootstrapSessions, SecurityError, validate_browser_boundary
from .state import OperationState
from .supervisor import SingleSupervisor, SupervisorError, process_identity_is_live


LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
SESSION_COOKIE = "reportmachine_web_session"
CSRF_HEADER = "X-CSRF-Token"


class WebDependencyError(RuntimeError):
    """The optional local Web dependencies are not installed."""


def _flask_symbols() -> tuple[Any, ...]:
    try:
        from flask import Flask, Response, jsonify, render_template, request
    except ImportError as exc:  # pragma: no cover - varies with optional extras
        raise WebDependencyError(
            "The localhost Web control plane requires Flask. Install it with "
            "`python3 -m pip install -e \".[web]\"` and run scripts/run_web.py again."
        ) from exc
    return Flask, Response, jsonify, render_template, request


def _validate_bind(host: str, port: int) -> tuple[str, int]:
    if host != LOOPBACK_HOST:
        raise ValueError("Web control plane must bind exactly to 127.0.0.1")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")
    return host, port


def _json_value(value: Any) -> Any:
    """Convert only known in-process value shapes into Flask JSON values."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_mapping"):
        return _json_value(value.to_mapping())
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


def _load_optional_services(
    project_root: Path, supervisor: SingleSupervisor
) -> tuple[Any | None, Any | None, tuple[type[Exception], ...]]:
    """Load inspectors lazily while their optional module remains independently usable."""

    errors: list[type[Exception]] = []
    status_reader: Any | None = None
    artifact_broker: Any | None = None
    try:
        from .status import StatusReader

        # Construct the broker first below when available.  StatusReader can still create
        # its own guarded broker, but it must observe this Web supervisor's receipt.
        status_reader = StatusReader(
            project_root,
            supervisor=supervisor,
            owner_adapter=supervisor.owner_adapter,
        )
        errors.append(ValueError)
    except ImportError:
        pass
    try:
        from .artifacts import ArtifactBroker, ArtifactError

        artifact_broker = ArtifactBroker(project_root)
        errors.append(ArtifactError)
    except ImportError:
        pass
    return status_reader, artifact_broker, tuple(errors)


def create_app(
    project_root: Path | str | None = None,
    *,
    host: str = LOOPBACK_HOST,
    port: int = DEFAULT_PORT,
    bootstrap_capability: str | None = None,
    supervisor: SingleSupervisor | None = None,
    sessions: BootstrapSessions | None = None,
    status_reader: Any | None = None,
    artifact_broker: Any | None = None,
) -> Any:
    """Create the optional Flask app, constrained to a single loopback origin."""

    Flask, Response, jsonify, render_template, request = _flask_symbols()
    host, port = _validate_bind(host, port)
    root = Path(project_root or Path.cwd()).expanduser().resolve()
    expected_host = f"{host}:{port}"
    expected_origin = f"http://{expected_host}"
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config.update(
        SECRET_KEY=None,
        SESSION_COOKIE_NAME=SESSION_COOKIE,
        JSON_SORT_KEYS=True,
        MAX_CONTENT_LENGTH=64 * 1024,
        WEB_EXPECTED_HOST=expected_host,
        WEB_EXPECTED_ORIGIN=expected_origin,
    )
    active_sessions = sessions or BootstrapSessions(bootstrap_capability)
    active_supervisor = supervisor or SingleSupervisor(root)
    optional_errors: tuple[type[Exception], ...] = ()
    if status_reader is None or artifact_broker is None:
        loaded_status, loaded_artifacts, optional_errors = _load_optional_services(
            root, active_supervisor
        )
        status_reader = status_reader if status_reader is not None else loaded_status
        artifact_broker = artifact_broker if artifact_broker is not None else loaded_artifacts
    app.extensions["reportmachine_web"] = {
        "sessions": active_sessions,
        "supervisor": active_supervisor,
        "status_reader": status_reader,
        "artifact_broker": artifact_broker,
    }

    def api_error(message: str, status: int) -> Any:
        return jsonify({"error": message}), status

    def is_api_request() -> bool:
        return request.path.startswith("/api/") or request.path == "/bootstrap"

    @app.before_request
    def enforce_boundary() -> Any | None:
        mutation = request.method == "POST"
        try:
            validate_browser_boundary(
                request.headers,
                expected_host=expected_host,
                expected_origin=expected_origin,
                mutation=mutation,
            )
        except SecurityError as exc:
            if is_api_request():
                return api_error(str(exc), 403)
            return Response("Forbidden", status=403, mimetype="text/plain")
        if request.method not in {"GET", "POST", "HEAD"}:
            if is_api_request():
                return api_error("method not allowed", 405)
            return Response("Method Not Allowed", status=405, mimetype="text/plain")
        return None

    @app.after_request
    def harden_response(response: Any) -> Any:
        # Do not add Access-Control-Allow-* headers: this surface never enables CORS.
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'; object-src 'none'"
        )
        return response

    def session_csrf() -> str:
        return active_sessions.validate_session(request.cookies.get(SESSION_COOKIE))

    def require_session(*, csrf: bool = False) -> str:
        csrf_token = session_csrf()
        if csrf:
            active_sessions.validate_csrf(
                request.cookies.get(SESSION_COOKIE), request.headers.get(CSRF_HEADER)
            )
        return csrf_token

    def strict_json() -> Mapping[str, Any]:
        if request.mimetype != "application/json":
            raise ContractError("Content-Type must be application/json")
        payload = request.get_json(silent=True)
        if not isinstance(payload, Mapping):
            raise ContractError("request body must be a JSON object")
        return payload

    def stable_artifact(run_id: str, artifact: str) -> Any:
        receipt = active_supervisor.current_receipt()
        receipt_identity = (
            None
            if receipt is None
            else (receipt.execution_id, receipt.state.value, receipt.updated_at)
        )
        if receipt is not None and (
            receipt.state in {
                OperationState.QUEUED,
                OperationState.RUNNING,
                OperationState.CANCELLING,
            }
            or (
                receipt.state is OperationState.INTERRUPTED_UNKNOWN
                and process_identity_is_live(receipt.pid, receipt.process_birth_hint)
            )
        ):
            raise SupervisorError("artifact reads are blocked while a writer may be active")

        def owner_snapshot() -> dict[str, object] | None:
            owner, error = active_supervisor.owner_adapter.read_safely()
            if error is not None:
                raise SupervisorError("core writer state is unreadable")
            return owner

        before = owner_snapshot()
        if before is not None and before.get("state") != "recovery_required":
            pid = before.get("pid")
            if not isinstance(pid, int) or process_identity_is_live(pid, None):
                raise SupervisorError("artifact reads are blocked while a writer may be active")
        first = artifact_broker.read(run_id, artifact)
        after = owner_snapshot()
        if before != after:
            raise SupervisorError("writer state changed during artifact inspection; retry")
        payload = first
        if before is None:
            second = artifact_broker.read(run_id, artifact)
            final = owner_snapshot()
            if after != final or first.sha256 != second.sha256 or first.size != second.size:
                raise SupervisorError(
                    "artifact changed during inspection; retry after the writer settles"
                )
            payload = second
        final_receipt = active_supervisor.current_receipt()
        final_identity = (
            None
            if final_receipt is None
            else (
                final_receipt.execution_id,
                final_receipt.state.value,
                final_receipt.updated_at,
            )
        )
        if receipt_identity != final_identity:
            raise SupervisorError("operation state changed during artifact inspection; retry")
        return payload

    @app.post("/bootstrap")
    def bootstrap() -> Any:
        try:
            payload = strict_json()
            if set(payload) != {"capability"} or not isinstance(payload["capability"], str):
                raise ContractError("bootstrap body must contain only capability")
            grant = active_sessions.exchange(payload["capability"])
        except (ContractError, SecurityError) as exc:
            return api_error(str(exc), 400 if isinstance(exc, ContractError) else 403)
        response = jsonify({"csrf_token": grant.csrf_token})
        response.set_cookie(
            SESSION_COOKIE,
            grant.session_token,
            httponly=True,
            samesite="Strict",
            secure=False,  # The service is intentionally plain HTTP on loopback only.
            path="/",
        )
        return response

    @app.get("/")
    def index() -> Any:
        try:
            csrf = session_csrf()
        except SecurityError:
            csrf = None
        return render_template(
            "index.html",
            authenticated=csrf is not None,
            csrf_token=csrf or "",
            origin=expected_origin,
        )

    @app.get("/api/status")
    @app.get("/api/status/<run_id>")
    def status(run_id: str | None = None) -> Any:
        try:
            require_session()
            receipt = active_supervisor.current_receipt()
            selected_run_id = run_id or (receipt.run_id if receipt is not None else None)
            result: dict[str, Any] = {"execution": _json_value(receipt), "run": None}
            if selected_run_id is not None and status_reader is not None:
                result["run"] = _json_value(status_reader.read(selected_run_id))
            return jsonify(result)
        except SecurityError as exc:
            return api_error(str(exc), 403)
        except optional_errors as exc:
            return api_error(str(exc), 404)
        except SupervisorError as exc:
            return api_error(str(exc), 409)

    @app.get("/api/log")
    def log_tail() -> Any:
        try:
            require_session()
            # This is exclusively the supervisor-owned, redacted Web log; never Codex events.
            return jsonify({"log": active_supervisor.store.read_log_tail()})
        except SecurityError as exc:
            return api_error(str(exc), 403)
        except Exception as exc:  # State storage errors are deliberately not exposed verbatim.
            return api_error(f"Web log is unavailable: {type(exc).__name__}", 503)

    @app.get("/api/artifacts/<run_id>/<artifact>")
    def artifact(run_id: str, artifact: str) -> Any:
        try:
            require_session()
            if artifact_broker is None:
                return api_error("artifact inspection is unavailable in this installation", 503)
            payload = stable_artifact(run_id, artifact)
            response = Response(payload.data, content_type=payload.media_type)
            response.headers["Content-Disposition"] = payload.content_disposition
            response.headers["X-Artifact-SHA256"] = payload.sha256
            response.headers["X-Artifact-Name"] = payload.artifact.value
            return response
        except SecurityError as exc:
            return api_error(str(exc), 403)
        except optional_errors as exc:
            return api_error(str(exc), 404)
        except SupervisorError as exc:
            return api_error(str(exc), 409)

    @app.post("/api/action")
    def action() -> Any:
        try:
            require_session(csrf=True)
            action_request = ActionRequest.from_mapping(strict_json())
            receipt = active_supervisor.start(
                action_request,
                idempotency_key=request.headers.get("Idempotency-Key"),
            )
            return jsonify({"execution": _json_value(receipt)}), 202
        except SecurityError as exc:
            return api_error(str(exc), 403)
        except ContractError as exc:
            return api_error(str(exc), 400)
        except SupervisorError as exc:
            return api_error(str(exc), 409)

    @app.post("/api/cancel")
    def cancel() -> Any:
        try:
            require_session(csrf=True)
            payload = strict_json()
            if set(payload) != {"execution_id"}:
                raise ContractError("cancel body must contain only execution_id")
            cancellation = ActionRequest.from_mapping({"action": "cancel", **payload})
            assert cancellation.execution_id is not None
            receipt = active_supervisor.cancel(cancellation.execution_id)
            return jsonify({"execution": _json_value(receipt)}), 202
        except SecurityError as exc:
            return api_error(str(exc), 403)
        except ContractError as exc:
            return api_error(str(exc), 400)
        except SupervisorError as exc:
            return api_error(str(exc), 409)

    @app.post("/api/recovery")
    def recovery() -> Any:
        try:
            require_session(csrf=True)
            payload = strict_json()
            if not set(payload).issubset({"execution_id", "owner_lease_id"}):
                raise ContractError(
                    "recovery body may contain only execution_id and owner_lease_id"
                )
            recovery_request = ActionRequest.from_mapping(
                {**payload, "action": "recover"}
            )
            assert recovery_request.execution_id is not None
            receipt = active_supervisor.acknowledge_recovery(
                recovery_request.execution_id,
                owner_lease_id=recovery_request.owner_lease_id,
            )
            return jsonify({"execution": _json_value(receipt)}), 200
        except SecurityError as exc:
            return api_error(str(exc), 403)
        except ContractError as exc:
            return api_error(str(exc), 400)
        except SupervisorError as exc:
            return api_error(str(exc), 409)

    return app


__all__ = [
    "CSRF_HEADER",
    "DEFAULT_PORT",
    "LOOPBACK_HOST",
    "SESSION_COOKIE",
    "WebDependencyError",
    "create_app",
]
