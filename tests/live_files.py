"""Live check for returned files: discovery, download, and the path fence.

Asks one private session to write a Markdown file and a CSV table, then verifies
that the bridge offers both back and refuses paths outside the allow-list.

Usage:
    python tests/live_files.py --base http://127.0.0.1:8766
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import urllib.error
import urllib.parse
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
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers)


def wait_task(base: str, task_id: str, limit: float = 600.0) -> dict:
    deadline = time.time() + limit
    last: dict = {}
    while time.time() < deadline:
        status, body, _ = call(base, "GET", f"/api/local-task/{task_id}")
        if status == 200:
            last = json.loads(body)
            if last.get("status") in {"completed", "failed", "interrupted", "cancelled"}:
                return last
        time.sleep(2.0)
    return last


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8766")
    parser.add_argument("--workspace", default=str(Path(load_config().projects_root) / "bridge-files-check"))
    args = parser.parse_args()
    base = args.base
    workspace = Path(args.workspace)
    shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)

    prompt = (
        "Create two files in the current working directory with the write tool: "
        "bridge-file-note.md containing the line '# bridge note', and "
        "bridge-file-table.csv containing the header 'name,value' and one row 'a,1'. "
        "Then reply DONE."
    )
    status, body, _ = call(
        base, "POST", "/api/local-tasks",
        {"op": "new_thread", "cwd": str(workspace), "title": "returned files", "text": prompt},
        timeout=300,
    )
    check("turn accepted", status == 200, body[:200].decode("utf-8", "replace"))
    if status != 200:
        return 1
    created = json.loads(body)
    task = wait_task(base, created["taskId"])
    check("turn completed", task.get("status") == "completed", f"{task.get('status')} {task.get('error')}")

    status, body, _ = call(base, "GET", f"/api/thread/{created['threadId']}?turnLimit=20")
    thread = (json.loads(body) or {}).get("thread") or {}
    attachments = thread.get("bridgeAttachments") or []
    names = sorted(item.get("name") for item in attachments)
    check("both written files are offered back", names == ["bridge-file-note.md", "bridge-file-table.csv"], f"{names}")

    if attachments:
        path = attachments[0]["localPath"]
        quoted = urllib.parse.quote(path)
        status, data, headers = call(base, "GET", f"/api/attachment?path={quoted}")
        content_type = {key.lower(): value for key, value in headers.items()}.get("content-type", "")
        check("download works", status == 200 and len(data) > 0, f"status={status} bytes={len(data)} type={content_type}")

    outside = str(Path.home() / ".dsh" / ".credentials.yaml")
    status, data, _ = call(base, "GET", f"/api/attachment?path={urllib.parse.quote(outside)}")
    check("path outside the allow-list is refused", status in {403, 404}, f"status={status}")

    status, body, _ = call(
        base, "POST", "/api/local-tasks",
        {"op": "new_thread", "cwd": "C:\\Windows", "title": "forbidden", "text": "hello"},
    )
    check("new session outside the allow-list is refused", status == 403, f"status={status} {body[:120].decode('utf-8', 'replace')}")

    shutil.rmtree(workspace, ignore_errors=True)
    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
