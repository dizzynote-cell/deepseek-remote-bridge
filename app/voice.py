# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
# Project: DeepSeek Remote Bridge
"""Optional on-demand speech synthesis with a bounded local MP3 cache.

Only the bridge process talks to the TTS provider; the browser receives a cache
key and an audio URL. Keys and provider identifiers stay in the bridge's own
configuration file or environment, never in the page.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

PROVIDER_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"
PIPELINE_TAG = "v3-sse-mp3-rate25"
MAX_TEXT_CHARS = 12_000
MAX_AUDIO_BYTES = 20 * 1024 * 1024
CACHE_SOFT_LIMIT = 2 * 1024**3
CACHE_FREE_FLOOR = 5 * 1024**3
REQUEST_TIMEOUT = 120


class VoiceError(RuntimeError):
    """A stable, user-facing voice failure."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(code)
        self.code = code
        self.message = message or code


@dataclass(frozen=True)
class VoiceSettings:
    api_key: str
    voice: str
    resource: str

    @property
    def ready(self) -> bool:
        return bool(self.api_key and self.voice and self.resource)

    def public(self) -> dict[str, object]:
        return {"configured": self.ready, "voice": self.voice, "resource": self.resource}


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except (FileNotFoundError, OSError):
        return values
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] in {'"', "'"} and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_settings(voice_env_file: Path | None) -> VoiceSettings:
    """Read provider credentials from the configured file, then the environment."""
    file_values: dict[str, str] = {}
    if voice_env_file is not None:
        file_values = _read_env_file(voice_env_file)
    def pick(*names: str) -> str:
        for name in names:
            for source in (os.environ, file_values):
                value = source.get(name, "")
                if value:
                    return value
        return ""
    return VoiceSettings(
        api_key=pick("VOLC_API_KEY"),
        voice=pick("VOLC_VOICE_ID"),
        resource=pick("VOLC_RESOURCE_ID", "X-Api-Resource-Id"),
    )


def digest(text: str, settings: VoiceSettings) -> str:
    """Cache key shared with the page: SHA-256 over the exact request payload."""
    payload = json.dumps(
        [text, settings.voice, settings.resource, PIPELINE_TAG],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_digest(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


class VoiceService:
    """Synthesize on demand and cache the MP3 beside the bridge's own state."""

    def __init__(self, cache_dir: Path, settings: VoiceSettings, enabled: bool):
        self.cache_dir = cache_dir
        self.settings = settings
        self.enabled = enabled
        self._lock = threading.RLock()
        self._inflight: dict[str, threading.Event] = {}
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def available(self) -> bool:
        return bool(self.enabled and self.settings.ready)

    def public(self) -> dict[str, object]:
        return {**self.settings.public(), "enabled": bool(self.enabled)}

    def path_for(self, key: str) -> Path:
        if not is_digest(key):
            raise VoiceError("invalid_voice_key")
        return self.cache_dir / f"{key}.mp3"

    def cached(self, key: str) -> bool:
        try:
            path = self.path_for(key)
        except VoiceError:
            return False
        return path.is_file() and path.stat().st_size > 0

    def status(self, keys: list[str]) -> dict[str, dict[str, bool]]:
        return {"cached": {key: self.cached(key) for key in keys if is_digest(key)}}

    def synthesize(self, text: str) -> tuple[str, Path]:
        if not self.available:
            raise VoiceError("voice_not_configured", "本机语音尚未配置或已关闭")
        clean = (text or "").strip()
        if not clean:
            raise VoiceError("voice_text_empty", "没有可朗读的文字")
        if len(clean) > MAX_TEXT_CHARS:
            raise VoiceError("voice_text_too_long", "文字过长，无法一次合成")
        key = digest(clean, self.settings)
        with self._lock:
            target = self.cache_dir / f"{key}.mp3"
            if target.is_file() and target.stat().st_size > 0:
                os.utime(target, None)
                return key, target
            event = self._inflight.get(key)
            producer = event is None
            if producer:
                event = threading.Event()
                self._inflight[key] = event
        if not producer:
            event.wait(REQUEST_TIMEOUT)
            if target.is_file() and target.stat().st_size > 0:
                return key, target
            raise VoiceError("voice_synthesis_timeout", "等待语音合成超时")
        try:
            audio = self._download(clean)
            with self._lock:
                self._trim(len(audio))
                temporary = self.cache_dir / f"{key}.{uuid.uuid4().hex}.tmp"
                try:
                    temporary.write_bytes(audio)
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
            return key, target
        finally:
            with self._lock:
                self._inflight.pop(key, None)
                event.set()

    def _download(self, text: str) -> bytes:
        body = json.dumps(
            {
                "user": {"uid": "deepseek-remote-bridge"},
                "req_params": {
                    "text": text,
                    "speaker": self.settings.voice,
                    "audio_params": {"format": "mp3", "bit_rate": 64000, "speech_rate": 25},
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            PROVIDER_URL,
            data=body,
            method="POST",
            headers={
                "X-Api-Key": self.settings.api_key,
                "X-Api-Resource-Id": self.settings.resource,
                "X-Api-Request-Id": str(uuid.uuid4()),
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
        )
        chunks: list[bytes] = []
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                if response.status != 200:
                    raise VoiceError("voice_provider_error", f"语音服务返回 HTTP {response.status}")
                for raw in response:
                    line = raw.strip()
                    if not line or not line.startswith(b"data:"):
                        continue
                    try:
                        payload = json.loads(line[5:])
                    except ValueError:
                        continue
                    code = payload.get("code")
                    if code not in (None, 0, 20000000):
                        raise VoiceError("voice_provider_error", f"语音服务返回错误码 {code}")
                    data = payload.get("data")
                    if data:
                        try:
                            chunks.append(base64.b64decode(data, validate=True))
                        except (ValueError, TypeError):
                            raise VoiceError("voice_provider_error", "语音服务返回了无法解码的音频") from None
                    if sum(len(chunk) for chunk in chunks) > MAX_AUDIO_BYTES:
                        raise VoiceError("voice_audio_too_large", "合成音频过大")
        except urllib.error.HTTPError as error:
            raise VoiceError("voice_provider_error", f"语音服务返回 HTTP {error.code}") from None
        except urllib.error.URLError as error:
            raise VoiceError("voice_provider_unavailable", f"无法连接语音服务：{error.reason}") from None
        except TimeoutError:
            raise VoiceError("voice_provider_unavailable", "语音服务请求超时") from None
        audio = b"".join(chunks)
        if not audio:
            raise VoiceError("voice_provider_error", "语音服务没有返回音频")
        return audio

    def _trim(self, required: int) -> None:
        """Keep the cache bounded without ever relying on a timer."""
        files = sorted((p for p in self.cache_dir.glob("*.mp3") if p.is_file()), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        free = shutil.disk_usage(self.cache_dir).free
        if total + required <= CACHE_SOFT_LIMIT and free - required >= CACHE_FREE_FLOOR:
            return
        for path in files:
            if total + required <= CACHE_SOFT_LIMIT // 2 and free - required >= CACHE_FREE_FLOOR:
                break
            if path.stem in self._inflight:
                continue
            try:
                size = path.stat().st_size
                path.unlink()
                total -= size
                free += size
            except OSError:
                continue

    def sweep(self, max_age_seconds: float = 30 * 86400) -> int:
        removed = 0
        deadline = time.time() - max_age_seconds
        try:
            for path in self.cache_dir.glob("*.mp3"):
                if path.stat().st_mtime < deadline:
                    path.unlink()
                    removed += 1
        except OSError:
            pass
        return removed
