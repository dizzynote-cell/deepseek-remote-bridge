"""Prove session ownership: a session held by another harness process is refused.

Starts a second, independent DeepSeek Harness SDK runtime, resumes a bridge-created
test session there, and then asks the bridge to send to the same session. The
bridge must refuse with 409 instead of writing into a session another process owns.

Usage:
    python tests/live_ownership.py --base http://127.0.0.1:8766 --session <session-id>
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_config  # noqa: E402
from app.dsh_rpc import RuntimeProcess  # noqa: E402

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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8766")
    parser.add_argument("--session", required=True)
    args = parser.parse_args()
    config = load_config()
    patch = config.runtime_dir / "ownership-holder.yml"
    patch.parent.mkdir(parents=True, exist_ok=True)
    patch.write_text(
        "\n".join(
            [
                "# Temporary patch used by tests/live_ownership.py",
                "- insert:",
                "    - id: ownership-holder",
                f"      name: 'file:///{str(ROOT / 'adapter' / 'index.js').replace(os.sep, '/')}'",
                "      config:",
                "        port: 0",
                "        token: 'unused'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["ELECTRON_RUN_AS_NODE"] = "1"
    holder = RuntimeProcess(
        command=[str(config.dsh_exe), "--expose-internals", str(config.dsh_cli), "--profile", "sdk", "--patch", str(patch)],
        env=env,
        cwd=config.runtime_dir,
        stderr_log=config.logs_dir / "ownership-holder.log",
        default_timeout=180.0,
    )
    try:
        holder.call("initialize", {"cwd": str(config.projects_root), "provider": config.provider, "model": config.model}, timeout=180.0)
        holder.call("session/resume", {"sessionId": args.session}, timeout=300.0)
        check("second runtime holds the session", True, args.session)

        status, payload = call(args.base, "POST", "/api/local-tasks", {"threadId": args.session, "text": "ownership probe"})
        check(
            "bridge refuses an occupied session",
            status == 409 and payload.get("code") == "session_busy",
            f"status={status} body={json.dumps(payload, ensure_ascii=False)[:200]}",
        )
        status, thread = call(args.base, "GET", f"/api/thread/{args.session}?turnLimit=10")
        check("history still readable while occupied", status == 200, f"status={status}")
        texts = [item.get("text") for turn in ((thread.get("thread") or {}).get("turns") or []) for item in turn.get("items") or []]
        check("the refused input never entered the session", not any("ownership probe" in str(text or "") for text in texts))
    finally:
        holder.close()
    time.sleep(1.0)

    status, payload = call(args.base, "POST", "/api/local-tasks", {"threadId": args.session, "text": "Say only: ownership released."})
    check("bridge accepts the session once the other process exits", status == 200, f"status={status} body={json.dumps(payload, ensure_ascii=False)[:200]}")
    if status == 200:
        task_id = payload["taskId"]
        deadline = time.time() + 300
        last = {}
        while time.time() < deadline:
            s, last = call(args.base, "GET", f"/api/local-task/{task_id}")
            if last.get("status") in {"completed", "failed", "interrupted", "cancelled"}:
                break
            time.sleep(2.0)
        check("the accepted turn completed", last.get("status") == "completed", f"{last.get('status')} {last.get('error')}")

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
