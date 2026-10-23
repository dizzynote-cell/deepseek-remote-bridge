"""Live integration check for the DeepSeek Remote Bridge.

Creates its own temporary workspace and its own sessions, drives one real turn,
reads the transcript back, then resumes the same session after a bridge restart.

Usage:
    python tests/live_check.py --base http://127.0.0.1:8766

Set DSH_BRIDGE_LIVE_KEEP=1 to keep the created workspace instead of removing it.
The script never touches an existing user session.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def call(base: str, method: str, path: str, payload: dict | None = None, timeout: float = 120.0):
    data = None
    headers = {"accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["content-type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(base.rstrip("/") + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        body = error.read()
        try:
            return error.code, json.loads(body or b"{}")
        except ValueError:
            return error.code, {"raw": body.decode("utf-8", "replace")}


def wait_task(base: str, task_id: str, limit: float = 420.0) -> dict:
    deadline = time.time() + limit
    last = {}
    while time.time() < deadline:
        status, payload = call(base, "GET", f"/api/local-task/{task_id}")
        if status != 200:
            return payload
        last = payload
        if payload.get("status") in {"completed", "failed", "interrupted", "cancelled"}:
            return payload
        time.sleep(2.0)
    return last


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8766")
    parser.add_argument("--workspace", default=str(ROOT / "tests" / "integration-workspace"))
    parser.add_argument("--phase", choices=("all", "create", "resume"), default="all")
    args = parser.parse_args()
    base = args.base
    workspace = Path(args.workspace)
    marker = workspace / "state.json"

    if args.phase in {"all", "create"}:
        workspace.mkdir(parents=True, exist_ok=True)
        for stale in workspace.glob("bridge-check-*.md"):
            stale.unlink()
        status, health = call(base, "GET", "/api/health")
        check("health endpoint", status == 200 and health.get("ok") is True, json.dumps(health.get("adapter"), ensure_ascii=False))
        status, info = call(base, "GET", "/api/bridge-info")
        caps = (info or {}).get("capabilities") or {}
        check(
            "bridge-info contract v1",
            status == 200 and info.get("contractVersion") == 1 and info.get("providerId") == "deepseek-harness",
            f"capabilities={json.dumps(caps, ensure_ascii=False)}",
        )
        status, balance = call(base, "GET", "/api/balance")
        check(
            "native balance answered",
            status == 200 and isinstance(balance.get("available"), bool),
            f"available={balance.get('available')} status={balance.get('status')} entries={len(balance.get('entries') or [])}",
        )

        prompt = (
            "Use the write tool (not a shell) to create a file named bridge-check-1.md in the "
            "current working directory containing exactly the line: bridge ok 1. "
            "Then reply with the single word DONE."
        )
        status, created = call(
            base,
            "POST",
            "/api/local-tasks",
            {"op": "new_thread", "cwd": str(workspace), "title": "bridge live check", "text": prompt},
            timeout=300,
        )
        check("new session accepted", status == 200 and bool(created.get("threadId")), json.dumps(created, ensure_ascii=False)[:300])
        if status != 200:
            return 1
        session_id = created["threadId"]
        task = wait_task(base, created["taskId"])
        check(
            "first turn reached a terminal state",
            task.get("status") == "completed",
            f"status={task.get('status')} turn={task.get('turnId')} error={task.get('error')}",
        )
        created_file = workspace / "bridge-check-1.md"
        check("tool really wrote the file", created_file.is_file(), str(created_file))
        status, thread = call(base, "GET", f"/api/thread/{session_id}?turnLimit=50")
        body = thread.get("thread") or {}
        turns = body.get("turns") or []
        tool_calls = [item for turn in turns for item in turn.get("items") or [] if item.get("type") == "toolCall"]
        user_messages = [item for turn in turns for item in turn.get("items") or [] if item.get("type") == "userMessage"]
        check("transcript has the user message", len(user_messages) >= 1, f"count={len(user_messages)}")
        check("transcript has tool calls", len(tool_calls) >= 1, f"tools={[item.get('name') for item in tool_calls]}")
        write_tool = [item for item in tool_calls if item.get("name") == "write"]
        check("write tool reported success", bool(write_tool) and not write_tool[0].get("isError"), json.dumps(write_tool[:1], ensure_ascii=False)[:300])
        check("turn completed", any(turn.get("status") == "completed" for turn in turns), json.dumps([turn.get("status") for turn in turns]))
        marker.write_text(json.dumps({"sessionId": session_id, "workspace": str(workspace)}, indent=1), encoding="utf-8")

    if args.phase in {"all", "resume"}:
        if not marker.is_file():
            check("previous session recorded", False, "run --phase create first")
            return 1
        state = json.loads(marker.read_text(encoding="utf-8"))
        session_id = state["sessionId"]
        status, before = call(base, "GET", f"/api/thread/{session_id}?turnLimit=50")
        turns_before = len(((before.get("thread") or {}).get("turns") or []))
        check("history still readable after restart", status == 200 and turns_before >= 1, f"turns={turns_before}")

        prompt = (
            "Use the write tool to create a file named bridge-check-2.md in the current working "
            "directory containing exactly the line: bridge ok 2. Then reply with the single word DONE."
        )
        status, created = call(
            base,
            "POST",
            "/api/local-tasks",
            {"threadId": session_id, "text": prompt},
            timeout=300,
        )
        check("resume of an existing session accepted", status == 200, json.dumps(created, ensure_ascii=False)[:300])
        if status != 200:
            return 1
        task = wait_task(base, created["taskId"])
        check(
            "second turn completed",
            task.get("status") == "completed",
            f"status={task.get('status')} turn={task.get('turnId')} error={task.get('error')}",
        )
        check("second tool wrote its file", (workspace / "bridge-check-2.md").is_file())
        status, after = call(base, "GET", f"/api/thread/{session_id}?turnLimit=50")
        turns_after = len(((after.get("thread") or {}).get("turns") or []))
        check("transcript continued in the same session", turns_after >= turns_before + 1, f"{turns_before} -> {turns_after}")

    if os.environ.get("DSH_BRIDGE_LIVE_KEEP") != "1":
        shutil.rmtree(workspace, ignore_errors=True)

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed", flush=True)
    if failed:
        print("failed: " + ", ".join(failed), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
