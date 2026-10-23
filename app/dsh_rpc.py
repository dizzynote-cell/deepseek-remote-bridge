# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
# Project: DeepSeek Remote Bridge
"""Newline-delimited JSON-RPC client for one DeepSeek Harness SDK runtime.

The wire is the official ``@deepseek-ai/dsh-sdk-protocol`` contract: one JSON
object per line on the child's stdin/stdout. This module owns framing, request
correlation, timeouts, notification fan-out, and crash detection. It never
interprets Harness method semantics; callers do that.

Stdout purity is deployment-enforced upstream, and the shipped DeepSeek account
model adapter writes a model-listing diagnostic line to stdout. Frames that do
not parse as JSON are therefore counted and ignored instead of failing the
channel.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable


class RpcError(RuntimeError):
    """A JSON-RPC error frame, or a transport failure."""

    def __init__(self, message: str, code: int | None = None, data: Any = None, transport: bool = False):
        super().__init__(message)
        self.code = code
        self.data = data
        self.transport = transport


class RuntimeProcess:
    """One supervised ``dsh --profile sdk`` child process."""

    def __init__(
        self,
        command: list[str],
        env: dict[str, str],
        cwd: Path,
        stderr_log: Path,
        on_notification: Callable[[str, dict[str, Any]], None] | None = None,
        on_exit: Callable[[str], None] | None = None,
        default_timeout: float = 120.0,
    ):
        self.command = command
        self.stderr_log = stderr_log
        self.on_notification = on_notification
        self.on_exit = on_exit
        self.default_timeout = default_timeout
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending: dict[int, dict[str, Any]] = {}
        self._next_id = 0
        self._closed = False
        self.exit_reason = ""
        self.protocol_noise = 0
        self.started_at = time.time()
        stderr_log.parent.mkdir(parents=True, exist_ok=True)
        self._stderr_handle = stderr_log.open("a", encoding="utf-8", errors="replace")
        self._stderr_handle.write(f"\n===== runtime start {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        self._stderr_handle.flush()
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd),
            env=env,
            bufsize=0,
            creationflags=creationflags,
        )
        self._stdout_thread = threading.Thread(target=self._pump_stdout, name="dsh-rpc-stdout", daemon=True)
        self._stderr_thread = threading.Thread(target=self._pump_stderr, name="dsh-rpc-stderr", daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    # -- lifecycle ------------------------------------------------------
    @property
    def alive(self) -> bool:
        return not self._closed and self.proc.poll() is None

    def _fail_pending(self, reason: str) -> None:
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for slot in pending:
            slot["error"] = RpcError(reason, transport=True)
            slot["event"].set()

    def _mark_closed(self, reason: str) -> None:
        if self._closed:
            return
        self._closed = True
        self.exit_reason = reason
        self._fail_pending(reason)
        if self.on_exit is not None:
            try:
                self.on_exit(reason)
            except Exception:  # pragma: no cover - observer isolation
                pass

    def close(self, timeout: float = 20.0) -> None:
        if self.proc.poll() is None:
            try:
                self.call("shutdown", timeout=10.0)
            except RpcError:
                pass
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover
                pass
        self._mark_closed("closed by caller")
        try:
            self._stderr_handle.close()
        except OSError:
            pass

    def kill(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass

    # -- pumps ----------------------------------------------------------
    def _pump_stdout(self) -> None:
        stream = self.proc.stdout
        if stream is None:
            self._mark_closed("runtime produced no stdout")
            return
        buffer = b""
        while True:
            try:
                chunk = stream.read(4096)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                self._handle_line(line)
        if buffer.strip():
            self._handle_line(buffer)
        reason = f"runtime exited with code {self.proc.poll()}"
        self._mark_closed(reason)

    def _handle_line(self, line: bytes) -> None:
        text = line.strip()
        if not text:
            return
        try:
            frame = json.loads(text)
        except ValueError:
            self.protocol_noise += 1
            return
        if not isinstance(frame, dict):
            self.protocol_noise += 1
            return
        if "id" in frame and "method" not in frame:
            with self._lock:
                slot = self._pending.pop(frame["id"], None)
            if slot is None:
                return
            error = frame.get("error")
            if isinstance(error, dict):
                slot["error"] = RpcError(
                    str(error.get("message") or "runtime error"),
                    code=error.get("code"),
                    data=error.get("data"),
                )
            else:
                slot["result"] = frame.get("result")
            slot["event"].set()
            return
        method = frame.get("method")
        if isinstance(method, str):
            params = frame.get("params")
            if self.on_notification is not None:
                try:
                    self.on_notification(method, params if isinstance(params, dict) else {})
                except Exception:  # pragma: no cover - observer isolation
                    pass

    def _pump_stderr(self) -> None:
        stream = self.proc.stderr
        if stream is None:
            return
        while True:
            try:
                line = stream.readline()
            except (OSError, ValueError):
                break
            if not line:
                break
            try:
                self._stderr_handle.write(line.decode("utf-8", "replace"))
                self._stderr_handle.flush()
            except (OSError, ValueError):
                break

    # -- calls ----------------------------------------------------------
    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        if self._closed or self.proc.poll() is not None:
            raise RpcError(self.exit_reason or "runtime is not running", transport=True)
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            slot: dict[str, Any] = {"event": threading.Event(), "result": None, "error": None}
            self._pending[request_id] = slot
        frame: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            frame["params"] = params
        payload = (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            with self._write_lock:
                if self.proc.stdin is None:
                    raise OSError("runtime stdin is closed")
                self.proc.stdin.write(payload)
                self.proc.stdin.flush()
        except (OSError, ValueError) as error:
            with self._lock:
                self._pending.pop(request_id, None)
            raise RpcError(f"cannot write to the runtime: {error}", transport=True) from None
        if not slot["event"].wait(timeout or self.default_timeout):
            with self._lock:
                self._pending.pop(request_id, None)
            raise RpcError(f"{method} timed out after {timeout or self.default_timeout:.0f}s")
        if slot["error"] is not None:
            raise slot["error"]
        return slot["result"]


class NotificationBuffer:
    """Bounded per-session notification history used for diagnostics."""

    def __init__(self, size: int = 500):
        self._items: deque[tuple[str, dict[str, Any]]] = deque(maxlen=size)
        self._lock = threading.Lock()

    def add(self, method: str, params: dict[str, Any]) -> None:
        with self._lock:
            self._items.append((method, params))

    def snapshot(self) -> list[tuple[str, dict[str, Any]]]:
        with self._lock:
            return list(self._items)
