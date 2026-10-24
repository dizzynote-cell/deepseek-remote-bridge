# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
"""Model and reasoning-effort catalog for the bridge.

The SDK runtime fixes its route when it boots: one ``provider/model/effort``
triple per process. The bridge therefore runs one runtime per route and can move
a session to another route by restarting that runtime and resuming the session
there (the harness records a new ``request/header`` for the next turn).

The catalog below mirrors the models shipped with DeepSeek Harness. It is a
static list on purpose: the bridge will not offer a model it has never booted.
"""

from __future__ import annotations

from dataclasses import dataclass

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
    if chosen_model not in MODEL_IDS:
        raise RouteError("unknown_model", f"未知模型：{chosen_model}")
    if chosen_effort not in EFFORT_IDS:
        raise RouteError("unknown_effort", f"未知推理强度：{chosen_effort}")
    return Route(provider=provider, model=chosen_model, effort=chosen_effort)


def catalog(provider: str, bridge_model: str, bridge_effort: str, session: Route | None = None) -> dict:
    """Payload for the composer picker; never claims a route that cannot boot."""
    return {
        "provider": provider,
        "models": [dict(model) for model in MODELS],
        "efforts": [dict(effort) for effort in EFFORTS],
        "bridgeDefault": {"model": bridge_model, "effort": bridge_effort},
        "session": (
            {"model": session.model, "effort": session.effort, "route": session.label}
            if session is not None
            else None
        ),
        "note": "切换模型会重启该会话所在的运行时（不影响其他模型上的会话）；进行中的任务需要等它结束。",
    }


def labels(model_id: str, effort_id: str) -> dict:
    model = next((item for item in MODELS if item["id"] == model_id), None)
    effort = next((item for item in EFFORTS if item["id"] == effort_id), None)
    return {
        "model": model["short"] if model else model_id,
        "modelName": model["name"] if model else model_id,
        "effort": effort["name"] if effort else effort_id,
    }
