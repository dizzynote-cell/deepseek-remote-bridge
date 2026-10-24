"""Live check for model / effort selection and per-session switching.

1. Creates a session on the default route and records its route.
2. Switches it to V4 Pro (this restarts that route's runtime, as the SDK fixes
   the route per process) and sends another turn.
3. Verifies the transcript's request headers show both models and that the
   switch is refused while a task is running.

Usage:
    python tests/live_models.py --base http://127.0.0.1:8766
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8766")
    parser.add_argument("--workspace", default=str(Path(load_config().projects_root) / "bridge-model-check"))
    args = parser.parse_args()
    base = args.base
    workspace = Path(args.workspace)
    shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)

    status, catalog = call(base, "GET", "/api/models")
    model_ids = [item["id"] for item in catalog.get("models") or []]
    efforts = [item["id"] for item in catalog.get("efforts") or []]
    check("catalog lists both models", {"deepseek-flash", "deepseek-v4-pro"} <= set(model_ids), str(model_ids))
    check("catalog lists the four efforts", efforts == ["off", "low", "high", "max"], str(efforts))

    status, created = call(
        base, "POST", "/api/local-tasks",
        {"op": "new_thread", "cwd": str(workspace), "title": "model switch",
         "text": "Reply with the single word READY."},
        timeout=300,
    )
    check("session accepted", status == 200, f"{status} {created}")
    if status != 200:
        return 1
    session_id = created["threadId"]
    wait_task(base, created["taskId"])

    status, before = call(base, "GET", f"/api/models?threadId={session_id}")
    start_route = (before.get("session") or {})
    check("session reports its route", status == 200 and start_route.get("model") in model_ids, json.dumps(start_route, ensure_ascii=False))

    target_model = "deepseek-v4-pro" if start_route.get("model") != "deepseek-v4-pro" else "deepseek-flash"
    target_effort = "low" if start_route.get("effort") != "low" else "high"
    status, switched = call(
        base, "POST", "/api/models",
        {"threadId": session_id, "model": target_model, "effort": target_effort},
    )
    check("switch accepted", status == 200, f"{status} {switched}")
    check("switch reports the target", (switched.get("session") or {}).get("model") == target_model, json.dumps(switched, ensure_ascii=False))

    # a second turn must run on the new route and be recorded in the log
    status, second = call(
        base, "POST", "/api/local-tasks",
        {"threadId": session_id, "text": "Reply with the single word SECOND."},
        timeout=300,
    )
    check("second turn accepted", status == 200, f"{status}")
    if status == 200:
        task = wait_task(base, second["taskId"])
        check("second turn completed", task.get("status") == "completed", str(task.get("status")))

    status, after = call(base, "GET", f"/api/models?threadId={session_id}")
    check("session now reports the new route", (after.get("session") or {}).get("model") == target_model, json.dumps(after.get("session"), ensure_ascii=False))

    status, refused = call(
        base, "POST", "/api/models",
        {"model": "nope", "effort": "low"},
    )
    check("unknown model refused", status == 400 and refused.get("code") == "unknown_model", f"{status} {refused}")

    # leave the machine on the route the bridge started with
    status, restored = call(
        base, "POST", "/api/models",
        {"threadId": session_id, "model": start_route.get("model"), "effort": start_route.get("effort")},
    )
    check("restored the original route", status == 200, f"{status}")

    shutil.rmtree(workspace, ignore_errors=True)
    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
