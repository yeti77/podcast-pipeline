#!/usr/bin/env python3
"""Hermetic tests for episode show-notes display helpers."""

import os
import json
import tempfile
import unittest
from unittest.mock import patch

from episode_show_notes_renderer import (
    DEFAULT_SHOW_NOTES_CHUNK_CHARS,
    SHOW_NOTES_PLACEHOLDER,
    SHOW_NOTES_TRANSLATED_HEADING,
    analyze_translated_show_notes_residual_english,
    build_show_notes_display_filter_result,
    build_show_notes_display_result,
    build_show_notes_sections,
    clean_show_notes_display_text,
    detect_show_notes_display_language,
    diagnose_show_notes_source_completeness,
    filter_show_notes_boilerplate_for_display,
    get_episode_show_notes_text,
    should_translate_show_notes_for_display,
    split_show_notes_text,
)
from show_notes_translation_runner import mock_translate_show_notes_chunk


class TestEpisodeShowNotesRenderer(unittest.TestCase):
    def test_show_notes_text_has_priority(self):
        ep = {
            "show_notes_text": "primary show notes",
            "show_notes": "secondary show notes",
            "description": "description text",
            "summary_3_sentences_cn": ["summary sentence"],
            "one_line_summary_cn": "one line",
        }

        self.assertEqual(get_episode_show_notes_text(ep), "primary show notes")

    def test_show_notes_and_description_fallbacks(self):
        self.assertEqual(
            get_episode_show_notes_text({"show_notes": "show notes fallback"}),
            "show notes fallback",
        )
        self.assertEqual(
            get_episode_show_notes_text({"description": "description fallback"}),
            "description fallback",
        )

    def test_summary_fields_are_last_resort_fallbacks(self):
        self.assertEqual(
            get_episode_show_notes_text({"summary_3_sentences_cn": ["第一句。", "第二句。"]}),
            "第一句。\n第二句。",
        )
        self.assertEqual(
            get_episode_show_notes_text({"one_line_summary_cn": "单句概述。"}),
            "单句概述。",
        )

    def test_empty_content_returns_placeholder_section(self):
        self.assertEqual(get_episode_show_notes_text({}), "")
        self.assertEqual(build_show_notes_sections({}), [SHOW_NOTES_PLACEHOLDER])

    def test_cleaning_unescapes_entities_and_removes_tags(self):
        text = clean_show_notes_display_text("<p>AI &amp; markets</p><script>x</script>")

        self.assertIn("AI & markets", text)
        self.assertNotIn("&amp;", text)
        self.assertNotIn("<p>", text)
        self.assertNotIn("</p>", text)
        self.assertNotIn("<script>", text)
        self.assertNotIn("x", text)


    def test_residual_english_diagnostic_ignores_links_timestamps_and_names(self):
        translated = (
            "本期讨论 Ada Palmer、Webflow 与 Open Circuit 的产品策略。\n"
            "- https://example.com/report\n"
            "(00:03:11) 进入主要话题。"
        )

        result = analyze_translated_show_notes_residual_english(translated)

        self.assertFalse(result["suspected_incomplete_translation"])
        self.assertLess(result["latin_word_count"], 10)

    def test_residual_english_diagnostic_flags_untranslated_prose(self):
        translated = (
            "本期首先讨论能源需求。\n\n"
            "This section remains largely untranslated and continues with a detailed "
            "discussion of electricity markets, data centers, battery storage, grid "
            "planning, infrastructure investment, and long term energy policy choices."
        )

        result = analyze_translated_show_notes_residual_english(translated)

        self.assertTrue(result["suspected_incomplete_translation"])
        self.assertGreaterEqual(result["latin_word_count"], 20)
        self.assertGreater(result["residual_latin_ratio"], 0.5)

    def test_residual_english_diagnostic_ignores_intentional_original_resources(self):
        translated = (
            "本期正文已经完整翻译，讨论互联网中的机器人流量和内容生态。\n\n"
            "延伸阅读（原文）：\n"
            "The Gray Area with Sean Illing | Apple Podcasts\n"
            "Galaxy Brain | The Atlantic\n"
            "The internet is all bots now | The Gray Area YouTube\n"
            "The feeling of control slipping away | The Atlantic\n"
            "How bots took over our lives | The New Yorker\n"
            "The dead internet theory is real and it is killing the web | Fast Company"
        )

        result = analyze_translated_show_notes_residual_english(translated)

        self.assertFalse(result["suspected_incomplete_translation"])
        self.assertLess(result["latin_word_count"], 10)

    def test_residual_english_diagnostic_handles_empty_and_non_string_values(self):
        for value in (None, "", {"text": "raw"}, ["raw"]):
            with self.subTest(value=value):
                result = analyze_translated_show_notes_residual_english(value)
                self.assertEqual(result["latin_word_count"], 0)
                self.assertEqual(result["cjk_character_count"], 0)
                self.assertFalse(result["suspected_incomplete_translation"])

    def test_source_completeness_reports_explicit_truncation(self):
        ep = {
            "show_notes_source": "itunes_summary",
            "show_notes_text": "A complete-looking sentence.",
            "show_notes_truncated": True,
            "rss_description_len": 400,
            "rss_content_encoded_len": 0,
            "rss_itunes_summary_len": 520,
        }

        result = diagnose_show_notes_source_completeness(ep)

        self.assertTrue(result["suspected_source_truncation"])
        self.assertIn("upstream_truncated", result["reasons"])
        self.assertEqual(result["source_field"], "itunes_summary")

    def test_source_completeness_flags_long_unterminated_provider_text(self):
        source = ("This episode discusses AI infrastructure and user research. " * 9) + "real-world exam"
        self.assertGreaterEqual(len(source), 500)
        ep = {
            "show_notes_source": "itunes_summary",
            "show_notes_text": source,
            "show_notes_text_len": len(source),
            "show_notes_truncated": False,
            "rss_description_len": 525,
            "rss_content_encoded_len": 0,
            "rss_itunes_summary_len": len(source),
        }

        result = diagnose_show_notes_source_completeness(
            ep,
            filtered_text=source.replace("user research", "research"),
        )

        self.assertTrue(result["suspected_source_truncation"])
        self.assertFalse(result["terminal_punctuation_present"])
        self.assertIn("long_unterminated_source", result["reasons"])
        self.assertLess(result["filtered_length"], result["source_length"])

    def test_source_completeness_accepts_complete_and_short_unknown_sources(self):
        complete = diagnose_show_notes_source_completeness(
            {"show_notes_text": "A" * 520 + ".", "show_notes_truncated": False}
        )
        short = diagnose_show_notes_source_completeness(
            {"show_notes_text": "Brief notes", "show_notes_truncated": False}
        )

        self.assertFalse(complete["suspected_source_truncation"])
        self.assertTrue(complete["terminal_punctuation_present"])
        self.assertFalse(short["suspected_source_truncation"])
        self.assertEqual(short["candidate_lengths"]["description"], 0)

    def test_html_paragraphs_and_breaks_become_readable_newlines(self):
        text = clean_show_notes_display_text("<p>第一段</p><p>第二段<br>第三行</p>")

        self.assertIn("第一段\n第二段\n第三行", text)

    def test_raw_dict_and_list_values_do_not_leak(self):
        self.assertEqual(clean_show_notes_display_text({"title": "raw"}), "")
        self.assertEqual(clean_show_notes_display_text(["raw", "list"]), "")
        self.assertEqual(get_episode_show_notes_text({"show_notes_text": {"title": "raw"}}), "")

    def test_split_preserves_long_text_without_truncation(self):
        text = "START " + ("中段内容。" * 120) + " END"
        chunks = split_show_notes_text(text, max_chars=80)
        combined = "".join(chunks)

        self.assertIn("START", combined)
        self.assertIn("中段内容", combined)
        self.assertIn("END", combined)
        self.assertEqual(combined, text)
        self.assertTrue(all(len(chunk) <= 80 for chunk in chunks))

    def test_split_prefers_paragraph_boundaries(self):
        text = "第一段。\n\n第二段。\n\n第三段。"

        self.assertEqual(
            split_show_notes_text(text, max_chars=20),
            ["第一段。", "第二段。", "第三段。"],
        )

    def test_split_handles_single_long_word_without_dropping_content(self):
        word = "A" * (DEFAULT_SHOW_NOTES_CHUNK_CHARS + 25)
        chunks = split_show_notes_text(word)

        self.assertEqual(chunks, [word])

    def test_split_empty_text_returns_empty_list(self):
        self.assertEqual(split_show_notes_text(""), [])
        self.assertEqual(split_show_notes_text("   "), [])

    def test_chinese_long_text_is_chunked(self):
        text = "开头。" + ("这是中文长文本。" * 100) + "结尾。"
        chunks = split_show_notes_text(text, max_chars=90)
        combined = "".join(chunks)

        self.assertIn("开头。", combined)
        self.assertIn("这是中文长文本。", combined)
        self.assertIn("结尾。", combined)
        self.assertEqual(combined, text)
        self.assertTrue(all(len(chunk) <= 90 for chunk in chunks))

    def test_language_detection_helpers_are_not_used_by_section_builder(self):
        ep = {
            "show_notes_text": (
                "Today, I am talking with the CEO of a leading energy technology company. "
                "We discuss the power grid, AI data centers, electricity demand, and the "
                "future of clean energy."
            )
        }

        sections = build_show_notes_sections(ep)

        self.assertEqual(detect_show_notes_display_language(ep["show_notes_text"]), "en")
        self.assertTrue(should_translate_show_notes_for_display(ep["show_notes_text"]))
        self.assertIn("Today, I am talking", sections[0])
        self.assertNotIn("中文翻译", "\n".join(sections))

    def test_translation_is_disabled_by_default_for_english_show_notes(self):
        ep = {
            "show_notes_text": (
                "Today, I’m talking with Skydio CEO Adam Bry. We discuss drones, AI, "
                "government work, and Chinese competition."
            )
        }

        sections = build_show_notes_sections(ep)
        combined = "\n".join(sections)

        self.assertIn("Today, I’m talking with Skydio CEO Adam Bry", combined)
        self.assertNotIn(SHOW_NOTES_TRANSLATED_HEADING, combined)
        self.assertNotIn("【中文翻译/mock】", combined)

    def test_structured_display_result_reports_disabled_translation(self):
        ep = {
            "show_notes_source": "description",
            "show_notes_text": (
                "A complete English episode description about energy markets.\n\n"
                "Privacy Policy: https://example.com/privacy"
            ),
        }

        result = build_show_notes_display_result(ep)

        self.assertEqual(result["heading"], "full")
        self.assertEqual(result["translation"]["status"], "disabled")
        self.assertNotIn("translated_text", result["translation"])
        self.assertEqual(result["source_completeness"]["source_field"], "description")
        self.assertEqual(result["display_filter"]["removed_category_counts"], {"privacy": 1})
        self.assertEqual(result["display_filter"]["removed_reasons"], ["privacy_marker"])
        self.assertNotIn("Privacy Policy", json.dumps(result["display_filter"]))
        self.assertEqual(build_show_notes_sections(ep), result["sections"])

    def test_structured_display_result_reports_translation_and_residual_evidence(self):
        ep = {
            "podcast_id": "energy",
            "episode_id": "episode-1",
            "language": "en",
            "show_notes_text": (
                "This episode discusses electricity markets, grid investment, data centers, "
                "battery storage, public policy, and the future of clean energy infrastructure."
            ),
        }

        result = build_show_notes_display_result(
            ep,
            translation_enabled=True,
            translation_options={
                "cache_enabled": False,
                "translate_chunk": lambda chunk, target_language="zh": "本期讨论电力市场和能源基础设施。",
            },
        )

        self.assertEqual(result["heading"], "translated")
        self.assertEqual(result["translation"]["status"], "translated")
        self.assertTrue(result["translation"]["should_translate"])
        self.assertFalse(result["translation"]["residual_english"]["suspected_incomplete_translation"])
        self.assertNotIn("translated_text", result["translation"])
        self.assertIn("本期讨论电力市场", "\n".join(result["sections"]))
        self.assertEqual(
            build_show_notes_sections(
                ep,
                translation_enabled=True,
                translation_options={
                    "cache_enabled": False,
                    "translate_chunk": lambda chunk, target_language="zh": "本期讨论电力市场和能源基础设施。",
                },
            )[0],
            SHOW_NOTES_TRANSLATED_HEADING,
        )

    def test_structured_display_result_records_failure_and_falls_back(self):
        source = (
            "This episode discusses electricity markets, grid investment, data centers, "
            "battery storage, public policy, and the future of clean energy infrastructure."
        )

        def failing_runner(chunk, target_language="zh"):
            raise RuntimeError("fake translation failure")

        result = build_show_notes_display_result(
            {"language": "en", "show_notes_text": source},
            translation_enabled=True,
            translation_options={"cache_enabled": False, "translate_chunk": failing_runner},
        )

        self.assertEqual(result["heading"], "full")
        self.assertEqual(result["translation"]["status"], "failed")
        self.assertTrue(result["translation"]["errors"])
        self.assertIn("This episode discusses", "\n".join(result["sections"]))

    def test_structured_display_result_contains_cache_hit_metadata(self):
        ep = {
            "podcast_id": "energy",
            "episode_id": "episode-cache",
            "language": "en",
            "show_notes_text": (
                "This episode discusses electricity markets, grid investment, data centers, "
                "battery storage, public policy, and the future of clean energy infrastructure."
            ),
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            first = build_show_notes_display_result(
                ep,
                translation_enabled=True,
                translation_options={
                    "cache_root": tmpdir,
                    "translate_chunk": lambda chunk, target_language="zh": "本期讨论电力市场。",
                },
            )
            second = build_show_notes_display_result(
                ep,
                translation_enabled=True,
                translation_options={
                    "cache_root": tmpdir,
                    "translate_chunk": lambda chunk, target_language="zh": self.fail("cache miss"),
                },
            )

        self.assertEqual(first["translation"]["status"], "translated")
        self.assertEqual(second["translation"]["status"], "cache_hit")
        self.assertTrue(second["translation"]["cache_hit"])
        self.assertEqual(second["heading"], "translated")

    def test_partial_resource_fallback_and_cache_hit_keep_translated_display(self):
        ep = {
            "podcast_id": "decoder",
            "episode_id": "resource-fallback",
            "language": "en",
            "show_notes_text": (
                "This episode discusses institutional patterns, artificial intelligence "
                "policy, and the future of public systems.\n\n"
                "Links:\n\n"
                "The Artificial State | The New Yorker"
            ),
        }
        calls = []

        def partial_runner(chunk, target_language="zh"):
            calls.append(chunk)
            if chunk.startswith("Links:"):
                return chunk
            return "本期节目讨论制度模式、人工智能政策与公共系统的未来。"

        with tempfile.TemporaryDirectory() as tmpdir:
            first = build_show_notes_display_result(
                ep,
                translation_enabled=True,
                translation_options={
                    "cache_root": tmpdir,
                    "translate_chunk": partial_runner,
                    "validate_translation_completeness": True,
                    "max_translation_attempts": 2,
                },
            )
            second = build_show_notes_display_result(
                ep,
                translation_enabled=True,
                translation_options={
                    "cache_root": tmpdir,
                    "translate_chunk": lambda chunk, target_language="zh": self.fail(
                        "partial cache miss"
                    ),
                    "validate_translation_completeness": True,
                    "max_translation_attempts": 2,
                },
            )

        for result, status in (
            (first, "partial_translated"),
            (second, "partial_cache_hit"),
        ):
            with self.subTest(status=status):
                self.assertEqual(result["heading"], "translated")
                self.assertEqual(result["translation"]["status"], status)
                self.assertEqual(
                    result["translation"]["localized_fallback_chunk_indices"],
                    [1],
                )
                combined = "\n".join(result["sections"])
                self.assertIn("本期节目讨论制度模式", combined)
                self.assertIn("延伸阅读（原文）：", combined)
                self.assertIn("The Artificial State", combined)
        self.assertTrue(second["translation"]["cache_hit"])
        self.assertEqual(len([chunk for chunk in calls if chunk.startswith("Links:")]), 2)

    def test_partial_body_failure_still_falls_back_to_full_source(self):
        ep = {
            "language": "en",
            "show_notes_text": (
                "This episode discusses institutional patterns, artificial intelligence "
                "policy, and the future of public systems.\n\n"
                "Links:\n\n"
                "The Artificial State | The New Yorker"
            ),
        }

        def body_copy_runner(chunk, target_language="zh"):
            if chunk.startswith("Links:"):
                return "延伸阅读：\n\n《人工国家》| The New Yorker"
            return chunk

        result = build_show_notes_display_result(
            ep,
            translation_enabled=True,
            translation_options={
                "cache_enabled": False,
                "translate_chunk": body_copy_runner,
                "validate_translation_completeness": True,
                "max_translation_attempts": 2,
            },
        )

        self.assertEqual(result["heading"], "full")
        self.assertEqual(result["translation"]["status"], "partial_failed")
        combined = "\n".join(result["sections"])
        self.assertIn("This episode discusses institutional patterns", combined)
        self.assertNotIn("延伸阅读（原文）：", combined)

    def test_structured_display_result_survives_diagnostic_failure(self):
        ep = {"show_notes_text": "A short description."}

        with patch(
            "episode_show_notes_renderer.diagnose_show_notes_source_completeness",
            side_effect=RuntimeError("diagnostic failure"),
        ):
            result = build_show_notes_display_result(ep)

        self.assertEqual(result["heading"], "full")
        self.assertIn("A short description", "\n".join(result["sections"]))
        self.assertTrue(result["source_completeness"]["diagnostic_error"])

    def test_structured_display_result_survives_filter_diagnostic_failure(self):
        ep = {"show_notes_text": "A short description that must remain visible."}

        with patch(
            "episode_show_notes_renderer.build_show_notes_display_filter_result",
            side_effect=RuntimeError("filter diagnostic failure"),
        ):
            result = build_show_notes_display_result(ep)

        self.assertEqual(result["heading"], "full")
        self.assertIn("must remain visible", "\n".join(result["sections"]))
        self.assertEqual(result["display_filter"]["kept_category_counts"], {})
        self.assertEqual(result["display_filter"]["removed_category_counts"], {})
        self.assertIn("RuntimeError", result["display_filter"]["diagnostic_error"])

    def test_explicit_translation_enabled_uses_mock_translation_sections(self):
        ep = {
            "podcast": "Decoder with Nilay Patel",
            "title": "Skydio CEO argues more drones will make us safer",
            "show_notes_text": (
                "Today, I’m talking with Skydio CEO Adam Bry. We discuss drones, AI, "
                "government work, and Chinese competition. The conversation covers "
                "autonomous systems, public safety, defense procurement, supply chains, "
                "and how American robotics companies compete in global markets."
            ),
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            sections = build_show_notes_sections(
                ep,
                translation_enabled=True,
                translation_options={
                    "cache_root": tmpdir,
                    "cache_enabled": False,
                    "translate_chunk": mock_translate_show_notes_chunk,
                },
            )

        combined = "\n".join(sections)
        self.assertEqual(sections[0], SHOW_NOTES_TRANSLATED_HEADING)
        self.assertIn("【中文翻译/mock】", combined)
        self.assertIn("Adam Bry", combined)
        self.assertNotIn("节目介绍 / Show Notes（完整）", combined)

    def test_explicit_translation_enabled_keeps_chinese_show_notes_untranslated(self):
        text = "本期节目讨论电力市场、AI 数据中心和能源基础设施。"

        sections = build_show_notes_sections(
            {"show_notes_text": text},
            translation_enabled=True,
            translation_options={"translate_chunk": mock_translate_show_notes_chunk},
        )

        self.assertEqual(sections, [text])
        self.assertNotIn(SHOW_NOTES_TRANSLATED_HEADING, "\n".join(sections))

    def test_translation_failure_falls_back_to_original_show_notes(self):
        def failing_runner(chunk, target_language="zh"):
            raise RuntimeError("boom")

        ep = {
            "show_notes_text": (
                "Today, I’m talking with Skydio CEO Adam Bry. We discuss drones, AI, "
                "government work, and Chinese competition. The conversation covers "
                "autonomous systems, public safety, defense procurement, supply chains, "
                "and how American robotics companies compete in global markets."
            )
        }

        sections = build_show_notes_sections(
            ep,
            translation_enabled=True,
            translation_options={"cache_enabled": False, "translate_chunk": failing_runner},
        )
        combined = "\n".join(sections)

        self.assertIn("Today, I’m talking with Skydio CEO Adam Bry", combined)
        self.assertNotIn(SHOW_NOTES_TRANSLATED_HEADING, combined)

    def test_cache_hit_and_translated_show_same_translation_heading(self):
        ep = {
            "podcast": "Decoder with Nilay Patel",
            "title": "Skydio CEO argues more drones will make us safer",
            "show_notes_text": (
                "Today, I’m talking with Skydio CEO Adam Bry. We discuss drones, AI, "
                "government work, and Chinese competition. The conversation covers "
                "autonomous systems, public safety, defense procurement, supply chains, "
                "and how American robotics companies compete in global markets."
            ),
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            first = build_show_notes_sections(
                ep,
                translation_enabled=True,
                translation_options={
                    "cache_root": tmpdir,
                    "translate_chunk": mock_translate_show_notes_chunk,
                },
            )
            second = build_show_notes_sections(
                ep,
                translation_enabled=True,
                translation_options={
                    "cache_root": tmpdir,
                    "translate_chunk": lambda chunk, target_language="zh": "SHOULD NOT RUN",
                },
            )

        self.assertEqual(first[0], SHOW_NOTES_TRANSLATED_HEADING)
        self.assertEqual(second[0], SHOW_NOTES_TRANSLATED_HEADING)
        self.assertIn("【中文翻译/mock】", "\n".join(second))

    def test_translation_enabled_filters_sponsor_footer_before_translation(self):
        recorded_chunks = []

        def recording_runner(chunk, target_language="zh"):
            recorded_chunks.append(chunk)
            return mock_translate_show_notes_chunk(chunk, target_language=target_language)

        ep = {
            "show_notes_text": (
                "Today, I’m talking with Skydio CEO Adam Bry about drones, AI, "
                "government work, and Chinese competition. The conversation covers "
                "autonomous systems, public safety, defense procurement, supply chains, "
                "and how American robotics companies compete in global markets.\n\n"
                "DraftKings: use promo code ROGAN.\n"
                "BetterHelp.com/JRE\n"
                "Sponsor of Chat With Traders Podcast: Trade The Pool.\n"
                "Learn more about your ad choices. Visit podcastchoices.com/adchoices."
            )
        }

        sections = build_show_notes_sections(
            ep,
            translation_enabled=True,
            translation_options={"cache_enabled": False, "translate_chunk": recording_runner},
        )
        combined = "\n".join(sections)
        translated_input = "\n\n".join(recorded_chunks)

        self.assertEqual(sections[0], SHOW_NOTES_TRANSLATED_HEADING)
        self.assertEqual(len(recorded_chunks), 1)
        self.assertIn("Skydio CEO Adam Bry", combined)
        self.assertIn("Skydio CEO Adam Bry", translated_input)
        self.assertNotIn("DraftKings", translated_input)
        self.assertNotIn("BetterHelp.com/JRE", translated_input)
        self.assertNotIn("Sponsor of Chat With Traders Podcast", translated_input)
        self.assertNotIn("Learn more about your ad choices", translated_input)
        self.assertNotIn("DraftKings", combined)
        self.assertNotIn("BetterHelp.com/JRE", combined)
        self.assertNotIn("Sponsor of Chat With Traders Podcast", combined)
        self.assertNotIn("Learn more about your ad choices", combined)

    def test_translation_cache_key_uses_filtered_display_text(self):
        base_show_notes = (
            "Today, I’m talking with Skydio CEO Adam Bry about drones, AI, "
            "government work, and Chinese competition. The conversation covers "
            "autonomous systems, public safety, defense procurement, supply chains, "
            "and how American robotics companies compete in global markets."
        )
        ep_with_sponsor = {
            "podcast_id": "decoder",
            "episode_id": "skydio",
            "url": "https://example.com/skydio",
            "language": "en",
            "show_notes_text": (
                base_show_notes
                + "\n\nDraftKings\nBetterHelp.com/JRE\n"
                "Learn more about your ad choices. Visit podcastchoices.com/adchoices."
            ),
        }
        ep_clean = dict(ep_with_sponsor, show_notes_text=base_show_notes)
        runner_calls = []

        def recording_runner(chunk, target_language="zh"):
            runner_calls.append(chunk)
            return mock_translate_show_notes_chunk(chunk, target_language=target_language)

        def fail_on_cache_miss(chunk, target_language="zh"):
            raise RuntimeError("cache miss should not run translation")

        with tempfile.TemporaryDirectory() as tmpdir:
            first = build_show_notes_sections(
                ep_with_sponsor,
                translation_enabled=True,
                translation_options={
                    "cache_root": tmpdir,
                    "translate_chunk": recording_runner,
                },
            )
            second = build_show_notes_sections(
                ep_clean,
                translation_enabled=True,
                translation_options={
                    "cache_root": tmpdir,
                    "translate_chunk": fail_on_cache_miss,
                },
            )

        self.assertEqual(first[0], SHOW_NOTES_TRANSLATED_HEADING)
        self.assertEqual(second[0], SHOW_NOTES_TRANSLATED_HEADING)
        self.assertEqual(len(runner_calls), 1)
        self.assertNotIn("DraftKings", runner_calls[0])
        self.assertNotIn("BetterHelp.com/JRE", runner_calls[0])
        self.assertNotIn("Learn more about your ad choices", runner_calls[0])
        self.assertIn("【中文翻译/mock】", "\n".join(second))

    def test_build_sections_cleans_and_chunks_without_side_effects(self):
        ep = {"show_notes_text": "<p>START &amp; intro</p>" + ("正文。" * 50) + "<p>END</p>"}

        with tempfile.TemporaryDirectory() as tmpdir:
            before = set(os.listdir(tmpdir))
            with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")), \
                 patch("subprocess.run", side_effect=AssertionError("subprocess forbidden")):
                sections = build_show_notes_sections(ep, max_chars=60)
            after = set(os.listdir(tmpdir))

        combined = "".join(sections)
        self.assertIn("START & intro", combined)
        self.assertIn("正文。", combined)
        self.assertIn("END", combined)
        self.assertNotIn("&amp;", combined)
        self.assertNotIn("<p>", combined)
        self.assertTrue(all(len(section) <= 60 for section in sections))
        self.assertEqual(before, after)

    def test_build_sections_filters_boilerplate_but_keeps_resources_and_body(self):
        ep = {
            "show_notes_text": """
SHOW_NOTES_BEGIN discusses AI agents, data infrastructure, supply chains, China, and semiconductors.

Resources:
Guest LinkedIn: https://www.linkedin.com/in/example
Apply to Y Combinator: https://www.ycombinator.com/apply
Work at a startup: https://www.ycombinator.com/jobs

SHOW_NOTES_MIDDLE contains core episode material.

Please note that the content here is for informational purposes only and should NOT be taken as legal, business, tax, or investment advice.
For more details please see a16z.com/disclosures.
Hosted by Simplecast, an AdsWizz company. See pcm.adswizz.com for information about privacy and advertising.
Learn more about your ad choices. Visit podcastchoices.com/adchoices
Perplexity: Download the app or ask Perplexity anything at https://pplx.ai/rogan.
Use code ROGAN at https://BlueChew.com to get 10% OFF.
Open an account in minutes at https://Chime.com/Rogan

SHOW_NOTES_END closes the episode description.
"""
        }

        sections = build_show_notes_sections(ep, max_chars=500)
        combined = "\n".join(sections)

        self.assertIn("SHOW_NOTES_BEGIN", combined)
        self.assertIn("SHOW_NOTES_MIDDLE", combined)
        self.assertIn("SHOW_NOTES_END", combined)
        self.assertIn("Resources:", combined)
        self.assertIn("Guest LinkedIn: https://www.linkedin.com/in/example", combined)
        self.assertIn("Apply to Y Combinator", combined)
        self.assertIn("Work at a startup", combined)
        self.assertIn("AI agents", combined)
        self.assertIn("data infrastructure", combined)
        self.assertIn("supply chains", combined)
        self.assertIn("China", combined)
        self.assertIn("semiconductors", combined)
        self.assertNotIn("Please note that the content here is for informational purposes only", combined)
        self.assertNotIn("a16z.com/disclosures", combined)
        self.assertNotIn("Hosted by Simplecast", combined)
        self.assertNotIn("AdsWizz", combined)
        self.assertNotIn("pcm.adswizz.com", combined)
        self.assertNotIn("Learn more about your ad choices", combined)
        self.assertNotIn("podcastchoices.com/adchoices", combined)
        self.assertNotIn("Perplexity:", combined)
        self.assertNotIn("BlueChew", combined)
        self.assertNotIn("Chime.com/Rogan", combined)

    def test_build_sections_returns_placeholder_when_filter_removes_all_content(self):
        ep = {
            "show_notes_text": (
                "Hosted by Simplecast, an AdsWizz company.\n"
                "Learn more about your ad choices. Visit podcastchoices.com/adchoices"
            )
        }

        self.assertEqual(build_show_notes_sections(ep), [SHOW_NOTES_PLACEHOLDER])

    def test_build_sections_keeps_chinese_show_notes_unchanged_after_filter(self):
        text = "本期讨论 AI、supply chain、data infrastructure、China 与 semiconductors。"

        self.assertEqual(build_show_notes_sections({"show_notes_text": text}), [text])

    def test_build_sections_chunks_filtered_long_text_without_dropping_sentinels(self):
        ep = {
            "show_notes_text": (
                "LONG_BEGIN. "
                + ("核心内容。 " * 80)
                + "Learn more about your ad choices. Visit podcastchoices.com/adchoices\n"
                + ("中段内容。 " * 80)
                + "LONG_END."
            )
        }

        sections = build_show_notes_sections(ep, max_chars=120)
        combined = "".join(sections)

        self.assertIn("LONG_BEGIN", combined)
        self.assertIn("核心内容", combined)
        self.assertIn("中段内容", combined)
        self.assertIn("LONG_END", combined)
        self.assertNotIn("Learn more about your ad choices", combined)
        self.assertTrue(all(len(section) <= 120 for section in sections))


class TestShowNotesLanguageDetection(unittest.TestCase):
    def test_chinese_text_is_not_translated(self):
        text = (
            "本期节目讨论电力市场、人工智能和能源转型。"
            "嘉宾详细介绍了行业变化、政策背景和投资机会。"
        )

        self.assertEqual(detect_show_notes_display_language(text), "zh")
        self.assertFalse(should_translate_show_notes_for_display(text))

    def test_english_text_is_translated(self):
        text = (
            "Today, I am talking with the CEO of a leading energy technology company. "
            "We discuss the power grid, AI data centers, electricity demand, and the "
            "future of clean energy."
        )

        self.assertEqual(detect_show_notes_display_language(text), "en")
        self.assertTrue(should_translate_show_notes_for_display(text))

    def test_mixed_text_with_chinese_majority_is_not_translated(self):
        text = (
            "本期节目讨论 AI、data center 和电力市场。"
            "嘉宾认为 electricity demand 会改变能源基础设施。"
        )

        self.assertIn(detect_show_notes_display_language(text), ("zh", "mixed"))
        self.assertFalse(should_translate_show_notes_for_display(text))

    def test_mostly_english_text_with_small_chinese_phrase_is_translated(self):
        text = (
            "This episode discusses AI data centers, power markets, electricity demand, "
            "grid infrastructure, clean energy development, and utility investment plans. "
            "主播 briefly mentions 中国市场, but the show notes are mostly English."
        )

        self.assertEqual(detect_show_notes_display_language(text), "en")
        self.assertTrue(should_translate_show_notes_for_display(text))

    def test_short_text_is_unknown_and_not_translated(self):
        text = "AI and power."

        self.assertEqual(detect_show_notes_display_language(text), "unknown")
        self.assertFalse(should_translate_show_notes_for_display(text))

    def test_w28_ali_siddiq_short_english_notes_are_translated(self):
        text = (
            'Ali Siddiq is a comedian, author, and public speaker. His new special, '
            '"My Father," is now streaming on YouTube. See him live on the "Custom Fit" Tour.\n'
            'https://youtu.be/XiSewRUOVyg\n'
            'www.youtube.com/@AliSiddiqComedy\n'
            'www.alisiddiq.com'
        )

        self.assertEqual(detect_show_notes_display_language(text), "en")
        self.assertTrue(should_translate_show_notes_for_display(text, source_language="en"))

    def test_explicit_english_allows_short_clear_chapter_notes(self):
        text = "00:00 Opening remarks\n02:15 Energy demand and grid planning\n08:40 Closing thoughts"

        self.assertEqual(detect_show_notes_display_language(text), "unknown")
        self.assertTrue(should_translate_show_notes_for_display(text, source_language="en"))

    def test_explicit_english_does_not_override_chinese_or_ambiguous_mixed_text(self):
        chinese = "本期节目讨论人工智能、能源市场和电网建设，嘉宾详细解释行业变化。"
        mixed = "本期讨论 AI data center、电力市场和 grid planning，并分析长期投资机会。"

        self.assertFalse(should_translate_show_notes_for_display(chinese, source_language="en"))
        self.assertFalse(should_translate_show_notes_for_display(mixed, source_language="en"))

    def test_url_heavy_text_is_unknown_and_not_translated(self):
        text = (
            "https://example.com https://podcasts.apple.com "
            "www.example.com 2026-06-21 00:00:00"
        )

        self.assertEqual(detect_show_notes_display_language(text), "unknown")
        self.assertFalse(should_translate_show_notes_for_display(text))

    def test_non_string_inputs_are_unknown_and_not_translated(self):
        for value in (None, {}, [], 123):
            with self.subTest(value=value):
                self.assertEqual(detect_show_notes_display_language(value), "unknown")
                self.assertFalse(should_translate_show_notes_for_display(value))

    def test_source_language_overrides_translation_decision(self):
        english_text = (
            "Today, I am talking with the CEO of a leading energy technology company. "
            "We discuss the power grid, AI data centers, electricity demand, and the "
            "future of clean energy."
        )
        chinese_text = (
            "本期节目讨论电力市场、人工智能和能源转型。"
            "嘉宾详细介绍了行业变化、政策背景和投资机会。"
        )

        self.assertFalse(
            should_translate_show_notes_for_display(english_text, source_language="zh")
        )
        self.assertTrue(
            should_translate_show_notes_for_display(english_text, source_language="en")
        )
        self.assertFalse(
            should_translate_show_notes_for_display(chinese_text, source_language="en")
        )


class TestShowNotesBoilerplateDisplayFilter(unittest.TestCase):
    def test_filters_a16z_disclosure_and_hosting_footer_but_keeps_resources(self):
        text = """
AI agents are changing data infrastructure and supply chains.

Resources:
Will Bryk on X: https://x.com/willbryk
Exa AI: https://exa.ai

Please note that the content here is for informational purposes only and should NOT be taken as legal, business, tax, or investment advice.
a16z and its affiliates may maintain investments in the companies discussed in this podcast.
For more details please see a16z.com/disclosures.

Hosted by Simplecast, an AdsWizz company. See pcm.adswizz.com for information about privacy and advertising.
"""

        filtered = filter_show_notes_boilerplate_for_display(text)

        self.assertIn("AI agents are changing data infrastructure and supply chains.", filtered)
        self.assertIn("Resources:", filtered)
        self.assertIn("Will Bryk on X: https://x.com/willbryk", filtered)
        self.assertIn("Exa AI: https://exa.ai", filtered)
        self.assertNotIn("Please note that the content here is for informational purposes only", filtered)
        self.assertNotIn("should NOT be taken as legal, business, tax, or investment advice", filtered)
        self.assertNotIn("a16z and its affiliates may maintain investments", filtered)
        self.assertNotIn("a16z.com/disclosures", filtered)
        self.assertNotIn("Hosted by Simplecast", filtered)
        self.assertNotIn("AdsWizz", filtered)
        self.assertNotIn("pcm.adswizz.com", filtered)

    def test_filters_a16z_fixed_stay_updated_footer_without_removing_resources(self):
        text = """
Resources:
Guest LinkedIn: https://www.linkedin.com/in/example

Stay Updated: Find a16z on YouTube: https://youtube.com/a16z
Listen to the a16z Show on Spotify: https://spotify.com/a16z
"""

        filtered = filter_show_notes_boilerplate_for_display(text)

        self.assertIn("Resources:", filtered)
        self.assertIn("Guest LinkedIn: https://www.linkedin.com/in/example", filtered)
        self.assertNotIn("Stay Updated:", filtered)
        self.assertNotIn("Find a16z on YouTube", filtered)
        self.assertNotIn("Listen to the a16z Show on Spotify", filtered)

    def test_filters_jre_adchoices_and_exact_sponsor_lines(self):
        text = """
Joe talks with a writer about Flashdance and Hollywood.
Guest IMDB: https://www.imdb.com/name/nm0000000/

Perplexity: Download the app or ask Perplexity anything at https://pplx.ai/rogan.
Use code ROGAN at https://BlueChew.com to get 10% OFF + Free Overnight Shipping on your first order.
Open an account in minutes at https://Chime.com/Rogan
Learn more about your ad choices. Visit podcastchoices.com/adchoices
"""

        filtered = filter_show_notes_boilerplate_for_display(text)

        self.assertIn("Joe talks with a writer about Flashdance and Hollywood.", filtered)
        self.assertIn("Guest IMDB: https://www.imdb.com/name/nm0000000/", filtered)
        self.assertNotIn("Perplexity:", filtered)
        self.assertNotIn("Download the app or ask Perplexity", filtered)
        self.assertNotIn("BlueChew", filtered)
        self.assertNotIn("Use code ROGAN", filtered)
        self.assertNotIn("Chime.com/Rogan", filtered)
        self.assertNotIn("Open an account in minutes at", filtered)
        self.assertNotIn("Learn more about your ad choices", filtered)
        self.assertNotIn("podcastchoices.com/adchoices", filtered)

    def test_keeps_yc_and_making_sense_content_that_is_not_yet_filtered(self):
        text = """
Chapters:
00:00 Intro
Apply to Y Combinator: https://www.ycombinator.com/apply
Work at a startup: https://www.ycombinator.com/jobs
SUBSCRIBE to gain access to all full episodes at samharris.org/subscribe.
"""

        filtered = filter_show_notes_boilerplate_for_display(text)

        self.assertIn("Apply to Y Combinator", filtered)
        self.assertIn("Work at a startup", filtered)
        self.assertIn("SUBSCRIBE", filtered)
        self.assertIn("samharris.org/subscribe", filtered)

    def test_chinese_show_notes_and_core_topic_terms_are_not_changed(self):
        text = "本期讨论 AI、supply chain、data infrastructure、China 与 semiconductors。"

        self.assertEqual(filter_show_notes_boilerplate_for_display(text), text)

    def test_empty_and_raw_container_inputs_return_empty_string(self):
        self.assertEqual(filter_show_notes_boilerplate_for_display(""), "")
        self.assertEqual(filter_show_notes_boilerplate_for_display(None), "")
        self.assertEqual(filter_show_notes_boilerplate_for_display({"raw": "dict"}), "")
        self.assertEqual(filter_show_notes_boilerplate_for_display(["raw", "list"]), "")

    def test_filter_has_no_file_network_or_subprocess_side_effects(self):
        text = "Learn more about your ad choices. Visit podcastchoices.com/adchoices"

        with tempfile.TemporaryDirectory() as tmpdir:
            before = set(os.listdir(tmpdir))
            with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")), \
                 patch("subprocess.run", side_effect=AssertionError("subprocess forbidden")):
                filtered = filter_show_notes_boilerplate_for_display(text)
            after = set(os.listdir(tmpdir))

        self.assertEqual(filtered, "")
        self.assertEqual(before, after)


class TestShowNotesBoilerplateFilterExpansionCandidates(unittest.TestCase):
    def test_catalyst_sponsor_blocks_are_filtered_without_dropping_discussion(self):
        text = """
Today we discuss the electric supercycle and demand growth across the grid.

This episode is brought to you by FischTank PR and EnergyHub.
Learn more about how EnergyHub supports distributed energy resources at https://example.com.

The conversation then turns to batteries, utilities, and load growth.
"""

        filtered = filter_show_notes_boilerplate_for_display(text)

        self.assertIn("electric supercycle", filtered)
        self.assertIn("demand growth", filtered)
        self.assertIn("batteries, utilities, and load growth", filtered)
        self.assertNotIn("FischTank", filtered)
        self.assertNotIn("EnergyHub supports distributed energy resources", filtered)
        self.assertNotIn("Learn more about how EnergyHub", filtered)

    def test_dwarkesh_sponsors_section_is_filtered_without_dropping_discussion(self):
        text = """
Ada Palmer joins the podcast to discuss Machiavelli and political thought.

Sponsors

* Cursor recently saved one of my podcast recordings. Get started at cursor.com/dwarkesh

* Jane Street is a quantitative trading firm. Find open roles at janestreet.com/dwarkesh

We then discuss Renaissance politics and historical interpretation.
"""

        filtered = filter_show_notes_boilerplate_for_display(text)

        self.assertIn("Ada Palmer", filtered)
        self.assertIn("Machiavelli", filtered)
        self.assertIn("Renaissance politics", filtered)
        self.assertNotIn("Cursor recently saved", filtered)
        self.assertNotIn("cursor.com/dwarkesh", filtered)
        self.assertNotIn("Jane Street is a quantitative trading firm", filtered)
        self.assertNotIn("janestreet.com/dwarkesh", filtered)

    def test_brand_names_are_kept_in_main_discussion_context(self):
        text = (
            "The guest previously worked at Jane Street and later used Cursor while building AI tools.\n"
            "This is part of the main discussion, not a sponsor segment.\n"
            "EnergyHub appears here as a company being analyzed, not as an ad.\n"
            "Ramp improves spend management for fast-growing companies.\n"
            "Vanta, WorkOS, Rogo, and Ridgeline are discussed as software companies."
        )

        filtered = filter_show_notes_boilerplate_for_display(text)

        self.assertIn("Jane Street", filtered)
        self.assertIn("Cursor", filtered)
        self.assertIn("EnergyHub", filtered)
        self.assertIn("Ramp improves spend management", filtered)
        self.assertIn("Vanta, WorkOS, Rogo, and Ridgeline", filtered)
        self.assertIn("main discussion", filtered)

    def test_generic_sponsor_markers_remove_only_sponsor_blocks(self):
        text = """
Main discussion about grid reliability and AI infrastructure.

Thanks to our sponsors for supporting this episode.
Use code PODCAST for a discount.
Promo code POWER at checkout.
Brought to you by Example Sponsor.
This episode is sponsored by Another Sponsor.

Final discussion about utility planning.
"""

        filtered = filter_show_notes_boilerplate_for_display(text)

        self.assertIn("Main discussion about grid reliability", filtered)
        self.assertIn("Final discussion about utility planning", filtered)
        self.assertNotIn("Thanks to our sponsors", filtered)
        self.assertNotIn("Use code PODCAST", filtered)
        self.assertNotIn("Promo code POWER", filtered)
        self.assertNotIn("Brought to you by Example Sponsor", filtered)
        self.assertNotIn("This episode is sponsored by Another Sponsor", filtered)

    def test_jre_draftkings_sponsor_is_filtered_and_guest_links_are_kept(self):
        text = """
Cameron Hanes is a bowhunter, outdoorsman, endurance athlete, author, and host of the podcasts “Keep Hammering Collective,” “Sh*t Talkers Weekly,” and “Lift. Run. Shoot.”
www.youtube.com/@cameronhanes
https://us.macmillan.com/books/9781250365941/undeniable/
www.cameronhanes.com
Don’t miss out on all the action this week at DraftKings! Download the DraftKings app today! Sign-up using https://dkng.co/rogan or through my promo code ROGAN.
"""

        filtered = filter_show_notes_boilerplate_for_display(text)

        self.assertIn("Cameron Hanes is a bowhunter", filtered)
        self.assertIn("www.youtube.com/@cameronhanes", filtered)
        self.assertIn("https://us.macmillan.com/books/9781250365941/undeniable/", filtered)
        self.assertIn("www.cameronhanes.com", filtered)
        self.assertNotIn("DraftKings", filtered)
        self.assertNotIn("dkng.co/rogan", filtered)
        self.assertNotIn("promo code ROGAN", filtered)

    def test_jre_armra_sponsor_is_filtered_and_guest_links_are_kept(self):
        text = """
Joey Diaz is a stand-up comedian, actor, and host.
Joey Diaz YouTube: www.youtube.com/@JoeyDiaz
Joey Diaz homepage: www.joeydiaz.net
Joey Diaz Patreon: www.patreon.com/JoeyDiaz
Get 30% off + 2 free gifts at https://ARMRA.com/rogan
"""

        filtered = filter_show_notes_boilerplate_for_display(text)

        self.assertIn("Joey Diaz is a stand-up comedian", filtered)
        self.assertIn("www.youtube.com/@JoeyDiaz", filtered)
        self.assertIn("www.joeydiaz.net", filtered)
        self.assertIn("www.patreon.com/JoeyDiaz", filtered)
        self.assertNotIn("ARMRA.com/rogan", filtered)

    def test_jre_visible_and_betterhelp_sponsors_are_filtered_and_guest_links_are_kept(self):
        text = """
Dean Radin is a scientist and author.
Dean Radin book: www.penguinrandomhouse.com/books/750262/the-science-of-magic-by-dean-radin-phd/
Terry Bradshaw profile: www.terrybradshaw.com
Switch today at https://www.Visible.com for just 25/mo. Or Save $10 on your first month of Visible+ Pro with code ROGAN.
This video is sponsored by BetterHelp. Visit https://BetterHelp.com/JRE
"""

        filtered = filter_show_notes_boilerplate_for_display(text)

        self.assertIn("Dean Radin is a scientist and author.", filtered)
        self.assertIn("www.penguinrandomhouse.com/books/750262", filtered)
        self.assertIn("Terry Bradshaw profile: www.terrybradshaw.com", filtered)
        self.assertNotIn("Visible.com", filtered)
        self.assertNotIn("Visible+ Pro", filtered)
        self.assertNotIn("BetterHelp.com/JRE", filtered)

    def test_w26_jre_sponsor_lines_are_filtered_and_guest_links_are_kept(self):
        text = """
Scott Horton is the director of the Libertarian Institute.
Scott Horton links: https://scotthorton.org
Try ZipRecruiter FOR FREE at https://ziprecruiter.com/rogan

Tim Dillon is a comedian.
Tim Dillon tour dates: www.timdilloncomedy.com
50% off your first box at https://www.thefarmersdog.com/rogan

Taylor Sheridan is a filmmaker.
Taylor Sheridan ranch: www.bosqueranchheadquarters.com
Visit https://wildpastures.com/rogan for 20% Off + Free Shipping
"""

        filtered = filter_show_notes_boilerplate_for_display(text)

        self.assertIn("Scott Horton is the director", filtered)
        self.assertIn("https://scotthorton.org", filtered)
        self.assertIn("Tim Dillon is a comedian.", filtered)
        self.assertIn("www.timdilloncomedy.com", filtered)
        self.assertIn("Taylor Sheridan is a filmmaker.", filtered)
        self.assertIn("www.bosqueranchheadquarters.com", filtered)
        self.assertNotIn("ZipRecruiter", filtered)
        self.assertNotIn("ziprecruiter.com/rogan", filtered)
        self.assertNotIn("thefarmersdog.com/rogan", filtered)
        self.assertNotIn("wildpastures.com/rogan", filtered)

    def test_chat_with_traders_footer_is_filtered_and_resources_are_kept(self):
        text = """
Links + Resources:
George Coyle: https://x.com/gfc4
Jack Schwager: https://x.com/jackschwager
Order Market Wizards: The Next Generation Book: https://harriman-house.com/authors/jack-d-schwager/market-wizards-the-next-generation/9781804093641

Sponsor of Chat With Traders Podcast:
Trade The Pool: http://www.tradethepool.com

Trading Disclaimer:
Trading in the financial markets involves a risk of loss. Podcast episodes and other content produced by Chat With Traders are for informational or educational purposes only and do not constitute trading or investment recommendations or advice.
"""

        filtered = filter_show_notes_boilerplate_for_display(text)

        self.assertIn("Links + Resources:", filtered)
        self.assertIn("George Coyle: https://x.com/gfc4", filtered)
        self.assertIn("Jack Schwager: https://x.com/jackschwager", filtered)
        self.assertIn("Order Market Wizards", filtered)
        self.assertNotIn("Sponsor of Chat With Traders Podcast", filtered)
        self.assertNotIn("Trade The Pool", filtered)
        self.assertNotIn("Trading Disclaimer", filtered)
        self.assertNotIn("do not constitute trading or investment recommendations", filtered)

    def test_invest_like_the_best_sponsor_blocks_are_filtered_without_dropping_body(self):
        text = """
My guest today is Vlad Barbalat, President and CIO of Liberty Mutual Investments.
We discuss permanent capital, underwriting, and portfolio construction.

Become a Colossus member to get transcripts and more.

Ramp's mission is to help companies manage spending. Go to ramp.com/invest for $250.

Vanta is trusted by thousands of businesses to monitor security. Visit vanta.com/invest.

WorkOS⁠ is the infrastructure B2B and AI-native companies use to sell to enterprise.
Learn more about WorkOS at workos.com.

Rogo is the AI platform for finance. Learn more at rogo.ai/invest.

Ridgeline builds a modern operating system for investment managers. Visit ridgeline.ai.

Timestamps:
00:00 Intro
12:30 Permanent capital

Editing and post-production work is provided by The Podcast Consultant.
"""

        filtered = filter_show_notes_boilerplate_for_display(text)

        self.assertIn("Vlad Barbalat", filtered)
        self.assertIn("Liberty Mutual Investments", filtered)
        self.assertIn("permanent capital", filtered)
        self.assertIn("Timestamps:", filtered)
        self.assertIn("00:00 Intro", filtered)
        self.assertNotIn("Become a Colossus member", filtered)
        self.assertNotIn("Ramp's mission", filtered)
        self.assertNotIn("ramp.com/invest", filtered)
        self.assertNotIn("Vanta is trusted", filtered)
        self.assertNotIn("vanta.com/invest", filtered)
        self.assertNotIn("WorkOS", filtered)
        self.assertNotIn("Rogo is the AI platform", filtered)
        self.assertNotIn("rogo.ai/invest", filtered)
        self.assertNotIn("Ridgeline builds", filtered)
        self.assertNotIn("ridgeline.ai", filtered)
        self.assertNotIn("The Podcast Consultant", filtered)

    def test_w31_lex_sponsor_section_is_filtered_and_outline_is_kept(self):
        text = """
Gary Gallagher is a historian of the American Civil War.

EPISODE LINKS:
Gary's UVA page: https://history.virginia.edu/people/gary-w-gallagher

SPONSORS:

To support this podcast, check out our sponsors & get discounts:

Upwork: Platform for hiring freelancers.

Go to https://upwork.com/lex

NetSuite: Business management software.

Go to http://netsuite.ai/lex

Shopify: Sell stuff online.

Go to https://shopify.com/lex

LMNT: Zero-sugar electrolyte drink mix.

Go to https://drinkLMNT.com/lex

Perplexity: AI-powered answer engine.

Go to https://perplexity.ai/

Plaud: AI-powered note-taking devices and software.

Go to https://plaud.ai/lex

OUTLINE:

(00:00) - Introduction

(2:34:06) - Robert E. Lee
"""

        filtered = filter_show_notes_boilerplate_for_display(text)

        self.assertIn("Gary Gallagher is a historian", filtered)
        self.assertIn("EPISODE LINKS:", filtered)
        self.assertIn("Gary's UVA page", filtered)
        self.assertIn("OUTLINE:", filtered)
        self.assertIn("(2:34:06) - Robert E. Lee", filtered)
        for sponsor_term in (
            "Upwork",
            "NetSuite",
            "Shopify",
            "LMNT",
            "Perplexity",
            "Plaud",
            "upwork.com/lex",
            "plaud.ai/lex",
        ):
            self.assertNotIn(sponsor_term, filtered)

    def test_terminal_live_long_and_prosper_signoff_is_filtered_only_as_own_block(self):
        terminal = (
            "The episode explores orbital energy infrastructure and satellite power.\n\n"
            "Live long and prosper."
        )
        editorial = (
            "The hosts discuss why the phrase Live long and prosper matters in science fiction."
        )

        self.assertNotIn(
            "Live long and prosper",
            filter_show_notes_boilerplate_for_display(terminal),
        )
        self.assertIn(
            "Live long and prosper",
            filter_show_notes_boilerplate_for_display(editorial),
        )

    def test_a16z_social_footer_is_filtered_but_guest_resource_links_are_kept(self):
        text = """
Resources:
Follow Marc Andreessen on X: https://x.com/pmarca
Follow Michael Malice on X: https://x.com/michaelmalice
If you enjoyed this episode, be sure to like, subscribe, and share with your friends!
Find a16z on X: https://twitter.com/a16z
Find a16z on LinkedIn: https://www.linkedin.com/company/a16z
Listen to the a16z Show on Apple Podcasts: https://podcasts.apple.com/us/podcast/a16z-podcast/id842818711
Follow our host: https://x.com/eriktorenberg
"""

        filtered = filter_show_notes_boilerplate_for_display(text)

        self.assertIn("Resources:", filtered)
        self.assertIn("Follow Marc Andreessen on X: https://x.com/pmarca", filtered)
        self.assertIn("Follow Michael Malice on X: https://x.com/michaelmalice", filtered)
        self.assertNotIn("Find a16z on X", filtered)
        self.assertNotIn("Find a16z on LinkedIn", filtered)
        self.assertNotIn("Listen to the a16z Show on Apple Podcasts", filtered)
        self.assertNotIn("Follow our host", filtered)

    def test_odd_lots_footer_is_filtered_but_body_resources_are_kept(self):
        text = """
Odd Lots discusses volatility, markets, and private credit.

Read more:
Bloomberg story: http://Bloomberg.com/markets/story

Only Bloomberg.com subscribers can listen to the full episode.
Subscribe to the Odd Lots Newsletter.
Join the conversation: discord.gg/oddlots
See omnystudio.com/listener for privacy information.
"""

        filtered = filter_show_notes_boilerplate_for_display(text)

        self.assertIn("Odd Lots discusses volatility", filtered)
        self.assertIn("Read more:", filtered)
        self.assertIn("http://Bloomberg.com/markets/story", filtered)
        self.assertNotIn("Only Bloomberg.com subscribers", filtered)
        self.assertNotIn("Odd Lots Newsletter", filtered)
        self.assertNotIn("discord.gg/oddlots", filtered)
        self.assertNotIn("omnystudio.com/listener", filtered)

    def test_decoder_nyt_and_volts_subscription_footers_are_filtered(self):
        text = """
Decoder talks with a CEO about antitrust, platforms, and hardware.
Volts analyzes grid planning and climate policy.
The Daily discusses the latest policy news.

Subscribe to The Verge to access the ad-free version of Decoder.
Decoder is a production of The Verge and part of the Vox Media Podcast Network.
Subscribe today at nytimes.com/podcasts.
This is a public episode. If you would like to discuss this with other subscribers or get access to bonus episodes, visit www.volts.wtf/subscribe.
"""

        filtered = filter_show_notes_boilerplate_for_display(text)

        self.assertIn("Decoder talks with a CEO", filtered)
        self.assertIn("Volts analyzes grid planning", filtered)
        self.assertIn("The Daily discusses", filtered)
        self.assertNotIn("ad-free version of Decoder", filtered)
        self.assertNotIn("production of The Verge", filtered)
        self.assertNotIn("nytimes.com/podcasts", filtered)
        self.assertNotIn("www.volts.wtf/subscribe", filtered)

    def test_chinese_sponsor_block_is_filtered_but_related_materials_are_kept(self):
        text = """
本期节目讨论 AI、消费电子和长期主义。

感谢🚀 红色火箭小程序 对本期节目的赞助播出。
关于这款 小程序，它可以帮助你快速生成工作流。
如果你对效率工具感兴趣，可以搜索红色火箭了解更多。

📁 本期内容相关资料
品哥在节目里提到的一篇文章：《命运的留白》
"""

        filtered = filter_show_notes_boilerplate_for_display(text)

        self.assertIn("本期节目讨论 AI、消费电子和长期主义。", filtered)
        self.assertIn("📁 本期内容相关资料", filtered)
        self.assertIn("《命运的留白》", filtered)
        self.assertNotIn("感谢🚀 红色火箭小程序", filtered)
        self.assertNotIn("关于这款 小程序", filtered)
        self.assertNotIn("搜索红色火箭", filtered)

    def test_translation_input_filters_w26_sponsor_footer_before_translation(self):
        recorded_chunks = []

        def recording_runner(chunk, target_language="zh"):
            recorded_chunks.append(chunk)
            return mock_translate_show_notes_chunk(chunk, target_language=target_language)

        ep = {
            "language": "en",
            "show_notes_text": """
My guest today is Vlad Barbalat, President and CIO of Liberty Mutual Investments.
We discuss permanent capital, underwriting, and portfolio construction.
This conversation covers insurance balance sheets, markets, and manager selection.

Ramp's mission is to help companies manage spending. Go to ramp.com/invest for $250.

Vanta is trusted by thousands of businesses. Visit vanta.com/invest.

WorkOS is infrastructure for B2B and AI-native companies selling to the enterprise.

Rogo is the AI platform for finance. Learn more at rogo.ai/invest.

Ridgeline builds a modern operating system for investment managers. Visit ridgeline.ai.

Timestamps:
00:00 Intro
12:30 Permanent capital
""",
        }

        sections = build_show_notes_sections(
            ep,
            translation_enabled=True,
            translation_options={"cache_enabled": False, "translate_chunk": recording_runner},
        )
        translated_input = "\n\n".join(recorded_chunks)

        self.assertEqual(sections[0], SHOW_NOTES_TRANSLATED_HEADING)
        self.assertIn("Vlad Barbalat", translated_input)
        self.assertIn("Timestamps:", translated_input)
        self.assertNotIn("Ramp", translated_input)
        self.assertNotIn("ramp.com/invest", translated_input)
        self.assertNotIn("Vanta", translated_input)
        self.assertNotIn("WorkOS", translated_input)
        self.assertNotIn("Rogo", translated_input)
        self.assertNotIn("Ridgeline", translated_input)

    def test_chinese_investment_disclaimer_is_kept_for_now(self):
        text = """
本期讨论 AI、消费电子和公司战略。
DISCLAIMER: 本内容不作为投资建议。
CONTACT: xiaojunzhang@lisw.ai
"""

        filtered = filter_show_notes_boilerplate_for_display(text)

        self.assertIn("本期讨论 AI、消费电子和公司战略。", filtered)
        self.assertIn("DISCLAIMER: 本内容不作为投资建议。", filtered)
        self.assertIn("CONTACT: xiaojunzhang@lisw.ai", filtered)


class TestStructuredShowNotesDisplayFilter(unittest.TestCase):
    def test_classifies_and_filters_display_blocks_by_category(self):
        text = """
This episode examines energy infrastructure and AI data centers.

Resources:
Research report: https://example.com/report

Chapters:
00:00 Introduction
08:30 Grid planning

Credits:
Produced by Example Producer. Original music by Example Musician.

Stay Updated:
Follow the show on social media and subscribe to our newsletter.

This episode is sponsored by Example Cloud. Visit example.com/deal.

See Privacy Policy at https://art19.com/privacy and California Privacy Notice.
"""

        result = build_show_notes_display_filter_result(text)

        self.assertIn("energy infrastructure", result["text"])
        self.assertIn("Resources:", result["text"])
        self.assertIn("https://example.com/report", result["text"])
        self.assertIn("Chapters:", result["text"])
        self.assertIn("08:30 Grid planning", result["text"])
        self.assertNotIn("Example Producer", result["text"])
        self.assertNotIn("subscribe to our newsletter", result["text"])
        self.assertNotIn("Example Cloud", result["text"])
        self.assertNotIn("art19.com/privacy", result["text"])
        self.assertGreaterEqual(result["kept_category_counts"].get("resources", 0), 1)
        self.assertGreaterEqual(result["kept_category_counts"].get("chapters", 0), 1)
        for category in ("credits", "cta", "sponsor", "privacy"):
            self.assertGreaterEqual(result["removed_category_counts"].get(category, 0), 1)
        self.assertNotIn("Example Cloud", json.dumps(result, ensure_ascii=False))

    def test_filters_w28_privacy_and_sponsor_variants(self):
        text = """
The episode discusses infrastructure investment and project delivery.

See Privacy Policy at https://art19.com/privacy and California Privacy Notice at https://art19.com/privacy#do-not-sell-my-info.

Get watch party snacks and groceries on Uber Eats.

onX Offroad: Try onX Offroad for 50% off- go to https://onXmaps.com/joerogan

Any leader should see for themselves the benefits of elite coaching. Try ALEX: tryalex.admiredleadership.com.
"""

        result = build_show_notes_display_filter_result(text)

        self.assertIn("infrastructure investment", result["text"])
        self.assertNotIn("Privacy Policy", result["text"])
        self.assertNotIn("Uber Eats", result["text"])
        self.assertNotIn("onX Offroad", result["text"])
        self.assertNotIn("Try ALEX", result["text"])
        self.assertGreaterEqual(result["removed_category_counts"].get("sponsor", 0), 3)
        self.assertEqual(result["removed_category_counts"].get("privacy"), 1)

    def test_filters_w28_credits_subscription_social_and_chinese_cta(self):
        text = """
The conversation covers AI companions, markets, and policy.

Additional Reading:
Why Social Media Bans Are Gaining Steam

Credits:
Decoder is produced by Kate Cox and edited by Ursa Wright. The music is by Breakmaster Cylinder.

We want to hear from you. Email us at hardfork@nytimes.com. Find Hard Fork on YouTube and TikTok.

Get full access to Dwarkesh Podcast at www.dwarkesh.com/subscribe.

If the Making Sense podcast logo in your player is BLACK, you can SUBSCRIBE to gain access to all full-length episodes at samharris.org/subscribe.

欢迎关注我们的公众号，并加入知识星球获取更多完整内容。
"""

        result = build_show_notes_display_filter_result(text)

        self.assertIn("AI companions", result["text"])
        self.assertIn("Additional Reading:", result["text"])
        self.assertIn("Why Social Media Bans", result["text"])
        self.assertNotIn("Kate Cox", result["text"])
        self.assertNotIn("hardfork@nytimes.com", result["text"])
        self.assertNotIn("dwarkesh.com/subscribe", result["text"])
        self.assertNotIn("samharris.org/subscribe", result["text"])
        self.assertNotIn("知识星球", result["text"])
        self.assertGreaterEqual(result["removed_category_counts"].get("cta", 0), 4)

    def test_keeps_brand_names_in_body_and_real_resource_links(self):
        text = """
The discussion compares how Cursor and Jane Street deploy AI. EnergyHub is discussed as a case study in flexible grid capacity.

Resources:
How Webflow users research customer needs: https://example.com/webflow
Ploy founder interview: https://example.com/ploy

Chapters:
00:00 Cursor and developer tools
12:30 Jane Street and model evaluation
"""

        result = build_show_notes_display_filter_result(text)

        for term in ("Cursor", "Jane Street", "EnergyHub", "Webflow", "Ploy"):
            self.assertIn(term, result["text"])
        self.assertEqual(result["removed_category_counts"], {})
        self.assertEqual(filter_show_notes_boilerplate_for_display(text), result["text"])

    def test_spaced_structural_headings_keep_expected_categories(self):
        cases = (
            ("Additional Reading :", "resources"),
            ("Links :", "resources"),
            ("Resources ：", "resources"),
            ("Chapters :", "chapters"),
        )
        for heading, category in cases:
            with self.subTest(heading=heading):
                text = (
                    "Translated body candidate.\n\n"
                    f"{heading}\n\n"
                    "OpenAI Says Its A.I. Models Went Rogue and Attacked a Digital Library"
                )

                result = build_show_notes_display_filter_result(text)

                self.assertIn(heading, result["text"])
                self.assertEqual(result["kept_category_counts"].get(category), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
