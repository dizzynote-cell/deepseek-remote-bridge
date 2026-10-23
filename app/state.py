# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
# Project: DeepSeek Remote Bridge
"""Shared bridge services and the capability description the entry menu reads."""

from __future__ import annotations

import threading
import time
from typing import Any

from .attachments import AttachmentStore, ReturnedFiles
from .balance import BalanceService
from .config import BridgeConfig
from .dsh_adapter import AdapterClient, AdapterError
from .dsh_runtime import RuntimePool, stage_diagnostics
from .interactions import InteractionBridge
from .sessions import SessionCatalog
from .tasks import TaskRunner


class BridgeState:
    """Own every long-lived service for one bridge process."""

    def __init__(self, config: BridgeConfig):
        self.config = config
        self.config.ensure_dirs()
        self.pool = RuntimePool(config)
        self.catalog = SessionCatalog(self.adapter)
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
        self.tasks = TaskRunner(config, self.pool, self.catalog, self.returned_files, self._on_task_finished)
        self.interactions = InteractionBridge(self.adapter, self.catalog)
        self.started_at = time.time()
        self.boot_error = ""
        self._runtime_lock = threading.Lock()

    # -- services -------------------------------------------------------
    def adapter(self) -> AdapterClient:
        handle = self.pool.acquire()
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
            "ok": bool(overview["routes"]) and adapter_ok,
            "version": self.config.version,
            "uptimeSeconds": round(time.time() - self.started_at, 1),
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
            "voice": bool(self.config.voice_enabled),
            "billing": "balance",
            "modelSelection": False,
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
            "logoMenu": {
                "enabled": True,
                "current": self.config.provider_id,
                "targets": [
                    {
                        "providerId": self.config.provider_id,
                        "displayName": self.config.display_name,
                        "current": True,
                        "enabled": True,
                    },
                    {
                        "providerId": "codex",
                        "displayName": "Codex",
                        "current": False,
                        # A second bridge is integrated by the entry page, not here.
                        "enabled": False,
                        "reason": "not-configured",
                        "url": None,
                    },
                ],
            },
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
            "voice": bool(self.config.voice_enabled),
            "permissionPreset": self.config.permission_preset,
        }
