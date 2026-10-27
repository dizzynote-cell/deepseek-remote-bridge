# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
# Project: DeepSeek Remote Bridge
"""Session catalog: real DeepSeek Harness sessions, read through the host adapter."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .dsh_adapter import AdapterClient, AdapterError
from .history_view import project_session


class SessionNotFound(RuntimeError):
    """The requested session is not in the harness store."""


@dataclass
class SessionRecord:
    id: str
    cwd: str | None
    created_at: int
    updated_at: int
    title: str | None
    live: bool
    agent_preset: str | None = None
    visibility: bool = True


@dataclass
class ThreadCacheEntry:
    thread: dict[str, Any]
    loaded_at: float
    events_count: int


class SessionCatalog:
    """List, read, and cache DSH sessions without making them live."""

    def __init__(self, adapter_factory, cache_ttl: float = 2.5, library=None):
        self.library = library
        self._adapter_factory = adapter_factory
        self._cache_ttl = cache_ttl
        self.lock = threading.RLock()
        self.records: dict[str, SessionRecord] = {}
        self.threads: dict[str, ThreadCacheEntry] = {}
        self.last_refresh = 0.0
        self.last_error = ""

    @property
    def adapter(self) -> AdapterClient:
        return self._adapter_factory()

    # -- listing --------------------------------------------------------
    def refresh(self, force: bool = False) -> dict[str, SessionRecord]:
        now = time.time()
        with self.lock:
            if not force and now - self.last_refresh < 1.5 and self.records:
                return dict(self.records)
        try:
            payload = self.adapter.sessions(limit=500)
        except AdapterError as error:
            with self.lock:
                self.last_error = str(error)
            return dict(self.records)
        records: dict[str, SessionRecord] = {}
        for row in payload.get("sessions") or []:
            if not isinstance(row, dict):
                continue
            session_id = str(row.get("id") or "")
            if not session_id:
                continue
            created = int(row.get("createdAt") or 0)
            title = row.get("title")
            records[session_id] = SessionRecord(
                id=session_id,
                cwd=row.get("cwd"),
                created_at=created,
                updated_at=max(created, int(row.get("updatedAt") or 0)),
                title=title if isinstance(title, str) else None,
                live=bool(row.get("live")),
                agent_preset=row.get("agentPreset"),
            )
        with self.lock:
            self.records = records
            self.last_refresh = time.time()
            self.last_error = ""
            for session_id, entry in self.threads.items():
                record = records.get(session_id)
                if record is not None and entry.thread.get("bridgeUpdatedAt"):
                    record.updated_at = max(record.updated_at, int(entry.thread["bridgeUpdatedAt"]))
        return dict(records)

    def rows(self, project_filter: str | None = None, limit: int = 100, offset: int = 0, include_hidden: bool = False) -> list[dict[str, Any]]:
        records = self.refresh()
        with self.lock:
            for session_id, entry in self.threads.items():
                record = records.get(session_id)
                if record is not None:
                    record.updated_at = max(record.updated_at, int(entry.thread.get("bridgeUpdatedAt") or 0))
        metadata = self.library.snapshot() if self.library else {}
        ordered = sorted((record for record in records.values() if include_hidden or not metadata.get(record.id, {}).get("hidden")), key=lambda record: (-int(bool(metadata.get(record.id, {}).get("pinned"))), -record.updated_at, -record.created_at, record.id))
        if project_filter:
            ordered = [record for record in ordered if (record.cwd or "") == project_filter]
        window = ordered[offset : offset + limit]
        rows: list[dict[str, Any]] = []
        for record in window:
            name = record.title or _short_cwd_name(record.cwd)
            rows.append(
                {
                    "id": record.id,
                    "name": name,
                    "status": "当前已加载" if record.live else "空闲",
                    "cwd": record.cwd,
                    "pinned": bool(metadata.get(record.id, {}).get("pinned")),
                    "bridgeCreatedAt": record.created_at,
                    "bridgeUpdatedAt": record.updated_at,
                    "bridgeLive": record.live,
                    "bridgeAgentPreset": record.agent_preset,
                }
            )
        return rows

    def known(self, session_id: str) -> SessionRecord | None:
        with self.lock:
            record = self.records.get(session_id)
        if record is not None:
            return record
        self.refresh(force=True)
        with self.lock:
            return self.records.get(session_id)

    # -- one session ----------------------------------------------------
    def thread(self, session_id: str, max_age: float | None = None) -> dict[str, Any]:
        ttl = self._cache_ttl if max_age is None else max_age
        now = time.time()
        with self.lock:
            cached = self.threads.get(session_id)
            if cached is not None and now - cached.loaded_at < ttl:
                return cached.thread
        try:
            payload = self.adapter.session(session_id)
        except AdapterError as error:
            if getattr(error, "status", 0) == 500 and "not" in str(error).lower():
                raise SessionNotFound(session_id) from None
            raise
        header = payload.get("header") or {}
        events = payload.get("events") or []
        thread = project_session(header, events)
        record = self.known(session_id)
        if record is not None and not thread.get("name"):
            thread["name"] = record.title or _short_cwd_name(record.cwd)
        last_time = 0
        for event in reversed(events):
            if isinstance(event, dict) and event.get("time") and event.get("type") in {"user/message", "assistant/message", "turn/start", "turn/end", "tool/call", "tool/result"}:
                last_time = int(event["time"])
                break
        thread["bridgeUpdatedAt"] = last_time or int(thread.get("createdAt") or 0)
        thread["bridgeEventCount"] = len(events)
        with self.lock:
            self.threads[session_id] = ThreadCacheEntry(thread=thread, loaded_at=now, events_count=len(events))
            record = self.records.get(session_id)
            if record is not None:
                record.updated_at = max(record.updated_at, int(thread["bridgeUpdatedAt"]))
        return thread

    def invalidate(self, session_id: str | None = None) -> None:
        with self.lock:
            if session_id is None:
                self.threads.clear()
            else:
                self.threads.pop(session_id, None)

    def projects(self) -> list[dict[str, Any]]:
        records = self.refresh()
        counts: dict[str, int] = {}
        for record in records.values():
            key = record.cwd or ""
            counts[key] = counts.get(key, 0) + 1
        return [
            {"cwd": cwd, "name": _short_cwd_name(cwd), "count": count}
            for cwd, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            if cwd
        ]


def _short_cwd_name(cwd: str | None) -> str:
    if not cwd:
        return "未命名会话"
    text = str(cwd).replace("\\", "/").rstrip("/")
    return text.split("/")[-1] or "未命名会话"
