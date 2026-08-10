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
import subprocess
from dataclasses import dataclass
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
    force: bool = False


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
    selected_model = str(model or policy.get("whisper_model") or "large-v3-turbo").strip()
    if not selected_model:
        raise CliInputError("a Whisper model name is required")

    return TranscriptionRequest(
        audio_path=audio_path,
        output_dir=resolved_output,
        language=selected_language,
        backend=selected_backend,
        model=selected_model,
        force=bool(force),
    )

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
