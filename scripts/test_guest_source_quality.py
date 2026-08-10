#!/usr/bin/env python3
import unittest

from guest_source_quality import classify_source_quality, rate_overall_source_quality


class TestGuestSourceQuality(unittest.TestCase):
    def test_classifies_primary_sources(self):
        self.assertEqual(
            classify_source_quality(
                {
                    "title": "嘉宾资料",
                    "url": "https://www.xiaoyuzhoufm.com/episode/abc",
                    "snippet": "来自小宇宙节目的官方资料",
                }
            ),
            "primary",
        )

    def test_classifies_secondary_sources(self):
        self.assertEqual(
            classify_source_quality(
                {
                    "title": "Research profile",
                    "url": "https://en.wikipedia.org/wiki/Guest_Profile",
                    "snippet": "A useful background page with enough context.",
                }
            ),
            "secondary",
        )

    def test_classifies_weak_sources(self):
        self.assertEqual(
            classify_source_quality(
                {
                    "title": "搜索结果 - 嘉宾",
                    "url": "https://duckduckgo.com/?q=guest",
                    "snippet": "Search page",
                }
            ),
            "weak",
        )

    def test_classifies_empty_and_malformed_sources_with_existing_behavior(self):
        self.assertEqual(classify_source_quality({}), "secondary")
        self.assertEqual(
            classify_source_quality(
                {
                    "title": "Bare result",
                    "url": "https://example.com/guest",
                    "snippet": "",
                }
            ),
            "weak",
        )

    def test_rates_empty_sources_as_weak(self):
        self.assertEqual(
            rate_overall_source_quality([]),
            {"quality": "weak", "primary_count": 0, "secondary_count": 0, "weak_count": 0},
        )

    def test_rates_mixed_sources_with_existing_precedence(self):
        primary = {
            "title": "Official episode",
            "url": "https://xiaoyuzhoufm.com/episode/abc",
            "snippet": "Official podcast profile",
        }
        secondary = {
            "title": "Wikipedia profile",
            "url": "https://wikipedia.org/wiki/Guest",
            "snippet": "Useful public profile",
        }
        weak = {
            "title": "搜索结果",
            "url": "https://example.com/list",
            "snippet": "short",
        }

        self.assertEqual(rate_overall_source_quality([primary, weak])["quality"], "primary")
        self.assertEqual(rate_overall_source_quality([secondary, weak])["quality"], "weak")
        self.assertEqual(rate_overall_source_quality([secondary, secondary, weak])["quality"], "secondary")


if __name__ == "__main__":
    unittest.main(verbosity=2)
