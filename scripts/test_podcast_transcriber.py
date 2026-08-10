#!/usr/bin/env python3
"""Hermetic tests for the optional local-audio Whisper CLI.

These tests must not import real Whisper packages, process real audio, fetch
RSS, call OpenClaw or Feishu, or write production runtime directories.
"""

from pathlib import Path
import tempfile
import unittest
from unittest import mock

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


class TestTranscriptionCapabilities(unittest.TestCase):
    def test_probe_reports_tools_packages_and_platform_without_importing_backends(self):
        looked_up_commands = []
        looked_up_modules = []

        def fake_which(command):
            looked_up_commands.append(command)
            return f"/fixture/{command}" if command in {"ffmpeg", "ffprobe"} else None

        def fake_find_spec(module_name):
            looked_up_modules.append(module_name)
            return object() if module_name == "mlx_whisper" else None

        with mock.patch("builtins.__import__", wraps=__import__) as import_spy:
            capabilities = transcriber.probe_transcription_capabilities(
                which=fake_which,
                find_spec=fake_find_spec,
                system=lambda: "Darwin",
                machine=lambda: "arm64",
            )

        self.assertEqual(looked_up_commands, ["ffmpeg", "ffprobe"])
        self.assertEqual(looked_up_modules, ["mlx_whisper", "whisper"])
        self.assertNotIn("mlx_whisper", [call.args[0] for call in import_spy.mock_calls if call.args])
        self.assertNotIn("whisper", [call.args[0] for call in import_spy.mock_calls if call.args])
        self.assertTrue(capabilities["ffmpeg"])
        self.assertTrue(capabilities["ffprobe"])
        self.assertTrue(capabilities["mlx_whisper"])
        self.assertFalse(capabilities["whisper"])
        self.assertEqual(capabilities["platform_system"], "Darwin")
        self.assertEqual(capabilities["platform_machine"], "arm64")

    def test_auto_selects_mlx_on_apple_silicon(self):
        capabilities = self.capabilities(mlx_whisper=True, whisper=True)
        self.assertEqual(transcriber.select_backend("auto", capabilities), "mlx")

    def test_auto_selects_openai_when_mlx_is_not_compatible_or_available(self):
        cases = [
            self.capabilities(system="Linux", machine="x86_64", mlx_whisper=True, whisper=True),
            self.capabilities(mlx_whisper=False, whisper=True),
        ]
        for capabilities in cases:
            with self.subTest(capabilities=capabilities):
                self.assertEqual(transcriber.select_backend("auto", capabilities), "openai")

    def test_auto_rejects_environment_without_supported_backend(self):
        with self.assertRaisesRegex(transcriber.EnvironmentCheckError, "Whisper backend"):
            transcriber.select_backend(
                "auto",
                self.capabilities(mlx_whisper=False, whisper=False),
            )

    def test_explicit_backend_never_silently_switches(self):
        with self.assertRaisesRegex(transcriber.EnvironmentCheckError, "mlx_whisper"):
            transcriber.select_backend(
                "mlx",
                self.capabilities(mlx_whisper=False, whisper=True),
            )
        with self.assertRaisesRegex(transcriber.EnvironmentCheckError, "openai-whisper"):
            transcriber.select_backend(
                "openai",
                self.capabilities(mlx_whisper=True, whisper=False),
            )

    def test_environment_validation_requires_ffmpeg_and_ffprobe(self):
        for missing in ("ffmpeg", "ffprobe"):
            capabilities = self.capabilities()
            capabilities[missing] = False
            with self.subTest(missing=missing):
                with self.assertRaisesRegex(transcriber.EnvironmentCheckError, missing):
                    transcriber.validate_transcription_environment(capabilities, "mlx")

    def test_check_result_is_machine_readable_and_does_not_create_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            untouched = Path(tmp) / "must-not-exist"
            result = transcriber.build_check_result(
                {
                    "whisper_backend": "auto",
                    "whisper_model": "large-v3-turbo",
                    "whisper_fallback_model": "base",
                },
                self.capabilities(),
            )

            self.assertEqual(result["status"], "check_ok")
            self.assertEqual(result["selected_backend"], "mlx")
            self.assertEqual(result["models"]["mlx"], "large-v3-turbo")
            self.assertEqual(result["models"]["openai"], "base")
            self.assertFalse(untouched.exists())

    @staticmethod
    def capabilities(
        *,
        system="Darwin",
        machine="arm64",
        ffmpeg=True,
        ffprobe=True,
        mlx_whisper=True,
        whisper=True,
    ):
        return {
            "platform_system": system,
            "platform_machine": machine,
            "ffmpeg": ffmpeg,
            "ffprobe": ffprobe,
            "mlx_whisper": mlx_whisper,
            "whisper": whisper,
        }


if __name__ == "__main__":
    unittest.main(verbosity=2)
