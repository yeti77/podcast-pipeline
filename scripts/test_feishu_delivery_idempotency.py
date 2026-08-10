#!/usr/bin/env python3
"""
Hermetic tests for weekly Feishu delivery idempotency and dry-run behavior.
These tests use temp files and monkeypatched delivery functions; no real Feishu
API, webhook, latest output, or network path is touched.
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

import deliver_weekly_report_to_feishu as dwf


class DeliveryHarness:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="feishu_delivery_test_")
        self.output_dir = os.path.join(self.tmp, "outputs")
        self.config_dir = os.path.join(self.tmp, "config")
        self.state_dir = os.path.join(self.tmp, "state")
        os.makedirs(self.output_dir)
        os.makedirs(self.config_dir)
        os.makedirs(self.state_dir)

        self.result_path = os.path.join(self.output_dir, "latest_screening_result.json")
        self.report_path = os.path.join(self.output_dir, "latest_screening_report.md")
        self.mapping_path = os.path.join(self.config_dir, "feishu_folder_mapping.json")
        self.policy_path = os.path.join(self.config_dir, "policy.yaml")
        self.delivery_log = os.path.join(self.state_dir, "delivery_log.jsonl")

        with open(self.mapping_path, "w", encoding="utf-8") as f:
            json.dump({
                "weekly_reports": {
                    "feishu_folder_id": "folder_123",
                    "feishu_folder_url": "https://tenant.feishu.cn/drive/folder/folder_123",
                }
            }, f)
        with open(self.report_path, "w", encoding="utf-8") as f:
            f.write("# Weekly report\n\n- one item\n")
        with open(self.policy_path, "w", encoding="utf-8") as f:
            f.write("show_notes_translation:\n  enabled: false\n  mode: mock\n")

        dwf.RESULT_JSON = self.result_path
        dwf.REPORT_MD = self.report_path
        dwf.FOLDER_MAPPING_PATH = self.mapping_path
        dwf.POLICY_CONFIG = self.policy_path
        dwf.DELIVERY_LOG = self.delivery_log

    def cleanup(self):
        shutil.rmtree(self.tmp)

    def write_result(self, delivery_meta=None):
        payload = {
            "run_id": "run_001",
            "week_id": "2026W22",
            "window_start": "2026-05-24T22:00:00+0800",
            "window_end": "2026-05-31T22:00:00+0800",
            "scan_date": "2026-06-01",
            "total_episodes": 1,
            "fetch_errors": [],
            "full": [],
            "preview": [],
            "skip": [],
        }
        if delivery_meta is not None:
            payload["delivery_meta"] = delivery_meta
        with open(self.result_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return payload

    def read_result(self):
        with open(self.result_path, encoding="utf-8") as f:
            return json.load(f)

    def log_records(self):
        if not os.path.exists(self.delivery_log):
            return []
        with open(self.delivery_log, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]


class TestWeeklyFeishuDelivery(unittest.TestCase):
    def setUp(self):
        importlib.reload(dwf)
        self.h = DeliveryHarness()

    def tearDown(self):
        self.h.cleanup()

    def run_main(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = dwf.main(argv)
        return result, buf.getvalue()

    def test_environment_credentials_take_precedence_without_file_reads(self):
        env = {"FEISHU_APP_ID": "env-app-id", "FEISHU_APP_SECRET": "env-app-secret"}

        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("builtins.open", side_effect=AssertionError("credential file read")):
            credentials = dwf.load_feishu_credentials()

        self.assertEqual(credentials, ("env-app-id", "env-app-secret"))

    def test_dry_run_no_network_no_writes(self):
        original = self.h.write_result()

        with mock.patch.object(dwf, "get_tenant_token", side_effect=AssertionError("token called")), \
             mock.patch.object(dwf, "create_doc", side_effect=AssertionError("create called")), \
             mock.patch.object(dwf, "insert_blocks", side_effect=AssertionError("insert called")), \
             mock.patch.object(dwf, "api_post", side_effect=AssertionError("api_post called")), \
             mock.patch.object(dwf.urllib.request, "urlopen", side_effect=AssertionError("urlopen called")):
            result, output = self.run_main(["--dry-run"])

        self.assertEqual(result, "dry-run")
        self.assertIn("DRY-RUN", output)
        self.assertIn("blocks_count", output)
        self.assertEqual(original, self.h.read_result())
        self.assertFalse(os.path.exists(self.h.delivery_log))

    def test_build_blocks_delegates_to_renderer(self):
        sentinel = [{"block_type": 2, "text": {"elements": [], "property": {}}}]
        result_data = {"week_id": "2026W22"}

        with mock.patch.object(dwf, "renderer_build_feishu_blocks", return_value=sentinel) as build_mock:
            result = dwf.build_blocks(result_data, "# report")

        self.assertIs(result, sentinel)
        build_mock.assert_called_once_with(
            result_data,
            "# report",
            show_notes_translation_enabled=False,
            show_notes_translation_options={},
        )

    def test_build_blocks_uses_mock_translation_policy_when_enabled(self):
        sentinel = [{"block_type": 2, "text": {"elements": [], "property": {}}}]
        result_data = {"week_id": "2026W22"}
        policy = {
            "show_notes_translation": {
                "enabled": True,
                "mode": "mock",
                "cache_enabled": False,
                "model": "mock-show-notes-translator-v1",
                "max_chunk_chars": 1800,
            }
        }

        with mock.patch.object(dwf, "renderer_build_feishu_blocks", return_value=sentinel) as build_mock:
            result = dwf.build_blocks(result_data, "# report", policy=policy)

        self.assertIs(result, sentinel)
        _, kwargs = build_mock.call_args
        self.assertTrue(kwargs["show_notes_translation_enabled"])
        self.assertFalse(kwargs["show_notes_translation_options"]["cache_enabled"])
        self.assertEqual(kwargs["show_notes_translation_options"]["model_name"], "mock-show-notes-translator-v1")
        self.assertIn("translate_chunk", kwargs["show_notes_translation_options"])

    def test_insert_blocks_reports_children_from_inner_api_data(self):
        blocks = [{"block_type": 2}, {"block_type": 2}, {"block_type": 2}]
        buf = io.StringIO()

        with mock.patch.object(
            dwf,
            "api_post",
            return_value={"children": [{}, {}, {}]},
        ), contextlib.redirect_stdout(buf):
            result = dwf.insert_blocks("doc_123", "token_123", blocks)

        self.assertTrue(result)
        self.assertIn("Done: 3 blocks inserted in 1 batches", buf.getvalue())

    def test_existing_success_meta_skips_without_network(self):
        meta = {
            "delivery_status": "success",
            "feishu_doc_id": "old_doc",
            "feishu_doc_url": "https://tenant.feishu.cn/docx/old_doc",
        }
        original = self.h.write_result(meta)

        with mock.patch.object(dwf, "get_tenant_token", side_effect=AssertionError("token called")), \
             mock.patch.object(dwf, "create_doc", side_effect=AssertionError("create called")), \
             mock.patch.object(dwf, "insert_blocks", side_effect=AssertionError("insert called")):
            result, output = self.run_main([])

        self.assertEqual(result, meta["feishu_doc_url"])
        self.assertIn("SKIP", output)
        self.assertEqual(original, self.h.read_result())
        self.assertFalse(os.path.exists(self.h.delivery_log))

    def test_existing_success_meta_skips_before_folder_mapping_and_blocks(self):
        meta = {
            "delivery_status": "success",
            "feishu_doc_id": "old_doc",
            "feishu_doc_url": "https://tenant.feishu.cn/docx/old_doc",
        }
        original = self.h.write_result(meta)
        os.remove(self.h.mapping_path)

        with mock.patch.object(dwf, "build_blocks", side_effect=AssertionError("build_blocks called")), \
             mock.patch.object(dwf, "get_tenant_token", side_effect=AssertionError("token called")), \
             mock.patch.object(dwf, "create_doc", side_effect=AssertionError("create called")), \
             mock.patch.object(dwf, "insert_blocks", side_effect=AssertionError("insert called")):
            result, output = self.run_main([])

        self.assertEqual(result, meta["feishu_doc_url"])
        self.assertIn("SKIP", output)
        self.assertEqual(original, self.h.read_result())
        self.assertFalse(os.path.exists(self.h.delivery_log))

    def test_force_recreates_and_overwrites_success_meta(self):
        self.h.write_result({
            "delivery_status": "success",
            "feishu_doc_id": "old_doc",
            "feishu_doc_url": "https://tenant.feishu.cn/docx/old_doc",
        })
        calls = []

        def fake_insert(doc_token, token, blocks):
            calls.append((doc_token, token, len(blocks)))
            return True

        with mock.patch.object(dwf, "get_tenant_token", return_value="token_123"), \
             mock.patch.object(dwf, "create_doc", return_value="new_doc"), \
             mock.patch.object(dwf, "insert_blocks", side_effect=fake_insert):
            result, _ = self.run_main(["--force"])

        data = self.h.read_result()
        self.assertEqual(result, "https://tenant.feishu.cn/docx/new_doc")
        self.assertEqual(data["delivery_meta"]["delivery_status"], "success")
        self.assertEqual(data["delivery_meta"]["feishu_doc_id"], "new_doc")
        self.assertEqual(calls[0][0], "new_doc")
        log = self.h.log_records()[0]
        self.assertTrue(log["forced"])
        self.assertEqual(log["old_doc_id"], "old_doc")
        self.assertEqual(log["new_doc_id"], "new_doc")

    def test_existing_error_meta_requires_force_without_network(self):
        self.h.write_result({
            "delivery_status": "error_write_blocks",
            "feishu_doc_id": "failed_doc",
            "feishu_doc_url": "https://tenant.feishu.cn/docx/failed_doc",
            "error": "previous failure",
        })

        with mock.patch.object(dwf, "get_tenant_token", side_effect=AssertionError("token called")), \
             mock.patch.object(dwf, "create_doc", side_effect=AssertionError("create called")), \
             mock.patch.object(dwf, "insert_blocks", side_effect=AssertionError("insert called")):
            with self.assertRaises(SystemExit) as cm:
                self.run_main([])

        self.assertEqual(cm.exception.code, 1)
        self.assertEqual("error_write_blocks", self.h.read_result()["delivery_meta"]["delivery_status"])
        self.assertFalse(os.path.exists(self.h.delivery_log))

    def test_insert_failure_writes_error_meta_with_doc_url(self):
        self.h.write_result()

        with mock.patch.object(dwf, "get_tenant_token", return_value="token_123"), \
             mock.patch.object(dwf, "create_doc", return_value="failed_doc"), \
             mock.patch.object(dwf, "insert_blocks", side_effect=RuntimeError("insert exploded")):
            with self.assertRaises(SystemExit) as cm:
                self.run_main([])

        self.assertEqual(cm.exception.code, 1)
        meta = self.h.read_result()["delivery_meta"]
        self.assertEqual(meta["delivery_status"], "error_write_blocks")
        self.assertEqual(meta["feishu_doc_id"], "failed_doc")
        self.assertEqual(meta["feishu_doc_url"], "https://tenant.feishu.cn/docx/failed_doc")
        self.assertIn("insert exploded", meta["error"])
        log = self.h.log_records()[0]
        self.assertEqual(log["status"], "error_write_blocks")
        self.assertEqual(log["doc_token"], "failed_doc")

    def test_success_writes_delivery_meta_and_log(self):
        self.h.write_result()

        with mock.patch.object(dwf, "get_tenant_token", return_value="token_123"), \
             mock.patch.object(dwf, "create_doc", return_value="ok_doc"), \
             mock.patch.object(dwf, "insert_blocks", return_value=True):
            result, _ = self.run_main([])

        self.assertEqual(result, "https://tenant.feishu.cn/docx/ok_doc")
        meta = self.h.read_result()["delivery_meta"]
        self.assertEqual(meta["delivery_status"], "success")
        self.assertEqual(meta["feishu_doc_id"], "ok_doc")
        log = self.h.log_records()[0]
        self.assertEqual(log["status"], "success")
        self.assertEqual(log["doc_token"], "ok_doc")


if __name__ == "__main__":
    unittest.main(verbosity=2)
