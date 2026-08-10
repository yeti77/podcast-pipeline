#!/usr/bin/env python3
"""Hermetic governance tests for structured episode scoring."""

import unittest

import podcast_screener as ps


def episode(notes: str, *, title: str = "Test episode", duration: int = 60) -> dict:
    return {
        "podcast_name": "Test Podcast",
        "episode_title": title,
        "show_notes_text": notes,
        "audio_url": "https://example.com/audio.mp3",
        "duration_minutes": duration,
        "pub_datetime": "2026-07-12T12:00:00+00:00",
    }


def policy(*, full: int = 75, preview: int = 45) -> dict:
    return {
        "score_policy": {
            "full_threshold": full,
            "preview_threshold": preview,
            "skip_threshold": 25,
        },
        "selection_policy": {"mode": "all_preview", "min_duration_minutes": 5},
        "full_suggestion_policy": {
            "yes_thresholds": {"final_score": 75, "transcription_value": 75},
            "maybe_thresholds": {"final_score": 55, "transcription_value": 55},
        },
    }


class TestScoringSignals(unittest.TestCase):
    def test_information_density_is_monotonic_without_ordinary_saturation(self):
        short = "A concise description of one idea."
        medium = " ".join(
            f"Sentence {index} explains a concrete market mechanism."
            for index in range(8)
        )
        long = "\n".join(
            f"- (00:{index:02d}:00) Chapter {index} explains infrastructure, policy, and markets."
            for index in range(30)
        )

        scores = [ps.calculate_information_density(value) for value in (short, medium, long)]

        self.assertLess(scores[0], scores[1])
        self.assertLess(scores[1], scores[2])
        self.assertLess(scores[1], 100)
        self.assertLessEqual(scores[2], 100)

    def test_english_strategy_and_action_terms_are_case_insensitive(self):
        notes = (
            "AI and GPU data centers are changing electricity demand, the power grid, energy "
            "infrastructure, semiconductor supply, regulation, and competition. The second half "
            "covers trading strategy, portfolio allocation, risk management, hedging, options, "
            "quant models, and investment signals."
        )
        scoring = ps.score_episode_structured(
            episode(notes),
            {"boost_keywords": [], "negative_keywords": [], "primary_topics": []},
            policy(),
        )

        self.assertGreaterEqual(scoring["strategic_value"], 60)
        self.assertGreaterEqual(scoring["actionability"], 60)

    def test_low_relevance_without_strategy_does_not_get_full_suggestion(self):
        suggestion = ps.compute_full_suggestion(
            60,
            70,
            policy(),
            topic_relevance=0,
            strategic_value=0,
            actionability=0,
        )

        self.assertEqual(suggestion, "no")

    def test_strategic_low_topic_episode_can_still_get_suggestion(self):
        suggestion = ps.compute_full_suggestion(
            60,
            70,
            policy(),
            topic_relevance=0,
            strategic_value=72,
            actionability=0,
        )

        self.assertEqual(suggestion, "maybe")


class TestScoringFunctionOwnership(unittest.TestCase):
    def test_priority_helper_supports_relative_and_score_based_modes(self):
        scores = [90, 80, 70, 60, 50]

        self.assertEqual(ps.compute_priority(90, scores, "all_preview"), "high")
        self.assertEqual(ps.compute_priority(70, scores, "all_preview"), "medium")
        self.assertEqual(ps.compute_priority(60, scores, "all_preview"), "low")
        self.assertEqual(ps.compute_priority(20, scores, "score_based"), "low")

    def test_quality_check_supports_duplicate_reason_context(self):
        ep = episode("This description has enough content to pass metadata checks.")
        scoring = {
            "reason_zh": "这是一段长度足够的中文推荐说明，用于验证重复理由检查仍然由唯一的质量检查函数负责。"
        }

        problems = ps.quality_check_episode(ep, scoring, [scoring["reason_zh"], scoring["reason_zh"]])

        self.assertTrue(any("重复" in problem for problem in problems))


class TestSelectionPolicyBoundary(unittest.TestCase):
    def test_all_preview_still_previews_valid_low_score_episode(self):
        ep = episode("A valid but intentionally low-signal episode description.")

        self.assertEqual(
            ps.decide_in_all_preview_mode(ep, {"final_score": 0}, policy()),
            "preview",
        )

    def test_score_based_decision_still_uses_configured_thresholds(self):
        ep = episode("A valid episode description with ordinary general discussion.")
        interests = {"boost_keywords": [], "negative_keywords": [], "primary_topics": []}

        forced_full = ps.score_episode_structured(ep, interests, policy(full=0, preview=0))
        forced_skip = ps.score_episode_structured(ep, interests, policy(full=101, preview=101))

        self.assertEqual(forced_full["decision"], "full")
        self.assertEqual(forced_skip["decision"], "skip")


if __name__ == "__main__":
    unittest.main(verbosity=2)
