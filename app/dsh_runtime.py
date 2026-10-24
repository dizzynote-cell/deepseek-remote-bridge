# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
# Project: DeepSeek Remote Bridge
"""Own the DeepSeek Harness SDK runtime processes the bridge drives.

One runtime serves every session that shares its model route. The bridge keeps a
small pool keyed by ``(provider, model, reasoningEffort)`` so a session resumed
under the model it last used keeps that behaviour.

The bridge writes its own profile *patch* file and passes it with ``--patch``;
the shipped ``sdk`` profile and the user's global DSH profile stay untouched.
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import BridgeConfig
from .dsh_rpc import NotificationBuffer, RpcError, RuntimeProcess


class RenderError(ValueError):
    """A generated patch value would escape its YAML scalar."""


class RuntimeUnavailable(RuntimeError):
    """The runtime could not be started or answered with an initialization error."""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@dataclass(frozen=True)
class RouteKey:
    provider: str
    model: str
    reasoning_effort: str

    @property
    def label(self) -> str:
        parts = [self.provider, self.model]
        if self.reasoning_effort:
            parts.append(self.reasoning_effort)
        return "/".join(parts)


class RuntimeHandle:
    """One initialized SDK runtime plus its adapter endpoint."""

    def __init__(self, key: RouteKey, process: RuntimeProcess, adapter_port: int, adapter_token: str):
        self.key = key
        self.process = process
        self.adapter_port = adapter_port
        self.adapter_token = adapter_token
        self.lock = threading.RLock()
        self.opened_sessions: set[str] = set()
        self.server_info: dict[str, Any] = {}

    @property
    def alive(self) -> bool:
        return self.process.alive

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        return self.process.call(method, params, timeout)


class RuntimePool:
    """Start, reuse, and recycle SDK runtimes for the bridge."""

    def __init__(self, config: BridgeConfig):
        self.config = config
        self.lock = threading.RLock()
        self.handles: dict[RouteKey, RuntimeHandle] = {}
        self.notifications = NotificationBuffer(2000)
        self.port_allocations: set[int] = set()
        self.last_error = ""

    # -- patch generation ----------------------------------------------
    def _patch_path(self, key: RouteKey) -> Path:
        slug = "-".join(part.replace("/", "_") for part in (key.provider, key.model, key.reasoning_effort or "default"))
        return self.config.runtime_dir / f"patch-{slug}.yml"

    def _write_patch(self, key: RouteKey, adapter_port: int, token: str) -> Path:
        """Write the profile patch that mounts the host adapter and fixes the preset.

        The patch file is rewritten on every start so the adapter port and token
        always match the live process. Secrets stay inside the bridge home.
        """
        patch = {
            "adapter": {
                "file": str(self.config.root / "adapter" / "index.js"),
                "port": adapter_port,
                "token": token,
                "clientVersion": self.config.version,
                "approvalTimeoutMs": self.config.approval_timeout_ms,
                "questionTimeoutMs": self.config.question_timeout_ms,
            },
            "preset": self.config.permission_preset,
            "route": {"provider": key.provider, "model": key.model, "reasoningEffort": key.reasoning_effort},
        }
        path = self._patch_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_patch(patch), encoding="utf-8")
        return path

    # -- acquisition ----------------------------------------------------
    def _command(self, patch: Path) -> list[str]:
        command = [str(self.config.dsh_exe), "--expose-internals", str(self.config.dsh_cli), "--profile", "sdk"]
        command += ["--patch", str(patch)]
        return command

    def _environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env["ELECTRON_RUN_AS_NODE"] = "1"
        if self.config.dsh_home is not None:
            env["DSH_HOME"] = str(self.config.dsh_home)
        # The sdk profile ships `tool-workspace-dependencies` disabled until this
        # path is present; it tells the model where the bundled Python/Office
        # dependencies live, exactly as in the desktop app.
        primary_runtime = self.primary_runtime_dir()
        if primary_runtime is not None and "DSH_PRIMARY_RUNTIME" not in env:
            env["DSH_PRIMARY_RUNTIME"] = str(primary_runtime)
        env.setdefault("NO_COLOR", "1")
        return env

    def primary_runtime_dir(self):
        """Locate the bundled runtime payload next to the installed harness."""
        try:
            root = Path(str(self.config.dsh_exe)).parent
        except (TypeError, ValueError):
            return None
        for candidate in (root / "resources" / "runtime" / "primary-runtime", root / "runtime" / "primary-runtime"):
            try:
                if (candidate / "runtime.json").is_file():
                    return candidate
            except OSError:
                continue
        return None

    def acquire(self, key: RouteKey | None = None, timeout: float = 180.0) -> RuntimeHandle:
        """Return a ready runtime for ``key``, starting one when needed."""
        key = key or self.default_key()
        with self.lock:
            handle = self.handles.get(key)
            if handle is not None and handle.alive:
                return handle
            if handle is not None:
                self.handles.pop(key, None)
                try:
                    handle.process.kill()
                except Exception:
                    pass
            handle = self._start(key, timeout)
            self.handles[key] = handle
            return handle

    def default_key(self) -> RouteKey:
        return RouteKey(self.config.provider, self.config.model, self.config.reasoning_effort)

    def _start(self, key: RouteKey, timeout: float) -> RuntimeHandle:
        adapter_port = _free_port()
        token = self.config.adapter_token
        patch = self._write_patch(key, adapter_port, token)
        stderr_log = self.config.logs_dir / f"runtime-{key.label.replace('/', '-').replace(':', '-')}.log"
        process = RuntimeProcess(
            command=self._command(patch),
            env=self._environment(),
            cwd=self.config.runtime_dir,
            stderr_log=stderr_log,
            on_notification=self._on_notification,
            on_exit=self._on_exit,
            default_timeout=120.0,
        )
        handle = RuntimeHandle(key, process, adapter_port, token)
        try:
            params: dict[str, Any] = {
                "cwd": str(self.config.projects_root),
                "provider": key.provider,
                "model": key.model,
            }
            if key.reasoning_effort:
                params["reasoningEffort"] = key.reasoning_effort
            if self.config.max_tokens > 0:
                params["maxTokens"] = self.config.max_tokens
            result = process.call("initialize", params, timeout=timeout)
            handle.server_info = (result or {}).get("serverInfo") or {}
        except RpcError as error:
            self.last_error = str(error)
            process.kill()
            raise RuntimeUnavailable(f"cannot initialize the DeepSeek Harness runtime: {error}") from None
        return handle

    # -- callbacks ------------------------------------------------------
    def _on_notification(self, method: str, params: dict[str, Any]) -> None:
        self.notifications.add(method, params)

    def _on_exit(self, reason: str) -> None:
        self.last_error = reason

    # -- teardown -------------------------------------------------------
    def release(self, key: RouteKey) -> None:
        """Stop one route's runtime, leaving other routes untouched."""
        with self.lock:
            handle = self.handles.pop(key, None)
        if handle is None:
            return
        try:
            handle.process.close()
        except Exception:  # noqa: BLE001 - a dead runtime is already released
            pass

    def keys(self) -> list[RouteKey]:
        with self.lock:
            return list(self.handles.keys())

    def shutdown(self) -> None:
        with self.lock:
            handles = list(self.handles.values())
            self.handles.clear()
        for handle in handles:
            try:
                handle.process.close()
            except Exception:
                pass


def render_patch(patch: dict[str, Any]) -> str:
    """Render the YAML profile patch consumed by ``dsh --patch``."""
    adapter = patch["adapter"]
    plugin_name = _yaml_scalar(
        "file:///" + str(adapter["file"]).replace("\\", "/"), "adapter file", r"[A-Za-z0-9._~:/+-]+"
    )
    token = _yaml_scalar(adapter["token"], "adapter token", r"[A-Za-z0-9_-]{8,200}")
    client_version = _yaml_scalar(adapter["clientVersion"], "client version", r"[A-Za-z0-9._-]{1,40}")
    lines = [
        "# Generated by the DeepSeek Remote Bridge. Do not edit by hand.",
        "# Mounts the loopback host adapter into the shipped sdk profile.",
        "- insert:",
        "    - id: bridge-adapter",
        f"      name: '{plugin_name}'",
        "      config:",
        f"        port: {int(adapter['port'])}",
        f"        token: '{token}'",
        f"        clientVersion: '{client_version}'",
        f"        approvalTimeoutMs: {int(adapter['approvalTimeoutMs'])}",
        f"        questionTimeoutMs: {int(adapter['questionTimeoutMs'])}",
        # The shipped sdk profile composes the user-questions service but not its
        # model-facing tool; the bridge supports asking, so mount the official tool.
        "    - id: bridge-tool-ask-user",
        "      name: '@deepseek-ai/dsh-tool-ask-user'",
        # Presenting deliverables is model-facing in the desktop profile; the sdk
        # profile leaves it out, so mount the same official plugin here.
        "    - id: bridge-tool-present",
        "      name: '@deepseek-ai/dsh-tool-present'",
    ]
    if patch.get("preset"):
        preset = _yaml_scalar(patch["preset"], "permission preset", r"[a-z][a-z0-9-]{0,40}")
        lines += [
            "- id: permission",
            "  config:",
            f"    defaultPreset: '{preset}'",
        ]
    return "\n".join(lines) + "\n"


def _yaml_scalar(value: Any, field: str, pattern: str) -> str:
    """Escape-check one generated scalar so the patch cannot be rewritten."""
    text = str(value)
    if re.fullmatch(pattern, text) is None:
        raise RenderError(f"{field} contains characters that are not allowed in the generated patch")
    return text


def stage_diagnostics(config: BridgeConfig) -> dict[str, Any]:
    """Describe where the bridge expects the runtime, without touching credentials."""
    return {
        "dshExe": str(config.dsh_exe),
        "dshExeExists": config.dsh_exe.exists(),
        "dshCli": str(config.dsh_cli),
        "dshCliExists": _inside_existing_archive(config.dsh_cli),
        "dshHome": str(config.dsh_home) if config.dsh_home else None,
        "adapterFile": str(config.root / "adapter" / "index.js"),
        "adapterFileExists": (config.root / "adapter" / "index.js").exists(),
        "generatedAt": int(time.time()),
    }


def _inside_existing_archive(path) -> bool:
    """A path inside app.asar cannot be probed directly; check the archive itself."""
    text = str(path)
    marker = ".asar"
    index = text.lower().find(marker)
    if index < 0:
        return path.exists()
    archive = text[: index + len(marker)]
    return Path(archive).exists()