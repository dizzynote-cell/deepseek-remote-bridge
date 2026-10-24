# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
# Project: DeepSeek Remote Bridge
"""Small bridge-local preference store (currently the voice button toggle)."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    # When the provider is configured, showing the buttons by default is the
    # least surprising behaviour; users can turn it off in the settings dialog.
    "voiceEnabled": True,
    # Preset applied to sessions created from the page. Empty means "use the
    # bridge configuration default"; it is validated against the live catalog
    # before use, never trusted blindly.
    "permissionPreset": "",
}


class Preferences:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            values = dict(DEFAULTS)
            try:
                stored = json.loads(self.path.read_text(encoding="utf-8"))
            except (FileNotFoundError, ValueError, OSError):
                return values
            if isinstance(stored, dict):
                for key, default in DEFAULTS.items():
                    if isinstance(stored.get(key), type(default)):
                        values[key] = stored[key]
            return values

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            values = self.snapshot()
            for key, default in DEFAULTS.items():
                if key in patch and isinstance(patch[key], type(default)):
                    values[key] = patch[key]
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(values, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(temporary, self.path)
            return values
