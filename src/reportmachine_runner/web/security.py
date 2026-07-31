"""Security primitives for the loopback-only Web surface.

These helpers do not depend on Flask.  The Web application is responsible for translating
headers and cookies into these small validation calls.
"""

from __future__ import annotations

import hmac
import re
import secrets
import threading
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit


class SecurityError(PermissionError):
    """A browser request failed the loopback security contract."""


ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
C0_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# This is intentionally a pragmatic log boundary, not a claim that arbitrary future secret
# formats can be discovered. Values following common credential names and common bearer/API
# token shapes are removed before text reaches the Web-owned log.
QUOTED_KEY_VALUE_SECRET = re.compile(
    r"(?i)([\"'](?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|passwd|secret|session|csrf|cookie)[\"']\s*:\s*[\"']?)([^\"'\s,;}]+)"
)
KEY_VALUE_SECRET = re.compile(
    r"(?i)(\b(?:[a-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|passwd|secret|session|csrf|cookie)\b\s*[:=]\s*)([^\s,;]+)"
)
BEARER_SECRET = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+\-/=]+")
OPENAI_STYLE_SECRET = re.compile(
    r"\b(?:sk(?:[-_]proj)?[-_]|sess[-_]|pat[-_])[A-Za-z0-9_-]{12,}\b"
)
LONG_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_-]{5,})?\b")


def redact_log_text(value: str | bytes) -> str:
    """Remove terminal controls and common secret shapes from Web-visible log text."""

    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = value
    text = ANSI_ESCAPE.sub("", text)
    text = C0_CONTROL.sub("", text)
    text = BEARER_SECRET.sub(lambda match: f"{match.group(1)} [REDACTED]", text)
    text = QUOTED_KEY_VALUE_SECRET.sub(
        lambda match: f"{match.group(1)}[REDACTED]",
        text,
    )
    text = KEY_VALUE_SECRET.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    text = OPENAI_STYLE_SECRET.sub("[REDACTED]", text)
    text = LONG_JWT.sub("[REDACTED]", text)
    return text


def exact_origin_from_referer(referer: str) -> str | None:
    try:
        parsed = urlsplit(referer)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def validate_browser_boundary(
    headers: Mapping[str, str],
    *,
    expected_host: str,
    expected_origin: str,
    mutation: bool,
) -> None:
    """Enforce exact Host and same-origin mutation headers.

    Forwarding headers are intentionally ignored. A missing Origin is accepted only when
    the Referer's parsed origin is exactly the configured loopback origin.
    """

    host = headers.get("Host", "")
    if not hmac.compare_digest(host, expected_host):
        raise SecurityError("invalid Host header")
    if not mutation:
        return
    origin = headers.get("Origin")
    if origin is not None:
        if origin == "null" or not hmac.compare_digest(origin, expected_origin):
            raise SecurityError("invalid Origin header")
        return
    referer = headers.get("Referer")
    if referer is None:
        raise SecurityError("Origin or Referer is required for mutation")
    referer_origin = exact_origin_from_referer(referer)
    if referer_origin is None or not hmac.compare_digest(referer_origin, expected_origin):
        raise SecurityError("invalid Referer header")


@dataclass(frozen=True)
class SessionGrant:
    session_token: str
    csrf_token: str


class BootstrapSessions:
    """One-time bootstrap capability and per-process in-memory browser sessions."""

    def __init__(self, bootstrap_capability: str | None = None) -> None:
        self._bootstrap = bootstrap_capability or secrets.token_urlsafe(32)
        self._bootstrap_consumed = False
        self._sessions: dict[str, str] = {}
        self._lock = threading.Lock()

    @property
    def bootstrap_capability(self) -> str:
        return self._bootstrap

    @property
    def bootstrap_consumed(self) -> bool:
        with self._lock:
            return self._bootstrap_consumed

    def exchange(self, capability: str) -> SessionGrant:
        if not isinstance(capability, str) or not capability:
            raise SecurityError("bootstrap capability is required")
        with self._lock:
            if self._bootstrap_consumed:
                raise SecurityError("bootstrap capability has already been consumed")
            if not hmac.compare_digest(capability, self._bootstrap):
                raise SecurityError("invalid bootstrap capability")
            self._bootstrap_consumed = True
            # Remove our retained capability immediately after the one successful exchange.
            self._bootstrap = ""
            session = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(32)
            self._sessions[session] = csrf
            return SessionGrant(session_token=session, csrf_token=csrf)

    def validate_session(self, session_token: str | None) -> str:
        if not session_token:
            raise SecurityError("authenticated session is required")
        with self._lock:
            expected = self._sessions.get(session_token)
        if expected is None:
            raise SecurityError("invalid session")
        return expected

    def validate_csrf(self, session_token: str | None, csrf_token: str | None) -> None:
        expected = self.validate_session(session_token)
        if not csrf_token or not hmac.compare_digest(csrf_token, expected):
            raise SecurityError("invalid CSRF token")

    def revoke(self, session_token: str | None) -> None:
        if not session_token:
            return
        with self._lock:
            self._sessions.pop(session_token, None)


__all__ = [
    "BootstrapSessions",
    "SecurityError",
    "SessionGrant",
    "exact_origin_from_referer",
    "redact_log_text",
    "validate_browser_boundary",
]
