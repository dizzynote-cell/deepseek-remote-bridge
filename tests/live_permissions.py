"""Live check for per-session permission presets and the plan (todo) projection.

1. Creates a session, reads its effective preset, switches it to
   danger-full-access, and verifies the switch is durable in the session log.
2. Creates a second session that requests danger-full-access at creation.
3. Asks for a plan, then verifies `todo/write` reaches the bridge as plan data.

Usage:
    python tests/live_permissions.py --base http://127.0.0.1:8766
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_config  # noqa: E402

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


def wait_task(base: str, task_id: str, limit: float = 600.0) -> dict:
    deadline = time.time() + limit
    last: dict = {}
    while time.time() < deadline:
        status, payload = call(base, "GET", f"/api/local-task/{task_id}")
        if status == 200:
            last = payload
            if last.get("status") in {"completed", "failed", "interrupted", "cancelled"}:
                return last
        time.sleep(2.0)
    return last


def events_of(base: str, session_id: str) -> list[dict]:
    status, thread = call(base, "GET", f"/api/thread/{session_id}?turnLimit=50")
    turns = ((thread.get("thread") or {}).get("turns") or [])
    return [item for turn in turns for item in turn.get("items") or []]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8766")
    parser.add_argument("--workspace", default=str(Path(load_config().projects_root) / "bridge-permission-check"))
    args = parser.parse_args()
    base = args.base
    workspace = Path(args.workspace)
    shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)

    status, catalog = call(base, "GET", "/api/permissions")
    values = [option.get("value") for option in catalog.get("options") or []]
    check("catalog lists both presets", {"workspace-write", "danger-full-access"} <= set(values), f"{values}")

    # -- session A: switch preset after creation -------------------------
    status, created = call(
        base, "POST", "/api/local-tasks",
        {"op": "new_thread", "cwd": str(workspace), "title": "permission switch",
         "text": "Reply with the single word READY.", "permissionPreset": "workspace-write"},
        timeout=300,
    )
    check("session accepted", status == 200, f"{status} {created}")
    if status != 200:
        return 1
    session_a = created["threadId"]
    wait_task(base, created["taskId"])

    status, current = call(base, "GET", f"/api/permissions?threadId={session_a}")
    check("session reports its preset", status == 200 and current.get("current") == "workspace-write", json.dumps(current, ensure_ascii=False)[:200])

    status, switched = call(base, "POST", "/api/permissions", {"threadId": session_a, "preset": "danger-full-access"})
    check("switch accepted", status == 200 and switched.get("current") == "danger-full-access", json.dumps(switched, ensure_ascii=False)[:200])

    status, after = call(base, "GET", f"/api/permissions?threadId={session_a}")
    check("switch is readable back", after.get("current") == "danger-full-access", json.dumps(after, ensure_ascii=False)[:200])

    # The switch must be durable in the harness log, not just in our memory.
    status, thread = call(base, "GET", f"/api/thread/{session_a}?turnLimit=50")
    notices = [item.get("text", "") for turn in ((thread.get("thread") or {}).get("turns") or []) for item in turn.get("items") or [] if item.get("type") == "notice"]
    check("permission change is logged", any("danger-full-access" in text for text in notices), str(notices[-2:]))

    status, rejected = call(base, "POST", "/api/permissions", {"threadId": session_a, "preset": "not-a-preset"})
    check("unknown preset is refused", status == 400 and rejected.get("code") == "unknown_preset", f"{status} {rejected}")

    # -- session B: preset at creation -----------------------------------
    status, created_b = call(
        base, "POST", "/api/local-tasks",
        {"op": "new_thread", "cwd": str(workspace), "title": "permission at creation",
         "text": "Reply with the single word READY.", "permissionPreset": "danger-full-access"},
        timeout=300,
    )
    check("session with preset accepted", status == 200, f"{status}")
    if status == 200:
        wait_task(base, created_b["taskId"])
        status, current_b = call(base, "GET", f"/api/permissions?threadId={created_b['threadId']}")
        check("creation preset took effect", current_b.get("current") == "danger-full-access", json.dumps(current_b, ensure_ascii=False)[:200])

    # -- plan projection --------------------------------------------------
    status, planned = call(
        base, "POST", "/api/local-tasks",
        {"threadId": session_a,
         "text": "Use the todo_write tool to create a plan with exactly three steps: '第一步', '第二步', '第三步'. "
                 "Mark the first as in_progress and the rest pending, then reply DONE."},
        timeout=300,
    )
    check("plan turn accepted", status == 200, f"{status}")
    if status == 200:
        wait_task(base, planned["taskId"])
        status, thread = call(base, "GET", f"/api/thread/{session_a}?turnLimit=50")
        body = thread.get("thread") or {}
        todos = body.get("bridgeTodos") or []
        check("plan reached the bridge", len(todos) >= 3, json.dumps(todos, ensure_ascii=False)[:300])
        check("plan progress is counted", body.get("bridgeTodosTotal") == len(todos) and 0 <= body.get("bridgeTodosDone", -1) <= len(todos), f"{body.get('bridgeTodosDone')}/{body.get('bridgeTodosTotal')}")
        cards = [item for item in events_of(base, session_a) if item.get("type") == "todo"]
        check("plan card is part of the transcript", len(cards) >= 1, f"cards={len(cards)}")

    shutil.rmtree(workspace, ignore_errors=True)
    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
