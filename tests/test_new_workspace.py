"""New conversations prepare only allowed directories before contacting Harness."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.server import _prepare_new_cwd
from app.tasks import TaskRejected


class NewWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = SimpleNamespace(config=SimpleNamespace(
            projects_root=self.root / 'projects', standalone_dir=self.root / 'sessions'))

    def test_new_project_and_standalone_create_missing_directories(self):
        for path in (self.state.config.projects_root / 'new-project', self.state.config.standalone_dir):
            self.assertFalse(path.exists())
            self.assertEqual(_prepare_new_cwd(self.state, str(path)), path)
            self.assertTrue(path.is_dir())

    def test_existing_project_keeps_its_contents(self):
        path = self.state.config.projects_root / 'existing'
        path.mkdir(parents=True)
        marker = path / 'keep.txt'
        marker.write_text('keep', encoding='utf8')
        self.assertEqual(_prepare_new_cwd(self.state, str(path)), path)
        self.assertEqual(marker.read_text(encoding='utf8'), 'keep')

    def test_http_creation_prepares_all_three_modes_before_submission(self):
        from app.config import BridgeConfig
        from tests.test_server import FakeState, get, start
        import json

        config = BridgeConfig(root=self.root, home=self.root / 'state', web_dir=self.root,
                              projects_root=self.state.config.projects_root,
                              standalone_dir=self.state.config.standalone_dir, port=0)
        state = FakeState(config, self.root)
        state.attachments = SimpleNamespace(stage=lambda *_: ([], []), prompt_suffix=lambda _: '')
        submitted = []

        def create(cwd, blocks, **kwargs):
            self.assertTrue(Path(cwd).is_dir())
            submitted.append(cwd)
            return SimpleNamespace(id='task-test', session_id=kwargs['session_id'], detail={})

        state.tasks.create_session = create
        server = start(state, config)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f'http://127.0.0.1:{server.server_address[1]}/api/local-tasks'
        existing = config.projects_root / 'existing'
        existing.mkdir(parents=True)
        for cwd in (config.projects_root / 'new-project', config.standalone_dir, existing):
            status, body, _ = get(url, method='POST', payload={
                'op': 'new_thread', 'cwd': str(cwd), 'text': 'test', 'title': 'test'})
            self.assertEqual(status, 200, body)
            self.assertEqual(json.loads(body)['taskId'], 'task-test')
        status, _, _ = get(url, method='POST', payload={
            'op': 'new_thread', 'cwd': str(self.root / 'forbidden'), 'text': 'test'})
        self.assertEqual(status, 403)
        self.assertEqual(len(submitted), 3)
        self.assertFalse((self.root / 'forbidden').exists())

    def test_forbidden_paths_do_not_create_directories(self):
        for raw in (str(self.root / 'outside'), str(self.state.config.projects_root / '..' / 'escape'), 'relative'):
            with patch.object(Path, 'mkdir') as mkdir:
                with self.assertRaises(TaskRejected) as caught:
                    _prepare_new_cwd(self.state, raw)
                self.assertEqual(caught.exception.code, 'cwd_forbidden')
                mkdir.assert_not_called()

    def test_file_and_permission_failures_are_reported(self):
        path = self.state.config.standalone_dir
        path.write_text('keep', encoding='utf8')
        with self.assertRaises(TaskRejected) as caught:
            _prepare_new_cwd(self.state, str(path))
        self.assertEqual(caught.exception.code, 'cwd_unavailable')
        self.assertEqual(path.read_text(encoding='utf8'), 'keep')
        with patch.object(Path, 'mkdir', side_effect=PermissionError('denied')):
            with self.assertRaises(TaskRejected) as caught:
                _prepare_new_cwd(self.state, str(self.state.config.projects_root / 'denied'))
        self.assertEqual(caught.exception.code, 'cwd_unavailable')
