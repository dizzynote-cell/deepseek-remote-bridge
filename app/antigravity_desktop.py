"""Local delivery of user messages through the Antigravity App's official sidecar."""
from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid


class DeliveryUnconfirmed(ValueError):
    """The App may already have received the message."""


def _db(home):
    path = Path(home) / "data" / "desktop-delivery.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=3, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS heartbeat (id INTEGER PRIMARY KEY, seen REAL)")
    db.execute("""CREATE TABLE IF NOT EXISTS deliveries (
        id TEXT PRIMARY KEY, session TEXT, digest TEXT, message TEXT,
        created REAL, state TEXT, detail TEXT DEFAULT '')""")
    return db


def _online(db):
    row = db.execute("SELECT seen FROM heartbeat WHERE id=1").fetchone()
    return bool(row and time.time() - row[0] < 16)


def available(home):
    if not (Path(home) / "data" / "desktop-delivery.sqlite").exists():
        return False
    with closing(_db(home)) as db:
        return _online(db)


def submit(home, native, message, request_id):
    request_id = str(uuid.UUID(str(request_id)))
    digest = hashlib.sha256(message.encode("utf8")).hexdigest()
    with closing(_db(home)) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM deliveries WHERE id=?", (request_id,)).fetchone()
        if row and (row["session"] != native or row["digest"] != digest):
            raise ValueError("本次请求编号已使用，请刷新后再试")
        if not row:
            if not _online(db):
                raise ValueError("桌面连接未就绪，请打开 Antigravity 客户端")
            if db.execute("SELECT 1 FROM deliveries WHERE session=? AND state IN ('queued','sending') AND created>?",
                          (native, time.time() - 20)).fetchone():
                raise ValueError("此对话正在投递另一条消息，请稍后再试")
            db.execute("INSERT INTO deliveries VALUES(?,?,?,?,?,'queued','')",
                       (request_id, native, digest, message, time.time()))
        db.commit()
        end = time.monotonic() + 15
        while time.monotonic() < end:
            row = db.execute("SELECT state,detail FROM deliveries WHERE id=?", (request_id,)).fetchone()
            if row["state"] == "sent":
                return
            if row["state"] == "uncertain":
                raise DeliveryUnconfirmed(row["detail"])
            if row["state"] == "failed":
                raise ValueError(row["detail"])
            time.sleep(.15)
        db.execute("UPDATE deliveries SET state='failed',detail='桌面未接收消息',message='' WHERE id=? AND state='queued'", (request_id,))
        raise DeliveryUnconfirmed("尚未确认投递，请先检查原对话")


def original_turns(home, index, native, limit, busy):
    # The App records agentapi delivery as a SYSTEM_MESSAGE. Only match texts
    # submitted by this bridge, excluding unrelated automation and system events.
    if not (Path(home) / "data" / "desktop-delivery.sqlite").is_file():
        return None
    with closing(_db(home)) as db:
        receipts = {}
        for row in db.execute("SELECT digest,created FROM deliveries WHERE session=? AND state!='failed'", (native,)):
            receipts.setdefault(row["digest"], []).append(row["created"])
    if not receipts or str(uuid.UUID(native)) != native:
        return None
    transcript = index.parent / "brain" / native / ".system_generated" / "logs" / "transcript.jsonl"
    if not transcript.is_file():
        return None
    replies, turns = [], []
    with transcript.open("rb") as reader:
        for event in _recent_events(reader):
            kind = event.get("type")
            content = event.get("content") or ""
            if not isinstance(content, str) or not content:
                continue
            if kind == "PLANNER_RESPONSE":
                replies.append(content)
                continue
            if kind == "USER_INPUT":
                prompt = content.partition("<USER_REQUEST>")[2].partition("</USER_REQUEST>")[0].strip()
                if not prompt:
                    continue
            elif kind == "SYSTEM_MESSAGE":
                header, sep, body = content.partition(" sender=system priority=MESSAGE_PRIORITY_HIGH content=")
                if not sep or "[Message] timestamp=" not in header:
                    continue
                prompt = body.removesuffix("\n</SYSTEM_MESSAGE>").strip()
                digest = hashlib.sha256(prompt.encode("utf8")).hexdigest()
                if digest not in receipts:
                    continue
                try:
                    timestamp = datetime.fromisoformat(header.rsplit("timestamp=", 1)[1].replace("Z", "+00:00")).timestamp()
                except ValueError:
                    continue
                if not any(abs(timestamp - created) <= 30 for created in receipts[digest]):
                    continue
            else:
                continue
            if limit is not None and len(turns) == limit:
                return {"turns": turns[::-1], "more": True}
            turns.append({"id": event["step_index"], "prompt": prompt,
                          "reply": "\n\n".join(reversed(replies)),
                          "status": "running" if not turns and busy else "completed" if replies else "unknown"})
            replies = []
    return {"turns": turns[::-1], "more": False} if turns else None


def last_reply_model(data_dir, native):
    if not native:
        return ""
    try:
        if str(uuid.UUID(native)) != native:
            return ""
        path = data_dir / "brain" / native / ".system_generated" / "logs" / "transcript.jsonl"
        with path.open("rb") as reader:
            return next((e["model"] for e in _recent_events(reader)
                         if isinstance(e, dict) and e.get("type") == "PLANNER_RESPONSE" and e.get("model")), "")
    except (OSError, ValueError):
        return ""


def _recent_events(reader):
    position = reader.seek(0, 2)
    tail = b""
    while position:
        size = min(position, 65536)
        position -= size
        reader.seek(position)
        lines = (reader.read(size) + tail).split(b"\n")
        tail = lines.pop(0) if position else b""
        for line in reversed(lines):
            if line:
                try:
                    yield json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue


def work(home, executable):
    with closing(_db(home)) as db:
        cleanup = heartbeat = 0
        while True:
            now = time.time()
            db.execute("BEGIN IMMEDIATE")
            if now >= heartbeat:
                db.execute("INSERT OR REPLACE INTO heartbeat VALUES(1,?)", (now,))
                heartbeat = now + 2
            if now >= cleanup:
                db.execute("""UPDATE deliveries SET state='failed',message='',detail='桌面未接收消息'
                              WHERE state='queued' AND created<?""", (now - 12,))
                db.execute("DELETE FROM deliveries WHERE state='failed' AND created<?", (now - 7*86400,))
                db.execute("""UPDATE deliveries SET state='uncertain',detail='投递结果未知，请检查原对话'
                              WHERE state='sending' AND created<?""", (now - 60,))
                cleanup = now + 30
            row = db.execute("SELECT * FROM deliveries WHERE state='queued' AND created>=? ORDER BY created LIMIT 1",
                             (now - 12,)).fetchone()
            if row:
                db.execute("UPDATE deliveries SET state='sending',message='' WHERE id=?", (row["id"],))
            db.commit()
            if not row:
                time.sleep(.5)
                continue
            try:
                result = subprocess.run([str(executable), "agentapi", "send-message", row["session"], row["message"]],
                                        capture_output=True, timeout=10, stdin=subprocess.DEVNULL,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                payload = json.loads(result.stdout or result.stderr or b"{}")
                if result.returncode or not isinstance(payload, dict) or payload.get("error"):
                    state, detail = "failed", "桌面客户端拒绝了向此原对话投递消息"
                else:
                    state, detail = "sent", ""
            except (OSError, ValueError, subprocess.TimeoutExpired):
                state, detail = "uncertain", "请检查原对话以确认投递结果"
            db.execute("UPDATE deliveries SET state=?,detail=? WHERE id=?", (state, detail, row["id"]))


if __name__ == "__main__":
    # Only the desktop-managed worker receives the App transport environment.
    if os.environ.get("ANTIGRAVITY_LS_ADDRESS") and "antigravity-cli" not in os.environ.get("ANTIGRAVITY_EXECUTABLE_DATA_DIR", "").lower():
        work(Path(sys.argv[1]), Path(sys.argv[2]))
