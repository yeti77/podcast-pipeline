#!/usr/bin/env python3
import json
import subprocess
import unittest

import guest_search_adapter as gsa


class FakeCompletedProcess:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.body


class TestGuestSearchAdapter(unittest.TestCase):
    def test_build_guest_search_queries_keeps_current_hint_behavior(self):
        self.assertEqual(
            gsa.build_guest_search_queries("张三"),
            ["张三 嘉宾 简介 背景", "张三 biography profile"],
        )
        self.assertEqual(
            gsa.build_guest_search_queries("Freda Duan"),
            ["Freda Duan 嘉宾 简介 背景", "Freda Duan biography profile"],
        )
        self.assertEqual(
            gsa.build_guest_search_queries("Freda Duan", affiliation_hint="Altimeter"),
            gsa.build_guest_search_queries("Freda Duan"),
        )

    def test_parse_openclaw_results_from_results_dict(self):
        stdout = json.dumps(
            {
                "results": [
                    {"title": "One", "url": "https://one.example", "snippet": "Snippet one"},
                    {"title": "Two", "url": "https://two.example", "snippet": "Snippet two"},
                ]
            }
        )

        self.assertEqual(
            gsa.parse_openclaw_results(stdout),
            [
                {"title": "One", "url": "https://one.example", "snippet": "Snippet one"},
                {"title": "Two", "url": "https://two.example", "snippet": "Snippet two"},
            ],
        )

    def test_parse_openclaw_results_from_items_dict_with_description_fallback(self):
        stdout = json.dumps(
            {
                "items": [
                    {"title": "Profile", "url": "https://profile.example", "description": "Description text"}
                ]
            }
        )

        self.assertEqual(
            gsa.parse_openclaw_results(stdout),
            [{"title": "Profile", "url": "https://profile.example", "snippet": "Description text"}],
        )

    def test_parse_openclaw_results_from_list_and_limits_results(self):
        stdout = json.dumps(
            [
                {"title": "One", "url": "https://one.example", "snippet": "Snippet one"},
                {"title": "Two", "url": "https://two.example", "snippet": "Snippet two"},
                {"title": "Three", "url": "https://three.example", "snippet": "Snippet three"},
            ]
        )

        self.assertEqual(
            gsa.parse_openclaw_results(stdout, max_results=2),
            [
                {"title": "One", "url": "https://one.example", "snippet": "Snippet one"},
                {"title": "Two", "url": "https://two.example", "snippet": "Snippet two"},
            ],
        )

    def test_parse_openclaw_results_malformed_json_returns_empty(self):
        self.assertEqual(gsa.parse_openclaw_results("{bad-json"), [])

    def test_parse_openclaw_results_help_or_unavailable_output_returns_empty(self):
        help_stdout = (
            "Usage: openclaw [options] [command]\n"
            "Commands:\n"
            "  agent\n"
            "  config\n"
            "Docs: docs.openclaw.ai"
        )
        self.assertTrue(gsa.is_openclaw_help_or_unavailable_output(help_stdout))
        self.assertTrue(gsa.is_openclaw_help_or_unavailable_output("unknown command 'web-search'"))
        self.assertTrue(gsa.is_openclaw_help_or_unavailable_output("unknown option '--query'"))
        self.assertEqual(gsa.parse_openclaw_results(help_stdout), [])

    def test_parse_duckduckgo_results_from_html_fixture(self):
        html = """
        <html>
          <a class="result__a" href="https://one.example">One Result</a>
          <a class="result__a" href="https://two.example">Two Result</a>
          <a class="result__a" href="https://three.example">Three Result</a>
        </html>
        """

        self.assertEqual(
            gsa.parse_duckduckgo_results(html, max_results=2),
            [
                {"title": "One Result", "url": "https://one.example", "snippet": ""},
                {"title": "Two Result", "url": "https://two.example", "snippet": ""},
            ],
        )

    def test_search_openclaw_results_skip_duckduckgo(self):
        calls = {"run": [], "urlopen": 0}

        def fake_run(cmd, capture_output, text, timeout):
            calls["run"].append(cmd)
            return FakeCompletedProcess(
                json.dumps({"results": [{"title": "OpenClaw", "url": "https://open.example", "snippet": "Open"}]})
            )

        def forbidden_urlopen(*args, **kwargs):
            calls["urlopen"] += 1
            raise AssertionError("DuckDuckGo fallback should not be called")

        old_run = gsa.subprocess.run
        old_urlopen = gsa.urllib.request.urlopen
        try:
            gsa.subprocess.run = fake_run
            gsa.urllib.request.urlopen = forbidden_urlopen

            results = gsa.search_guest_background_openclaw("Freda Duan")
        finally:
            gsa.subprocess.run = old_run
            gsa.urllib.request.urlopen = old_urlopen

        self.assertEqual(
            results,
            [
                {"title": "OpenClaw", "url": "https://open.example", "snippet": "Open"},
                {"title": "OpenClaw", "url": "https://open.example", "snippet": "Open"},
            ],
        )
        self.assertEqual(calls["urlopen"], 0)
        self.assertEqual(calls["run"][0], ["openclaw", "web-search", "--query", "Freda Duan 嘉宾 简介 背景", "--limit", "5"])

    def test_search_openclaw_empty_results_returns_empty_without_duckduckgo(self):
        calls = {"urlopen": 0}

        def fake_run(cmd, capture_output, text, timeout):
            return FakeCompletedProcess(json.dumps({"results": []}))

        def forbidden_urlopen(*args, **kwargs):
            calls["urlopen"] += 1
            raise AssertionError("DuckDuckGo fallback should not be called")

        old_run = gsa.subprocess.run
        old_urlopen = gsa.urllib.request.urlopen
        try:
            gsa.subprocess.run = fake_run
            gsa.urllib.request.urlopen = forbidden_urlopen

            results = gsa.search_guest_background_openclaw("Freda Duan")
        finally:
            gsa.subprocess.run = old_run
            gsa.urllib.request.urlopen = old_urlopen

        self.assertEqual(results, [])
        self.assertEqual(calls["urlopen"], 0)

    def test_search_subprocess_exception_returns_empty_without_duckduckgo(self):
        calls = {"urlopen": 0}

        def failing_run(cmd, capture_output, text, timeout):
            raise RuntimeError("openclaw unavailable")

        def forbidden_urlopen(*args, **kwargs):
            calls["urlopen"] += 1
            raise AssertionError("DuckDuckGo fallback should not be called")

        old_run = gsa.subprocess.run
        old_urlopen = gsa.urllib.request.urlopen
        try:
            gsa.subprocess.run = failing_run
            gsa.urllib.request.urlopen = forbidden_urlopen

            results = gsa.search_guest_background_openclaw("Freda Duan")
        finally:
            gsa.subprocess.run = old_run
            gsa.urllib.request.urlopen = old_urlopen

        self.assertEqual(results, [])
        self.assertEqual(calls["urlopen"], 0)

    def test_search_openclaw_help_output_returns_empty_without_duckduckgo(self):
        calls = {"urlopen": 0}
        help_stdout = (
            "Usage: openclaw [options] [command]\n"
            "Commands:\n"
            "  agent\n"
            "  config\n"
            "  models\n"
            "Docs: docs.openclaw.ai"
        )

        def fake_run(cmd, capture_output, text, timeout):
            return FakeCompletedProcess(help_stdout)

        def forbidden_urlopen(*args, **kwargs):
            calls["urlopen"] += 1
            raise AssertionError("DuckDuckGo fallback should not be called")

        old_run = gsa.subprocess.run
        old_urlopen = gsa.urllib.request.urlopen
        try:
            gsa.subprocess.run = fake_run
            gsa.urllib.request.urlopen = forbidden_urlopen

            results = gsa.search_guest_background_openclaw("Freda Duan")
        finally:
            gsa.subprocess.run = old_run
            gsa.urllib.request.urlopen = old_urlopen

        self.assertEqual(results, [])
        self.assertEqual(calls["urlopen"], 0)

    def test_search_openclaw_unknown_command_or_option_returns_empty(self):
        outputs = ["unknown command 'web-search'", "unknown option '--query'"]
        for output in outputs:
            with self.subTest(output=output):
                calls = {"urlopen": 0}

                def fake_run(cmd, capture_output, text, timeout):
                    return FakeCompletedProcess(stdout="", stderr=output, returncode=1)

                def forbidden_urlopen(*args, **kwargs):
                    calls["urlopen"] += 1
                    raise AssertionError("DuckDuckGo fallback should not be called")

                old_run = gsa.subprocess.run
                old_urlopen = gsa.urllib.request.urlopen
                try:
                    gsa.subprocess.run = fake_run
                    gsa.urllib.request.urlopen = forbidden_urlopen

                    results = gsa.search_guest_background_openclaw("Freda Duan")
                finally:
                    gsa.subprocess.run = old_run
                    gsa.urllib.request.urlopen = old_urlopen

                self.assertEqual(results, [])
                self.assertEqual(calls["urlopen"], 0)

    def test_search_openclaw_non_json_stdout_returns_empty(self):
        calls = {"urlopen": 0}

        def fake_run(cmd, capture_output, text, timeout):
            return FakeCompletedProcess("some non json output")

        def forbidden_urlopen(*args, **kwargs):
            calls["urlopen"] += 1
            raise AssertionError("DuckDuckGo fallback should not be called")

        old_run = gsa.subprocess.run
        old_urlopen = gsa.urllib.request.urlopen
        try:
            gsa.subprocess.run = fake_run
            gsa.urllib.request.urlopen = forbidden_urlopen

            results = gsa.search_guest_background_openclaw("Freda Duan")
        finally:
            gsa.subprocess.run = old_run
            gsa.urllib.request.urlopen = old_urlopen

        self.assertEqual(results, [])
        self.assertEqual(calls["urlopen"], 0)

    def test_search_openclaw_timeout_returns_empty(self):
        calls = {"urlopen": 0}

        def fake_run(cmd, capture_output, text, timeout):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

        def forbidden_urlopen(*args, **kwargs):
            calls["urlopen"] += 1
            raise AssertionError("DuckDuckGo fallback should not be called")

        old_run = gsa.subprocess.run
        old_urlopen = gsa.urllib.request.urlopen
        try:
            gsa.subprocess.run = fake_run
            gsa.urllib.request.urlopen = forbidden_urlopen

            results = gsa.search_guest_background_openclaw("Freda Duan")
        finally:
            gsa.subprocess.run = old_run
            gsa.urllib.request.urlopen = old_urlopen

        self.assertEqual(results, [])
        self.assertEqual(calls["urlopen"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
