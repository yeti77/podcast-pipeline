#!/usr/bin/env python3
"""Hermetic tests for tracked policy plus local operator overrides."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import yaml

import deliver_weekly_report_to_feishu as delivery
import podcast_screener
import podcast_transcriber
from policy_config import deep_merge_policy, load_policy_config


class TestPolicyConfig(unittest.TestCase):
    def test_recursive_merge_preserves_public_siblings(self):
        base = {
            "selection_policy": {"mode": "all_preview", "min_duration_minutes": 5},
            "show_notes_translation": {
                "enabled": False,
                "mode": "mock",
                "target_language": "zh",
            },
        }
        override = {
            "show_notes_translation": {
                "enabled": True,
                "mode": "openclaw",
                "agent_id": "main",
            }
        }
        original_base = deepcopy(base)
        original_override = deepcopy(override)

        merged = deep_merge_policy(base, override)

        self.assertEqual(merged["selection_policy"], base["selection_policy"])
        self.assertEqual(merged["show_notes_translation"]["target_language"], "zh")
        self.assertTrue(merged["show_notes_translation"]["enabled"])
        self.assertEqual(merged["show_notes_translation"]["mode"], "openclaw")
        self.assertEqual(merged["show_notes_translation"]["agent_id"], "main")
        self.assertEqual(base, original_base)
        self.assertEqual(override, original_override)

    def test_sequences_and_scalars_are_replaced(self):
        merged = deep_merge_policy(
            {"nested": {"items": ["public"], "value": 1}},
            {"nested": {"items": ["local"], "value": 2}},
        )

        self.assertEqual(merged, {"nested": {"items": ["local"], "value": 2}})

    def test_missing_local_file_returns_public_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_path = Path(tmp) / "policy.yaml"
            base = {"show_notes_translation": {"enabled": False, "mode": "mock"}}
            base_path.write_text(yaml.safe_dump(base), encoding="utf-8")

            loaded = load_policy_config(base_path)

        self.assertEqual(loaded, base)

    def test_default_local_sibling_overrides_public_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            base_path = config_dir / "policy.yaml"
            local_path = config_dir / "policy.local.yaml"
            base_path.write_text(
                yaml.safe_dump({
                    "selection_policy": {"mode": "all_preview"},
                    "show_notes_translation": {"enabled": False, "mode": "mock"},
                }),
                encoding="utf-8",
            )
            local_path.write_text(
                yaml.safe_dump({
                    "show_notes_translation": {
                        "enabled": True,
                        "mode": "openclaw",
                        "agent_id": "main",
                    }
                }),
                encoding="utf-8",
            )

            loaded = load_policy_config(base_path)

        self.assertEqual(loaded["selection_policy"]["mode"], "all_preview")
        self.assertTrue(loaded["show_notes_translation"]["enabled"])
        self.assertEqual(loaded["show_notes_translation"]["mode"], "openclaw")
        self.assertEqual(loaded["show_notes_translation"]["agent_id"], "main")

    def test_invalid_yaml_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_path = Path(tmp) / "policy.yaml"
            base_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "policy.yaml must contain a mapping"):
                load_policy_config(base_path)

    def test_runtime_consumers_share_the_effective_local_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "podcasts.yaml").write_text("podcasts: []\n", encoding="utf-8")
            (config_dir / "interests.yaml").write_text("topics: []\n", encoding="utf-8")
            (config_dir / "policy.yaml").write_text(
                yaml.safe_dump({
                    "selection_policy": {"mode": "all_preview"},
                    "show_notes_translation": {"enabled": False, "mode": "mock"},
                    "whisper": {"whisper_backend": "public"},
                }),
                encoding="utf-8",
            )
            (config_dir / "policy.local.yaml").write_text(
                yaml.safe_dump({
                    "show_notes_translation": {
                        "enabled": True,
                        "mode": "openclaw",
                        "agent_id": "main",
                    },
                    "whisper": {"whisper_backend": "local"},
                }),
                encoding="utf-8",
            )

            with mock.patch.object(podcast_screener, "CONFIG_DIR", str(config_dir)), mock.patch.object(
                podcast_screener, "log"
            ):
                _, _, screener_policy = podcast_screener.load_configs()

            delivery_policy = delivery.load_policy_config(str(config_dir / "policy.yaml"))

            previous_config_dir = podcast_transcriber.CONFIG_DIR
            previous_cache = podcast_transcriber._WHISPER_CFG
            podcast_transcriber.CONFIG_DIR = str(config_dir)
            podcast_transcriber._WHISPER_CFG = None
            try:
                whisper_policy = podcast_transcriber.load_whisper_config()
            finally:
                podcast_transcriber.CONFIG_DIR = previous_config_dir
                podcast_transcriber._WHISPER_CFG = previous_cache

        self.assertEqual(screener_policy["selection_policy"]["mode"], "all_preview")
        self.assertTrue(screener_policy["show_notes_translation"]["enabled"])
        self.assertEqual(screener_policy["show_notes_translation"]["agent_id"], "main")
        self.assertTrue(delivery_policy["show_notes_translation"]["enabled"])
        self.assertEqual(delivery_policy["show_notes_translation"]["mode"], "openclaw")
        self.assertEqual(whisper_policy["whisper_backend"], "local")


if __name__ == "__main__":
    unittest.main(verbosity=2)
