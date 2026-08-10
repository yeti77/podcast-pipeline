#!/usr/bin/env python3
"""
Golden-style rendering tests for Markdown episode rows and Feishu blocks.

These tests call pure rendering helpers only. They do not run the screener main
pipeline, delivery main path, Feishu APIs, RSS fetches, or guest searches.
"""

import json
from pathlib import Path
import unittest
from unittest import mock
import yaml

import deliver_weekly_report_to_feishu as delivery
import podcast_screener
from show_notes_translation_runner import mock_translate_show_notes_chunk


CONFIRMED_GUEST_FALLBACK = (
    "已确认本期嘉宾，但节目元数据未提供足够背景信息，暂不补充背景。"
)
LONG_SHOW_NOTES = (
    "<p>SHOW_NOTES_BEGIN &amp; opening context.</p>"
    "<p>SHOW_NOTES_MIDDLE discusses capital allocation, AI strategy, and operating decisions "
    "with enough detail to prove the report is not summarizing this section.</p>"
    "<p>Hosted by Simplecast, an AdsWizz company. See pcm.adswizz.com for information about privacy.</p>"
    "<p>Learn more about your ad choices. Visit podcastchoices.com/adchoices</p>"
    "<p>SHOW_NOTES_END closes the original episode description.</p>"
)
LONG_ENGLISH_TRANSLATION_SHOW_NOTES = (
    "Today, I am talking with Skydio CEO Adam Bry about drones, artificial intelligence, "
    "government work, public safety, defense procurement, supply chains, and Chinese competition. "
    "The conversation explains how autonomous systems are changing infrastructure, robotics, "
    "airspace policy, manufacturing strategy, and the future of American technology companies."
)
EMPTY_SHOW_NOTES_PLACEHOLDER = "暂无节目介绍。"


def flatten_block_text(blocks):
    return json.dumps(blocks, ensure_ascii=False)


def preview_episode():
    return {
        "podcast_name": "Test Podcast",
        "podcast_id": "test_podcast",
        "episode_title": "AI Strategy &amp; Capital Allocation",
        "duration_minutes": 42,
        "score": 8.7,
        "final_score": 8.7,
        "priority": "high",
        "full_suggestion": "maybe",
        "selection_policy_mode": "all_preview",
        "pub_datetime": "2026-05-28T09:00:00+0800",
        "reason": "本期讨论AI战略、资本配置和组织决策，值得预览。",
        "reason_zh": "本期讨论AI战略、资本配置和组织决策，值得预览。",
        "why_important": "本期讨论AI战略、资本配置和组织决策，值得预览。",
        "one_line_summary_cn": "本期讨论AI战略与资本配置。",
        "summary_3_sentences_cn": ["本期讨论AI战略与资本配置。"],
        "show_notes_text": LONG_SHOW_NOTES,
        "key_points_cn": [],
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


def skip_episode():
    return {
        "podcast_name": "Low Signal Show",
        "episode_title": "Weekly News &amp; Ads",
        "duration_minutes": 12,
        "score": 2.0,
        "final_score": 2.0,
        "priority": "low",
        "full_suggestion": "no",
        "pub_datetime": "2026-05-29T09:00:00+0800",
        "reason": "广告和泛泛新闻回顾较多，信息密度不足。",
        "reason_zh": "广告和泛泛新闻回顾较多，信息密度不足。",
        "one_line_summary_cn": "本期主要是新闻回顾和广告。",
        "show_notes_text": LONG_SHOW_NOTES,
    }


def fallback_guest_episode():
    ep = preview_episode()
    ep.update({
        "episode_title": "Founder Roundtable &amp; Market Notes",
        "guest_names": ["Guest A", "Guest B"],
        "guest_background_zh": CONFIRMED_GUEST_FALLBACK,
        "guest_background_sources": [
            {"title": "节目元数据", "snippet": "职务/头衔：founder", "url": ""}
        ],
    })
    return ep


def markdown_preview_episode():
    ep = preview_episode()
    # _fmt_ep receives records after podcast_screener has normalized
    # episode_title during result construction.
    ep["episode_title"] = podcast_screener.clean_display_text(ep["episode_title"])
    return ep


def markdown_skip_episode():
    ep = skip_episode()
    # _fmt_skip_ep receives records after podcast_screener has normalized
    # episode_title during result construction.
    ep["episode_title"] = podcast_screener.clean_display_text(ep["episode_title"])
    return ep


def markdown_full_episode():
    ep = markdown_preview_episode()
    ep.update({
        "episode_title": "Full Transcript Candidate",
        "full_suggestion": "yes",
        "decision": "full",
        "reason": "本期信息密度高，适合完整转写。",
        "reason_zh": "本期信息密度高，适合完整转写。",
        "why_important": "本期信息密度高，适合完整转写。",
        "final_score": 92,
        "score": 92,
        "priority": "high",
    })
    return ep


def english_translation_episode():
    ep = markdown_preview_episode()
    ep.update({
        "podcast_name": "Decoder with Nilay Patel",
        "podcast_id": "decoder",
        "episode_title": "Skydio CEO argues more drones will make us safer",
        "show_notes_text": LONG_ENGLISH_TRANSLATION_SHOW_NOTES,
        "summary_3_sentences_cn": ["本期讨论无人机、AI和公共安全。"],
        "one_line_summary_cn": "本期讨论无人机、AI和公共安全。",
    })
    return ep


def result_data(full=None, preview=None, skip=None):
    full_items = [] if full is None else [full]
    preview_items = [preview if preview is not None else preview_episode()]
    skip_items = [skip if skip is not None else skip_episode()]
    return {
        "run_id": "run_golden_001",
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


class TestMarkdownEpisodeRenderingGolden(unittest.TestCase):
    def test_short_episode_duration_uses_exact_seconds_and_label(self):
        cases = [
            (113, "1分53秒（短节目）"),
            (367, "6分07秒（短节目）"),
            (750, "12分30秒（短节目）"),
        ]

        for seconds, expected in cases:
            with self.subTest(seconds=seconds):
                episode = markdown_preview_episode()
                episode["duration_seconds"] = seconds
                episode["duration_minutes"] = seconds // 60
                text = "\n".join(podcast_screener._fmt_ep(episode, "Preview"))
                self.assertIn(expected, text)

    def test_long_and_legacy_episode_duration_display_stays_compact(self):
        long_episode = markdown_preview_episode()
        long_episode["duration_seconds"] = 3907
        long_episode["duration_minutes"] = 65
        long_text = "\n".join(podcast_screener._fmt_ep(long_episode, "Preview"))

        legacy_episode = markdown_preview_episode()
        legacy_episode["duration_minutes"] = 12
        legacy_episode.pop("duration_seconds", None)
        legacy_text = "\n".join(podcast_screener._fmt_ep(legacy_episode, "Preview"))

        self.assertIn("65分钟", long_text)
        self.assertNotIn("短节目", long_text)
        self.assertIn("12分钟", legacy_text)
        self.assertNotIn("短节目", legacy_text)

    def test_markdown_report_metadata_sink_receives_each_episode_diagnostics(self):
        captured = []

        podcast_screener.build_markdown_report(
            result_data(preview=english_translation_episode()),
            podcast_count=2,
            show_notes_metadata_sink=lambda episode, metadata: captured.append(
                (episode["podcast_name"], metadata)
            ),
        )

        self.assertEqual([item[0] for item in captured], ["Decoder with Nilay Patel", "Low Signal Show"])
        for _, metadata in captured:
            self.assertIn(metadata["heading"], {"full", "translated"})
            self.assertEqual(metadata["translation"]["status"], "disabled")
            self.assertIn("source_completeness", metadata)
            self.assertIn("display_filter", metadata)
            self.assertNotIn("translated_text", metadata["translation"])

    def test_production_metadata_collector_persists_diagnostics_on_episode_record(self):
        data = result_data(preview=english_translation_episode())

        podcast_screener.build_markdown_report(
            data,
            podcast_count=2,
            show_notes_metadata_sink=podcast_screener.store_show_notes_display_metadata,
        )

        metadata = data["preview"][0]["show_notes_display_metadata"]
        snapshot = data["preview"][0]["show_notes_display_snapshot"]
        self.assertEqual(metadata["translation"]["status"], "disabled")
        self.assertIn("source_completeness", metadata)
        self.assertIn("display_filter", metadata)
        self.assertIn("removed_category_counts", metadata["display_filter"])
        serialized = json.dumps(data, ensure_ascii=False)
        self.assertIn("show_notes_display_metadata", serialized)
        self.assertEqual(snapshot["version"], "show_notes_display_v1")
        self.assertEqual(snapshot["heading"], "full")
        self.assertTrue(snapshot["sections"])
        self.assertIn("Today, I am talking", "\n".join(snapshot["sections"]))
        self.assertEqual(
            data["preview"][0]["show_notes_text"],
            LONG_ENGLISH_TRANSLATION_SHOW_NOTES,
        )
        self.assertNotIn("translated_text", serialized)
        self.assertNotIn("Hosted by Simplecast", json.dumps(metadata, ensure_ascii=False))

    def test_runtime_metadata_contains_commit_and_safe_translation_config(self):
        policy = {
            "show_notes_translation": {
                "enabled": True,
                "mode": "openclaw",
                "agent_id": "main",
                "model": "minimax-portal/MiniMax-M2.7",
                "secret_token": "must-not-leak",
                "cache_dir": "private/cache/path",
            }
        }

        metadata = podcast_screener.build_runtime_metadata(policy, git_commit="abc123")

        self.assertEqual(metadata["git_commit"], "abc123")
        self.assertEqual(
            metadata["show_notes_translation"],
            {
                "enabled": True,
                "mode": "openclaw",
                "agent_id": "main",
                "model": "minimax-portal/MiniMax-M2.7",
            },
        )
        self.assertNotIn("must-not-leak", json.dumps(metadata))
        self.assertNotIn("private/cache/path", json.dumps(metadata))

    def test_translation_summary_aggregates_visible_and_failed_episodes(self):
        translated = english_translation_episode()
        translated["show_notes_display_metadata"] = {
            "translation": {
                "status": "translated",
                "should_translate": True,
                "cache_hit": False,
            }
        }
        partial = english_translation_episode()
        partial["episode_title"] = "Partial resources"
        partial["show_notes_display_metadata"] = {
            "translation": {
                "status": "partial_cache_hit",
                "should_translate": True,
                "cache_hit": True,
            }
        }
        failed = english_translation_episode()
        failed["episode_title"] = "Failed English notes"
        failed["show_notes_display_metadata"] = {
            "translation": {
                "status": "partial_failed",
                "should_translate": True,
                "cache_hit": False,
            }
        }
        chinese = preview_episode()
        chinese["episode_title"] = "中文节目"
        chinese["show_notes_display_metadata"] = {
            "translation": {
                "status": "skipped",
                "should_translate": False,
                "cache_hit": False,
            }
        }
        data = {
            "full": [],
            "preview": [translated, partial, failed, chinese],
            "skip": [],
        }

        summary = podcast_screener.build_show_notes_translation_summary(data)

        self.assertEqual(summary["episode_count"], 4)
        self.assertEqual(summary["eligible_count"], 3)
        self.assertEqual(summary["translated_count"], 1)
        self.assertEqual(summary["partial_count"], 1)
        self.assertEqual(summary["failed_count"], 1)
        self.assertEqual(summary["skipped_count"], 1)
        self.assertEqual(summary["cache_hit_count"], 1)
        self.assertEqual(summary["visible_translation_count"], 2)
        self.assertEqual(summary["failed_episodes"][0]["title"], "Failed English notes")

    def test_current_git_commit_uses_bounded_injected_command(self):
        completed = mock.Mock(returncode=0, stdout="abc123def456\n")
        run_command = mock.Mock(return_value=completed)

        value = podcast_screener.get_current_git_commit(run_command=run_command)

        self.assertEqual(value, "abc123def456")
        args, kwargs = run_command.call_args
        self.assertEqual(args[0], ["git", "rev-parse", "--short=12", "HEAD"])
        self.assertEqual(kwargs["timeout"], 5)
        self.assertFalse(kwargs["check"])

    def test_fmt_ep_preview_contains_summary_and_show_notes_without_guest_display(self):
        episode = markdown_preview_episode()
        self.assertTrue(episode["guest_names"])
        self.assertTrue(episode["guest_background_zh"])

        text = "\n".join(podcast_screener._fmt_ep(episode, "Preview"))

        self.assertIn("Test Podcast", text)
        self.assertIn("AI Strategy & Capital Allocation", text)
        self.assertNotIn("&amp;", text)
        self.assertIn("**概述：**", text)
        self.assertIn("本期讨论AI战略与资本配置。", text)
        self.assertNotIn("**推荐理由：**", text)
        self.assertNotIn("本期讨论AI战略、资本配置和组织决策，值得预览。", text)
        self.assertNotIn("👤 嘉宾：", text)
        self.assertNotIn("Jane Doe是Example Capital合伙人，专注AI投资。", text)
        self.assertNotIn("Example Capital bio", text)
        self.assertIn("**节目介绍 / Show Notes（完整）：**", text)
        self.assertIn("SHOW_NOTES_BEGIN & opening context.", text)
        self.assertIn("SHOW_NOTES_MIDDLE", text)
        self.assertIn("SHOW_NOTES_END", text)
        self.assertNotIn("Hosted by Simplecast", text)
        self.assertNotIn("AdsWizz", text)
        self.assertNotIn("podcastchoices.com/adchoices", text)
        self.assertNotIn("{'title':", text)

    def test_fmt_ep_fallback_guest_is_not_displayed(self):
        episode = fallback_guest_episode()
        self.assertTrue(episode["guest_names"])
        self.assertTrue(episode["guest_background_zh"])

        text = "\n".join(podcast_screener._fmt_ep(episode, "Preview"))

        self.assertNotIn(CONFIRMED_GUEST_FALLBACK, text)
        self.assertNotIn("职务/头衔", text)
        self.assertNotIn("{'title':", text)
        self.assertNotIn("👤 嘉宾：", text)

    def test_fmt_skip_ep_contains_title_summary_and_reason(self):
        text = "\n".join(podcast_screener._fmt_skip_ep(markdown_skip_episode()))

        self.assertIn("Low Signal Show", text)
        self.assertIn("Weekly News & Ads", text)
        self.assertNotIn("&amp;", text)
        self.assertIn("**概述：**", text)
        self.assertIn("本期主要是新闻回顾和广告。", text)
        self.assertIn("**跳过理由：**", text)
        self.assertIn("广告和泛泛新闻回顾较多，信息密度不足。", text)
        self.assertIn("**节目介绍 / Show Notes（完整）：**", text)
        self.assertIn("SHOW_NOTES_BEGIN & opening context.", text)
        self.assertIn("SHOW_NOTES_MIDDLE", text)
        self.assertIn("SHOW_NOTES_END", text)
        self.assertNotIn("Hosted by Simplecast", text)
        self.assertNotIn("AdsWizz", text)
        self.assertNotIn("podcastchoices.com/adchoices", text)
        self.assertNotIn("{'title':", text)

    def test_fmt_skip_ep_prefers_structured_decision_reason(self):
        episode = markdown_skip_episode()
        episode.update({
            "reason": "内容较短，优先预览即可。",
            "reason_zh": "内容较短，优先预览即可。",
            "decision_reason_code": "below_minimum_duration",
            "decision_reason_zh": "节目时长低于最低 5 分钟门槛。",
        })

        text = "\n".join(podcast_screener._fmt_skip_ep(episode))

        self.assertIn("**跳过理由：**节目时长低于最低 5 分钟门槛。", text)
        self.assertNotIn("优先预览即可", text)

    def test_fmt_ep_empty_show_notes_uses_placeholder(self):
        ep = markdown_preview_episode()
        ep["show_notes_text"] = ""
        ep["show_notes"] = ""
        ep["description"] = ""
        ep["summary_3_sentences_cn"] = []
        ep["one_line_summary_cn"] = ""

        text = "\n".join(podcast_screener._fmt_ep(ep, "Preview"))

        self.assertIn("**节目介绍 / Show Notes（完整）：**", text)
        self.assertIn(EMPTY_SHOW_NOTES_PLACEHOLDER, text)

    def test_build_markdown_report_contains_full_preview_skip_structure(self):
        data = result_data(
            full=markdown_full_episode(),
            preview=markdown_preview_episode(),
            skip=markdown_skip_episode(),
        )

        text = podcast_screener.build_markdown_report(data, podcast_count=3)

        self.assertIn("播客筛选报告", text)
        self.assertIn("2026W22", text)
        self.assertIn("2026-05-24 22:00", text)
        self.assertIn("2026-05-31 22:00", text)
        self.assertIn("总计扫描", text)
        self.assertIn("覆盖播客：3个", text)
        self.assertIn("本周建议完整转写（Full", text)
        self.assertIn("本周建议预览转写（Preview", text)
        self.assertIn("本周跳过（Skip", text)
        self.assertIn("Full Transcript Candidate", text)
        self.assertIn("**概述：**", text)
        self.assertIn("本期讨论AI战略与资本配置。", text)
        self.assertNotIn("**推荐理由：**", text)
        self.assertNotIn("👤 嘉宾：", text)
        self.assertNotIn("Jane Doe是Example Capital合伙人，专注AI投资。", text)
        self.assertNotIn("Example Capital bio", text)
        self.assertIn("Low Signal Show", text)
        self.assertIn("Weekly News & Ads", text)
        self.assertIn("**跳过理由：**", text)
        self.assertIn("广告和泛泛新闻回顾较多，信息密度不足。", text)
        self.assertEqual(text.count("**节目介绍 / Show Notes（完整）：**"), 3)
        self.assertIn("SHOW_NOTES_BEGIN & opening context.", text)
        self.assertIn("SHOW_NOTES_MIDDLE", text)
        self.assertIn("SHOW_NOTES_END", text)
        self.assertNotIn("Hosted by Simplecast", text)
        self.assertNotIn("AdsWizz", text)
        self.assertNotIn("podcastchoices.com/adchoices", text)
        self.assertNotIn("📝 原始报告", text)
        self.assertNotIn("&amp;", text)
        self.assertNotIn("<p>", text)
        self.assertNotIn("{'title':", text)

    def test_build_markdown_report_uses_explicit_podcast_count(self):
        text = podcast_screener.build_markdown_report(result_data(), podcast_count=99)

        self.assertIn("覆盖播客：99个", text)

    def test_build_markdown_report_default_does_not_translate_show_notes(self):
        text = podcast_screener.build_markdown_report(
            result_data(preview=english_translation_episode(), skip=None),
            podcast_count=1,
        )

        self.assertIn("**节目介绍 / Show Notes（完整）：**", text)
        self.assertNotIn("节目介绍 / Show Notes（中文翻译，原文已保留）", text)
        self.assertNotIn("【中文翻译/mock】", text)
        self.assertNotIn("**推荐理由：**", text)
        self.assertNotIn("📝 原始报告", text)

    def test_tracked_public_policy_keeps_translation_disabled(self):
        with Path("config/policy.yaml").open(encoding="utf-8") as f:
            policy = yaml.safe_load(f)

        enabled, options = podcast_screener.build_show_notes_translation_render_options(policy)

        self.assertFalse(policy["show_notes_translation"]["enabled"])
        self.assertEqual(policy["show_notes_translation"]["mode"], "mock")
        self.assertEqual(policy["show_notes_translation"]["agent_id"], "")
        self.assertEqual(policy["show_notes_translation"]["model"], "mock-show-notes-translator-v1")
        self.assertFalse(enabled)
        self.assertEqual(options, {})

    def test_missing_or_disabled_policy_keeps_translation_disabled(self):
        for policy in (None, {}, {"show_notes_translation": {}}, {"show_notes_translation": {"enabled": False}}):
            with self.subTest(policy=policy):
                enabled, options = podcast_screener.build_show_notes_translation_render_options(policy)

                self.assertFalse(enabled)
                self.assertEqual(options, {})

    def test_build_markdown_report_can_explicitly_translate_show_notes(self):
        text = podcast_screener.build_markdown_report(
            result_data(preview=english_translation_episode(), skip=None),
            podcast_count=1,
            show_notes_translation_enabled=True,
            show_notes_translation_options={
                "cache_enabled": False,
                "translate_chunk": mock_translate_show_notes_chunk,
            },
        )

        self.assertIn("Decoder with Nilay Patel", text)
        self.assertIn("Skydio CEO argues more drones will make us safer", text)
        self.assertIn("8.7分", text)
        self.assertIn("**节目介绍 / Show Notes（中文翻译，原文已保留）：**", text)
        self.assertIn("【中文翻译/mock】", text)
        self.assertNotIn("**节目介绍 / Show Notes（完整）：**", text)

    def test_markdown_keeps_translated_body_when_resource_titles_fall_back(self):
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

        text = podcast_screener.build_markdown_report(
            result_data(preview=episode, skip=None),
            podcast_count=1,
            show_notes_translation_enabled=True,
            show_notes_translation_options={
                "cache_enabled": False,
                "translate_chunk": partial_runner,
                "validate_translation_completeness": True,
                "max_translation_attempts": 2,
            },
        )

        self.assertIn("**节目介绍 / Show Notes（中文翻译，原文已保留）：**", text)
        self.assertIn("本期节目讨论制度模式", text)
        self.assertIn("延伸阅读（原文）：", text)
        self.assertIn("The Artificial State | The New Yorker", text)
        self.assertNotIn("**节目介绍 / Show Notes（完整）：**", text)
        self.assertNotIn("推荐理由", text)
        self.assertNotIn("原始报告", text)

    def test_mock_policy_can_enable_markdown_translation(self):
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

        text = podcast_screener.build_markdown_report(
            result_data(preview=english_translation_episode(), skip=None),
            podcast_count=1,
            show_notes_translation_enabled=enabled,
            show_notes_translation_options=options,
        )

        self.assertTrue(enabled)
        self.assertIn("**节目介绍 / Show Notes（中文翻译，原文已保留）：**", text)
        self.assertIn("【中文翻译/mock】", text)

    def test_openclaw_policy_can_enable_markdown_translation_with_fake_runner(self):
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

        text = podcast_screener.build_markdown_report(
            result_data(preview=english_translation_episode(), skip=None),
            podcast_count=1,
            show_notes_translation_enabled=enabled,
            show_notes_translation_options=options,
        )

        self.assertTrue(enabled)
        self.assertGreaterEqual(len(calls), 1)
        self.assertTrue(all(target_language == "zh" for _, target_language in calls))
        self.assertIn("**节目介绍 / Show Notes（中文翻译，原文已保留）：**", text)
        self.assertIn("【OpenClaw fake】", text)
        self.assertNotIn("【中文翻译/mock】", text)

    def test_openclaw_policy_passes_agent_id_to_default_runner(self):
        calls = []

        def fake_translate_chunk_with_openclaw(
            chunk,
            target_language="zh",
            *,
            model_name="",
            timeout_seconds=0,
            agent_id="",
        ):
            calls.append(
                {
                    "chunk": chunk,
                    "target_language": target_language,
                    "model_name": model_name,
                    "timeout_seconds": timeout_seconds,
                    "agent_id": agent_id,
                }
            )
            return "【OpenClaw fake】agent main translated."

        policy = {
            "show_notes_translation": {
                "enabled": True,
                "mode": "openclaw",
                "target_language": "zh",
                "cache_enabled": False,
                "model": "minimax-portal/MiniMax-M2.7",
                "agent_id": "main",
                "max_chunk_chars": 1800,
                "timeout_seconds": 120,
            }
        }
        with mock.patch.object(
            podcast_screener,
            "translate_show_notes_chunk_with_openclaw",
            side_effect=fake_translate_chunk_with_openclaw,
        ):
            enabled, options = podcast_screener.build_show_notes_translation_render_options(policy)
            translated = options["translate_chunk"]("English source chunk.", target_language="zh")

        self.assertTrue(enabled)
        self.assertEqual(translated, "【OpenClaw fake】agent main translated.")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["agent_id"], "main")
        self.assertEqual(calls[0]["model_name"], "minimax-portal/MiniMax-M2.7")
        self.assertEqual(calls[0]["timeout_seconds"], 120)

    def test_openclaw_policy_disabled_does_not_call_fake_runner(self):
        calls = []
        policy = {
            "show_notes_translation": {
                "enabled": False,
                "mode": "openclaw",
            }
        }
        enabled, options = podcast_screener.build_show_notes_translation_render_options(
            policy,
            openclaw_translate_chunk=lambda chunk, target_language="zh": calls.append(chunk),
        )

        text = podcast_screener.build_markdown_report(
            result_data(preview=english_translation_episode(), skip=None),
            podcast_count=1,
            show_notes_translation_enabled=enabled,
            show_notes_translation_options=options,
        )

        self.assertFalse(enabled)
        self.assertEqual(options, {})
        self.assertEqual(calls, [])
        self.assertIn("**节目介绍 / Show Notes（完整）：**", text)
        self.assertNotIn("节目介绍 / Show Notes（中文翻译，原文已保留）", text)

    def test_openclaw_policy_fake_failure_falls_back_to_full_show_notes(self):
        def failing_openclaw_translate_chunk(chunk, target_language="zh"):
            raise RuntimeError("fake openclaw failed")

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
            openclaw_translate_chunk=failing_openclaw_translate_chunk,
        )

        text = podcast_screener.build_markdown_report(
            result_data(preview=english_translation_episode(), skip=None),
            podcast_count=1,
            show_notes_translation_enabled=enabled,
            show_notes_translation_options=options,
        )

        self.assertTrue(enabled)
        self.assertIn("**节目介绍 / Show Notes（完整）：**", text)
        self.assertNotIn("节目介绍 / Show Notes（中文翻译，原文已保留）", text)
        self.assertNotIn("fake openclaw failed", text)

    def test_non_mock_policy_mode_does_not_enable_translation(self):
        calls = []
        policy = {"show_notes_translation": {"enabled": True, "mode": "real"}}
        enabled, options = podcast_screener.build_show_notes_translation_render_options(
            policy,
            openclaw_translate_chunk=lambda chunk, target_language="zh": calls.append(chunk),
        )
        text = podcast_screener.build_markdown_report(
            result_data(preview=english_translation_episode(), skip=None),
            podcast_count=1,
            show_notes_translation_enabled=enabled,
            show_notes_translation_options=options,
        )

        self.assertFalse(enabled)
        self.assertEqual(options, {})
        self.assertEqual(calls, [])
        self.assertIn("**节目介绍 / Show Notes（完整）：**", text)
        self.assertNotIn("【中文翻译/mock】", text)

    def test_build_markdown_report_translation_failure_falls_back_to_full_show_notes(self):
        def failing_runner(chunk, target_language="zh"):
            raise RuntimeError("boom")

        text = podcast_screener.build_markdown_report(
            result_data(preview=english_translation_episode(), skip=None),
            podcast_count=1,
            show_notes_translation_enabled=True,
            show_notes_translation_options={
                "cache_enabled": False,
                "translate_chunk": failing_runner,
            },
        )

        self.assertIn("**节目介绍 / Show Notes（完整）：**", text)
        self.assertNotIn("节目介绍 / Show Notes（中文翻译，原文已保留）", text)
        self.assertNotIn("RuntimeError", text)


class TestFeishuBlocksRenderingGolden(unittest.TestCase):
    def test_build_blocks_contains_weekly_structure_and_episode_content(self):
        blocks = delivery.build_blocks(result_data(), report_md="# golden")
        flat = flatten_block_text(blocks)

        self.assertIsInstance(blocks, list)
        self.assertGreater(len(blocks), 0)
        self.assertTrue(all(isinstance(block, dict) for block in blocks))
        self.assertIn("播客周报", flat)
        self.assertIn("2026W22", flat)
        self.assertIn("2026-05-24T22:00:00+0800", flat)
        self.assertIn("2026-05-31T22:00:00+0800", flat)
        self.assertIn("Full / Preview / Skip", flat)
        self.assertIn("Test Podcast", flat)
        self.assertIn("AI Strategy & Capital Allocation", flat)
        self.assertNotIn("推荐理由", flat)
        self.assertNotIn("本期讨论AI战略、资本配置和组织决策，值得预览。", flat)
        self.assertNotIn("👤 嘉宾", flat)
        self.assertNotIn("Jane Doe是Example Capital合伙人，专注AI投资。", flat)
        self.assertNotIn("Example Capital bio", flat)
        self.assertIn("Low Signal Show", flat)
        self.assertIn("Weekly News & Ads", flat)
        self.assertIn("跳过理由", flat)
        self.assertIn("广告和泛泛新闻回顾较多，信息密度不足。", flat)
        self.assertNotIn("📝 原始报告", flat)
        self.assertNotIn("原始报告", flat)
        self.assertNotIn("&amp;", flat)
        self.assertNotIn("{'title':", flat)

    def test_build_blocks_omit_fallback_guest_display(self):
        blocks = delivery.build_blocks(
            result_data(preview=fallback_guest_episode(), skip=skip_episode()),
            report_md="# golden",
        )
        flat = flatten_block_text(blocks)

        self.assertNotIn(CONFIRMED_GUEST_FALLBACK, flat)
        self.assertNotIn("👤 嘉宾", flat)
        self.assertNotIn("职务/头衔", flat)
        self.assertNotIn("{'title':", flat)


if __name__ == "__main__":
    unittest.main(verbosity=2)
