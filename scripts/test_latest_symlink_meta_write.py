#!/usr/bin/env python3
"""
Hermetic tests for latest_screening_result.json symlink meta writes.

These tests call only local write helpers. They do not run delivery/notify main
paths, call Feishu APIs/webhooks, or touch real outputs/state files.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

import latest_result_store as store


class LatestSymlinkHarness:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.outputs_dir = self.root / "outputs"
        self.run_dir = self.outputs_dir / "runs" / "2026W22" / "run123"
        self.run_result = self.run_dir / "screening_result.json"
        self.latest = self.outputs_dir / "latest_screening_result.json"
        self.symlink_target = Path("runs/2026W22/run123/screening_result.json")

        self.run_dir.mkdir(parents=True)
        self.write_result(self.base_result())
        self.latest.symlink_to(self.symlink_target)

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

    @staticmethod
    def delivery_meta():
        return {
            "delivery_status": "success",
            "feishu_doc_id": "doc_123",
            "feishu_doc_url": "https://tenant.feishu.cn/docx/doc_123",
        }

    @staticmethod
    def notification_meta():
        return {
            "notification_status": "success",
            "run_id": "run123",
            "week_id": "2026W22",
            "feishu_doc_id": "doc_123",
            "feishu_doc_url": "https://tenant.feishu.cn/docx/doc_123",
            "notified_at": "2026-06-04T10:00:00+0800",
        }

    def write_result(self, payload):
        with open(self.run_result, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def read_run_result(self):
        with open(self.run_result, encoding="utf-8") as f:
            return json.load(f)

    def read_latest_result(self):
        with open(self.latest, encoding="utf-8") as f:
            return json.load(f)

    def assert_latest_symlink_unchanged(self, test_case):
        test_case.assertTrue(os.path.islink(self.latest))
        test_case.assertEqual(os.readlink(self.latest), str(self.symlink_target))


class TestLatestSymlinkMetaWrite(unittest.TestCase):
    def setUp(self):
        self.h = LatestSymlinkHarness()

    def tearDown(self):
        self.h.cleanup()

    def test_delivery_meta_write_follows_latest_symlink(self):
        result_data = self.h.read_latest_result()
        delivery_meta = self.h.delivery_meta()

        store.write_delivery_meta(str(self.h.latest), result_data, delivery_meta)

        self.h.assert_latest_symlink_unchanged(self)
        self.assertEqual(self.h.read_run_result()["delivery_meta"], delivery_meta)
        self.assertEqual(self.h.read_latest_result()["delivery_meta"], delivery_meta)

    def test_notification_meta_write_follows_latest_symlink(self):
        result_data = self.h.read_latest_result()
        notification_meta = self.h.notification_meta()

        store.write_notification_meta(str(self.h.latest), result_data, notification_meta)

        self.h.assert_latest_symlink_unchanged(self)
        self.assertEqual(self.h.read_run_result()["notification_meta"], notification_meta)
        self.assertEqual(self.h.read_latest_result()["notification_meta"], notification_meta)

    def test_delivery_then_notification_preserves_symlink_and_both_meta(self):
        delivery_meta = self.h.delivery_meta()
        notification_meta = self.h.notification_meta()

        store.write_delivery_meta(str(self.h.latest), self.h.read_latest_result(), delivery_meta)
        after_delivery = self.h.read_latest_result()
        store.write_notification_meta(str(self.h.latest), after_delivery, notification_meta)

        self.h.assert_latest_symlink_unchanged(self)
        final_run_result = self.h.read_run_result()
        final_latest_result = self.h.read_latest_result()
        self.assertEqual(final_run_result["delivery_meta"], delivery_meta)
        self.assertEqual(final_run_result["notification_meta"], notification_meta)
        self.assertEqual(final_latest_result["delivery_meta"], delivery_meta)
        self.assertEqual(final_latest_result["notification_meta"], notification_meta)


if __name__ == "__main__":
    unittest.main(verbosity=2)
