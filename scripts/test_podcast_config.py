#!/usr/bin/env python3

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent


class TestPodcastConfig(unittest.TestCase):
    def test_public_show_notes_translation_default_is_safe(self):
        policy = yaml.safe_load((ROOT / "config" / "policy.yaml").read_text()) or {}
        translation = policy.get("show_notes_translation") or {}

        self.assertIs(translation.get("enabled"), False)
        self.assertEqual(translation.get("mode"), "mock")
        self.assertEqual(translation.get("agent_id"), "")
        self.assertEqual(translation.get("model"), "mock-show-notes-translator-v1")

    def test_short_episode_policy_is_explicit(self):
        policy = yaml.safe_load((ROOT / "config" / "policy.yaml").read_text()) or {}
        selection = policy.get("selection_policy") or {}

        self.assertEqual(selection.get("min_duration_minutes"), 5)
        self.assertEqual(selection.get("short_episode_max_minutes"), 15)

    def test_joe_rogan_experience_is_not_monitored(self):
        config = yaml.safe_load((ROOT / "config" / "podcasts.yaml").read_text()) or {}
        podcasts = config.get("podcasts") or []

        self.assertNotIn("JRE", {podcast.get("id") for podcast in podcasts})
        self.assertNotIn(
            "The Joe Rogan Experience",
            {podcast.get("name") for podcast in podcasts},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
