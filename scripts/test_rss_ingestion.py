#!/usr/bin/env python3
"""
test_rss_ingestion.py
RSS Metadata Ingestion Fix — 验证 show_notes 字段读取逻辑
"""
import sys
import os
import types
import re

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_DIR = os.path.dirname(_SCRIPT_DIR)
os.chdir(_PIPELINE_DIR)
sys.path.insert(0, _SCRIPT_DIR)

# 防止 podcast_screener.py 的 if __name__ == '__main__': main() 自动运行
_src = open(os.path.join(_SCRIPT_DIR, "podcast_screener.py")).read()
_src = re.sub(r"\nif __name__ == '__main__':.*", "", _src, flags=re.DOTALL)
_pod_mod = types.ModuleType("podcast_screener")
sys.modules["podcast_screener"] = _pod_mod
exec(_src, _pod_mod.__dict__)

from podcast_screener import (
    clean_show_notes, select_best_show_notes, parse_rss_episodes,
    SHOW_NOTES_MAX_CHARS
)


def make_entry(desc="", enc="", summ="", sub=""):
    return {
        "rss_description_raw": desc,
        "rss_content_encoded_raw": enc,
        "rss_itunes_summary_raw": summ,
        "rss_itunes_subtitle_raw": sub,
    }


# ── 1. select_best_show_notes 基础测试 ───────────────────────────────
def test_uses_content_encoded_when_longer():
    entry = make_entry(
        desc="Short description",
        enc="A much longer content encoded text " * 50,
        summ="",
        sub=""
    )
    result = select_best_show_notes(entry)
    assert result["show_notes_source"] == "content_encoded", \
        f"Expected content_encoded, got {result['show_notes_source']}"
    print("✓ content_encoded wins when longer")


def test_itunes_summary_wins_when_longest():
    entry = make_entry(
        desc="Short desc",
        enc="Medium content",
        summ="A very long itunes summary " * 80,
        sub=""
    )
    result = select_best_show_notes(entry)
    assert result["show_notes_source"] == "itunes_summary"
    print("✓ itunes_summary wins when longest")


def test_description_fallback():
    entry = make_entry(
        desc="This is a decent description",
        enc="",
        summ="",
        sub=""
    )
    result = select_best_show_notes(entry)
    assert result["show_notes_source"] == "description"
    print("✓ description fallback works")


def test_cdata_content_encoded_parsed():
    xml = """<item>
<title>Test Episode</title>
<description><![CDATA[Short desc here]]></description>
<content:encoded><![CDATA[Longer content encoded with more details here]]></content:encoded>
</item>"""
    episodes = parse_rss_episodes(xml, "TestPodcast")
    ep = episodes[0]
    assert ep["show_notes_source"] == "content_encoded"
    assert "Longer content encoded" in ep["show_notes"]
    print("✓ CDATA content:encoded parsed correctly")


def test_html_entities_preserved():
    entry = make_entry(
        desc="Title: &#39;Test&#39; &amp; more &lt;info&gt;",
        enc="",
        summ="",
        sub=""
    )
    result = select_best_show_notes(entry)
    # clean_show_notes removes < > so the entities should decode
    assert "Test" in result["show_notes_text"]
    assert "&" in result["show_notes_text"]  # &amp; → &
    print("✓ HTML entities decoded in clean text")


def test_show_notes_not_truncated_to_500():
    """show_notes should NOT be truncated to 500 chars"""
    long_text = "A" * 2000
    entry = make_entry(desc=long_text, enc="", summ="", sub="")
    result = select_best_show_notes(entry)
    assert result["show_notes_text_len"] == 2000, \
        f"Expected 2000, got {result['show_notes_text_len']} (was truncated)"
    assert not result["show_notes_truncated"]
    print("✓ show_notes not truncated to 500")


def test_show_notes_max_chars_truncation():
    """超过 SHOW_NOTES_MAX_CHARS 时才截断"""
    very_long = "B" * (SHOW_NOTES_MAX_CHARS + 5000)
    entry = make_entry(desc=very_long, enc="", summ="", sub="")
    result = select_best_show_notes(entry)
    assert result["show_notes_truncated"] is True
    assert result["show_notes_text_len"] == SHOW_NOTES_MAX_CHARS
    print(f"✓ show_notes truncated when > {SHOW_NOTES_MAX_CHARS} chars")


def test_jre_mma_big_john_not_lost():
    """
    JRE MMA Show #179: content:encoded (1203) >> description (786)
    Big John McCarthy 必须仍在 show_notes_text 中
    """
    xml = """<item>
<title>JRE MMA Show #179 with Josh Thompson &amp; Big John McCarthy</title>
<description><![CDATA[Joe sits down with Josh Thompson, a retired champion mixed martial artist and fight analyst, and Big John McCarthy, a veteran MMA referee and commentator. They discuss fight officiating and training.]]></description>
<content:encoded><![CDATA[<p>Joe sits down with Josh Thompson, a retired champion mixed martial artist and fight analyst, and Big John McCarthy, a veteran mixed martial arts referee, Professional Fighters League commentator, and founder of Big John McCarthy's C.O.M.M.A.N.D., an internationally recognized training school for referees and judges in mixed martial arts. Josh and John host the "Weighing In" podcast.</p>]]></content:encoded>
</item>"""
    episodes = parse_rss_episodes(xml, "The Joe Rogan Experience")
    ep = episodes[0]
    # content_encoded is longer, should be selected
    assert ep["show_notes_source"] == "content_encoded"
    # Big John McCarthy 必须在 clean 后的文本中
    assert "Big John McCarthy" in ep["show_notes"] or "McCarthy" in ep["show_notes"], \
        f"Big John McCarthy NOT found in: {ep['show_notes'][:300]}"
    print("✓ JRE MMA: Big John McCarthy not lost to 500-char truncation")


def test_lex_497_don_lincoln_not_lost():
    """
    Lex #497: Real RSS description is ~4301 chars with HTML tags.
    clean_show_notes removes tags but preserves all content.
    Don Lincoln and Fermilab must still be in show_notes_text after cleaning.
    """
    # Realistic HTML description similar to actual Lex RSS (4301+ char with tags)
    html_desc = (
        "<p>Don Lincoln is a particle physicist at Fermilab who has spent decades "
        "working at the frontiers of high energy physics.</p><p>In this episode they "
        "discuss the biggest mysteries in physics including antimatter, dark energy "
        "and the theory of everything.</p>"
        "<p>Thank you for listening. Check out our sponsors.</p>"
        "<p>See below for timestamps, and to give feedback, submit questions, "
        "contact Lex, etc.</p>"
    ) * 5  # Repeat to match ~4300 char real RSS length
    xml = f"""<item>
<title>#497 – Biggest Mysteries in Physics: Antimatter, Dark Energy &amp; ToE – Don Lincoln</title>
<description><![CDATA[{html_desc}]]></description>
</item>"""
    episodes = parse_rss_episodes(xml, "Lex Fridman Podcast")
    ep = episodes[0]
    assert ep["show_notes_source"] == "description"
    assert len(ep["show_notes"]) > 1000, \
        f"Lex #497 show_notes too short: {len(ep['show_notes'])} chars"
    assert "Don Lincoln" in ep["show_notes"]
    assert "Fermilab" in ep["show_notes"]
    print(f"✓ Lex #497: Don Lincoln preserved (show_notes_text_len={len(ep['show_notes'])})")


def test_a16z_1b_exits_david_george_not_lost():
    """
    a16Z $1B Exits: content:encoded (2379) >> description (截断后)
    David George 和 David Clark 必须仍在 show_notes_text 中
    """
    xml = """<item>
<title>Why $1B Exits are Dead</title>
<description><![CDATA[Short description here]]></description>
<content:encoded><![CDATA[<p>David George, General Partner at a16z, and David Clark, CIO at VenCap, discuss how AI is reshaping venture capital and the technology industry itself. They examine why today's AI companies are scaling faster than any previous generation of startups.</p>]]></content:encoded>
</item>"""
    episodes = parse_rss_episodes(xml, "a16Z")
    ep = episodes[0]
    assert ep["show_notes_source"] == "content_encoded"
    assert "David George" in ep["show_notes"]
    assert "David Clark" in ep["show_notes"]
    print("✓ a16Z $1B Exits: David George/Clark preserved")


def test_zxj_143_he_xiaopeng_info_preserved():
    """
    张小珺 #143: show_notes_text 包含完整"小鹏汽车董事长兼CEO何小鹏"
    """
    xml = """<item>
<title>143. 对何小鹏的第二次访谈：更大赌注、人形机器人Iron诞生</title>
<description><![CDATA[<p>本集是小鹏汽车董事长兼CEO何小鹏的返场。在我们之前那次访谈（70集）中，何小鹏形容，造车就像在"在血海里游泳"。</p>]]></description>
</item>"""
    episodes = parse_rss_episodes(xml, "张小珺商业访谈录")
    ep = episodes[0]
    assert ep["show_notes_source"] == "description"
    assert "小鹏汽车董事长兼CEO何小鹏" in ep["show_notes"] or "何小鹏" in ep["show_notes"]
    print("✓ 张小珺 #143: 何小鹏身份信息 preserved")


def test_zxj_142_dai_yusen_info_preserved():
    """
    张小珺 #142: show_notes_text 包含"真格基金管理合伙人戴雨森"
    """
    xml = """<item>
<title>142. 雨森的创投观察第2集：Harness、下一个字节、2026大机会</title>
<description><![CDATA[<p>今天是我们的系列节目《雨森的创投观察》第2集。在《雨森的创投观察》第1集（我们节目124集）中，真格基金管理合伙人戴雨森预言称，2026年的关键词是"The Year of R"。</p>]]></description>
</item>"""
    episodes = parse_rss_episodes(xml, "张小珺商业访谈录")
    ep = episodes[0]
    assert ep["show_notes_source"] == "description"
    assert "真格基金管理合伙人戴雨森" in ep["show_notes"] or "戴雨森" in ep["show_notes"]
    print("✓ 张小珺 #142: 戴雨森身份信息 preserved")


def test_field_lengths_recorded():
    """show_notes_text_len / rss_*_len 等字段必须存在"""
    entry = make_entry(
        desc="Description text here",
        enc="Content encoded longer text here",
        summ="Itunes summary",
        sub=""
    )
    result = select_best_show_notes(entry)
    for key in ["show_notes_text_len", "show_notes_truncated",
               "rss_description_len", "rss_content_encoded_len",
               "rss_itunes_summary_len", "show_notes_source"]:
        assert key in result, f"Missing field: {key}"
    assert result["rss_description_len"] == len(clean_show_notes("Description text here"))
    assert result["rss_content_encoded_len"] == len(clean_show_notes("Content encoded longer text here"))
    print("✓ all audit fields present and correct")


def test_no_hard_truncation_at_500():
    """确认 parse_rss_episodes 不再把 show_notes 截断到 500"""
    long_desc = "X" * 2000
    xml = f"""<item>
<title>Long Episode</title>
<description><![CDATA[{long_desc}]]></description>
</item>"""
    episodes = parse_rss_episodes(xml, "Test")
    ep = episodes[0]
    # 必须保留完整长度
    assert ep["show_notes_text_len"] == 2000, \
        f"Was truncated! show_notes_text_len={ep['show_notes_text_len']}"
    print("✓ parse_rss_episodes: no 500-char hard truncation")


def test_truncated_flag_only_when_over_limit():
    entry_ok = make_entry(desc="Short text", enc="", summ="", sub="")
    r_ok = select_best_show_notes(entry_ok)
    assert r_ok["show_notes_truncated"] is False

    entry_big = make_entry(desc="Y" * (SHOW_NOTES_MAX_CHARS + 1), enc="", summ="", sub="")
    r_big = select_best_show_notes(entry_big)
    assert r_big["show_notes_truncated"] is True
    print("✓ show_notes_truncated flag only set when over limit")


if __name__ == "__main__":
    # ── 防止 exec 后自动触发 main() ────────────────────────────────
    # podcast_screener.py 末尾有 if __name__ == "__main__": main()
    # 用非 __main__ 绕过
    import types
    tests = [
        test_uses_content_encoded_when_longer,
        test_itunes_summary_wins_when_longest,
        test_description_fallback,
        test_cdata_content_encoded_parsed,
        test_html_entities_preserved,
        test_show_notes_not_truncated_to_500,
        test_show_notes_max_chars_truncation,
        test_jre_mma_big_john_not_lost,
        test_lex_497_don_lincoln_not_lost,
        test_a16z_1b_exits_david_george_not_lost,
        test_zxj_143_he_xiaopeng_info_preserved,
        test_zxj_142_dai_yusen_info_preserved,
        test_field_lengths_recorded,
        test_no_hard_truncation_at_500,
        test_truncated_flag_only_when_over_limit,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"FAIL: {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR: {t.__name__}: {e}")
            failed += 1

    print()
    if failed == 0:
        print("=" * 60)
        print("ALL TESTS PASSED")
        print("=" * 60)
    else:
        print("=" * 60)
        print(f"SOME TESTS FAILED ✗ ({failed} failures)")
        print("=" * 60)
        sys.exit(1)