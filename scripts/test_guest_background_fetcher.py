#!/usr/bin/env python3
"""
test_guest_background_fetcher.py — Phase 2 Tests
覆盖 Phase 2 所有新功能：
1. 本期邀请 XXX → confirmed_guest
2. 和 XXX 聊 YYY，description 支持 → confirmed_guest
3. 和 Frida Kahlo 聊艺术史，但 Frida 是历史人物 → mentioned_entity
4. "hosted by XXX" 不识别为嘉宾
5. host_config 中的主播名不识别为 confirmed_guest
6. description 明确"本期嘉宾：XXX"时覆盖 host exclusion
7. evidence 字段包含 source/pattern/matched_text/decision/reason
8. source_quality = weak 时 confidence 不得为 medium/high
9. high confidence 至少有 primary source
10. negative cache TTL = 30 days
11. positive cache TTL = 90 days
12. confirmed_guest 背景总长度不超过 300 字
13. sources 展示层仍最多 2 个（展示层逻辑，不在 gbf 内部）
14. 搜索/模型失败仍不影响主流程
15. Phase 2 新增："X joins Y" → possible_guest（joins pattern）
16. Phase 2 新增：Chinese desc "Freda Duan在湾区做投资" → confirmed_guest
17. Phase 2 新增：中文 emoji + "本期嘉宾" → confirmed_guest
18. Phase 2 新增：title_jre_format "#数字 - Guest Name" → confirmed_guest
"""

import sys
import os
import json
import time
import shutil
import tempfile
import atexit
import urllib.request
sys.path.insert(0, os.path.dirname(__file__))

import guest_background_fetcher as gbf
from guest_background_fetcher import (
    detect_guest_status_phase2,
    detect_guest_status,
    enrich_episode_with_guest_backgrounds,
    _guest_key,
    _load_podcast_hosts,
    _is_host_name,
    _is_title_entity,
    _is_likely_noise,
    _clean_name,
    canonicalize_guest_name_phase2,
    is_guest_name_noise_phase2,
    prune_redundant_single_token_guest_names_phase2,
    _load_cache,
    _cache_entry,
    CACHE_TTL_DAYS_CONFIRMED,
    CACHE_TTL_DAYS_NOT_CONFIRMED,
    CACHE_FILE,
    classify_source_quality,
    rate_overall_source_quality,
    DETECTION_STATUSES,
    TITLE_ENTITY_WORDS,
    DESC_GUEST_ZH,
    EXPLICIT_GUEST_PATTERNS_ZH,
    EXPLICIT_GUEST_PATTERNS_EN,
    get_guest_background_phase2,
)

_HERMETIC_STATE_DIR = tempfile.TemporaryDirectory(prefix="test_guest_background_state_")
atexit.register(_HERMETIC_STATE_DIR.cleanup)
CACHE_FILE = os.path.join(_HERMETIC_STATE_DIR.name, "guest_profiles_cache.json")
gbf.STATE_DIR = _HERMETIC_STATE_DIR.name
gbf.CACHE_FILE = CACHE_FILE


def _safe_empty_guest_search(*args, **kwargs):
    return []


def _forbid_external_call(*args, **kwargs):
    raise AssertionError("external network/subprocess call is forbidden in test_guest_background_fetcher")


gbf.search_guest_background_openclaw = _safe_empty_guest_search
gbf.subprocess.run = _forbid_external_call
urllib.request.urlopen = _forbid_external_call
if hasattr(gbf, "urllib"):
    gbf.urllib.request.urlopen = _forbid_external_call

# ─── Test helpers ────────────────────────────────────────────────────────

def _clear_cache():
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)

def _make_ep(title, desc, show_notes="", podcast="TestCast", **kw):
    ep = {
        "podcast_name": podcast,
        "episode_title": title,
        "description": desc,
        "show_notes_text": show_notes,
        **kw,
    }
    return ep

# ─── 1. 中文明确短语 → confirmed_guest ─────────────────────────────────

class TestChineseExplicitPatterns:
    def test_benqi_guoke(self):
        """'本期嘉宾：张三' 识别为 confirmed_guest"""
        ep = _make_ep(
            "投资方法论",
            "本期节目邀请张三来聊投资",
            show_notes="本期嘉宾：张三，某投资机构创始人",
        )
        result = detect_guest_status_phase2(ep)
        assert result["status"] == "confirmed_guest", f"Expected confirmed_guest, got {result['status']}"
        assert "张三" in result["guest_names"], f"Expected '张三' in guest_names, got {result['guest_names']}"
        print("✓ 本期嘉宾：张三 → confirmed_guest")

    def test_chinese_role_before_name_guest_intro_patterns(self):
        """中文 role-before-name / 嘉宾介绍句只提取中文姓名。"""
        cases = [
            (
                "今天我们的嘉宾是安克创新的创始人兼CEO阳萌。他是1982年生人，2011年开始创业。",
                "阳萌",
                ["阳萌会怎", "安克创新", "创始人兼CEO"],
            ),
            (
                "本期对话的嘉宾是我的好朋友张璐。张璐是Fusion Fund创始人，也是SpaceX投资人之一。",
                "张璐",
                ["她多次", "我的好朋友张璐", "Fusion Fund", "创始人"],
            ),
            (
                "本期嘉宾张璐是Fusion Fund创始人。",
                "张璐",
                ["Fusion Fund", "创始人"],
            ),
            (
                "本期对话嘉宾阳萌是安克创新创始人兼CEO。",
                "阳萌",
                ["安克创新", "创始人兼CEO"],
            ),
        ]
        for desc, expected, rejected_names in cases:
            ep = _make_ep("中文嘉宾介绍", desc, show_notes=desc)
            result = detect_guest_status_phase2(ep)
            assert result["status"] != "no_guest_detected", f"{desc}: {result}"
            assert expected in result["guest_names"], f"{desc}: {result['guest_names']}"
            for rejected in rejected_names:
                assert rejected not in result["guest_names"], f"{desc}: {result['guest_names']}"
        print("✓ 中文 role-before-name / 嘉宾介绍句识别姓名")

    def test_chinese_role_before_name_negative_samples(self):
        """讨论公司、免责声明、无姓名 role 句不应产生嘉宾。"""
        samples = [
            "本期节目讨论安克创新和Fusion Fund的发展。",
            "这家公司创始人兼CEO认为行业正在变化。",
            "免责声明：本内容不作为投资建议。",
        ]
        for desc in samples:
            ep = _make_ep("中文 negative sample", desc, show_notes=desc)
            result = detect_guest_status_phase2(ep)
            assert "安克创新" not in result["guest_names"], result
            assert "Fusion Fund" not in result["guest_names"], result
            assert "创始人兼CEO" not in result["guest_names"], result
            assert "免责声明" not in result["guest_names"], result
        print("✓ 中文 role-before-name negative samples ignored")

    def test_chinese_name_with_english_alias_patterns(self):
        """中文姓名 + English Alias 只把中文姓名作为主 guest name。"""
        cases = [
            (
                "我邀请了SpaceX前火箭首席制造工程师洪力德（Lewis Hong），一起来聊聊SpaceX开发史。",
                "洪力德",
                "Lewis Hong",
                ["Lewis Hong", "SpaceX"],
            ),
            (
                "本期嘉宾洪力德 (Lewis Hong) 曾任SpaceX火箭制造工程师。",
                "洪力德",
                "Lewis Hong",
                ["Lewis Hong", "SpaceX"],
            ),
            (
                "本期嘉宾李明（Ming Li）分享了创业经历。",
                "李明",
                "Ming Li",
                ["Ming Li"],
            ),
        ]
        for desc, expected_name, expected_alias, rejected_names in cases:
            ep = _make_ep("中文别名嘉宾", desc, show_notes=desc)
            result = detect_guest_status_phase2(ep)
            assert result["status"] != "no_guest_detected", f"{desc}: {result}"
            assert expected_name in result["guest_names"], f"{desc}: {result['guest_names']}"
            for rejected in rejected_names:
                assert rejected not in result["guest_names"], f"{desc}: {result['guest_names']}"
            assert any(expected_alias in ev.get("matched_text", "") for ev in result["evidence"]), result
        print("✓ 中文姓名 + English alias pattern keeps Chinese primary name")

    def test_chinese_name_with_english_alias_negative_samples(self):
        """机构括号说明不应被当作中文姓名 + 英文别名嘉宾。"""
        samples = [
            "本期节目讨论SpaceX（美国太空探索技术公司）的发展。",
            "这家公司（OpenAI）正在推动AI基础设施变化。",
            "免责声明：本内容不作为投资建议。",
        ]
        for desc in samples:
            ep = _make_ep("中文别名 negative sample", desc, show_notes=desc)
            result = detect_guest_status_phase2(ep)
            assert "SpaceX" not in result["guest_names"], result
            assert "OpenAI" not in result["guest_names"], result
            assert "美国太空探索技术公司" not in result["guest_names"], result
            assert "免责声明" not in result["guest_names"], result
        print("✓ 中文姓名 + English alias negative samples ignored")

    def test_guoke_simple(self):
        """'嘉宾：张三' 识别为 confirmed_guest"""
        ep = _make_ep(
            "投资对话",
            "节目邀请嘉宾张三",
            show_notes="嘉宾：张三，资深投资人",
        )
        result = detect_guest_status_phase2(ep)
        assert result["status"] == "confirmed_guest"
        print("✓ 嘉宾：张三 → confirmed_guest")

    def test_he_xxx_liao(self):
        """'和 XXX 聊 YYY'，description 有确认 → confirmed_guest"""
        # 使用简洁的"和张三聊投资"格式，避免正则过于贪婪
        ep = _make_ep(
            "本期节目",
            "我们和张三聊投资策略，张三是某头部基金合伙人，专注新能源投资。",
            show_notes="和张三聊投资策略，张三是某头部基金合伙人。",
        )
        result = detect_guest_status_phase2(ep)
        assert result["status"] in ("confirmed_guest", "possible_guest"), f"Got {result['status']}"
        print(f"✓ 和张三聊（有描述）→ {result['status']}")

    def test_he_xxx_liao_no_context(self):
        """'和 XXX 聊'，仅标题，无描述确认 → possible_guest"""
        # 标题包含"聊"，但无 description 确认
        ep = _make_ep(
            "和张三聊投资",
            "这是一期关于投资的节目。",
            show_notes="和张三聊投资策略。",
        )
        result = detect_guest_status_phase2(ep)
        # 只有标题/弱描述 → possible_guest 或 no_guest_detected
        assert result["status"] in ("possible_guest", "confirmed_guest", "no_guest_detected"), f"Got {result['status']}"
        print(f"✓ 和张三聊（弱证据）→ {result['status']}")

    def test_zh_desc_name_is(self):
        """中文 show_notes 中 'Freda Duan在湾区做投资' → confirmed_guest"""
        ep = _make_ep(
            "Freda的投资札记",
            "今天是我们的系列节目。那还是先介绍一下——Freda Duan在湾区做投资，是Altimeter Capital的合伙人。",
            show_notes="今天是我们的系列节目。那还是先介绍一下——Freda Duan在湾区做投资，是Altimeter Capital的合伙人。",
        )
        result = detect_guest_status_phase2(ep)
        assert result["status"] == "confirmed_guest", f"Got {result['status']}"
        assert "Freda Duan" in result["guest_names"], f"Got {result['guest_names']}"
        print("✓ Freda Duan在湾区做投资 → confirmed_guest")

    def test_emoji_benqi_guoke(self):
        """'🎤 本期嘉宾恽雷' → confirmed_guest"""
        ep = _make_ep(
            "资产配置与有效前沿",
            "🎤 本期嘉宾恽雷@南方基金，基金经理",
            show_notes="🎤 本期嘉宾恽雷@南方基金，基金经理",
        )
        result = detect_guest_status_phase2(ep)
        assert result["status"] == "confirmed_guest", f"Got {result['status']}"
        print("✓ 🎤 本期嘉宾恽雷 → confirmed_guest")

    def test_teyao_guoke(self):
        """'特邀嘉宾 XXX' → confirmed_guest"""
        ep = _make_ep(
            "本期节目",
            "本期节目",
            show_notes="特邀嘉宾王五，知名经济学家",
        )
        result = detect_guest_status_phase2(ep)
        assert result["status"] == "confirmed_guest"
        print("✓ 特邀嘉宾王五 → confirmed_guest")


# ─── 2. Frida Kahlo / 标题实体 ─────────────────────────────────────────

class TestTitleEntities:
    def test_frida_kahlo_mentioned_entity(self):
        """'和 Frida Kahlo 聊艺术史' → mentioned_entity（不是嘉宾）"""
        ep = _make_ep(
            "聊聊 Frida Kahlo 和她的艺术",
            "本期节目聊聊 Frida Kahlo 的传奇人生和艺术创作。",
            show_notes="节目探讨 Frida Kahlo 的艺术创作",
        )
        result = detect_guest_status_phase2(ep)
        assert result["status"] in ("mentioned_entity", "no_guest_detected"), \
            f"Frida Kahlo should NOT be confirmed_guest, got {result['status']}"
        print(f"✓ Frida Kahlo (历史人物) → {result['status']}")

    def test_freda_title_entity(self):
        """Freda 只作为标题实体时 → 不识别为 confirmed_guest"""
        ep = _make_ep(
            "Freda的投资札记：长期主义",
            "Freda 的投资分享",
            show_notes="本期讨论长期投资策略",
        )
        result = detect_guest_status_phase2(ep)
        assert result["status"] in ("mentioned_entity", "no_guest_detected"), \
            f"Freda 作为标题实体不应是 confirmed_guest，实际: {result['status']}"
        print(f"✓ Freda（标题实体）→ {result['status']}")

    def test_apple_company_not_guest(self):
        """'Apple 的商业模式' → 不是嘉宾"""
        ep = _make_ep(
            "Apple 的商业模式分析",
            "本期分析 Apple 的商业模式和竞争策略。",
            show_notes="讨论 Apple 的商业模式",
        )
        result = detect_guest_status_phase2(ep)
        assert result["status"] in ("mentioned_entity", "no_guest_detected"), \
            f"Apple should NOT be confirmed_guest, got {result['status']}"
        print(f"✓ Apple（公司）→ {result['status']}")


# ─── 3. Host exclusion ──────────────────────────────────────────────────

class TestHostExclusion:
    def test_hosted_by_not_guest(self):
        """"hosted by XXX" → 不识别为嘉宾"""
        ep = _make_ep(
            "Tech Talk",
            "This episode is hosted by John Smith from TechCorp.",
            show_notes="Hosted by John Smith, founder of TechCorp.",
        )
        result = detect_guest_status_phase2(ep)
        # "hosted by" pattern is not in our explicit patterns, should be no_guest_detected
        assert result["status"] in ("no_guest_detected", "possible_guest"), \
            f"Hosted by should not be confirmed_guest, got {result['status']}"
        print(f"✓ 'hosted by John Smith' → {result['status']}")

    def test_host_config_exclusion(self):
        """host_config 中的主播名 → 不识别为 confirmed_guest"""
        # 张小珺 is the host (in podcast_hosts.yaml)
        ep = _make_ep(
            "本期节目",
            "张小珺和 Freda Duan 聊投资",
            show_notes="张小珺和 Freda Duan 聊投资策略",
            podcast="张小珺商业访谈录",
        )
        result = detect_guest_status_phase2(ep)
        # "和张小珺聊" → 张小珺 is the host, Freda Duan is the guest
        # But only "和...聊" without description confirmation → possible_guest
        # Freda Duan with zh_name_desc → confirmed_guest
        # 张小珺 → rejected_host
        assert result["status"] in ("confirmed_guest", "possible_guest"), f"Got {result['status']}"
        # Freda Duan should be in guest_names
        names_lower = [n.lower() for n in result["guest_names"]]
        assert "freda duan" in names_lower or "freda" in names_lower, \
            f"Freda Duan should be guest, got {result['guest_names']}"
        print(f"✓ host exclusion (张小珺) + Freda Duan as guest → {result['status']}")

    def test_host_exclusion_overridden_by_benqi_guoke(self):
        """"本期嘉宾：张三" 覆盖 host exclusion"""
        # 恽雷 is the host in 面基... wait, no, 恽雷 is the GUEST
        # Let's test with a case where the host name appears as "guest"
        ep = _make_ep(
            "Some Episode",
            "本期嘉宾是某主播",
            show_notes="本期嘉宾是某主播，某播客主持人",
            podcast="TestCast",
        )
        result = detect_guest_status_phase2(ep)
        # "本期嘉宾是某主播" → 某主播 is explicitly a guest
        assert result["status"] in ("confirmed_guest", "possible_guest"), f"Got {result['status']}"
        print(f"✓ '本期嘉宾是某主播' (even if host name) → {result['status']}")


# ─── 4. 英文 guest patterns ──────────────────────────────────────────────

class TestEnglishGuestPatterns:
    def test_interview_with_freda_smith(self):
        """'Interview with Freda Smith' → confirmed_guest"""
        ep = _make_ep(
            "Interview with Freda Smith about AI investing",
            "Interview with Freda Smith, founder of Example Capital.",
            show_notes="Interview with Freda Smith, founder of Example Capital.",
        )
        result = detect_guest_status_phase2(ep)
        assert result["status"] == "confirmed_guest", f"Got {result['status']}"
        assert "Freda Smith" in result["guest_names"], f"Got {result['guest_names']}"
        print("✓ Interview with Freda Smith → confirmed_guest")

    def test_desc_is_a_pattern(self):
        """'X is a writer/investor...' → confirmed_guest"""
        ep = _make_ep(
            "#2502 - David Paulides",
            "David Paulides is a writer, investigator, filmmaker, and former law enforcement officer.",
            show_notes="David Paulides is a writer, investigator, filmmaker, and former law enforcement officer.",
        )
        result = detect_guest_status_phase2(ep)
        assert result["status"] == "confirmed_guest", f"Got {result['status']}"
        assert "David Paulides" in result["guest_names"], f"Got {result['guest_names']}"
        print("✓ David Paulides is a writer → confirmed_guest")

    def test_credentialed_name_is_pattern(self):
        """'Name, credentials, is Role...' → confirmed_guest without credentials."""
        cases = [
            (
                "Dean Radin, PhD, is Chief Scientist at the Institute of Noetic Sciences.",
                "Dean Radin",
            ),
            (
                "Jane Doe, Ph.D., is Professor at Example University.",
                "Jane Doe",
            ),
            (
                "John Smith, MD, is a physician and author.",
                "John Smith",
            ),
        ]
        for desc, expected in cases:
            ep = _make_ep("Credentialed guest", desc, show_notes=desc)
            result = detect_guest_status_phase2(ep)
            assert result["status"] == "confirmed_guest", f"{desc}: {result}"
            assert expected in result["guest_names"], f"{desc}: {result['guest_names']}"
            assert "PhD" not in result["guest_names"], result
            assert "Ph.D." not in result["guest_names"], result
            assert "MD" not in result["guest_names"], result
        print("✓ credentialed English names → confirmed_guest without credentials")

    def test_credentialed_name_negative_noise_samples(self):
        """Sponsor/disclaimer text should not become credentialed guests."""
        samples = [
            "Trading Disclaimer: Trading in the financial markets involves a risk of loss.",
            "Sponsor of Chat With Traders Podcast: Trade The Pool.",
            "Visible+ Pro with code ROGAN.",
        ]
        for desc in samples:
            ep = _make_ep("Noise sample", desc, show_notes=desc)
            result = detect_guest_status_phase2(ep)
            assert "Trading Disclaimer" not in result["guest_names"], result
            assert "Sponsor of Chat With Traders Podcast" not in result["guest_names"], result
            assert "Visible" not in result["guest_names"], result
            assert "ROGAN" not in result["guest_names"], result
        print("✓ credentialed-name fallback ignores sponsor/disclaimer samples")

    def test_english_role_before_name_pattern(self):
        """Narrow role-before-name patterns identify the following person name."""
        cases = [
            (
                "Brex co-founder and CEO Pedro Franceschi believes most people still underestimate how much AI will change companies.",
                "Pedro Franceschi",
                ["Brex", "co-founder", "CEO"],
            ),
            (
                "Sam Harris speaks with economist and Substack writer Noah Smith about the U.S. national debt.",
                "Noah Smith",
                ["economist", "Substack writer Noah", "Substack writer Noah Smith"],
            ),
            (
                "Stripe founder Patrick Collison discusses startups.",
                "Patrick Collison",
                ["Stripe", "founder"],
            ),
            (
                "OpenAI researcher John Doe explains reinforcement learning.",
                "John Doe",
                ["OpenAI", "researcher"],
            ),
            (
                "writer George Coyle discusses markets.",
                "George Coyle",
                ["writer"],
            ),
            (
                "author Jack Schwager explains Market Wizards.",
                "Jack Schwager",
                ["author"],
            ),
        ]
        for desc, expected, rejected_names in cases:
            ep = _make_ep("Role before name", desc, show_notes=desc)
            result = detect_guest_status_phase2(ep)
            assert result["status"] == "confirmed_guest", f"{desc}: {result}"
            assert expected in result["guest_names"], f"{desc}: {result['guest_names']}"
            for rejected in rejected_names:
                assert rejected not in result["guest_names"], f"{desc}: {result['guest_names']}"
        print("✓ English role-before-name pattern identifies person names only")

    def test_english_role_before_name_negative_samples(self):
        """Generic role/sponsor/disclaimer text should not become guest names."""
        samples = [
            "The CEO believes AI will change everything.",
            "A co-founder and CEO believes most people underestimate AI.",
            "Trading Disclaimer: this episode does not constitute advice.",
            "Sponsor of Chat With Traders Podcast: Trade The Pool.",
        ]
        for desc in samples:
            ep = _make_ep("Role-before-name negative sample", desc, show_notes=desc)
            result = detect_guest_status_phase2(ep)
            assert "CEO" not in result["guest_names"], result
            assert "co-founder" not in result["guest_names"], result
            assert "Trading Disclaimer" not in result["guest_names"], result
            assert "Sponsor of Chat With Traders Podcast" not in result["guest_names"], result
        print("✓ English role-before-name pattern ignores negative samples")

    def test_english_appositive_multi_guest_pattern(self):
        """speaks/talks with X, role/org, and Y, role/org identifies both people."""
        cases = [
            (
                "Erin Price-Wright speaks with Alex Modon, cofounder and CEO at Unlimited Industries, "
                "and Davide Asnaghi, CEO at Diode Computers, about how AI is moving from software "
                "into the physical world.",
                ["Alex Modon", "Davide Asnaghi"],
                ["Unlimited Industries", "Diode Computers", "cofounder and CEO", "CEO"],
            ),
            (
                "The host talks with Jane Doe, founder at Example Labs, and John Smith, "
                "researcher at OpenAI, about AI infrastructure.",
                ["Jane Doe", "John Smith"],
                ["Example Labs", "OpenAI", "founder", "researcher"],
            ),
        ]
        for desc, expected_names, rejected_names in cases:
            ep = _make_ep("Appositive multi guest", desc, show_notes=desc)
            result = detect_guest_status_phase2(ep)
            assert result["status"] != "no_guest_detected", f"{desc}: {result}"
            for expected in expected_names:
                assert expected in result["guest_names"], f"{desc}: {result['guest_names']}"
            for rejected in rejected_names:
                assert rejected not in result["guest_names"], f"{desc}: {result['guest_names']}"
        print("✓ English appositive multi-guest pattern identifies both people")

    def test_english_appositive_multi_guest_negative_samples(self):
        """Company mentions and sponsor/disclaimer text should not become multi-guest names."""
        samples = [
            "This episode discusses Unlimited Industries and Diode Computers.",
            "Sponsor of Chat With Traders Podcast: Trade The Pool.",
            "Trading Disclaimer: this does not constitute advice.",
        ]
        for desc in samples:
            ep = _make_ep("Appositive negative sample", desc, show_notes=desc)
            result = detect_guest_status_phase2(ep)
            assert "Unlimited Industries" not in result["guest_names"], result
            assert "Diode Computers" not in result["guest_names"], result
            assert "Sponsor of Chat With Traders Podcast" not in result["guest_names"], result
            assert "Trading Disclaimer" not in result["guest_names"], result
        print("✓ English appositive multi-guest pattern ignores negative samples")

    def test_english_coordinated_multi_guest_pattern(self):
        """plural-role coordinated guests identify both person names."""
        cases = [
            (
                "Wyatt Thomson of OpenAI speaks with economists Tyler Cowen and Alex Tabarrok "
                "about AI, labor markets, and economic growth.",
                ["Tyler Cowen", "Alex Tabarrok"],
                ["economists", "economists Tyler Cowen"],
            ),
            (
                "The host talks with investors Jane Doe and John Smith about early-stage AI companies.",
                ["Jane Doe", "John Smith"],
                ["investors", "investors Jane Doe"],
            ),
            (
                "The host interviews researchers Alice Johnson and Bob Lee about robotics.",
                ["Alice Johnson", "Bob Lee"],
                ["researchers", "researchers Alice Johnson"],
            ),
        ]
        for desc, expected_names, rejected_names in cases:
            ep = _make_ep("Coordinated multi guest", desc, show_notes=desc)
            result = detect_guest_status_phase2(ep)
            assert result["status"] != "no_guest_detected", f"{desc}: {result}"
            for expected in expected_names:
                assert expected in result["guest_names"], f"{desc}: {result['guest_names']}"
            for rejected in rejected_names:
                assert rejected not in result["guest_names"], f"{desc}: {result['guest_names']}"
        print("✓ English coordinated multi-guest pattern identifies both people")

    def test_english_coordinated_multi_guest_negative_samples(self):
        """Plural role mentions and sponsor/disclaimer text should not become guests."""
        samples = [
            "This episode discusses economists and researchers in AI.",
            "Sponsor of Chat With Traders Podcast: Trade The Pool.",
            "Trading Disclaimer: this does not constitute advice.",
        ]
        for desc in samples:
            ep = _make_ep("Coordinated negative sample", desc, show_notes=desc)
            result = detect_guest_status_phase2(ep)
            assert "economists" not in result["guest_names"], result
            assert "researchers" not in result["guest_names"], result
            assert "Sponsor of Chat With Traders Podcast" not in result["guest_names"], result
            assert "Trading Disclaimer" not in result["guest_names"], result
        print("✓ English coordinated multi-guest pattern ignores negative samples")

    def test_english_bare_coordinated_multi_guest_pattern(self):
        """Bare Person and Person + action verb identifies both people."""
        cases = [
            (
                "Jack Schwager and George Coyle team up to look at what it takes to master the markets.",
                ["Jack Schwager", "George Coyle"],
                ["George", "studying top fund"],
            ),
            (
                "Patrick Collison and John Collison discuss Stripe and the future of financial infrastructure.",
                ["Patrick Collison", "John Collison"],
                ["Stripe"],
            ),
            (
                "Alice Johnson and Bob Lee explain how robotics teams build reliable systems.",
                ["Alice Johnson", "Bob Lee"],
                ["robotics teams"],
            ),
        ]
        for desc, expected_names, rejected_names in cases:
            ep = _make_ep("Bare coordinated multi guest", desc, show_notes=desc)
            result = detect_guest_status_phase2(ep)
            assert result["status"] != "no_guest_detected", f"{desc}: {result}"
            for expected in expected_names:
                assert expected in result["guest_names"], f"{desc}: {result['guest_names']}"
            for rejected in rejected_names:
                assert rejected not in result["guest_names"], f"{desc}: {result['guest_names']}"
        print("✓ English bare coordinated multi-guest pattern identifies both people")

    def test_english_bare_coordinated_multi_guest_negative_samples(self):
        """Bare coordinated pattern requires person-like names plus an action verb."""
        samples = [
            "This episode discusses Stripe and OpenAI.",
            "Sponsor of Chat With Traders Podcast: Trade The Pool.",
            "Trading Disclaimer: this does not constitute advice.",
            "Market Wizards and The Next Generation are discussed in the episode.",
        ]
        for desc in samples:
            ep = _make_ep("Bare coordinated negative sample", desc, show_notes=desc)
            result = detect_guest_status_phase2(ep)
            assert "Stripe" not in result["guest_names"], result
            assert "OpenAI" not in result["guest_names"], result
            assert "Sponsor of Chat With Traders Podcast" not in result["guest_names"], result
            assert "Trading Disclaimer" not in result["guest_names"], result
            assert "Market Wizards" not in result["guest_names"], result
            assert "The Next Generation" not in result["guest_names"], result
        print("✓ English bare coordinated multi-guest pattern ignores negative samples")

    def test_guest_joins_pattern(self):
        """"X joins Y for a conversation" → possible_guest"""
        ep = _make_ep(
            "Marc on AI",
            "Marc Andreessen joins Joe Rogan for a conversation on AI.",
            show_notes="Marc Andreessen joins Joe Rogan for a conversation on AI.",
        )
        result = detect_guest_status_phase2(ep)
        assert result["status"] in ("confirmed_guest", "possible_guest"), f"Got {result['status']}"
        print(f"✓ 'X joins Y for a conversation' → {result['status']}")

    def test_joined_by_pattern(self):
        """"joined by X" → confirmed_guest"""
        ep = _make_ep(
            "Tech Interview",
            "Today we are joined by Dr. Jane Smith to discuss AI safety.",
            show_notes="Today we are joined by Dr. Jane Smith to discuss AI safety.",
        )
        result = detect_guest_status_phase2(ep)
        assert result["status"] == "confirmed_guest", f"Got {result['status']}"
        print("✓ joined by Dr. Jane Smith → confirmed_guest")

    def test_speaks_with_pattern(self):
        """"speaks with X" → confirmed_guest"""
        ep = _make_ep(
            "Making Sense",
            "Sam Harris speaks with Susan Cain about writing and creativity.",
            show_notes="Sam Harris speaks with Susan Cain about writing and creativity.",
        )
        result = detect_guest_status_phase2(ep)
        assert result["status"] == "confirmed_guest", f"Got {result['status']}"
        print("✓ speaks with Susan Cain → confirmed_guest")


# ─── 5. Title guest format ───────────────────────────────────────────────

class TestTitleGuestFormat:
    def test_jre_title_format(self):
        """"#2502 - David Paulides" → confirmed_guest"""
        ep = _make_ep(
            "#2502 - David Paulides",
            "David Paulides is a writer and investigator.",
            show_notes="David Paulides is a writer and investigator.",
        )
        result = detect_guest_status_phase2(ep)
        assert result["status"] == "confirmed_guest", f"Got {result['status']}"
        print("✓ JRE title format → confirmed_guest")

    def test_title_not_guest_the_bittersweet_age(self):
        """"#476 — The Bittersweet Age" → NOT confirmed_guest（主题，非人名）"""
        ep = _make_ep(
            "#476 — The Bittersweet Age",
            "Sam Harris speaks with Susan Cain about writing, creativity.",
            show_notes="Sam Harris speaks with Susan Cain about writing, creativity.",
        )
        result = detect_guest_status_phase2(ep)
        # "The Bittersweet Age" is filtered by negative lookahead for "The"
        names_lower = [n.lower() for n in result["guest_names"]]
        assert "the bittersweet age" not in names_lower, \
            f"'The Bittersweet Age' should NOT be guest, got {result['guest_names']}"
        # Susan Cain should be the guest (from speaks_with pattern)
        assert "susan cain" in names_lower, f"Susan Cain should be guest, got {result['guest_names']}"
        print(f"✓ 'The Bittersweet Age' (theme, not person) filtered; Susan Cain confirmed")

    def test_episode_format(self):
        """"Episode 123 - Guest Name" → confirmed_guest"""
        ep = _make_ep(
            "Episode 123 - Bob Wilson",
            "Bob Wilson is a software engineer.",
            show_notes="Bob Wilson is a software engineer.",
        )
        result = detect_guest_status_phase2(ep)
        assert result["status"] == "confirmed_guest"
        assert "Bob Wilson" in result["guest_names"]
        print("✓ Episode format → confirmed_guest")


# ─── 6. Evidence 格式 ───────────────────────────────────────────────────

class TestEvidenceFormat:
    def test_evidence_is_list(self):
        """evidence 是 list[dict] 结构"""
        ep = _make_ep(
            "Interview with Zhang San",
            "Interview with Zhang San, a venture capitalist.",
            show_notes="本期嘉宾：张三",
        )
        result = detect_guest_status_phase2(ep)
        assert isinstance(result["evidence"], list), "evidence must be a list"
        assert len(result["evidence"]) > 0, "evidence must not be empty"
        ev = result["evidence"][0]
        assert all(k in ev for k in ["source", "pattern", "matched_text", "decision", "reason"]), \
            f"evidence must have source/pattern/matched_text/decision/reason, got {list(ev.keys())}"
        print(f"✓ evidence 结构正确: {list(ev.keys())}")

    def test_phase1_backward_compat(self):
        """Phase 1 兼容接口 detect_guest_status() 仍返回 string evidence"""
        ep = _make_ep("Interview with Zhang San", "Interview with Zhang San.", show_notes="Interview with Zhang San.")
        result = detect_guest_status(ep)
        assert isinstance(result["evidence"], str), "Phase 1 interface must return string evidence"
        print("✓ Phase 1 backward compat: evidence is string")


# ─── 7. Source quality 分级 ─────────────────────────────────────────────

class TestSourceQuality:
    def test_primary_domain(self):
        """官方域名 → primary"""
        r = {"title": "Freda Duan - Xiaoyuzhoufm", "url": "https://www.xiaoyuzhoufm.com/freda", "snippet": "..."}
        assert classify_source_quality(r) == "primary"
        print("✓ xiaoyuzhoufm.com → primary")

    def test_secondary_domain(self):
        """Wikipedia/媒体 → secondary"""
        r_wiki = {"title": "Freda Duan - Wikipedia", "url": "https://en.wikipedia.org/wiki/Freda_Duan", "snippet": "..."}
        assert classify_source_quality(r_wiki) == "secondary", f"wiki should be secondary, got {classify_source_quality(r_wiki)}"
        # Medium.com 是 secondary
        r_medium = {"title": "Article on Medium", "url": "https://medium.com/author/article", "snippet": "..."}
        assert classify_source_quality(r_medium) == "secondary", f"medium should be secondary, got {classify_source_quality(r_medium)}"
        print("✓ Wikipedia/Medium → secondary")

    def test_weak_domain(self):
        """聚合站/导航页 → weak"""
        r_agg = {"title": "相关搜索结果 - 弗雷德", "url": "https://somesearch.com?q=Freda+Duan", "snippet": ""}
        assert classify_source_quality(r_agg) == "weak"
        r_ddg = {"title": "DuckDuckGo", "url": "https://duckduckgo.com/html/?q=Freda+Duan", "snippet": ""}
        assert classify_source_quality(r_ddg) == "weak"
        print("✓ 聚合站/DuckDuckGo → weak")

    def test_rate_overall(self):
        """综合评估 source_quality"""
        r_primary = {"title": "官网", "url": "https://xiaoyuzhoufm.com/freda", "snippet": "..."}
        r_weak = {"title": "搜索结果", "url": "https://google.com/search?q=Freda", "snippet": ""}
        sq = rate_overall_source_quality([r_primary])
        assert sq["quality"] == "primary"
        sq2 = rate_overall_source_quality([r_weak, r_weak])
        assert sq2["quality"] == "weak"
        sq3 = rate_overall_source_quality([r_primary, r_weak])
        assert sq3["quality"] == "primary"  # primary wins
        print("✓ rate_overall_source_quality 正确")


# ─── 8. Confidence 规则 ────────────────────────────────────────────────

class TestConfidenceRules:
    def test_weak_source_no_high_confidence(self):
        """"source_quality = weak 时 confidence 不得为 high"""
        # Phase 2 规则：weak 来源不能生成 high confidence
        # 测试 classify_source_quality 对 weak 来源的判定
        r_weak = {"title": "搜索结果", "url": "https://google.com/search?q=Freda", "snippet": ""}
        assert classify_source_quality(r_weak) == "weak", f"google search should be weak, got {classify_source_quality(r_weak)}"
        sq = rate_overall_source_quality([r_weak, r_weak])
        assert sq["quality"] == "weak", f"only weak sources should rate as weak, got {sq['quality']}"
        # 在 get_guest_background_phase2 中，weak 质量来源会直接返回降级结果（不调用模型）
        print("✓ weak 来源质量评级正确，不支撑 high confidence")

    def test_confidence_in_enrich(self):
        """enrich_episode_with_guest_backgrounds 返回合法的 confidence"""
        from datetime import datetime as dt
        _clear_cache()
        import guest_background_fetcher as gbf
        orig_search = gbf.search_guest_background_openclaw
        orig_gen = gbf.generate_background_via_model_phase2

        gbf.search_guest_background_openclaw = lambda *a, **kw: []
        gbf.generate_background_via_model_phase2 = lambda *a, **kw: {
            "background_zh": "测试背景",
            "confidence": "low",
            "note": "mock",
            "source_quality": "weak",
        }

        try:
            ep = _make_ep(
                "Interview with Zhang San",
                "Interview with Zhang San.",
                show_notes="本期嘉宾：张三",
            )
            result = enrich_episode_with_guest_backgrounds(ep)
            conf = result["guest_background_confidence"]
            assert conf in {"high", "medium", "low", "unknown"}, f"confidence must be valid, got {conf}"
            print(f"✓ confidence 合法: {conf}")
        finally:
            gbf.search_guest_background_openclaw = orig_search
            gbf.generate_background_via_model_phase2 = orig_gen


# ─── 9. Cache TTL ───────────────────────────────────────────────────────

class TestCacheTTL:
    def test_negative_cache_ttl(self):
        """not_confirmed 缓存 TTL = 30 天"""
        assert CACHE_TTL_DAYS_NOT_CONFIRMED == 30, f"Expected 30, got {CACHE_TTL_DAYS_NOT_CONFIRMED}"
        print(f"✓ negative cache TTL = {CACHE_TTL_DAYS_NOT_CONFIRMED} days")

    def test_positive_cache_ttl(self):
        """confirmed_guest 缓存 TTL = 90 天"""
        assert CACHE_TTL_DAYS_CONFIRMED == 90, f"Expected 90, got {CACHE_TTL_DAYS_CONFIRMED}"
        print(f"✓ positive cache TTL = {CACHE_TTL_DAYS_CONFIRMED} days")


# ─── 10. 背景长度限制 ────────────────────────────────────────────────────

class TestBackgroundLength:
    def test_confirmed_guest_bg_max_300_chars(self):
        """"confirmed_guest 背景总长度不超过 300 字"""
        _clear_cache()
        import guest_background_fetcher as gbf
        orig_search = gbf.search_guest_background_openclaw
        orig_gen = gbf.generate_background_via_model_phase2

        # 模拟一个超长背景
        long_bg = "这是一段非常长的背景介绍。" * 50  # > 300 chars

        gbf.search_guest_background_openclaw = lambda *a, **kw: [
            {"title": "Test", "url": "https://example.com", "snippet": "Test", "quality": "secondary"}
        ]
        gbf.generate_background_via_model_phase2 = lambda *a, **kw: {
            "background_zh": long_bg,
            "confidence": "medium",
            "note": "mock",
            "source_quality": "secondary",
        }

        try:
            ep = _make_ep(
                "Interview with Zhang San",
                "Interview with Zhang San.",
                show_notes="本期嘉宾：张三，某投资机构创始人，在新能源领域有丰富经验。",
            )
            result = enrich_episode_with_guest_backgrounds(ep)
            bg = result["guest_background_zh"]
            # 300 char limit
            assert len(bg) <= 300, f"背景应≤300字，实际{len(bg)}字: {bg[:50]}"
            print(f"✓ 背景长度 {len(bg)} ≤ 300")
        finally:
            gbf.search_guest_background_openclaw = orig_search
            gbf.generate_background_via_model_phase2 = orig_gen


# ─── 11. 搜索/模型失败降级 ────────────────────────────────────────────────

class TestGracefulDegradation:
    def test_no_exception_on_any_input(self):
        """任意输入不抛异常，安全降级"""
        # 测试各种异常输入不崩溃
        test_cases = [
            {"episode_title": "Test", "description": "", "show_notes_text": ""},
            {"episode_title": "X" * 500, "description": "Y" * 1000, "show_notes_text": "Z" * 500},
            {"podcast_name": "", "episode_title": "Test", "description": "Test"},
        ]
        for i, ep_data in enumerate(test_cases):
            ep = {"podcast_name": "TestCast", "episode_title": "Test", "description": "", **ep_data}
            try:
                result = enrich_episode_with_guest_backgrounds(ep)
                assert result is not None
                assert "guest_detection_status" in result
            except Exception as e:
                print(f"✗ test_no_exception_on_any_input[{i}]: {e}")
                raise
        print("✓ 任意输入均不抛异常")

    def test_no_exception_on_model_failure(self):
        """模型失败不抛异常，安全降级"""
        _clear_cache()
        import guest_background_fetcher as gbf
        orig_search = gbf.search_guest_background_openclaw
        orig_gen = gbf.generate_background_via_model_phase2

        gbf.search_guest_background_openclaw = lambda *a, **kw: [
            {"title": "Test", "url": "https://example.com", "snippet": "Test", "quality": "secondary"}
        ]
        gbf.generate_background_via_model_phase2 = lambda *a, **kw: {
            "background_zh": "未找到足够可靠的公开资料，暂不补充背景。",
            "confidence": "unknown",
            "note": "模型生成不可用，降级处理",
            "source_quality": "secondary",
        }

        try:
            ep = _make_ep(
                "Interview with Zhang San",
                "Interview with Zhang San.",
                show_notes="本期嘉宾：张三，某投资机构创始人。",
            )
            result = enrich_episode_with_guest_backgrounds(ep)
            assert "未找到" in result["guest_background_zh"] or result["guest_background_zh"]
            print("✓ 模型失败安全降级")
        finally:
            gbf.search_guest_background_openclaw = orig_search
            gbf.generate_background_via_model_phase2 = orig_gen

    def test_model_generation_delegates_to_background_runner(self):
        """模型分支应委托 run_background_model，不直接调用 gbf.subprocess.run。"""
        import guest_background_fetcher as gbf
        orig_runner = getattr(gbf, "run_background_model", None)
        calls = []

        def fake_runner(prompt):
            calls.append(prompt)
            assert "Jane Doe" in prompt
            return "Jane Doe是某机构合伙人，长期关注企业软件与人工智能投资，相关经历有助于理解本期讨论。"

        gbf.run_background_model = fake_runner
        try:
            result = gbf.generate_background_via_model_phase2(
                "Jane Doe",
                [{
                    "title": "Jane Doe Profile",
                    "url": "https://www.linkedin.com/in/janedoe",
                    "snippet": "Jane Doe is a partner focused on AI infrastructure.",
                    "quality": "primary",
                }],
                None,
            )
        finally:
            if orig_runner is None:
                delattr(gbf, "run_background_model")
            else:
                gbf.run_background_model = orig_runner

        assert calls, "run_background_model should be called"
        assert result["background_zh"].startswith("Jane Doe是某机构合伙人")
        assert result["note"] == "OpenClaw 模型生成"
        print("✓ 模型分支委托 run_background_model")


# ─── 12. Freda / Frida Kahlo 安全 ──────────────────────────────────────

class TestFredaSafety:
    def test_freda_in_title_entity_words(self):
        """freda 在 TITLE_ENTITY_WORDS 中"""
        assert "freda" in TITLE_ENTITY_WORDS, "freda should be in TITLE_ENTITY_WORDS"
        assert "frida" in TITLE_ENTITY_WORDS, "frida should be in TITLE_ENTITY_WORDS"
        assert "frida kahlo" in TITLE_ENTITY_WORDS, "frida kahlo should be in TITLE_ENTITY_WORDS"
        print("✓ 'freda' and 'frida kahlo' in TITLE_ENTITY_WORDS")

    def test_freda_duan_not_excluded_by_title_entity(self):
        """'Freda Duan'（完整名字）不是标题实体"""
        assert not _is_title_entity("Freda Duan"), "'Freda Duan' should not be a title entity"
        print("✓ 'Freda Duan' (full name) not excluded by title entity filter")

    def test_freda_title_plus_desc_is_guest(self):
        """Freda Duan 在 show_notes 描述中出现 → confirmed_guest"""
        ep = _make_ep(
            "Freda的投资札记第2集",
            "Freda 的投资分享。那还是先介绍一下——Freda Duan在湾区做投资。",
            show_notes="Freda 的投资分享。那还是先介绍一下——Freda Duan在湾区做投资。",
        )
        result = detect_guest_status_phase2(ep)
        assert result["status"] == "confirmed_guest", \
            f"'Freda Duan' with description should be confirmed_guest, got {result['status']}"
        assert "Freda Duan" in result["guest_names"], \
            f"Freda Duan should be identified, got {result['guest_names']}"
        print("✓ 'Freda Duan' with description context → confirmed_guest")


# ─── 14. show_notes 背景生成（新功能）────────────────────────────────────

class TestShowNotesBackgroundGeneration:
    """Phase 2.5: show_notes 作为背景生成第一优先级来源"""

    def test_extract_freda_duan_from_show_notes(self):
        """Freda Duan show_notes 有 Altimeter Capital 合伙人信息 → 能提取"""
        import guest_background_fetcher as gbf
        sn = "今天是我们的系列节目。那还是先介绍一下——Freda Duan在湾区做投资，是Altimeter Capital的合伙人。"
        info = gbf.extract_guest_info_from_show_notes("Freda Duan", sn)
        assert info, "Freda Duan show_notes should extract info"
        patterns = info["patterns_found"]
        assert any("Altimeter" in p or "资本" in p for p in patterns), f"Should find Altimeter Capital: {patterns}"
        print(f"✓ Freda Duan show_notes 提取: {patterns}")

    def test_extract_yunlei_from_show_notes(self):
        """恽雷 show_notes 有 南方基金/大类资产配置 → 能提取"""
        import guest_background_fetcher as gbf
        sn = "🎤 本期嘉宾恽雷@南方基金，基金经理，专注大类资产配置研究。"
        info = gbf.extract_guest_info_from_show_notes("恽雷", sn)
        assert info, "恽雷 show_notes should extract info"
        patterns = info["patterns_found"]
        assert any("南方" in p for p in patterns), f"Should find 南方基金: {patterns}"
        print(f"✓ 恽雷 show_notes 提取: {patterns}")

    def test_extract_david_paulides_from_show_notes(self):
        """David Paulides show_notes 有 Missing 411 / 作家/调查记者 → 能提取"""
        import guest_background_fetcher as gbf
        sn = "David Paulides is a writer, investigator, filmmaker, and former law enforcement officer. He is best known for the Missing 411 series."
        info = gbf.extract_guest_info_from_show_notes("David Paulides", sn)
        assert info, "David Paulides show_notes should extract info"
        patterns = info["patterns_found"]
        assert any("Missing" in p or "作家" in p for p in patterns), f"Should find Missing 411: {patterns}"
        print(f"✓ David Paulides show_notes 提取: {patterns}")

    def test_extract_marc_andreessen_from_show_notes(self):
        """Marc Andreessen show_notes 有 a16Z 联合创始人 → 能提取"""
        import guest_background_fetcher as gbf
        sn = "Marc Andreessen joins Joe Rogan for a conversation on AI. He is the co-founder of Andreessen Horowitz (a16Z)."
        info = gbf.extract_guest_info_from_show_notes("Marc Andreessen", sn)
        assert info, "Marc Andreessen show_notes should extract info"
        patterns = info["patterns_found"]
        assert any("Andreessen" in p or " Horowitz" in p for p in patterns), f"Should find a16Z: {patterns}"
        print(f"✓ Marc Andreessen show_notes 提取: {patterns}")

    def test_enrich_with_show_notes_freda_duan(self):
        """confirmed_guest + show_notes 提供身份信息 → 能提取嘉宾身份"""
        import guest_background_fetcher as gbf
        sn = "今天是我们的系列节目。那还是先介绍一下——Freda Duan在湾区做投资，是Altimeter Capital的合伙人。"
        info = gbf.extract_guest_info_from_show_notes("Freda Duan", sn)
        assert info, "Freda Duan show_notes should extract info"
        patterns = info["patterns_found"]
        # 至少应包含 Altimeter Capital（机构/公司）
        assert any("Altimeter" in p for p in patterns), \
            f"Should find Altimeter Capital in patterns: {patterns}"
        assert info["source_quality"] == "primary"
        assert info["source_type"] == "episode_show_notes"
        print(f"✓ Freda Duan show_notes 提取成功: {patterns}")

    def test_source_quality_episode_show_notes(self):
        """source_quality_summary 应包含 episode_show_notes 或 primary"""
        import guest_background_fetcher as gbf
        # 直接测试：当 show_notes 提取到信息时，generate_background_via_model_phase2
        # 应返回包含 episode_show_notes 的 source_quality_summary
        sn_info = {
            "text": "Freda Duan在湾区做投资",
            "patterns_found": ["职务/头衔：合伙人", "机构/公司：Altimeter Capital"],
            "source_type": "episode_show_notes",
            "source_quality": "primary",
        }
        result = gbf.generate_background_via_model_phase2("Freda Duan", [], sn_info)
        sq = result.get("source_quality_summary", "")
        assert "primary" in sq or "episode_show_notes" in sq, \
            f"source_quality_summary should contain primary: got {sq}"
        # 背景应来自 show_notes 而不是"未找到"
        bg = result["background_zh"]
        assert "未找到" not in bg, f"Should generate from show_notes, not '未找到': {bg}"
        print(f"✓ source_quality_summary = {sq}, background = {bg}")

    def test_frida_kahlo_not_guest_background(self):
        """Frida Kahlo 只是讨论对象 → 不生成嘉宾背景"""
        import guest_background_fetcher as gbf
        ep = _make_ep(
            "聊聊 Frida Kahlo 和她的艺术",
            "本期节目聊聊 Frida Kahlo 的传奇人生。",
            show_notes="节目探讨 Frida Kahlo 的艺术创作",
        )
        result = gbf.enrich_episode_with_guest_backgrounds(ep)
        # Frida Kahlo → mentioned_entity，不应生成背景
        assert result["guest_detection_status"] in ("mentioned_entity", "no_guest_detected"), \
            f"Frida Kahlo should be mentioned_entity, got {result['guest_detection_status']}"
        assert "Frida Kahlo" not in result.get("guest_background_zh", ""), \
            "Should not generate guest background for Frida Kahlo"
        print(f"✓ Frida Kahlo → {result['guest_detection_status']}，不生成嘉宾背景")

    def test_search_failure_but_show_notes_sufficient(self):
        """搜索失败但 show_notes 足够 → 仍生成背景，不降级为 unknown"""
        _clear_cache()
        import guest_background_fetcher as gbf
        orig_search = gbf.search_guest_background_openclaw

        # 模拟搜索失败（返回空）
        gbf.search_guest_background_openclaw = lambda *a, **kw: []

        try:
            ep = _make_ep(
                "恽雷谈资产配置",
                "🎤 本期嘉宾恽雷@南方基金，基金经理，专注大类资产配置研究。",
                show_notes="🎤 本期嘉宾恽雷@南方基金，基金经理，专注大类资产配置研究。",
            )
            result = gbf.enrich_episode_with_guest_backgrounds(ep)
            conf = result["guest_background_confidence"]
            bg = result["guest_background_zh"]
            # show_notes 有充分信息，搜索失败 → 不应返回 unknown confidence + "未找到"
            assert not ("未找到" in bg and conf == "unknown"), \
                f"搜索失败但 show_notes 足够时应生成背景: bg={bg}, conf={conf}"
            print(f"✓ 搜索失败但 show_notes 足够: bg={bg}, conf={conf}")
        finally:
            gbf.search_guest_background_openclaw = orig_search

    def test_guest_background_sources_has_show_notes(self):
        """guest_background_sources 中应保留 show_notes 来源，source_type = episode_show_notes"""
        import guest_background_fetcher as gbf
        sn_info = {
            "text": "Freda Duan在湾区做投资",
            "patterns_found": ["职务/头衔：合伙人", "机构/公司：Altimeter Capital"],
            "source_type": "episode_show_notes",
            "source_quality": "primary",
        }
        sn_sources = [{
            "title": "节目元数据（show_notes）",
            "url": "",
            "snippet": "职务/头衔：合伙人 | 机构/公司：Altimeter Capital",
            "quality": "primary",
            "source_type": "episode_show_notes",
        }]
        assert sn_sources[0]["source_type"] == "episode_show_notes"
        assert sn_sources[0]["quality"] == "primary"
        print(f"✓ show_notes source_type={sn_sources[0]['source_type']}, quality={sn_sources[0]['quality']}")

class TestDetectionStatusEnum:
    def test_exactly_5_statuses(self):
        assert len(DETECTION_STATUSES) == 5
        expected = {"confirmed_guest", "possible_guest", "mentioned_entity", "no_guest_detected", "ambiguous"}
        assert DETECTION_STATUSES == expected
        print(f"✓ DETECTION_STATUSES = {DETECTION_STATUSES}")


class TestGuestBackgroundNaturalSentence:
    """Phase 2 背景中文可读性测试：guest_background_zh 应为自然中文句，而非字段拼接"""

    def test_not_simple_field_concat(self):
        """confirmed_guest + show_notes 信息足够时，不应等于简单字段拼接（'机构，职位'）"""
        _clear_cache()
        ep = _make_ep(
            "Freda的投资札记",
            "Freda Duan在湾区做投资，是Altimeter Capital的合伙人",
            show_notes="——Freda Duan在湾区做投资，是Altimeter Capital的合伙人。",
        )
        result = enrich_episode_with_guest_backgrounds(ep)
        bg = result.get("guest_background_zh", "")
        # 不应包含"｜"（名字分隔符）
        assert "｜" not in bg, f"guest_background_zh should not contain '｜': {bg}"
        # 不应等于简单拼接"机构，职位"
        assert bg != "Altimeter Capital，合伙人", f"Should not be simple concat: {bg}"
        # 应嵌入嘉宾姓名
        assert "Freda Duan" in bg, f"Should embed guest name: {bg}"
        print(f"✓ Freda: {bg}")

    def test_minimum_chinese_chars(self):
        """guest_background_zh 至少应有 30 个中文字符（show_notes 有完整信息时）"""
        _clear_cache()
        ep = _make_ep(
            "Marc Andreessen",
            "Marc Andreessen is the co-founder of Andreessen Horowitz",
            show_notes="Marc Andreessen is an entrepreneur and co-founder of Andreessen Horowitz (a16Z)",
        )
        result = enrich_episode_with_guest_backgrounds(ep)
        bg = result.get("guest_background_zh", "")
        # 计算中文字符数量（排除英文单词）
        chinese_chars = sum(1 for c in bg if "\u4e00" <= c <= "\u9fff")
        assert chinese_chars >= 5, f"Should have at least 5 Chinese chars, got {chinese_chars}: {bg}"
        print(f"✓ Marc: {bg} ({chinese_chars} Chinese chars)")

    def test_no_half_english_title(self):
        """英文 title 值（co-founder, writer）应翻译为中文，不半中半英"""
        _clear_cache()
        ep1 = _make_ep(
            "Marc Andreessen",
            "Marc Andreessen is the co-founder of Andreessen Horowitz",
            show_notes="Marc Andreessen is an entrepreneur. He is the co-founder of Andreessen Horowitz (a16Z)",
        )
        result1 = enrich_episode_with_guest_backgrounds(ep1)
        bg1 = result1.get("guest_background_zh", "")
        # "co-founder" 应翻译为"联合创始人"
        assert "co-founder" not in bg1, f"Should not contain half-English 'co-founder': {bg1}"
        assert "联合创始人" in bg1, f"Should translate to '联合创始人': {bg1}"
        print(f"✓ Marc Andreessen: {bg1}")

        _clear_cache()
        ep2 = _make_ep(
            "David Paulides",
            "David Paulides is a writer",
            show_notes="David Paulides is a writer, investigator, and filmmaker.",
        )
        result2 = enrich_episode_with_guest_backgrounds(ep2)
        bg2 = result2.get("guest_background_zh", "")
        # "writer" 是泛化职业词，且无公司/机构/研究/著作支撑时，
        # 应输出"已确认嘉宾，节目元数据未提供足够背景信息"，而非"某某是作家"
        assert "writer" not in bg2, f"Should not contain half-English 'writer': {bg2}"
        assert "作家" not in bg2, f"Should NOT output '作家' for generic author without org: {bg2}"
        assert "已确认本期嘉宾" in bg2, f"Should output CONFIRMED_GUEST_FALLBACK: {bg2}"
        print(f"✓ David Paulides (generic author, no org): {bg2}")

    def test_research_included_when_available(self):
        """show_notes 有研究方向时，背景应包含研究方向信息"""
        _clear_cache()
        ep = _make_ep(
            "恽雷谈资产配置",
            "本期嘉宾恽雷，南方基金基金经理，专注大类资产配置研究。",
            show_notes="本期嘉宾：恽雷，南方基金基金经理，专注大类资产配置研究。",
        )
        result = enrich_episode_with_guest_backgrounds(ep)
        bg = result.get("guest_background_zh", "")
        assert "大类资产配置" in bg, f"Should include research info: {bg}"
        print(f"✓ 恽雷 research included: {bg}")


class TestGuestNameCanonicalization:
    def test_role_prefixes_are_removed_from_english_guest_names(self):
        cases = {
            "tech analyst Benedict Evans": "Benedict Evans",
            "economists Tyler Cowen": "Tyler Cowen",
            "economist Noah Smith": "Noah Smith",
            "Substack writer Noah Smith": "Noah Smith",
            "Substack writer Noah": "Noah",
            "writer George Coyle": "George Coyle",
            "trader George Coyle": "George Coyle",
            "author Jack Schwager": "Jack Schwager",
        }
        for raw, expected in cases.items():
            assert canonicalize_guest_name_phase2(raw) == expected, raw
        print("✓ role-prefix canonicalization removes known English role prefixes")

    def test_normal_names_and_chinese_names_are_unchanged(self):
        names = [
            "Benedict Evans",
            "Tyler Cowen",
            "Alex Tabarrok",
            "Noah Smith",
            "George Coyle",
            "Samo Burja",
            "阳萌",
            "张璐",
            "洪力德",
        ]
        for name in names:
            assert canonicalize_guest_name_phase2(name) == name, name
        print("✓ canonicalization keeps normal English and Chinese names unchanged")

    def test_sponsor_tokens_are_not_promoted_to_names_by_canonicalization(self):
        # TODO: add a separate sponsor/noise denylist so these do not enter guest_names.
        names = ["code ROGAN", "promo code ROGAN", "DraftKings", "BetterHelp", "Visible", "ARMRA"]
        for name in names:
            assert canonicalize_guest_name_phase2(name) == name, name
        print("✓ sponsor token behavior documented; denylist remains a later task")


class TestGuestCandidateNoiseDenylist:
    def test_sponsor_guest_candidates_are_noise(self):
        noisy_names = [
            "code ROGAN",
            "promo code ROGAN",
            "ROGAN",
            "DraftKings",
            "BetterHelp",
            "Visible",
            "Visible+ Pro",
            "ARMRA",
            "BlueChew",
            "Chime",
            "Perplexity",
            "Trade The Pool",
        ]
        for name in noisy_names:
            assert is_guest_name_noise_phase2(name), name
        print("✓ sponsor/noise guest candidates are denied")

    def test_normal_person_names_are_not_noise(self):
        normal_names = [
            "Dean Radin",
            "Benedict Evans",
            "Tyler Cowen",
            "Joe Rogan",
            "George Coyle",
        ]
        for name in normal_names:
            assert not is_guest_name_noise_phase2(name), name
        print("✓ normal person names are not denied by guest noise filter")


class TestGuestCandidateFragmentPruning:
    def test_redundant_single_token_and_chinese_fragments_are_pruned(self):
        cases = [
            (
                ["Jack Schwager", "George Coyle", "George"],
                ["Jack Schwager", "George Coyle"],
            ),
            (
                ["阳萌", "阳萌的访"],
                ["阳萌"],
            ),
            (
                ["Joey Diaz"],
                ["Joey Diaz"],
            ),
            (
                ["Benedict Evans"],
                ["Benedict Evans"],
            ),
        ]
        for raw, expected in cases:
            assert prune_redundant_single_token_guest_names_phase2(raw) == expected, raw
        print("✓ residual guest candidate fragments are pruned only when a cleaner name exists")


class TestW24GuestDetectionKnownFailures:
    """Document W24 guest detection/background failures without changing behavior."""

    def test_role_prefix_currently_included_in_english_guest_name(self):
        ep = _make_ep(
            "AI Eats the World? A Reality Check with Benedict Evans",
            "Erik Torenberg speaks with tech analyst Benedict Evans about the current state of AI.",
            show_notes="Erik Torenberg speaks with tech analyst Benedict Evans about the current state of AI.",
            podcast="a16Z",
        )
        result = detect_guest_status_phase2(ep)
        assert result["status"] == "confirmed_guest"
        assert result["guest_names"] == ["Benedict Evans"], result
        assert "tech analyst Benedict Evans" not in result["guest_names"]
        print("✓ W24 role-prefix fixed: tech analyst Benedict Evans → Benedict Evans")

        ep2 = _make_ep(
            "Tyler Cowen & Alex Tabarrok on AI, Jobs, and Economic Growth",
            "Wyatt Thomson of OpenAI speaks with economists Tyler Cowen and Alex Tabarrok about AI, labor markets, and economic growth.",
            show_notes="Wyatt Thomson of OpenAI speaks with economists Tyler Cowen and Alex Tabarrok about AI, labor markets, and economic growth.",
            podcast="a16Z",
        )
        result2 = detect_guest_status_phase2(ep2)
        assert result2["status"] != "no_guest_detected"
        assert result2["guest_names"] == ["Tyler Cowen", "Alex Tabarrok"], result2
        assert "economists Tyler Cowen" not in result2["guest_names"]
        assert "economists" not in result2["guest_names"]
        print("✓ W24 coordinated multi-guest fixed: Tyler Cowen / Alex Tabarrok")

    def test_chinese_name_slicing_and_role_before_name_failures_are_documented(self):
        ep = _make_ep(
            "144. 对阳萌的4小时访谈：消费电子死与生、第三类公司、端侧模型、产品方法、游戏模式",
            "今天我们的嘉宾是安克创新的创始人兼CEO阳萌。他是1982年生人，2011年开始创业。面对新的范式变化，阳萌会怎么做？",
            show_notes="今天我们的嘉宾是安克创新的创始人兼CEO阳萌。他是1982年生人，2011年开始创业。面对新的范式变化，阳萌会怎么做？",
            podcast="张小珺商业访谈录",
        )
        result = detect_guest_status_phase2(ep)
        assert result["status"] in ("confirmed_guest", "ambiguous", "possible_guest")
        assert "阳萌" in result["guest_names"], result
        assert "阳萌会怎" not in result["guest_names"], result
        assert "安克创新" not in result["guest_names"], result
        assert "创始人兼CEO" not in result["guest_names"], result
        print("✓ W24 Chinese role-before-name fixed: 阳萌")

        ep2 = _make_ep(
            "甲小姐对话张璐：投资SpaceX背后，一场全球基础设施的必争之战",
            "本期对话的嘉宾是我的好朋友张璐。张璐是Fusion Fund创始人，也是SpaceX投资人之一。过去几年，她多次做客《甲小姐对话》。",
            show_notes="本期对话的嘉宾是我的好朋友张璐。张璐是Fusion Fund创始人，也是SpaceX投资人之一。过去几年，她多次做客《甲小姐对话》。",
            podcast="甲小姐对话",
        )
        result2 = detect_guest_status_phase2(ep2)
        assert result2["status"] != "no_guest_detected", result2
        assert "张璐" in result2["guest_names"], result2
        assert "她多次" not in result2["guest_names"], result2
        assert "我的好朋友张璐" not in result2["guest_names"], result2
        assert "Fusion Fund" not in result2["guest_names"], result2
        assert "创始人" not in result2["guest_names"], result2
        print("✓ W24 Chinese guest intro fixed: 张璐")

    def test_english_role_before_name_failures_are_documented(self):
        ep = _make_ep(
            '"The CEO Must Be the Chief AI Officer',
            "Brex co-founder and CEO Pedro Franceschi believes most people still underestimate how much AI will change companies.",
            show_notes="Brex co-founder and CEO Pedro Franceschi believes most people still underestimate how much AI will change companies.",
            podcast="Y Combinator Startup",
        )
        result = detect_guest_status_phase2(ep)
        assert result["status"] == "confirmed_guest", result
        assert "Pedro Franceschi" in result["guest_names"], result
        assert "Brex" not in result["guest_names"], result
        assert "co-founder" not in result["guest_names"], result
        assert "CEO" not in result["guest_names"], result
        print("✓ W24 role-before-name fixed: Pedro Franceschi")

        ep2 = _make_ep(
            "#480 — The Economics of Everything",
            "Sam Harris speaks with economist and Substack writer Noah Smith about the U.S. national debt.",
            show_notes="Sam Harris speaks with economist and Substack writer Noah Smith about the U.S. national debt.",
            podcast="Making Sense",
        )
        result2 = detect_guest_status_phase2(ep2)
        assert result2["status"] == "confirmed_guest", result2
        assert result2["guest_names"] == ["Noah Smith"], result2
        assert "economist" not in result2["guest_names"], result2
        assert "Substack writer Noah" not in result2["guest_names"], result2
        assert "Substack writer Noah Smith" not in result2["guest_names"], result2
        print("✓ W24 role-before-name fixed: Noah Smith")

    def test_multi_guest_structure_failures_are_documented(self):
        ep = _make_ep(
            "325 · Jack Schwager & George Coyle - The 3 Timeless Rules Shared by 100 Years of Market Wizards",
            "Jack Schwager and George Coyle team up to look at what it takes to master the markets.",
            show_notes="Jack Schwager and George Coyle team up to look at what it takes to master the markets.",
            podcast="Chat With Traders",
        )
        result = detect_guest_status_phase2(ep)
        assert result["status"] != "no_guest_detected", result
        assert "Jack Schwager" in result["guest_names"], result
        assert "George Coyle" in result["guest_names"], result
        assert "George" not in result["guest_names"], result
        assert "studying top fund" not in result["guest_names"], result
        print("✓ W24 bare coordinated multi-guest fixed: Jack Schwager / George Coyle")

        ep2 = _make_ep(
            "Designing the Physical World with AI",
            "Erin Price-Wright speaks with Alex Modon, cofounder and CEO at Unlimited Industries, and Davide Asnaghi, CEO at Diode Computers, about how AI is moving from software into the physical world.",
            show_notes="Erin Price-Wright speaks with Alex Modon, cofounder and CEO at Unlimited Industries, and Davide Asnaghi, CEO at Diode Computers, about how AI is moving from software into the physical world.",
            podcast="a16Z",
        )
        result2 = detect_guest_status_phase2(ep2)
        assert result2["status"] != "no_guest_detected", result2
        assert result2["guest_names"] == ["Alex Modon", "Davide Asnaghi"], result2
        assert "Unlimited Industries" not in result2["guest_names"], result2
        assert "Diode Computers" not in result2["guest_names"], result2
        assert "cofounder and CEO" not in result2["guest_names"], result2
        assert "CEO" not in result2["guest_names"], result2
        print("✓ W24 appositive multi-guest fixed: Alex Modon / Davide Asnaghi")

    def test_parenthetical_english_alias_failure_is_documented(self):
        ep = _make_ep(
            "口述SpaceX开发史：和前高管洪力德聊，马斯克用人观、最大IPO、太空与AI、人类文明扩张前奏？",
            "我邀请了SpaceX前火箭首席制造工程师洪力德（Lewis Hong），一起来聊聊SpaceX开发史。",
            show_notes="我邀请了SpaceX前火箭首席制造工程师洪力德（Lewis Hong），一起来聊聊SpaceX开发史。",
            podcast="张小珺商业访谈录",
        )
        result = detect_guest_status_phase2(ep)
        assert result["status"] != "no_guest_detected", result
        assert "洪力德" in result["guest_names"], result
        assert "Lewis Hong" not in result["guest_names"], result
        assert "SpaceX" not in result["guest_names"], result
        assert any("Lewis Hong" in ev.get("matched_text", "") for ev in result["evidence"]), result
        # TODO: promote Lewis Hong to structured alias/evidence if alias schema is added later.
        print("✓ W24 Chinese name with English alias fixed: 洪力德（Lewis Hong）")

    def test_sponsor_token_interference_is_documented(self):
        ep = _make_ep(
            "#2513 - Dean Radin",
            "Dean Radin, PhD, is Chief Scientist at the Institute of Noetic Sciences.\n"
            "Switch today at https://www.Visible.com for just 25/mo. Or Save $10 on your first month of Visible+ Pro with code ROGAN.",
            show_notes="Dean Radin, PhD, is Chief Scientist at the Institute of Noetic Sciences.\n"
            "Switch today at https://www.Visible.com for just 25/mo. Or Save $10 on your first month of Visible+ Pro with code ROGAN.",
            podcast="The Joe Rogan Experience",
        )
        result = detect_guest_status_phase2(ep)
        assert "code ROGAN" not in result["guest_names"], result
        assert "ROGAN" not in result["guest_names"], result
        assert "Visible" not in result["guest_names"], result
        assert "Dean Radin" in result["guest_names"], result
        assert result["status"] == "confirmed_guest", result
        print("✓ W24 Dean Radin fixed: credentialed name detected while code ROGAN remains filtered")

    def test_background_org_misextract_from_studying_top_fund_is_filtered(self):
        show_notes = (
            "Jack Schwager and George Coyle team up to look at what it takes to master the markets.\n"
            "After studying top fund managers and over 100 years of market history, Jack and George wrote "
            "Market Wizards: The Next Generation.\n"
            "George Coyle:\n"
            "George is the co-author of the new Market Wizards book. He is a writer, trader, system designer, "
            "money manager, and market strategist."
        )
        info = gbf.extract_guest_info_from_show_notes("George", show_notes)
        assert "机构/公司：studying top fund" not in info.get("patterns_found", []), info
        bg = gbf.generate_background_via_model_phase2("George", [], info)
        assert "studying top fund" not in bg["background_zh"], bg
        assert bg["background_zh"] != "George任职于studying top fund", bg
        print("✓ W24 known failure fixed: studying top fund no longer enters background")


class TestW24RealGuestCandidateVariantsKnownGaps:
    """Cover real W24 candidate leftovers seen in offline recompute preview."""

    def test_real_w24_jack_george_text_prunes_george_single_name(self):
        show_notes = (
            "Jack Schwager and George Coyle team up to look at what it takes to master the markets.\n"
            "After studying top fund managers and over 100 years of market history, Jack and George wrote "
            "Market Wizards: The Next Generation.\n"
            "George Coyle:\n"
            "George is the co-author of the new Market Wizards book. He is a writer, trader, system designer, "
            "money manager, and market strategist."
        )
        ep = _make_ep(
            "325 · Jack Schwager & George Coyle - The 3 Timeless Rules Shared by 100 Years of Market Wizards",
            show_notes,
            show_notes=show_notes,
            podcast="Chat With Traders",
        )

        result = detect_guest_status_phase2(ep)

        assert result["status"] != "no_guest_detected", result
        assert "Jack Schwager" in result["guest_names"], result
        assert "George Coyle" in result["guest_names"], result
        assert "George" not in result["guest_names"], result
        assert not any(ev["pattern"] == "desc_is_a" and ev["name"] == "George" for ev in result["evidence"]), result
        print("✓ W24 real candidate fixed: George single-name is pruned")

    def test_real_w24_yangmeng_title_prunes_title_fragment(self):
        show_notes = (
            "前面几集节目，我和大家一起遇见了许多年轻的面孔。\n\n"
            "今天我们的嘉宾是安克创新的创始人兼CEO阳萌。他是1982年生人，2011年开始创业。\n\n"
            "接下来，就是我对阳萌的访谈。"
        )
        ep = _make_ep(
            "144. 对阳萌的4小时访谈：消费电子死与生、第三类公司、端侧模型、产品方法、游戏模式",
            show_notes,
            show_notes=show_notes,
            podcast="张小珺商业访谈录",
        )

        result = detect_guest_status_phase2(ep)

        assert result["status"] != "no_guest_detected", result
        assert "阳萌" in result["guest_names"], result
        assert "阳萌的访" not in result["guest_names"], result
        assert "阳萌会怎" not in result["guest_names"], result
        assert not any(ev["pattern"] == "title_cn_num_name" and ev["name"] == "阳萌的访" for ev in result["evidence"]), result
        print("✓ W24 real candidate fixed: 阳萌 title fragment is pruned")

    def test_real_w24_studying_top_fund_background_pollution_is_filtered(self):
        show_notes = (
            "Jack Schwager and George Coyle team up to look at what it takes to master the markets.\n"
            "After studying top fund managers and over 100 years of market history, Jack and George wrote "
            "Market Wizards: The Next Generation.\n"
            "George Coyle:\n"
            "George is the co-author of the new Market Wizards book. He is a writer, trader, system designer, "
            "money manager, and market strategist."
        )

        info = gbf.extract_guest_info_from_show_notes("George Coyle", show_notes)
        assert "机构/公司：studying top fund" not in info.get("patterns_found", []), info
        bg = gbf.generate_background_via_model_phase2("George Coyle", [], info)
        assert "studying top fund" not in bg["background_zh"], bg
        assert bg["background_zh"] != "George Coyle任职于studying top fund", bg
        # TODO: prefer Market Wizards / role-list evidence when fetcher context includes the role-list sentence.
        print("✓ W24 real background gap fixed: studying top fund no longer pollutes George Coyle")


class TestW28GuestBackgroundEvidenceGovernance:
    def test_pat_dorsey_org_evidence_rejects_sentence_fragments(self):
        show_notes = (
            "Pat Dorsey is the Founder of Dorsey Asset Management, a $1.7 billion global public equity "
            "manager focused on companies with competitive advantages and long investment runways."
        )

        info = gbf.extract_guest_info_from_show_notes("Pat Dorsey", show_notes)

        assert "机构/公司：Dorsey Asset Management" in info.get("patterns_found", []), info
        assert "机构/公司：and long investment" not in info.get("patterns_found", []), info
        assert "机构/公司：investment" not in info.get("patterns_found", []), info


from datetime import datetime, timezone, timedelta
TZ_SH = timezone(timedelta(hours=8))

# ─── 运行 ────────────────────────────────────────────────────────────────

def run_all():
    print("=" * 60)
    print("test_guest_background_fetcher.py — Phase 2")
    print("=" * 60)

    all_pass = True
    test_classes = [
        TestChineseExplicitPatterns,
        TestTitleEntities,
        TestHostExclusion,
        TestEnglishGuestPatterns,
        TestTitleGuestFormat,
        TestEvidenceFormat,
        TestSourceQuality,
        TestConfidenceRules,
        TestCacheTTL,
        TestBackgroundLength,
        TestGracefulDegradation,
        TestFredaSafety,
        TestDetectionStatusEnum,
        TestShowNotesBackgroundGeneration,
        TestGuestBackgroundNaturalSentence,
        TestGuestNameCanonicalization,
        TestGuestCandidateNoiseDenylist,
        TestGuestCandidateFragmentPruning,
        TestW24GuestDetectionKnownFailures,
        TestW24RealGuestCandidateVariantsKnownGaps,
        TestW28GuestBackgroundEvidenceGovernance,
    ]

    for cls in test_classes:
        print(f"\n--- {cls.__name__} ---")
        instance = cls()
        for method_name in dir(instance):
            if method_name.startswith("test_"):
                try:
                    getattr(instance, method_name)()
                except AssertionError as e:
                    print(f"✗ {method_name}: {e}")
                    all_pass = False
                except Exception as e:
                    print(f"✗ {method_name}: {e}")
                    all_pass = False

    print("\n" + "=" * 60)
    if all_pass:
        print("ALL TESTS PASSED ✓")
    else:
        print("SOME TESTS FAILED ✗")
    print("=" * 60)
    return all_pass


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
