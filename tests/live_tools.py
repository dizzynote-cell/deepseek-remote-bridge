"""Live check: are the model-facing tools actually callable from the bridge?

Exercises the two tools the desktop profile has but the plain sdk profile does
not: `load_workspace_dependencies` (needs DSH_PRIMARY_RUNTIME) and `present`.

Usage:
    python tests/live_tools.py --base http://127.0.0.1:8766
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
    parser.add_argument("--workspace", default=str(Path(load_config().projects_root) / "bridge-tool-check"))
    args = parser.parse_args()
    base = args.base
    workspace = Path(args.workspace)
    shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)

    prompt = (
        "Do these steps in order and report each result briefly: "
        "1) call load_workspace_dependencies and tell me the python command it returns; "
        "2) use the write tool to create tool-check.md containing 'tool check'; "
        "3) call the present tool on tool-check.md; "
        "4) reply DONE."
    )
    status, created = call(
        base, "POST", "/api/local-tasks",
        {"op": "new_thread", "cwd": str(workspace), "title": "tool parity", "text": prompt},
        timeout=300,
    )
    check("turn accepted", status == 200, f"{status} {created}")
    if status != 200:
        return 1
    task = wait_task(base, created["taskId"])
    check("turn completed", task.get("status") == "completed", f"{task.get('status')} {task.get('error')}")

    status, thread = call(base, "GET", f"/api/thread/{created['threadId']}?turnLimit=20")
    items = [item for turn in ((thread.get("thread") or {}).get("turns") or []) for item in turn.get("items") or []]
    tools = [item for item in items if item.get("type") == "toolCall"]
    names = [item.get("name") for item in tools]
    check("load_workspace_dependencies was callable", "load_workspace_dependencies" in names, f"{names}")
    check("present was callable", "present" in names, f"{names}")
    dependency = next((item for item in tools if item.get("name") == "load_workspace_dependencies"), None)
    if dependency:
        result = str(dependency.get("result") or "")
        check("dependency result names a python path", "python" in result.lower(), result[:200])
    presented = next((item for item in tools if item.get("name") == "present"), None)
    if presented:
        result = str(presented.get("result") or "")
        check("present returned a durable reference", "tool-check.md" in result, result[:200])
    check("file really exists", (workspace / "tool-check.md").is_file())

    shutil.rmtree(workspace, ignore_errors=True)
    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
