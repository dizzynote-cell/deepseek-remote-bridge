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

    def ensure_open(self, session_id):
        return None

    def route_for(self, session_id):
        from app.dsh_runtime import RouteKey

        return RouteKey("deepseek-account", "deepseek-flash", "max")

    def switch_route(self, session_id, model, effort):
        if session_id == "session-running":
            from app.tasks import TaskRejected

            raise TaskRejected("该会话正在运行，等它结束后再切换模型。", code="session_running", status=409)
        from app.dsh_runtime import RouteKey

        return RouteKey("deepseek-account", model, effort)

    def active_task_for(self, session_id):
        return None

    def status(self, task_id):
        return None


class FakePermissionAdapter:
    options = [
        {"value": "workspace-write", "name": "workspace-write", "description": "写工作区"},
        {"value": "danger-full-access", "name": "danger-full-access", "description": "完全权限"},
    ]
    current_by_session = {}

    def permissions(self, session_id=None):
        return {
            "options": list(self.options),
            "defaultPreset": "workspace-write",
            "current": self.current_by_session.get(session_id) if session_id else None,
        }

    def set_permission(self, session_id, preset):
        if preset not in {option["value"] for option in self.options}:
            from app.dsh_adapter import AdapterError

            raise AdapterError("unknown preset", status=400)
        self.current_by_session[session_id] = preset
        return {"current": preset}


class FakeCatalog:
    last_error = ""

    def __init__(self):
        self.adapter = FakePermissionAdapter()

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
        return object() if session_id in {"session-running", "session-idle"} else None

    def invalidate(self, session_id=None):
        return None


class FakeBalance:
    def snapshot_for_page(self, force=False):
        return {"available": True, "entries": [], "display": {"label": "余额 ¥1.00", "primary": None}}


class FakeVoice:
    def __init__(self, cache_dir):
        self.cache_dir = cache_dir
        self.enabled = True
        self.settings = type("Settings", (), {"ready": True, "voice": "v", "resource": "r"})()

    @property
    def available(self):
        return True

    def public(self):
        return {"enabled": True, "configured": True, "voice": "v", "resource": "r"}

    def status(self, keys):
        return {"cached": {key: self.cached(key) for key in keys}}

    def cached(self, key):
        try:
            return self.path_for(key).is_file()
        except Exception:
            return False

    def path_for(self, key):
        from app.voice import VoiceError, is_digest

        if not is_digest(key):
            raise VoiceError("invalid_voice_key")
        return self.cache_dir / f"{key}.mp3"

    def synthesize(self, text):
        from app.voice import VoiceError

        if not str(text or "").strip():
            raise VoiceError("voice_text_empty", "没有可朗读的文字")
        key = "a" * 64
        path = self.cache_dir / f"{key}.mp3"
        path.write_bytes(b"ID3-fake")
        return key, path


class FakePreferences:
    def __init__(self):
        self.values = {"voiceEnabled": True}

    def snapshot(self):
        return dict(self.values)

    def update(self, patch):
        self.values.update(patch)
        return dict(self.values)


class FakeState:
    def session_adapter(self, session_id):
        return self.catalog.adapter

    def __init__(self, config, web_dir):
        self.config = config
        from app.thread_library import ThreadLibrary
        self.thread_library = ThreadLibrary(config.data_dir / "thread-library.json")
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
        self.preferences = FakePreferences()
        self.voice = FakeVoice(Path(config.home) / "voice-cache")
        self.voice.cache_dir.mkdir(parents=True, exist_ok=True)

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

    def test_library_hide_restore_pin_and_validation(self):
        _, port = self.build(base_path="/dsh/")
        url = f"http://127.0.0.1:{port}/dsh/api/thread-library"
        self.assertEqual(get(url, method="POST", payload={"threadId":"missing", "hidden":True})[0],404)
        self.assertEqual(get(url, method="POST", payload={"threadId":"session-idle", "hidden":"true"})[0],400)
        self.assertEqual(get(url, method="POST", payload={"threadId":"session-idle", "hidden":True,"pinned":True,"name":"测试"})[0],200)
        self.assertTrue(json.loads(get(url)[1])["threads"]["session-idle"]["hidden"])
        self.assertEqual(get(url, method="POST", payload={"threadId":"session-idle","hidden":False})[0],200)
        saved=json.loads(get(url)[1])["threads"]["session-idle"]
        self.assertFalse(saved["hidden"])
        self.assertTrue(saved["pinned"])
        self.assertEqual(get(url, method="POST", headers={"Origin":"https://other.example"},payload={"threadId":"session-idle","hidden":True})[0],403)

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

    def test_model_catalog_and_default_update(self):
        _, port = self.build()
        status, body, _ = get(f"http://127.0.0.1:{port}/api/models")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in payload["models"]], ["deepseek-flash", "deepseek-v4-pro"])
        self.assertEqual([item["id"] for item in payload["efforts"]], ["off", "low", "high", "max"])
        self.assertEqual(payload["bridgeDefault"], {"model": "deepseek-flash", "effort": "high"})

        status, body, _ = get(
            f"http://127.0.0.1:{port}/api/models",
            method="POST",
            payload={"model": "deepseek-v4-pro", "effort": "high"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["bridgeDefault"], {"model": "deepseek-v4-pro", "effort": "high"})

    def test_model_switch_for_a_session(self):
        _, port = self.build()
        status, body, _ = get(
            f"http://127.0.0.1:{port}/api/models",
            method="POST",
            payload={"threadId": "session-demo", "model": "deepseek-v4-pro", "effort": "low"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["session"], {"model": "deepseek-v4-pro", "effort": "low"})

    def test_unknown_model_is_refused(self):
        _, port = self.build()
        status, body, _ = get(
            f"http://127.0.0.1:{port}/api/models",
            method="POST",
            payload={"model": "gpt-4o", "effort": "high"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["code"], "unknown_model")

    def test_model_switch_is_refused_while_running(self):
        _, port = self.build()
        status, body, _ = get(
            f"http://127.0.0.1:{port}/api/models",
            method="POST",
            payload={"threadId": "session-running", "model": "deepseek-v4-pro", "effort": "low"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["code"], "session_running")

    def test_permission_catalog_and_switch(self):
        _, port = self.build()
        status, body, _ = get(f"http://127.0.0.1:{port}/api/permissions")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual([option["value"] for option in payload["options"]], ["workspace-write", "danger-full-access"])
        self.assertEqual(payload["bridgeDefault"], "workspace-write")

        status, body, _ = get(
            f"http://127.0.0.1:{port}/api/permissions",
            method="POST",
            payload={"threadId": "session-demo", "preset": "danger-full-access"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["current"], "danger-full-access")
        status, body, _ = get(f"http://127.0.0.1:{port}/api/permissions?threadId=session-demo")
        self.assertEqual(json.loads(body)["current"], "danger-full-access")

    def test_permission_rejects_unknown_preset(self):
        _, port = self.build()
        status, body, _ = get(
            f"http://127.0.0.1:{port}/api/permissions",
            method="POST",
            payload={"threadId": "session-demo", "preset": "root"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["code"], "unknown_preset")

    def test_permission_without_thread_updates_the_default(self):
        _, port = self.build()
        status, body, _ = get(
            f"http://127.0.0.1:{port}/api/permissions",
            method="POST",
            payload={"preset": "danger-full-access"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["bridgeDefault"], "danger-full-access")

    def test_manifest_is_served_with_the_manifest_type(self):
        (self.web / "manifest.webmanifest").write_text("{}", encoding="utf-8")
        (self.web / "favicon.svg").write_text("<svg/>", encoding="utf-8")
        _, port = self.build()
        status, _, headers = get(f"http://127.0.0.1:{port}/manifest.webmanifest")
        content_type = {key.lower(): value for key, value in headers.items()}.get("content-type")
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("application/manifest+json"), content_type)
        status, _, headers = get(f"http://127.0.0.1:{port}/favicon.svg")
        self.assertIn("image/svg+xml", {key.lower(): value for key, value in headers.items()}.get("content-type"))

    def test_unknown_api_is_404(self):
        _, port = self.build()
        self.assertEqual(get(f"http://127.0.0.1:{port}/api/nope")[0], 404)

    def test_voice_config_and_preferences(self):
        _, port = self.build()
        status, body, _ = get(f"http://127.0.0.1:{port}/api/voice/config")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["configured"])
        status, body, _ = get(f"http://127.0.0.1:{port}/api/preferences")
        self.assertEqual(json.loads(body), {"voiceEnabled": True})
        status, body, _ = get(
            f"http://127.0.0.1:{port}/api/preferences",
            method="POST",
            payload={"voiceEnabled": False},
        )
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["voiceEnabled"])

    def test_voice_request_then_audio_is_served(self):
        _, port = self.build()
        status, body, _ = get(
            f"http://127.0.0.1:{port}/api/voice/request",
            method="POST",
            payload={"text": "讲一句话"},
        )
        self.assertEqual(status, 200)
        key = json.loads(body)["key"]
        self.assertRegex(key, r"^[0-9a-f]{64}$")
        status, audio, headers = get(f"http://127.0.0.1:{port}/api/voice/audio/{key}")
        self.assertEqual(status, 200)
        content_type = {key.lower(): value for key, value in headers.items()}.get("content-type")
        self.assertEqual(content_type, "audio/mpeg")
        self.assertEqual(audio, b"ID3-fake")

    def test_voice_request_rejects_empty_text(self):
        _, port = self.build()
        status, body, _ = get(
            f"http://127.0.0.1:{port}/api/voice/request",
            method="POST",
            payload={"text": "   "},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["code"], "voice_text_empty")

    def test_unknown_voice_key_is_404(self):
        _, port = self.build()
        self.assertEqual(get(f"http://127.0.0.1:{port}/api/voice/audio/not-a-key")[0], 404)
        self.assertEqual(get(f"http://127.0.0.1:{port}/api/voice/audio/{'b' * 64}")[0], 404)


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
