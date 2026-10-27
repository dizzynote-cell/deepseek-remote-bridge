# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
# Project: DeepSeek Remote Bridge
"""Shared bridge services and the capability description the entry menu reads."""

from __future__ import annotations

import threading
import time
import urllib.parse
from typing import Any

from .attachments import AttachmentStore, ReturnedFiles
from .balance import BalanceService
from .config import BridgeConfig
from .dsh_adapter import AdapterClient, AdapterError
from .dsh_runtime import RuntimePool, stage_diagnostics
from .interactions import InteractionBridge
from .preferences import Preferences
from .sessions import SessionCatalog
from .thread_library import ThreadLibrary
from .tasks import TaskRunner
from .voice import VoiceService, load_settings


class BridgeState:
    """Own every long-lived service for one bridge process."""

    def __init__(self, config: BridgeConfig):
        self.config = config
        self.config.ensure_dirs()
        self.pool = RuntimePool(config)
        self.thread_library = ThreadLibrary(config.data_dir / "thread-library.json")
        self.catalog = SessionCatalog(self.adapter, library=self.thread_library)
        self.balance = BalanceService(self.adapter, config.provider_id)
        self.attachments = AttachmentStore(
            config.uploads_dir,
            config.attachments_max_bytes,
            config.attachments_max_batch_bytes,
        )
        try:
            self.attachments.sweep()
        except OSError:
            pass
        self.returned_files = ReturnedFiles(
            [config.projects_root, config.standalone_dir, config.uploads_dir],
            config.attachments_max_bytes,
        )
        self.tasks = TaskRunner(
            config, self.pool, self.catalog, self.returned_files, self._on_task_finished,
            routes_path=config.data_dir / "session-routes.json",
        )
        self.interactions = InteractionBridge(self.interaction_adapters, self.catalog)
        self.preferences = Preferences(config.data_dir / "preferences.json")
        self.voice = VoiceService(
            config.cache_dir / "voice",
            load_settings(config.voice_env_file),
            enabled=bool(config.voice_enabled),
        )
        try:
            self.voice.sweep()
        except OSError:
            pass
        self.started_at = time.time()
        self.boot_error = ""
        self._runtime_lock = threading.Lock()

    # -- services -------------------------------------------------------
    def adapter(self) -> AdapterClient:
        handle = self.pool.acquire()
        return AdapterClient(handle.adapter_port, handle.adapter_token)

    def interaction_adapters(self) -> dict[str, AdapterClient]:
        self.pool.acquire()
        with self.pool.lock:
            handles = [handle for handle in self.pool.handles.values() if handle.alive]
        return {handle.instance_id: AdapterClient(handle.adapter_port, handle.adapter_token, timeout=5.0)
                for handle in handles}

    def session_adapter(self, session_id: str) -> AdapterClient:
        handle = self.pool.acquire(self.tasks.route_for(session_id))
        return AdapterClient(handle.adapter_port, handle.adapter_token)

    def _on_task_finished(self, task) -> None:
        self.catalog.invalidate(task.session_id)

    def start_background(self) -> None:
        self.interactions.start()

    def warm_runtime(self) -> None:
        """Start the default runtime before the first page request."""
        try:
            self.pool.acquire()
        except Exception as error:  # noqa: BLE001 - surfaced through health
            self.boot_error = str(error)

    def shutdown(self) -> None:
        self.interactions.stop()
        self.pool.shutdown()

    # -- status ---------------------------------------------------------
    def runtime_overview(self) -> dict[str, Any]:
        handles = list(self.pool.handles.values())
        return {
            "routes": [
                {
                    "route": handle.key.label,
                    "alive": handle.alive,
                    "sessions": len(handle.opened_sessions),
                    "adapterPort": handle.adapter_port,
                    "serverInfo": handle.server_info,
                }
                for handle in handles
            ],
            "lastError": self.pool.last_error,
            "bootError": self.boot_error,
        }

    def health(self) -> dict[str, Any]:
        overview = self.runtime_overview()
        adapter_ok = True
        adapter_error = ""
        try:
            self.adapter().health(timeout=3.0)
        except AdapterError as error:
            adapter_ok = False
            adapter_error = str(error)
        return {
            "relay": {"enabled": bool(getattr(self.config, "relay_url", "")),
                      "connected": bool(getattr(getattr(self, "relay", None), "connected", False)),
                      "lastError": getattr(getattr(self, "relay", None), "last_error", "")},
            "ok": bool(overview["routes"]) and adapter_ok,
            "version": self.config.version,
            "uptimeSeconds": round(time.time() - self.started_at, 1),
            "snapshotSync": {"enabled": bool(getattr(self,"snapshot_sync",None)), "lastSuccess":getattr(getattr(self,"snapshot_sync",None),"last_success",0), "lastError":getattr(getattr(self,"snapshot_sync",None),"last_error","")},
            "runtime": overview,
            "adapter": {"ok": adapter_ok, "error": adapter_error},
            "lastSessionsError": self.catalog.last_error,
            "staging": stage_diagnostics(self.config),
        }

    def status(self) -> dict[str, Any]:
        overview = self.runtime_overview()
        alive = [route for route in overview["routes"] if route["alive"]]
        if not alive:
            label = "运行时未启动"
            short = "运行时未启动"
            mode = "detecting"
        else:
            route = alive[0]["route"]
            label = f"真实 DSH 会话 · {route} · 预设 {self.config.permission_preset}"
            short = f"真实 DSH 会话 · {self.config.model}"
            mode = "local"
        return {"label": label, "shortLabel": short, "mode": mode, "runtime": overview}

    def capabilities(self) -> dict[str, Any]:
        return {
            "existingSessions": True,
            "newSession": True,
            "steer": False,
            "queuedInput": True,
            "cancel": False,
            "attachments": True,
            "approvals": True,
            "userQuestions": True,
            "permissionSwitch": True,
            "voice": bool(self.voice.available and self.preferences.snapshot().get("voiceEnabled", True)),
            "billing": "balance",
            "modelSelection": True,
        }

    def bridge_info(self) -> dict[str, Any]:
        return {
            "contractVersion": 1,
            "providerId": self.config.provider_id,
            "displayName": self.config.display_name,
            "version": self.config.version,
            "capabilities": self.capabilities(),
            "runtime": {
                "provider": self.config.provider,
                "model": self.config.model,
                "reasoningEffort": self.config.reasoning_effort or None,
                "permissionPreset": self.config.permission_preset,
            },
            "basePath": self.config.normalized_base_path,
            "logoMenu": self.logo_menu(),
        }

    def logo_menu(self) -> dict[str, Any]:
        """Describe the top-left switch entries.

        The sibling entry stays disabled unless its target is configured, so the
        page never offers a dead jump. A configured entry performs a full page
        navigation; both bridges keep their own sessions and drafts.
        """
        sibling_url = _safe_sibling_url(self.config.sibling_url)
        return {
            "enabled": True,
            "current": self.config.provider_id,
            "targets": [
                {
                    "providerId": self.config.provider_id,
                    "displayName": self.config.display_name,
                    "current": True,
                    "enabled": True,
                    "url": None,
                },
                {
                    "providerId": self.config.sibling_provider,
                    "displayName": self.config.sibling_name,
                    "current": False,
                    "enabled": sibling_url is not None,
                    "reason": None if sibling_url is not None else "not-configured",
                    "url": sibling_url,
                },
            ],
        }

    def site_config(self) -> dict[str, Any]:
        return {
            "title": self.config.site_title,
            "subtitle": self.config.site_subtitle,
            "version": self.config.version,
            "displayName": self.config.display_name,
            "providerId": self.config.provider_id,
            "basePath": self.config.normalized_base_path,
            "projectsRoot": str(self.config.projects_root),
            "standaloneDir": str(self.config.standalone_dir),
            "authRequired": False,
            "ownerConfigured": True,
            "authenticated": True,
            "voice": {
                "enabled": bool(self.voice.enabled),
                "configured": bool(self.voice.settings.ready),
                "available": bool(self.voice.available),
                "shown": bool(self.voice.available and self.preferences.snapshot().get("voiceEnabled", True)),
            },
            "permissionPreset": self.config.permission_preset,
        }


def _safe_sibling_url(value: str) -> str | None:
    """Accept only an explicit http(s) target; anything else disables the entry."""
    text = (value or "").strip()
    if not text:
        return None
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return text
