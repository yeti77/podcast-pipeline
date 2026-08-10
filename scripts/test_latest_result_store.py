#!/usr/bin/env python3
"""
Hermetic tests for latest_result_store.py.

These tests use only TemporaryDirectory paths and do not touch real outputs,
state, Feishu, RSS, or guest cache.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

import latest_result_store as store


class LatestResultStoreHarness:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.outputs_dir = self.root / "outputs"
        self.run_dir = self.outputs_dir / "runs" / "2026W22" / "run123"
        self.result_path = self.run_dir / "screening_result.json"
        self.report_path = self.run_dir / "screening_report.md"
        self.latest_json = self.outputs_dir / "latest_screening_result.json"
        self.latest_report = self.outputs_dir / "latest_screening_report.md"
        self.latest_json_target = Path("runs/2026W22/run123/screening_result.json")

        self.run_dir.mkdir(parents=True)
        self.write_result(self.base_result())
        self.report_path.write_text("# report\n", encoding="utf-8")

    def cleanup(self):
        self.tmp.cleanup()

    @staticmethod
    def base_result():
        return {
            "run_id": "run123",
            "week_id": "2026W22",
            "window_start": "2026-05-24T22:00:00+0800",
            "window_end": "2026-05-31T22:00:00+0800",
            "full": [],
            "preview": [],
            "skip": [],
        }

    def write_result(self, data):
        with open(self.result_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def read_run_result(self):
        return store.read_result(str(self.result_path))

    def make_latest_json_symlink(self):
        self.latest_json.symlink_to(self.latest_json_target)


class TestLatestResultStore(unittest.TestCase):
    def setUp(self):
        self.h = LatestResultStoreHarness()

    def tearDown(self):
        self.h.cleanup()

    def test_read_result_reads_json(self):
        data = store.read_result(str(self.h.result_path))
        self.assertEqual(data["run_id"], "run123")
        self.assertEqual(data["week_id"], "2026W22")

    def test_write_result_follow_symlink_updates_target_without_replacing_link(self):
        self.h.make_latest_json_symlink()
        original_target = os.readlink(self.h.latest_json)
        data = self.h.base_result()
        data["extra"] = "written-through-latest"

        store.write_result_follow_symlink(str(self.h.latest_json), data)

        self.assertTrue(os.path.islink(self.h.latest_json))
        self.assertEqual(os.readlink(self.h.latest_json), original_target)
        self.assertEqual(self.h.read_run_result()["extra"], "written-through-latest")
        self.assertEqual(store.read_result(str(self.h.latest_json))["extra"], "written-through-latest")

    def test_delivery_and_notification_meta_coexist(self):
        self.h.make_latest_json_symlink()
        delivery_meta = {
            "delivery_status": "success",
            "feishu_doc_id": "doc_123",
            "feishu_doc_url": "https://tenant.feishu.cn/docx/doc_123",
        }
        notification_meta = {
            "notification_status": "success",
            "run_id": "run123",
            "week_id": "2026W22",
            "feishu_doc_id": "doc_123",
            "feishu_doc_url": "https://tenant.feishu.cn/docx/doc_123",
        }

        store.write_delivery_meta(str(self.h.latest_json), store.read_result(str(self.h.latest_json)), delivery_meta)
        after_delivery = store.read_result(str(self.h.latest_json))
        store.write_notification_meta(str(self.h.latest_json), after_delivery, notification_meta)

        final = self.h.read_run_result()
        self.assertEqual(final["delivery_meta"], delivery_meta)
        self.assertEqual(final["notification_meta"], notification_meta)
        self.assertTrue(os.path.islink(self.h.latest_json))
        self.assertEqual(os.readlink(self.h.latest_json), str(self.h.latest_json_target))

    def test_update_latest_pointers_creates_relative_symlinks(self):
        store.update_latest_pointers(
            str(self.h.outputs_dir),
            str(self.h.run_dir),
            str(self.h.result_path),
            str(self.h.report_path),
        )

        self.assertTrue(os.path.islink(self.h.latest_json))
        self.assertTrue(os.path.islink(self.h.latest_report))
        self.assertEqual(os.readlink(self.h.latest_json), "runs/2026W22/run123/screening_result.json")
        self.assertEqual(os.readlink(self.h.latest_report), "runs/2026W22/run123/screening_report.md")
        result_dir = Path(self.h.outputs_dir / os.readlink(self.h.latest_json)).parent
        report_dir = Path(self.h.outputs_dir / os.readlink(self.h.latest_report)).parent
        self.assertEqual(result_dir, report_dir)
        self.assertEqual(store.read_result(str(self.h.latest_json))["run_id"], "run123")

    def test_update_latest_pointers_fails_fast_on_run_id_mismatch(self):
        bad = self.h.base_result()
        bad["run_id"] = "wrong_run"
        self.h.write_result(bad)

        with self.assertRaises(ValueError):
            store.update_latest_pointers(
                str(self.h.outputs_dir),
                str(self.h.run_dir),
                str(self.h.result_path),
                str(self.h.report_path),
            )
        self.assertFalse(self.h.latest_json.exists())

    def test_update_latest_pointers_fails_fast_on_week_id_mismatch(self):
        bad = self.h.base_result()
        bad["week_id"] = "2026W99"
        self.h.write_result(bad)

        with self.assertRaises(ValueError):
            store.update_latest_pointers(
                str(self.h.outputs_dir),
                str(self.h.run_dir),
                str(self.h.result_path),
                str(self.h.report_path),
            )
        self.assertFalse(self.h.latest_json.exists())

    def test_update_latest_pointers_fails_fast_when_result_outside_run_dir(self):
        outside_dir = self.h.outputs_dir / "runs" / "2026W22" / "other_run"
        outside_dir.mkdir(parents=True)
        outside_result = outside_dir / "screening_result.json"
        with open(outside_result, "w", encoding="utf-8") as f:
            json.dump(self.h.base_result(), f, ensure_ascii=False, indent=2)

        with self.assertRaisesRegex(ValueError, "result_path.*run_dir"):
            store.update_latest_pointers(
                str(self.h.outputs_dir),
                str(self.h.run_dir),
                str(outside_result),
                str(self.h.report_path),
            )
        self.assertFalse(self.h.latest_json.exists())

    def test_update_latest_pointers_fails_fast_when_report_outside_run_dir(self):
        outside_dir = self.h.outputs_dir / "runs" / "2026W22" / "other_run"
        outside_dir.mkdir(parents=True)
        outside_report = outside_dir / "screening_report.md"
        outside_report.write_text("# report\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "report_path.*run_dir"):
            store.update_latest_pointers(
                str(self.h.outputs_dir),
                str(self.h.run_dir),
                str(self.h.result_path),
                str(outside_report),
            )
        self.assertFalse(self.h.latest_json.exists())

    def test_update_latest_pointers_fails_fast_on_unexpected_filenames(self):
        wrong_result = self.h.run_dir / "wrong_result.json"
        with open(wrong_result, "w", encoding="utf-8") as f:
            json.dump(self.h.base_result(), f, ensure_ascii=False, indent=2)
        wrong_report = self.h.run_dir / "wrong_report.md"
        wrong_report.write_text("# report\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "screening_result.json"):
            store.update_latest_pointers(
                str(self.h.outputs_dir),
                str(self.h.run_dir),
                str(wrong_result),
                str(self.h.report_path),
            )
        with self.assertRaisesRegex(ValueError, "screening_report.md"):
            store.update_latest_pointers(
                str(self.h.outputs_dir),
                str(self.h.run_dir),
                str(self.h.result_path),
                str(wrong_report),
            )
        self.assertFalse(self.h.latest_json.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
