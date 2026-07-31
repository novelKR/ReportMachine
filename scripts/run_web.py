#!/usr/bin/env python3
"""Serve the optional ReportMachine loopback Web control plane with Waitress."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from reportmachine_runner.web.app import DEFAULT_PORT, LOOPBACK_HOST, WebDependencyError, create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve ReportMachine's local Web control plane.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        app = create_app(
            ROOT,
            host=LOOPBACK_HOST,
            port=args.port,
        )
    except (ValueError, WebDependencyError) as exc:
        print(f"error: {exc}")
        return 2
    try:
        from waitress import serve
    except ImportError:
        print(
            'error: local Web serving requires the optional Web extra. '
            'Install `python3 -m pip install -e ".[web]"` and retry.'
        )
        return 2
    capability = app.extensions["reportmachine_web"]["sessions"].bootstrap_capability
    print(
        f"Open http://{LOOPBACK_HOST}:{args.port}/ and enter this one-time capability: {capability}",
        flush=True,
    )
    serve(app, host=LOOPBACK_HOST, port=args.port, threads=4)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
