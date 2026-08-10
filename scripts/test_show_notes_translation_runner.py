#!/usr/bin/env python3
"""Hermetic tests for show-notes translation runner helpers."""

import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from show_notes_translation_runner import (
    MOCK_SHOW_NOTES_TRANSLATION_MODEL,
    append_missing_source_urls_to_translation,
    build_original_resource_fallback,
    build_show_notes_translation_prompt,
    extract_urls_for_translation_preservation,
    find_untranslated_source_lines,
    mock_translate_show_notes_chunk,
    translate_show_notes_chunks_with_runner,
)


class TestShowNotesTranslationRunner(unittest.TestCase):
    def test_prompt_includes_translation_rules_and_chunk(self):
        chunk = "Read more at https://example.com.\n- Keep the grid stable."

        prompt = build_show_notes_translation_prompt(chunk)

        self.assertIn("翻译成中文", prompt)
        self.assertIn("保留 URL", prompt)
        self.assertIn("保留时间戳", prompt)
        self.assertIn("保留项目符号", prompt)
        self.assertIn("不要总结", prompt)
        self.assertIn("不要删减", prompt)
        self.assertIn("章节标题", prompt)
        self.assertIn("链接标题", prompt)
        self.assertIn("资源标题", prompt)
        self.assertIn("更正和免责声明", prompt)
        self.assertIn("文章、书籍、报告和链接标题", prompt)
        self.assertIn("只保留出版方原名", prompt)
        self.assertIn(chunk, prompt)

    def test_mock_translation_is_deterministic(self):
        first = mock_translate_show_notes_chunk("Hello")
        second = mock_translate_show_notes_chunk("Hello")

        self.assertEqual(first, second)
        self.assertIn("【中文翻译/mock】", first)
        self.assertIn("Hello", first)

    def test_multiple_chunks_are_translated_and_joined(self):
        chunks = ["Paragraph one.", "Paragraph two with https://example.com."]

        result = translate_show_notes_chunks_with_runner(
            chunks,
            translate_chunk=mock_translate_show_notes_chunk,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["chunk_count"], 2)
        self.assertEqual(result["translated_chunk_count"], 2)
        self.assertEqual(result["model"], MOCK_SHOW_NOTES_TRANSLATION_MODEL)
        self.assertEqual(result["target_language"], "zh")
        self.assertEqual(result["errors"], [])
        self.assertIn("Paragraph one.", result["translated_text"])
        self.assertIn("Paragraph two with https://example.com.", result["translated_text"])
        self.assertIn("\n\n", result["translated_text"])

    def test_empty_chunks_are_skipped_without_error(self):
        result = translate_show_notes_chunks_with_runner(
            [],
            translate_chunk=mock_translate_show_notes_chunk,
        )

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["chunk_count"], 0)
        self.assertEqual(result["translated_chunk_count"], 0)
        self.assertEqual(result["translated_text"], "")
        self.assertEqual(result["errors"], [])

    def test_all_chunks_failed_are_captured_without_raising(self):
        def failing_runner(chunk, target_language="zh"):
            raise RuntimeError("boom")

        result = translate_show_notes_chunks_with_runner(
            ["A", "B"],
            translate_chunk=failing_runner,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["chunk_count"], 2)
        self.assertEqual(result["translated_chunk_count"], 0)
        self.assertEqual(result["translated_text"], "")
        self.assertEqual(len(result["errors"]), 2)
        self.assertEqual(result["errors"][0]["chunk_index"], 0)
        self.assertIn("boom", result["errors"][0]["error"])

    def test_partial_failure_keeps_successful_chunks_and_records_error(self):
        def partly_failing_runner(chunk, target_language="zh"):
            if chunk == "B":
                raise RuntimeError("boom")
            return f"translated:{chunk}"

        result = translate_show_notes_chunks_with_runner(
            ["A", "B"],
            translate_chunk=partly_failing_runner,
        )

        self.assertEqual(result["status"], "partial_failed")
        self.assertEqual(result["chunk_count"], 2)
        self.assertEqual(result["translated_chunk_count"], 1)
        self.assertEqual(result["translated_text"], "translated:A")
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["chunk_index"], 1)

    def test_input_chunks_are_not_modified(self):
        chunks = ["A", "B"]
        before = list(chunks)

        translate_show_notes_chunks_with_runner(
            chunks,
            translate_chunk=mock_translate_show_notes_chunk,
        )

        self.assertEqual(chunks, before)

    def test_structure_is_preserved_by_mock_runner(self):
        chunk = (
            "(00:03:11) – Comparing human vs AI sample efficiency\n"
            "- The power grid supply crunch\n"
            "https://example.com/report"
        )

        result = translate_show_notes_chunks_with_runner(
            [chunk],
            translate_chunk=mock_translate_show_notes_chunk,
        )

        self.assertIn("(00:03:11)", result["translated_text"])
        self.assertIn("- The power grid supply crunch", result["translated_text"])
        self.assertIn("https://example.com/report", result["translated_text"])

    def test_extract_urls_for_translation_preservation_keeps_order_and_trims_punctuation(self):
        text = (
            "Read https://example.com/report. Then see http://example.com/a, "
            "visit www.example.com/newsletter) and "
            "https://example.com/path?x=1&y=2]. "
            "Duplicate https://example.com/report."
        )

        urls = extract_urls_for_translation_preservation(text)

        self.assertEqual(
            urls,
            [
                "https://example.com/report",
                "http://example.com/a",
                "www.example.com/newsletter",
                "https://example.com/path?x=1&y=2",
            ],
        )

    def test_extract_urls_for_translation_preservation_ignores_non_strings(self):
        self.assertEqual(extract_urls_for_translation_preservation(None), [])
        self.assertEqual(extract_urls_for_translation_preservation({"url": "https://example.com"}), [])

    def test_append_missing_source_urls_does_not_duplicate_existing_urls(self):
        source = "Read https://example.com/report for more."
        translation = "请阅读 https://example.com/report 了解更多。"

        result = append_missing_source_urls_to_translation(source, translation)

        self.assertEqual(result, translation)
        self.assertNotIn("原文链接", result)

    def test_append_missing_source_urls_adds_missing_url(self):
        source = "Read https://example.com/report for more."
        translation = "请阅读报告了解更多。"

        result = append_missing_source_urls_to_translation(source, translation)

        self.assertIn("请阅读报告了解更多。", result)
        self.assertIn("原文链接：", result)
        self.assertIn("- https://example.com/report", result)

    def test_append_missing_source_urls_only_adds_urls_missing_from_translation(self):
        source = "Read https://example.com/a and https://example.com/b."
        translation = "请阅读 https://example.com/a 和另一篇文章。"

        result = append_missing_source_urls_to_translation(source, translation)

        self.assertIn("https://example.com/a", result)
        self.assertEqual(result.count("https://example.com/a"), 1)
        self.assertIn("- https://example.com/b", result)
        self.assertNotIn("- https://example.com/a", result)

    def test_chunk_runner_appends_urls_dropped_by_translation_runner(self):
        def dropping_url_runner(chunk, target_language="zh"):
            del chunk, target_language
            return "这是一段译文，但故意漏掉链接。"

        result = translate_show_notes_chunks_with_runner(
            ["Read https://example.com/report"],
            translate_chunk=dropping_url_runner,
        )

        self.assertEqual(result["status"], "ok")
        self.assertIn("这是一段译文", result["translated_text"])
        self.assertIn("原文链接：", result["translated_text"])
        self.assertIn("- https://example.com/report", result["translated_text"])

    def test_finds_unchanged_timestamp_and_resource_lines(self):
        source = (
            "Chapters:\n"
            "00:00 Introduction\n"
            "03:39 How electrification clicked: the 2008 iPhone moment\n\n"
            "Additional Reading:\n"
            "The loudest warning about A.I. and jobs yet"
        )
        translation = (
            "章节：\n"
            "00:00 Introduction\n"
            "03:39 How electrification clicked: the 2008 iPhone moment\n\n"
            "延伸阅读：\n"
            "The loudest warning about A.I. and jobs yet"
        )

        unchanged = find_untranslated_source_lines(source, translation)

        self.assertEqual(
            unchanged,
            [
                "00:00 Introduction",
                "03:39 How electrification clicked: the 2008 iPhone moment",
                "The loudest warning about A.I. and jobs yet",
            ],
        )

    def test_translation_quality_check_allows_translated_lines_with_proper_nouns(self):
        source = (
            "00:00 Introduction\n"
            "03:39 How electrification clicked: the 2008 iPhone moment\n"
            "Nvidia opens a new autonomous driving platform"
        )
        translation = (
            "00:00 引言\n"
            "03:39 电气化如何迎来转折：2008 年的 iPhone 时刻\n"
            "Nvidia 推出新的自动驾驶平台"
        )

        self.assertEqual(find_untranslated_source_lines(source, translation), [])

    def test_w31_timestamp_proper_names_do_not_fail_completeness_check(self):
        source = (
            "The episode covers Codex and OpenAI research.\n"
            "(00:46:31) Codex\n"
            "(00:50:20) Alec Radford\n"
            "(2:34:06) – Robert E. Lee\n"
            "(2:53:40) – Why the final battle changed the war"
        )
        translation = (
            "本期节目讨论 Codex 与 OpenAI 的研究。\n"
            "(00:46:31) Codex\n"
            "(00:50:20) Alec Radford\n"
            "(2:34:06) – Robert E. Lee\n"
            "(2:53:40) – Why the final battle changed the war"
        )

        self.assertEqual(
            find_untranslated_source_lines(source, translation),
            ["(2:53:40) – Why the final battle changed the war"],
        )

    def test_single_entity_timestamp_chunk_allows_codex_but_not_introduction(self):
        source = "(00:00:00) Introduction\n(00:46:31) Codex"

        self.assertEqual(
            find_untranslated_source_lines(source, source),
            ["(00:00:00) Introduction"],
        )

    def test_incomplete_translation_is_retried_once_and_corrected(self):
        source = "Chapters:\n00:00 Introduction\n03:39 How electrification clicked"
        responses = iter(
            [
                source,
                "章节：\n00:00 引言\n03:39 电气化如何迎来转折",
            ]
        )
        calls = []

        def runner(chunk, target_language="zh"):
            calls.append((chunk, target_language))
            return next(responses)

        result = translate_show_notes_chunks_with_runner(
            [source],
            translate_chunk=runner,
            validate_translation_completeness=True,
            max_translation_attempts=2,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["translated_chunk_count"], 1)
        self.assertEqual(len(calls), 2)
        self.assertIn("00:00 引言", result["translated_text"])
        self.assertEqual(result["errors"], [])

    def test_two_incomplete_attempts_fail_without_accepting_chunk(self):
        source = "Chapters:\n00:00 Introduction\n03:39 How electrification clicked"
        calls = []

        def runner(chunk, target_language="zh"):
            calls.append((chunk, target_language))
            return source

        result = translate_show_notes_chunks_with_runner(
            [source],
            translate_chunk=runner,
            validate_translation_completeness=True,
            max_translation_attempts=2,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["translated_chunk_count"], 0)
        self.assertEqual(result["translated_text"], "")
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["errors"][0]["type"], "incomplete_translation")
        self.assertIn("00:00 Introduction", result["errors"][0]["unchanged_source_lines"])

    def test_build_original_resource_fallback_removes_english_heading(self):
        source = (
            "Links :\n\n"
            "Article One | The Verge\n\n"
            "Article Two | Bloomberg"
        )

        result = build_original_resource_fallback(source)

        self.assertEqual(
            result,
            "延伸阅读（原文）：\n"
            "Article One | The Verge\n\n"
            "Article Two | Bloomberg",
        )

    def test_resource_only_incompleteness_keeps_translated_body(self):
        body = "This episode discusses institutional patterns and AI policy."
        resources = (
            "Links:\n\n"
            "The Artificial State | The New Yorker\n\n"
            "Sam Altman | The Joe Rogan Experience"
        )
        calls = []

        def runner(chunk, target_language="zh"):
            calls.append((chunk, target_language))
            if chunk == body:
                return "本期节目讨论制度模式与人工智能政策。"
            return resources

        result = translate_show_notes_chunks_with_runner(
            [body, resources],
            translate_chunk=runner,
            validate_translation_completeness=True,
            max_translation_attempts=2,
        )

        self.assertEqual(result["status"], "partial_ok")
        self.assertEqual(result["translated_chunk_count"], 1)
        self.assertEqual(result["localized_fallback_chunk_indices"], [1])
        self.assertIn("本期节目讨论制度模式", result["translated_text"])
        self.assertIn("延伸阅读（原文）：", result["translated_text"])
        self.assertNotIn("\nLinks:", result["translated_text"])
        self.assertIn("The Artificial State | The New Yorker", result["translated_text"])
        self.assertEqual([chunk for chunk, _ in calls].count(resources), 2)
        self.assertEqual(result["errors"][0]["type"], "incomplete_translation")
        self.assertTrue(result["errors"][0]["localized_fallback"])

    def test_resource_fallback_without_any_translated_chunk_is_not_partial_ok(self):
        resources = "Links:\n\nThe Artificial State | The New Yorker"

        result = translate_show_notes_chunks_with_runner(
            [resources],
            translate_chunk=lambda chunk, target_language="zh": chunk,
            validate_translation_completeness=True,
            max_translation_attempts=2,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["translated_chunk_count"], 0)
        self.assertEqual(result["localized_fallback_chunk_indices"], [0])

    def test_body_incompleteness_remains_failed_without_local_fallback(self):
        body = "This body paragraph remains completely untranslated after both attempts."

        result = translate_show_notes_chunks_with_runner(
            [body],
            translate_chunk=lambda chunk, target_language="zh": chunk,
            validate_translation_completeness=True,
            max_translation_attempts=2,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["translated_text"], "")
        self.assertEqual(result["localized_fallback_chunk_indices"], [])

    def test_runner_has_no_file_or_subprocess_side_effects(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            before = set(os.listdir(tmpdir))
            with patch.dict(os.environ, {"SHOW_NOTES_TRANSLATION_TEST": "1"}), \
                 patch.object(subprocess, "run", side_effect=AssertionError("subprocess forbidden")):
                first = translate_show_notes_chunks_with_runner(
                    ["A"],
                    translate_chunk=mock_translate_show_notes_chunk,
                )
                second = translate_show_notes_chunks_with_runner(
                    ["A"],
                    translate_chunk=mock_translate_show_notes_chunk,
                )
            after = set(os.listdir(tmpdir))

        self.assertEqual(first, second)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
