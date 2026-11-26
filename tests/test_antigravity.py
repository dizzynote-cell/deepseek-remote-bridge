from contextlib import closing
import hashlib
import base64
import json
import time
import uuid
from datetime import datetime, timezone
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import antigravity as ag


class Handler:
    def __init__(self, root):
        self.config = SimpleNamespace(home=root, projects_root=root/'dsh', standalone_dir=root/'dsh-sessions',
                                      antigravity_projects_root=root/'anti', antigravity_standalone_dir=root/'anti-sessions')
        self.data = {}

    def _read_json(self):
        return self.data

    def _json(self, data):
        self.result = data

    def _error(self, code, message):
        self.result = (code, message)


class AntigravityTests(unittest.TestCase):
    def test_uploads_persist_images_and_documents_and_retry_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Handler(Path(tmp).resolve())
            files = [{'name': '截图.png', 'mime': 'image/png', 'data': base64.b64encode(b'png-bytes').decode()},
                     {'name': '../note.txt', 'data': base64.b64encode(b'document').decode()}]
            data = {'id': 'desktop:test', 'requestId': 'same-request', 'files': files}
            prompt = ag._attachment_prompt(h.config, data, 'read')
            self.assertEqual(prompt, ag._attachment_prompt(h.config, data, 'read'))
            stored = list((h.config.home / 'data/uploads/antigravity').rglob('*'))
            actual = [p for p in stored if p.is_file()]
            self.assertEqual(len(actual), 2)
            self.assertEqual({p.read_bytes() for p in actual}, {b'png-bytes', b'document'})
            for p in actual:
                self.assertIn(str(p), prompt)
                self.assertTrue(p.resolve().is_relative_to(h.config.home / 'data/uploads/antigravity'))
            for bad in [[files[0]]*4, [None], [{'name':'x', 'data':'???'}]]:
                with self.assertRaises(ValueError):
                    ag._attachment_prompt(h.config, {'files':bad}, 'read')

    def test_attachment_only_message_reaches_cli_as_local_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Handler(Path(tmp).resolve())
            h.data = {'standalone':True, 'files':[{'name':'shot.png','data':'aW1hZ2U='}]}
            with patch.object(ag, '_cli', return_value='agy'), patch.object(ag.threading, 'Thread') as worker:
                ag.handle(h, 'POST', 'send')
                args = worker.call_args.kwargs['args']
                self.assertIn('01-shot.png', args[5])
                result = SimpleNamespace(returncode=0, stdout='{"status":"SUCCESS","response":"ok","conversation_id":"test"}')
                with patch.object(ag, '_run', return_value=result) as run:
                    ag._work(*args)
                self.assertIn('01-shot.png', run.call_args.args[0][-1])

    def test_native_resume_and_workspace_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Handler(Path(tmp).resolve())
            h.data = {"prompt": "first", "project": ".."}
            with patch.object(ag, "_cli", return_value="agy"), patch.object(ag.threading, "Thread") as worker:
                ag.handle(h, "POST", "send")
                self.assertEqual(h.result[0], 400)
                worker.assert_not_called()
                (h.config.antigravity_projects_root / "demo").mkdir(parents=True)
                h.data["project"] = "demo"
                ag.handle(h, "POST", "send")
                sid = h.result["id"]
                h.data["id"] = sid
                ag.handle(h, "POST", "send")
                self.assertEqual(h.result[0], 409)
                args = worker.call_args.kwargs["args"]
                result = SimpleNamespace(returncode=0, stdout='{"status":"SUCCESS","response":"done","conversation_id":"native-id"}')
                with patch.object(ag, "_run", return_value=result) as run:
                    ag._work(*args)
                self.assertNotIn("--dangerously-skip-permissions", run.call_args.args[0])
                ag.handle(h, "GET", "history/" + sid)
                self.assertEqual(h.result["turns"][0]["status"], "completed")
                h.data["permission"] = "full"
                ag.handle(h, "POST", "send")
                args = worker.call_args.kwargs["args"]
                with patch.object(ag, "_run", return_value=result) as run:
                    ag._work(*args)
                self.assertIn("native-id", run.call_args.args[0])
                self.assertIn("--dangerously-skip-permissions", run.call_args.args[0])


    def test_desktop_delivery_replay_and_transcript(self):
        delivery = ag.desktop_delivery
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            sid, key = str(uuid.uuid4()), str(uuid.uuid4())
            prompt, reply = '联调', '收到'
            now = time.time()
            index = home / 'desktop.db'
            self.assertFalse(delivery.available(home))
            with self.assertRaisesRegex(ValueError, '桌面连接未就绪'):
                delivery.submit(home, sid, prompt, key)
            with closing(delivery._db(home)) as db:
                db.execute("INSERT INTO heartbeat VALUES(1,?)", (now,))
                db.execute("INSERT INTO deliveries VALUES(?,?,?,?,?,'sent','')",
                           (key, sid, hashlib.sha256(prompt.encode()).hexdigest(), '', now))
            delivery.submit(home, sid, prompt, key)
            with self.assertRaisesRegex(ValueError, '编号已使用'):
                delivery.submit(home, sid, '不同草稿', key)
            stamp = datetime.fromtimestamp(now, timezone.utc).isoformat()
            header = f'[Message] timestamp={stamp} sender=system priority=MESSAGE_PRIORITY_HIGH content='
            events = [('USER_INPUT', '<USER_REQUEST>旧消息</USER_REQUEST>'), ('PLANNER_RESPONSE', '较早回复'),
                      ('SYSTEM_MESSAGE', header + prompt + '\n</SYSTEM_MESSAGE>'),
                      ('SYSTEM_MESSAGE', header + '无关通知\n</SYSTEM_MESSAGE>'), ('PLANNER_RESPONSE', reply)]
            path = home / 'brain' / sid / '.system_generated' / 'logs' / 'transcript.jsonl'
            path.parent.mkdir(parents=True)
            path.write_text(''.join(json.dumps(dict(step_index=i,type=k,content=v))+'\n'
                            for i,(k,v) in enumerate(events)), 'utf8')
            history = delivery.original_turns(home,index,sid,1,False)
            self.assertEqual(history, {'turns':[{'id':2,'prompt':prompt,'reply':reply,'status':'completed'}], 'more':True})
            all_history = delivery.original_turns(home, index, sid, None, False)
            self.assertFalse(all_history['more'])
            self.assertEqual([turn['id'] for turn in all_history['turns']], [0, 2])
            with closing(delivery._db(home)) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0], 1)
                db.execute("UPDATE heartbeat SET seen=0")
            self.assertFalse(delivery.available(home))

    def test_provider_project_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Handler(Path(tmp).resolve())
            cfg = h.config
            other = cfg.projects_root / 'DSH project'
            other.mkdir(parents=True)
            with patch.object(ag, '_DESKTOP_INDEX', Path(tmp) / 'absent.db'):
                ag.handle(h, 'GET', 'sessions')
                self.assertEqual(h.result['projects'], [])
                path = ag._working_directory(cfg, None, {'newProject': True, 'project': '视频'})
                self.assertEqual(path, cfg.antigravity_projects_root / '视频')
                ag.handle(h, 'GET', 'sessions')
                self.assertEqual(h.result['projects'], ['视频'])
                self.assertEqual(ag._working_directory(cfg, None, {'project': '视频'}), path)
                for name in ('DSH project', str(other), '../dsh', '.', '', 'NUL.txt'):
                    with self.subTest(name=name), self.assertRaises(ValueError):
                        ag._working_directory(cfg, None, {'project': name})
                with self.assertRaises(ValueError):
                    ag._working_directory(cfg, None, {'project': '../outside', 'newProject': True})
                self.assertEqual(ag._working_directory(cfg, None, {'standalone': True}), cfg.antigravity_standalone_dir)
                self.assertFalse(cfg.standalone_dir.exists())
                self.assertEqual(ag._working_directory(cfg, {'cwd': other}, {'project': '视频'}), other)

    def test_desktop_index_visibility_history_and_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Handler(Path(tmp).resolve())
            index = Path(tmp) / 'desktop.db'
            with closing(sqlite3.connect(index)) as db, db:
                db.executescript("""
                    CREATE TABLE conversation_summaries(conversation_id, title, workspace_uris,
                        last_modified_time, nesting_depth, parent_conversation_id, status);
                    CREATE TABLE conversations_fts(conversation_id, chunk_index, archived,
                        user_body, agent_body, user_step_byte_offsets, agent_step_byte_offsets);
                """)
                for name, parent in [('visible', ''), ('archived', ''), ('child', 'visible')]:
                    db.execute("INSERT INTO conversation_summaries VALUES(?,?,?,?,?,?,?)",
                               (name, name, '["file:///D:/my%20project"]', '2026-01-01', bool(parent), parent, 'IDLE'))
                db.execute("INSERT INTO conversations_fts VALUES('archived', -1, 1, '', '', '', '')")
                for n in range(8):
                    db.execute("INSERT INTO conversations_fts VALUES(?,?,?,?,?,?,?)",
                               ('visible', n, 0, '问'+str(n), '甲\n乙', json.dumps([[3*n,0]]),
                                json.dumps([[3*n+1,0],[3*n+2,4]])))
            with patch.object(ag, '_DESKTOP_INDEX', index):
                ag.handle(h, 'GET', 'sessions')
                rows = h.result['sessions']
                self.assertEqual([r['title'] for r in rows], ['visible'])
                self.assertTrue(rows[0]['readOnly'])
                self.assertIn('my project', rows[0]['cwd'])
                self.assertEqual(ag._desktop_sessions(h.config, {'visible'}), [])
                sid = rows[0]['id']
                ag.handle(h, 'GET', 'history/'+sid)
                self.assertTrue(h.result['more'])
                self.assertEqual([t['prompt'] for t in h.result['turns']], ['问'+str(i) for i in range(2,8)])
                self.assertEqual(h.result['turns'][-1]['reply'], '甲\n\n乙')
                ag.handle(h, 'GET', 'history/'+sid, {'limit':['12']})
                self.assertFalse(h.result['more'])
                self.assertEqual(len(h.result['turns']), 8)
                h.data = {'id':sid, 'prompt':'test'}
                with patch.object(ag, '_run') as run:
                    ag.handle(h, 'POST', 'send')
                    self.assertEqual(h.result[0], 409)
                    run.assert_not_called()
                with closing(sqlite3.connect(index)) as db, db:
                    db.execute("DELETE FROM conversation_summaries WHERE conversation_id='visible'")
                ag.handle(h, 'GET', 'sessions')
                self.assertEqual(h.result['sessions'], [])
