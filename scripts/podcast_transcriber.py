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
from datetime import datetime
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
