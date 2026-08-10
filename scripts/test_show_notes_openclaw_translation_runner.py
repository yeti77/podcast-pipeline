#!/usr/bin/env python3
"""Hermetic tests for the OpenClaw show-notes translation runner.

These tests use fake subprocess callables only. They must not invoke real
OpenClaw, MiniMax/tokenplan, network, cache, outputs, or production commands.
"""

import os
import subprocess
import tempfile
import unittest

from show_notes_openclaw_translation_runner import (
    DEFAULT_OPENCLAW_SHOW_NOTES_TRANSLATION_MODEL,
    DEFAULT_OPENCLAW_TRANSLATION_TIMEOUT_SECONDS,
    build_openclaw_show_notes_translation_prompt,
    extract_openclaw_translation_text,
    summarize_openclaw_json_stdout_schema,
    translate_show_notes_chunk_with_openclaw,
)
from show_notes_translation_runner import translate_show_notes_chunks_with_runner


class FakeCompletedProcess:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class RecordingRunner:
    def __init__(self, result=None, exc=None):
        self.result = result if result is not None else FakeCompletedProcess(stdout='{"text": "这是中文译文。"}')
        self.exc = exc
        self.calls = []

    def __call__(self, cmd, capture_output, text, timeout):
        self.calls.append(
            {
                "cmd": cmd,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
            }
        )
        if self.exc:
            raise self.exc
        return self.result


class TestOpenClawShowNotesTranslationRunner(unittest.TestCase):
    def test_prompt_includes_translation_constraints_and_chunk(self):
        chunk = "Read more at https://example.com/report.\n- Keep the grid stable."

        prompt = build_openclaw_show_notes_translation_prompt(chunk)

        self.assertIn("翻译成中文", prompt)
        self.assertIn("只输出译文", prompt)
        self.assertIn("不总结", prompt)
        self.assertIn("不删减", prompt)
        self.assertIn("保留 URL", prompt)
        self.assertIn("必须逐字复制", prompt)
        self.assertIn("每一个 URL", prompt)
        self.assertIn("不要删除", prompt)
        self.assertIn("不要改写", prompt)
        self.assertIn("保留时间戳", prompt)
        self.assertIn("保留项目符号", prompt)
        self.assertIn("保留人名", prompt)
        self.assertIn("章节标题", prompt)
        self.assertIn("链接标题", prompt)
        self.assertIn("资源标题", prompt)
        self.assertIn("更正和免责声明", prompt)
        self.assertIn("文章、书籍、报告和链接标题", prompt)
        self.assertIn("只保留出版方原名", prompt)
        self.assertIn("不要重新补充", prompt)
        self.assertIn(chunk, prompt)

    def test_success_returns_stripped_stdout_and_preserves_command_protocol(self):
        runner = RecordingRunner(FakeCompletedProcess(stdout='{"text": "  这是中文译文。  "}\n'))
        chunk = "Hello world."

        result = translate_show_notes_chunk_with_openclaw(chunk, run_subprocess=runner)

        self.assertEqual(result, "这是中文译文。")
        self.assertEqual(len(runner.calls), 1)
        call = runner.calls[0]
        cmd = call["cmd"]
        self.assertEqual(cmd[:2], ["openclaw", "agent"])
        self.assertNotIn("--model", cmd)
        self.assertNotIn(DEFAULT_OPENCLAW_SHOW_NOTES_TRANSLATION_MODEL, cmd)
        self.assertNotIn("eval", cmd)
        self.assertNotIn("--prompt", cmd)
        self.assertIn("--message", cmd)
        self.assertIn("--json", cmd)
        self.assertIn("--timeout", cmd)
        self.assertEqual(cmd[cmd.index("--timeout") + 1], str(DEFAULT_OPENCLAW_TRANSLATION_TIMEOUT_SECONDS))
        prompt = cmd[cmd.index("--message") + 1]
        self.assertIn(chunk, prompt)
        self.assertEqual(call["timeout"], DEFAULT_OPENCLAW_TRANSLATION_TIMEOUT_SECONDS)
        self.assertTrue(call["capture_output"])
        self.assertTrue(call["text"])

    def test_agent_id_is_added_to_default_command(self):
        runner = RecordingRunner()

        translate_show_notes_chunk_with_openclaw(
            "Hello world.",
            agent_id="translator",
            run_subprocess=runner,
        )

        cmd = runner.calls[0]["cmd"]
        self.assertEqual(cmd[:4], ["openclaw", "agent", "--agent", "translator"])
        self.assertIn("--message", cmd)
        self.assertIn("--json", cmd)
        self.assertIn("--timeout", cmd)
        self.assertNotIn("--model", cmd)
        self.assertNotIn("eval", cmd)
        self.assertNotIn("--prompt", cmd)

    def test_empty_agent_id_keeps_default_command_without_agent_option(self):
        runner = RecordingRunner()

        translate_show_notes_chunk_with_openclaw(
            "Hello world.",
            agent_id="",
            run_subprocess=runner,
        )

        cmd = runner.calls[0]["cmd"]
        self.assertEqual(cmd[:2], ["openclaw", "agent"])
        self.assertNotIn("--agent", cmd)
        self.assertIn("--message", cmd)

    def test_custom_command_with_existing_agent_does_not_duplicate_agent_option(self):
        runner = RecordingRunner()

        translate_show_notes_chunk_with_openclaw(
            "Hello world.",
            openclaw_command=["openclaw", "agent", "--agent", "existing-agent"],
            agent_id="new-agent",
            run_subprocess=runner,
        )

        cmd = runner.calls[0]["cmd"]
        self.assertEqual(cmd.count("--agent"), 1)
        self.assertEqual(cmd[cmd.index("--agent") + 1], "existing-agent")
        self.assertNotIn("new-agent", cmd)
        self.assertIn("--message", cmd)

    def test_custom_command_without_agent_gets_agent_id(self):
        runner = RecordingRunner()

        translate_show_notes_chunk_with_openclaw(
            "Hello world.",
            openclaw_command=["openclaw", "agent"],
            agent_id="translator",
            run_subprocess=runner,
        )

        cmd = runner.calls[0]["cmd"]
        self.assertEqual(cmd[:4], ["openclaw", "agent", "--agent", "translator"])
        self.assertIn("--message", cmd)

    def test_extracts_translation_from_common_json_fields(self):
        cases = [
            ('{"text": "译文 text"}', "译文 text"),
            ('{"reply": "译文 reply"}', "译文 reply"),
            ('{"message": "译文 message"}', "译文 message"),
            ('{"content": "译文 content"}', "译文 content"),
            ('{"output": "译文 output"}', "译文 output"),
            ('{"response": "译文 response"}', "译文 response"),
            ('{"message": {"content": "嵌套译文"}}', "嵌套译文"),
            ('{"data": {"text": "data 译文"}}', "data 译文"),
            ('{"result": {"text": "result 译文"}}', "result 译文"),
            ('{"result": {"payloads": [{"text": "payload 译文"}]}}', "payload 译文"),
            (
                '{"result": {"payloads": [{"text": ""}, {"text": "第二个 payload 的译文"}]}}',
                "第二个 payload 的译文",
            ),
            ('{"result": {"payloads": [{"content": "payload content 译文"}]}}', "payload content 译文"),
            (
                '{"result": {"payloads": [{"message": {"content": "payload message content 译文"}}]}}',
                "payload message content 译文",
            ),
            ('["list 译文"]', "list 译文"),
            ('[{"text": "list dict 译文"}]', "list dict 译文"),
        ]

        for stdout, expected in cases:
            with self.subTest(stdout=stdout):
                self.assertEqual(extract_openclaw_translation_text(stdout), expected)

    def test_runner_extracts_translation_from_openclaw_result_payload_text(self):
        stdout = """
        {
          "status": "ok",
          "runId": "abc",
          "summary": "done",
          "result": {
            "meta": {
              "provider": "minimax-portal",
              "model": "MiniMax-M2.7"
            },
            "payloads": [
              {
                "text": "今天，我与一家领先能源技术公司的 CEO 进行了对话。"
              }
            ]
          }
        }
        """
        runner = RecordingRunner(FakeCompletedProcess(stdout=stdout, stderr="", returncode=0))

        result = translate_show_notes_chunk_with_openclaw("Hello world.", run_subprocess=runner)

        self.assertEqual(result, "今天，我与一家领先能源技术公司的 CEO 进行了对话。")

    def test_runner_appends_source_url_when_openclaw_translation_drops_it(self):
        stdout = """
        {
          "result": {
            "payloads": [
              {
                "text": "这是一段译文，但漏掉链接。"
              }
            ]
          }
        }
        """
        runner = RecordingRunner(FakeCompletedProcess(stdout=stdout, stderr="", returncode=0))

        result = translate_show_notes_chunk_with_openclaw(
            "Read https://example.com/report for details.",
            run_subprocess=runner,
        )

        self.assertIn("这是一段译文", result)
        self.assertIn("原文链接：", result)
        self.assertIn("- https://example.com/report", result)

    def test_summarizes_json_dict_stdout_schema(self):
        schema = summarize_openclaw_json_stdout_schema(
            '{"ok": true, "events": [{"type": "message"}], "final": {"content": "hello"}}'
        )

        self.assertIn("json dict", schema)
        self.assertIn("keys=", schema)
        self.assertIn("ok", schema)
        self.assertIn("events", schema)
        self.assertIn("final", schema)

    def test_summarizes_nested_json_stdout_schema(self):
        schema = summarize_openclaw_json_stdout_schema(
            '{"message": {"role": "assistant", "parts": [{"text": "译文"}]}, "data": {"id": "abc"}}'
        )

        self.assertIn("message keys", schema)
        self.assertIn("role", schema)
        self.assertIn("parts", schema)
        self.assertIn("data keys", schema)
        self.assertIn("id", schema)

    def test_summarizes_result_payloads_stdout_schema_without_values(self):
        schema = summarize_openclaw_json_stdout_schema(
            """
            {
              "result": {
                "meta": {"provider": "minimax-portal"},
                "payloads": [
                  {"text": "不要在 schema 摘要里输出完整译文", "type": "text"}
                ]
              }
            }
            """
        )

        self.assertIn("result keys", schema)
        self.assertIn("payloads", schema)
        self.assertIn("result.payloads", schema)
        self.assertIn("first_keys", schema)
        self.assertIn("text", schema)
        self.assertIn("type", schema)
        self.assertNotIn("不要在 schema 摘要里输出完整译文", schema)

    def test_summarizes_json_list_stdout_schema(self):
        schema = summarize_openclaw_json_stdout_schema('[{"type": "message", "content": "译文"}]')

        self.assertIn("json list", schema)
        self.assertIn("first_type=dict", schema)
        self.assertIn("first_keys", schema)
        self.assertIn("type", schema)
        self.assertIn("content", schema)

    def test_summarizes_non_json_stdout_schema(self):
        self.assertEqual(summarize_openclaw_json_stdout_schema("plain text"), "non-json stdout")

    def test_non_json_stdout_falls_back_to_stripped_text(self):
        self.assertEqual(
            extract_openclaw_translation_text("  这是非 JSON 译文。  \n"),
            "这是非 JSON 译文。",
        )

    def test_empty_json_or_json_without_text_raises(self):
        for stdout in ("{}", '{"foo": "bar"}', '{"message": {"role": "assistant"}}', "[]"):
            with self.subTest(stdout=stdout):
                with self.assertRaisesRegex(RuntimeError, "empty stdout|no translation text"):
                    extract_openclaw_translation_text(stdout)

    def test_prompt_preserves_structure_constraints_for_realistic_chunk(self):
        chunk = (
            "(00:03:11) - Comparing human vs AI sample efficiency\n"
            "- The power grid supply crunch\n"
            "https://example.com/report"
        )

        prompt = build_openclaw_show_notes_translation_prompt(chunk)

        self.assertIn("(00:03:11)", prompt)
        self.assertIn("- The power grid supply crunch", prompt)
        self.assertIn("https://example.com/report", prompt)
        self.assertIn("保留 URL", prompt)
        self.assertIn("保留时间戳", prompt)
        self.assertIn("保留项目符号", prompt)

    def test_nonzero_returncode_raises_with_returncode_and_stderr(self):
        runner = RecordingRunner(FakeCompletedProcess(stdout="", stderr="model failed", returncode=1))

        with self.assertRaisesRegex(RuntimeError, "returncode.*1.*model failed"):
            translate_show_notes_chunk_with_openclaw("Hello world.", run_subprocess=runner)

    def test_empty_stdout_raises(self):
        runner = RecordingRunner(FakeCompletedProcess(stdout="   ", stderr="", returncode=0))

        with self.assertRaisesRegex(RuntimeError, "empty stdout"):
            translate_show_notes_chunk_with_openclaw("Hello world.", run_subprocess=runner)

    def test_json_without_usable_text_raises(self):
        runner = RecordingRunner(
            FakeCompletedProcess(
                stdout='{"ok": true, "events": [{"type": "message"}]}',
                stderr="some stderr log",
                returncode=0,
            )
        )

        with self.assertRaisesRegex(RuntimeError, "returned no translation text") as ctx:
            translate_show_notes_chunk_with_openclaw("Hello world.", run_subprocess=runner)
        message = str(ctx.exception)
        self.assertIn("stdout_schema", message)
        self.assertIn("ok", message)
        self.assertIn("events", message)
        self.assertIn("stdout_excerpt", message)
        self.assertIn("stderr_excerpt", message)
        self.assertIn("some stderr log", message)

    def test_json_payload_without_usable_text_raises_with_payload_schema(self):
        runner = RecordingRunner(
            FakeCompletedProcess(
                stdout='{"result": {"payloads": [{"foo": "bar"}]}}',
                stderr="",
                returncode=0,
            )
        )

        with self.assertRaisesRegex(RuntimeError, "returned no translation text") as ctx:
            translate_show_notes_chunk_with_openclaw("Hello world.", run_subprocess=runner)
        message = str(ctx.exception)
        self.assertIn("stdout_schema", message)
        self.assertIn("result.payloads", message)
        self.assertIn("foo", message)
        self.assertIn("stdout_excerpt", message)

    def test_nonzero_returncode_includes_stdout_and_stderr_diagnostics(self):
        runner = RecordingRunner(
            FakeCompletedProcess(
                stdout='{"error": {"message": "bad request"}}',
                stderr="agent failed",
                returncode=1,
            )
        )

        with self.assertRaisesRegex(RuntimeError, "returncode 1") as ctx:
            translate_show_notes_chunk_with_openclaw("Hello world.", run_subprocess=runner)
        message = str(ctx.exception)
        self.assertIn("agent failed", message)
        self.assertIn("stdout_schema", message)
        self.assertIn("error", message)
        self.assertIn("stdout_excerpt", message)

    def test_timeout_is_not_swallowed(self):
        timeout = subprocess.TimeoutExpired(cmd=["openclaw"], timeout=120)
        runner = RecordingRunner(exc=timeout)

        with self.assertRaisesRegex(RuntimeError, "timed out|timeout"):
            translate_show_notes_chunk_with_openclaw("Hello world.", run_subprocess=runner)

    def test_subprocess_exception_is_not_swallowed(self):
        runner = RecordingRunner(exc=OSError("openclaw not found"))

        with self.assertRaisesRegex(RuntimeError, "openclaw not found"):
            translate_show_notes_chunk_with_openclaw("Hello world.", run_subprocess=runner)

    def test_invalid_inputs_raise_value_error(self):
        for value in (None, {}, [], "", "   "):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    translate_show_notes_chunk_with_openclaw(value, run_subprocess=RecordingRunner())

    def test_custom_command_prefix_is_used(self):
        runner = RecordingRunner()

        translate_show_notes_chunk_with_openclaw(
            "Hello world.",
            openclaw_command=["openclaw", "agent", "--agent", "translator"],
            run_subprocess=runner,
        )

        cmd = runner.calls[0]["cmd"]
        self.assertEqual(cmd[:4], ["openclaw", "agent", "--agent", "translator"])
        self.assertIn("--message", cmd)
        self.assertIn("--json", cmd)
        self.assertIn("--timeout", cmd)
        self.assertNotIn("--model", cmd)
        self.assertNotIn("eval", cmd)
        self.assertNotIn("--prompt", cmd)
        self.assertNotIn(DEFAULT_OPENCLAW_SHOW_NOTES_TRANSLATION_MODEL, cmd)

    def test_compatible_with_existing_chunk_runner(self):
        runner = RecordingRunner(FakeCompletedProcess(stdout="这是中文译文。"))

        result = translate_show_notes_chunks_with_runner(
            ["Hello world."],
            translate_chunk=lambda chunk, target_language="zh": translate_show_notes_chunk_with_openclaw(
                chunk,
                target_language=target_language,
                agent_id="translator",
                run_subprocess=runner,
            ),
        )

        self.assertEqual(result["status"], "ok")
        self.assertIn("这是中文译文。", result["translated_text"])
        self.assertEqual(result["translated_chunk_count"], 1)
        cmd = runner.calls[0]["cmd"]
        self.assertEqual(cmd[:4], ["openclaw", "agent", "--agent", "translator"])

    def test_no_file_or_environment_side_effects(self):
        runner = RecordingRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            before = set(os.listdir(tmpdir))
            old_env = dict(os.environ)
            result = translate_show_notes_chunk_with_openclaw(
                "Hello world.",
                run_subprocess=runner,
            )
            after = set(os.listdir(tmpdir))

        self.assertEqual(result, "这是中文译文。")
        self.assertEqual(before, after)
        self.assertEqual(os.environ, old_env)


if __name__ == "__main__":
    unittest.main(verbosity=2)
