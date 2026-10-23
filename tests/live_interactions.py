"""Live check for human approvals and user questions through the bridge.

Runs two short turns in a private session:
1. ``ask_user_question`` — answered from the bridge's interaction API.
2. an explicit sandbox-escalation request — approved from the same API.

Usage:
    python tests/live_interactions.py --base http://127.0.0.1:8766
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


def wait_for_interaction(base: str, kind: str, limit: float = 180.0) -> dict | None:
    deadline = time.time() + limit
    while time.time() < deadline:
        status, payload = call(base, "GET", "/api/interactions")
        for request in (payload.get("requests") or []):
            if request.get("kind") == kind:
                return request
        time.sleep(1.0)
    return None


def wait_task(base: str, task_id: str, limit: float = 420.0) -> dict:
    deadline = time.time() + limit
    last: dict = {}
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
    parser.add_argument("--workspace", default=str(Path(load_config().projects_root) / "bridge-interaction-check"))
    args = parser.parse_args()
    base = args.base
    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    # -- question -------------------------------------------------------
    prompt = (
        "Use the ask_user_question tool to ask me exactly one question with id 'color', "
        "text 'Which colour?', and options 'red' and 'blue'. Then, using the write tool, "
        "create answer.txt in the current working directory containing only the label I chose. "
        "Reply with the single word DONE when finished."
    )
    status, created = call(
        base, "POST", "/api/local-tasks", {"op": "new_thread", "cwd": str(workspace), "title": "interaction check", "text": prompt}, timeout=300
    )
    check("question turn accepted", status == 200, json.dumps(created, ensure_ascii=False)[:200])
    if status != 200:
        return 1
    session_id = created["threadId"]
    question = wait_for_interaction(base, "questions", limit=240)
    check("ask_user_question reached the bridge", question is not None, json.dumps(question, ensure_ascii=False)[:300] if question else "no interaction")
    if question is not None:
        questions = question.get("questions") or []
        question_id = questions[0].get("id") if questions else ""
        status, answered = call(
            base,
            "POST",
            "/api/interactions/respond",
            {"id": question["id"], "session": "local", "action": "answer", "answers": {question_id: "blue"}},
        )
        check("answer accepted", status == 200, json.dumps(answered, ensure_ascii=False)[:200])
    task = wait_task(base, created["taskId"])
    check("question turn completed", task.get("status") == "completed", f"{task.get('status')} {task.get('error')}")
    answer_file = workspace / "answer.txt"
    check("model received the answer", answer_file.is_file() and "blue" in answer_file.read_text(encoding="utf-8", errors="replace").lower(), answer_file.read_text(encoding="utf-8", errors="replace")[:80] if answer_file.exists() else "missing")
    if answer_file.exists():
        answer_file.unlink()

    # -- approval -------------------------------------------------------
    prompt = (
        "Call the pwsh tool with sandbox_permissions set to danger-full-access to run the command "
        "'Write-Output bridge-approval-ok'. If the approval is rejected, say REJECTED instead. "
        "Then reply with the single word DONE."
    )
    status, created = call(base, "POST", "/api/local-tasks", {"threadId": session_id, "text": prompt}, timeout=300)
    check("approval turn accepted", status == 200, json.dumps(created, ensure_ascii=False)[:200])
    if status != 200:
        return 1
    approval = wait_for_interaction(base, "approval", limit=300)
    check("escalation approval reached the bridge", approval is not None, json.dumps(approval, ensure_ascii=False)[:300] if approval else "no interaction")
    if approval is not None:
        status, decided = call(
            base,
            "POST",
            "/api/interactions/respond",
            {"id": approval["id"], "session": "local", "action": "accept"},
        )
        check("approval accepted", status == 200, json.dumps(decided, ensure_ascii=False)[:200])
    task = wait_task(base, created["taskId"])
    check("approval turn completed", task.get("status") == "completed", f"{task.get('status')} {task.get('error')}")

    status, thread = call(base, "GET", f"/api/thread/{session_id}?turnLimit=50")
    items = [item for turn in ((thread.get("thread") or {}).get("turns") or []) for item in turn.get("items") or []]
    notices = [item for item in items if item.get("type") == "notice" and item.get("level") == "approval"]
    check("approval audit appears in the transcript", len(notices) >= 2, f"notices={len(notices)}")
    approvals = [item for item in items if item.get("type") == "toolCall" and item.get("name") == "pwsh"]
    check("escalated tool ran", any("bridge-approval-ok" in str(item.get("result") or "") for item in approvals), json.dumps(approvals[-1:], ensure_ascii=False)[:300])

    shutil.rmtree(workspace, ignore_errors=True)

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed", flush=True)
    if failed:
        print("failed: " + ", ".join(failed), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
