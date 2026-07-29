from __future__ import annotations

import importlib
import unittest


_SPLIT_MODULES = [
    "tests.test_cli_smoke",
    "tests.test_cli_prepare",
    "tests.test_cli_execution",
    "tests.test_cli_artifacts",
    "tests.test_cli_summary_integration",
    "tests.test_cli_prerequisites",
]


def load_tests(loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None) -> unittest.TestSuite:
    if pattern not in (None, "test_cli.py"):
        return tests
    suite = unittest.TestSuite()
    for module_name in _SPLIT_MODULES:
        suite.addTests(loader.loadTestsFromModule(importlib.import_module(module_name)))
    return suite
