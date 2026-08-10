#!/usr/bin/env python3
"""Hermetic tests for show-notes translation orchestration helpers."""

import tempfile
import unittest
from pathlib import Path

from episode_show_notes_renderer import filter_show_notes_boilerplate_for_display
from show_notes_translation_cache import (
    TRANSLATION_CACHE_VERSION,
    build_show_notes_translation_cache_key,
    compute_show_notes_source_hash,
    write_show_notes_translation_cache,
)
from show_notes_translation_orchestrator import translate_show_notes_for_display
from show_notes_translation_runner import (
    MOCK_SHOW_NOTES_TRANSLATION_MODEL,
    mock_translate_show_notes_chunk,
)


LONG_ENGLISH_SHOW_NOTES = (
    "Today we talk with an energy technology founder about AI data centers, "
    "electricity demand, grid bottlenecks, batteries, transmission, and the "
    "future of clean energy infrastructure. The conversation covers markets, "
    "policy, capital allocation, and long-term industrial strategy."
)

URL_PRESERVATION_ENGLISH_CONTEXT = (
    "The episode discusses AI data centers, electricity demand, grid bottlenecks, "
    "battery storage, transmission planning, market design, and the long-term "
    "infrastructure choices shaping clean energy systems."
)


class CountingRunner:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = set(fail_on or [])

    def __call__(self, chunk, target_language="zh"):
        self.calls.append((chunk, target_language))
        if len(self.calls) - 1 in self.fail_on:
            raise RuntimeError("boom")
        return mock_translate_show_notes_chunk(chunk, target_language=target_language)


def _write_ok_translation_cache(
    cache_root,
    *,
    podcast_id="hardfork",
    episode_id="episode-1",
    episode_url="",
    show_notes_text,
    translated_text,
    chunk_count=1,
):
    cache_key = build_show_notes_translation_cache_key(
        podcast_id=podcast_id,
        episode_id=episode_id,
        episode_url=episode_url,
        show_notes_text=show_notes_text,
        translation_version=TRANSLATION_CACHE_VERSION,
        model_name=MOCK_SHOW_NOTES_TRANSLATION_MODEL,
    )
    write_show_notes_translation_cache(
        cache_root,
        cache_key,
        {
            "status": "ok",
            "translated_text": translated_text,
            "source_hash": compute_show_notes_source_hash(show_notes_text),
            "translation_version": TRANSLATION_CACHE_VERSION,
            "model": MOCK_SHOW_NOTES_TRANSLATION_MODEL,
            "chunk_count": chunk_count,
            "translated_chunk_count": chunk_count,
        },
    )
    return cache_key


class TestShowNotesTranslationOrchestrator(unittest.TestCase):
    def test_w28_ali_siddiq_short_english_source_reaches_runner(self):
        calls = []
        text = (
            'Ali Siddiq is a comedian, author, and public speaker. His new special, '
            '"My Father," is now streaming on YouTube. See him live on the "Custom Fit" Tour.\n'
            'https://youtu.be/XiSewRUOVyg\nwww.youtube.com/@AliSiddiqComedy\nwww.alisiddiq.com'
        )

        def runner(chunk, target_language="zh"):
            calls.append(chunk)
            return "Ali Siddiq 是喜剧演员、作家和公共演说家。"

        result = translate_show_notes_for_display(
            podcast_id="jre",
            episode_id="ali-siddiq",
            show_notes_text=text,
            source_language="en",
            cache_enabled=False,
            translate_chunk=runner,
        )

        self.assertEqual(result["status"], "translated")
        self.assertEqual(len(calls), 1)
        self.assertIn("Ali Siddiq", calls[0])

    def test_skips_empty_and_non_string_without_runner_or_cache_write(self):
        for value in (None, {}, ""):
            with tempfile.TemporaryDirectory() as tmpdir:
                runner = CountingRunner()

                result = translate_show_notes_for_display(
                    podcast_id="hardfork",
                    episode_id="empty",
                    show_notes_text=value,
                    cache_root=Path(tmpdir),
                    translate_chunk=runner,
                )

                self.assertEqual(result["status"], "skipped")
                self.assertFalse(result["should_translate"])
                self.assertEqual(result["translated_text"], "")
                self.assertFalse(result["cache_hit"])
                self.assertEqual(runner.calls, [])
                self.assertEqual(list(Path(tmpdir).glob("*.json")), [])

    def test_skips_chinese_text(self):
        runner = CountingRunner()
        text = "本期节目讨论人工智能、电力系统、能源基础设施和长期产业变化。"

        result = translate_show_notes_for_display(
            podcast_id="zhpod",
            episode_id="zh-1",
            show_notes_text=text,
            translate_chunk=runner,
        )

        self.assertEqual(result["status"], "skipped")
        self.assertFalse(result["should_translate"])
        self.assertEqual(result["source_language"], "zh")
        self.assertEqual(runner.calls, [])

    def test_english_cache_miss_translates_and_writes_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = CountingRunner()

            result = translate_show_notes_for_display(
                podcast_id="hardfork",
                episode_id="episode-1",
                show_notes_text=LONG_ENGLISH_SHOW_NOTES,
                cache_root=Path(tmpdir),
                translate_chunk=runner,
                max_chunk_chars=120,
            )

            self.assertEqual(result["status"], "translated")
            self.assertTrue(result["should_translate"])
            self.assertFalse(result["cache_hit"])
            self.assertIn("【中文翻译/mock】", result["translated_text"])
            self.assertGreater(result["chunk_count"], 0)
            self.assertEqual(result["chunk_count"], result["translated_chunk_count"])
            self.assertEqual(len(runner.calls), result["chunk_count"])
            self.assertEqual(len(list(Path(tmpdir).glob("*.json"))), 1)

    def test_english_cache_hit_does_not_call_runner(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_root = Path(tmpdir)
            _write_ok_translation_cache(
                cache_root,
                podcast_id="hardfork",
                episode_id="episode-1",
                show_notes_text=LONG_ENGLISH_SHOW_NOTES,
                translated_text="缓存中文翻译",
            )
            runner = CountingRunner()

            result = translate_show_notes_for_display(
                podcast_id="hardfork",
                episode_id="episode-1",
                show_notes_text=LONG_ENGLISH_SHOW_NOTES,
                cache_root=cache_root,
                translate_chunk=runner,
                validate_translation_completeness=True,
                max_translation_attempts=2,
            )

            self.assertEqual(result["status"], "cache_hit")
            self.assertTrue(result["cache_hit"])
            self.assertEqual(result["translated_text"], "缓存中文翻译")
            self.assertEqual(runner.calls, [])

    def test_incomplete_volts_cache_is_bypassed_and_refreshed(self):
        show_notes = (
            "Most energy decisions are made in state legislatures, where new lawmakers "
            "evaluate rapidly changing technology and billion-dollar infrastructure budgets.\n\n"
            "Chapters:\n\n"
            "00:00 Introduction\n\n"
            "03:39 How electrification clicked: the 2008 iPhone moment"
        )
        incomplete_cached_translation = (
            "大多数能源决策由州立法机构作出。\n\n"
            "Chapters:\n\n"
            "00:00 Introduction\n\n"
            "03:39 How electrification clicked: the 2008 iPhone moment"
        )
        calls = []

        def corrected_runner(chunk, target_language="zh"):
            calls.append((chunk, target_language))
            if chunk.startswith("Chapters:"):
                return "章节：\n\n00:00 引言\n\n03:39 电气化如何迎来转折：2008 年的 iPhone 时刻"
            return "大多数能源决策由州立法机构作出，新议员需要评估快速变化的技术和基础设施预算。"

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_root = Path(tmpdir)
            _write_ok_translation_cache(
                cache_root,
                podcast_id="volts",
                episode_id="electrification",
                show_notes_text=show_notes,
                translated_text=incomplete_cached_translation,
            )

            result = translate_show_notes_for_display(
                podcast_id="volts",
                episode_id="electrification",
                show_notes_text=show_notes,
                source_language="en",
                cache_root=cache_root,
                translate_chunk=corrected_runner,
                validate_translation_completeness=True,
                max_translation_attempts=2,
            )

        self.assertEqual(result["status"], "translated")
        self.assertFalse(result["cache_hit"])
        self.assertEqual(len(calls), 2)
        self.assertIn("00:00 引言", result["translated_text"])
        self.assertNotIn("00:00 Introduction", result["translated_text"])
        self.assertTrue(
            any(error.get("type") == "incomplete_cached_translation" for error in result["errors"])
        )

    def test_cache_hit_appends_source_url_missing_from_cached_translation(self):
        show_notes = (
            "Read the full report at https://example.com/report.\n\n"
            f"{URL_PRESERVATION_ENGLISH_CONTEXT}"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_root = Path(tmpdir)
            _write_ok_translation_cache(
                cache_root,
                show_notes_text=show_notes,
                translated_text="本期节目讨论 AI 数据中心和电力需求。",
            )
            runner = CountingRunner()

            result = translate_show_notes_for_display(
                podcast_id="hardfork",
                episode_id="episode-1",
                show_notes_text=show_notes,
                cache_root=cache_root,
                translate_chunk=runner,
            )

        self.assertEqual(result["status"], "cache_hit")
        self.assertTrue(result["cache_hit"])
        self.assertIn("https://example.com/report", result["translated_text"])
        self.assertIn("原文链接：", result["translated_text"])
        self.assertEqual(runner.calls, [])

    def test_cache_hit_does_not_duplicate_url_already_in_cached_translation(self):
        show_notes = (
            "Read the full report at https://example.com/report.\n\n"
            f"{URL_PRESERVATION_ENGLISH_CONTEXT}"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_root = Path(tmpdir)
            _write_ok_translation_cache(
                cache_root,
                show_notes_text=show_notes,
                translated_text="请阅读 https://example.com/report。",
            )
            runner = CountingRunner()

            result = translate_show_notes_for_display(
                podcast_id="hardfork",
                episode_id="episode-1",
                show_notes_text=show_notes,
                cache_root=cache_root,
                translate_chunk=runner,
            )

        self.assertEqual(result["status"], "cache_hit")
        self.assertNotIn("原文链接：", result["translated_text"])
        self.assertEqual(result["translated_text"].count("https://example.com/report"), 1)
        self.assertEqual(runner.calls, [])

    def test_cache_hit_appends_only_missing_urls(self):
        show_notes = (
            "Read https://example.com/a and https://example.com/b for the full context. "
            f"{URL_PRESERVATION_ENGLISH_CONTEXT}"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_root = Path(tmpdir)
            _write_ok_translation_cache(
                cache_root,
                show_notes_text=show_notes,
                translated_text="请先阅读 https://example.com/a。",
            )
            runner = CountingRunner()

            result = translate_show_notes_for_display(
                podcast_id="hardfork",
                episode_id="episode-1",
                show_notes_text=show_notes,
                cache_root=cache_root,
                translate_chunk=runner,
            )

        self.assertEqual(result["status"], "cache_hit")
        self.assertEqual(result["translated_text"].count("https://example.com/a"), 1)
        self.assertIn("- https://example.com/b", result["translated_text"])
        self.assertNotIn("- https://example.com/a", result["translated_text"])
        self.assertEqual(runner.calls, [])

    def test_cache_hit_uses_filtered_display_text_for_url_preservation(self):
        raw_show_notes = """
Today we discuss AI data centers and electricity demand.
Read the full report at https://example.com/report.
The conversation covers grid planning, battery storage, transmission constraints, and clean energy markets.

This episode is brought to you by FischTank PR and EnergyHub.
Learn more about how EnergyHub supports distributed energy resources at https://sponsor.example.com/deal.
"""
        filtered_show_notes = filter_show_notes_boilerplate_for_display(raw_show_notes)
        self.assertIn("https://example.com/report", filtered_show_notes)
        self.assertNotIn("https://sponsor.example.com/deal", filtered_show_notes)

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_root = Path(tmpdir)
            _write_ok_translation_cache(
                cache_root,
                show_notes_text=filtered_show_notes,
                translated_text="本期节目讨论 AI 数据中心和电力需求。",
            )
            runner = CountingRunner()

            result = translate_show_notes_for_display(
                podcast_id="hardfork",
                episode_id="episode-1",
                show_notes_text=filtered_show_notes,
                cache_root=cache_root,
                translate_chunk=runner,
            )

        self.assertEqual(result["status"], "cache_hit")
        self.assertIn("https://example.com/report", result["translated_text"])
        self.assertNotIn("https://sponsor.example.com/deal", result["translated_text"])
        self.assertEqual(runner.calls, [])

    def test_cache_disabled_translates_without_cache_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = CountingRunner()

            result = translate_show_notes_for_display(
                podcast_id="hardfork",
                episode_id="episode-1",
                show_notes_text=LONG_ENGLISH_SHOW_NOTES,
                cache_root=Path(tmpdir),
                cache_enabled=False,
                translate_chunk=runner,
            )

            self.assertEqual(result["status"], "translated")
            self.assertFalse(result["cache_hit"])
            self.assertGreater(len(runner.calls), 0)
            self.assertEqual(list(Path(tmpdir).glob("*.json")), [])

    def test_show_notes_change_version_and_model_invalidate_cache_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_root = Path(tmpdir)

            first = translate_show_notes_for_display(
                podcast_id="hardfork",
                episode_id="episode-1",
                show_notes_text=LONG_ENGLISH_SHOW_NOTES,
                cache_root=cache_root,
                translate_chunk=CountingRunner(),
            )
            changed_text = translate_show_notes_for_display(
                podcast_id="hardfork",
                episode_id="episode-1",
                show_notes_text=LONG_ENGLISH_SHOW_NOTES + " New sentence about chips.",
                cache_root=cache_root,
                translate_chunk=CountingRunner(),
            )
            changed_version = translate_show_notes_for_display(
                podcast_id="hardfork",
                episode_id="episode-1",
                show_notes_text=LONG_ENGLISH_SHOW_NOTES,
                cache_root=cache_root,
                translation_version="show_notes_zh_v2",
                translate_chunk=CountingRunner(),
            )
            changed_model = translate_show_notes_for_display(
                podcast_id="hardfork",
                episode_id="episode-1",
                show_notes_text=LONG_ENGLISH_SHOW_NOTES,
                cache_root=cache_root,
                model_name="mock-model-v2",
                translate_chunk=CountingRunner(),
            )

            self.assertNotEqual(first["cache_key"], changed_text["cache_key"])
            self.assertNotEqual(first["cache_key"], changed_version["cache_key"])
            self.assertNotEqual(first["cache_key"], changed_model["cache_key"])
            self.assertFalse(changed_text["cache_hit"])
            self.assertFalse(changed_version["cache_hit"])
            self.assertFalse(changed_model["cache_hit"])

    def test_runner_failure_returns_failed_and_does_not_write_ok_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = translate_show_notes_for_display(
                podcast_id="hardfork",
                episode_id="episode-1",
                show_notes_text=LONG_ENGLISH_SHOW_NOTES,
                cache_root=Path(tmpdir),
                translate_chunk=CountingRunner(fail_on={0, 1, 2, 3, 4}),
                max_chunk_chars=80,
            )

            self.assertEqual(result["status"], "failed")
            self.assertGreater(len(result["errors"]), 0)
            self.assertEqual(list(Path(tmpdir).glob("*.json")), [])

    def test_partial_failure_returns_partial_failed(self):
        result = translate_show_notes_for_display(
            podcast_id="hardfork",
            episode_id="episode-1",
            show_notes_text=LONG_ENGLISH_SHOW_NOTES,
            translate_chunk=CountingRunner(fail_on={1}),
            cache_enabled=False,
            max_chunk_chars=80,
        )

        self.assertEqual(result["status"], "partial_failed")
        self.assertGreater(len(result["errors"]), 0)
        self.assertGreater(result["translated_chunk_count"], 0)
        self.assertIn("【中文翻译/mock】", result["translated_text"])

    def test_resource_fallback_is_cached_and_reused_without_runner(self):
        show_notes = (
            "This episode discusses institutional patterns, artificial intelligence policy, "
            "and the future of public systems.\n\n"
            "Links:\n\n"
            "The Artificial State | The New Yorker\n\n"
            "Sam Altman | The Joe Rogan Experience"
        )
        calls = []

        def partial_runner(chunk, target_language="zh"):
            calls.append((chunk, target_language))
            if chunk.startswith("Links:"):
                return chunk
            return "本期节目讨论制度模式、人工智能政策与公共系统的未来。"

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_root = Path(tmpdir)
            first = translate_show_notes_for_display(
                podcast_id="decoder",
                episode_id="artificial-state",
                show_notes_text=show_notes,
                source_language="en",
                cache_root=cache_root,
                translate_chunk=partial_runner,
                validate_translation_completeness=True,
                max_translation_attempts=2,
            )

            def forbidden_runner(chunk, target_language="zh"):
                raise AssertionError("partial cache hit must not call runner")

            second = translate_show_notes_for_display(
                podcast_id="decoder",
                episode_id="artificial-state",
                show_notes_text=show_notes,
                source_language="en",
                cache_root=cache_root,
                translate_chunk=forbidden_runner,
                validate_translation_completeness=True,
                max_translation_attempts=2,
            )

            self.assertEqual(first["status"], "partial_translated")
            self.assertFalse(first["cache_hit"])
            self.assertEqual(first["localized_fallback_chunk_indices"], [1])
            self.assertIn("本期节目讨论制度模式", first["translated_text"])
            self.assertIn("延伸阅读（原文）：", first["translated_text"])
            self.assertEqual(len(list(cache_root.glob("*.json"))), 1)
            self.assertEqual(second["status"], "partial_cache_hit")
            self.assertTrue(second["cache_hit"])
            self.assertEqual(second["translated_text"], first["translated_text"])
            self.assertEqual(second["localized_fallback_chunk_indices"], [1])
            self.assertEqual(len([chunk for chunk, _ in calls if chunk.startswith("Links:")]), 2)

    def test_invalid_partial_cache_entry_is_ignored(self):
        show_notes = (
            "This episode discusses artificial intelligence policy, institutional design, "
            "and the future of public systems.\n\n"
            "Links:\n\n"
            "The Artificial State | The New Yorker"
        )
        calls = []

        def corrected_runner(chunk, target_language="zh"):
            calls.append((chunk, target_language))
            if chunk.startswith("Links:"):
                return "延伸阅读：\n\n《人工国家》| The New Yorker"
            return "本期节目讨论人工智能政策、制度设计与公共系统的未来。"

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_root = Path(tmpdir)
            cache_key = build_show_notes_translation_cache_key(
                podcast_id="decoder",
                episode_id="invalid-partial",
                show_notes_text=show_notes,
                translation_version=TRANSLATION_CACHE_VERSION,
                model_name=MOCK_SHOW_NOTES_TRANSLATION_MODEL,
            )
            write_show_notes_translation_cache(
                cache_root,
                cache_key,
                {
                    "status": "partial_ok",
                    "translated_text": "缺少局部降级索引的缓存",
                    "source_hash": compute_show_notes_source_hash(show_notes),
                    "translation_version": TRANSLATION_CACHE_VERSION,
                    "model": MOCK_SHOW_NOTES_TRANSLATION_MODEL,
                    "chunk_count": 2,
                    "translated_chunk_count": 1,
                },
            )

            result = translate_show_notes_for_display(
                podcast_id="decoder",
                episode_id="invalid-partial",
                show_notes_text=show_notes,
                source_language="en",
                cache_root=cache_root,
                translate_chunk=corrected_runner,
                validate_translation_completeness=True,
                max_translation_attempts=2,
            )

        self.assertEqual(result["status"], "translated")
        self.assertFalse(result["cache_hit"])
        self.assertEqual(len(calls), 2)
        self.assertIn("《人工国家》", result["translated_text"])

    def test_partial_cache_with_nonlocalized_error_is_ignored(self):
        show_notes = (
            "This episode discusses artificial intelligence policy, institutional design, "
            "and the future of public systems.\n\n"
            "Links:\n\n"
            "The Artificial State | The New Yorker"
        )
        calls = []

        def corrected_runner(chunk, target_language="zh"):
            calls.append(chunk)
            if chunk.startswith("Links:"):
                return "延伸阅读：\n\n《人工国家》| The New Yorker"
            return "本期节目讨论人工智能政策、制度设计与公共系统的未来。"

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_root = Path(tmpdir)
            cache_key = build_show_notes_translation_cache_key(
                podcast_id="decoder",
                episode_id="invalid-partial-errors",
                show_notes_text=show_notes,
                translation_version=TRANSLATION_CACHE_VERSION,
                model_name=MOCK_SHOW_NOTES_TRANSLATION_MODEL,
            )
            write_show_notes_translation_cache(
                cache_root,
                cache_key,
                {
                    "status": "partial_ok",
                    "translated_text": (
                        "本期节目讨论人工智能政策。\n\n"
                        "延伸阅读（原文）：\nThe Artificial State | The New Yorker"
                    ),
                    "source_hash": compute_show_notes_source_hash(show_notes),
                    "translation_version": TRANSLATION_CACHE_VERSION,
                    "model": MOCK_SHOW_NOTES_TRANSLATION_MODEL,
                    "chunk_count": 2,
                    "translated_chunk_count": 1,
                    "localized_fallback_chunk_indices": [1],
                    "errors": [
                        {
                            "chunk_index": 0,
                            "type": "incomplete_translation",
                            "localized_fallback": False,
                        }
                    ],
                },
            )

            result = translate_show_notes_for_display(
                podcast_id="decoder",
                episode_id="invalid-partial-errors",
                show_notes_text=show_notes,
                source_language="en",
                cache_root=cache_root,
                translate_chunk=corrected_runner,
                validate_translation_completeness=True,
                max_translation_attempts=2,
            )

        self.assertEqual(result["status"], "translated")
        self.assertFalse(result["cache_hit"])
        self.assertEqual(len(calls), 2)

    def test_cache_write_failure_is_non_blocking(self):
        def failing_write(cache_root, cache_key, entry):
            raise OSError("disk full")

        result = translate_show_notes_for_display(
            podcast_id="hardfork",
            episode_id="episode-1",
            show_notes_text=LONG_ENGLISH_SHOW_NOTES,
            cache_root=Path("/tmp/not-used-by-failing-write"),
            translate_chunk=CountingRunner(),
            write_cache=failing_write,
        )

        self.assertEqual(result["status"], "translated")
        self.assertTrue(any(error["type"] == "cache_write_failed" for error in result["errors"]))
        self.assertIn("【中文翻译/mock】", result["translated_text"])

    def test_cache_read_failure_is_non_blocking(self):
        def failing_read(cache_root, cache_key):
            raise OSError("cannot read")

        result = translate_show_notes_for_display(
            podcast_id="hardfork",
            episode_id="episode-1",
            show_notes_text=LONG_ENGLISH_SHOW_NOTES,
            cache_root=Path("/tmp/not-used-by-failing-read"),
            translate_chunk=CountingRunner(),
            read_cache=failing_read,
            write_cache=lambda cache_root, cache_key, entry: None,
        )

        self.assertEqual(result["status"], "translated")
        self.assertTrue(any(error["type"] == "cache_read_failed" for error in result["errors"]))
        self.assertIn("【中文翻译/mock】", result["translated_text"])

    def test_source_language_override(self):
        english_forced_zh = translate_show_notes_for_display(
            podcast_id="hardfork",
            episode_id="episode-1",
            show_notes_text=LONG_ENGLISH_SHOW_NOTES,
            source_language="zh",
            translate_chunk=CountingRunner(),
        )
        chinese_forced_en = translate_show_notes_for_display(
            podcast_id="zhpod",
            episode_id="zh-1",
            show_notes_text="本期节目讨论人工智能和能源基础设施。",
            source_language="en",
            translate_chunk=CountingRunner(),
        )
        english_forced_en = translate_show_notes_for_display(
            podcast_id="hardfork",
            episode_id="episode-1",
            show_notes_text=LONG_ENGLISH_SHOW_NOTES,
            source_language="en",
            cache_enabled=False,
            translate_chunk=CountingRunner(),
        )

        self.assertEqual(english_forced_zh["status"], "skipped")
        self.assertEqual(chinese_forced_en["status"], "skipped")
        self.assertEqual(english_forced_en["status"], "translated")


if __name__ == "__main__":
    unittest.main(verbosity=2)
