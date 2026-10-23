# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
# Project: DeepSeek Remote Bridge
"""Attachment staging and returned-file discovery.

Uploads from the page are written under the bridge's own ``uploads`` directory,
never into the user's project. Images can additionally travel inline to the
harness as encoded image blocks; documents travel as real local paths that the
harness tools can read.
"""

from __future__ import annotations

import base64
import binascii
import mimetypes
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}
RETURNABLE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".bmp",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".csv",
    ".ppt",
    ".pptx",
    ".txt",
    ".md",
    ".json",
}
WINDOWS_PATH = re.compile(r"[A-Za-z]:\\[^\r\n<>\"|?*\x00]+")
SAFE_NAME = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")


class AttachmentError(ValueError):
    """The upload violates the bridge's attachment policy."""


@dataclass
class StagedFile:
    name: str
    path: Path
    size: int
    mime: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "path": str(self.path), "size": self.size, "mime": self.mime}


def safe_name(name: str) -> str:
    cleaned = SAFE_NAME.sub("_", str(name or "file")).strip("._") or "file"
    return cleaned[:120]


class AttachmentStore:
    """Write uploads into the bridge's own staging directory."""

    def __init__(self, root: Path, max_bytes: int, max_batch_bytes: int):
        self.root = root
        self.max_bytes = max_bytes
        self.max_batch_bytes = max_batch_bytes

    def stage(self, task_key: str, files: list[dict[str, Any]]) -> tuple[list[StagedFile], list[dict[str, str]]]:
        """Persist uploads for one task.

        Returns staged non-image files plus the encoded image blocks accepted for
        inline delivery.
        """
        if not files:
            return [], []
        total = 0
        target = self.root / safe_name(task_key)
        target.mkdir(parents=True, exist_ok=True)
        staged: list[StagedFile] = []
        images: list[dict[str, str]] = []
        counter = 0
        for raw in files:
            if not isinstance(raw, dict):
                continue
            name = safe_name(str(raw.get("name") or "file"))
            encoded = raw.get("data")
            if not isinstance(encoded, str):
                raise AttachmentError("附件内容缺失")
            try:
                payload = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                raise AttachmentError("附件编码无效") from None
            size = len(payload)
            if size == 0:
                continue
            if size > self.max_bytes:
                raise AttachmentError(f"附件 {name} 超过单文件 {self.max_bytes // (1024 * 1024)} MB 限制")
            total += size
            if total > self.max_batch_bytes:
                raise AttachmentError(f"附件总量超过 {self.max_batch_bytes // (1024 * 1024)} MB 限制")
            suffix = Path(name).suffix.lower()
            mime = str(raw.get("mime") or IMAGE_MIME.get(suffix) or mimetypes.guess_type(name)[0] or "application/octet-stream")
            if suffix in IMAGE_SUFFIXES:
                images.append({"type": "image", "data": base64.b64encode(payload).decode("ascii"), "mimeType": IMAGE_MIME.get(suffix, mime)})
                continue
            counter += 1
            destination = target / f"{counter:02d}-{name}"
            destination.write_bytes(payload)
            staged.append(StagedFile(name=name, path=destination, size=size, mime=mime))
        return staged, images

    def prompt_suffix(self, staged: list[StagedFile]) -> str:
        if not staged:
            return ""
        lines = ["", "", "我从网页上传了以下附件（本机绝对路径）："]
        for item in staged:
            lines.append(f"- {item.path}")
        lines.append("请先读取这些文件，再回答我的问题。")
        return "\n".join(lines)

    def cleanup(self, task_key: str, older_than_seconds: float | None = None) -> None:
        target = self.root / safe_name(task_key)
        if not target.is_dir():
            return
        if older_than_seconds is not None:
            age = time.time() - target.stat().st_mtime
            if age < older_than_seconds:
                return
        for path in sorted(target.rglob("*"), reverse=True):
            try:
                path.unlink() if path.is_file() else path.rmdir()
            except OSError:
                pass
        try:
            target.rmdir()
        except OSError:
            pass

    def sweep(self, max_age_seconds: float = 7 * 86400) -> int:
        """Delete staging folders older than ``max_age_seconds``.

        The sweep only ever walks this store's own root, so it cannot touch a
        user project or another bridge's data.
        """
        removed = 0
        if not self.root.is_dir():
            return 0
        deadline = time.time() - max_age_seconds
        for entry in self.root.iterdir():
            try:
                if entry.is_dir() and entry.stat().st_mtime < deadline:
                    self.cleanup(entry.name)
                    removed += 1
            except OSError:
                continue
        return removed


class ReturnedFiles:
    """Discover files the harness reported so the page can offer them back."""

    def __init__(self, allowed_roots: list[Path], max_bytes: int):
        self.allowed_roots = [root.resolve() for root in allowed_roots if root]
        self.max_bytes = max_bytes

    def allowed(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
        except OSError:
            return False
        return any(resolved == root or root in resolved.parents for root in self.allowed_roots)

    def collect(self, texts: list[str]) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for text in texts:
            for match in WINDOWS_PATH.finditer(text or ""):
                for candidate in _path_candidates(match.group(0)):
                    path = Path(candidate)
                    if not path.is_file():
                        continue
                    if path.suffix.lower() not in RETURNABLE_SUFFIXES:
                        break
                    if not self.allowed(path):
                        break
                    try:
                        size = path.stat().st_size
                    except OSError:
                        break
                    if size > self.max_bytes:
                        break
                    key = str(path).lower()
                    found[key] = {
                        "name": path.name,
                        "localPath": str(path),
                        "agentDiskPath": None,
                        "size": size,
                        "delivery": "local_only",
                        "reason": "任务中生成或引用的本机文件",
                    }
                    break
        return list(found.values())


def is_image_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def read_file_bounded(path: Path, limit: int) -> bytes:
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise AttachmentError("文件超过下载上限")
    return data


def describe_upload(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return IMAGE_MIME.get(suffix, mimetypes.guess_type(name)[0] or "application/octet-stream")


def ensure_owner(path: Path) -> None:
    """Keep staged uploads readable only by the current user where possible."""
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _path_candidates(raw: str):
    """Yield plausible file paths from one prose span, longest first."""
    text = raw.strip().rstrip(".,:;)]}'\"")
    if not text:
        return
    tokens = text.split()
    if len(tokens) == 1:
        yield text
        return
    # A Windows path may contain spaces; try the longest prefix first so a path
    # followed by prose resolves to the path itself.
    for size in range(len(tokens), 0, -1):
        candidate = " ".join(tokens[:size])
        if re.match(r"^[A-Za-z]:\\", candidate):
            yield candidate