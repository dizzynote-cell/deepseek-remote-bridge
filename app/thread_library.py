"""Reversible bridge-only hide/pin metadata; never changes harness history."""
import json
import os
import threading
import time
from pathlib import Path

class ThreadLibrary:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def snapshot(self):
        with self.lock:
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
                return data if isinstance(data, dict) else {}
            except (OSError, ValueError):
                return {}

    def update(self, thread_id, patch):
        with self.lock:
            data = self.snapshot()
            item = data.setdefault(thread_id, {})
            for key in ('hidden', 'pinned'):
                if key in patch:
                    if type(patch[key]) is not bool:
                        raise ValueError(key + ' must be boolean')
                    item[key] = patch[key]
            if isinstance(patch.get('name'), str):
                item['name'] = patch['name'][:200]
            item["updatedAt"] = time.time()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            os.replace(temp, self.path)
            return dict(item)
