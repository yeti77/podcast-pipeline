#!/usr/bin/env python3
"""
podcast_transcriber.py — Phase 2：转写层（只负责音频获取 + Whisper，脱水由 OpenClaw 会话生成）
"""

import sys
import os
import re
import json
import glob
import hashlib
import importlib.util
import platform
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Optional
from pipeline_paths import get_pipeline_paths
from policy_config import load_policy_config

sys.path.insert(0, os.path.dirname(__file__))
try:
    import audio_resolver
except ImportError:
    audio_resolver = None

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

    if backend == "mlx":
        if not capabilities.get("mlx_whisper"):
            raise EnvironmentCheckError("mlx_whisper is not installed")
        return "mlx"
    if backend == "openai":
        if not capabilities.get("whisper"):
            raise EnvironmentCheckError("openai-whisper is not installed")
        return "openai"

    apple_silicon = (
        str(capabilities.get("platform_system", "")).lower() == "darwin"
        and str(capabilities.get("platform_machine", "")).lower() in {"arm64", "aarch64"}
    )
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


def run_whisper(audio_path: str, output_base: str, language: str = "auto") -> dict:
    """
    Whisper large-v3-turbo 转写。backend/model 从 policy.yaml 读取。
    mlx-whisper 失败时自动 fallback 到 openai-whisper CPU。
    返回 {'txt': path, 'srt': path, 'vtt': path}
    """
    import time
    cfg = load_whisper_config()

    result = {
        'txt': f"{output_base}.txt",
        'srt': f"{output_base}.srt",
        'vtt': f"{output_base}.vtt",
    }

    if os.path.exists(result['txt']) and os.path.exists(result['srt']):
        log(f"[Whisper] output exists, skipping: {output_base}")
        return result

    primary_backend  = cfg.get("whisper_backend", "mlx")
    primary_model    = cfg.get("whisper_model", "large-v3-turbo")
    fallback_backend = cfg.get("whisper_fallback_backend", "openai")
    fallback_model  = cfg.get("whisper_fallback_model", "large-v3-turbo")
    lang_code = None if language == "auto" else language
    audio_dur = _audio_duration(audio_path)

    t0 = time.time()
    actual_backend = primary_backend
    actual_model   = primary_model

    # ── Backend 1: mlx-whisper ───────────────────────────────────────
    mlx_exc = None
    try:
        import mlx_whisper as mw
        hf_repo = f"mlx-community/whisper-{primary_model.replace('.', '-')}"
        log(f"[Whisper] trying mlx-whisper (MPS) | model={hf_repo}...")
        mlx_result = mw.transcribe(
            audio=audio_path,
            path_or_hf_repo=hf_repo,
            language=lang_code,
            verbose=False,
        )
        transcribe_time = time.time() - t0
        log(f"[Whisper] mlx done in {transcribe_time:.1f}s "
            f"({audio_dur/max(transcribe_time,0.1):.1f}x realtime)")

        os.makedirs(os.path.dirname(output_base), exist_ok=True)
        with open(result['txt'], "w", encoding="utf-8") as f:
            f.write(mlx_result.get('text', ''))
        segments = mlx_result.get('segments', [])
        with open(result['srt'], "w", encoding="utf-8") as f:
            f.write(_segments_to_srt(segments))
        with open(result['vtt'], "w", encoding="utf-8") as f:
            f.write(_segments_to_vtt(segments))

        total_time = time.time() - t0
        log(f"[Whisper] backend={actual_backend} model={primary_model} "
            f"device=mps total={total_time:.1f}s fallback=False")

    except Exception as e:
        mlx_exc = str(e)
        actual_backend = fallback_backend
        actual_model   = fallback_model
        log(f"[Whisper] mlx-whisper failed ({mlx_exc[:80]}), fallback to openai-whisper CPU")

        # ── Backend 2: openai-whisper (CPU) ─────────────────────────
        try:
            import torch, whisper
            t_load = time.time()
            model = whisper.load_model(fallback_model, device="cpu")
            load_time = time.time() - t_load

            t_tr = time.time()
            audio = whisper.load_audio(audio_path)
            result_api = model.transcribe(audio, language=lang_code)
            transcribe_time = time.time() - t_tr
            log(f"[Whisper] openai done in {transcribe_time:.1f}s "
                f"({audio_dur/max(transcribe_time,0.1):.1f}x realtime)")

            os.makedirs(os.path.dirname(output_base), exist_ok=True)
            with open(result['txt'], "w", encoding="utf-8") as f:
                f.write(result_api.get("text", ""))
            segments = result_api.get('segments', [])
            with open(result['srt'], "w", encoding="utf-8") as f:
                f.write(_segments_to_srt(segments))
            with open(result['vtt'], "w", encoding="utf-8") as f:
                f.write(_segments_to_vtt(segments))

            total_time = time.time() - t0
            log(f"[Whisper] backend={actual_backend} model={fallback_model} "
                f"device=cpu total={total_time:.1f}s load={load_time:.1f}s "
                f"transcribe={transcribe_time:.1f}s fallback=True "
                f"fallback_reason={mlx_exc[:60]}")

        except Exception as e2:
            log(f"[Whisper] fallback openai-whisper also failed: {e2}")
            return result

    return result
