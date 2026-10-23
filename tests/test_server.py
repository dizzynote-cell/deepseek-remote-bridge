# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
"""HTTP surface: base-path routing, static safety, CSRF fence, thread shaping."""

from __future__ import annotations

import json
import tempfile
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import BridgeConfig
from app.server import BridgeRequestHandler, BridgeServer, _content_blocks, _path_allowed


class FakeTasks:
    def is_running(self, session_id):
        return session_id == "session-running"

    def active_task_for(self, session_id):
        return None

    def status(self, task_id):
        return None


class FakeCatalog:
    last_error = ""

    def rows(self, project_filter=None, limit=50, offset=0):
        return [
            {"id": "session-running", "name": "运行中的会话", "status": "空闲", "cwd": r"D:\workspaces\demo"},
            {"id": "session-idle", "name": "空闲会话", "status": "空闲", "cwd": r"D:\workspaces\demo"},
        ]

    def projects(self):
        return [{"cwd": r"D:\workspaces\demo", "name": "demo", "count": 2}]

    def thread(self, session_id):
        return {
            "id": session_id,
            "name": "演示",
            "cwd": r"D:\workspaces\demo",
            "status": "idle",
            "turns": [
                {
                    "id": "turn-1",
                    "status": "completed",
                    "items": [
                        {"type": "userMessage", "content": [{"type": "text", "text": "hello"}]},
                        {"type": "toolCall", "name": "write", "arguments": "{}", "result": "wrote demo.txt", "status": "completed"},
                    ],
                }
            ],
            "bridgeTotalTurns": 1,
            "bridgeUpdatedAt": 1800000000,
        }

    def known(self, session_id):
        return None


class FakeBalance:
    def snapshot_for_page(self, force=False):
        return {"available": True, "entries": [], "display": {"label": "余额 ¥1.00", "primary": None}}


class FakeState:
    def __init__(self, config, web_dir):
        self.config = config
        self.catalog = FakeCatalog()
        self.tasks = FakeTasks()
        self.balance = FakeBalance()
        self.returned_files = type(
            "Returned",
            (),
            {
                "allowed": staticmethod(lambda path: str(path).startswith(str(web_dir))),
                "collect": staticmethod(lambda texts: []),
            },
        )()
        self.interactions = type("Interactions", (), {"snapshot": lambda self: {"requests": []}, "respond": lambda self, payload: {"status": "answered"}})()

    def health(self):
        return {"ok": True}

    def bridge_info(self):
        return {"contractVersion": 1, "providerId": "deepseek-harness", "capabilities": {}}

    def status(self):
        return {"label": "真实 DSH 会话", "shortLabel": "真实 DSH 会话", "mode": "local"}

    def site_config(self):
        return {"authRequired": False}


def start(state, config):
    server = BridgeServer(config, state)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def get(url, headers=None, method="GET", payload=None):
    data = None
    request_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["content-type"] = "application/json"
    request = Request(url, data=data, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, response.read(), dict(response.headers)
    except HTTPError as error:
        return error.code, error.read(), dict(error.headers)


class HttpSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        web = Path(self.temp.name) / "web"
        web.mkdir()
        (web / "index.html").write_text("<html><head><!-- bridge-boot --></head><body>桥</body></html>", encoding="utf-8")
        (web / "app.js").write_text("// app", encoding="utf-8")
        (web / "secret.txt").write_text("private", encoding="utf-8")
        self.web = web
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        self.temp.cleanup()

    def build(self, base_path="/", port=0):
        config = BridgeConfig(root=Path(self.temp.name), home=Path(self.temp.name) / "state", web_dir=self.web, port=port, base_path=base_path)
        server = start(FakeState(config, self.web), config)
        self.servers.append(server)
        return config, server.server_address[1]

    def test_root_mount_serves_index_with_boot_injection(self):
        _, port = self.build()
        status, body, headers = get(f"http://127.0.0.1:{port}/")
        self.assertEqual(status, 200)
        self.assertIn("window.DSH_BRIDGE=", body.decode("utf-8"))
        self.assertIn('"basePath": "/"', body.decode("utf-8"))

    def test_prefix_mount_isolates_api_and_assets(self):
        _, port = self.build(base_path="/dsh/")
        status, body, _ = get(f"http://127.0.0.1:{port}/dsh/")
        self.assertEqual(status, 200)
        self.assertIn('"basePath": "/dsh/"', body.decode("utf-8"))
        self.assertEqual(get(f"http://127.0.0.1:{port}/dsh/app.js")[0], 200)
        # Nothing outside the prefix may reach the bridge, so a shared origin
        # cannot leak DSH routes into the Codex bridge.
        for path in ("/api/threads", "/api/health", "/app.js", "/"):
            self.assertEqual(get(f"http://127.0.0.1:{port}{path}")[0], 404, path)
        self.assertEqual(get(f"http://127.0.0.1:{port}/dsh/api/threads")[0], 200)
        status, body, _ = get(f"http://127.0.0.1:{port}/dsh")
        self.assertEqual(status, 200)
        self.assertIn('"basePath": "/dsh/"', body.decode("utf-8"))

    def test_thread_rows_mark_running_sessions(self):
        _, port = self.build()
        status, body, _ = get(f"http://127.0.0.1:{port}/api/threads")
        rows = json.loads(body)["threads"]
        running = next(row for row in rows if row["id"] == "session-running")
        idle = next(row for row in rows if row["id"] == "session-idle")
        self.assertTrue(running["bridgeRunning"])
        self.assertEqual(running["status"], "运行中")
        self.assertFalse(idle["bridgeRunning"])

    def test_thread_endpoint_trims_turns(self):
        _, port = self.build()
        status, body, _ = get(f"http://127.0.0.1:{port}/api/thread/session-demo?turnLimit=1")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["thread"]["turns"]), 1)
        self.assertEqual(payload["thread"]["bridgeTotalTurns"], 1)

    def test_cross_site_post_is_refused(self):
        _, port = self.build()
        status, body, _ = get(
            f"http://127.0.0.1:{port}/api/open-attachment",
            headers={"origin": "https://evil.example", "host": f"127.0.0.1:{port}"},
            method="POST",
            payload={"path": "x"},
        )
        self.assertEqual(status, 403)
        self.assertIn("cross_origin", body.decode("utf-8"))

    def test_static_traversal_is_blocked(self):
        _, port = self.build()
        self.assertEqual(get(f"http://127.0.0.1:{port}/..%2f..%2fetc%2fpasswd")[0], 404)

    def test_unknown_api_is_404(self):
        _, port = self.build()
        self.assertEqual(get(f"http://127.0.0.1:{port}/api/nope")[0], 404)


class HelperTests(unittest.TestCase):
    def config(self, base="/"):
        return BridgeConfig(root=Path("."), home=Path("."), web_dir=Path("."), base_path=base)

    def handler(self, base="/"):
        handler = object.__new__(BridgeRequestHandler)
        handler.config = self.config(base)
        return handler

    def test_strip_base(self):
        handler = self.handler("/dsh/")
        self.assertEqual(handler._strip_base("/dsh/api/health"), "/api/health")
        self.assertEqual(handler._strip_base("/dsh"), "")
        self.assertIsNone(handler._strip_base("/api/health"))
        self.assertEqual(self.handler("/")._strip_base("/api/health"), "/api/health")

    def test_content_blocks_keep_text_and_images(self):
        blocks = _content_blocks("  hello  ", [{"data": "AAA", "mimeType": "image/png"}])
        self.assertEqual(blocks[0], {"type": "text", "text": "hello"})
        self.assertEqual(blocks[1]["type"], "image")
        self.assertEqual(_content_blocks("", []), [])

    def test_workspace_allow_list(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "projects"
            (root / "demo").mkdir(parents=True)
            config = BridgeConfig(root=Path(directory), home=Path(directory) / "s", web_dir=Path(directory), projects_root=root, standalone_dir=Path(directory) / "sessions")

            class State:
                pass

            state = State()
            state.config = config
            self.assertTrue(_path_allowed(state, root / "demo"))
            self.assertFalse(_path_allowed(state, Path(directory) / "elsewhere"))


if __name__ == "__main__":
    unittest.main()
