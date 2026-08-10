#!/usr/bin/env python3
"""Hermetic tests for the show-notes translation cache store."""

import json
import tempfile
import unittest
from pathlib import Path

from show_notes_translation_cache import (
    TRANSLATION_CACHE_VERSION,
    build_show_notes_translation_cache_key,
    compute_show_notes_source_hash,
    normalize_show_notes_for_cache,
    read_show_notes_translation_cache,
    write_show_notes_translation_cache,
)


class TestShowNotesTranslationCache(unittest.TestCase):
    def test_normalize_and_hash_show_notes_text(self):
        self.assertEqual(normalize_show_notes_for_cache(None), "")
        self.assertEqual(normalize_show_notes_for_cache({"raw": "dict"}), "")
        self.assertEqual(normalize_show_notes_for_cache("  Hello\n\nworld\t中文  "), "Hello world 中文")

        text = "Visit https://example.com/a?b=1\n中文 notes with emoji 🚀"
        same_text = " Visit   https://example.com/a?b=1 中文 notes with emoji 🚀 "
        changed_text = text + " updated"

        self.assertEqual(
            compute_show_notes_source_hash(text),
            compute_show_notes_source_hash(same_text),
        )
        self.assertNotEqual(
            compute_show_notes_source_hash(text),
            compute_show_notes_source_hash(changed_text),
        )

    def test_cache_key_is_stable_and_changes_with_inputs(self):
        base = {
            "podcast_id": "hardfork",
            "episode_id": "episode-1",
            "episode_url": "https://example.com/episode-1",
            "show_notes_text": "English show notes about AI.",
            "translation_version": TRANSLATION_CACHE_VERSION,
            "model_name": "mock-model",
        }

        key = build_show_notes_translation_cache_key(**base)
        self.assertEqual(key, build_show_notes_translation_cache_key(**base))

        for field, value in (
            ("podcast_id", "decoder"),
            ("episode_id", "episode-2"),
            ("episode_url", "https://example.com/episode-2"),
            ("show_notes_text", "Changed show notes about AI."),
            ("translation_version", "show_notes_zh_v2"),
            ("model_name", "mock-model-2"),
        ):
            changed = dict(base)
            changed[field] = value
            self.assertNotEqual(key, build_show_notes_translation_cache_key(**changed))

    def test_translation_cache_version_tracks_display_filter_version(self):
        self.assertEqual(
            TRANSLATION_CACHE_VERSION,
            "show_notes_zh_v2_display_filter_v2_completeness_v2",
        )

    def test_write_and_read_roundtrip_inside_temp_root(self):
        entry = {
            "cached_at": "2026-06-24T00:00:00Z",
            "translation_version": TRANSLATION_CACHE_VERSION,
            "model": "mock-model",
            "source_hash": compute_show_notes_source_hash("show notes"),
            "status": "ok",
            "translated_text": "中文翻译",
            "chunk_count": 3,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_root = Path(tmpdir)
            cache_key = build_show_notes_translation_cache_key(
                podcast_id="hardfork",
                episode_id="episode-1",
                show_notes_text="show notes",
                model_name="mock-model",
            )

            path = write_show_notes_translation_cache(cache_root, cache_key, entry)

            self.assertTrue(path.exists())
            self.assertEqual(path.suffix, ".json")
            self.assertTrue(path.resolve().is_relative_to(cache_root.resolve()))
            self.assertEqual(read_show_notes_translation_cache(cache_root, cache_key), entry)

    def test_missing_bad_and_non_dict_cache_return_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_root = Path(tmpdir)

            self.assertIsNone(read_show_notes_translation_cache(cache_root, "missing-key"))

            bad_path = write_show_notes_translation_cache(cache_root, "bad-json", {"ok": True})
            bad_path.write_text("{not-json", encoding="utf-8")
            self.assertIsNone(read_show_notes_translation_cache(cache_root, "bad-json"))

            list_path = write_show_notes_translation_cache(cache_root, "list-json", {"ok": True})
            list_path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
            self.assertIsNone(read_show_notes_translation_cache(cache_root, "list-json"))

    def test_atomic_write_overwrites_without_tmp_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_root = Path(tmpdir)
            cache_key = "stable-key"

            first_path = write_show_notes_translation_cache(cache_root, cache_key, {"translated_text": "旧"})
            second_path = write_show_notes_translation_cache(cache_root, cache_key, {"translated_text": "新"})

            self.assertEqual(first_path, second_path)
            self.assertEqual(
                read_show_notes_translation_cache(cache_root, cache_key),
                {"translated_text": "新"},
            )
            self.assertEqual(list(cache_root.glob("*.tmp")), [])
            self.assertEqual(list(cache_root.glob(".*.tmp")), [])

    def test_path_safety_for_unsafe_cache_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_root = Path(tmpdir)
            path = write_show_notes_translation_cache(cache_root, "../bad/key", {"status": "ok"})

            self.assertTrue(path.exists())
            self.assertTrue(path.resolve().is_relative_to(cache_root.resolve()))
            self.assertFalse((cache_root.parent / "bad").exists())
            self.assertEqual(
                read_show_notes_translation_cache(cache_root, "../bad/key"),
                {"status": "ok"},
            )

    def test_unicode_content_roundtrip(self):
        show_notes = "English intro.\n中文段落。\nLink: https://example.com 🚀"
        entry = {
            "cached_at": "2026-06-24T00:00:00Z",
            "translation_version": TRANSLATION_CACHE_VERSION,
            "model": "mock-model",
            "source_hash": compute_show_notes_source_hash(show_notes),
            "status": "ok",
            "translated_text": "这是一段中文翻译，保留链接 https://example.com 和 emoji 🚀",
            "chunk_count": 1,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_key = build_show_notes_translation_cache_key(
                podcast_id="mixed",
                episode_id="unicode",
                show_notes_text=show_notes,
                model_name="mock-model",
            )

            write_show_notes_translation_cache(Path(tmpdir), cache_key, entry)

            self.assertEqual(read_show_notes_translation_cache(Path(tmpdir), cache_key), entry)


if __name__ == "__main__":
    unittest.main(verbosity=2)
