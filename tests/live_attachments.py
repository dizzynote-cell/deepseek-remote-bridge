"""Live check for attachments and image prompts.

Uploads a small text file and a generated PNG to a private session, then asks the
model to echo a magic word from the text file and describe the image.

Usage:
    python tests/live_attachments.py --base http://127.0.0.1:8766
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESULTS: list[tuple[str, bool, str]] = []
MAGIC = "ATTACH-MAGIC-7788"


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


def tiny_png(width: int = 32, height: int = 32, rgb=(87, 217, 154)) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


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
    parser.add_argument("--workspace", default=str(ROOT / "tests" / "integration-workspace"))
    args = parser.parse_args()
    base = args.base
    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    text_file = f"附件说明\nmagic: {MAGIC}\n"
    files = [
        {"name": "attachment-note.txt", "mime": "text/plain", "data": base64.b64encode(text_file.encode("utf-8")).decode("ascii")},
        {"name": "attachment-green.png", "mime": "image/png", "data": base64.b64encode(tiny_png()).decode("ascii")},
    ]
    prompt = (
        "I attached one text file and one image. Read the text file and reply with its magic value "
        "exactly, then on a second line describe the image colour in one word."
    )
    status, created = call(
        base,
        "POST",
        "/api/local-tasks",
        {"op": "new_thread", "cwd": str(workspace), "title": "attachment check", "text": prompt, "files": files},
        timeout=300,
    )
    check("attachment turn accepted", status == 200, json.dumps(created, ensure_ascii=False)[:300])
    if status != 200:
        return 1
    session_id = created["threadId"]
    task = wait_task(base, created["taskId"])
    check("attachment turn completed", task.get("status") == "completed", f"{task.get('status')} {task.get('error')}")

    status, thread = call(base, "GET", f"/api/thread/{session_id}?turnLimit=10")
    items = [item for turn in ((thread.get("thread") or {}).get("turns") or []) for item in turn.get("items") or []]
    user_items = [item for item in items if item.get("type") == "userMessage"]
    check("image counted on the user message", any(item.get("bridgeImageCount") for item in user_items), json.dumps(user_items[:1], ensure_ascii=False)[:200])
    answer = " ".join(str(item.get("text") or "") for item in items if item.get("type") == "agentMessage")
    check("model received the attachment path and read it", MAGIC in answer, answer[:300])
    check("model acknowledges the image", "green" in answer.lower() or "绿" in answer, answer[:300])

    uploads = ROOT / "state" / "data" / "uploads" / session_id
    check("upload staged inside the bridge home", uploads.is_dir() and any(uploads.iterdir()), str(uploads))

    shutil.rmtree(workspace, ignore_errors=True)
    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
