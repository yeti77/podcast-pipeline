#!/usr/bin/env python3
"""Hermetic tests for the optional local-audio Whisper CLI.

These tests must not import real Whisper packages, process real audio, fetch
RSS, call OpenClaw or Feishu, or write production runtime directories.
"""

from pathlib import Path
from datetime import datetime, timezone
import io
import json
import os
import subprocess
import sys
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

    def test_check_reports_portable_auto_backend_instead_of_policy_preference(self):
        result = transcriber.build_check_result(
            {
                "whisper_backend": "mlx",
                "whisper_model": "large-v3-turbo",
                "whisper_fallback_model": "base",
            },
            self.capabilities(
                system="Linux",
                machine="x86_64",
                mlx_whisper=False,
                whisper=True,
            ),
        )

        self.assertEqual(result["status"], "check_ok")
        self.assertEqual(result["selected_backend"], "openai")

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


class TestTranscriptionArtifacts(unittest.TestCase):
    def test_duration_probe_uses_ffprobe_argument_list(self):
        calls = []

        def fake_run(command, capture_output, text, timeout):
            calls.append(
                {
                    "command": command,
                    "capture_output": capture_output,
                    "text": text,
                    "timeout": timeout,
                }
            )
            return subprocess.CompletedProcess(command, 0, stdout="123.456\n", stderr="")

        duration = transcriber.probe_audio_duration(
            Path("/fixture/episode.mp3"),
            run_subprocess=fake_run,
        )

        self.assertEqual(duration, 123.456)
        self.assertEqual(calls[0]["command"][0], "ffprobe")
        self.assertEqual(calls[0]["command"][-1], "/fixture/episode.mp3")
        self.assertTrue(calls[0]["capture_output"])
        self.assertTrue(calls[0]["text"])

    def test_duration_probe_returns_none_for_failed_or_invalid_probe(self):
        failures = [
            subprocess.CompletedProcess([], 1, stdout="", stderr="failed"),
            subprocess.CompletedProcess([], 0, stdout="not-a-number", stderr=""),
        ]
        for completed in failures:
            with self.subTest(completed=completed):
                self.assertIsNone(
                    transcriber.probe_audio_duration(
                        Path("/fixture/episode.mp3"),
                        run_subprocess=lambda *args, **kwargs: completed,
                    )
                )

    def test_metadata_contains_stable_schema_and_absolute_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = self.request(Path(tmp))
            result = self.backend_result()
            created_at = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)

            metadata = transcriber.build_transcription_metadata(
                request=request,
                source_sha256="abc123",
                backend_result=result,
                duration_seconds=123.456,
                elapsed_seconds=4.25,
                created_at=created_at,
            )

            self.assertEqual(metadata["schema_version"], 1)
            self.assertEqual(metadata["status"], "success")
            self.assertEqual(metadata["source_audio"], str(request.audio_path))
            self.assertEqual(metadata["source_sha256"], "abc123")
            self.assertEqual(metadata["backend"], "mlx")
            self.assertEqual(metadata["model"], "large-v3-turbo")
            self.assertEqual(metadata["language_requested"], "auto")
            self.assertEqual(metadata["language_detected"], "en")
            self.assertEqual(metadata["audio_duration_seconds"], 123.456)
            self.assertEqual(metadata["elapsed_seconds"], 4.25)
            self.assertEqual(metadata["created_at"], "2026-08-10T12:00:00+00:00")
            self.assertEqual(
                metadata["outputs"]["txt"],
                str((request.output_dir / "transcript.txt").resolve()),
            )

    def test_reuse_requires_matching_fingerprint_and_complete_nonempty_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = self.request(Path(tmp))
            output_dir = request.output_dir
            output_dir.mkdir()
            fingerprint = transcriber.build_reuse_fingerprint(
                source_sha256="abc123",
                backend="mlx",
                model="large-v3-turbo",
                language="auto",
            )
            metadata = {"status": "success", **fingerprint}
            for name in ("transcript.txt", "transcript.srt", "transcript.vtt"):
                (output_dir / name).write_text(f"content for {name}", encoding="utf-8")
            (output_dir / "transcription_meta.json").write_text(
                json.dumps(metadata), encoding="utf-8"
            )

            self.assertEqual(
                transcriber.find_reusable_result(output_dir, fingerprint)["status"],
                "success",
            )

            for changed_key, changed_value in (
                ("source_sha256", "different"),
                ("backend", "openai"),
                ("model", "base"),
                ("language_requested", "en"),
            ):
                changed = dict(fingerprint)
                changed[changed_key] = changed_value
                with self.subTest(changed_key=changed_key):
                    self.assertIsNone(
                        transcriber.find_reusable_result(output_dir, changed)
                    )

            (output_dir / "transcript.srt").write_text("", encoding="utf-8")
            self.assertIsNone(transcriber.find_reusable_result(output_dir, fingerprint))

    def test_atomic_publication_preserves_unrelated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "transcript"
            output_dir.mkdir()
            (output_dir / "keep.me").write_text("operator file", encoding="utf-8")
            metadata = {"status": "success", "schema_version": 1}

            transcriber.publish_artifacts_atomically(
                output_dir,
                text="Transcript body",
                srt="SRT body",
                vtt="VTT body",
                metadata=metadata,
            )

            self.assertEqual((output_dir / "transcript.txt").read_text(), "Transcript body")
            self.assertEqual((output_dir / "transcript.srt").read_text(), "SRT body")
            self.assertEqual((output_dir / "transcript.vtt").read_text(), "VTT body")
            self.assertEqual((output_dir / "keep.me").read_text(), "operator file")

    def test_atomic_publication_rolls_back_all_managed_files_on_replace_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "transcript"
            output_dir.mkdir()
            managed = (
                "transcript.txt",
                "transcript.srt",
                "transcript.vtt",
                "transcription_meta.json",
            )
            old_values = {}
            for name in managed:
                value = f"old-{name}"
                old_values[name] = value
                (output_dir / name).write_text(value, encoding="utf-8")

            replace_calls = []

            def failing_replace(source, destination):
                source_path = Path(source)
                destination_path = Path(destination)
                replace_calls.append((source_path, destination_path))
                if (
                    source_path.name == "transcript.srt"
                    and ".transcription-stage-" in source_path.parent.name
                ):
                    raise OSError("simulated publish failure")
                os.replace(source_path, destination_path)

            with self.assertRaisesRegex(transcriber.OutputWriteError, "publish"):
                transcriber.publish_artifacts_atomically(
                    output_dir,
                    text="new text",
                    srt="new srt",
                    vtt="new vtt",
                    metadata={"status": "success"},
                    replace=failing_replace,
                )

            self.assertTrue(replace_calls)
            for name, old_value in old_values.items():
                self.assertEqual((output_dir / name).read_text(), old_value)

    def test_orchestrator_reuses_matching_result_without_backend_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = self.request(Path(tmp))
            backend_calls = []
            capabilities = self.capabilities()

            first = transcriber.transcribe_local_audio(
                request,
                capabilities=capabilities,
                backend_runner=self.recording_backend(backend_calls),
                duration_probe=lambda path: 10.0,
                now=lambda: datetime(2026, 8, 10, tzinfo=timezone.utc),
                monotonic=self.monotonic_values(1.0, 3.0),
            )
            second = transcriber.transcribe_local_audio(
                request,
                capabilities=capabilities,
                backend_runner=self.recording_backend(backend_calls),
                duration_probe=lambda path: 10.0,
                now=lambda: datetime(2026, 8, 10, tzinfo=timezone.utc),
                monotonic=self.monotonic_values(5.0, 7.0),
            )

            self.assertEqual(first["status"], "success")
            self.assertEqual(second["status"], "reused")
            self.assertEqual(len(backend_calls), 1)

    def test_force_bypasses_reuse_and_source_change_invalidates_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = self.request(root)
            calls = []
            kwargs = {
                "capabilities": self.capabilities(),
                "backend_runner": self.recording_backend(calls),
                "duration_probe": lambda path: None,
                "now": lambda: datetime(2026, 8, 10, tzinfo=timezone.utc),
            }

            transcriber.transcribe_local_audio(
                request,
                monotonic=self.monotonic_values(1.0, 2.0),
                **kwargs,
            )
            forced = transcriber.transcribe_local_audio(
                transcriber.replace(request, force=True),
                monotonic=self.monotonic_values(3.0, 4.0),
                **kwargs,
            )
            request.audio_path.write_bytes(b"changed-audio")
            changed = transcriber.transcribe_local_audio(
                request,
                monotonic=self.monotonic_values(5.0, 6.0),
                **kwargs,
            )

            self.assertEqual(forced["status"], "success")
            self.assertEqual(changed["status"], "success")
            self.assertEqual(len(calls), 3)

    @staticmethod
    def capabilities():
        return {
            "platform_system": "Darwin",
            "platform_machine": "arm64",
            "ffmpeg": True,
            "ffprobe": True,
            "mlx_whisper": True,
            "whisper": True,
        }

    @staticmethod
    def backend_result():
        return transcriber.BackendResult(
            backend="mlx",
            model="large-v3-turbo",
            text="Transcript body",
            segments=[{"start": 0, "end": 1, "text": "Transcript body"}],
            language="en",
        )

    def request(self, root):
        audio = root / "episode.mp3"
        audio.write_bytes(b"fixture-audio")
        return transcriber.TranscriptionRequest(
            audio_path=audio.resolve(),
            output_dir=(root / "transcript").resolve(),
            language="auto",
            backend="auto",
            model="large-v3-turbo",
            fallback_model="base",
            force=False,
        )

    def recording_backend(self, calls):
        result = self.backend_result()

        def runner(request, selected_backend, openai_available=False):
            calls.append(
                {
                    "request": request,
                    "selected_backend": selected_backend,
                    "openai_available": openai_available,
                }
            )
            return result

        return runner

    @staticmethod
    def monotonic_values(*values):
        iterator = iter(values)
        return lambda: next(iterator)


class TestTranscriptionCli(unittest.TestCase):
    def test_check_mode_emits_one_json_object_and_does_not_transcribe(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        capabilities = TestTranscriptionArtifacts.capabilities()

        with mock.patch.object(
            transcriber,
            "load_whisper_config",
            return_value={
                "whisper_backend": "auto",
                "whisper_model": "large-v3-turbo",
                "whisper_fallback_model": "base",
            },
        ), mock.patch.object(
            transcriber,
            "probe_transcription_capabilities",
            return_value=capabilities,
        ), mock.patch.object(
            transcriber,
            "transcribe_local_audio",
            side_effect=AssertionError("check mode must not transcribe"),
        ):
            exit_code = transcriber.main(["--check"], stdout=stdout, stderr=stderr)

        payload = self.single_json(stdout)
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "check_ok")
        self.assertEqual(payload["selected_backend"], "mlx")
        self.assertEqual(stderr.getvalue(), "")

    def test_missing_transcription_arguments_return_json_input_error(self):
        for argv in ([], ["--audio", "/missing.mp3"]):
            with self.subTest(argv=argv):
                stdout = io.StringIO()
                stderr = io.StringIO()
                exit_code = transcriber.main(argv, stdout=stdout, stderr=stderr)
                payload = self.single_json(stdout)
                self.assertEqual(exit_code, 2)
                self.assertEqual(payload["status"], "input_error")
                self.assertEqual(payload["exit_code"], 2)
                self.assertTrue(stderr.getvalue().strip())

    def test_success_and_reused_results_are_emitted_unchanged(self):
        for status in ("success", "reused"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                audio = root / "episode.mp3"
                audio.write_bytes(b"fixture-audio")
                expected = {
                    "status": status,
                    "outputs": {"txt": str(root / "transcript.txt")},
                }
                stdout = io.StringIO()
                stderr = io.StringIO()
                with mock.patch.object(
                    transcriber,
                    "load_whisper_config",
                    return_value={"whisper_backend": "auto"},
                ), mock.patch.object(
                    transcriber,
                    "probe_transcription_capabilities",
                    return_value=TestTranscriptionArtifacts.capabilities(),
                ), mock.patch.object(
                    transcriber,
                    "transcribe_local_audio",
                    return_value=expected,
                ) as transcribe_spy:
                    exit_code = transcriber.main(
                        [
                            "--audio",
                            str(audio),
                            "--output-dir",
                            str(root / "transcript"),
                        ],
                        stdout=stdout,
                        stderr=stderr,
                    )

                self.assertEqual(exit_code, 0)
                self.assertEqual(self.single_json(stdout), expected)
                self.assertEqual(transcribe_spy.call_count, 1)
                self.assertEqual(stderr.getvalue(), "")

    def test_error_classes_map_to_stable_exit_codes_and_json(self):
        cases = [
            (transcriber.CliInputError("bad input"), 2, "input_error"),
            (transcriber.EnvironmentCheckError("missing backend"), 3, "environment_error"),
            (transcriber.TranscriptionCliError("model failed"), 4, "transcription_error"),
            (transcriber.OutputWriteError("disk failed"), 5, "output_error"),
        ]
        for error, expected_code, expected_status in cases:
            with self.subTest(error=error), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                audio = root / "episode.mp3"
                audio.write_bytes(b"fixture-audio")
                stdout = io.StringIO()
                stderr = io.StringIO()
                with mock.patch.object(
                    transcriber,
                    "load_whisper_config",
                    return_value={"whisper_backend": "auto"},
                ), mock.patch.object(
                    transcriber,
                    "probe_transcription_capabilities",
                    return_value=TestTranscriptionArtifacts.capabilities(),
                ), mock.patch.object(
                    transcriber,
                    "transcribe_local_audio",
                    side_effect=error,
                ):
                    exit_code = transcriber.main(
                        [
                            "--audio",
                            str(audio),
                            "--output-dir",
                            str(root / "transcript"),
                        ],
                        stdout=stdout,
                        stderr=stderr,
                    )

                payload = self.single_json(stdout)
                self.assertEqual(exit_code, expected_code)
                self.assertEqual(payload["status"], expected_status)
                self.assertEqual(payload["exit_code"], expected_code)
                self.assertIn(str(error), payload["error"])
                self.assertIn(str(error), stderr.getvalue())

    def test_invalid_backend_returns_input_error_without_argparse_system_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "episode.mp3"
            audio.write_bytes(b"fixture-audio")
            stdout = io.StringIO()
            stderr = io.StringIO()

            exit_code = transcriber.main(
                [
                    "--audio",
                    str(audio),
                    "--output-dir",
                    str(root / "transcript"),
                    "--backend",
                    "remote",
                ],
                stdout=stdout,
                stderr=stderr,
            )

            self.assertEqual(exit_code, 2)
            self.assertEqual(self.single_json(stdout)["status"], "input_error")

    def test_unknown_argument_returns_json_input_error(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        exit_code = transcriber.main(
            ["--unknown-option"],
            stdout=stdout,
            stderr=stderr,
        )

        payload = self.single_json(stdout)
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["status"], "input_error")
        self.assertIn("unrecognized arguments", payload["error"])

    def test_import_is_side_effect_free_in_fresh_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "must-not-exist"
            env = dict(os.environ)
            env["PODCAST_PIPELINE_HOME"] = str(root)
            env["PYTHONDONTWRITEBYTECODE"] = "1"

            completed = subprocess.run(
                [sys.executable, "-c", "import podcast_transcriber"],
                cwd=Path(__file__).resolve().parent,
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertFalse(root.exists())

    @staticmethod
    def single_json(stream):
        value = stream.getvalue()
        lines = value.splitlines()
        if len(lines) != 1:
            raise AssertionError(f"expected exactly one stdout line, got: {value!r}")
        return json.loads(lines[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
