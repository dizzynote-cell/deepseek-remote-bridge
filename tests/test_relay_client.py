import base64
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from app.relay_client import RelayClient


class RelayClientTests(unittest.TestCase):
    def test_poll_recovers_and_result_retry_does_not_repeat_execution(self):
        relay = RelayClient('https://bridge.example.test/api/device/dsh-relay', 't' * 48,
                            'http://127.0.0.1:8766')
        calls = []
        def remote(path, *args, **kwargs):
            calls.append(path)
            if len(calls) == 1:
                raise ConnectionError('synthetic disconnect')
            relay.stop.set()
            return {}
        try:
            with patch.object(relay, '_remote', side_effect=remote), patch.object(relay.stop, 'wait', return_value=False):
                relay._loop()
            self.assertEqual(calls, ['/poll', '/poll'])
            self.assertTrue(relay.connected)
            self.assertEqual(relay.last_error, '')
            relay.stop.clear()
            relay.slots.acquire()
            with patch.object(relay, 'execute', return_value={'id': 'demo'}) as execute, \
                 patch.object(relay, '_remote', side_effect=[ConnectionError(), {}]) as report, \
                 patch.object(relay.stop, 'wait', return_value=False):
                relay._run_job({'id': 'demo'})
            execute.assert_called_once_with({'id': 'demo'})
            self.assertEqual(report.call_count, 2)
        finally:
            relay.shutdown()

    def test_requires_https_and_loopback_destination(self):
        for remote, local in [('http://example.test', 'http://localhost:1'),
                              ('https://example.test', 'http://example.test')]:
            with self.assertRaises(ValueError): RelayClient(remote, 't' * 48, local)

    def test_binary_response_and_no_request_retries(self):
        calls = []
        class H(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                calls.append((self.path, self.headers.get('Cookie'), self.rfile.read(int(self.headers.get('Content-Length', 0)))))
                self.send_response(200); self.send_header('Content-Length', '3'); self.end_headers(); self.wfile.write(b'\x00\x01\xff')
        server = ThreadingHTTPServer(('127.0.0.1', 0), H)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        relay = RelayClient('https://bridge.example.test/api/device/dsh-relay', 't' * 48,
                            f'http://127.0.0.1:{server.server_port}')
        try:
            result = relay.execute({'id': 'demo', 'method': 'POST', 'path': '/dsh/api/local-tasks',
                'headers': {'Cookie': 'must-be-dropped'}, 'body': base64.b64encode(b'{}').decode()})
            self.assertEqual(result['status'], 200)
            self.assertEqual(base64.b64decode(result['body']), b'\x00\x01\xff')
            self.assertEqual(calls, [('/dsh/api/local-tasks', None, b'{}')])
            bad = relay.execute({'id': 'bad', 'method': 'POST', 'path': '/dsh/../api/sync'})
            self.assertEqual(bad['status'], 400); self.assertEqual(len(calls), 1)
        finally:
            relay.shutdown(); server.shutdown(); server.server_close(); thread.join()
