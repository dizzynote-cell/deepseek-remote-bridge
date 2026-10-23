# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
"""Transcript projection from a real-shaped harness session log."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.history_view import project_session

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "harness-session.json"


def load():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return payload["header"], payload["events"]


class TranscriptProjectionTests(unittest.TestCase):
    def setUp(self):
        header, events = load()
        self.thread = project_session(header, events)

    def items(self, turn_id):
        turn = next(turn for turn in self.thread["turns"] if turn["id"] == turn_id)
        return turn["items"]

    def test_title_and_effective_directory(self):
        self.assertEqual(self.thread["name"], "Create demo file")
        # The session header said demo/, a working-directory/change moved it.
        self.assertEqual(self.thread["cwd"], r"D:\workspaces\demo\project-a")

    def test_harness_context_is_not_rendered_as_a_human_message(self):
        users = [item for item in self.items("turn-1") if item["type"] == "userMessage"]
        self.assertEqual(len(users), 1)
        self.assertIn("Create demo.txt", users[0]["content"][0]["text"])
        self.assertEqual(users[0]["bridgeMessageId"], "message-1")

    def test_reasoning_text_and_answer_are_separate_items(self):
        items = self.items("turn-1")
        reasoning = [item for item in items if item["type"] == "reasoning"]
        answers = [item for item in items if item["type"] == "agentMessage"]
        self.assertEqual(reasoning[0]["text"], "A short plan.")
        self.assertEqual([item["text"] for item in answers], ["Writing the file now.", "DONE"])

    def test_tool_call_pairs_arguments_with_result(self):
        tools = [item for item in self.items("turn-1") if item["type"] == "toolCall"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "write")
        self.assertEqual(tools[0]["status"], "completed")
        self.assertIn("demo.txt", tools[0]["arguments"])
        self.assertIn("Wrote", tools[0]["result"])

    def test_usage_is_aggregated_per_turn(self):
        usage = [item for item in self.items("turn-1") if item["type"] == "usage"]
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0]["totalTokens"], 1212)

    def test_turn_statuses(self):
        self.assertEqual([turn["status"] for turn in self.thread["turns"]], ["completed", "interrupted", "in_progress"])
        self.assertEqual(self.thread["status"], "running")

    def test_approval_audit_is_visible(self):
        notices = [item for item in self.items("turn-2") if item["type"] == "notice"]
        self.assertTrue(any("请求批准" in item["text"] for item in notices))
        self.assertTrue(any("已拒绝" in item["text"] for item in notices))
        failed = [item for item in self.items("turn-2") if item["type"] == "toolCall"][0]
        self.assertTrue(failed["isError"])
        self.assertEqual(failed["status"], "failed")

    def test_model_records_the_real_route(self):
        self.assertEqual(self.thread["bridgeModel"], "deepseek-flash")
        self.assertEqual(self.thread["bridgeProvider"], "deepseek-account")

    def test_empty_log_still_projects(self):
        thread = project_session({"id": "session-empty", "cwd": "D:\\workspaces\\empty"}, [])
        self.assertEqual(thread["turns"], [])
        self.assertEqual(thread["status"], "idle")
        self.assertEqual(thread["name"], "empty")


if __name__ == "__main__":
    unittest.main()
