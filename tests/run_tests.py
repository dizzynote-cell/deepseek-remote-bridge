#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
"""Run every offline test, then the Node-side UI tests.

Usage:
    python tests/run_tests.py              # everything offline
    python tests/run_tests.py -k balance   # one module

Live harness checks are separate and need a running bridge:
    python tests/live_check.py --base http://127.0.0.1:8766
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

NODE_TESTS = ("test_markdown.cjs", "test_ui_contract.cjs")


def run_python(argv: list[str]) -> int:
    loader = unittest.TestLoader()
    if "-k" in argv:
        pattern = argv[argv.index("-k") + 1]
        suite = loader.discover(str(ROOT / "tests"), pattern=f"test_*{pattern}*.py", top_level_dir=str(ROOT))
    else:
        suite = loader.discover(str(ROOT / "tests"), pattern="test_*.py", top_level_dir=str(ROOT))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


def run_node() -> int:
    failures = 0
    for name in NODE_TESTS:
        path = ROOT / "tests" / name
        if not path.is_file():
            print(f"[skip] {name} is missing")
            continue
        print(f"\n=== node {name} ===", flush=True)
        node = _find_node()
        if node is None:
            print("[skip] node.exe not found; UI tests need Node.js")
            continue
        result = subprocess.run([node, str(path)], cwd=str(ROOT))
        failures += 0 if result.returncode == 0 else 1
    return failures


def _find_node() -> str | None:
    import shutil

    for candidate in (
        shutil.which("node"),
        r"C:\Program Files\nodejs\node.exe",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def main(argv: list[str]) -> int:
    python_status = run_python(argv)
    node_status = run_node()
    if python_status or node_status:
        print("\nFAILED")
        return 1
    print("\nAll offline tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
