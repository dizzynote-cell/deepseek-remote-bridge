#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
# Project: DeepSeek Remote Bridge
"""Launch the DeepSeek Remote Bridge.

Usage:
    python bridge.py                 # serve with config/.env
    python bridge.py --port 8767     # override the listen port
    python bridge.py --base-path /dsh/
    python bridge.py --check         # start, verify the runtime, exit

The script adds its own directory to ``sys.path`` first so it also runs under
Python distributions whose ``._pth`` file disables the usual script-directory
entry (the embedded runtime shipped with DeepSeek Harness, for example).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import main  # noqa: E402 - path set above

if __name__ == "__main__":
    sys.exit(main())
