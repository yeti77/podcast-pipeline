#!/usr/bin/env python3
"""
Hermetic tests for Feishu group notification dry-run and idempotency behavior.
These tests use temp files and monkeypatched webhook functions; no real latest
output, delivery log, Feishu webhook, or network path is touched.
"""

import contextlib
import importlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import feishu_notify as notify


class NotifyHarness:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="feishu_notify_test_")
        self.output_dir = os.path.join(self.tmp, "outputs")
        self.config_dir = os.path.join(self.tmp, "config")
        self.state_dir = os.path.join(self.tmp, "state")
        os.makedirs(self.output_dir)
        os.makedirs(self.config_dir)
        os.makedirs(self.state_dir)

        self.result_path = os.path.join(self.output_dir, "latest_screening_result.json")
        self.config_path = os.path.join(self.config_dir, "feishu_config.json")
        self.delivery_log = os.path.join(self.state_dir, "delivery_log.jsonl")

        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({"webhook_url": "https://example.test/webhook"}, f)

        notify.RESULT_JSON = self.result_path
        notify.FEISHU_CONFIG = self.config_path
        notify.DELIVERY_LOG = self.delivery_log

    def cleanup(self):
        shutil.rmtree(self.tmp)

    def write_result(self, delivery_meta=None, notification_meta=None, translation_summary=None):
        payload = {
            "run_id": "run_001",
            "week_id": "2026W22",
            "window_start": "2026-05-24T22:00:00+0800",
            "window_end": "2026-05-31T22:00:00+0800",
            "total_episodes": 3,
            "fetch_errors": [],
            "full": [
                {
                    "podcast_name": "Show A",
                    "episode_title": "Important episode",
                    "duration_minutes": 42,
                    "score": 9,
                    "decision": "full",
                }
            ],
            "preview": [],
            "skip": [{}, {}],
        }
        if delivery_meta is not None:
            payload["delivery_meta"] = delivery_meta
        if notification_meta is not None:
            payload["notification_meta"] = notification_meta
        if translation_summary is not None:
            payload["show_notes_translation_summary"] = translation_summary
        with open(self.result_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return payload

    def success_delivery(self):
        return {
            "delivery_status": "success",
            "feishu_doc_id": "doc_123",
            "feishu_doc_url": "https://tenant.feishu.cn/docx/doc_123",
        }

    def success_notification(self):
        return {
            "notification_status": "success",
            "run_id": "run_001",
            "week_id": "2026W22",
            "feishu_doc_id": "doc_123",
            "feishu_doc_url": "https://tenant.feishu.cn/docx/doc_123",
            "notified_at": "2026-06-02T22:00:00+0800",
        }

    def read_result(self):
        with open(self.result_path, encoding="utf-8") as f:
            return json.load(f)

    def log_records(self):
        if not os.path.exists(self.delivery_log):
            return []
        with open(self.delivery_log, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]


class TestFeishuNotifyIdempotency(unittest.TestCase):
    def setUp(self):
        importlib.reload(notify)
        self.h = NotifyHarness()

    def tearDown(self):
        self.h.cleanup()

    def run_main(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = notify.main(argv)
        return result, buf.getvalue()

    def test_environment_webhook_takes_precedence_without_file_reads(self):
        with mock.patch.dict(
            os.environ,
            {"FEISHU_WEBHOOK_URL": "https://example.test/env-webhook"},
            clear=False,
        ), mock.patch("builtins.open", side_effect=AssertionError("config file read")):
            webhook = notify.get_webhook_url()

        self.assertEqual(webhook, "https://example.test/env-webhook")

    def test_dry_run_no_webhook_no_writes(self):
        original = self.h.write_result(delivery_meta=self.h.success_delivery())

        with mock.patch.object(notify, "send_post_message", side_effect=AssertionError("webhook called")), \
             mock.patch.object(notify.urllib.request, "urlopen", side_effect=AssertionError("urlopen called")):
            result, output = self.run_main(["--dry-run"])

        self.assertEqual(result, "dry-run")
        self.assertIn("DRY-RUN", output)
        self.assertIn("has_delivery_success=True", output)
        self.assertEqual(original, self.h.read_result())
        self.assertFalse(os.path.exists(self.h.delivery_log))

    def test_notification_payload_warns_about_translation_fallbacks(self):
        data = self.h.write_result(
            delivery_meta=self.h.success_delivery(),
            translation_summary={
                "eligible_count": 31,
                "translated_count": 27,
                "partial_count": 1,
                "failed_count": 3,
                "visible_translation_count": 28,
            },
        )

        _title, paragraphs, summary = notify.build_notification_payload(data)
        text = json.dumps(paragraphs, ensure_ascii=False)

        self.assertIn("Show Notes 翻译：28/31", text)
        self.assertIn("3 期回退原文", text)
        self.assertIn("每周日 22:10 执行", text)
        self.assertEqual(summary["translation_failed_n"], 3)

    def test_existing_success_notification_skips_without_webhook(self):
        original = self.h.write_result(
            delivery_meta=self.h.success_delivery(),
            notification_meta=self.h.success_notification(),
        )

        with mock.patch.object(notify, "get_webhook_url", side_effect=AssertionError("config read")), \
             mock.patch.object(notify, "send_post_message", side_effect=AssertionError("webhook called")):
            result, output = self.run_main([])

        self.assertEqual(result, "skip")
        self.assertIn("SKIP", output)
        self.assertEqual(original, self.h.read_result())
        self.assertFalse(os.path.exists(self.h.delivery_log))

    def test_force_resends_and_overwrites_success_notification(self):
        self.h.write_result(
            delivery_meta=self.h.success_delivery(),
            notification_meta=self.h.success_notification(),
        )

        with mock.patch.object(notify, "send_post_message", return_value=True):
            result, _ = self.run_main(["--force"])

        self.assertEqual(result, "success")
        meta = self.h.read_result()["notification_meta"]
        self.assertEqual(meta["notification_status"], "success")
        self.assertEqual(meta["feishu_doc_id"], "doc_123")
        self.assertNotEqual(meta["notified_at"], "2026-06-02T22:00:00+0800")
        log = self.h.log_records()[0]
        self.assertTrue(log["forced"])
        self.assertEqual(log["old_notification_status"], "success")
        self.assertEqual(log["old_notified_at"], "2026-06-02T22:00:00+0800")

    def test_missing_success_delivery_does_not_notify(self):
        self.h.write_result(delivery_meta={"delivery_status": "error_write_blocks"})

        with mock.patch.object(notify, "send_post_message", side_effect=AssertionError("webhook called")):
            with self.assertRaises(SystemExit) as cm:
                self.run_main([])

        self.assertEqual(cm.exception.code, 1)
        self.assertFalse(os.path.exists(self.h.delivery_log))
        self.assertNotIn("notification_meta", self.h.read_result())

    def test_existing_error_notification_requires_force_without_webhook(self):
        self.h.write_result(
            delivery_meta=self.h.success_delivery(),
            notification_meta={"notification_status": "error", "error": "previous failure"},
        )

        with mock.patch.object(notify, "send_post_message", side_effect=AssertionError("webhook called")):
            with self.assertRaises(SystemExit) as cm:
                self.run_main([])

        self.assertEqual(cm.exception.code, 1)
        self.assertEqual("error", self.h.read_result()["notification_meta"]["notification_status"])
        self.assertFalse(os.path.exists(self.h.delivery_log))

    def test_webhook_success_writes_notification_meta_and_log(self):
        self.h.write_result(delivery_meta=self.h.success_delivery())

        with mock.patch.object(notify, "send_post_message", return_value=True):
            result, _ = self.run_main([])

        self.assertEqual(result, "success")
        meta = self.h.read_result()["notification_meta"]
        self.assertEqual(meta["notification_status"], "success")
        self.assertEqual(meta["run_id"], "run_001")
        self.assertEqual(meta["week_id"], "2026W22")
        self.assertEqual(meta["feishu_doc_id"], "doc_123")
        log = self.h.log_records()[0]
        self.assertEqual(log["event"], "feishu_group_post")
        self.assertEqual(log["status"], "success")
        self.assertFalse(log["forced"])

    def test_webhook_failure_writes_error_notification_meta_and_log(self):
        self.h.write_result(delivery_meta=self.h.success_delivery())

        with mock.patch.object(notify, "send_post_message", return_value=False):
            with self.assertRaises(SystemExit) as cm:
                self.run_main([])

        self.assertEqual(cm.exception.code, 1)
        meta = self.h.read_result()["notification_meta"]
        self.assertEqual(meta["notification_status"], "error")
        self.assertEqual(meta["feishu_doc_id"], "doc_123")
        self.assertIn("webhook returned failure", meta["error"])
        log = self.h.log_records()[0]
        self.assertEqual(log["status"], "error")
        self.assertEqual(log["doc_id"], "doc_123")


if __name__ == "__main__":
    unittest.main(verbosity=2)
