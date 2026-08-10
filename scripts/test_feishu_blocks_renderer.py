#!/usr/bin/env python3
"""
Hermetic tests for pure Feishu blocks rendering helpers.

These tests do not import delivery orchestration, call Feishu APIs, read latest
outputs, or write delivery state.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import feishu_blocks_renderer as renderer
import podcast_screener
from show_notes_translation_runner import mock_translate_show_notes_chunk


CONFIRMED_GUEST_FALLBACK = (
    "已确认本期嘉宾，但节目元数据未提供足够背景信息，暂不补充背景。"
)
LONG_SHOW_NOTES = (
    "<p>FEISHU_NOTES_BEGIN &amp; opening context.</p>"
    "<p>FEISHU_NOTES_MIDDLE covers AI strategy, capital allocation, and operating decisions "
    "with enough detail to prove the blocks are not summarizing this section.</p>"
    "<p>Hosted by Simplecast, an AdsWizz company. See pcm.adswizz.com for information about privacy.</p>"
    "<p>Learn more about your ad choices. Visit podcastchoices.com/adchoices</p>"
    "<p>FEISHU_NOTES_END closes the original episode description.</p>"
)
LONG_ENGLISH_TRANSLATION_SHOW_NOTES = (
    "Today, I am talking with Skydio CEO Adam Bry about drones, artificial intelligence, "
    "government work, public safety, defense procurement, supply chains, and Chinese competition. "
    "The conversation explains how autonomous systems are changing infrastructure, robotics, "
    "airspace policy, manufacturing strategy, and the future of American technology companies."
)
EMPTY_SHOW_NOTES_PLACEHOLDER = "暂无节目介绍。"


def flatten_blocks(blocks):
    return json.dumps(blocks, ensure_ascii=False)


def full_episode():
    return {
        "podcast_name": "Full Show",
        "episode_title": "AI Strategy &amp; Capital Allocation",
        "duration_minutes": 64,
        "final_score": 9.2,
        "priority": "high",
        "full_suggestion": "yes",
        "pub_datetime": "2026-05-27T09:00:00+0800",
        "reason_zh": "这期深入讨论AI战略与资本配置，值得完整转写。",
        "one_line_summary_cn": "本期讨论AI战略与资本配置。",
        "show_notes_text": LONG_SHOW_NOTES,
        "guest_detection_status": "confirmed_guest",
        "guest_names": ["Jane Doe"],
        "guest_background_zh": "Jane Doe是Example Capital合伙人，专注AI投资。",
        "guest_background_sources": [
            {
                "title": "Example Capital bio",
                "snippet": "Jane Doe is a partner at Example Capital focused on AI.",
                "url": "https://example.com/jane",
            }
        ],
        "topic_relevance": 90,
        "information_density": 85,
        "novelty": 80,
        "actionability": 70,
        "strategic_value": 88,
        "transcription_value": 75,
    }


def preview_episode():
    ep = full_episode()
    ep.update({
        "podcast_name": "Preview Show",
        "episode_title": "Market Notes &amp; Founder Lessons",
        "final_score": 8.1,
        "priority": "medium",
        "full_suggestion": "maybe",
        "reason_zh": "这期适合先预览，再判断是否完整转写。",
    })
    return ep


def fallback_episode():
    ep = preview_episode()
    ep.update({
        "episode_title": "Founder Roundtable &amp; Market Notes",
        "guest_names": ["Guest A", "Guest B"],
        "guest_background_zh": CONFIRMED_GUEST_FALLBACK,
        "guest_background_sources": [
            {"title": "节目元数据", "snippet": "职务/头衔：founder", "url": ""},
        ],
    })
    return ep


def skip_episode():
    return {
        "podcast_name": "Low Signal Show",
        "episode_title": "Weekly News &amp; Ads",
        "duration_minutes": 12,
        "final_score": 2.0,
        "priority": "low",
        "full_suggestion": "no",
        "pub_datetime": "2026-05-29T09:00:00+0800",
        "reason_zh": "广告和泛泛新闻回顾较多，信息密度不足。",
        "show_notes_text": LONG_SHOW_NOTES,
    }


def english_translation_episode():
    ep = full_episode()
    ep.update({
        "podcast_name": "Decoder with Nilay Patel",
        "episode_title": "Skydio CEO argues more drones will make us safer",
        "show_notes_text": LONG_ENGLISH_TRANSLATION_SHOW_NOTES,
        "one_line_summary_cn": "本期讨论无人机、AI和公共安全。",
    })
    return ep


def chinese_show_notes_episode():
    ep = full_episode()
    ep.update({
        "podcast_name": "中文节目",
        "episode_title": "AI 数据中心和能源基础设施",
        "show_notes_text": "本期节目讨论电力市场、AI 数据中心和能源基础设施。",
    })
    return ep


def result_data(full=None, preview=None, skip=None):
    full_items = [] if full is None else [full]
    preview_items = [] if preview is None else [preview]
    skip_items = [] if skip is None else [skip]
    return {
        "run_id": "run_blocks_001",
        "week_id": "2026W22",
        "window_start": "2026-05-24T22:00:00+0800",
        "window_end": "2026-05-31T22:00:00+0800",
        "scan_date": "2026-06-01",
        "total_episodes": len(full_items) + len(preview_items) + len(skip_items),
        "fetch_errors": [],
        "full": full_items,
        "preview": preview_items,
        "skip": skip_items,
    }


class TestFeishuBlocksRenderer(unittest.TestCase):
    def test_short_episode_duration_uses_exact_seconds_and_label(self):
        episode = preview_episode()
        episode["duration_seconds"] = 367
        episode["duration_minutes"] = 6

        text = flatten_blocks(renderer.build_episode_blocks(episode, "Preview"))

        self.assertIn("6分07秒（短节目）", text)

    def test_long_and_legacy_episode_duration_display_stays_compact(self):
        long_episode = preview_episode()
        long_episode["duration_seconds"] = 3907
        long_episode["duration_minutes"] = 65
        legacy_episode = skip_episode()
        legacy_episode.pop("duration_seconds", None)

        long_text = flatten_blocks(renderer.build_episode_blocks(long_episode, "Preview"))
        legacy_text = flatten_blocks(renderer.build_skip_blocks(legacy_episode))

        self.assertIn("65分钟", long_text)
        self.assertNotIn("短节目", long_text)
        self.assertIn("12分钟", legacy_text)
        self.assertNotIn("短节目", legacy_text)

    def test_episode_blocks_reuse_persisted_display_snapshot_without_translation(self):
        episode = english_translation_episode()
        episode["show_notes_display_snapshot"] = {
            "version": "show_notes_display_v1",
            "heading": "translated",
            "sections": ["这是 Markdown 阶段已经确认的中文译文。"],
        }

        with patch.object(
            renderer,
            "build_show_notes_display_result",
            side_effect=AssertionError("translation renderer must not run"),
        ) as shared:
            blocks = renderer.build_episode_blocks(
                episode,
                "Preview",
                show_notes_translation_enabled=True,
                show_notes_translation_options={
                    "translate_chunk": lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        AssertionError("OpenClaw runner must not run")
                    ),
                },
            )

        shared.assert_not_called()
        text = flatten_blocks(blocks)
        self.assertIn("节目介绍 / Show Notes（中文翻译，原文已保留）", text)
        self.assertIn("这是 Markdown 阶段已经确认的中文译文。", text)
        self.assertNotIn(LONG_ENGLISH_TRANSLATION_SHOW_NOTES, text)

    def test_episode_blocks_use_shared_structured_show_notes_result(self):
        structured = {
            "heading": "translated",
            "sections": ["统一结构化译文。"],
            "translation": {"status": "cache_hit"},
            "source_completeness": {"suspected_source_truncation": False},
        }

        with patch.object(renderer, "build_show_notes_display_result", return_value=structured) as shared:
            blocks = renderer.build_episode_blocks(
                preview_episode(),
                "Preview",
                show_notes_translation_enabled=True,
            )

        text = flatten_blocks(blocks)
        shared.assert_called_once()
        self.assertIn("节目介绍 / Show Notes（中文翻译，原文已保留）", text)
        self.assertIn("统一结构化译文", text)

    def test_summary_blocks_include_window_and_counts(self):
        blocks = renderer.build_summary_blocks(result_data(
            full=full_episode(),
            preview=preview_episode(),
            skip=skip_episode(),
        ))
        flat = flatten_blocks(blocks)

        self.assertIn("播客周报", flat)
        self.assertIn("2026W22", flat)
        self.assertIn("2026-05-24T22:00:00+0800", flat)
        self.assertIn("2026-05-31T22:00:00+0800", flat)
        self.assertIn("Full / Preview / Skip：1 / 1 / 1", flat)

    def test_full_and_preview_episode_blocks_include_core_fields_without_guest_display(self):
        episode = full_episode()
        self.assertTrue(episode["guest_names"])
        self.assertTrue(episode["guest_background_zh"])

        full_text = flatten_blocks(renderer.build_episode_blocks(episode, "Full"))
        preview_text = flatten_blocks(renderer.build_episode_blocks(preview_episode(), "Preview"))

        self.assertIn("Full Show", full_text)
        self.assertIn("AI Strategy & Capital Allocation", full_text)
        self.assertIn("9.2分", full_text)
        self.assertIn("高优先级", full_text)
        self.assertNotIn("推荐理由", full_text)
        self.assertNotIn("这期深入讨论AI战略与资本配置，值得完整转写。", full_text)
        self.assertNotIn("👤 嘉宾", full_text)
        self.assertNotIn("Jane Doe是Example Capital合伙人，专注AI投资。", full_text)
        self.assertNotIn("Example Capital bio", full_text)
        self.assertIn("节目介绍 / Show Notes（完整）", full_text)
        self.assertIn("FEISHU_NOTES_BEGIN & opening context.", full_text)
        self.assertIn("FEISHU_NOTES_MIDDLE", full_text)
        self.assertIn("FEISHU_NOTES_END", full_text)
        self.assertNotIn("Hosted by Simplecast", full_text)
        self.assertNotIn("AdsWizz", full_text)
        self.assertNotIn("podcastchoices.com/adchoices", full_text)
        self.assertIn("Preview Show", preview_text)
        self.assertIn("Market Notes & Founder Lessons", preview_text)
        self.assertIn("8.1分", preview_text)
        self.assertIn("中优先级", preview_text)
        self.assertNotIn("推荐理由", preview_text)
        self.assertNotIn("这期适合先预览，再判断是否完整转写。", preview_text)
        self.assertIn("节目介绍 / Show Notes（完整）", preview_text)
        self.assertIn("FEISHU_NOTES_BEGIN & opening context.", preview_text)
        self.assertIn("FEISHU_NOTES_MIDDLE", preview_text)
        self.assertIn("FEISHU_NOTES_END", preview_text)
        self.assertNotIn("Hosted by Simplecast", preview_text)
        self.assertNotIn("AdsWizz", preview_text)
        self.assertNotIn("podcastchoices.com/adchoices", preview_text)
        self.assertNotIn("&amp;", full_text + preview_text)
        self.assertNotIn("<p>", full_text + preview_text)
        self.assertNotIn("{'title':", full_text + preview_text)

    def test_skip_blocks_include_title_and_reason(self):
        text = flatten_blocks(renderer.build_skip_blocks(skip_episode()))

        self.assertIn("Low Signal Show", text)
        self.assertIn("Weekly News & Ads", text)
        self.assertIn("skip", text)
        self.assertIn("跳过理由：广告和泛泛新闻回顾较多，信息密度不足。", text)
        self.assertIn("节目介绍 / Show Notes（完整）", text)
        self.assertIn("FEISHU_NOTES_BEGIN & opening context.", text)
        self.assertIn("FEISHU_NOTES_MIDDLE", text)
        self.assertIn("FEISHU_NOTES_END", text)
        self.assertNotIn("Hosted by Simplecast", text)
        self.assertNotIn("AdsWizz", text)
        self.assertNotIn("podcastchoices.com/adchoices", text)
        self.assertNotIn("&amp;", text)
        self.assertNotIn("<p>", text)

    def test_skip_blocks_prefer_structured_decision_reason(self):
        episode = skip_episode()
        episode.update({
            "reason": "内容较短，优先预览即可。",
            "reason_zh": "内容较短，优先预览即可。",
            "decision_reason_code": "below_minimum_duration",
            "decision_reason_zh": "节目时长低于最低 5 分钟门槛。",
        })

        text = flatten_blocks(renderer.build_skip_blocks(episode))

        self.assertIn("跳过理由：节目时长低于最低 5 分钟门槛。", text)
        self.assertNotIn("优先预览即可", text)

    def test_empty_show_notes_uses_placeholder(self):
        ep = full_episode()
        ep["show_notes_text"] = ""
        ep["show_notes"] = ""
        ep["description"] = ""
        ep["summary_3_sentences_cn"] = []
        ep["one_line_summary_cn"] = ""

        text = flatten_blocks(renderer.build_episode_blocks(ep, "Full"))

        self.assertIn("节目介绍 / Show Notes（完整）", text)
        self.assertIn(EMPTY_SHOW_NOTES_PLACEHOLDER, text)

    def test_fallback_background_is_not_displayed(self):
        episode = fallback_episode()
        self.assertTrue(episode["guest_names"])
        self.assertTrue(episode["guest_background_zh"])

        text = flatten_blocks(renderer.build_episode_blocks(episode, "Preview"))

        self.assertNotIn(CONFIRMED_GUEST_FALLBACK, text)
        self.assertNotIn("👤 嘉宾", text)
        self.assertNotIn("职务/头衔", text)
        self.assertNotIn("{'title':", text)
        self.assertNotIn("[来源1]", text)

    def test_build_feishu_blocks_includes_empty_placeholders_without_report_appendix(self):
        blocks = renderer.build_feishu_blocks(result_data(), report_md="# 原始报告\n\n- one item")
        flat = flatten_blocks(blocks)

        self.assertTrue(all(isinstance(block, dict) for block in blocks))
        self.assertIn("本周无 Full 推荐", flat)
        self.assertIn("本周无 Preview 推荐", flat)
        self.assertIn("本周无 Skip", flat)
        self.assertNotIn("原始报告", flat)
        self.assertNotIn("one item", flat)

    def test_feishu_blocks_default_do_not_translate_show_notes(self):
        blocks = renderer.build_feishu_blocks(result_data(full=english_translation_episode()))
        flat = flatten_blocks(blocks)

        self.assertIn("节目介绍 / Show Notes（完整）", flat)
        self.assertNotIn("节目介绍 / Show Notes（中文翻译，原文已保留）", flat)
        self.assertNotIn("【中文翻译/mock】", flat)
        self.assertNotIn("推荐理由", flat)
        self.assertNotIn("原始报告", flat)

    def test_feishu_blocks_can_explicitly_translate_show_notes(self):
        blocks = renderer.build_feishu_blocks(
            result_data(full=english_translation_episode()),
            show_notes_translation_enabled=True,
            show_notes_translation_options={
                "cache_enabled": False,
                "translate_chunk": mock_translate_show_notes_chunk,
            },
        )
        flat = flatten_blocks(blocks)

        self.assertIn("节目介绍 / Show Notes（中文翻译，原文已保留）", flat)
        self.assertIn("【中文翻译/mock】", flat)
        self.assertNotIn("节目介绍 / Show Notes（完整）", flat)
        self.assertIn("Decoder with Nilay Patel", flat)
        self.assertIn("9.2分", flat)

    def test_feishu_keeps_translated_body_when_resource_titles_fall_back(self):
        episode = english_translation_episode()
        episode["show_notes_text"] = (
            "This episode discusses institutional patterns, artificial intelligence "
            "policy, and the future of public systems.\n\n"
            "Links:\n\n"
            "The Artificial State | The New Yorker"
        )

        def partial_runner(chunk, target_language="zh"):
            if chunk.startswith("Links:"):
                return chunk
            return "本期节目讨论制度模式、人工智能政策与公共系统的未来。"

        blocks = renderer.build_feishu_blocks(
            result_data(full=episode),
            show_notes_translation_enabled=True,
            show_notes_translation_options={
                "cache_enabled": False,
                "translate_chunk": partial_runner,
                "validate_translation_completeness": True,
                "max_translation_attempts": 2,
            },
        )
        flat = flatten_blocks(blocks)

        self.assertIn("节目介绍 / Show Notes（中文翻译，原文已保留）", flat)
        self.assertIn("本期节目讨论制度模式", flat)
        self.assertIn("延伸阅读（原文）：", flat)
        self.assertIn("The Artificial State | The New Yorker", flat)
        self.assertNotIn("节目介绍 / Show Notes（完整）", flat)
        self.assertNotIn("推荐理由", flat)
        self.assertNotIn("原始报告", flat)

    def test_mock_policy_can_enable_feishu_show_notes_translation(self):
        policy = {
            "show_notes_translation": {
                "enabled": True,
                "mode": "mock",
                "target_language": "zh",
                "cache_enabled": False,
                "model": "mock-show-notes-translator-v1",
                "max_chunk_chars": 1800,
            }
        }
        enabled, options = podcast_screener.build_show_notes_translation_render_options(policy)
        blocks = renderer.build_feishu_blocks(
            result_data(full=english_translation_episode()),
            show_notes_translation_enabled=enabled,
            show_notes_translation_options=options,
        )
        flat = flatten_blocks(blocks)

        self.assertTrue(enabled)
        self.assertIn("节目介绍 / Show Notes（中文翻译，原文已保留）", flat)
        self.assertIn("【中文翻译/mock】", flat)

    def test_openclaw_policy_can_enable_feishu_translation_with_fake_runner(self):
        calls = []

        def fake_openclaw_translate_chunk(chunk, target_language="zh"):
            calls.append((chunk, target_language))
            return "【OpenClaw fake】这是通过 OpenClaw fake runner 返回的译文。"

        policy = {
            "show_notes_translation": {
                "enabled": True,
                "mode": "openclaw",
                "target_language": "zh",
                "cache_enabled": False,
                "model": "minimax-portal/MiniMax-M2.7",
                "max_chunk_chars": 1800,
                "timeout_seconds": 120,
            }
        }
        enabled, options = podcast_screener.build_show_notes_translation_render_options(
            policy,
            openclaw_translate_chunk=fake_openclaw_translate_chunk,
        )
        blocks = renderer.build_feishu_blocks(
            result_data(full=english_translation_episode()),
            show_notes_translation_enabled=enabled,
            show_notes_translation_options=options,
        )
        flat = flatten_blocks(blocks)

        self.assertTrue(enabled)
        self.assertTrue(options["validate_translation_completeness"])
        self.assertEqual(options["max_translation_attempts"], 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], "zh")
        self.assertIn("节目介绍 / Show Notes（中文翻译，原文已保留）", flat)
        self.assertIn("【OpenClaw fake】", flat)
        self.assertNotIn("【中文翻译/mock】", flat)

    def test_non_mock_policy_keeps_feishu_translation_disabled(self):
        enabled, options = podcast_screener.build_show_notes_translation_render_options({
            "show_notes_translation": {"enabled": True, "mode": "real"}
        })
        blocks = renderer.build_feishu_blocks(
            result_data(full=english_translation_episode()),
            show_notes_translation_enabled=enabled,
            show_notes_translation_options=options,
        )
        flat = flatten_blocks(blocks)

        self.assertFalse(enabled)
        self.assertEqual(options, {})
        self.assertIn("节目介绍 / Show Notes（完整）", flat)
        self.assertNotIn("【中文翻译/mock】", flat)

    def test_feishu_blocks_keep_chinese_show_notes_untranslated_when_enabled(self):
        blocks = renderer.build_feishu_blocks(
            result_data(full=chinese_show_notes_episode()),
            show_notes_translation_enabled=True,
            show_notes_translation_options={
                "cache_enabled": False,
                "translate_chunk": mock_translate_show_notes_chunk,
            },
        )
        flat = flatten_blocks(blocks)

        self.assertIn("节目介绍 / Show Notes（完整）", flat)
        self.assertIn("本期节目讨论电力市场", flat)
        self.assertNotIn("节目介绍 / Show Notes（中文翻译，原文已保留）", flat)

    def test_feishu_blocks_translation_cache_hit_uses_same_heading(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            first = renderer.build_feishu_blocks(
                result_data(full=english_translation_episode()),
                show_notes_translation_enabled=True,
                show_notes_translation_options={
                    "cache_root": tmpdir,
                    "translate_chunk": mock_translate_show_notes_chunk,
                },
            )
            second = renderer.build_feishu_blocks(
                result_data(full=english_translation_episode()),
                show_notes_translation_enabled=True,
                show_notes_translation_options={
                    "cache_root": tmpdir,
                    "translate_chunk": lambda chunk, target_language="zh": "SHOULD NOT RUN",
                },
            )

        first_flat = flatten_blocks(first)
        second_flat = flatten_blocks(second)
        self.assertIn("节目介绍 / Show Notes（中文翻译，原文已保留）", first_flat)
        self.assertIn("节目介绍 / Show Notes（中文翻译，原文已保留）", second_flat)
        self.assertIn("【中文翻译/mock】", second_flat)

    def test_renderer_has_no_feishu_or_state_side_effects(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            before = set(os.listdir(tmpdir))
            with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
                blocks = renderer.build_feishu_blocks(
                    result_data(full=full_episode(), preview=preview_episode(), skip=skip_episode()),
                    report_md="# golden",
                )
            after = set(os.listdir(tmpdir))

        self.assertGreater(len(blocks), 0)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
