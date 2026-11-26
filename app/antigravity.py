"""Antigravity CLI sessions scoped to the bridge project directory."""
import json
import hashlib
from contextlib import contextmanager
import os
import re
from pathlib import Path
import shutil
import sqlite3
import subprocess
import threading
from urllib.parse import unquote, urlsplit
import uuid

from .thread_library import ThreadLibrary
from .attachments import AttachmentStore
from . import antigravity_desktop as desktop_delivery

_lock = threading.Lock()
_workers = set()
_models_lock = threading.Lock()
_models_cache = []


def _attachment_prompt(cfg, data, prompt):
    files = data.get('files') or []
    if not isinstance(files, list) or len(files) > 3 or any(not isinstance(f, dict) for f in files):
        raise ValueError('附件最多 3 个，附件数据无效')
    if not files:
        return prompt
    # Stable staging paths preserve desktop delivery idempotency on retries.
    key = hashlib.sha256(json.dumps([data.get('id'), data.get('requestId'), prompt, files],
                                   sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    store = AttachmentStore(Path(cfg.home) / 'data/uploads/antigravity',
                            min(getattr(cfg, 'attachments_max_bytes', 10*1024*1024), 10*1024*1024),
                            min(getattr(cfg, 'attachments_max_batch_bytes', 15*1024*1024), 15*1024*1024))
    staged, _ = store.stage(key, files, persist_images=True)
    if not staged:
        raise ValueError('附件内容为空')
    return prompt + store.prompt_suffix(staged)


def _cli():
    exe = shutil.which("agy.exe") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "agy/bin/agy.exe")
    if not Path(exe).is_file():
        raise ValueError("本机尚未安装 Antigravity CLI")
    return exe


def _run(arguments, cwd, timeout):
    # agy starts console children. They can share this hidden console.
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        creationflags = subprocess.CREATE_NEW_CONSOLE
    return subprocess.run([_cli(), *arguments], cwd=cwd, capture_output=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          stdin=subprocess.DEVNULL, startupinfo=startupinfo,
                          creationflags=creationflags)


@contextmanager
def _connect(config):
    path = Path(config.home) / "data" / "antigravity.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, native TEXT, cwd TEXT, title TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS turns(n INTEGER PRIMARY KEY, session TEXT, prompt TEXT, reply TEXT, status TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS settings(session TEXT PRIMARY KEY, model TEXT, mode TEXT, effort TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS cli_permissions(session TEXT PRIMARY KEY, permission TEXT NOT NULL)")
    try:
        with db:
            yield db
    finally:
        db.close()


_DESKTOP_INDEX = Path.home() / ".gemini" / "antigravity" / "conversation_summaries.db"
_DESKTOP_PREFIX = "desktop:"


@contextmanager
def _desktop_index():
    # Read the live index, including committed WAL updates, without copying chats.
    db = sqlite3.connect(_DESKTOP_INDEX.as_uri() + "?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def _desktop_workspace(raw):
    uris = json.loads(raw or "[]")
    if not uris:
        return ""
    uri = urlsplit(uris[0])
    if uri.scheme != "file":
        return uris[0]
    path = unquote(uri.path)
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    return str(Path("//" + uri.netloc + path if uri.netloc else path))


# The desktop index includes internal child conversations and archived entries.
_VISIBLE_DESKTOP = """FROM conversation_summaries s
    WHERE COALESCE(s.parent_conversation_id,'')='' AND s.nesting_depth=0
    AND NOT EXISTS (SELECT 1 FROM conversations_fts f WHERE f.conversation_id=s.conversation_id
        AND f.chunk_index=-1 AND f.archived=1)"""


def _desktop_access(cfg, row):
    try:
        uris = json.loads(row["workspace_uris"] or "[]")
        root = _projects_root(cfg)
        paths = [Path(_desktop_workspace(json.dumps([uri]))).resolve()
                 for uri in uris if urlsplit(uri).scheme == "file" and not urlsplit(uri).netloc]
        allowed = bool(paths) and len(paths) == len(uris) and all(
            path != root and path.is_relative_to(root) and path.is_dir() for path in paths)
    except (ValueError, OSError):
        allowed = False
    return {"readOnly": not (allowed and desktop_delivery.available(cfg.home)),
            "desktopBusy": row["status"] != "CASCADE_RUN_STATUS_IDLE"}


def _desktop_sessions(cfg, known):
    if not _DESKTOP_INDEX.is_file():
        return []
    with _desktop_index() as db:
        rows = db.execute("SELECT conversation_id,title,workspace_uris,status " + _VISIBLE_DESKTOP
                          + " ORDER BY last_modified_time DESC")
        return [{"id": _DESKTOP_PREFIX + row["conversation_id"],
                 "title": row["title"].strip() or "未命名对话",
                 "cwd": _desktop_workspace(row["workspace_uris"]), **_desktop_access(cfg, row)}
                for row in rows if row["conversation_id"] not in known]


def _indexed_messages(chunk):
    # Each role has its own UTF-8 byte offsets. Step indices interleave both roles.
    messages = []
    for role in ("user", "agent"):
        raw = (chunk[role + "_body"] or "").encode("utf-8")
        offsets = json.loads(chunk[role + "_step_byte_offsets"] or "[]")
        end = len(raw)
        for step, start in reversed(offsets):
            text = raw[start:end].decode("utf-8").strip()
            if text:
                messages.append((step, role, text))
            end = start
    return sorted(messages, reverse=True)


def _desktop_history(cfg, native, limit):
    if not _DESKTOP_INDEX.is_file():
        raise ValueError("桌面会话索引不可用")
    with _desktop_index() as db:
        db.execute("BEGIN")
        row = db.execute("SELECT status, workspace_uris " + _VISIBLE_DESKTOP + " AND s.conversation_id=?", (native,)).fetchone()
        if row is None:
            raise ValueError("此桌面会话已移除或归档")
        access = _desktop_access(cfg, row)
        access["lastReplyModel"] = desktop_delivery.last_reply_model(_DESKTOP_INDEX.parent, native)
        live = desktop_delivery.original_turns(cfg.home, _DESKTOP_INDEX, native, limit, access["desktopBusy"])
        if live is not None:
            return {**live, **access}
        chunks = db.execute("""SELECT user_body,agent_body,user_step_byte_offsets,agent_step_byte_offsets
            FROM conversations_fts WHERE conversation_id=? AND chunk_index>=0 AND archived=0
            ORDER BY chunk_index DESC""", (native,))
        turns, replies = [], []
        for chunk in chunks:
            for step, role, text in _indexed_messages(chunk):
                if role == "agent":
                    replies.append(text)
                    continue
                if limit is not None and len(turns) == limit:
                    return {"turns": turns[::-1], "more": True, **access}
                status = "completed" if replies else "unknown"
                if not turns and row["status"] == "CASCADE_RUN_STATUS_RUNNING":
                    status = "running"
                turns.append({"id": step, "prompt": text, "reply": "\n\n".join(reversed(replies)), "status": status})
                replies = []
        return {"turns": turns[::-1], "more": False, **access}


def _projects_root(cfg):
    return (cfg.antigravity_projects_root or Path.home() / "Documents" / "Gemini project").resolve()


def _standalone_dir(cfg):
    return (cfg.antigravity_standalone_dir or Path.home() / "Antigravity-Bridge" / "sessions").resolve()


def _projects(cfg):
    root = _projects_root(cfg)
    if not root.is_dir():
        return []
    return sorted((p.name for p in root.iterdir() if not p.name.startswith(".")
                   and p.is_dir() and p.resolve().parent == root), key=str.casefold)


def _working_directory(cfg, row, data):
    root = _projects_root(cfg)
    standalone = _standalone_dir(cfg)
    if row:
        # Older bridge conversations keep the exact directory recorded at creation.
        cwd = Path(row["cwd"]).resolve()
        if cwd.is_dir() and (cwd.is_relative_to(root) or cwd == standalone
                              or cwd.is_relative_to(Path(cfg.projects_root).resolve())
                              or cwd == Path(cfg.standalone_dir).resolve()):
            return cwd
        raise ValueError("此会话的工作目录已不可用")
    if data.get("standalone") and not data.get("newProject"):
        standalone.mkdir(parents=True, exist_ok=True)
        return standalone
    name = str(data.get("project") or "")
    if (data.get("standalone") or not name or name.startswith(".") or name.endswith((" ", "."))
            or not re.fullmatch(r'[^<>:"/\\|?*\x00-\x1f]{1,255}', name)
            or (data.get("newProject") and len(name) > 80)
            or re.fullmatch(r"(?:CON|NUL|PRN|AUX|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", name, re.I)):
        raise ValueError("请选择有效的 Antigravity 项目")
    cwd = (root / name).resolve()
    if cwd.parent != root:
        raise ValueError("请选择 Antigravity 项目目录内的项目")
    if data.get("newProject"):
        root.mkdir(parents=True, exist_ok=True)
        cwd.mkdir()
    if not cwd.is_dir():
        raise ValueError("所选 Antigravity 项目不存在")
    return cwd


def handle(handler, method, action, query=None):
    cfg = handler.config
    try:
        if method == "GET" and action == "icon":
            _cli()
            return handler._serve_static("antigravity-logo.svg")
        if method == "GET" and action == "usage":
            result = _run(["-p", "/usage"], Path.home(), 40)
            if result.returncode:
                raise ValueError("额度暂不可用，请确认本机 CLI 已登录")
            return handler._json({"report": result.stdout.strip()})
        if method == "GET" and action == "models":
            with _models_lock:
                try:
                    result = _run(["models"], Path.home(), 40)
                    if result.returncode:
                        raise ValueError("模型列表暂不可用")
                    items = []
                    for line in result.stdout.splitlines():
                        slug, sep, name = line.partition("\t")
                        if sep and re.fullmatch(r"[\w.:/-]+", slug):
                            items.append({"id":slug, "name":name.strip()})
                    if items:
                        _models_cache[:] = items
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    if not _models_cache:
                        raise
                return handler._json({"models": list(_models_cache)})
        if method == "GET" and action == "sessions":
            with _connect(cfg) as db, _lock:
                rows = db.execute("""SELECT s.id,s.native,s.title,s.cwd,g.model,g.mode,g.effort,coalesce(p.permission,'default') AS permission,max(t.n) AS recent
                    FROM sessions s JOIN turns t ON s.id=t.session LEFT JOIN settings g ON s.id=g.session LEFT JOIN cli_permissions p ON s.id=p.session
                    GROUP BY s.id ORDER BY recent DESC""")
                sessions = [dict(row) for row in rows]
            known = {session.pop("native") for session in sessions}
            desktop = _desktop_sessions(cfg, known)
            with _lock:
                library = ThreadLibrary(Path(cfg.home) / "data/antigravity-library.json").snapshot()
            for item in sessions + desktop:
                flags = library.get(item["id"], {})
                item.update(pinned=bool(flags.get("pinned")), hidden=bool(flags.get("hidden")))
            return handler._json({"threadLibrary": True, "sessions": sessions + desktop, "projects": _projects(cfg), "cliPermissions": True, "attachments": True})
        if method == "GET" and action.startswith("history/"):
            sid = action.split("/", 1)[1]
            requested = (query or {}).get("limit", ["6"])[0]
            limit = None if requested == "all" else max(6, min(100000, int(requested)))
            if sid.startswith(_DESKTOP_PREFIX):
                return handler._json({**_desktop_history(cfg, sid[len(_DESKTOP_PREFIX):], limit), "supportsAll": True})
            with _connect(cfg) as db, _lock:
                total = db.execute("SELECT count(*) FROM turns WHERE session=?", (sid,)).fetchone()[0]
                rows = [dict(row) for row in db.execute(
                    "SELECT n AS id,prompt,reply,status FROM turns WHERE session=? ORDER BY n DESC LIMIT ?",
                    (sid, limit if limit is not None else -1))]
                remaining = total - len(rows)
                rows.reverse()
                for row in rows:
                    if row["status"] == "running" and sid not in _workers:
                        row["status"] = "unknown"
                native = db.execute("SELECT native FROM sessions WHERE id=?", (sid,)).fetchone()
                model = desktop_delivery.last_reply_model(_DESKTOP_INDEX.parent.with_name("antigravity-cli"), native[0] if native else None)
                return handler._json({"turns":rows,"more":remaining > 0,"remaining":remaining,"supportsAll":True,"lastReplyModel":model})
        if method == "POST" and action == "library":
            data = handler._read_json()
            sid = data.get("threadId")
            if not isinstance(sid, str) or not sid or len(sid) > 200:
                raise ValueError("无效的会话 ID")
            patch = {k: data[k] for k in ("hidden", "pinned") if k in data}
            if not patch:
                raise ValueError("缺少整理操作")
            if sid.startswith(_DESKTOP_PREFIX):
                if not _DESKTOP_INDEX.is_file():
                    raise ValueError("桌面会话不存在")
                with _desktop_index() as db:
                    found = db.execute("SELECT 1 " + _VISIBLE_DESKTOP + " AND s.conversation_id=?",
                                       (sid[len(_DESKTOP_PREFIX):],)).fetchone()
            else:
                with _connect(cfg) as db:
                    found = db.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone()
            if not found:
                return handler._error(404, "会话不存在")
            with _lock:
                saved = ThreadLibrary(Path(cfg.home) / "data/antigravity-library.json").update(sid, patch)
            return handler._json(saved)
        if method != "POST" or action != "send":
            return handler._error(404, "接口不存在")
        data = handler._read_json()
        prompt = str(data.get("prompt") or "").strip()
        if not prompt and data.get('files'):
            prompt = '请查看并处理我上传的附件。'
        if not prompt or len(prompt)>16000 or prompt.startswith("/"):
            raise ValueError("请输入普通对话内容（最多 16000 字）")
        mode = data.get("mode") or "default"
        model = data.get("model") or ""
        effort = data.get("effort") or ""
        if effort not in ("","low","medium","high","xhigh","max"):
            raise ValueError("推理强度无效")
        if mode not in ("default", "plan", "accept-edits") or (model and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,119}", model)):
            raise ValueError("模型或执行模式无效")
        sid = data.get("id") or str(uuid.uuid4())
        if sid.startswith(_DESKTOP_PREFIX):
            native = sid[len(_DESKTOP_PREFIX):]
            if not _DESKTOP_INDEX.is_file():
                return handler._error(409, "桌面索引不可用")
            with _desktop_index() as db:
                row = db.execute("SELECT status,workspace_uris " + _VISIBLE_DESKTOP
                                 + " AND s.conversation_id=?", (native,)).fetchone()
                if row is None or _desktop_access(cfg, row)["readOnly"]:
                    return handler._error(409, "原对话暂不可写，请检查桌面连接和项目目录")
                if row["status"] != "CASCADE_RUN_STATUS_IDLE":
                    return handler._error(409, "桌面正在处理这条对话，请稍后再发送")
            try:
                prompt = _attachment_prompt(cfg, data, prompt)
                desktop_delivery.submit(cfg.home, native, prompt, data.get("requestId"))
            except desktop_delivery.DeliveryUnconfirmed as error:
                return handler._error(409, str(error), "delivery_unconfirmed")
            except ValueError as error:
                return handler._error(409, str(error), "delivery_rejected")
            return handler._json({"id": sid})
        permission = data.get("permission", "default")
        if permission not in ("default", "full"):
            raise ValueError("无效的 CLI 工具权限")
        _cli()
        with _lock, _connect(cfg) as db:
            if sid in _workers:
                return handler._error(409, "当前对话正在运行")
            row = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            if data.get("id") and (not row or not row["native"]):
                raise ValueError("无法续聊，请重新创建会话")
            cwd = _working_directory(cfg, row, data)
            prompt = _attachment_prompt(cfg, data, prompt)
            if not row:
                title = str(data.get("title") or "").strip() or prompt[:48]
                db.execute("INSERT INTO sessions VALUES(?,?,?,?)",(sid,None,str(cwd),title[:100]))
            db.execute("INSERT OR REPLACE INTO settings VALUES(?,?,?,?)",(sid,model,mode,effort))
            db.execute("INSERT OR REPLACE INTO cli_permissions(session,permission) VALUES(?,?)", (sid, permission))
            n=db.execute("INSERT INTO turns(session,prompt,reply,status) VALUES(?,?,?,?)",(sid,prompt,"","running")).lastrowid
            db.commit()
            _workers.add(sid)
        threading.Thread(target=_work,args=(cfg,sid,n,cwd,row["native"] if row else None,prompt,model,mode,effort,permission),daemon=True).start()
        handler._json({"id":sid})
    except sqlite3.Error:
        handler._error(503, "会话数据库暂不可读，请刷新重试")
    except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
        handler._error(400,str(exc))


def _work(cfg,sid,n,cwd,native,prompt,model,mode,effort,permission):
    reply, status = "CLI 未能完成本轮，请检查本机登录状态。", "failed"
    try:
        args = ["--output-format","json"]
        if permission == "full":
            args.append("--dangerously-skip-permissions")
        if model:
            args.extend(["--model", model])
        if effort:
            args.extend(["--effort", effort])
        if mode != "default":
            args.extend(["--mode", mode])
        if native:
            args.extend(["--conversation",native])
        result = _run([*args,"--print="+prompt],cwd,21600)
        data=json.loads(result.stdout)
        native=data.get("conversation_id") or native
        reply=data.get("response") or data.get("error") or reply
        if result.returncode==0 and data.get("status")=="SUCCESS":
            status="completed"
        elif data.get("status") in {"WAITING","INTERRUPTED","CANCELED","RUNNING"}:
            status={"WAITING":"waiting","INTERRUPTED":"interrupted","CANCELED":"cancelled","RUNNING":"unknown"}[data["status"]]
            reply=data.get("response") or data.get("error") or ""
    except (ValueError,OSError,subprocess.TimeoutExpired):
        pass
    finally:
        with _lock, _connect(cfg) as db:
            db.execute("UPDATE sessions SET native=? WHERE id=?",(native,sid))
            db.execute("UPDATE turns SET reply=?,status=? WHERE n=?",(reply,status,n))
            _workers.discard(sid)
