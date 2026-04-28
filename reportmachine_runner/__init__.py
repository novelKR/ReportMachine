"""Source-tree shim for `python3 -m reportmachine_runner`.

The installable package lives in `src/reportmachine_runner`. This shim keeps
direct module execution working from an uninstalled checkout.
"""

from __future__ import annotations

from pathlib import Path

_SRC_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "reportmachine_runner"
if _SRC_PACKAGE.is_dir():
    __path__.append(str(_SRC_PACKAGE))
