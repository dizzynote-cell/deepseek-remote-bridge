# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
"""The top-left logo switch: which entries are offered, and when they are dead."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config import BridgeConfig
from app.state import BridgeState, _safe_sibling_url


def build_state(**overrides) -> tuple[BridgeState, tempfile.TemporaryDirectory]:
    directory = tempfile.TemporaryDirectory()
    home = Path(directory.name)
    config = BridgeConfig(root=home, home=home, web_dir=home, **overrides)
    return BridgeState(config), directory


class SiblingUrlTests(unittest.TestCase):
    def test_accepts_only_explicit_http_targets(self):
        self.assertEqual(_safe_sibling_url("https://bridge.example.com/"), "https://bridge.example.com/")
        self.assertEqual(_safe_sibling_url("http://127.0.0.1:8765/"), "http://127.0.0.1:8765/")
        for bad in ("", "   ", "javascript:alert(1)", "/relative", "file:///C:/x", "ftp://host/", "https://"):
            self.assertIsNone(_safe_sibling_url(bad), bad)


class LogoMenuTests(unittest.TestCase):
    def test_unconfigured_sibling_is_disabled_without_a_url(self):
        state, directory = build_state()
        try:
            menu = state.logo_menu()
            self.assertTrue(menu["enabled"])
            self.assertEqual(menu["current"], "deepseek-harness")
            current, sibling = menu["targets"]
            self.assertTrue(current["current"] and current["enabled"])
            self.assertFalse(sibling["enabled"])
            self.assertIsNone(sibling["url"])
            self.assertEqual(sibling["reason"], "not-configured")
        finally:
            directory.cleanup()

    def test_configured_sibling_becomes_a_real_target(self):
        state, directory = build_state(
            sibling_provider="codex",
            sibling_name="Codex",
            sibling_url="https://entry.example.com/",
        )
        try:
            sibling = state.logo_menu()["targets"][1]
            self.assertTrue(sibling["enabled"])
            self.assertEqual(sibling["url"], "https://entry.example.com/")
            self.assertIsNone(sibling["reason"])
            self.assertEqual(sibling["displayName"], "Codex")
        finally:
            directory.cleanup()

    def test_unsafe_url_keeps_the_entry_disabled(self):
        state, directory = build_state(sibling_url="javascript:alert(1)")
        try:
            sibling = state.logo_menu()["targets"][1]
            self.assertFalse(sibling["enabled"])
            self.assertIsNone(sibling["url"])
        finally:
            directory.cleanup()

    def test_bridge_info_exposes_the_same_menu(self):
        state, directory = build_state(sibling_url="https://entry.example.com/dsh/")
        try:
            info = state.bridge_info()
            self.assertEqual(info["contractVersion"], 1)
            self.assertEqual(info["providerId"], "deepseek-harness")
            self.assertEqual(info["logoMenu"]["targets"][1]["url"], "https://entry.example.com/dsh/")
            self.assertIn("permissionSwitch", info["capabilities"])
        finally:
            directory.cleanup()


if __name__ == "__main__":
    unittest.main()
