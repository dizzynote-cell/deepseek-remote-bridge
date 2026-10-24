"""Live checks for concurrency: two sessions in parallel, and queued input in one.

Creates its own sessions under the configured projects root and never touches an
existing session.

Usage:
    python tests/live_parallel.py --base http://127.0.0.1:8766
"""

from __future__ import annotations

import argparse
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
    import json

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


def wait_tasks(base: str, task_ids: list[str], limit: float = 600.0) -> dict[str, dict]:
    pending = set(task_ids)
    result: dict[str, dict] = {}
    deadline = time.time() + limit
    while pending and time.time() < deadline:
        for task_id in list(pending):
            status, payload = call(base, "GET", f"/api/local-task/{task_id}")
            if status == 200 and payload.get("status") in {"completed", "failed", "interrupted", "cancelled"}:
                result[task_id] = payload
                pending.discard(task_id)
        if pending:
            time.sleep(2.0)
    for task_id in pending:
        result[task_id] = {"status": "timeout"}
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8766")
    parser.add_argument(
        "--workspace", default=str(Path(load_config().projects_root) / "bridge-parallel-check")
    )
    args = parser.parse_args()
    base = args.base
    workspace = Path(args.workspace)
    (workspace / "a").mkdir(parents=True, exist_ok=True)
    (workspace / "b").mkdir(parents=True, exist_ok=True)
    for stale in workspace.rglob("bridge-parallel-*.md"):
        stale.unlink()

    # -- two independent sessions submitted back to back -----------------
    prompt_a = (
        "Use the write tool to create bridge-parallel-a.md in the current working directory "
        "containing exactly: parallel-a. Then reply DONE."
    )
    prompt_b = (
        "Use the write tool to create bridge-parallel-b.md in the current working directory "
        "containing exactly: parallel-b. Then reply DONE."
    )
    started = time.time()
    status_a, created_a = call(
        base, "POST", "/api/local-tasks", {"op": "new_thread", "cwd": str(workspace / "a"), "title": "parallel a", "text": prompt_a}, timeout=300
    )
    status_b, created_b = call(
        base, "POST", "/api/local-tasks", {"op": "new_thread", "cwd": str(workspace / "b"), "title": "parallel b", "text": prompt_b}, timeout=300
    )
    check("both submissions accepted", status_a == 200 and status_b == 200, f"{status_a}/{status_b}")
    if not (status_a == 200 and status_b == 200):
        return 1
    session_a = created_a["threadId"]
    session_b = created_b["threadId"]
    check("sessions are distinct", session_a != session_b, f"{session_a} vs {session_b}")

    # While both run, the list must show them as running.
    status, listing = call(base, "GET", "/api/threads?limit=20")
    running_ids = {row["id"] for row in (listing.get("threads") or []) if row.get("bridgeRunning")}
    overlap = {session_a, session_b} & running_ids
    check("both sessions appear running at the same time", len(overlap) >= 1, f"running={sorted(running_ids)[:5]}")

    results = wait_tasks(base, [created_a["taskId"], created_b["taskId"]])
    task_a = results.get(created_a["taskId"], {})
    task_b = results.get(created_b["taskId"], {})
    check("session A completed", task_a.get("status") == "completed", f"{task_a.get('status')} {task_a.get('error')}")
    check("session B completed", task_b.get("status") == "completed", f"{task_b.get('status')} {task_b.get('error')}")
    check("session A wrote its own file", (workspace / "a" / "bridge-parallel-a.md").is_file())
    check("session B wrote its own file", (workspace / "b" / "bridge-parallel-b.md").is_file())
    print(f"[info] both turns finished in {time.time() - started:.1f}s", flush=True)

    # -- queued input on a session that is already running ----------------
    followup = (
        "Use the write tool to create bridge-parallel-a2.md in the current working directory "
        "containing exactly: parallel-a2. Then reply DONE."
    )
    status, queued = call(base, "POST", "/api/local-tasks", {"threadId": session_a, "text": followup}, timeout=300)
    check("follow-up on an idle session is accepted", status == 200, f"{status} {queued}")
    if status == 200:
        check("follow-up is not marked steered when idle", queued.get("steered") is False, f"steered={queued.get('steered')}")
        results = wait_tasks(base, [queued["taskId"]])
        task = results.get(queued["taskId"], {})
        check("follow-up completed in the same session", task.get("status") == "completed", f"{task.get('status')} {task.get('error')}")
        check("follow-up wrote its file", (workspace / "a" / "bridge-parallel-a2.md").is_file())

    # -- input queued while the same session is running -------------------
    first = (
        "Use the write tool to create bridge-parallel-s1.md in the current working directory "
        "containing exactly: steer-1. Then reply DONE."
    )
    second = (
        "Use the write tool to create bridge-parallel-s2.md in the current working directory "
        "containing exactly: steer-2. Then reply DONE."
    )
    status, first_created = call(
        base, "POST", "/api/local-tasks", {"op": "new_thread", "cwd": str(workspace / "a"), "title": "steer base", "text": first}, timeout=300
    )
    check("steer base accepted", status == 200, f"{status}")
    if status == 200:
        status2, second_created = call(base, "POST", "/api/local-tasks", {"threadId": first_created["threadId"], "text": second}, timeout=300)
        check("second input accepted while the first turn runs", status2 == 200, f"{status2} {second_created}")
        check("second input is marked steered", bool(second_created.get("steered")), f"steered={second_created.get('steered')}")
        results = wait_tasks(base, [first_created["taskId"], second_created["taskId"]], limit=600.0)
        first_task = results.get(first_created["taskId"], {})
        second_task = results.get(second_created["taskId"], {})
        check("queued input completed without resending", first_task.get("status") == "completed" and second_task.get("status") == "completed", f"{first_task.get('status')}/{second_task.get('status')}")
        check("both queued writes landed", (workspace / "a" / "bridge-parallel-s1.md").is_file() and (workspace / "a" / "bridge-parallel-s2.md").is_file())

    # -- transcript still consistent --------------------------------------
    status, thread = call(base, "GET", f"/api/thread/{session_a}?turnLimit=50")
    turns = ((thread.get("thread") or {}).get("turns") or [])
    user_texts = [
        item.get("content", [{}])[0].get("text", "")
        for turn in turns
        for item in turn.get("items") or []
        if item.get("type") == "userMessage"
    ]
    check("transcript keeps both inputs", any("bridge-parallel-a.md" in text for text in user_texts) and any("bridge-parallel-a2.md" in text for text in user_texts), f"turns={len(turns)}")
    check("session A has two completed turns", len(turns) >= 2 and all(turn.get("status") == "completed" for turn in turns[-2:]), f"statuses={[t.get('status') for t in turns]}")

    shutil.rmtree(workspace, ignore_errors=True)
    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
