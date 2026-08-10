#!/usr/bin/env python3
"""Display expectations for future show-notes translation integration.

These tests intentionally do not enable translation in production renderers yet.
They document the display contract for the next integration step and assert that
current show-notes rendering remains unchanged.
"""

import unittest
import tempfile

from episode_show_notes_renderer import build_show_notes_sections
from show_notes_translation_runner import mock_translate_show_notes_chunk


EXPECTED_TRANSLATED_SHOW_NOTES_HEADING = "节目介绍 / Show Notes（中文翻译，原文已保留）"
CURRENT_SHOW_NOTES_HEADING = "节目介绍 / Show Notes（完整）"


def _expected_show_notes_heading_for_translation_result(
    *,
    source_language: str,
    translation_status: str,
    translated_text: str,
) -> str:
    translated_statuses = {
        "translated",
        "cache_hit",
        "partial_translated",
        "partial_cache_hit",
    }
    if (
        source_language == "en"
        and translation_status in translated_statuses
        and translated_text
    ):
        return EXPECTED_TRANSLATED_SHOW_NOTES_HEADING
    return CURRENT_SHOW_NOTES_HEADING


class TestShowNotesTranslationDisplayExpectations(unittest.TestCase):
    def test_english_markdown_translation_display_contract(self):
        episode = {
            "podcast": "Decoder with Nilay Patel",
            "title": "Skydio CEO argues more drones will make us safer",
            "show_notes_text": (
                "Today, I’m talking with Skydio CEO Adam Bry. We discuss drones, "
                "AI, government work, and Chinese competition. The conversation covers "
                "autonomous systems, public safety, defense procurement, supply chains, "
                "and how American robotics companies compete in global markets."
            ),
        }

        sections = build_show_notes_sections(
            episode,
            translation_enabled=True,
            translation_options={
                "cache_enabled": False,
                "translate_chunk": mock_translate_show_notes_chunk,
            },
        )
        joined = "\n".join(sections)

        self.assertEqual(sections[0], EXPECTED_TRANSLATED_SHOW_NOTES_HEADING)
        self.assertIn("【中文翻译/mock】", joined)
        self.assertIn("Skydio CEO Adam Bry", joined)
        self.assertNotIn(CURRENT_SHOW_NOTES_HEADING, joined)

    def test_chinese_mixed_and_unknown_show_notes_keep_current_heading(self):
        cases = [
            {
                "source_language": "zh",
                "translation_status": "skipped",
                "translated_text": "",
                "text": "本期节目讨论电力市场、AI 数据中心和能源基础设施。",
            },
            {
                "source_language": "mixed",
                "translation_status": "skipped",
                "translated_text": "",
                "text": "本期节目讨论 AI infrastructure and 电力市场。",
            },
            {
                "source_language": "unknown",
                "translation_status": "skipped",
                "translated_text": "",
                "text": "Short note.",
            },
        ]

        for case in cases:
            with self.subTest(source_language=case["source_language"]):
                heading = _expected_show_notes_heading_for_translation_result(
                    source_language=case["source_language"],
                    translation_status=case["translation_status"],
                    translated_text=case["translated_text"],
                )
                sections = build_show_notes_sections(
                    {"show_notes_text": case["text"]},
                    translation_enabled=True,
                    translation_options={
                        "cache_enabled": False,
                        "translate_chunk": mock_translate_show_notes_chunk,
                    },
                )
                joined = "\n".join(sections)

                self.assertEqual(heading, CURRENT_SHOW_NOTES_HEADING)
                self.assertNotEqual(heading, EXPECTED_TRANSLATED_SHOW_NOTES_HEADING)
                self.assertNotIn(EXPECTED_TRANSLATED_SHOW_NOTES_HEADING, joined)
                self.assertNotIn("【中文翻译/mock】", joined)

    def test_feishu_translation_display_contract_matches_markdown_heading(self):
        episode = {
            "show_notes_text": (
                "Today, I’m talking with Skydio CEO Adam Bry. We discuss drones, AI, "
                "government work, and Chinese competition. The conversation covers "
                "autonomous systems, public safety, defense procurement, supply chains, "
                "and how American robotics companies compete in global markets."
            )
        }
        sections = build_show_notes_sections(
            episode,
            translation_enabled=True,
            translation_options={
                "cache_enabled": False,
                "translate_chunk": mock_translate_show_notes_chunk,
            },
        )
        feishu_text_blocks = [
            "节目标题：Skydio CEO argues more drones will make us safer",
            *sections,
        ]
        joined = "\n".join(feishu_text_blocks)

        self.assertIn(EXPECTED_TRANSLATED_SHOW_NOTES_HEADING, joined)
        self.assertNotIn("英文原文完整附录", joined)
        self.assertNotIn("原始报告", joined)

    def test_translation_failure_falls_back_to_current_full_show_notes_heading(self):
        def failing_runner(chunk, target_language="zh"):
            raise RuntimeError("boom")

        episode = {
            "show_notes_text": (
                "Today, I’m talking with Skydio CEO Adam Bry. We discuss drones, AI, "
                "government work, and Chinese competition. The conversation covers "
                "autonomous systems, public safety, defense procurement, supply chains, "
                "and how American robotics companies compete in global markets."
            )
        }

        sections = build_show_notes_sections(
            episode,
            translation_enabled=True,
            translation_options={"cache_enabled": False, "translate_chunk": failing_runner},
        )
        joined = "\n".join(sections)

        self.assertIn("Today, I’m talking with Skydio CEO Adam Bry", joined)
        self.assertNotIn(EXPECTED_TRANSLATED_SHOW_NOTES_HEADING, joined)

    def test_cache_hit_and_translated_status_share_same_user_visible_heading(self):
        episode = {
            "podcast": "Decoder with Nilay Patel",
            "show_notes_text": (
                "Today, I’m talking with Skydio CEO Adam Bry. We discuss drones, AI, "
                "government work, and Chinese competition. The conversation covers "
                "autonomous systems, public safety, defense procurement, supply chains, "
                "and how American robotics companies compete in global markets."
            ),
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            translated = build_show_notes_sections(
                episode,
                translation_enabled=True,
                translation_options={
                    "cache_root": tmpdir,
                    "translate_chunk": mock_translate_show_notes_chunk,
                },
            )
            cache_hit = build_show_notes_sections(
                episode,
                translation_enabled=True,
                translation_options={
                    "cache_root": tmpdir,
                    "translate_chunk": lambda chunk, target_language="zh": "SHOULD NOT RUN",
                },
            )

        self.assertEqual(translated[0], EXPECTED_TRANSLATED_SHOW_NOTES_HEADING)
        self.assertEqual(cache_hit[0], EXPECTED_TRANSLATED_SHOW_NOTES_HEADING)
        self.assertIn("【中文翻译/mock】", "\n".join(cache_hit))

    def test_partial_translation_statuses_share_translated_heading(self):
        for status in ("partial_translated", "partial_cache_hit"):
            with self.subTest(status=status):
                heading = _expected_show_notes_heading_for_translation_result(
                    source_language="en",
                    translation_status=status,
                    translated_text=(
                        "本期讨论人工智能政策。\n\n"
                        "延伸阅读（原文）：\nThe Artificial State | The New Yorker"
                    ),
                )

                self.assertEqual(heading, EXPECTED_TRANSLATED_SHOW_NOTES_HEADING)

    def test_current_build_show_notes_sections_behavior_is_unchanged(self):
        english_episode = {
            "show_notes_text": (
                "Today, I’m talking with Skydio CEO Adam Bry. We discuss drones, "
                "AI, government work, and Chinese competition."
            )
        }

        sections = build_show_notes_sections(english_episode)
        joined = "\n".join(sections)

        self.assertIn("Today, I’m talking with Skydio CEO Adam Bry", joined)
        self.assertNotIn("中文翻译", joined)
        self.assertNotIn(EXPECTED_TRANSLATED_SHOW_NOTES_HEADING, joined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
