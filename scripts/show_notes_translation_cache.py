#!/usr/bin/env python3
"""Pure helpers for caching translated show-notes display text."""

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Optional


TRANSLATION_CACHE_VERSION = "show_notes_zh_v2_display_filter_v2_completeness_v2"
DEFAULT_SHOW_NOTES_TRANSLATION_CACHE_ROOT = Path("cache/show_notes_translations")

_SAFE_CACHE_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")


def normalize_show_notes_for_cache(text: object) -> str:
    """Normalize source text so cache keys are stable across whitespace noise."""
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text).strip()


def compute_show_notes_source_hash(text: object) -> str:
    normalized = normalize_show_notes_for_cache(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalize_key_part(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def build_show_notes_translation_cache_key(
    *,
    podcast_id: str,
    episode_id: str = "",
    episode_url: str = "",
    show_notes_text: object,
    translation_version: str = TRANSLATION_CACHE_VERSION,
    model_name: str = "",
) -> str:
    payload = {
        "podcast_id": _normalize_key_part(podcast_id),
        "episode_id": _normalize_key_part(episode_id),
        "episode_url": _normalize_key_part(episode_url),
        "source_hash": compute_show_notes_source_hash(show_notes_text),
        "translation_version": _normalize_key_part(translation_version),
        "model_name": _normalize_key_part(model_name),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"show_notes_zh_{digest}"


def _cache_path(cache_root: Path, cache_key: str) -> Path:
    cache_key_text = str(cache_key)
    if _SAFE_CACHE_KEY_RE.fullmatch(cache_key_text):
        filename_stem = cache_key_text
    else:
        filename_stem = hashlib.sha256(cache_key_text.encode("utf-8")).hexdigest()
    return Path(cache_root) / f"{filename_stem}.json"


def read_show_notes_translation_cache(cache_root: Path, cache_key: str) -> Optional[dict]:
    path = _cache_path(Path(cache_root), cache_key)
    if not path.exists():
        return None

    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    return data if isinstance(data, dict) else None


def write_show_notes_translation_cache(cache_root: Path, cache_key: str, entry: dict) -> Path:
    root = Path(cache_root)
    root.mkdir(parents=True, exist_ok=True)
    final_path = _cache_path(root, cache_key)
    tmp_path = root / f".{final_path.name}.{uuid.uuid4().hex}.tmp"

    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, final_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    return final_path
