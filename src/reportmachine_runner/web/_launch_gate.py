"""POSIX start barrier for the server-owned Web runner child."""

from __future__ import annotations

import os
import sys
from uuid import UUID


START_FD_ENV = "REPORTMACHINE_WEB_START_FD"
WEB_EXECUTION_ID_ENV = "REPORTMACHINE_WEB_EXECUTION_ID"
START_PERMIT = b"\x01"
GATE_DENIED_EXIT_CODE = 125
EXEC_FAILED_EXIT_CODE = 126


def _deny(message: str) -> int:
    try:
        os.write(2, f"reportmachine Web start gate denied: {message}\n".encode("ascii"))
    except OSError:
        pass
    return GATE_DENIED_EXIT_CODE


def _valid_execution_id(value: str | None) -> bool:
    if not value:
        return False
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        return False
    return parsed.version == 4 and str(parsed) == value


def main(argv: list[str] | None = None) -> int:
    """Wait for one parent permit byte, then replace this process with fixed argv."""

    runner_argv = list(sys.argv[1:] if argv is None else argv)
    raw_fd = os.environ.pop(START_FD_ENV, None)
    if os.name != "posix":
        return _deny("POSIX start barrier is unavailable")
    if not _valid_execution_id(os.environ.get(WEB_EXECUTION_ID_ENV)):
        return _deny("invalid execution identity")
    if not runner_argv or not os.path.isabs(runner_argv[0]):
        return _deny("invalid fixed runner argv")
    try:
        read_fd = int(raw_fd) if raw_fd is not None else -1
    except ValueError:
        return _deny("invalid barrier descriptor")
    if read_fd < 3:
        return _deny("invalid barrier descriptor")

    try:
        try:
            permit = os.read(read_fd, 1)
        except OSError:
            return _deny("barrier read failed")
    finally:
        try:
            os.close(read_fd)
        except OSError:
            pass
    if permit != START_PERMIT:
        return _deny("parent permit was not committed")

    try:
        os.execve(runner_argv[0], runner_argv, dict(os.environ))
    except OSError:
        try:
            os.write(2, b"reportmachine Web runner exec failed\n")
        except OSError:
            pass
        return EXEC_FAILED_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main())
