# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
# Project: DeepSeek Remote Bridge
"""The bridge's local web server.

Every byte the page receives comes from this process: the DeepSeek Harness
sessions it lists, the turns it reads, the prompts it sends, the balance it
reports. The harness runs as a separate child process and is never exposed.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
import time
import urllib.parse
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import pricing
from .attachments import AttachmentError, AttachmentStore, is_image_path, read_file_bounded
from .config import BridgeConfig
from .dsh_adapter import AdapterError
from .sessions import SessionNotFound
from .state import BridgeState
from .tasks import TaskRejected
from .voice import VoiceError

STATIC_CACHE = "no-store"
TEXT_CONTENT_TYPES = {".html", ".js", ".css", ".svg", ".json", ".txt", ".webmanifest"}
LOOPBACK_ADDRESSES = {"127.0.0.1", "::1", "localhost"}


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class BridgeRequestHandler(BaseHTTPRequestHandler):
    server_version = "DeepSeekRemoteBridge"
    protocol_version = "HTTP/1.1"
    state: BridgeState
    config: BridgeConfig

    # -- logging --------------------------------------------------------
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003 - stdlib signature
        if os.environ.get("DSH_BRIDGE_HTTP_LOG") == "1":
            super().log_message(format, *args)

    # -- helpers --------------------------------------------------------
    @property
    def base_path(self) -> str:
        return self.config.normalized_base_path

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", STATIC_CACHE)
        self.send_header("x-content-type-options", "nosniff")
        self.send_header("referrer-policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(status, _json_bytes(payload), "application/json; charset=utf-8")

    def _error(self, status: int, message: str, code: str = "error") -> None:
        self._json({"error": message, "code": code}, status=status)

    def _read_json(self, limit: int = 32 * 1024 * 1024) -> dict[str, Any]:
        try:
            length = int(self.headers.get("content-length") or "0")
        except ValueError:
            raise ValueError("invalid content length") from None
        if length <= 0:
            return {}
        if length > limit:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("invalid JSON body") from None
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def _strip_base(self, path: str) -> str | None:
        base = self.base_path
        if base == "/":
            return path
        if path == base.rstrip("/"):
            return ""
        if path.startswith(base):
            return "/" + path[len(base) :]
        return None

    def _csrf_ok(self) -> bool:
        """Reject cross-site writes without requiring a token for local use."""
        origin = self.headers.get("origin")
        host = self.headers.get("host") or ""
        if origin:
            parsed = urllib.parse.urlparse(origin)
            if parsed.netloc and parsed.netloc != host:
                return False
        site = self.headers.get("sec-fetch-site")
        if site is not None and site not in {"same-origin", "none"}:
            return False
        return True

    def _client_is_local(self) -> bool:
        address = self.client_address[0] if self.client_address else ""
        return address in LOOPBACK_ADDRESSES or address.startswith("127.") or address == "::1"

    # -- verbs ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        try:
            self._route("GET")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:  # noqa: BLE001 - never leak a traceback to the page
            self._error(500, f"内部错误：{error}", "internal")

    def do_HEAD(self) -> None:  # noqa: N802
        try:
            self._route("HEAD")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self) -> None:  # noqa: N802
        try:
            if not self._csrf_ok():
                self._error(403, "跨站请求被拒绝", "cross_origin")
                return
            self._route("POST")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:  # noqa: BLE001
            self._error(500, f"内部错误：{error}", "internal")

    # -- routing --------------------------------------------------------
    def _route(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)
        relative = self._strip_base(path)
        if relative is None:
            self._error(404, "该路径不属于 DeepSeek Remote Bridge", "unknown_path")
            return
        if relative == "":
            self._redirect(self.base_path)
            return
        if relative == "/":
            self._serve_index()
            return
        if relative == "/api" or relative.startswith("/api/"):
            self._route_api(method, relative, query)
            return
        if relative in {"/favicon.ico", "/apple-touch-icon.png"}:
            self._serve_file(self.config.web_dir / "favicon.svg", download=False)
            return
        self._serve_static(relative)

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("location", location)
        self.send_header("content-length", "0")
        self.end_headers()

    # -- API ------------------------------------------------------------
    def _route_api(self, method: str, path: str, query: dict[str, list[str]]) -> None:
        state = self.state
        if method in {"GET", "HEAD"}:
            if path == "/api/health":
                self._json(state.health())
                return
            if path == "/api/bridge-info":
                self._json(state.bridge_info())
                return
            if path == "/api/status":
                self._json(state.status())
                return
            if path == "/api/auth/config":
                self._json(state.site_config())
                return
            if path == "/api/threads":
                self._api_threads(query)
                return
            if path.startswith("/api/thread/"):
                self._api_thread(path[len("/api/thread/") :], query)
                return
            if path == "/api/balance":
                force = _first(query, "refresh") in {"1", "true", "yes"}
                payload = state.balance.snapshot_for_page(force=force)
                payload["pricing"] = pricing.payload_for(
                    state.config.peak_windows,
                    state.config.peak_days,
                    state.config.peak_offset_minutes,
                )
                self._json(payload)
                return
            if path == "/api/interactions":
                self._json(state.interactions.snapshot())
                return
            if path == "/api/pinned":
                self._json({"pinned": [], "hidden": []})
                return
            if path == "/api/attachment":
                self._api_attachment(query)
                return
            if path.startswith("/api/local-task/"):
                task = state.tasks.status(path[len("/api/local-task/") :])
                if task is None:
                    self._error(404, "任务不存在", "task_not_found")
                    return
                self._json(task.as_dict())
                return
            if path == "/api/preferences":
                self._json(state.preferences.snapshot())
                return
            if path == "/api/permissions":
                self._api_permissions(query)
                return
            if path == "/api/voice/config":
                self._json({**state.voice.public(), "preferences": state.preferences.snapshot()})
                return
            if path == "/api/voice/status":
                keys = [item for item in (_first(query, "keys") or "").split(",") if item]
                self._json(state.voice.status(keys[:50]))
                return
            if path.startswith("/api/voice/audio/"):
                self._api_voice_audio(path[len("/api/voice/audio/") :])
                return
            self._error(404, "未知接口", "unknown_endpoint")
            return

        if method == "POST":
            if path == "/api/local-tasks":
                self._api_submit()
                return
            if path == "/api/preferences":
                self._api_preferences()
                return
            if path == "/api/permissions":
                self._api_set_permission()
                return
            if path == "/api/voice/request":
                self._api_voice_request()
                return
            if path == "/api/interactions/respond":
                self._api_respond()
                return
            if path == "/api/open-attachment":
                self._api_open_attachment()
                return
            if path == "/api/balance/refresh":
                self._json(state.balance.snapshot_for_page(force=True))
                return
            self._error(404, "未知接口", "unknown_endpoint")
            return

        self._error(405, "不支持的请求方法", "method_not_allowed")

    def _api_threads(self, query: dict[str, list[str]]) -> None:
        state = self.state
        try:
            limit = max(1, min(200, int(_first(query, "limit") or "50")))
        except ValueError:
            limit = 50
        offset = 0
        cursor = _first(query, "cursor")
        if cursor:
            try:
                offset = max(0, int(cursor))
            except ValueError:
                offset = 0
        project = _first(query, "project")
        rows = state.catalog.rows(project_filter=project or None, limit=limit, offset=offset)
        for row in rows:
            running = state.tasks.is_running(str(row.get("id") or ""))
            row["bridgeRunning"] = running
            if running:
                row["status"] = "运行中"
        next_cursor = str(offset + limit) if len(rows) == limit else None
        self._json({"threads": rows, "projects": state.catalog.projects(), "nextCursor": next_cursor})

    def _api_thread(self, session_id: str, query: dict[str, list[str]]) -> None:
        state = self.state
        session_id = session_id.strip("/")
        if not session_id:
            self._error(400, "缺少会话 ID", "missing_session")
            return
        try:
            turn_limit = max(1, min(10_000, int(_first(query, "turnLimit") or "6")))
        except ValueError:
            turn_limit = 6
        try:
            thread = state.catalog.thread(session_id)
        except SessionNotFound:
            self._error(404, "会话不存在", "session_not_found")
            return
        except AdapterError as error:
            self._error(502, f"读取会话失败：{error}", "adapter_error")
            return
        turns = thread.get("turns") or []
        visible = turns[-turn_limit:] if turn_limit and len(turns) > turn_limit else turns
        projected = dict(thread)
        projected["turns"] = visible
        projected["bridgeTotalTurns"] = len(turns)
        projected["bridgeAttachments"] = []
        self._attach_returned_files(projected, visible)
        task = state.tasks.active_task_for(session_id)
        if task is not None and task.status not in {"completed", "failed", "interrupted", "cancelled"}:
            projected["status"] = "running"
        else:
            projected["status"] = thread.get("status")
        self._json({"thread": projected})

    def _attach_returned_files(self, thread: dict[str, Any], turns: list[dict[str, Any]]) -> None:
        texts: list[str] = []
        for turn in turns:
            for item in turn.get("items") or []:
                if item.get("type") in {"agentMessage", "toolCall"}:
                    texts.append(str(item.get("text") or ""))
                    texts.append(str(item.get("result") or ""))
        files = self.state.returned_files.collect(texts)
        if files:
            thread["bridgeAttachments"] = files

    # -- permissions -----------------------------------------------------
    def _api_permissions(self, query: dict[str, list[str]]) -> None:
        state = self.state
        session_id = _first(query, "threadId") or None
        try:
            payload = state.catalog.adapter.permissions(session_id)
        except AdapterError as error:
            self._error(502, f"读取权限预设失败：{error}", "adapter_error")
            return
        preferences = state.preferences.snapshot()
        self._json(
            {
                "options": payload.get("options") or [],
                "defaultPreset": payload.get("defaultPreset"),
                "current": payload.get("current"),
                "bridgeDefault": preferences.get("permissionPreset") or state.config.permission_preset,
                "threadId": session_id,
            }
        )

    def _api_set_permission(self) -> None:
        state = self.state
        try:
            payload = self._read_json()
        except ValueError as error:
            self._error(400, str(error), "bad_request")
            return
        preset = str(payload.get("preset") or "").strip()
        session_id = str(payload.get("threadId") or "").strip()
        if not preset:
            self._error(400, "缺少权限预设", "missing_preset")
            return
        try:
            catalog = state.catalog.adapter.permissions(None)
        except AdapterError as error:
            self._error(502, f"读取权限预设失败：{error}", "adapter_error")
            return
        known = {str(option.get("value")) for option in catalog.get("options") or [] if isinstance(option, dict)}
        if preset not in known:
            self._error(400, "该权限预设不在当前运行时的可选列表中", "unknown_preset")
            return
        if not session_id:
            state.preferences.update({"permissionPreset": preset})
            self._json({"ok": True, "bridgeDefault": preset})
            return
        try:
            state.tasks.ensure_open(session_id)
            payload = state.catalog.adapter.set_permission(session_id, preset)
        except TaskRejected as error:
            self._error(error.http_status, str(error), error.code)
            return
        except AdapterError as error:
            status = error.status if 400 <= error.status < 600 else 502
            self._error(status, f"切换权限失败：{error}", "adapter_error")
            return
        state.catalog.invalidate(session_id)
        self._json({"ok": True, "current": payload.get("current"), "preset": preset})

    # -- preferences and voice ------------------------------------------
    def _api_preferences(self) -> None:
        try:
            payload = self._read_json()
        except ValueError as error:
            self._error(400, str(error), "bad_request")
            return
        self._json(self.state.preferences.update(payload))

    def _api_voice_request(self) -> None:
        state = self.state
        try:
            payload = self._read_json()
        except ValueError as error:
            self._error(400, str(error), "bad_request")
            return
        if not state.preferences.snapshot().get("voiceEnabled", True):
            self._error(403, "网页的语音朗读已关闭", "voice_disabled")
            return
        text = str(payload.get("text") or "")
        try:
            key, path = state.voice.synthesize(text)
        except VoiceError as error:
            status = 400 if error.code.startswith("voice_text") else (503 if error.code == "voice_not_configured" else 502)
            self._error(status, error.message, error.code)
            return
        self._json({"cached": True, "key": key, "bytes": path.stat().st_size})

    def _api_voice_audio(self, key: str) -> None:
        state = self.state
        clean = key.strip("/")
        try:
            path = state.voice.path_for(clean)
        except VoiceError:
            self._error(404, "语音不存在", "voice_not_found")
            return
        if not path.is_file():
            self._error(404, "语音不存在", "voice_not_found")
            return
        try:
            data = path.read_bytes()
        except OSError as error:
            self._error(500, f"读取语音失败：{error}", "voice_read_failed")
            return
        self._send(
            200,
            data,
            "audio/mpeg",
            {"cache-control": "private, max-age=86400", "accept-ranges": "none"},
        )

    def _api_attachment(self, query: dict[str, list[str]]) -> None:
        raw = _first(query, "path")
        if not raw:
            self._error(400, "缺少文件路径", "missing_path")
            return
        path = Path(raw)
        if not path.is_file():
            self._error(404, "文件不存在", "file_not_found")
            return
        if not self.state.returned_files.allowed(path):
            self._error(403, "该路径不在桥允许的目录内", "path_forbidden")
            return
        try:
            data = read_file_bounded(path, self.state.config.attachments_max_bytes)
        except AttachmentError as error:
            self._error(413, str(error), "file_too_large")
            return
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        disposition = "inline" if is_image_path(path) else "attachment"
        encoded = urllib.parse.quote(path.name)
        self._send(
            200,
            data,
            mime,
            {"content-disposition": f"{disposition}; filename*=UTF-8''{encoded}"},
        )

    def _api_open_attachment(self) -> None:
        try:
            payload = self._read_json()
        except ValueError as error:
            self._error(400, str(error), "bad_request")
            return
        raw = str(payload.get("path") or "")
        path = Path(raw)
        if not path.is_file():
            self._error(404, "文件不存在", "file_not_found")
            return
        if not self.state.returned_files.allowed(path):
            self._error(403, "该路径不在桥允许的目录内", "path_forbidden")
            return
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                self._error(501, "当前平台不支持本机打开", "unsupported")
                return
        except OSError as error:
            self._error(500, f"打开失败：{error}", "open_failed")
            return
        self._json({"ok": True})

    def _api_submit(self) -> None:
        state = self.state
        try:
            payload = self._read_json()
        except ValueError as error:
            self._error(400, str(error), "bad_request")
            return
        text = str(payload.get("text") or "").strip()
        files = payload.get("files") or []
        if not isinstance(files, list):
            self._error(400, "附件格式不正确", "bad_files")
            return
        operation = str(payload.get("op") or "")
        try:
            if operation == "new_thread":
                cwd = str(payload.get("cwd") or "").strip()
                title = str(payload.get("title") or "").strip()
                if not cwd:
                    raise TaskRejected("缺少项目目录")
                if not _path_allowed(state, Path(cwd)):
                    raise TaskRejected("该目录不在桥允许的工作区列表内", code="cwd_forbidden", status=403)
                session_id = f"session-{uuid.uuid4()}"
                preset = str(payload.get("permissionPreset") or "").strip()
                if not preset:
                    preset = str(state.preferences.snapshot().get("permissionPreset") or state.config.permission_preset)
                staged, images = state.attachments.stage(session_id, files)
                blocks = _content_blocks(text + state.attachments.prompt_suffix(staged), images)
                task = state.tasks.create_session(cwd, blocks, session_id=session_id, preset=preset or None)
                if title:
                    task.detail["title"] = title
                self._json({"taskId": task.id, "threadId": task.session_id})
                return
            session_id = str(payload.get("threadId") or "").strip()
            if not session_id:
                raise TaskRejected("缺少会话 ID")
            if not text and not files:
                raise TaskRejected("发送内容为空")
            staged, images = state.attachments.stage(session_id, files)
            blocks = _content_blocks(text + state.attachments.prompt_suffix(staged), images)
            steered = state.tasks.is_running(session_id)
            task = state.tasks.submit(session_id, blocks, steered=steered)
            self._json({"taskId": task.id, "threadId": task.session_id, "steered": task.steered})
        except TaskRejected as error:
            self._error(error.http_status, str(error), error.code)
        except AttachmentError as error:
            self._error(413, str(error), "attachment_rejected")
        except AdapterError as error:
            self._error(502, f"读取会话失败：{error}", "adapter_error")

    def _api_respond(self) -> None:
        try:
            payload = self._read_json()
        except ValueError as error:
            self._error(400, str(error), "bad_request")
            return
        try:
            result = self.state.interactions.respond(payload)
        except KeyError as error:
            self._error(404, str(error).strip("'\""), "interaction_missing")
            return
        except ValueError as error:
            self._error(400, str(error), "invalid_answer")
            return
        except AdapterError as error:
            self._error(502, f"转发回答失败：{error}", "adapter_error")
            return
        self._json(result)

    # -- static ---------------------------------------------------------
    def _serve_index(self) -> None:
        index = self.config.web_dir / "index.html"
        try:
            html = index.read_text(encoding="utf-8")
        except OSError:
            self._error(500, "页面文件缺失", "missing_index")
            return
        boot = {
            "basePath": self.base_path,
            "version": self.config.version,
            "title": self.config.site_title,
            "subtitle": self.config.site_subtitle,
            "displayName": self.config.display_name,
            "providerId": self.config.provider_id,
        }
        injected = f"<script>window.DSH_BRIDGE={json.dumps(boot, ensure_ascii=False)};</script>"
        html = html.replace("<!-- bridge-boot -->", injected)
        body = html.encode("utf-8")
        self._send(200, body, "text/html; charset=utf-8")

    def _serve_static(self, relative: str) -> None:
        safe = relative.lstrip("/")
        if ".." in safe.split("/") or safe.startswith("."):
            self._error(404, "文件不存在", "file_not_found")
            return
        candidate = (self.config.web_dir / safe).resolve()
        try:
            candidate.relative_to(self.config.web_dir.resolve())
        except ValueError:
            self._error(404, "文件不存在", "file_not_found")
            return
        self._serve_file(candidate, download=False)

    def _serve_file(self, path: Path, download: bool) -> None:
        if not path.is_file():
            self._error(404, "文件不存在", "file_not_found")
            return
        try:
            data = path.read_bytes()
        except OSError as error:
            self._error(500, f"读取文件失败：{error}", "file_error")
            return
        suffix = path.suffix.lower()
        mime = "text/javascript; charset=utf-8" if suffix == ".js" else (mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        if suffix == ".webmanifest":
            mime = "application/manifest+json"
        elif suffix == ".svg":
            mime = "image/svg+xml"
        if suffix in TEXT_CONTENT_TYPES and "charset" not in mime:
            mime += "; charset=utf-8"
        extra = {}
        if download:
            extra["content-disposition"] = f"attachment; filename*=UTF-8''{urllib.parse.quote(path.name)}"
        self._send(200, data, mime, extra)


def _first(query: dict[str, list[str]], name: str) -> str:
    values = query.get(name)
    return values[0] if values else ""


def _path_allowed(state: BridgeState, path: Path) -> bool:
    config = state.config
    roots = [config.projects_root, config.standalone_dir]
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in roots:
        try:
            root = root.resolve()
        except OSError:
            continue
        if resolved == root or root in resolved.parents:
            return True
    return False


def _content_blocks(text: str, images: list[dict[str, str]]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    cleaned = text.strip()
    if cleaned:
        blocks.append({"type": "text", "text": cleaned})
    for image in images:
        blocks.append({"type": "image", "data": image["data"], "mimeType": image["mimeType"]})
    return blocks


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config: BridgeConfig, state: BridgeState):
        self.config = config
        self.state = state
        host = config.host
        handler = type(
            "BoundBridgeRequestHandler",
            (BridgeRequestHandler,),
            {"state": state, "config": config},
        )
        super().__init__((host, config.port), handler)


def serve(config: BridgeConfig, state: BridgeState) -> BridgeServer:
    state.start_background()
    server = BridgeServer(config, state)
    thread = threading.Thread(target=server.serve_forever, name="bridge-server", daemon=False)
    thread.start()
    return server
