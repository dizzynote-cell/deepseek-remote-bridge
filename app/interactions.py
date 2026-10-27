# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
# Project: DeepSeek Remote Bridge
"""Mirror the harness's open human interactions for the page.

Approvals and ask_user_question requests wait inside the runtime process. The
host adapter exposes them on loopback; this module mirrors them into the shape
the page already renders and forwards decisions back to the exact request.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from .dsh_adapter import AdapterClient, AdapterError


class InteractionBridge:
    """Poll the adapter, keep a stable revision, and forward decisions."""

    def __init__(self, adapter_factory, catalog, poll_interval: float = 1.0):
        self._adapter_factory = adapter_factory
        self.catalog = catalog
        self.poll_interval = poll_interval
        self.lock = threading.RLock()
        self.requests: dict[str, dict[str, Any]] = {}
        self.revision = 0
        self.session_key = uuid.uuid4().hex
        self._destinations = {}
        self._settled = set()
        self._sending = set()
        self.started_at = int(time.time() * 1000)
        self.available = False
        self.last_error = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def adapter(self) -> AdapterClient:
        return self._adapter_factory()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="interactions-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_interval):
            try:
                self.poll()
            except Exception:  # noqa: BLE001 - a poll failure must not kill the loop
                pass

    def poll(self) -> None:
        try:
            sources = self.adapter
            sources = sources if isinstance(sources, dict) else {"": sources}
        except AdapterError as error:
            with self.lock:
                self.available = False
                self.last_error = str(error)
            return
        incoming, destinations, succeeded, failures = {}, {}, 0, []
        for instance, adapter in sources.items():
            try:
                payload = adapter.interactions()
                succeeded += 1
            except AdapterError as error:
                failures.append(str(error))
                continue
            for raw in payload.get("interactions") or []:
                if not isinstance(raw, dict):
                    continue
                request = self._shape(raw)
                if request is not None:
                    native_id = request["id"]
                    request["id"] = f"{instance}:{native_id}" if instance else native_id
                    incoming[request["id"]] = request
                    destinations[request["id"]] = (adapter, native_id)
        with self.lock:
            incoming = {key: value for key, value in incoming.items() if key not in self._settled}
            changed = set(incoming) != set(self.requests)
            if changed:
                self.revision += 1
            self.requests = incoming
            self._destinations = {key: destinations[key] for key in incoming}
            self.available = succeeded > 0
            self.last_error = "" if succeeded else ('; '.join(failures)[:400] or "运行时暂时无法连接")

    def _shape(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        interaction_id = str(raw.get("id") or "")
        if not interaction_id:
            return None
        session_id = raw.get("sessionId")
        kind = str(raw.get("kind") or "approval")
        name = self._session_name(session_id)
        if kind == "questions":
            questions = []
            for question in raw.get("questions") or []:
                if not isinstance(question, dict):
                    continue
                questions.append(
                    {
                        "id": str(question.get("id") or ""),
                        "question": str(question.get("question") or ""),
                        "pc": False,
                        "options": [
                            {
                                "label": str(option.get("label") or ""),
                                "description": option.get("description"),
                            }
                            for option in question.get("options") or []
                            if isinstance(option, dict)
                        ],
                    }
                )
            return {
                "id": interaction_id,
                "kind": "questions",
                "category": "web",
                "threadId": session_id,
                "turnId": None,
                "status": "pending",
                "title": "DeepSeek Harness 需要你的回答",
                "message": name,
                "help": "回答后问题会回到正在运行的工具调用。",
                "questions": questions,
                "fields": [],
                "actions": ["answer"],
                "url": None,
                "details": None,
                "delivery": None,
                "openedAt": raw.get("openedAt"),
                "deadline": raw.get("deadline"),
            }
        tool_name = raw.get("toolName") or "工具"
        reason = raw.get("reason") or ""
        return {
            "id": interaction_id,
            "kind": "approval",
            "category": "web",
            "threadId": session_id,
            "turnId": None,
            "status": "pending",
            "title": f"需要批准：{tool_name}",
            "message": name,
            "help": "批准只对本次请求有效；拒绝后模型会收到失败结果。",
            "questions": [],
            "fields": [],
            "actions": ["accept", "decline"],
            "url": None,
            "details": reason or None,
            "delivery": None,
            "openedAt": raw.get("openedAt"),
            "deadline": raw.get("deadline"),
        }

    def _session_name(self, session_id: Any) -> str:
        if not session_id:
            return "本机运行时请求"
        text = str(session_id)
        record = self.catalog.known(text)
        if record is None:
            return f"会话 {text[:12]}"
        return record.title or f"会话 {text[:12]}"

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            requests = [dict(item) for item in self.requests.values()]
            return {
                "session": self.session_key,
                "startedAt": self.started_at,
                "revision": self.revision,
                "available": self.available,
                "requests": requests,
                "error": self.last_error,
            }

    def respond(self, payload: dict[str, Any]) -> dict[str, Any]:
        interaction_id = str(payload.get("id") or "")
        action = str(payload.get("action") or "")
        with self.lock:
            request = self.requests.get(interaction_id)
            if payload.get("session") != self.session_key:
                raise ValueError("请求实例已变化，请刷新后重试")
            if request and payload.get("threadId") != request.get("threadId"):
                raise ValueError("回答的会话与原请求不一致")
            if payload.get("providerId", "deepseek-harness") != "deepseek-harness":
                raise ValueError("回答属于其他后端")
            destination = self._destinations.get(interaction_id)
        if request is None:
            raise KeyError("该请求已不在等待状态")
        if request["kind"] == "questions":
            if action != "answer":
                raise ValueError("这个问题只接受回答")
            answers = payload.get("answers")
            if not isinstance(answers, dict) or not answers:
                raise ValueError("请回答所有问题")
            shaped = []
            for question in request.get("questions") or []:
                value = answers.get(question["id"])
                if value is None or (isinstance(value, str) and value.strip() == ""):
                    raise ValueError("请回答所有问题")
                text = str(value)
                labels = [option["label"] for option in question.get("options") or []]
                if text in labels:
                    shaped.append({"id": question["id"], "selected": [text]})
                else:
                    shaped.append({"id": question["id"], "selected": [], "custom": text})
            response = {"answers": shaped}
        else:
            outcome = {"accept": "allowed-once", "decline": "rejected", "cancel": "cancelled"}.get(action)
            if outcome is None:
                raise ValueError("不支持的批准动作")
            response = {"outcome": outcome}
        with self.lock:
            if interaction_id in self._sending or interaction_id in self._settled:
                raise KeyError("该请求正在提交或已经处理")
            if self.requests.get(interaction_id) is not request or destination is None:
                raise KeyError("该请求已过期，请刷新")
            self._sending.add(interaction_id)
        try:
            adapter, native_id = destination
            adapter.respond(native_id, response)
        finally:
            with self.lock:
                self._sending.discard(interaction_id)
        with self.lock:
            self._settled.add(interaction_id)
            self.requests.pop(interaction_id, None)
            self._destinations.pop(interaction_id, None)
            self.revision += 1
        return {"status": "answered"}
