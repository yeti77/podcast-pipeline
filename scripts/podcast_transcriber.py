#!/usr/bin/env python3
"""
podcast_transcriber.py — Phase 2：转写层（只负责音频获取 + Whisper，脱水由 OpenClaw 会话生成）
"""

import sys
import os
import json
import hashlib
import importlib.util
import platform
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Optional
from pipeline_paths import get_pipeline_paths
from policy_config import load_policy_config

_RUNTIME_PATHS = get_pipeline_paths()
PIPELINE_DIR = str(_RUNTIME_PATHS.pipeline_dir)
CONFIG_DIR = str(_RUNTIME_PATHS.config_dir)
OUTPUT_DIR = str(_RUNTIME_PATHS.outputs_dir)

# ── Whisper 配置（从 policy.yaml 读取）─────────────────────────────────
_WHISPER_CFG = None

TRANSCRIPTION_METADATA_SCHEMA_VERSION = 1
ALLOWED_TRANSCRIPTION_BACKENDS = {"auto", "mlx", "openai"}


class TranscriptionCliError(RuntimeError):
    exit_code = 4
    status = "transcription_error"


class CliInputError(TranscriptionCliError):
    exit_code = 2
    status = "input_error"


class EnvironmentCheckError(TranscriptionCliError):
    exit_code = 3
    status = "environment_error"


class OutputWriteError(TranscriptionCliError):
    exit_code = 5
    status = "output_error"


@dataclass(frozen=True)
class TranscriptionRequest:
    audio_path: Path
    output_dir: Path
    language: str
    backend: str
    model: str
    fallback_model: str = "large-v3-turbo"
    force: bool = False


@dataclass(frozen=True)
class BackendResult:
    backend: str
    model: str
    text: str
    segments: list
    language: str


def validate_local_audio_path(value: object) -> Path:
    raw_value = str(value or "").strip()
    if raw_value.lower().startswith(("http://", "https://")):
        raise CliInputError("--audio must be a local audio file, not a URL")
    if not raw_value:
        raise CliInputError("--audio must name an existing local audio file")
    path = Path(raw_value).expanduser().resolve()
    if not path.is_file():
        raise CliInputError(f"local audio file not found or not a regular file: {path}")
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def build_transcription_request(
    *,
    audio: object,
    output_dir: object,
    language: Optional[str],
    backend: Optional[str],
    model: Optional[str],
    force: bool,
    policy: dict,
) -> TranscriptionRequest:
    audio_path = validate_local_audio_path(audio)
    output_value = str(output_dir or "").strip()
    if not output_value:
        raise CliInputError("--output-dir is required for transcription")
    resolved_output = Path(output_value).expanduser().resolve()

    selected_backend = str(backend or policy.get("whisper_backend") or "auto").strip().lower()
    if selected_backend not in ALLOWED_TRANSCRIPTION_BACKENDS:
        raise CliInputError(
            f"unsupported backend {selected_backend!r}; expected auto, mlx, or openai"
        )
    selected_language = str(language or "auto").strip() or "auto"
    fallback_model = str(
        policy.get("whisper_fallback_model") or "large-v3-turbo"
    ).strip()
    policy_model = fallback_model if selected_backend == "openai" else policy.get("whisper_model")
    selected_model = str(model or policy_model or "large-v3-turbo").strip()
    if not selected_model:
        raise CliInputError("a Whisper model name is required")

    return TranscriptionRequest(
        audio_path=audio_path,
        output_dir=resolved_output,
        language=selected_language,
        backend=selected_backend,
        model=selected_model,
        fallback_model=fallback_model,
        force=bool(force),
    )


def probe_transcription_capabilities(
    *,
    which=shutil.which,
    find_spec=importlib.util.find_spec,
    system=platform.system,
    machine=platform.machine,
) -> dict:
    return {
        "platform_system": str(system() or ""),
        "platform_machine": str(machine() or ""),
        "ffmpeg": bool(which("ffmpeg")),
        "ffprobe": bool(which("ffprobe")),
        "mlx_whisper": find_spec("mlx_whisper") is not None,
        "whisper": find_spec("whisper") is not None,
    }


def select_backend(requested_backend: str, capabilities: dict) -> str:
    backend = str(requested_backend or "auto").strip().lower()
    if backend not in ALLOWED_TRANSCRIPTION_BACKENDS:
        raise CliInputError(f"unsupported backend: {backend}")
    apple_silicon = (
        str(capabilities.get("platform_system", "")).lower() == "darwin"
        and str(capabilities.get("platform_machine", "")).lower() in {"arm64", "aarch64"}
    )

    if backend == "mlx":
        if not apple_silicon:
            raise EnvironmentCheckError("mlx_whisper requires compatible Apple Silicon")
        if not capabilities.get("mlx_whisper"):
            raise EnvironmentCheckError("mlx_whisper is not installed")
        return "mlx"
    if backend == "openai":
        if not capabilities.get("whisper"):
            raise EnvironmentCheckError("openai-whisper is not installed")
        return "openai"

    if apple_silicon and capabilities.get("mlx_whisper"):
        return "mlx"
    if capabilities.get("whisper"):
        return "openai"
    raise EnvironmentCheckError("no supported local Whisper backend is installed")


def validate_transcription_environment(capabilities: dict, selected_backend: str) -> None:
    for command in ("ffmpeg", "ffprobe"):
        if not capabilities.get(command):
            raise EnvironmentCheckError(f"required media tool is unavailable: {command}")
    if selected_backend == "mlx" and not capabilities.get("mlx_whisper"):
        raise EnvironmentCheckError("mlx_whisper is not installed")
    if selected_backend == "openai" and not capabilities.get("whisper"):
        raise EnvironmentCheckError("openai-whisper is not installed")


def build_check_result(policy: dict, capabilities: dict) -> dict:
    requested_backend = str(policy.get("whisper_backend") or "auto")
    selected_backend = select_backend(requested_backend, capabilities)
    validate_transcription_environment(capabilities, selected_backend)
    return {
        "status": "check_ok",
        "platform": {
            "system": capabilities.get("platform_system", ""),
            "machine": capabilities.get("platform_machine", ""),
        },
        "media_tools": {
            "ffmpeg": bool(capabilities.get("ffmpeg")),
            "ffprobe": bool(capabilities.get("ffprobe")),
        },
        "backends": {
            "mlx": bool(capabilities.get("mlx_whisper")),
            "openai": bool(capabilities.get("whisper")),
        },
        "selected_backend": selected_backend,
        "models": {
            "mlx": str(policy.get("whisper_model") or "large-v3-turbo"),
            "openai": str(policy.get("whisper_fallback_model") or "large-v3-turbo"),
        },
    }


def format_timestamp(seconds: object, decimal_marker: str = ",") -> str:
    try:
        total_milliseconds = max(0, round(float(seconds) * 1000))
    except (TypeError, ValueError):
        raise ValueError(f"invalid timestamp: {seconds!r}")
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return (
        f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}"
        f"{decimal_marker}{milliseconds:03d}"
    )


def _normalized_segments(segments: object) -> list:
    if not isinstance(segments, list):
        return []
    normalized = []
    for segment in segments:
        if not isinstance(segment, Mapping):
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        try:
            start = max(0.0, float(segment.get("start", 0.0)))
            end = max(start, float(segment.get("end", start)))
        except (TypeError, ValueError):
            continue
        normalized.append({"start": start, "end": end, "text": text})
    return normalized


def segments_to_srt(segments: object) -> str:
    blocks = []
    for index, segment in enumerate(_normalized_segments(segments), start=1):
        blocks.append(
            "\n".join(
                [
                    str(index),
                    (
                        f"{format_timestamp(segment['start'], ',')} --> "
                        f"{format_timestamp(segment['end'], ',')}"
                    ),
                    segment["text"],
                ]
            )
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def segments_to_vtt(segments: object) -> str:
    blocks = []
    for segment in _normalized_segments(segments):
        blocks.append(
            "\n".join(
                [
                    (
                        f"{format_timestamp(segment['start'], '.')} --> "
                        f"{format_timestamp(segment['end'], '.')}"
                    ),
                    segment["text"],
                ]
            )
        )
    body = "\n\n".join(blocks)
    suffix = "\n" if body else ""
    return f"WEBVTT\n\n{body}{suffix}"


def normalize_backend_result(result: object) -> dict:
    if not isinstance(result, Mapping):
        raise TranscriptionCliError("Whisper backend returned an invalid result")
    segments = _normalized_segments(result.get("segments", []))
    text = str(result.get("text") or "").strip()
    if not text and segments:
        text = " ".join(segment["text"] for segment in segments).strip()
    if not text:
        raise TranscriptionCliError("Whisper backend returned an empty transcript")
    return {
        "text": text,
        "segments": segments,
        "language": str(result.get("language") or "").strip(),
    }


def run_mlx_backend(
    request: TranscriptionRequest,
    *,
    mlx_module=None,
) -> BackendResult:
    if mlx_module is None:
        import mlx_whisper as mlx_module

    model_reference = (
        request.model
        if "/" in request.model
        else f"mlx-community/whisper-{request.model.replace('.', '-')}"
    )
    raw_result = mlx_module.transcribe(
        audio=str(request.audio_path),
        path_or_hf_repo=model_reference,
        language=None if request.language == "auto" else request.language,
        verbose=False,
    )
    normalized = normalize_backend_result(raw_result)
    return BackendResult(
        backend="mlx",
        model=request.model,
        text=normalized["text"],
        segments=normalized["segments"],
        language=normalized["language"],
    )


def run_openai_backend(
    request: TranscriptionRequest,
    *,
    whisper_module=None,
) -> BackendResult:
    if whisper_module is None:
        import whisper as whisper_module

    model = whisper_module.load_model(request.model, device="cpu")
    raw_result = model.transcribe(
        str(request.audio_path),
        language=None if request.language == "auto" else request.language,
    )
    normalized = normalize_backend_result(raw_result)
    return BackendResult(
        backend="openai",
        model=request.model,
        text=normalized["text"],
        segments=normalized["segments"],
        language=normalized["language"],
    )


def run_selected_backend(
    request: TranscriptionRequest,
    selected_backend: str,
    *,
    mlx_runner=run_mlx_backend,
    openai_runner=run_openai_backend,
    openai_available: bool = False,
) -> BackendResult:
    if selected_backend == "openai":
        actual_request = request
        if request.backend == "auto":
            actual_request = replace(
                request,
                backend="openai",
                model=request.fallback_model,
            )
        try:
            return openai_runner(actual_request)
        except Exception as exc:
            raise TranscriptionCliError(f"openai backend failed: {exc}") from exc

    try:
        return mlx_runner(request)
    except Exception as mlx_exc:
        if request.backend == "auto" and openai_available:
            fallback_request = replace(
                request,
                backend="openai",
                model=request.fallback_model,
            )
            try:
                return openai_runner(fallback_request)
            except Exception as openai_exc:
                raise TranscriptionCliError(
                    f"mlx backend failed: {mlx_exc}; openai fallback failed: {openai_exc}"
                ) from openai_exc
        raise TranscriptionCliError(f"mlx backend failed: {mlx_exc}") from mlx_exc


def probe_audio_duration(
    audio_path: Path,
    *,
    run_subprocess=subprocess.run,
    timeout_seconds: int = 30,
) -> Optional[float]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    try:
        completed = run_subprocess(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if getattr(completed, "returncode", 1) != 0:
            return None
        duration = float(str(getattr(completed, "stdout", "")).strip())
        return duration if duration >= 0 else None
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return None


def build_reuse_fingerprint(
    *,
    source_sha256: str,
    backend: str,
    model: str,
    language: str,
) -> dict:
    return {
        "schema_version": TRANSCRIPTION_METADATA_SCHEMA_VERSION,
        "source_sha256": str(source_sha256),
        "backend": str(backend),
        "model": str(model),
        "language_requested": str(language),
    }


def _artifact_paths(output_dir: Path) -> dict:
    root = Path(output_dir).resolve()
    return {
        "txt": root / "transcript.txt",
        "srt": root / "transcript.srt",
        "vtt": root / "transcript.vtt",
        "metadata": root / "transcription_meta.json",
    }


def build_transcription_metadata(
    *,
    request: TranscriptionRequest,
    source_sha256: str,
    backend_result: BackendResult,
    duration_seconds: Optional[float],
    elapsed_seconds: float,
    created_at: datetime,
) -> dict:
    paths = _artifact_paths(request.output_dir)
    return {
        "status": "success",
        **build_reuse_fingerprint(
            source_sha256=source_sha256,
            backend=backend_result.backend,
            model=backend_result.model,
            language=request.language,
        ),
        "source_audio": str(request.audio_path.resolve()),
        "language_detected": backend_result.language,
        "audio_duration_seconds": duration_seconds,
        "elapsed_seconds": round(float(elapsed_seconds), 3),
        "created_at": created_at.isoformat(),
        "outputs": {
            "txt": str(paths["txt"]),
            "srt": str(paths["srt"]),
            "vtt": str(paths["vtt"]),
        },
    }


def find_reusable_result(output_dir: Path, fingerprint: dict) -> Optional[dict]:
    paths = _artifact_paths(output_dir)
    try:
        metadata = json.loads(paths["metadata"].read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(metadata, dict) or metadata.get("status") != "success":
        return None
    if any(metadata.get(key) != value for key, value in fingerprint.items()):
        return None
    for key in ("txt", "srt", "vtt"):
        try:
            if paths[key].stat().st_size <= 0:
                return None
        except OSError:
            return None
    return metadata


def publish_artifacts_atomically(
    output_dir: Path,
    *,
    text: str,
    srt: str,
    vtt: str,
    metadata: dict,
    replace=os.replace,
) -> None:
    destination = Path(output_dir).resolve()
    if destination.exists() and not destination.is_dir():
        raise OutputWriteError(f"output path is not a directory: {destination}")

    parent = destination.parent
    stage = None
    backup = None
    managed_names = (
        "transcript.txt",
        "transcript.srt",
        "transcript.vtt",
        "transcription_meta.json",
    )
    try:
        parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".transcription-stage-", dir=str(parent)))
        backup = Path(tempfile.mkdtemp(prefix=".transcription-backup-", dir=str(parent)))
        staged_values = {
            "transcript.txt": str(text),
            "transcript.srt": str(srt),
            "transcript.vtt": str(vtt),
            "transcription_meta.json": json.dumps(
                metadata,
                ensure_ascii=False,
                indent=2,
            ) + "\n",
        }
        for name, value in staged_values.items():
            (stage / name).write_text(value, encoding="utf-8")

        destination.mkdir(parents=False, exist_ok=True)
        backed_up = []
        published = []
        try:
            for name in managed_names:
                target = destination / name
                if target.exists():
                    replace(target, backup / name)
                    backed_up.append(name)
            for name in managed_names:
                replace(stage / name, destination / name)
                published.append(name)
        except Exception:
            for name in published:
                target = destination / name
                if target.exists():
                    target.unlink()
            for name in backed_up:
                saved = backup / name
                if saved.exists():
                    os.replace(saved, destination / name)
            raise
    except Exception as exc:
        raise OutputWriteError(f"failed to publish transcription artifacts: {exc}") from exc
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)


def transcribe_local_audio(
    request: TranscriptionRequest,
    *,
    capabilities: Optional[dict] = None,
    backend_runner=run_selected_backend,
    duration_probe=probe_audio_duration,
    now=None,
    monotonic=time.monotonic,
) -> dict:
    effective_capabilities = capabilities or probe_transcription_capabilities()
    selected_backend = select_backend(request.backend, effective_capabilities)
    validate_transcription_environment(effective_capabilities, selected_backend)
    selected_model = (
        request.fallback_model
        if selected_backend == "openai" and request.backend == "auto"
        else request.model
    )
    source_digest = sha256_file(request.audio_path)
    fingerprint = build_reuse_fingerprint(
        source_sha256=source_digest,
        backend=selected_backend,
        model=selected_model,
        language=request.language,
    )
    if not request.force:
        reusable = find_reusable_result(request.output_dir, fingerprint)
        if reusable is not None:
            return {
                "status": "reused",
                "metadata": reusable,
                "outputs": dict(reusable.get("outputs") or {}),
            }

    started = monotonic()
    backend_result = backend_runner(
        request,
        selected_backend,
        openai_available=bool(effective_capabilities.get("whisper")),
    )
    elapsed = max(0.0, monotonic() - started)
    duration = duration_probe(request.audio_path)
    created_at = now() if now is not None else datetime.now().astimezone()
    metadata = build_transcription_metadata(
        request=request,
        source_sha256=source_digest,
        backend_result=backend_result,
        duration_seconds=duration,
        elapsed_seconds=elapsed,
        created_at=created_at,
    )
    publish_artifacts_atomically(
        request.output_dir,
        text=backend_result.text,
        srt=segments_to_srt(backend_result.segments),
        vtt=segments_to_vtt(backend_result.segments),
        metadata=metadata,
    )
    return {
        "status": "success",
        "metadata": metadata,
        "outputs": dict(metadata["outputs"]),
    }

def load_whisper_config() -> dict:
    """从合并后的 policy 配置读取 Whisper 设置（全局缓存）。"""
    global _WHISPER_CFG
    if _WHISPER_CFG is not None:
        return _WHISPER_CFG
    try:
        cfg = load_policy_config(os.path.join(CONFIG_DIR, "policy.yaml"))
        _WHISPER_CFG = cfg.get("whisper", {})
    except Exception:
        _WHISPER_CFG = {}
    return _WHISPER_CFG
