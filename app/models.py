# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
"""Model and reasoning-effort catalog for the bridge.

The SDK runtime fixes its route when it boots: one ``provider/model/effort``
triple per process. The bridge therefore runs one runtime per route and can move
a session to another route by restarting that runtime and resuming the session
there (the harness records a new ``request/header`` for the next turn).

The built-in catalog is a fallback for older harness versions. Current runtimes
advertise their authenticated providers, models and reasoning capabilities.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import threading

_lock = threading.RLock()
_discovered: dict[str, dict] = {}


def selection_id(provider: str, model: str) -> str:
    return model if provider == "deepseek-account" else f"{provider}::{model}"


def valid_route_part(value: str) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}", value) is not None


def refresh(adapter) -> bool:
    """Publish an atomic, allowlisted snapshot; never copy raw provider config."""
    incoming = {}
    for item in adapter.models().get("models", []):
        provider, model = item.get("provider", ""), item.get("id", "")
        if not valid_route_part(provider) or not valid_route_part(model):
            continue
        efforts = [{"id": effort["id"], "name": str(effort.get("name") or effort["id"]),
                    "note": str(effort.get("description") or "")}
                   for effort in item.get("efforts", [])
                   if isinstance(effort, dict) and valid_route_part(effort.get("id", ""))]
        if not efforts:
            continue
        default = item.get("defaultEffort")
        if default not in {entry["id"] for entry in efforts}:
            default = efforts[0]["id"]
        key = selection_id(provider, model)
        builtin = next((entry for entry in MODELS if entry["id"] == key), {})
        effort_names = {"off": "关闭", "minimal": "最低", "low": "低", "medium": "中", "high": "高", "xhigh": "超高", "max": "最大", "default": "默认"}
        for entry in efforts:
            entry["name"] = effort_names.get(entry["id"], entry["name"])
        incoming[key] = {"id": key, "provider": provider, "model": model,
                         "name": str(item.get("name") or model), "short": builtin.get("short") or str(item.get("name") or model),
                         "note": provider, "efforts": efforts, "defaultEffort": default,
                         "inputModalities": [mode for mode in item.get("inputModalities", []) if mode in ("text", "image")]}
    if incoming:
        with _lock:
            _discovered.clear()
            _discovered.update(incoming)
    return bool(incoming)


def available() -> list[dict]:
    with _lock:
        return [dict(item) for item in _discovered.values()] if _discovered else [dict(item) for item in MODELS]

MODELS = (
    {
        "id": "deepseek-flash",
        "name": "DeepSeek-V4.1-Flash",
        "short": "V4.1 Flash",
        "note": "文本与图片，日常速度",
        "inputModalities": ["text", "image"],
    },
    {
        "id": "deepseek-v4-pro",
        "name": "DeepSeek-V4-Pro",
        "short": "V4 Pro",
        "note": "更强的编码与复杂推理，成本更高",
        "inputModalities": ["text", "image"],
    },
)

EFFORTS = (
    {"id": "off", "name": "关闭", "note": "不进行推理，最快"},
    {"id": "low", "name": "低", "note": "短推理"},
    {"id": "high", "name": "高", "note": "默认强度"},
    {"id": "max", "name": "最大", "note": "最慢，质量优先"},
)

MODEL_IDS = {model["id"] for model in MODELS}
EFFORT_IDS = {effort["id"] for effort in EFFORTS}


class RouteError(ValueError):
    """Unknown model or effort, with a stable code for the HTTP layer."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Route:
    provider: str
    model: str
    effort: str

    @property
    def label(self) -> str:
        return f"{self.provider}/{self.model}/{self.effort or 'default'}"


def resolve(provider: str, model: str | None, effort: str | None, default_model: str, default_effort: str) -> Route:
    """Validate a requested route, falling back to the configured defaults."""
    # An empty configured effort means "whatever the harness defaults to", which
    # the model package resolves to high; say so instead of failing validation.
    chosen_model = (model or "").strip() or (default_model or "").strip() or "deepseek-flash"
    chosen_effort = (effort or "").strip() or (default_effort or "").strip() or "high"
    entry = next((item for item in available() if item["id"] == chosen_model), None)
    if entry is None:
        raise RouteError("unknown_model", f"未知模型：{chosen_model}")
    efforts = {item["id"] for item in entry.get("efforts", EFFORTS)}
    if not (effort or "").strip() and chosen_effort not in efforts:
        chosen_effort = entry.get("defaultEffort", chosen_effort)
    if chosen_effort not in efforts:
        raise RouteError("unknown_effort", f"未知推理强度：{chosen_effort}")
    return Route(provider=entry.get("provider", provider), model=entry.get("model", chosen_model), effort=chosen_effort)


def catalog(provider: str, bridge_model: str, bridge_effort: str, session: Route | None = None) -> dict:
    """Payload for the composer picker; never claims a route that cannot boot."""
    return {
        "provider": provider,
        "models": available(),
        "efforts": [dict(effort) for effort in EFFORTS],
        "bridgeDefault": {"model": bridge_model, "effort": bridge_effort},
        "session": (
            {"model": selection_id(session.provider, session.model), "effort": session.effort, "route": session.label}
            if session is not None
            else None
        ),
        "note": "切换模型会重启该会话所在的运行时（不影响其他模型上的会话）；进行中的任务需要等它结束。",
    }


def labels(model_id: str, effort_id: str) -> dict:
    model = next((item for item in available() if item["id"] == model_id), None)
    effort = next((item for item in EFFORTS if item["id"] == effort_id), None)
    return {
        "model": model["short"] if model else model_id,
        "modelName": model["name"] if model else model_id,
        "effort": effort["name"] if effort else effort_id,
    }
