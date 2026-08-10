#!/usr/bin/env python3
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from guest_background_generator import (
    BACKGROUND_MODEL_TIMEOUT_SECONDS,
    CONFIRMED_GUEST_FALLBACK,
    OPENCLAW_BACKGROUND_MODEL,
    build_background_prompt,
    generate_background_from_show_notes,
    parse_english_guest_role_list_from_show_notes,
    run_background_model,
    should_accept_model_background,
)


class TestGuestBackgroundGenerator(unittest.TestCase):
    def _show_notes_info(self, patterns):
        return {
            "text": "节目元数据中的嘉宾介绍片段",
            "patterns_found": patterns,
            "source_type": "episode_show_notes",
            "source_quality": "primary",
        }

    def test_show_notes_generates_natural_sentence_from_role_org_and_research(self):
        info = self._show_notes_info([
            "职务/头衔：合伙人",
            "机构/公司：Altimeter Capital",
            "专长/研究：AI基础设施投资",
        ])

        result = generate_background_from_show_notes("Freda Duan", [], info)

        self.assertIsNotNone(result)
        self.assertIn("Freda Duan 是 Altimeter Capital 的合伙人", result["background_zh"])
        self.assertIn("专注AI基础设施投资", result["background_zh"])
        self.assertEqual(result["confidence"], "medium")
        self.assertEqual(result["source_quality"], "primary")
        self.assertIn("episode_show_notes", result["source_quality_summary"])
        self.assertNotIn("{'title':", result["background_zh"])

    def test_english_co_founder_semantics_are_preserved(self):
        info = self._show_notes_info([
            "职务/头衔：co-founder",
            "机构/公司：Andreessen Horowitz",
        ])

        result = generate_background_from_show_notes("Marc Andreessen", [], info)

        self.assertIsNotNone(result)
        self.assertIn("Marc Andreessen 是 Andreessen Horowitz 的联合创始人", result["background_zh"])
        self.assertNotIn("co-founder", result["background_zh"])

    def test_generic_writer_without_supporting_info_uses_existing_fallback(self):
        info = self._show_notes_info(["职务/头衔：writer"])

        result = generate_background_from_show_notes("David Paulides", [], info)

        self.assertIsNotNone(result)
        self.assertEqual(result["background_zh"], CONFIRMED_GUEST_FALLBACK)
        self.assertNotIn("David Paulides是作家", result["background_zh"])

    def test_noisy_book_or_topic_phrase_does_not_enter_background(self):
        info = self._show_notes_info([
            "职务/头衔：Partner",
            "机构/公司：capital allocation strategy",
            "著作/案例：创投观察第2集",
        ])

        result = generate_background_from_show_notes("Guest Name", [], info)

        self.assertIsNotNone(result)
        self.assertNotIn("capital allocation strategy", result["background_zh"])
        self.assertNotIn("创投观察", result["background_zh"])
        self.assertNotIn("代表作", result["background_zh"])

    def test_insufficient_show_notes_returns_none(self):
        info = self._show_notes_info([])

        result = generate_background_from_show_notes("Guest Name", [], info)

        self.assertIsNone(result)

    def test_does_not_call_subprocess_or_write_files(self):
        info = self._show_notes_info([
            "职务/头衔：基金经理",
            "机构/公司：南方基金",
            "专长/研究：大类资产配置",
        ])

        with tempfile.TemporaryDirectory() as tmpdir:
            before = set(os.listdir(tmpdir))
            with patch.object(subprocess, "run", side_effect=AssertionError("subprocess forbidden")):
                result = generate_background_from_show_notes("恽雷", [], info)
            after = set(os.listdir(tmpdir))

        self.assertIsNotNone(result)
        self.assertIn("恽雷 是 南方基金 的基金经理", result["background_zh"])
        self.assertIn("专注大类资产配置", result["background_zh"])
        self.assertEqual(before, after)

    def test_build_background_prompt_preserves_source_format_and_constraints(self):
        snippet = "A" * 250
        sources = [
            {
                "title": "Official Profile",
                "url": "https://example.com/profile",
                "snippet": snippet,
                "quality": "primary",
            },
            {
                "title": "Interview Notes",
                "url": "https://example.com/interview",
                "snippet": "",
                "quality": "secondary",
            },
        ]

        prompt = build_background_prompt("Jane Doe", sources)

        self.assertIn("嘉宾「Jane Doe」", prompt)
        self.assertIn("[1[primary]] Official Profile", prompt)
        self.assertIn("摘要：" + ("A" * 200), prompt)
        self.assertNotIn("A" * 201, prompt)
        self.assertIn("[2[secondary]] Interview Notes (https://example.com/interview)", prompt)
        self.assertIn("只基于搜索结果", prompt)
        self.assertIn("不要编造信息", prompt)
        self.assertIn("80-150字", prompt)
        self.assertIn("夸大形容", prompt)
        self.assertIn("私生活八卦", prompt)

    def test_build_background_prompt_uses_only_first_three_sources(self):
        sources = [
            {"title": "Source 1", "url": "https://example.com/1", "snippet": "one", "quality": "primary"},
            {"title": "Source 2", "url": "https://example.com/2", "snippet": "two", "quality": "secondary"},
            {"title": "Source 3", "url": "https://example.com/3", "snippet": "three", "quality": "weak"},
            {"title": "Source 4", "url": "https://example.com/4", "snippet": "four", "quality": "primary"},
        ]

        prompt = build_background_prompt("Jane Doe", sources)

        self.assertIn("Source 1", prompt)
        self.assertIn("Source 2", prompt)
        self.assertIn("Source 3", prompt)
        self.assertNotIn("Source 4", prompt)

    def test_should_accept_model_background_matches_current_acceptance_rules(self):
        self.assertTrue(should_accept_model_background(
            "Jane Doe是某机构合伙人，长期关注企业软件与人工智能投资，相关经历有助于理解本期讨论。"
        ))
        self.assertFalse(should_accept_model_background(""))
        self.assertFalse(should_accept_model_background("   "))
        self.assertFalse(should_accept_model_background("太短"))
        self.assertFalse(should_accept_model_background("信息不足，无法判断该嘉宾背景。"))
        self.assertFalse(should_accept_model_background("无法生成可靠背景介绍。"))

    def test_run_background_model_returns_text_from_openclaw_agent_json(self):
        completed = type("Completed", (), {
            "returncode": 0,
            "stdout": '{"result":{"payloads":[{"text":"张三是能源行业专家。"}]}}',
            "stderr": "",
        })()
        custom_env = {"EXISTING_KEY": "keep-me"}

        with patch("guest_background_generator.subprocess.run", return_value=completed) as run_mock:
            result = run_background_model("prompt text", env=custom_env)

        self.assertEqual(result, "张三是能源行业专家。")
        run_mock.assert_called_once()
        args, kwargs = run_mock.call_args
        self.assertEqual(
            args[0],
            [
                "openclaw",
                "agent",
                "--agent",
                "main",
                "--message",
                "prompt text",
                "--json",
                "--timeout",
                str(BACKGROUND_MODEL_TIMEOUT_SECONDS),
            ],
        )
        self.assertNotIn("--model", args[0])
        self.assertNotIn(OPENCLAW_BACKGROUND_MODEL, args[0])
        self.assertNotIn("eval", args[0])
        self.assertNotIn("--prompt", args[0])
        self.assertTrue(kwargs["capture_output"])
        self.assertTrue(kwargs["text"])
        self.assertEqual(kwargs["timeout"], BACKGROUND_MODEL_TIMEOUT_SECONDS)
        self.assertEqual(kwargs["env"]["EXISTING_KEY"], "keep-me")
        self.assertEqual(custom_env, {"EXISTING_KEY": "keep-me"})

    def test_run_background_model_accepts_plain_text_stdout(self):
        completed = type("Completed", (), {
            "returncode": 0,
            "stdout": "  张三是能源行业专家。  \n",
            "stderr": "",
        })()

        with patch("guest_background_generator.subprocess.run", return_value=completed):
            result = run_background_model("prompt text")

        self.assertEqual(result, "张三是能源行业专家。")

    def test_run_background_model_returns_none_for_json_without_text(self):
        completed = type("Completed", (), {
            "returncode": 0,
            "stdout": '{"result":{"payloads":[{"kind":"debug"}]}}',
            "stderr": "",
        })()

        with patch("guest_background_generator.subprocess.run", return_value=completed):
            result = run_background_model("prompt text")

        self.assertIsNone(result)

    def test_run_background_model_returns_none_for_empty_stdout(self):
        completed = type("Completed", (), {"returncode": 0, "stdout": " \n "})()

        with patch("guest_background_generator.subprocess.run", return_value=completed):
            result = run_background_model("prompt text")

        self.assertIsNone(result)

    def test_run_background_model_returns_none_for_nonzero_returncode(self):
        completed = type("Completed", (), {"returncode": 1, "stdout": "some output"})()

        with patch("guest_background_generator.subprocess.run", return_value=completed):
            result = run_background_model("prompt text")

        self.assertIsNone(result)

    def test_run_background_model_returns_none_for_subprocess_exception(self):
        with patch("guest_background_generator.subprocess.run", side_effect=RuntimeError("boom")):
            result = run_background_model("prompt text")

        self.assertIsNone(result)

    def test_run_background_model_returns_none_for_timeout(self):
        timeout = subprocess.TimeoutExpired(cmd=["openclaw"], timeout=BACKGROUND_MODEL_TIMEOUT_SECONDS)

        with patch("guest_background_generator.subprocess.run", side_effect=timeout):
            result = run_background_model("prompt text")

        self.assertIsNone(result)


class TestW24GuestBackgroundRoleListKnownGaps(unittest.TestCase):
    def _show_notes_info(self, text, patterns):
        return {
            "text": text,
            "patterns_found": patterns,
            "source_type": "episode_show_notes",
            "source_quality": "primary",
        }

    def _background_zh(self, guest_name, text, patterns):
        result = generate_background_from_show_notes(
            guest_name,
            [],
            self._show_notes_info(text, patterns),
        )
        self.assertIsNotNone(result)
        return result["background_zh"]

    def test_cameron_hanes_role_list_generates_natural_background(self):
        show_notes = (
            'Cameron Hanes is a bowhunter, outdoorsman, endurance athlete, '
            'author, and host of the podcast "Keep Hammering with Cameron Hanes."'
        )

        background = self._background_zh(
            "Cameron Hanes",
            show_notes,
            ["职务/头衔：author"],
        )

        self.assertIn("Cameron Hanes", background)
        self.assertIn("弓猎者", background)
        self.assertIn("户外运动者", background)
        self.assertIn("耐力运动员", background)
        self.assertIn("作者", background)
        self.assertIn("Keep Hammering with Cameron Hanes", background)

    def test_joey_diaz_role_list_generates_natural_background(self):
        show_notes = "Joey Diaz is a stand-up comedian, actor, and writer."

        background = self._background_zh(
            "Joey Diaz",
            show_notes,
            ["职务/头衔：writer"],
        )

        self.assertIn("Joey Diaz", background)
        self.assertIn("单口喜剧演员", background)
        self.assertIn("演员", background)
        self.assertIn("作家", background)

    def test_terry_bradshaw_role_list_generates_natural_background(self):
        show_notes = (
            "Terry Bradshaw is a retired NFL quarterback, actor, sports analyst, "
            "and musician."
        )

        background = self._background_zh(
            "Terry Bradshaw",
            show_notes,
            ["职务/头衔：actor"],
        )

        self.assertIn("Terry Bradshaw", background)
        self.assertIn("退役 NFL 四分卫", background)
        self.assertIn("演员", background)
        self.assertIn("体育评论员", background)
        self.assertIn("音乐人", background)

    def test_dean_radin_credentialed_role_generates_natural_background(self):
        show_notes = "Dean Radin, PhD, is Chief Scientist at the Institute of Noetic Sciences."

        background = self._background_zh(
            "Dean Radin",
            show_notes,
            [
                "职务/头衔：Chief Scientist",
                "机构/公司：Institute of Noetic Sciences",
            ],
        )

        self.assertIn("Dean Radin", background)
        self.assertIn("Institute of Noetic Sciences", background)
        self.assertIn("首席科学家", background)

    def test_george_coyle_role_list_generates_person_first_background(self):
        show_notes = (
            "George Coyle is the co-author of the new Market Wizards book. "
            "He is a writer, trader, system designer, money manager, and market strategist."
        )

        background = self._background_zh(
            "George Coyle",
            show_notes,
            [
                "著作/案例：new Market Wizards book",
                "职务/头衔：writer",
            ],
        )

        self.assertIn("George Coyle", background)
        self.assertIn("Market Wizards", background)
        self.assertIn("作家", background)
        self.assertIn("交易员", background)
        self.assertIn("系统设计师", background)
        self.assertIn("资金管理人", background)
        self.assertIn("市场策略师", background)
        self.assertNotIn("studying top fund", background)

    def test_role_list_parser_negative_cases_keep_existing_fallback_boundary(self):
        for show_notes in [
            "Sponsor of Chat With Traders Podcast: Trade The Pool.",
            "Trading Disclaimer: this does not constitute advice.",
            "This episode discusses markets and strategy.",
        ]:
            with self.subTest(show_notes=show_notes):
                result = generate_background_from_show_notes(
                    "Guest Name",
                    [],
                    self._show_notes_info(show_notes, ["职务/头衔：writer"]),
                )

                self.assertIsNotNone(result)
                self.assertEqual(result["background_zh"], CONFIRMED_GUEST_FALLBACK)

    def test_studying_top_fund_phrase_does_not_generate_org_background(self):
        show_notes = "After studying top fund managers and over 100 years of market history, the author wrote a book."
        result = generate_background_from_show_notes(
            "George Coyle",
            [],
            self._show_notes_info(
                show_notes,
                [
                    "机构/公司：studying top fund",
                    "职务/头衔：writer",
                ],
            ),
        )

        if result is not None:
            self.assertNotIn("studying top fund", result["background_zh"])
            self.assertNotIn("George Coyle任职于studying top fund", result["background_zh"])


class TestEnglishGuestRoleListParserExpectedBehavior(unittest.TestCase):
    """TODO specs for a future pure role-list parser helper.

    These tests intentionally do not import or call a parser that does not
    exist yet. They keep the desired contract explicit while preserving the
    current passing test suite.
    """

    def _expected_positive_cases(self):
        return [
            {
                "name": "Cameron Hanes role list",
                "guest_name": "Cameron Hanes",
                "show_notes": (
                    'Cameron Hanes is a bowhunter, outdoorsman, endurance athlete, '
                    'author, and host of the podcast "Keep Hammering with Cameron Hanes."'
                ),
                "expected": {
                    "guest_name": "Cameron Hanes",
                    "roles": ["bowhunter", "outdoorsman", "endurance athlete", "author"],
                    "host_of": "Keep Hammering with Cameron Hanes",
                    "org": None,
                    "title": None,
                    "credential": None,
                    "source": "show_notes_is_a_role_list",
                },
            },
            {
                "name": "Joey Diaz role list",
                "guest_name": "Joey Diaz",
                "show_notes": "Joey Diaz is a stand-up comedian, actor, and writer.",
                "expected": {
                    "guest_name": "Joey Diaz",
                    "roles": ["stand-up comedian", "actor", "writer"],
                    "host_of": None,
                    "org": None,
                    "title": None,
                    "credential": None,
                    "source": "show_notes_is_a_role_list",
                },
            },
            {
                "name": "Terry Bradshaw role list",
                "guest_name": "Terry Bradshaw",
                "show_notes": (
                    "Terry Bradshaw is a retired NFL quarterback, actor, "
                    "sports analyst, and musician."
                ),
                "expected": {
                    "guest_name": "Terry Bradshaw",
                    "roles": ["retired NFL quarterback", "actor", "sports analyst", "musician"],
                    "host_of": None,
                    "org": None,
                    "title": None,
                    "credential": None,
                    "source": "show_notes_is_a_role_list",
                },
            },
            {
                "name": "Dean Radin credentialed title org",
                "guest_name": "Dean Radin",
                "show_notes": "Dean Radin, PhD, is Chief Scientist at the Institute of Noetic Sciences.",
                "expected": {
                    "guest_name": "Dean Radin",
                    "credential": "PhD",
                    "title": "Chief Scientist",
                    "org": "Institute of Noetic Sciences",
                    "roles": [],
                    "host_of": None,
                    "source": "show_notes_credentialed_title_org",
                },
            },
            {
                "name": "George Coyle multi-sentence role list",
                "guest_name": "George Coyle",
                "show_notes": (
                    "George Coyle is the co-author of the new Market Wizards book. "
                    "He is a writer, trader, system designer, money manager, and market strategist."
                ),
                "expected": {
                    "guest_name": "George Coyle",
                    "roles": [
                        "co-author of the new Market Wizards book",
                        "writer",
                        "trader",
                        "system designer",
                        "money manager",
                        "market strategist",
                    ],
                    "host_of": None,
                    "org": None,
                    "title": None,
                    "credential": None,
                    "source": "show_notes_is_a_role_list",
                },
            },
        ]

    def _expected_negative_cases(self):
        return [
            "This episode discusses markets and strategy.",
            "Sponsor of Chat With Traders Podcast: Trade The Pool.",
            "Trading Disclaimer: this does not constitute advice.",
            "The book Market Wizards and The Next Generation are discussed.",
        ]

    def test_expected_positive_cases_define_future_parser_contract(self):
        cases = self._expected_positive_cases()

        self.assertEqual(
            [case["guest_name"] for case in cases],
            ["Cameron Hanes", "Joey Diaz", "Terry Bradshaw", "Dean Radin", "George Coyle"],
        )
        for case in cases:
            with self.subTest(case=case["name"]):
                parsed = parse_english_guest_role_list_from_show_notes(
                    case["guest_name"],
                    case["show_notes"],
                )
                self.assertEqual(parsed, case["expected"])

    def test_expected_negative_cases_define_non_role_list_inputs(self):
        cases = self._expected_negative_cases()

        self.assertEqual(len(cases), 4)
        self.assertIn("Sponsor of Chat With Traders Podcast", cases[1])
        self.assertIn("Trading Disclaimer", cases[2])
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNone(parse_english_guest_role_list_from_show_notes("Guest Name", text))


class TestW24RealGuestBackgroundVariantsKnownGaps(unittest.TestCase):
    def _show_notes_info(self, text, patterns):
        return {
            "text": text,
            "patterns_found": patterns,
            "source_type": "episode_show_notes",
            "source_quality": "primary",
        }

    def _background_zh(self, guest_name, text, patterns):
        result = generate_background_from_show_notes(
            guest_name,
            [],
            self._show_notes_info(text, patterns),
        )
        self.assertIsNotNone(result)
        return result["background_zh"]

    def test_george_coyle_real_w24_text_uses_role_list_not_studying_top_fund(self):
        show_notes = (
            "Jack Schwager and George Coyle team up to look at what it takes to master the markets.\n"
            "After studying top fund managers and over 100 years of market history, Jack and George wrote "
            "Market Wizards: The Next Generation.\n"
            "George Coyle:\n"
            "George is the co-author of the new Market Wizards book. He is a writer, trader, system designer, "
            "money manager, and market strategist."
        )

        background = self._background_zh(
            "George Coyle",
            show_notes,
            [
                "机构/公司：studying top fund",
                "机构/公司：fund",
                "著作/案例：new Market Wizards book",
                "职务/头衔：writer",
            ],
        )

        self.assertIn("George Coyle", background)
        self.assertIn("Market Wizards", background)
        self.assertIn("作家", background)
        self.assertIn("交易员", background)
        self.assertIn("系统设计师", background)
        self.assertIn("资金管理人", background)
        self.assertIn("市场策略师", background)
        self.assertNotIn("studying top fund", background)
        self.assertNotIn("George Coyle任职于studying top fund", background)

    def test_george_coyle_real_w24_text_parser_uses_first_name_continuation(self):
        show_notes = (
            "Jack Schwager and George Coyle team up to look at what it takes to master the markets.\n"
            "After studying top fund managers and over 100 years of market history, Jack and George wrote "
            "Market Wizards: The Next Generation.\n"
            "George Coyle:\n"
            "George is the co-author of the new Market Wizards book. He is a writer, trader, system designer, "
            "money manager, and market strategist."
        )

        parsed = parse_english_guest_role_list_from_show_notes("George Coyle", show_notes)

        self.assertIsNotNone(parsed)
        self.assertEqual(
            parsed["roles"],
            [
                "co-author of the new Market Wizards book",
                "writer",
                "trader",
                "system designer",
                "money manager",
                "market strategist",
            ],
        )

    def test_cameron_hanes_real_w24_podcasts_plural_variant_generates_background(self):
        show_notes = (
            "Cameron Hanes is a bowhunter, outdoorsman, endurance athlete, author, and host of the podcasts "
            "“Keep Hammering with Cameron Hanes,” “Sh*t Talkers Weekly,” and “Lift. Run. Shoot.” "
            "His most recent book is “Undeniable: How to Reach the Top and Stay There.”"
        )

        background = self._background_zh("Cameron Hanes", show_notes, ["职务/头衔：author"])

        self.assertIn("Cameron Hanes", background)
        self.assertIn("弓猎者", background)
        self.assertIn("户外运动者", background)
        self.assertIn("耐力运动员", background)
        self.assertIn("作者", background)
        self.assertIn("Keep Hammering with Cameron Hanes", background)

    def test_joey_diaz_real_w24_host_and_book_variant_generates_background(self):
        show_notes = (
            "Joey Diaz is a stand-up comedian, actor, and writer. "
            "He is the host of the podcast “The Church of What’s Happening Now: The New Testament” "
            "and the author of “Tremendous: The Life of a Comedy Savage.”"
        )

        background = self._background_zh("Joey Diaz", show_notes, ["职务/头衔：writer"])

        self.assertIn("Joey Diaz", background)
        self.assertIn("单口喜剧演员", background)
        self.assertIn("演员", background)
        self.assertIn("作家", background)

    def test_terry_bradshaw_real_w24_whose_variant_generates_background(self):
        show_notes = (
            "Terry Bradshaw is a retired NFL quarterback whose 14 seasons with the Pittsburgh Steelers "
            "included four Super Bowl wins, leading to his induction into the Pro Football Hall of Fame. "
            "Bradshaw is also a musician, actor, sports analyst, author, entrepreneur, commentator, and co-host of “Fox NFL Sunday.”"
        )

        background = self._background_zh("Terry Bradshaw", show_notes, ["职务/头衔：actor"])

        self.assertIn("Terry Bradshaw", background)
        self.assertIn("退役 NFL 四分卫", background)
        self.assertIn("演员", background)
        self.assertIn("体育评论员", background)
        self.assertIn("音乐人", background)

    def test_dean_radin_real_w24_long_title_org_uses_natural_formatting(self):
        show_notes = (
            "Dean Radin, PhD, is Chief Scientist at the Institute of Noetic Sciences (IONS), "
            "Associate Distinguished Professor at the California Institute of Integral Studies, and co-founder "
            "and chairman of the neuroengineering company Cognigenics. His latest book is “The Science of Magic: "
            "How the Mind Weaves the Fabric of Reality.”"
        )

        background = self._background_zh(
            "Dean Radin",
            show_notes,
            [
                "职务/头衔：Chief Scientist",
                "机构/公司：Institute of Noetic Sciences",
            ],
        )

        self.assertEqual(
            background,
            "Dean Radin 是 Institute of Noetic Sciences (IONS) 的首席科学家。",
        )

    def test_negative_texts_do_not_generate_role_list_backgrounds(self):
        for show_notes in [
            "After studying top fund managers and over 100 years of market history...",
            "Sponsor of Chat With Traders Podcast: Trade The Pool.",
            "Trading Disclaimer: this does not constitute advice.",
            "This episode discusses markets and strategy.",
        ]:
            with self.subTest(show_notes=show_notes):
                parsed = parse_english_guest_role_list_from_show_notes("Guest Name", show_notes)
                self.assertIsNone(parsed)


class TestW28GuestBackgroundGovernance(unittest.TestCase):
    def _result(self, guest_name, show_notes, patterns):
        result = generate_background_from_show_notes(
            guest_name,
            [],
            {
                "text": show_notes,
                "patterns_found": patterns,
                "source_type": "episode_show_notes",
                "source_quality": "primary",
            },
        )
        self.assertIsNotNone(result)
        return result["background_zh"]

    def test_craig_albert_title_and_org_use_natural_chinese_format(self):
        show_notes = (
            "For this episode, host Ed Crooks and regular guest Dr. Melissa Lott are joined by "
            "Craig Albert, the President and COO of Bechtel, one of the world’s biggest engineering "
            "and construction companies."
        )

        background = self._result(
            "Craig Albert",
            show_notes,
            ["职务/头衔：COO", "机构/公司：Bechtel"],
        )

        self.assertEqual(background, "Craig Albert 是 Bechtel 的总裁兼 COO。")
        self.assertNotIn("BechtelCOO", background)

    def test_pat_dorsey_rejects_weak_org_fragments_and_formats_founder(self):
        show_notes = (
            "Pat Dorsey is the Founder of Dorsey Asset Management, a global public equity manager "
            "focused on companies with competitive advantages and long investment runways."
        )

        background = self._result(
            "Pat Dorsey",
            show_notes,
            [
                "职务/头衔：Founder",
                "机构/公司：and long investment",
                "机构/公司：investment",
                "机构/公司：Dorsey Asset Management",
            ],
        )

        self.assertEqual(background, "Pat Dorsey 是 Dorsey Asset Management 的创始人。")
        self.assertNotIn("and long investment", background)
        self.assertNotIn("ManagementFounder", background)

    def test_ali_siddiq_role_list_generates_supported_background(self):
        show_notes = (
            "Ali Siddiq is a comedian, author, and public speaker. His new special, "
            '"My Father," is now streaming on YouTube.'
        )

        background = self._result("Ali Siddiq", show_notes, ["职务/头衔：author"])

        self.assertIn("Ali Siddiq", background)
        self.assertIn("喜剧演员", background)
        self.assertIn("作者", background)
        self.assertIn("公共演说家", background)
        self.assertNotEqual(background, CONFIRMED_GUEST_FALLBACK)


if __name__ == "__main__":
    unittest.main(verbosity=2)
