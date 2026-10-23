# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
"""Release hygiene: no private paths, credentials, sessions, or harness binaries.

The check runs over the files that would be published, so a stray local value is
caught before a commit rather than after a push.
"""

from __future__ import annotations

import fnmatch
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Directories that never enter the public tree.
EXCLUDED_DIRS = {
    ".git",
    "state",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "integration-workspace",
    "spike",
}

EXCLUDED_FILES = {"config/.env", "BASELINE_FILES.txt"}

TEXT_SUFFIXES = {
    ".py",
    ".js",
    ".cjs",
    ".mjs",
    ".css",
    ".html",
    ".md",
    ".json",
    ".yml",
    ".yaml",
    ".ps1",
    ".cmd",
    ".txt",
    ".example",
    "",
}

FORBIDDEN_PATTERNS = [
    (re.compile(r"C:\\Users\\[A-Za-z0-9._-]+", re.IGNORECASE), "absolute private user path"),
    (re.compile(r"C:\\Users\\", re.IGNORECASE), "absolute private user path"),
    (re.compile(r"D:\\" + "Deepseek" + "work", re.IGNORECASE), "private development path"),
    (re.compile(r"D:\\DSH\\", re.IGNORECASE), "local harness install path"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "GitHub token"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "API key"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(r"VOLC_API_KEY\s*=\s*[A-Za-z0-9]{8,}"), "voice provider key"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "private key"),
]

#: Synthetic placeholders that are allowed to look like identifiers.
ALLOWED_UUIDS = {"00000000-0000-4000-8000-000000000001"}
SESSION_ID = re.compile(r"session-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

BINARY_SUFFIXES = {".exe", ".dll", ".asar", ".node", ".zip", ".7z", ".pak", ".bin", ".pyc"}


def published_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if str(relative).replace("\\", "/") in EXCLUDED_FILES:
            continue
        files.append(relative)
    return files


class DistributionTests(unittest.TestCase):
    def test_no_private_paths_or_secrets(self):
        problems: list[str] = []
        for relative in published_files():
            path = ROOT / relative
            if path.suffix.lower() in BINARY_SUFFIXES:
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES and path.suffix:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for pattern, label in FORBIDDEN_PATTERNS:
                if pattern.search(text):
                    problems.append(f"{relative}: {label}")
        self.assertEqual(problems, [], "private data found: " + "; ".join(problems))

    def test_no_foreign_session_identifiers(self):
        problems: list[str] = []
        for relative in published_files():
            path = ROOT / relative
            if path.suffix.lower() in BINARY_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for match in SESSION_ID.finditer(text):
                identifier = match.group(0)
                if identifier.removeprefix("session-") not in ALLOWED_UUIDS:
                    problems.append(f"{relative}: {identifier}")
        self.assertEqual(problems, [], "foreign session ids: " + "; ".join(problems))

    def test_no_harness_binaries_are_vendored(self):
        offenders = [str(path) for path in published_files() if path.suffix.lower() in BINARY_SUFFIXES]
        self.assertEqual(offenders, [])

    def test_required_release_files_exist(self):
        for name in ("LICENSE", "VERSION", "README.md", "CHANGELOG.md", ".gitignore", "requirements.txt"):
            self.assertTrue((ROOT / name).is_file(), name)
        self.assertTrue((ROOT / "config" / ".env.example").is_file())
        self.assertTrue((ROOT / "adapter" / "index.js").is_file())
        self.assertTrue((ROOT / "bridge.py").is_file())

    def test_local_secrets_are_ignored(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        ignored = [line.strip() for line in ignored if line.strip() and not line.startswith("#")]
        for required in ("config/.env", "state/", "*.pem", "*.key", "logs/"):
            self.assertTrue(
                any(fnmatch.fnmatch(required, pattern.rstrip("/")) or pattern.rstrip("/") in required for pattern in ignored),
                f"{required} is not covered by .gitignore",
            )

    def test_license_and_attribution(self):
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", license_text)
        self.assertIn("xiyannan", license_text)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("codex-remote-bridge", readme)
        self.assertIn("DeepSeek Harness", readme)

    def test_version_is_set(self):
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^\d+\.\d+\.\d+")
        self.assertNotIn("0.2.16", version)


if __name__ == "__main__":
    unittest.main()
