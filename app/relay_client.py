# SPDX-License-Identifier: MIT
"""Outbound HTTPS relay. No runtime keys, cookies, or inbound ports are exposed."""
from __future__ import annotations

import base64
import concurrent.futures
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_REPLY = 32 * 1024 * 1024
MAX_MESSAGE = 64 * 1024 * 1024


class RelayClient:
    def __init__(self, relay_url, token, local_origin, base_path='/dsh/', workers=8):
        parsed = urllib.parse.urlsplit(relay_url)
        if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError('Relay requires an HTTPS endpoint without embedded credentials')
        local = urllib.parse.urlsplit(local_origin)
        if local.scheme != 'http' or local.hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise ValueError('Relay destination must be loopback HTTP')
        if len(token) < 32:
            raise ValueError('Relay token is not configured')
        self.relay_url = relay_url.rstrip('/')
        self.token = token
        self.local_origin = local_origin.rstrip('/')
        self.base_path = base_path
        self.stop = threading.Event()
        self.slots = threading.BoundedSemaphore(workers)
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix='dsh-relay')
        self.thread = None
        self.connected = False
        self.last_error = ''
        self.last_connected_at = 0.0

    def _remote(self, path, payload=None, timeout=35):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.relay_url + path, data=data, headers={
            'Authorization': 'Bearer ' + self.token,
            'Content-Type': 'application/json', 'Accept': 'application/json'},
            method='POST' if data is not None else 'GET')
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_MESSAGE + 1)
        if len(raw) > MAX_MESSAGE:
            raise ValueError('Relay message too large')
        return json.loads(raw)

    def execute(self, job):
        key = str(job.get('id') or '')
        path = str(job.get('path') or '')
        method = job.get('method')
        parsed = urllib.parse.urlsplit(path)
        decoded = urllib.parse.unquote(parsed.path)
        if (parsed.scheme or parsed.netloc or not decoded.startswith(self.base_path)
                or '\\' in decoded or any(x in {'.', '..'} for x in decoded.split('/'))
                or method not in {'GET', 'HEAD', 'POST'}):
            return {'id': key, 'status': 400, 'headers': {}, 'body': ''}
        try:
            body = base64.b64decode(job.get('body', ''), validate=True)
            if len(body) > 45 * 1024 * 1024:
                raise ValueError('Request too large')
            headers = {name: value for name, value in (job.get('headers') or {}).items()
                       if name.lower() in {'host', 'origin', 'content-type', 'accept', 'range', 'sec-fetch-site'}
                       and isinstance(value, str) and not any(c in value for c in '\r\n')}
            request = urllib.request.Request(self.local_origin + path,
                data=body if method == 'POST' else None, headers=headers, method=method)
            # Never follow a local redirect to a different host or outside the DSH mount.
            opener = urllib.request.build_opener(_NoRedirect)
            try:
                response = opener.open(request, timeout=145)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                raw = response.read(MAX_REPLY + 1)
                if len(raw) > MAX_REPLY:
                    raise ValueError('Response too large')
                return {'id': key, 'status': response.code,
                    'headers': dict(response.headers.items()), 'body': base64.b64encode(raw).decode()}
        except Exception:
            # No retries: a timed-out POST may already have reached the Harness.
            message = json.dumps({'error': 'dsh_local_unavailable', 'message':
                '主机请求结果暂未确认，请先检查历史，不要重复发送。'}, ensure_ascii=False).encode()
            return {'id': key, 'status': 502, 'headers': {'Content-Type': 'application/json; charset=utf-8'},
                    'body': base64.b64encode(message).decode()}

    def _run_job(self, job):
        try:
            result = self.execute(job)
            for attempt in range(3):
                try:
                    self._remote('/result', result, timeout=35)
                    break
                except Exception:
                    self.stop.wait(1 + attempt)
        finally:
            self.slots.release()

    def _loop(self):
        delay = 1
        while not self.stop.is_set():
            acquired = self.slots.acquire(blocking=False)
            try:
                data = self._remote('/poll' if acquired else '/heartbeat')
                self.connected = True
                self.last_error = ''
                self.last_connected_at = time.time()
                delay = 1
                job = data.get('request') if acquired else None
                if job:
                    self.executor.submit(self._run_job, job)
                    acquired = False
                elif not acquired:
                    self.stop.wait(5)
            except Exception as error:
                self.connected = False
                self.last_error = type(error).__name__
                self.stop.wait(delay)
                delay = min(20, delay * 2)
            finally:
                if acquired:
                    self.slots.release()

    def start(self):
        self.thread = threading.Thread(target=self._loop, name='dsh-outbound-relay', daemon=True)
        self.thread.start()

    def shutdown(self):
        self.stop.set()
        self.executor.shutdown(wait=False, cancel_futures=True)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
