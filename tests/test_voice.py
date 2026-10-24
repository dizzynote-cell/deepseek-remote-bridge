# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan
"""Voice settings, cache keys, and the local-only synthesis boundary."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.preferences import Preferences
from app.voice import (
    PIPELINE_TAG,
    VoiceError,
    VoiceService,
    VoiceSettings,
    digest,
    is_digest,
    load_settings,
)

READY = VoiceSettings(api_key="test-key", voice="voice-x", resource="seed-tts-2.0")


class DigestTests(unittest.TestCase):
    def test_digest_matches_the_page_implementation(self):
        # Computed with the page's crypto.subtle variant:
        # SHA-256 of JSON.stringify([text, voice, resource, pipelineTag]).
        expected = "b69752457e7c5c60bc80e52ed05fa7e3db066788c1daecfc43b45b2089b79346"
        self.assertEqual(digest("测试语音内容", READY), expected)
        self.assertEqual(PIPELINE_TAG, "v3-sse-mp3-rate25")

    def test_digest_changes_with_voice_and_resource(self):
        base = digest("hello", READY)
        self.assertNotEqual(base, digest("hello", VoiceSettings("k", "other", "seed-tts-2.0")))
        self.assertNotEqual(base, digest("hello", VoiceSettings("k", "voice-x", "other")))
        self.assertNotEqual(base, digest("hello!", READY))

    def test_key_shape(self):
        self.assertTrue(is_digest(digest("hello", READY)))
        for bad in ("", "abc", "../" + "a" * 61, "Z" * 64):
            self.assertFalse(is_digest(bad))


class SettingsTests(unittest.TestCase):
    def test_env_file_values_are_read(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / "voiceapi.env"
            env.write_text(
                "VOLC_API_KEY=test-key\nVOLC_VOICE_ID=voice-x\nX-Api-Resource-Id=seed-tts-2.0\n",
                encoding="utf-8",
            )
            settings = load_settings(env)
            self.assertTrue(settings.ready)
            self.assertEqual(settings.voice, "voice-x")
            self.assertEqual(settings.resource, "seed-tts-2.0")

    def test_missing_file_is_not_ready(self):
        settings = load_settings(Path("Z:/does-not-exist/voiceapi.env"))
        self.assertFalse(settings.ready or settings.api_key)
        self.assertEqual(settings.public()["configured"], False)

    def test_partial_configuration_is_not_ready(self):
        settings = VoiceSettings(api_key="k", voice="", resource="r")
        self.assertFalse(settings.ready)


class ServiceTests(unittest.TestCase):
    def service(self, directory, settings=READY, enabled=True):
        return VoiceService(Path(directory), settings, enabled=enabled)

    def test_unconfigured_service_refuses_to_synthesize(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory, VoiceSettings("", "", ""))
            self.assertFalse(service.available)
            with self.assertRaises(VoiceError) as caught:
                service.synthesize("hello")
            self.assertEqual(caught.exception.code, "voice_not_configured")

    def test_disabled_service_refuses_even_when_configured(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory, enabled=False)
            self.assertFalse(service.available)

    def test_empty_and_oversized_text(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            with self.assertRaises(VoiceError) as caught:
                service.synthesize("   ")
            self.assertEqual(caught.exception.code, "voice_text_empty")
            with self.assertRaises(VoiceError) as caught:
                service.synthesize("x" * 12001)
            self.assertEqual(caught.exception.code, "voice_text_too_long")

    def test_cached_audio_is_reused_without_the_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            with mock.patch.object(VoiceService, "_download", return_value=b"ID3-fake-audio"):
                key, path = service.synthesize("hello")
            self.assertTrue(path.is_file())
            with mock.patch.object(VoiceService, "_download", side_effect=AssertionError("provider called")):
                second_key, second_path = service.synthesize("hello")
            self.assertEqual((key, path), (second_key, second_path))
            self.assertEqual(service.status([key]), {"cached": {key: True}})

    def test_status_ignores_invalid_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            self.assertEqual(service.status(["../etc/passwd", "x" * 64]), {"cached": {}})

    def test_path_for_rejects_unsafe_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            with self.assertRaises(VoiceError):
                service.path_for("../../secret.mp3")

    def test_synthesis_writes_inside_the_cache_only(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            with mock.patch.object(VoiceService, "_download", return_value=b"ID3-fake-audio"):
                key, path = service.synthesize("hello")
            self.assertEqual(path.parent.resolve(), Path(directory).resolve())
            self.assertEqual(path.name, f"{key}.mp3")

    def test_provider_failure_is_reported_with_a_code(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            with mock.patch.object(VoiceService, "_download", side_effect=VoiceError("voice_provider_error", "boom")):
                with self.assertRaises(VoiceError) as caught:
                    service.synthesize("hello")
            self.assertEqual(caught.exception.code, "voice_provider_error")

    def test_cache_trim_keeps_recent_files(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            path = Path(directory)
            for index in range(3):
                (path / f"{index:064x}.mp3").write_bytes(b"x" * 10)
            service._trim(0)
            # Well under the soft limit: nothing is removed.
            self.assertEqual(len(list(path.glob("*.mp3"))), 3)


class PreferencesTests(unittest.TestCase):
    def test_defaults_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Preferences(Path(directory) / "preferences.json")
            self.assertTrue(store.snapshot()["voiceEnabled"])
            updated = store.update({"voiceEnabled": False})
            self.assertFalse(updated["voiceEnabled"])
            self.assertFalse(store.snapshot()["voiceEnabled"])

    def test_unknown_and_mistyped_values_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Preferences(Path(directory) / "preferences.json")
            store.update({"voiceEnabled": "yes", "unknown": 1})
            self.assertTrue(store.snapshot()["voiceEnabled"])

    def test_corrupt_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertTrue(Preferences(path).snapshot()["voiceEnabled"])


if __name__ == "__main__":
    unittest.main()
