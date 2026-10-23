# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
# Project: DeepSeek Remote Bridge
"""Project DeepSeek Harness session events into the bridge's chat transcript.

The projection is deliberately pure: it takes the header and the event list that
the adapter read from the real session log and returns plain dictionaries. It
never invents events, summaries, or reasoning text that the harness did not log.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

TERMINAL_KINDS = {
    "completed": "completed",
    "max-tokens": "completed",
    "cancelled": "cancelled",
    "interrupted": "interrupted",
    "error": "failed",
    "failed": "failed",
}

#: Tool names whose result already appears as a compact window in the UI.
CHAT_TOOL_NAMES = {
    "read",
    "write",
    "edit",
    "glob",
    "grep",
    "pwsh",
    "bash",
    "todo_write",
    "present",
    "ask_user_question",
}

MAX_TOOL_RESULT_CHARS = 4000


def _text_of_blocks(blocks: Iterable[Any]) -> str:
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n\n".join(part for part in parts if part)


def _reasoning_of_blocks(blocks: Iterable[Any]) -> str:
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "reasoning":
            text = block.get("text") or block.get("summary")
            if isinstance(text, str) and text.strip():
                parts.append(text)
    return "\n\n".join(parts)


def _truncate(value: str, limit: int = MAX_TOOL_RESULT_CHARS) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 40] + f"\n…（已截断，共 {len(value)} 字符）"


def _format_arguments(raw: Any) -> str:
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return raw
    else:
        parsed = raw
    try:
        return json.dumps(parsed, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(parsed)


@dataclass
class TurnAccumulator:
    number: int
    items: list[dict[str, Any]] = field(default_factory=list)
    status: str = "in_progress"
    model: dict[str, Any] | None = None
    started_at: int = 0
    ended_at: int = 0

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": f"turn-{self.number}",
            "status": self.status,
            "items": self.items,
            "bridgeTurn": self.number,
        }
        if self.model:
            result["bridgeModel"] = self.model
        if self.started_at:
            result["bridgeStartedAt"] = self.started_at
        if self.ended_at:
            result["bridgeEndedAt"] = self.ended_at
        return result


class TranscriptProjector:
    """Fold one session's event log into display turns."""

    def __init__(self, header: dict[str, Any], events: list[dict[str, Any]]):
        self.header = header
        self.events = events
        self.turns: dict[int, TurnAccumulator] = {}
        self.title: str = ""
        self.model: dict[str, Any] | None = None
        self.pending_tool_calls: dict[str, dict[str, Any]] = {}
        self.user_sequence: list[dict[str, Any]] = []
        self._active_turn: int = 0
        self.effective_cwd: str | None = None

    def _turn(self, number: int) -> TurnAccumulator:
        if number not in self.turns:
            self.turns[number] = TurnAccumulator(number=number)
        return self.turns[number]

    def project(self) -> dict[str, Any]:
        for event in self.events:
            if not isinstance(event, dict):
                continue
            handler = getattr(self, "_on_" + re.sub(r"[^0-9a-zA-Z]+", "_", str(event.get("type", ""))).strip("_"), None)
            if handler is not None:
                handler(event)
        turns = [self.turns[number].as_dict() for number in sorted(self.turns)]
        session_id = str(self.header.get("id") or "")
        return {
            "id": session_id,
            "name": self.title or _fallback_name(self.header, self.events),
            "cwd": self.effective_cwd or self.header.get("cwd"),
            "createdAt": self.header.get("createdAt"),
            "status": _session_status(turns),
            "turns": turns,
            "bridgeTotalTurns": len(turns),
            "bridgeProvider": (self.model or {}).get("provider"),
            "bridgeModel": (self.model or {}).get("model"),
            "bridgeAgentPreset": self.header.get("agentPreset"),
        }

    # -- individual events ---------------------------------------------
    def _on_turn_start(self, event: dict[str, Any]) -> None:
        data = event.get("data") or {}
        number = int(data.get("turn") or 0)
        turn = self._turn(number)
        self._active_turn = number
        turn.started_at = int(event.get("time") or 0)

    def _on_turn_end(self, event: dict[str, Any]) -> None:
        data = event.get("data") or {}
        number = int(data.get("turn") or 0)
        turn = self._turn(number)
        self._active_turn = number
        reason = data.get("reason") or {}
        kind = reason.get("kind") if isinstance(reason, dict) else None
        turn.status = TERMINAL_KINDS.get(str(kind), "failed")
        turn.ended_at = int(event.get("time") or 0)
        if turn.status == "failed" and isinstance(reason, dict) and reason.get("message"):
            turn.items.append({"type": "notice", "level": "error", "text": str(reason["message"])})

    def _on_user_message(self, event: dict[str, Any]) -> None:
        data = event.get("data") or {}
        message = data.get("message") if isinstance(data.get("message"), dict) else data
        content = message.get("content") or []
        text = _text_of_blocks(content)
        images = [block for block in content if isinstance(block, dict) and block.get("type") == "image"]
        source = data.get("source") if isinstance(data.get("source"), dict) else {}
        source_kind = str((source or {}).get("kind") or "user")
        number = int(data.get("turn") or 0) or self._active_turn
        if number == 0:
            # A message that has not opened a turn yet is queued input.
            self.user_sequence.append({"text": text, "images": images, "time": event.get("time")})
            return
        turn = self._turn(number)
        if source_kind != "user":
            # Harness-authored context (working directory, runtime snapshot, skill
            # catalogue) is model context, not something a human typed.
            if source_kind in {"user-question-reply", "user-approval", "command"} and text.strip():
                turn.items.append({"type": "notice", "level": "info", "text": text})
            return
        turn.items.append(
            {
                "type": "userMessage",
                "content": [{"type": "text", "text": text}] if text else [],
                "bridgeImageCount": len(images),
                "bridgeMessageId": message.get("id") or data.get("id"),
                "bridgeSource": source_kind,
            }
        )

    def _on_assistant_message(self, event: dict[str, Any]) -> None:
        data = event.get("data") or {}
        number = int(data.get("turn") or 0)
        turn = self._turn(number)
        message = data.get("message") or {}
        content = message.get("content") or []
        source = message.get("source") or {}
        if isinstance(source, dict) and source.get("provider"):
            self.model = {"provider": source.get("provider"), "model": source.get("model")}
            turn.model = self.model
        reasoning = _reasoning_of_blocks(content)
        if reasoning.strip():
            turn.items.append({"type": "reasoning", "text": reasoning})
        text = _text_of_blocks(content)
        if text.strip():
            turn.items.append({"type": "agentMessage", "text": text, "phase": "final"})
        usage = data.get("usage")
        if isinstance(usage, dict) and usage:
            turn.items = [item for item in turn.items if item.get("type") != "usage"]
            turn.items.append(
                {
                    "type": "usage",
                    "inputTokens": usage.get("inputTokens"),
                    "outputTokens": usage.get("outputTokens"),
                    "totalTokens": usage.get("totalTokens"),
                }
            )

    def _on_tool_call(self, event: dict[str, Any]) -> None:
        data = event.get("data") or {}
        number = int(data.get("turn") or 0)
        turn = self._turn(number)
        call_id = str(data.get("callId") or "")
        item = {
            "type": "toolCall",
            "name": str(data.get("name") or "tool"),
            "callId": call_id,
            "arguments": _format_arguments(data.get("arguments")),
            "status": "running",
            "result": "",
            "isError": False,
        }
        self.pending_tool_calls[call_id] = item
        turn.items.append(item)

    def _on_tool_result(self, event: dict[str, Any]) -> None:
        data = event.get("data") or {}
        message = data.get("message") or {}
        call_id = str(data.get("toolCallId") or message.get("toolCallId") or "")
        content = message.get("content") or []
        text = _text_of_blocks(content) or _describe_non_text_content(content)
        item = self.pending_tool_calls.get(call_id)
        if item is None:
            number = int(data.get("turn") or 0)
            turn = self._turn(number)
            item = {
                "type": "toolCall",
                "name": str(data.get("toolName") or "tool"),
                "callId": call_id,
                "arguments": "",
                "status": "completed",
                "result": "",
                "isError": False,
            }
            turn.items.append(item)
        item["status"] = "failed" if message.get("isError") else "completed"
        item["isError"] = bool(message.get("isError"))
        item["result"] = _truncate(text)

    def _on_approval_asked(self, event: dict[str, Any]) -> None:
        data = event.get("data") or {}
        turn = self._turn(self._active_turn)
        turn.items.append(
            {
                "type": "notice",
                "level": "approval",
                "text": f"请求批准：{data.get('toolName') or '工具'}"
                + (f" — {data.get('reason')}" if data.get("reason") else ""),
                "toolName": data.get("toolName"),
                "reason": data.get("reason"),
                "approvalId": data.get("id"),
            }
        )

    def _on_approval_decided(self, event: dict[str, Any]) -> None:
        data = event.get("data") or {}
        turn = self._turn(self._active_turn)
        turn.items.append(
            {
                "type": "notice",
                "level": "approval",
                "text": f"批准结果：{_outcome_label(data.get('outcome'))}",
                "approvalId": data.get("id"),
                "outcome": data.get("outcome"),
            }
        )

    def _on_session_title(self, event: dict[str, Any]) -> None:
        data = event.get("data") or {}
        title = data.get("title")
        if isinstance(title, str) and title.strip():
            self.title = title.strip()

    def _on_working_directory_change(self, event: dict[str, Any]) -> None:
        data = event.get("data") or {}
        cwd = data.get("cwd")
        if isinstance(cwd, str) and cwd:
            self.effective_cwd = cwd

    def _on_permission_preset(self, event: dict[str, Any]) -> None:
        data = event.get("data") or {}
        self._append_notice(f"权限预设：{data.get('preset')}", event)

    def _on_sandbox_mode(self, event: dict[str, Any]) -> None:
        data = event.get("data") or {}
        self._append_notice(f"沙箱模式：{data.get('mode')}", event)

    def _append_notice(self, text: str, event: dict[str, Any]) -> None:
        number = self._active_turn
        if number <= 0:
            return
        self._turn(number).items.append({"type": "notice", "level": "info", "text": text})


def _describe_non_text_content(content: Iterable[Any]) -> str:
    kinds: list[str] = []
    for block in content:
        if isinstance(block, dict):
            kinds.append(str(block.get("type") or "unknown"))
    return "（非文本结果：" + ", ".join(sorted(set(kinds))) + "）" if kinds else ""



def _outcome_label(outcome: Any) -> str:
    return {
        "allowed-once": "已允许本次",
        "rejected": "已拒绝",
        "cancelled": "已取消",
        "unavailable": "无人应答（失败关闭）",
    }.get(str(outcome), str(outcome or "未知"))


def _session_status(turns: list[dict[str, Any]]) -> str:
    if not turns:
        return "idle"
    last = turns[-1]
    status = last.get("status")
    if status in {"completed", "failed", "interrupted", "cancelled"}:
        return "idle"
    return "running"


def _fallback_name(header: dict[str, Any], events: list[dict[str, Any]]) -> str:
    for event in events:
        if event.get("type") == "user/message":
            data = event.get("data") or {}
            text = _text_of_blocks((data.get("message") or data).get("content") or [])
            if text.strip():
                first = text.strip().splitlines()[0]
                return first[:40]
    cwd = header.get("cwd") or ""
    return str(cwd).replace("\\", "/").rstrip("/").split("/")[-1] or "未命名会话"


def project_session(header: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    return TranscriptProjector(header, events).project()


@dataclass
class SessionSummary:
    """Compact row for the sidebar."""

    id: str
    name: str
    cwd: str
    created_at: int
    updated_at: int
    live: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": "idle",
            "cwd": self.cwd,
            "bridgeCreatedAt": self.created_at,
            "bridgeUpdatedAt": self.updated_at,
            "bridgeLive": self.live,
        }
