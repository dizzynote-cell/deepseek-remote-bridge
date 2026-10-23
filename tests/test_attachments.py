# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
"""Attachment staging, limits, and returned-file discovery."""

from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from app.attachments import AttachmentError, AttachmentStore, ReturnedFiles, safe_name

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "uploads"
        self.store = AttachmentStore(self.root, max_bytes=1024, max_batch_bytes=1536)

    def tearDown(self):
        self.temp.cleanup()

    def upload(self, name, payload, mime="application/octet-stream"):
        return {"name": name, "mime": mime, "data": base64.b64encode(payload).decode("ascii")}

    def test_documents_are_staged_as_real_files(self):
        staged, images = self.store.stage("session-a", [self.upload("notes.txt", b"hello", "text/plain")])
        self.assertEqual(images, [])
        self.assertEqual(len(staged), 1)
        self.assertTrue(staged[0].path.is_file())
        self.assertEqual(staged[0].path.read_text(encoding="utf-8"), "hello")
        self.assertIn(str(staged[0].path), self.store.prompt_suffix(staged))

    def test_images_become_inline_blocks(self):
        staged, images = self.store.stage("session-a", [self.upload("shot.png", PNG_BYTES, "image/png")])
        self.assertEqual(staged, [])
        self.assertEqual(images[0]["type"], "image")
        self.assertEqual(images[0]["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(images[0]["data"]), PNG_BYTES)

    def test_single_file_limit(self):
        with self.assertRaises(AttachmentError):
            self.store.stage("session-a", [self.upload("big.txt", b"x" * 2048)])

    def test_batch_limit(self):
        with self.assertRaises(AttachmentError):
            self.store.stage(
                "session-a",
                [self.upload("a.txt", b"x" * 1000), self.upload("b.txt", b"y" * 1000)],
            )

    def test_invalid_base64_is_rejected(self):
        with self.assertRaises(AttachmentError):
            self.store.stage("session-a", [{"name": "x.txt", "mime": "text/plain", "data": "not base64!!"}])

    def test_names_cannot_escape_the_staging_directory(self):
        staged, _ = self.store.stage("session-a", [self.upload("../../evil.txt", b"payload")])
        self.assertTrue(str(staged[0].path.resolve()).startswith(str(self.root.resolve())))
        self.assertEqual(safe_name("..\\..\\evil.txt"), "evil.txt")

    def test_sweep_only_removes_old_folders(self):
        self.store.stage("old", [self.upload("a.txt", b"a")])
        self.store.stage("new", [self.upload("b.txt", b"b")])
        import os
        import time

        old_dir = self.root / "old"
        stamp = time.time() - 30 * 86400
        os.utime(old_dir, (stamp, stamp))
        removed = self.store.sweep(max_age_seconds=7 * 86400)
        self.assertEqual(removed, 1)
        self.assertFalse(old_dir.exists())
        self.assertTrue((self.root / "new").exists())


class ReturnedFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.files = ReturnedFiles([self.root], max_bytes=1024)

    def tearDown(self):
        self.temp.cleanup()

    def test_only_allowed_roots_and_extensions(self):
        inside = self.root / "report.md"
        inside.write_text("ok", encoding="utf-8")
        outside = Path(self.temp.name).parent / "outside-secret.md"
        outside.write_text("no", encoding="utf-8")
        try:
            found = self.files.collect([f"已生成 {inside} 和 {outside}"])
        finally:
            outside.unlink(missing_ok=True)
        self.assertEqual([item["name"] for item in found], ["report.md"])

    def test_missing_and_oversized_files_are_skipped(self):
        big = self.root / "big.pdf"
        big.write_bytes(b"x" * 2048)
        self.assertEqual(self.files.collect([str(big)]), [])
        self.assertEqual(self.files.collect([str(self.root / "missing.pdf")]), [])

    def test_unsupported_extension_is_skipped(self):
        binary = self.root / "payload.exe"
        binary.write_bytes(b"MZ")
        self.assertEqual(self.files.collect([str(binary)]), [])


if __name__ == "__main__":
    unittest.main()
