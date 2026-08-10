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

    def test_explicit_mlx_requires_compatible_apple_silicon(self):
        with self.assertRaisesRegex(transcriber.EnvironmentCheckError, "Apple Silicon"):
            transcriber.select_backend(
                "mlx",
                self.capabilities(
                    system="Linux",
                    machine="x86_64",
                    mlx_whisper=True,
                    whisper=True,
                ),
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


class FakeMlxWhisper:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def transcribe(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


class FakeOpenAIModel:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def transcribe(self, audio, language=None):
        self.calls.append({"audio": audio, "language": language})
        return self.result


class FakeOpenAIWhisper:
    def __init__(self, result):
        self.model = FakeOpenAIModel(result)
        self.load_calls = []

    def load_model(self, model_name, device="cpu"):
        self.load_calls.append({"model_name": model_name, "device": device})
        return self.model


class TestWhisperBackendsAndFormatting(unittest.TestCase):
    SEGMENTS = [
        {"start": 0.0, "end": 1.25, "text": " First line. "},
        {"start": 61.5, "end": 63.0, "text": "Second line."},
    ]

    def test_segments_render_as_srt_and_vtt(self):
        srt = transcriber.segments_to_srt(self.SEGMENTS)
        vtt = transcriber.segments_to_vtt(self.SEGMENTS)

        self.assertIn("1\n00:00:00,000 --> 00:00:01,250\nFirst line.", srt)
        self.assertIn("00:01:01,500 --> 00:01:03,000\nSecond line.", srt)
        self.assertTrue(vtt.startswith("WEBVTT\n\n"))
        self.assertIn("00:01:01.500 --> 00:01:03.000\nSecond line.", vtt)

    def test_malformed_segments_are_ignored_and_negative_time_is_clamped(self):
        segments = [
            {"start": -3, "end": 1, "text": "Valid after clamp"},
            {"start": "bad", "end": 2, "text": "Bad time"},
            {"start": 2, "end": 3, "text": ""},
            "not-a-mapping",
        ]

        srt = transcriber.segments_to_srt(segments)

        self.assertIn("00:00:00,000 --> 00:00:01,000", srt)
        self.assertIn("Valid after clamp", srt)
        self.assertNotIn("Bad time", srt)
        self.assertNotIn("not-a-mapping", srt)

    def test_mlx_adapter_uses_local_path_model_and_auto_language(self):
        request = self.request(backend="mlx", language="auto", model="large-v3-turbo")
        fake = FakeMlxWhisper(self.backend_payload(language="en"))

        result = transcriber.run_mlx_backend(request, mlx_module=fake)

        self.assertEqual(result.backend, "mlx")
        self.assertEqual(result.model, "large-v3-turbo")
        self.assertEqual(result.text, "First line. Second line.")
        self.assertEqual(result.language, "en")
        self.assertEqual(len(fake.calls), 1)
        call = fake.calls[0]
        self.assertEqual(call["audio"], str(request.audio_path))
        self.assertEqual(
            call["path_or_hf_repo"],
            "mlx-community/whisper-large-v3-turbo",
        )
        self.assertIsNone(call["language"])
        self.assertFalse(call["verbose"])

    def test_mlx_adapter_preserves_explicit_hugging_face_repo(self):
        request = self.request(model="organization/custom-whisper")
        fake = FakeMlxWhisper(self.backend_payload())

        transcriber.run_mlx_backend(request, mlx_module=fake)

        self.assertEqual(fake.calls[0]["path_or_hf_repo"], "organization/custom-whisper")

    def test_openai_adapter_loads_cpu_model_and_passes_language(self):
        request = self.request(backend="openai", language="en", model="base")
        fake = FakeOpenAIWhisper(self.backend_payload(language="en"))

        result = transcriber.run_openai_backend(request, whisper_module=fake)

        self.assertEqual(result.backend, "openai")
        self.assertEqual(result.model, "base")
        self.assertEqual(fake.load_calls, [{"model_name": "base", "device": "cpu"}])
        self.assertEqual(
            fake.model.calls,
            [{"audio": str(request.audio_path), "language": "en"}],
        )

    def test_normalization_reconstructs_text_from_segments_and_rejects_empty_output(self):
        normalized = transcriber.normalize_backend_result(
            {"text": "", "segments": self.SEGMENTS, "language": "en"}
        )
        self.assertEqual(normalized["text"], "First line. Second line.")

        with self.assertRaisesRegex(transcriber.TranscriptionCliError, "empty transcript"):
            transcriber.normalize_backend_result({"text": "", "segments": []})

    def test_auto_mlx_failure_falls_back_to_openai_with_fallback_model(self):
        request = self.request(
            backend="auto",
            model="large-v3-turbo",
            fallback_model="base",
        )
        calls = []

        def fail_mlx(actual_request):
            calls.append(("mlx", actual_request.model))
            raise RuntimeError("simulated mlx failure")

        def pass_openai(actual_request):
            calls.append(("openai", actual_request.model))
            return transcriber.BackendResult(
                backend="openai",
                model=actual_request.model,
                text="Fallback transcript.",
                segments=[],
                language="en",
            )

        result = transcriber.run_selected_backend(
            request,
            "mlx",
            mlx_runner=fail_mlx,
            openai_runner=pass_openai,
            openai_available=True,
        )

        self.assertEqual(result.backend, "openai")
        self.assertEqual(calls, [("mlx", "large-v3-turbo"), ("openai", "base")])

    def test_explicit_mlx_failure_does_not_fallback(self):
        request = self.request(backend="mlx", fallback_model="base")
        openai_called = []

        def fail_mlx(actual_request):
            del actual_request
            raise RuntimeError("simulated mlx failure")

        def unexpected_openai(actual_request):
            openai_called.append(actual_request)
            raise AssertionError("explicit mlx must not fall back")

        with self.assertRaisesRegex(transcriber.TranscriptionCliError, "mlx backend failed"):
            transcriber.run_selected_backend(
                request,
                "mlx",
                mlx_runner=fail_mlx,
                openai_runner=unexpected_openai,
                openai_available=True,
            )

        self.assertEqual(openai_called, [])

    def request(
        self,
        *,
        backend="mlx",
        language="auto",
        model="large-v3-turbo",
        fallback_model="large-v3-turbo",
    ):
        root = Path(tempfile.gettempdir()).resolve()
        return transcriber.TranscriptionRequest(
            audio_path=root / "fixture-audio.mp3",
            output_dir=root / "fixture-transcript",
            language=language,
            backend=backend,
            model=model,
            fallback_model=fallback_model,
            force=False,
        )

    def backend_payload(self, *, language="en"):
        return {
            "text": " First line. Second line. ",
            "segments": list(self.SEGMENTS),
            "language": language,
        }


if __name__ == "__main__":
    unittest.main(verbosity=2)
