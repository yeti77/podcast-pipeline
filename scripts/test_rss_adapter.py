#!/usr/bin/env python3
"""Hermetic tests for the RSS network adapter."""

import os
import subprocess
import unittest
from unittest.mock import patch

import rss_adapter

with patch("os.makedirs"):
    import podcast_screener as screener


def valid_jsonld_html():
    return """
    <html><body>
    <script type="application/ld+json">
    {
      "workExample": [
        {
          "name": "Wrapper JSON-LD Episode",
          "datePublished": "2026-05-27T02:00:00",
          "duration": "PT1H02M03S",
          "description": "<p>Wrapper show notes &amp; context.</p>",
          "offers": [{"url": "https://example.test/wrapper-audio.mp3"}]
        }
      ]
    }
    </script>
    </body></html>
    """


INVALID_JSONLD_HTML = "<html><body><p>No JSON-LD workExample here.</p></body></html>"


class TestFetchFeed(unittest.TestCase):
    def test_proxy_mode_adds_proxy_argument(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="<rss/>", stderr="")

        with patch.dict(os.environ, {"https_proxy": "http://127.0.0.1:7890"}, clear=True):
            with patch.object(rss_adapter.subprocess, "run", return_value=completed) as run:
                result = rss_adapter.fetch_feed("https://example.test/feed.xml", "rss", "proxy")

        self.assertEqual(result, "<rss/>")
        run.assert_called_once_with(
            [
                "curl",
                "-sL",
                "--max-time",
                "30",
                "--proxy",
                "http://127.0.0.1:7890",
                "https://example.test/feed.xml",
            ],
            capture_output=True,
            text=True,
            timeout=40,
        )

    def test_direct_mode_does_not_add_proxy_argument(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="<rss/>", stderr="")

        with patch.dict(os.environ, {"https_proxy": "http://127.0.0.1:7890"}, clear=True):
            with patch.object(rss_adapter.subprocess, "run", return_value=completed) as run:
                result = rss_adapter.fetch_feed("https://example.test/feed.xml", "rss", "direct")

        self.assertEqual(result, "<rss/>")
        run.assert_called_once_with(
            ["curl", "-sL", "--max-time", "30", "https://example.test/feed.xml"],
            capture_output=True,
            text=True,
            timeout=40,
        )

    def test_auto_mode_keeps_current_no_proxy_behavior(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="<rss/>", stderr="")

        with patch.dict(os.environ, {"https_proxy": "http://127.0.0.1:7890"}, clear=True):
            with patch.object(rss_adapter.subprocess, "run", return_value=completed) as run:
                result = rss_adapter.fetch_feed("https://example.test/feed.xml", "rss", "auto")

        self.assertEqual(result, "<rss/>")
        run.assert_called_once_with(
            ["curl", "-sL", "--max-time", "30", "https://example.test/feed.xml"],
            capture_output=True,
            text=True,
            timeout=40,
        )

    def test_nonzero_return_keeps_stdout_current_behavior(self):
        completed = subprocess.CompletedProcess(args=[], returncode=7, stdout="partial", stderr="failed")

        with patch.dict(os.environ, {}, clear=True):
            with patch.object(rss_adapter.subprocess, "run", return_value=completed):
                result = rss_adapter.fetch_feed("https://example.test/feed.xml", "rss", "direct")

        self.assertEqual(result, "partial")

    def test_subprocess_exception_returns_empty_string_and_logs(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(rss_adapter.subprocess, "run", side_effect=TimeoutError("boom")):
                with patch.object(rss_adapter, "log_stderr") as log_stderr:
                    result = rss_adapter.fetch_feed("https://example.test/feed.xml", "rss", "direct")

        self.assertEqual(result, "")
        log_stderr.assert_called_once()
        self.assertIn("FETCH_ERROR https://example.test/feed.xml: boom", log_stderr.call_args.args[0])


class TestCleanShowNotesSpacing(unittest.TestCase):
    def test_paragraph_boundaries_do_not_join_english_sentences(self):
        raw = "<p>First sentence.</p><p>Chapters:<br/>00:00 Intro</p>"

        cleaned = rss_adapter.clean_show_notes(raw)

        self.assertRegex(cleaned, r"First sentence\.\s+Chapters:")
        self.assertNotIn("sentence.Chapters", cleaned)

    def test_br_variants_preserve_boundaries(self):
        raw = "Line one<br>Line two<br/>Line three<br />Line four"

        cleaned = rss_adapter.clean_show_notes(raw)

        self.assertIn("Line one\nLine two\nLine three\nLine four", cleaned)

    def test_list_items_preserve_boundaries(self):
        raw = "<ul><li>Apply to Y Combinator</li><li>Work at a startup</li></ul>"

        cleaned = rss_adapter.clean_show_notes(raw)

        self.assertIn("Apply to Y Combinator", cleaned)
        self.assertIn("Work at a startup", cleaned)
        self.assertNotIn("CombinatorWork", cleaned)

    def test_anchor_after_sentence_keeps_url_separator(self):
        raw = 'Flashdance.”<a href="https://imdb.test">www.imdb.com/name/nm0000390/</a>'

        cleaned = rss_adapter.clean_show_notes(raw)

        self.assertIn("Flashdance.” www.imdb.com/name/nm0000390/", cleaned)
        self.assertNotIn("Flashdance.”www", cleaned)

    def test_chapters_and_resources_keep_separators(self):
        raw = (
            "<p>Business model.</p><p>Chapters:</p>"
            "<p>00:00 Intro</p><p>Resources:</p><p>https://example.test</p>"
        )

        cleaned = rss_adapter.clean_show_notes(raw)

        self.assertRegex(cleaned, r"Business model\.\s+Chapters:")
        self.assertRegex(cleaned, r"00:00 Intro\s+Resources:")
        self.assertNotIn("model.Chapters", cleaned)
        self.assertNotIn("IntroResources", cleaned)

    def test_inline_anchor_after_label_keeps_separator(self):
        raw = 'Stay Updated:<a href="https://example.test">Find a16z on YouTube</a>'

        cleaned = rss_adapter.clean_show_notes(raw)

        self.assertIn("Stay Updated: Find a16z on YouTube", cleaned)
        self.assertNotIn("Updated:Find", cleaned)

    def test_chinese_text_and_entities_remain_readable(self):
        raw = "<p>本期讨论AI&amp;资本配置。</p><p>嘉宾：张三</p>"

        cleaned = rss_adapter.clean_show_notes(raw)

        self.assertIn("本期讨论AI&资本配置。", cleaned)
        self.assertIn("嘉宾：张三", cleaned)
        self.assertNotIn("&amp;", cleaned)
        self.assertNotIn("<p>", cleaned)

    def test_english_show_notes_realistic_spacing_fixtures(self):
        joe = (
            '<p>Joe Eszterhas is an author and screenwriter known for films such as '
            '“Basic Instinct,” “Showgirls,” and “Flashdance.”</p>'
            '<p><a href="https://www.imdb.com/name/nm0000390/">'
            'www.imdb.com/name/nm0000390/</a></p>'
        )
        devon = (
            '<p>Devon Larratt is a veteran of the Canadian Armed Forces and a professional '
            'arm wrestler who is widely considered one of the sport’s greatest competitors.</p>'
            '<p><a href="https://www.youtube.com/@devlarratt">www.youtube.com/@devlarratt</a></p>'
            '<p><a href="https://armbet.net">https://armbet.net</a></p>'
        )
        yc = (
            "<p>Some of the biggest companies of the next decade won't be software businesses.</p>"
            "<p>Chapters:</p><p>00:00 — Intro to AI Services Companies</p>"
            '<p>Apply to Y Combinator: <a href="https://www.ycombinator.com/apply">'
            'https://www.ycombinator.com/apply</a></p>'
            '<p>Work at a startup: <a href="https://www.ycombinator.com/jobs">'
            'https://www.ycombinator.com/jobs</a></p>'
        )
        a16z = (
            '<p>Stay Updated:<a href="https://youtube.com/a16z">Find a16z on YouTube</a></p>'
            '<p>Hosted by Simplecast, an AdsWizz company. See pcm.adswizz.com for information.</p>'
        )

        joe_cleaned = rss_adapter.clean_show_notes(joe)
        devon_cleaned = rss_adapter.clean_show_notes(devon)
        yc_cleaned = rss_adapter.clean_show_notes(yc)
        a16z_cleaned = rss_adapter.clean_show_notes(a16z)

        self.assertNotIn("Flashdance.”www.imdb.com", joe_cleaned)
        self.assertIn("Flashdance.”", joe_cleaned)
        self.assertIn("www.imdb.com/name/nm0000390/", joe_cleaned)

        self.assertNotIn("competitors.www.youtube", devon_cleaned)
        self.assertIn("competitors.", devon_cleaned)
        self.assertIn("www.youtube.com/@devlarratt", devon_cleaned)
        self.assertIn("https://armbet.net", devon_cleaned)

        self.assertNotIn("businesses.Chapters", yc_cleaned)
        self.assertNotIn("InApply", yc_cleaned)
        self.assertNotIn("applyWork", yc_cleaned)
        self.assertIn("Chapters:", yc_cleaned)
        self.assertIn("Apply to Y Combinator", yc_cleaned)
        self.assertIn("Work at a startup", yc_cleaned)

        self.assertNotIn("Updated:Find", a16z_cleaned)
        self.assertIn("Stay Updated:", a16z_cleaned)
        self.assertIn("Find a16z on YouTube", a16z_cleaned)


class TestJsonLdParsing(unittest.TestCase):
    def test_extract_jsonld_reads_work_examples_from_html_script(self):
        html = """
        <html><body>
        <script type="application/ld+json">
        {
          "workExample": [
            {
              "name": "JSON-LD Episode",
              "datePublished": "2026-05-27T02:00:00",
              "duration": "PT1H02M03S",
              "description": "A JSON-LD description",
              "offers": [{"url": "https://example.test/audio.mp3"}]
            }
          ]
        }
        </script>
        </body></html>
        """

        data = rss_adapter.extract_jsonld(html)

        self.assertIn("workExample", data)
        self.assertEqual(data["workExample"][0]["name"], "JSON-LD Episode")

    def test_parse_jsonld_common_outputs_episode_fields(self):
        data = {
            "workExample": [
                {
                    "name": "JSON-LD Strategy &amp; Capital",
                    "datePublished": "2026-05-27T02:00:00",
                    "duration": "PT1H02M03S",
                    "description": "<p>Detailed JSON-LD show notes &amp; context.</p>",
                    "offers": [{"url": "https://example.test/audio.mp3"}],
                }
            ]
        }

        episodes = rss_adapter._parse_jsonld_episodes_common(data, "JSON-LD Feed")

        self.assertEqual(len(episodes), 1)
        ep = episodes[0]
        self.assertEqual(ep["podcast_name"], "JSON-LD Feed")
        self.assertEqual(ep["episode_title"], "JSON-LD Strategy &amp; Capital")
        self.assertEqual(ep["publish_date"], "2026-05-27")
        self.assertEqual(ep["pub_datetime"], "2026-05-27T02:00:00")
        self.assertEqual(ep["duration_minutes"], 62)
        self.assertEqual(ep["show_notes"], "Detailed JSON-LD show notes & context.")
        self.assertEqual(ep["show_notes_source"], "description")
        self.assertEqual(ep["audio_url"], "https://example.test/audio.mp3")


class TestDurationParsing(unittest.TestCase):
    def test_parse_duration_to_seconds_supports_w32_and_iso_forms(self):
        cases = {
            "12:30": 750,
            "06:07": 367,
            "678": 678,
            "113": 113,
            "PT1H02M03S": 3723,
            "PT34M12S": 2052,
            "PT45M": 2700,
            "PT59S": 59,
        }

        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(rss_adapter.parse_duration_to_seconds(raw), expected)
                self.assertEqual(rss_adapter.parse_duration_to_minutes(raw), expected // 60)

    def test_parse_rss_episode_preserves_duration_seconds(self):
        xml = """
        <rss><channel><item>
          <title>Short episode</title>
          <pubDate>Sun, 09 Aug 2026 12:00:00 +0000</pubDate>
          <itunes:duration>06:07</itunes:duration>
          <description>Short but valid episode notes.</description>
          <enclosure url="https://example.test/short.mp3" />
        </item></channel></rss>
        """

        episode = rss_adapter.parse_rss_episodes(xml, "Short Feed")[0]

        self.assertEqual(episode["duration_seconds"], 367)
        self.assertEqual(episode["duration_minutes"], 6)

    def test_parse_jsonld_episode_preserves_iso_duration_without_hours(self):
        data = {
            "workExample": [
                {
                    "name": "Short JSON-LD Episode",
                    "datePublished": "2026-08-09T12:00:00",
                    "duration": "PT34M12S",
                    "description": "Short JSON-LD notes.",
                    "offers": [{"url": "https://example.test/short-jsonld.mp3"}],
                }
            ]
        }

        episode = rss_adapter._parse_jsonld_episodes_common(data, "JSON-LD Feed")[0]

        self.assertEqual(episode["duration_seconds"], 2052)
        self.assertEqual(episode["duration_minutes"], 34)


class TestJsonLdWrapperLogging(unittest.TestCase):
    def test_apple_jsonld_success_returns_episodes_without_logging(self):
        with patch.object(screener, "log_stderr") as log_stderr:
            episodes = screener.parse_apple_jsonld_episodes(valid_jsonld_html(), "Wrapper Feed")

        self.assertEqual(len(episodes), 1)
        ep = episodes[0]
        self.assertEqual(ep["episode_title"], "Wrapper JSON-LD Episode")
        self.assertEqual(ep["podcast_name"], "Wrapper Feed")
        self.assertEqual(ep["duration_minutes"], 62)
        self.assertEqual(ep["show_notes"], "Wrapper show notes & context.")
        self.assertEqual(ep["audio_url"], "https://example.test/wrapper-audio.mp3")
        log_stderr.assert_not_called()

    def test_apple_jsonld_failure_logs_once_and_returns_empty_list(self):
        with patch.object(screener, "log_stderr") as log_stderr:
            episodes = screener.parse_apple_jsonld_episodes(INVALID_JSONLD_HTML, "Broken Feed")

        self.assertEqual(episodes, [])
        log_stderr.assert_called_once()
        self.assertIn("JSON-LD parse failed", log_stderr.call_args.args[0])
        self.assertIn("Broken Feed", log_stderr.call_args.args[0])

    def test_html_jsonld_success_returns_episodes_without_logging(self):
        with patch.object(screener, "log_stderr") as log_stderr:
            episodes = screener.parse_html_jsonld_episodes(valid_jsonld_html(), "Wrapper Feed")

        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["episode_title"], "Wrapper JSON-LD Episode")
        log_stderr.assert_not_called()

    def test_html_jsonld_failure_logs_fallback_then_apple_failure(self):
        with patch.object(screener, "log_stderr") as log_stderr:
            episodes = screener.parse_html_jsonld_episodes(INVALID_JSONLD_HTML, "Broken Feed")

        self.assertEqual(episodes, [])
        self.assertEqual(log_stderr.call_count, 2)
        first = log_stderr.call_args_list[0].args[0]
        second = log_stderr.call_args_list[1].args[0]
        self.assertIn("html_jsonld", first)
        self.assertIn("trying apple fallback", first)
        self.assertIn("JSON-LD parse failed", second)
        self.assertIn("Broken Feed", second)


if __name__ == "__main__":
    unittest.main(verbosity=2)
