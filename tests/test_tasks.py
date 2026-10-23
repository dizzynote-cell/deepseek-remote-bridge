# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
"""Task terminal states, busy-session handling, and queued-input marking."""

from __future__ import annotations

import unittest

from app.dsh_rpc import RpcError
from app.tasks import STATUS_MESSAGE, TASK_TERMINAL, LocalTask, TaskRejected, TaskRunner, is_busy_error


class FakeHandle:
    def __init__(self, script=None):
        import threading

        self.lock = threading.RLock()
        self.opened_sessions = set()
        self.calls = []
        self.script = script or {}

    def call(self, method, params=None, timeout=None):
        self.calls.append((method, params))
        action = self.script.get(method)
        if isinstance(action, Exception):
            raise action
        if callable(action):
            return action(params)
        if method == "session/prompt":
            return {"messageId": "message-new"}
        return {}


class FakePool:
    def __init__(self, handle):
        self.handle = handle

    def acquire(self, key=None, timeout=180.0):
        return self.handle


class FakeRecord:
    cwd = r"D:\workspaces\demo"


class FakeCatalog:
    def __init__(self, events=None):
        self.events = events or []

        class Adapter:
            def __init__(self, events):
                self.events = events

            def session(self, session_id):
                return {"header": {"id": session_id, "cwd": FakeRecord.cwd}, "events": self.events}

        self.adapter = Adapter(self.events)
        self.invalidated = []

    def known(self, session_id):
        return FakeRecord()

    def invalidate(self, session_id=None):
        self.invalidated.append(session_id)


class FakeConfig:
    projects_root = FakeRecord.cwd


def runner(handle=None, events=None):
    handle = handle or FakeHandle()
    catalog = FakeCatalog(events)
    return TaskRunner(FakeConfig(), FakePool(handle), catalog, None), handle, catalog


def completed_events(message_id="message-new", turn=1, finish="completed"):
    return [
        {"type": "turn/start", "seq": 0, "time": 1, "data": {"turn": turn}},
        {"type": "user/message", "seq": 1, "time": 2, "data": {"content": [{"type": "text", "text": "hi"}], "source": {"kind": "user"}, "id": message_id}},
        {"type": "turn/end", "seq": 2, "time": 3, "data": {"turn": turn, "reason": {"kind": finish}}},
    ]


class TaskTests(unittest.TestCase):
    def test_busy_session_maps_to_a_rejection_without_resending(self):
        handle = FakeHandle({"session/resume": RpcError("session already owned by another process")})
        tasks, handle, _ = runner(handle)
        with self.assertRaises(TaskRejected) as caught:
            tasks.submit("session-demo", [{"type": "text", "text": "hello"}])
        self.assertEqual(caught.exception.code, "session_busy")
        self.assertEqual(caught.exception.http_status, 409)
        self.assertNotIn("session/prompt", [method for method, _ in handle.calls])
        self.assertTrue(is_busy_error(RpcError("SessionAlreadyOwnedError")))

    def test_prompt_failure_is_reported_not_retried(self):
        handle = FakeHandle({"session/prompt": RpcError("model route unavailable")})
        tasks, handle, _ = runner(handle)
        with self.assertRaises(TaskRejected) as caught:
            tasks.submit("session-demo", [{"type": "text", "text": "hello"}])
        self.assertEqual(caught.exception.code, "prompt_failed")
        self.assertEqual([method for method, _ in handle.calls].count("session/prompt"), 1)

    def test_empty_content_is_refused(self):
        tasks, _, _ = runner()
        with self.assertRaises(TaskRejected):
            tasks.submit("session-demo", [])

    def test_resume_happens_once_per_session(self):
        tasks, handle, _ = runner()
        tasks.submit("session-demo", [{"type": "text", "text": "one"}])
        tasks.submit("session-demo", [{"type": "text", "text": "two"}])
        self.assertEqual([method for method, _ in handle.calls].count("session/resume"), 1)

    def test_terminal_state_mapping(self):
        cases = {
            "completed": "completed",
            "interrupted": "interrupted",
            "cancelled": "cancelled",
            "max-tokens": "completed",
            "error": "failed",
        }
        for reason, expected in cases.items():
            tasks, _, _ = runner(events=completed_events(finish=reason))
            task = LocalTask(id="t", session_id="session-demo", cwd=None, created_at=0.0)
            from app.history_view import project_session

            thread = project_session({"id": "session-demo", "cwd": None}, completed_events(finish=reason))
            status, turn_id = tasks._terminal_state(thread, "message-new", None)
            self.assertEqual(status, expected, reason)
            self.assertEqual(turn_id, "turn-1")

    def test_turn_lookup_uses_the_prompt_message_id(self):
        events = completed_events(message_id="other") + completed_events(message_id="message-new", turn=2)
        from app.history_view import project_session

        thread = project_session({"id": "session-demo", "cwd": None}, events)
        tasks, _, _ = runner()
        status, turn_id = tasks._terminal_state(thread, "message-new", None)
        self.assertEqual((status, turn_id), ("completed", "turn-2"))

    def test_unfinished_turn_is_reported_as_interrupted(self):
        events = [
            {"type": "turn/start", "seq": 0, "time": 1, "data": {"turn": 1}},
            {"type": "user/message", "seq": 1, "time": 2, "data": {"content": [{"type": "text", "text": "hi"}], "source": {"kind": "user"}, "id": "message-new"}},
        ]
        from app.history_view import project_session

        thread = project_session({"id": "session-demo", "cwd": None}, events)
        tasks, _, _ = runner()
        status, turn_id = tasks._terminal_state(thread, "message-new", None)
        self.assertEqual((status, turn_id), ("interrupted", "turn-1"))

    def test_terminal_statuses_have_user_messages(self):
        for status in TASK_TERMINAL:
            self.assertIn(status, STATUS_MESSAGE)

    def test_active_task_is_released_on_finish(self):
        tasks, _, catalog = runner()
        task = LocalTask(id="t", session_id="session-demo", cwd=None, created_at=0.0)
        tasks._register(task)
        self.assertTrue(tasks.is_running("session-demo"))
        tasks._finish(task, "completed")
        self.assertFalse(tasks.is_running("session-demo"))
        self.assertEqual(catalog.invalidated, ["session-demo"])
        self.assertEqual(task.message, "任务已完成")


if __name__ == "__main__":
    unittest.main()
