# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
"""Configuration parsing, base-path handling, and patch rendering."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config import BridgeConfig, config_problems, load_config
from app.dsh_runtime import RenderError, render_patch


class ConfigTests(unittest.TestCase):
    def test_base_path_normalization(self):
        cases = {
            "/": "/",
            "": "/",
            "/dsh": "/dsh/",
            "/dsh/": "/dsh/",
            "dsh": "/dsh/",
            "//": "/",
        }
        base = BridgeConfig(root=Path("."), home=Path("."), web_dir=Path("."))
        for raw, expected in cases.items():
            config = BridgeConfig(root=Path("."), home=Path("."), web_dir=Path("."), base_path=raw)
            self.assertEqual(config.normalized_base_path, expected, raw)
        self.assertEqual(base.normalized_base_path, "/")

    def test_external_url_respects_base_path_and_loopback(self):
        config = BridgeConfig(root=Path("."), home=Path("."), web_dir=Path("."), port=9000, base_path="/dsh/")
        self.assertEqual(config.external_url("api/health"), "http://127.0.0.1:9000/dsh/api/health")
        wildcard = BridgeConfig(root=Path("."), home=Path("."), web_dir=Path("."), host="0.0.0.0", port=9000)
        self.assertTrue(wildcard.external_url().startswith("http://127.0.0.1:9000/"))

    def test_environment_beats_defaults(self):
        import os

        previous = {key: os.environ.get(key) for key in ("DSH_BRIDGE_PORT", "DSH_BRIDGE_BASE_PATH")}
        try:
            os.environ["DSH_BRIDGE_PORT"] = "9911"
            os.environ["DSH_BRIDGE_BASE_PATH"] = "/x/"
            config = load_config()
            self.assertEqual(config.port, 9911)
            self.assertEqual(config.normalized_base_path, "/x/")
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_config_problems_reports_missing_runtime(self):
        config = BridgeConfig(
            root=Path("."),
            home=Path("."),
            web_dir=Path("."),
            dsh_exe=Path("Z:/missing/DeepSeek Harness.exe"),
            dsh_cli=Path("Z:/missing/cli.js"),
        )
        problems = config_problems(config)
        self.assertEqual(len(problems), 2)
        self.assertIn("DSH_EXE", problems[0])
        self.assertIn("DSH_CLI", problems[1])

    def test_config_problems_accepts_paths_inside_an_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "app.asar"
            archive.write_bytes(b"asar")
            config = BridgeConfig(
                root=Path("."),
                home=Path("."),
                web_dir=Path("."),
                dsh_exe=Path("."),
                dsh_cli=archive / "dsh/node_modules/@deepseek-ai/dsh-desktop-host/lib/cli.js",
            )
            # The archive exists, so the CLI path is considered present.
            problems = [problem for problem in config_problems(config) if "DSH_CLI" in problem]
            self.assertEqual(problems, [])

    def test_tokens_are_persistent_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            config = BridgeConfig(root=Path("."), home=Path(directory), web_dir=Path("."))
            config.ensure_dirs()
            first = config.adapter_token
            second = config.adapter_token
            self.assertEqual(first, second)
            self.assertGreaterEqual(len(first), 32)
            self.assertNotEqual(config.adapter_token, config.session_token)


class PatchTests(unittest.TestCase):
    def payload(self, **overrides):
        base = {
            "adapter": {
                "file": r"D:\bridge\adapter\index.js",
                "port": 4567,
                "token": "secret-token",
                "clientVersion": "1.2.3",
                "approvalTimeoutMs": 900000,
                "questionTimeoutMs": 900000,
            },
            "preset": "workspace-write",
        }
        base.update(overrides)
        return base

    def test_patch_mounts_adapter_and_question_tool(self):
        text = render_patch(self.payload())
        self.assertIn("- insert:", text)
        self.assertIn("file:///D:/bridge/adapter/index.js", text)
        self.assertIn("port: 4567", text)
        self.assertIn("token: 'secret-token'", text)
        self.assertIn("@deepseek-ai/dsh-tool-ask-user", text)
        self.assertIn("@deepseek-ai/dsh-tool-present", text)
        self.assertIn("defaultPreset: 'workspace-write'", text)

    def test_patch_omits_preset_when_unset(self):
        text = render_patch(self.payload(preset=""))
        self.assertNotIn("defaultPreset", text)

    def test_patch_rejects_injection_in_token(self):
        with self.assertRaises(RenderError):
            render_patch(self.payload(adapter={**self.payload()["adapter"], "token": "a'\n- id: evil"}))
        with self.assertRaises(RenderError):
            render_patch(self.payload(preset="workspace-write\n- id: evil"))

class PidFileTests(unittest.TestCase):
    def test_pid_file_is_written_and_cleared(self):
        import os

        from app.main import _clear_pid, _write_pid

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bridge.pid"
            _write_pid(path)
            self.assertEqual(path.read_text(encoding="ascii").strip(), str(os.getpid()))
            _clear_pid(path)
            self.assertFalse(path.exists())

    def test_clear_leaves_a_foreign_pid_file(self):
        from app.main import _clear_pid

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bridge.pid"
            path.write_text("1", encoding="ascii")
            _clear_pid(path)
            self.assertTrue(path.exists())

if __name__ == "__main__":
    unittest.main()
