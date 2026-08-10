#!/usr/bin/env python3
"""Pure orchestration helper for display-layer show-notes translation."""

from pathlib import Path

from episode_show_notes_renderer import (
    detect_show_notes_display_language,
    should_translate_show_notes_for_display,
)
from show_notes_translation_cache import (
    TRANSLATION_CACHE_VERSION,
    build_show_notes_translation_cache_key,
    compute_show_notes_source_hash,
    read_show_notes_translation_cache,
    write_show_notes_translation_cache,
)
from show_notes_translation_chunker import split_show_notes_for_translation
from show_notes_translation_runner import (
    MOCK_SHOW_NOTES_TRANSLATION_MODEL,
    append_missing_source_urls_to_translation,
    find_untranslated_source_lines,
    mock_translate_show_notes_chunk,
    translate_show_notes_chunks_with_runner,
)


def translate_show_notes_for_display(
    *,
    podcast_id: str,
    episode_id: str = "",
    episode_url: str = "",
    show_notes_text: object,
    source_language: str = "",
    cache_root=None,
    translation_version: str = TRANSLATION_CACHE_VERSION,
    model_name: str = MOCK_SHOW_NOTES_TRANSLATION_MODEL,
    translate_chunk=mock_translate_show_notes_chunk,
    cache_enabled: bool = True,
    max_chunk_chars: int = 1800,
    validate_translation_completeness: bool = False,
    max_translation_attempts: int = 1,
    read_cache=read_show_notes_translation_cache,
    write_cache=write_show_notes_translation_cache,
) -> dict:
    detected_language = detect_show_notes_display_language(show_notes_text)
    source_lang = (source_language or detected_language or "unknown").strip().lower()
    should_translate = should_translate_show_notes_for_display(show_notes_text, source_language=source_language)
    source_hash = compute_show_notes_source_hash(show_notes_text)
    base_errors = []

    if not should_translate:
        return _result(
            status="skipped",
            translated_text="",
            source_language=detected_language,
            should_translate=False,
            cache_hit=False,
            cache_key="",
            source_hash=source_hash,
            chunk_count=0,
            translated_chunk_count=0,
            model=model_name,
            errors=[],
        )

    cache_key = build_show_notes_translation_cache_key(
        podcast_id=podcast_id,
        episode_id=episode_id,
        episode_url=episode_url,
        show_notes_text=show_notes_text,
        translation_version=translation_version,
        model_name=model_name,
    )
    root = Path(cache_root) if cache_root is not None else None
    use_cache = bool(cache_enabled and root is not None)

    if use_cache:
        try:
            cached = read_cache(root, cache_key)
        except Exception as exc:
            cached = None
            base_errors.append(_error("cache_read_failed", exc))
        if isinstance(cached, dict) and cached.get("status") == "ok" and cached.get("translated_text"):
            translated_text = append_missing_source_urls_to_translation(
                show_notes_text,
                cached.get("translated_text", ""),
            )
            unchanged_lines = (
                find_untranslated_source_lines(show_notes_text, translated_text)
                if validate_translation_completeness
                else []
            )
            if not unchanged_lines:
                return _result(
                    status="cache_hit",
                    translated_text=translated_text,
                    source_language=source_lang if source_lang else detected_language,
                    should_translate=True,
                    cache_hit=True,
                    cache_key=cache_key,
                    source_hash=source_hash,
                    chunk_count=int(cached.get("chunk_count") or 0),
                    translated_chunk_count=int(cached.get("translated_chunk_count") or cached.get("chunk_count") or 0),
                    model=str(cached.get("model") or model_name),
                    errors=base_errors,
                )
            base_errors.append(
                {
                    "type": "incomplete_cached_translation",
                    "error": "cached translation copied significant source lines unchanged",
                    "unchanged_source_lines": unchanged_lines[:8],
                }
            )
        elif _is_valid_partial_cache_entry(cached):
            translated_text = append_missing_source_urls_to_translation(
                show_notes_text,
                cached.get("translated_text", ""),
            )
            cached_errors = cached.get("errors") or []
            return _result(
                status="partial_cache_hit",
                translated_text=translated_text,
                source_language=source_lang if source_lang else detected_language,
                should_translate=True,
                cache_hit=True,
                cache_key=cache_key,
                source_hash=source_hash,
                chunk_count=int(cached.get("chunk_count") or 0),
                translated_chunk_count=int(cached.get("translated_chunk_count") or 0),
                model=str(cached.get("model") or model_name),
                errors=base_errors + list(cached_errors),
                localized_fallback_chunk_indices=cached.get(
                    "localized_fallback_chunk_indices"
                ),
            )

    chunks = split_show_notes_for_translation(show_notes_text, max_chars=max_chunk_chars)
    try:
        runner_result = translate_show_notes_chunks_with_runner(
            chunks,
            translate_chunk=translate_chunk,
            target_language="zh",
            model_name=model_name,
            validate_translation_completeness=validate_translation_completeness,
            max_translation_attempts=max_translation_attempts,
        )
    except Exception as exc:
        runner_result = {
            "status": "failed",
            "translated_text": "",
            "chunk_count": len(chunks),
            "translated_chunk_count": 0,
            "model": model_name,
            "target_language": "zh",
            "errors": [_error("runner_failed", exc)],
            "localized_fallback_chunk_indices": [],
        }

    runner_status = runner_result.get("status", "failed")
    if runner_status == "ok":
        status = "translated"
    elif runner_status == "partial_ok":
        status = "partial_translated"
    elif runner_status == "partial_failed":
        status = "partial_failed"
    elif runner_status == "skipped":
        status = "skipped"
    else:
        status = "failed"

    errors = list(base_errors)
    errors.extend(runner_result.get("errors") or [])

    localized_fallback_chunk_indices = list(
        runner_result.get("localized_fallback_chunk_indices") or []
    )
    if use_cache and status in {"translated", "partial_translated"}:
        cache_entry = {
            "status": "partial_ok" if status == "partial_translated" else "ok",
            "translated_text": runner_result.get("translated_text", ""),
            "source_hash": source_hash,
            "translation_version": translation_version,
            "model": model_name,
            "chunk_count": runner_result.get("chunk_count", len(chunks)),
            "translated_chunk_count": runner_result.get("translated_chunk_count", 0),
            "target_language": "zh",
        }
        if status == "partial_translated":
            cache_entry["localized_fallback_chunk_indices"] = (
                localized_fallback_chunk_indices
            )
            cache_entry["errors"] = list(runner_result.get("errors") or [])
        try:
            write_cache(root, cache_key, cache_entry)
        except Exception as exc:
            errors.append(_error("cache_write_failed", exc))

    return _result(
        status=status,
        translated_text=str(runner_result.get("translated_text") or ""),
        source_language=source_lang if source_lang else detected_language,
        should_translate=True,
        cache_hit=False,
        cache_key=cache_key,
        source_hash=source_hash,
        chunk_count=int(runner_result.get("chunk_count") or len(chunks)),
        translated_chunk_count=int(runner_result.get("translated_chunk_count") or 0),
        model=str(runner_result.get("model") or model_name),
        errors=errors,
        localized_fallback_chunk_indices=localized_fallback_chunk_indices,
    )


def _result(
    *,
    status,
    translated_text,
    source_language,
    should_translate,
    cache_hit,
    cache_key,
    source_hash,
    chunk_count,
    translated_chunk_count,
    model,
    errors,
    localized_fallback_chunk_indices=None,
):
    return {
        "status": status,
        "translated_text": translated_text,
        "source_language": source_language or "unknown",
        "target_language": "zh",
        "should_translate": bool(should_translate),
        "cache_hit": bool(cache_hit),
        "cache_key": cache_key,
        "source_hash": source_hash,
        "chunk_count": chunk_count,
        "translated_chunk_count": translated_chunk_count,
        "model": model,
        "errors": errors,
        "localized_fallback_chunk_indices": list(
            localized_fallback_chunk_indices or []
        ),
    }


def _is_valid_partial_cache_entry(cached):
    if not isinstance(cached, dict):
        return False
    if cached.get("status") != "partial_ok" or not cached.get("translated_text"):
        return False

    chunk_count = cached.get("chunk_count")
    translated_chunk_count = cached.get("translated_chunk_count")
    indices = cached.get("localized_fallback_chunk_indices")
    errors = cached.get("errors")
    if not isinstance(chunk_count, int) or chunk_count <= 0:
        return False
    if (
        not isinstance(translated_chunk_count, int)
        or isinstance(translated_chunk_count, bool)
        or not 0 < translated_chunk_count < chunk_count
    ):
        return False
    if not isinstance(indices, list) or not indices:
        return False
    if not all(
        isinstance(index, int) and not isinstance(index, bool) and 0 <= index < chunk_count
        for index in indices
    ):
        return False
    if len(set(indices)) != len(indices):
        return False
    if not isinstance(errors, list) or not errors:
        return False
    if not all(
        isinstance(error, dict)
        and error.get("localized_fallback") is True
        and error.get("chunk_index") in indices
        for error in errors
    ):
        return False
    if {error.get("chunk_index") for error in errors} != set(indices):
        return False
    return "延伸阅读（原文）：" in str(cached.get("translated_text") or "")


def _error(error_type, exc):
    return {
        "type": error_type,
        "error": f"{type(exc).__name__}: {exc}",
    }
