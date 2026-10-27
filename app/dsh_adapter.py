# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
# Project: DeepSeek Remote Bridge
"""Loopback client for the host adapter plugin mounted inside the DSH runtime.

The adapter is the only component that talks to DeepSeek Harness services the
official SDK stdio protocol does not expose (account balance, session query,
human approvals, user questions). It listens on 127.0.0.1 with a per-process
bearer token, so this client never leaves the machine.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class AdapterError(RuntimeError):
    """The adapter rejected a request or could not be reached."""

    def __init__(self, message: str, status: int = 0, payload: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.payload = payload or {}


class AdapterClient:
    """Thin HTTP client for one adapter endpoint."""

    def __init__(self, port: int, token: str, timeout: float = 20.0):
        self.base = f"http://127.0.0.1:{port}"
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None, timeout: float | None = None):
        url = self.base + path
        data = None
        headers = {"x-bridge-token": self.token, "accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["content-type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            raw = error.read() if error.fp is not None else b""
            payload: dict[str, Any] = {}
            try:
                payload = json.loads(raw or b"{}")
            except (ValueError, TypeError):
                payload = {}
            message = payload.get("message") or payload.get("error") or f"adapter HTTP {error.code}"
            raise AdapterError(str(message), status=error.code, payload=payload) from None
        except urllib.error.URLError as error:
            raise AdapterError(f"adapter unreachable: {error.reason}") from None
        except TimeoutError:
            raise AdapterError("adapter request timed out") from None
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            raise AdapterError("adapter returned a non-JSON body") from None

    # -- health ---------------------------------------------------------
    def health(self, timeout: float = 5.0) -> dict[str, Any]:
        return self._request("GET", "/health", timeout=timeout)

    # -- account --------------------------------------------------------
    def balance(self, locale: str = "zh-CN", tz_offset_seconds: int = 0) -> dict[str, Any]:
        query = urllib.parse.urlencode({"locale": locale, "tzOffset": tz_offset_seconds})
        return self._request("GET", f"/balance?{query}", timeout=45.0)

    # -- sessions -------------------------------------------------------
    def sessions(self, limit: int = 200) -> dict[str, Any]:
        return self._request("GET", f"/sessions?limit={int(limit)}", timeout=60.0)

    def session(self, session_id: str) -> dict[str, Any]:
        quoted = urllib.parse.quote(session_id, safe="")
        return self._request("GET", f"/session/{quoted}", timeout=90.0)

    # -- permissions ----------------------------------------------------
    def permissions(self, session_id: str | None = None) -> dict[str, Any]:
        query = "" if session_id is None else "?" + urllib.parse.urlencode({"sessionId": session_id})
        return self._request("GET", f"/permissions{query}", timeout=20.0)

    def set_permission(self, session_id: str, preset: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/permission",
            body={"sessionId": session_id, "preset": preset},
            timeout=20.0,
        )

    # -- interactions ---------------------------------------------------
    def interactions(self, session_id: str | None = None) -> dict[str, Any]:
        query = "" if session_id is None else "?" + urllib.parse.urlencode({"sessionId": session_id})
        return self._request("GET", f"/interactions{query}", timeout=min(5.0, self.timeout))

    def respond(self, interaction_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        quoted = urllib.parse.quote(interaction_id, safe="")
        return self._request("POST", f"/interaction/{quoted}", body=payload, timeout=20.0)
