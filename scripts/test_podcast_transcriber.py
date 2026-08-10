#!/usr/bin/env python3
"""Hermetic tests for the optional local-audio Whisper CLI.

These tests must not import real Whisper packages, process real audio, fetch
RSS, call OpenClaw or Feishu, or write production runtime directories.
"""

from pathlib import Path
import tempfile
import unittest

import podcast_transcriber as transcriber


class TestTranscriptionRequest(unittest.TestCase):
    def test_validate_local_audio_path_accepts_existing_regular_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "episode.mp3"
            audio.write_bytes(b"fixture-audio")

            self.assertEqual(transcriber.validate_local_audio_path(audio), audio.resolve())

    def test_validate_local_audio_path_rejects_http_and_https(self):
        for value in ("http://example.test/a.mp3", "https://example.test/a.mp3"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(transcriber.CliInputError, "local audio file"):
                    transcriber.validate_local_audio_path(value)

    def test_validate_local_audio_path_rejects_missing_and_directory_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(transcriber.CliInputError):
                transcriber.validate_local_audio_path(Path(tmp) / "missing.mp3")
            with self.assertRaises(transcriber.CliInputError):
                transcriber.validate_local_audio_path(tmp)

    def test_source_sha256_changes_when_audio_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "episode.mp3"
            audio.write_bytes(b"version-one")
            first = transcriber.sha256_file(audio)
            audio.write_bytes(b"version-two")

            self.assertNotEqual(first, transcriber.sha256_file(audio))

    def test_request_cli_values_override_whisper_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "episode.mp3"
            audio.write_bytes(b"fixture-audio")
            policy = {
                "whisper_backend": "mlx",
                "whisper_model": "policy-model",
                "whisper_language_mode": "auto",
            }

            request = transcriber.build_transcription_request(
                audio=audio,
                output_dir=root / "transcript",
                language="en",
                backend="openai",
                model="cli-model",
                force=True,
                policy=policy,
            )

            self.assertEqual(request.audio_path, audio.resolve())
            self.assertEqual(request.output_dir, (root / "transcript").resolve())
            self.assertEqual(request.language, "en")
            self.assertEqual(request.backend, "openai")
            self.assertEqual(request.model, "cli-model")
            self.assertTrue(request.force)

    def test_request_uses_policy_defaults_without_creating_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "episode.mp3"
            output_dir = root / "not-created-yet"
            audio.write_bytes(b"fixture-audio")

            request = transcriber.build_transcription_request(
                audio=audio,
                output_dir=output_dir,
                language=None,
                backend=None,
                model=None,
                force=False,
                policy={
                    "whisper_backend": "mlx",
                    "whisper_model": "large-v3-turbo",
                    "whisper_language_mode": "explicit",
                },
            )

            self.assertEqual(request.language, "auto")
            self.assertEqual(request.backend, "mlx")
            self.assertEqual(request.model, "large-v3-turbo")
            self.assertFalse(output_dir.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
