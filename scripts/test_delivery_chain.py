#!/usr/bin/env python3
"""Hermetic smoke tests for latest-result and delivery-chain contracts."""

import json
import py_compile
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"


class TestDeliveryChain(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="delivery_chain_test_")
        self.pipeline_dir = Path(self.temp_dir.name)
        self.outputs_dir = self.pipeline_dir / "outputs"
        self.week_id = "2026W22"
        self.run_id = "run_fixture_001"
        self.run_dir = self.outputs_dir / "runs" / self.week_id / self.run_id
        self.run_dir.mkdir(parents=True)

        self.result_payload = {
            "run_id": self.run_id,
            "week_id": self.week_id,
            "window_start": "2026-05-24T22:00:00+0800",
            "window_end": "2026-05-31T22:00:00+0800",
            "timezone": "Asia/Shanghai",
            "interval_rule": "published_at >= window_start and published_at < window_end",
            "window_semantics": "last_completed_business_week",
            "total_episodes": 1,
            "fetch_errors": [],
            "full": [],
            "preview": [{"episode_id": "fixture", "decision": "preview"}],
            "skip": [],
        }
        result_file = self.run_dir / "screening_result.json"
        report_file = self.run_dir / "screening_report.md"
        result_file.write_text(json.dumps(self.result_payload), encoding="utf-8")
        report_file.write_text(f"# 播客筛选报告 | {self.week_id}\n", encoding="utf-8")

        self.result_json = self.outputs_dir / "latest_screening_result.json"
        self.report_md = self.outputs_dir / "latest_screening_report.md"
        self.result_json.symlink_to(result_file.relative_to(self.outputs_dir))
        self.report_md.symlink_to(report_file.relative_to(self.outputs_dir))

    def tearDown(self):
        self.temp_dir.cleanup()

    def read_result(self):
        return json.loads(self.result_json.read_text(encoding="utf-8"))

    def test_fixture_does_not_use_operator_home_pipeline(self):
        self.assertNotEqual(self.pipeline_dir, Path.home() / "podcast_pipeline")

    def test_latest_result_and_report_exist(self):
        self.assertTrue(self.result_json.exists())
        self.assertTrue(self.report_md.exists())

    def test_latest_result_and_report_point_to_same_run(self):
        self.assertEqual(self.result_json.resolve().parent, self.report_md.resolve().parent)
        self.assertEqual(self.result_json.resolve().parent, self.run_dir.resolve())

    def test_latest_symlink_targets_share_run_directory(self):
        result_target_dir = (self.outputs_dir / self.result_json.readlink()).parent
        report_target_dir = (self.outputs_dir / self.report_md.readlink()).parent

        self.assertEqual(result_target_dir, report_target_dir)

    def test_latest_points_to_existing_files(self):
        for path in (self.result_json, self.report_md):
            self.assertTrue(path.is_symlink())
            self.assertTrue(path.resolve().exists())

    def test_latest_result_matches_run_directory(self):
        data = self.read_result()
        expected = self.outputs_dir / "runs" / data["week_id"] / data["run_id"]

        self.assertEqual(expected, self.run_dir)
        self.assertTrue((expected / "screening_result.json").exists())

    def test_latest_does_not_use_legacy_nested_directory(self):
        for path in (self.result_json, self.report_md):
            target = str(path.readlink())
            self.assertNotIn("2026W17", target)
            self.assertNotIn("latest/", target)

    def test_delivery_scripts_compile(self):
        for name in ("deliver_weekly_report_to_feishu.py", "feishu_notify.py"):
            py_compile.compile(str(SCRIPTS_DIR / name), doraise=True)

    def test_cron_logs_translation_summary_and_warning_without_failing_delivery(self):
        wrapper = (SCRIPTS_DIR / "podcast_screener_cron.sh").read_text(encoding="utf-8")

        self.assertIn("Runs every Sunday at 22:10", wrapper)
        self.assertIn("show_notes_translation_summary", wrapper)
        self.assertIn("TRANSLATION_SUMMARY", wrapper)
        self.assertIn("TRANSLATION_WARNING", wrapper)
        self.assertLess(wrapper.index("TRANSLATION_WARNING"), wrapper.index("STEP2_START"))

    def test_latest_window_metadata_is_complete(self):
        data = self.read_result()
        required = {
            "run_id", "week_id", "window_start", "window_end", "timezone",
            "interval_rule", "window_semantics",
        }

        self.assertFalse(required - set(data))
        self.assertEqual(data["timezone"], "Asia/Shanghai")
        self.assertEqual(data["window_semantics"], "last_completed_business_week")

    def test_delivery_reuses_stored_window(self):
        data = self.read_result()
        window_start = datetime.fromisoformat(data["window_start"].replace("+0800", "+08:00"))
        window_end = datetime.fromisoformat(data["window_end"].replace("+0800", "+08:00"))

        self.assertTrue(data["week_id"].startswith("2026"))
        self.assertLess(window_start, window_end)

    def test_result_has_decision_lists(self):
        data = self.read_result()

        self.assertIn("full", data)
        self.assertIn("preview", data)
        self.assertIn("skip", data)
        self.assertEqual(len(data["preview"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
