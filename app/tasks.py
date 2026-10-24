# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
# Project: DeepSeek Remote Bridge
"""Local task runner: turn one page submission into one real harness turn.

A task owns exactly one ``session/prompt`` submission. It never resends an input
after a timeout or a transport failure, and it reports the terminal state that
the harness actually logged.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .dsh_rpc import RpcError
from .history_view import project_session

TASK_TERMINAL = {"completed", "failed", "interrupted", "cancelled"}

STATUS_MESSAGE = {
    "queued": "已提交到 DeepSeek Harness",
    "running": "DeepSeek Harness 正在处理",
    "completed": "任务已完成",
    "failed": "任务执行失败",
    "interrupted": "本轮已中断",
    "cancelled": "任务已取消",
}

BUSY_PATTERNS = (
    "already owned",
    "already open",
    "is already open or opening",
    "sessionalreadyowned",
    "写入锁",
)


class TaskRejected(RuntimeError):
    """The submission was refused before any input reached the harness."""

    def __init__(self, message: str, code: str = "rejected", status: int = 400):
        super().__init__(message)
        self.code = code
        self.http_status = status


def is_busy_error(error: BaseException) -> bool:
    text = str(error).lower()
    return any(pattern in text for pattern in BUSY_PATTERNS)


@dataclass
class LocalTask:
    id: str
    session_id: str
    cwd: str | None
    created_at: float
    status: str = "queued"
    message: str = STATUS_MESSAGE["queued"]
    turn_id: str | None = None
    steered: bool = False
    message_id: str | None = None
    error: str = ""
    finished_at: float = 0.0
    new_session: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "taskId": self.id,
            "threadId": self.session_id,
            "status": self.status,
            "message": self.message,
            "turnId": self.turn_id,
            "steered": self.steered,
            "createdAt": self.created_at,
            "finishedAt": self.finished_at or None,
            "error": self.error,
        }


class TaskRunner:
    """Submit prompts and follow them to a durable terminal state."""

    def __init__(self, config, pool, catalog, returned_files, on_finished=None):
        self.config = config
        self.pool = pool
        self.catalog = catalog
        self.returned_files = returned_files
        self.on_finished = on_finished
        self.lock = threading.RLock()
        self.tasks: dict[str, LocalTask] = {}
        self.active_by_session: dict[str, str] = {}
        self.worker_timeout = 6 * 3600.0

    # -- public API -----------------------------------------------------
    def submit(self, session_id: str, content_blocks: list[dict[str, Any]], steered: bool = False) -> LocalTask:
        if not content_blocks:
            raise TaskRejected("发送内容为空")
        handle = self._handle_for(session_id)
        record = self.catalog.known(session_id)
        task = LocalTask(
            id=f"task-{uuid.uuid4()}",
            session_id=session_id,
            cwd=(record.cwd if record else None) or str(self.config.projects_root),
            created_at=time.time(),
            steered=steered,
        )
        self._register(task)
        try:
            self._ensure_open(handle, session_id)
            response = handle.call(
                "session/prompt",
                {"sessionId": session_id, "contentBlocks": content_blocks},
                timeout=120.0,
            )
            task.message_id = (response or {}).get("messageId")
            task.status = "running"
            task.message = STATUS_MESSAGE["running"]
        except RpcError as error:
            self._finish(task, "failed", str(error))
            if is_busy_error(error):
                raise TaskRejected(
                    "该会话正被另一个 DeepSeek Harness 进程占用，尚未发送任何内容。",
                    code="session_busy",
                    status=409,
                ) from None
            raise TaskRejected(f"发送失败：{error}", code="prompt_failed", status=502) from None
        except Exception as error:  # noqa: BLE001 - converted into a task failure
            self._finish(task, "failed", str(error))
            raise TaskRejected(f"发送失败：{error}", code="prompt_failed", status=500) from None
        threading.Thread(target=self._follow, args=(task, handle), name=f"task-{task.id}", daemon=True).start()
        return task

    def create_session(
        self,
        cwd: str,
        content_blocks: list[dict[str, Any]],
        session_id: str | None = None,
        preset: str | None = None,
    ) -> LocalTask:
        if not cwd:
            raise TaskRejected("缺少工作目录")
        session_id = session_id or f"session-{uuid.uuid4()}"
        handle = self.pool.acquire()
        task = LocalTask(
            id=f"task-{uuid.uuid4()}",
            session_id=session_id,
            cwd=cwd,
            created_at=time.time(),
            new_session=True,
        )
        self._register(task)
        try:
            with handle.lock:
                # Create the session without starting a turn, so the permission
                # preset is in effect before the first tool call can run.
                handle.call("session/working-directory/set", {"sessionId": session_id, "path": cwd}, timeout=120.0)
                handle.opened_sessions.add(session_id)
            if preset:
                try:
                    self.catalog.adapter.set_permission(session_id, preset)
                except Exception as error:  # noqa: BLE001 - keep the first turn alive
                    task.detail["permissionError"] = str(error)
            response = handle.call(
                "session/prompt",
                {"sessionId": session_id, "contentBlocks": content_blocks},
                timeout=120.0,
            )
            task.message_id = (response or {}).get("messageId")
            task.status = "running"
            task.message = STATUS_MESSAGE["running"]
        except RpcError as error:
            self._finish(task, "failed", str(error))
            raise TaskRejected(f"创建会话失败：{error}", code="create_failed", status=502) from None
        threading.Thread(target=self._follow, args=(task, handle), name=f"task-{task.id}", daemon=True).start()
        return task

    def ensure_open(self, session_id: str) -> None:
        """Make a session live in our runtime so its settings can be changed."""
        handle = self.pool.acquire()
        try:
            self._ensure_open(handle, session_id)
        except RpcError as error:
            if is_busy_error(error):
                raise TaskRejected(
                    "该会话正被另一个 DeepSeek Harness 进程占用，无法修改设置。",
                    code="session_busy",
                    status=409,
                ) from None
            raise TaskRejected(f"无法打开会话：{error}", code="resume_failed", status=502) from None

    def status(self, task_id: str) -> LocalTask | None:
        with self.lock:
            return self.tasks.get(task_id)

    def active_task_for(self, session_id: str) -> LocalTask | None:
        with self.lock:
            task_id = self.active_by_session.get(session_id)
            return self.tasks.get(task_id) if task_id else None

    def is_running(self, session_id: str) -> bool:
        task = self.active_task_for(session_id)
        return task is not None and task.status not in TASK_TERMINAL

    # -- internals ------------------------------------------------------
    def _register(self, task: LocalTask) -> None:
        with self.lock:
            self.tasks[task.id] = task
            self.active_by_session[task.session_id] = task.id
            if len(self.tasks) > 500:
                for key in [key for key, value in self.tasks.items() if value.status in TASK_TERMINAL][:100]:
                    self.tasks.pop(key, None)

    def _finish(self, task: LocalTask, status: str, error: str = "") -> None:
        task.status = status
        task.message = STATUS_MESSAGE.get(status, status)
        task.error = error
        task.finished_at = time.time()
        with self.lock:
            if self.active_by_session.get(task.session_id) == task.id:
                self.active_by_session.pop(task.session_id, None)
        self.catalog.invalidate(task.session_id)
        if self.on_finished is not None:
            try:
                self.on_finished(task)
            except Exception:
                pass

    def _handle_for(self, session_id: str):
        return self.pool.acquire()

    def _ensure_open(self, handle, session_id: str) -> None:
        with handle.lock:
            if session_id in handle.opened_sessions:
                return
            try:
                handle.call("session/resume", {"sessionId": session_id}, timeout=300.0)
            except RpcError as error:
                raise
            handle.opened_sessions.add(session_id)

    def _follow(self, task: LocalTask, handle) -> None:
        try:
            handle.call("session/wait", {"sessionId": task.session_id}, timeout=self.worker_timeout)
        except RpcError as error:
            self._finish(task, "failed", str(error))
            return
        try:
            payload = self.catalog.adapter.session(task.session_id)
        except Exception as error:  # noqa: BLE001 - keep the task honest about uncertainty
            self._finish(task, "completed", f"运行结束，但读取最终状态失败：{error}")
            return
        thread = project_session(payload.get("header") or {}, payload.get("events") or [])
        status, turn_id = self._terminal_state(thread, task.message_id, task.cwd)
        task.turn_id = turn_id
        self._finish(task, status)

    def _terminal_state(self, thread: dict[str, Any], message_id: str | None, cwd: str | None) -> tuple[str, str | None]:
        turns = thread.get("turns") or []
        target = None
        if message_id:
            target = _turn_for_message(thread, message_id)
        if target is None and turns:
            target = turns[-1]
        if target is None:
            return "completed", None
        status = str(target.get("status") or "")
        if status == "in_progress":
            # The wait returned while a queued message had not produced a terminal.
            return "interrupted", target.get("id")
        if status == "failed":
            return "failed", target.get("id")
        if status == "cancelled":
            return "cancelled", target.get("id")
        if status == "interrupted":
            return "interrupted", target.get("id")
        return "completed", target.get("id")


def _turn_for_message(thread: dict[str, Any], message_id: str) -> dict[str, Any] | None:
    """Locate the turn whose user message carries the prompt's message id."""
    turns = thread.get("turns") or []
    for index, turn in enumerate(turns):
        for item in turn.get("items") or []:
            if item.get("type") != "userMessage":
                continue
            if message_id in (item.get("bridgeMessageId"), item.get("messageId")):
                return turn
            if item.get("bridgeMessageId") is None:
                # No id was projected; fall back to a text match on the first turn
                # whose user message is still the newest unmatched one.
                continue
    return None
