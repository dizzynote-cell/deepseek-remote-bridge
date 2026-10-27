# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
"""Approval/question mirroring and answer routing."""

from __future__ import annotations

import unittest

from app.dsh_adapter import AdapterError
from app.interactions import InteractionBridge


class FakeAdapter:
    def __init__(self, payload=None):
        self.payload = payload or {"interactions": []}
        self.calls = []
        self.raises = None

    def interactions(self, session_id=None):
        if self.raises is not None:
            raise self.raises
        return self.payload

    def respond(self, interaction_id, payload):
        self.calls.append((interaction_id, payload))
        return {"ok": True}


class FakeCatalog:
    def known(self, session_id):
        class Record:
            title = "演示会话"

        return Record() if session_id else None


def bridge(payload):
    adapter = FakeAdapter(payload)
    service = InteractionBridge(lambda: adapter, FakeCatalog(), poll_interval=3600)
    return service, adapter


APPROVAL = {
    "interactions": [
        {
            "id": "approval-1",
            "kind": "approval",
            "sessionId": "session-demo",
            "toolName": "pwsh",
            "callId": "call-1",
            "reason": "escalate sandbox to danger-full-access",
            "openedAt": 1800000000000,
            "deadline": 1800000900000,
        }
    ]
}

QUESTIONS = {
    "interactions": [
        {
            "id": "questions-1",
            "kind": "questions",
            "sessionId": "session-demo",
            "questions": [
                {"id": "q1", "question": "Which colour?", "options": [{"label": "red"}, {"label": "blue"}]},
            ],
            "openedAt": 1800000000000,
            "deadline": 1800000900000,
        }
    ]
}


class InteractionTests(unittest.TestCase):
    def test_nondefault_route_is_polled_and_answered_at_its_own_adapter(self):
        import copy
        first, second = FakeAdapter(), FakeAdapter(copy.deepcopy(APPROVAL))
        service = InteractionBridge(lambda: {"route-a": first, "route-b": second}, FakeCatalog())
        service.poll()
        request = service.snapshot()["requests"][0]
        self.assertEqual(request["id"], "route-b:approval-1")
        service.respond({"id": request["id"], "session": service.session_key,
                         "threadId": "session-demo", "action": "accept"})
        self.assertEqual(first.calls, [])
        self.assertEqual(second.calls, [("approval-1", {"outcome": "allowed-once"})])
        service.poll()
        self.assertEqual(service.snapshot()["requests"], [])

    def test_wrong_instance_session_or_provider_cannot_answer(self):
        for patch in [{"session": "old"}, {"threadId": "other"}, {"providerId": "codex"}]:
            service, adapter = bridge(APPROVAL)
            service.poll()
            payload = {"id": "approval-1", "session": service.session_key,
                       "threadId": "session-demo", "action": "accept", **patch}
            with self.assertRaises(ValueError):
                service.respond(payload)
            self.assertEqual(adapter.calls, [])

    def test_approval_is_shaped_for_the_page(self):
        service, _ = bridge(APPROVAL)
        service.poll()
        snapshot = service.snapshot()
        self.assertTrue(snapshot["available"])
        request = snapshot["requests"][0]
        self.assertEqual(request["kind"], "approval")
        self.assertEqual(request["actions"], ["accept", "decline"])
        self.assertIn("pwsh", request["title"])
        self.assertEqual(request["threadId"], "session-demo")

    def test_accept_maps_to_allowed_once(self):
        service, adapter = bridge(APPROVAL)
        service.poll()
        service.respond({"session": service.session_key, "threadId": "session-demo", "id": "approval-1", "action": "accept"})
        self.assertEqual(adapter.calls, [("approval-1", {"outcome": "allowed-once"})])
        self.assertEqual(service.snapshot()["requests"], [])

    def test_decline_maps_to_rejected(self):
        service, adapter = bridge(APPROVAL)
        service.poll()
        service.respond({"session": service.session_key, "threadId": "session-demo", "id": "approval-1", "action": "decline"})
        self.assertEqual(adapter.calls[0][1], {"outcome": "rejected"})

    def test_question_answers_by_option_label(self):
        service, adapter = bridge(QUESTIONS)
        service.poll()
        service.respond({"session": service.session_key, "threadId": "session-demo", "id": "questions-1", "action": "answer", "answers": {"q1": "blue"}})
        self.assertEqual(adapter.calls[0][1], {"answers": [{"id": "q1", "selected": ["blue"]}]})

    def test_free_text_becomes_a_custom_answer(self):
        service, adapter = bridge(QUESTIONS)
        service.poll()
        service.respond({"session": service.session_key, "threadId": "session-demo", "id": "questions-1", "action": "answer", "answers": {"q1": "purple"}})
        self.assertEqual(
            adapter.calls[0][1],
            {"answers": [{"id": "q1", "selected": [], "custom": "purple"}]},
        )

    def test_blank_answer_is_refused(self):
        service, adapter = bridge(QUESTIONS)
        service.poll()
        with self.assertRaises(ValueError):
            service.respond({"session": service.session_key, "threadId": "session-demo", "id": "questions-1", "action": "answer", "answers": {"q1": "   "}})
        self.assertEqual(adapter.calls, [])

    def test_unknown_or_settled_interaction_is_refused(self):
        service, _ = bridge(APPROVAL)
        service.poll()
        service.respond({"session": service.session_key, "threadId": "session-demo", "id": "approval-1", "action": "accept"})
        with self.assertRaises(KeyError):
            service.respond({"session": service.session_key, "threadId": "session-demo", "id": "approval-1", "action": "accept"})

    def test_revision_moves_only_when_the_set_changes(self):
        service, adapter = bridge(APPROVAL)
        service.poll()
        first = service.snapshot()["revision"]
        service.poll()
        self.assertEqual(service.snapshot()["revision"], first)
        adapter.payload = {"interactions": []}
        service.poll()
        self.assertGreater(service.snapshot()["revision"], first)

    def test_adapter_failure_marks_the_panel_unavailable(self):
        service, adapter = bridge(APPROVAL)
        adapter.raises = AdapterError("unreachable")
        service.poll()
        snapshot = service.snapshot()
        self.assertFalse(snapshot["available"])
        self.assertIn("unreachable", snapshot["error"])


if __name__ == "__main__":
    unittest.main()
