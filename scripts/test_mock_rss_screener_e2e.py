#!/usr/bin/env python3
"""
Hermetic mock RSS screener fixture.

This is an E2E-like test for the safe, local parts of the screener chain:
mock RSS XML -> parse -> business-week filter -> dedup -> all_preview/skip
decision -> result-like structure -> Markdown episode rendering.

It does not run podcast_screener.py main, fetch RSS, call guest search, or write
real outputs/state/cache.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import podcast_screener as screener


TZ_SH = ZoneInfo("Asia/Shanghai")
WINDOW_START = datetime.fromisoformat("2026-05-24T22:00:00+08:00").replace(tzinfo=TZ_SH)
WINDOW_END = datetime.fromisoformat("2026-05-31T22:00:00+08:00").replace(tzinfo=TZ_SH)


INTERESTS = {
    "boost_keywords": ["AI", "capital allocation", "strategy"],
    "negative_keywords": [],
    "primary_topics": ["AI", "strategy"],
    "important_people": ["Jane Doe"],
}


POLICY = {
    "selection_policy": {
        "mode": "all_preview",
        "min_duration_minutes": 5,
    },
    "ad_detection_policy": {
        "keywords_en": ["sponsor", "brought to you by"],
        "keywords_zh": ["广告"],
    },
    "score_policy": {
        "full_threshold": 80,
        "preview_threshold": 30,
        "skip_threshold": 10,
    },
}


def mock_rss_xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Mock Screener Feed</title>
    <item>
      <title><![CDATA[AI Strategy &amp; Capital Allocation with Jane Doe]]></title>
      <pubDate>Wed, 27 May 2026 02:00:00 GMT</pubDate>
      <itunes:duration>00:42:00</itunes:duration>
      <description><![CDATA[
        Jane Doe joins the show to discuss AI strategy, capital allocation,
        portfolio construction, and operating decisions for investors.
        This episode contains enough detail for a preview decision.
      ]]></description>
      <enclosure url="https://example.test/audio-preview.mp3" type="audio/mpeg"/>
    </item>
    <item>
      <title><![CDATA[AI Strategy &amp; Capital Allocation with Jane Doe]]></title>
      <pubDate>Wed, 27 May 2026 02:00:00 GMT</pubDate>
      <itunes:duration>00:42:00</itunes:duration>
      <description><![CDATA[
        Duplicate copy of the same episode. It should be removed by the same
        episode_id rule used by the production screener.
      ]]></description>
      <enclosure url="https://example.test/audio-preview-duplicate.mp3" type="audio/mpeg"/>
    </item>
    <item>
      <title>Weekly News &amp; Sponsor Break</title>
      <pubDate>Thu, 28 May 2026 03:00:00 GMT</pubDate>
      <itunes:duration>00:12:00</itunes:duration>
      <description><![CDATA[
        This short update is brought to you by a sponsor and contains sponsor
        messages instead of substantive analysis.
      ]]></description>
      <enclosure url="https://example.test/audio-ad.mp3" type="audio/mpeg"/>
    </item>
    <item>
      <title>Outside Window Episode</title>
      <pubDate>Mon, 01 Jun 2026 03:00:00 GMT</pubDate>
      <itunes:duration>00:30:00</itunes:duration>
      <description>Outside the target business week.</description>
      <enclosure url="https://example.test/audio-outside.mp3" type="audio/mpeg"/>
    </item>
  </channel>
</rss>
"""


def dedupe_like_screener(episodes, podcast_id):
    seen_ids = set()
    deduped = []
    for ep in episodes:
        ep_id = screener.make_episode_id(
            podcast_id,
            ep["publish_date"][:10],
            ep["episode_title"],
        )
        if ep_id in seen_ids:
            continue
        seen_ids.add(ep_id)
        deduped.append(ep)
    return deduped


def build_result_like_record(ep, podcast_id):
    scoring = screener.score_episode_structured(ep, INTERESTS, POLICY)
    decision = screener.decide_in_all_preview_mode(ep, scoring, POLICY)
    title = screener.clean_display_text(ep["episode_title"])
    record = {
        "podcast_name": ep["podcast_name"],
        "podcast_id": podcast_id,
        "episode_title": title,
        "episode_id": screener.make_episode_id(podcast_id, ep["publish_date"][:10], title),
        "publish_date": ep["publish_date"],
        "publish_at": ep["pub_datetime"],
        "pub_datetime": ep["pub_datetime"],
        "duration_minutes": ep["duration_minutes"],
        "audio_url": ep["audio_url"],
        "score": scoring["final_score"],
        "final_score": scoring["final_score"],
        "decision": decision,
        "reason": scoring["reason_zh"],
        "reason_zh": scoring["reason_zh"],
        "why_important": scoring["reason_zh"],
        "uncertainty_zh": scoring["uncertainty_zh"],
        "one_line_summary_cn": scoring.get("one_line_summary_cn", ""),
        "summary_3_sentences_cn": [
            f"{ep['podcast_name']}本期主题为「{title}」。"
        ],
        "key_points_cn": [],
        "show_notes_text": ep["show_notes"],
        "show_notes_source": ep["show_notes_source"],
        "show_notes_text_len": ep["show_notes_text_len"],
        "show_notes_truncated": ep["show_notes_truncated"],
        "selection_policy_mode": "all_preview",
        "priority": "medium",
        "full_suggestion": "no",
        "topic_relevance": scoring["topic_relevance"],
        "information_density": scoring["information_density"],
        "novelty": scoring["novelty"],
        "actionability": scoring["actionability"],
        "strategic_value": scoring["strategic_value"],
        "transcription_value": scoring["transcription_value"],
        "guest_detection_status": "no_guest_detected",
        "guest_names": [],
        "guest_background_zh": "",
        "guest_background_sources": [],
    }
    if "Jane Doe" in title:
        record.update({
            "guest_detection_status": "confirmed_guest",
            "guest_names": ["Jane Doe"],
            "guest_background_zh": screener.CONFIRMED_GUEST_FALLBACK,
            "guest_background_sources": [
                {"title": "节目元数据", "snippet": "职务/头衔：founder", "url": ""}
            ],
        })
    return record


def build_mock_result_like(run_id="mock_run_001"):
    parsed = screener.parse_rss_episodes(mock_rss_xml(), "Mock Screener Feed")
    in_window = [
        ep for ep in parsed
        if screener.episode_in_window(ep, WINDOW_START, WINDOW_END)
    ]
    deduped = dedupe_like_screener(in_window, "mock_feed")
    records = [build_result_like_record(ep, "mock_feed") for ep in deduped]
    preview = [r for r in records if r["decision"] == "preview"]
    skip = [r for r in records if r["decision"] == "skip"]
    return {
        "run_id": run_id,
        "week_id": "2026W22",
        "window_start": WINDOW_START.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "window_end": WINDOW_END.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "full": [],
        "preview": preview,
        "skip": skip,
    }


class TestMockRssScreenerE2E(unittest.TestCase):
    def test_mock_rss_to_result_like_structure_and_markdown(self):
        parsed = screener.parse_rss_episodes(mock_rss_xml(), "Mock Screener Feed")
        self.assertEqual(len(parsed), 4)
        self.assertTrue(all(ep.get("audio_url") for ep in parsed))

        in_window = [
            ep for ep in parsed
            if screener.episode_in_window(ep, WINDOW_START, WINDOW_END)
        ]
        self.assertEqual(len(in_window), 3)
        self.assertNotIn("Outside Window Episode", [ep["episode_title"] for ep in in_window])

        deduped = dedupe_like_screener(in_window, "mock_feed")
        self.assertEqual(len(deduped), 2)

        records = [build_result_like_record(ep, "mock_feed") for ep in deduped]
        preview = [r for r in records if r["decision"] == "preview"]
        skip = [r for r in records if r["decision"] == "skip"]

        self.assertEqual(len(preview), 1)
        self.assertEqual(len(skip), 1)
        self.assertEqual(preview[0]["episode_title"], "AI Strategy & Capital Allocation with Jane Doe")
        self.assertNotIn("&amp;", preview[0]["episode_title"])
        self.assertIn("portfolio construction", preview[0]["show_notes_text"])
        self.assertIn("Sponsor", skip[0]["episode_title"])
        self.assertIn("sponsor", skip[0]["show_notes_text"].lower())
        self.assertEqual(preview[0]["guest_detection_status"], "confirmed_guest")
        self.assertEqual(preview[0]["guest_background_zh"], screener.CONFIRMED_GUEST_FALLBACK)

        result_like = build_mock_result_like()
        self.assertEqual(result_like["week_id"], "2026W22")
        self.assertEqual(len(result_like["preview"]), 1)
        self.assertEqual(len(result_like["skip"]), 1)

        markdown = "\n".join(screener._fmt_ep(preview[0], "Preview"))
        self.assertIn("AI Strategy & Capital Allocation with Jane Doe", markdown)
        self.assertIn(f"{preview[0]['score']:.1f}分", markdown)
        self.assertIn("Preview", markdown)
        self.assertNotIn("&amp;", markdown)
        self.assertIn("**概述：**", markdown)
        self.assertIn("reason_zh", preview[0])
        self.assertTrue(preview[0]["reason_zh"])
        self.assertNotIn("推荐理由：", markdown)
        self.assertNotIn("**推荐理由：**", markdown)
        self.assertNotIn("📝 原始报告", markdown)
        self.assertNotIn("原始报告", markdown)
        self.assertNotIn(screener.CONFIRMED_GUEST_FALLBACK, markdown)
        self.assertNotIn("👤 嘉宾", markdown)
        self.assertIn("**节目介绍 / Show Notes（完整）：**", markdown)
        self.assertIn("portfolio construction", markdown)
        self.assertNotIn("职务/头衔", markdown)

    def test_mock_result_writes_run_outputs_and_latest_symlinks_in_tempdir(self):
        result_like = build_mock_result_like(run_id="run_mock_001")

        with tempfile.TemporaryDirectory(prefix="mock_screener_outputs_") as tmp:
            outputs_dir = Path(tmp) / "outputs"
            run_dir = outputs_dir / "runs" / "2026W22" / "run_mock_001"
            run_dir.mkdir(parents=True)
            result_path = run_dir / "screening_result.json"
            report_path = run_dir / "screening_report.md"

            with open(result_path, "w", encoding="utf-8") as f:
                json.dump(result_like, f, ensure_ascii=False, indent=2)
            report_text = "\n".join(screener._fmt_ep(result_like["preview"][0], "Preview"))
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(report_text)

            old_output_dir = screener.OUTPUT_DIR
            try:
                screener.OUTPUT_DIR = str(outputs_dir)
                screener.update_latest_pointers(str(run_dir), str(result_path), str(report_path))
            finally:
                screener.OUTPUT_DIR = old_output_dir

            latest_json = outputs_dir / "latest_screening_result.json"
            latest_md = outputs_dir / "latest_screening_report.md"
            self.assertTrue(result_path.exists())
            self.assertTrue(report_path.exists())
            self.assertTrue(os.path.islink(latest_json))
            self.assertTrue(os.path.islink(latest_md))
            self.assertEqual(
                os.readlink(latest_json),
                "runs/2026W22/run_mock_001/screening_result.json",
            )
            self.assertEqual(
                os.readlink(latest_md),
                "runs/2026W22/run_mock_001/screening_report.md",
            )

            with open(latest_json, encoding="utf-8") as f:
                latest_data = json.load(f)
            self.assertEqual(latest_data["run_id"], "run_mock_001")
            self.assertEqual(latest_data["week_id"], "2026W22")
            self.assertIn("AI Strategy & Capital Allocation", latest_md.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
