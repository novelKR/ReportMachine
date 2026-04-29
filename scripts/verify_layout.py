#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

if __package__:
    from .layout_contract import verify_layout_contract
else:
    from layout_contract import verify_layout_contract


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the ReportMachine repository layout.")
    parser.add_argument(
        "--release",
        action="store_true",
        help="Also fail on generated artifacts that must not be included in release archives.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    errors = verify_layout_contract(root, release=args.release)
    if errors:
        print("Layout verification failed:")
        for error in errors:
            print(f" - {error}")
        return 1

    print("Layout OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
