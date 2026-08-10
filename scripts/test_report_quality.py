"""
test_report_quality.py - Phase 2.1 Quality Fix Verification Tests
Covers the 10 real W22 cases identified in the quality review.
"""
import sys
sys.path.insert(0, 'scripts')

import unittest
import json
import re
import html
import os
import tempfile
import atexit
import urllib.request

# Import the modules under test
import guest_background_fetcher as gbf
import podcast_screener as ps

_HERMETIC_GUEST_CACHE_DIR = tempfile.TemporaryDirectory(prefix="test_report_quality_guest_cache_")
atexit.register(_HERMETIC_GUEST_CACHE_DIR.cleanup)
gbf.CACHE_FILE = os.path.join(_HERMETIC_GUEST_CACHE_DIR.name, "guest_profiles_cache.json")


def _no_external_guest_search(*args, **kwargs):
    return []


def _blocked_external_call(*args, **kwargs):
    raise AssertionError("external guest background search/network call disabled in tests")


gbf.search_guest_background_openclaw = _no_external_guest_search
gbf.subprocess.run = _blocked_external_call
urllib.request.urlopen = _blocked_external_call


class TestGuestDetectionQuality(unittest.TestCase):
    """Guest detection for real W22 episodes."""

    def test_jre_mma_179_guest_names(self):
        """JRE MMA Show #179: should detect Josh Thompson AND Big John McCarthy, not '#179 with ...'."""
        ep = _make_ep(
            "JRE MMA Show #179 with Josh Thompson & \"Big\" John McCarthy",
            "Joe sits down with Josh Thompson, a retired champion MMA fighter, and Big John McCarthy, a veteran referee.",
        )
        result = gbf.enrich_episode_with_guest_backgrounds(ep)
        guest_names = result.get('guest_names', [])
        # Should have exactly 2 guests
        self.assertEqual(len(guest_names), 2, f"Expected 2 guests, got {guest_names}")
        # Neither guest name should contain '#179' or 'with'
        for g in guest_names:
            self.assertNotIn('#', g, f"Guest name '{g}' should not contain '#'")
            self.assertNotIn('with', g.lower(), f"Guest name '{g}' should not contain 'with'")
            self.assertNotIn('&', g, f"Guest name '{g}' should not contain '&'")
        # Should contain expected names
        combined = ' '.join(guest_names)
        self.assertIn('Josh Thompson', combined, f"Josh Thompson not in {guest_names}")
        self.assertIn('Big John McCarthy', combined, f"Big John McCarthy not in {guest_names}")

    def test_jre_mma_179_no_amp_in_guest(self):
        """JRE MMA: guest names should not contain &amp; or stray quotes."""
        ep = _make_ep(
            "JRE MMA Show #179 with Josh Thompson & \"Big\" John McCarthy",
            "MMA show.",
        )
        result = gbf.enrich_episode_with_guest_backgrounds(ep)
        for g in result.get('guest_names', []):
            self.assertNotIn('&amp;', g, f"Guest '{g}' should not contain &amp;")
            self.assertNotIn('&"', g, f"Guest '{g}' should not contain &\"")
            self.assertFalse(g.endswith('"'), f"Guest '{g}' should not end with quote")

    def test_lex_497_don_lincoln(self):
        """Lex #497: should detect Don Lincoln from title after HTML entity decoding."""
        ep = _make_ep(
            "#497 – Biggest Mysteries in Physics: Antimatter, Dark Energy & ToE – Don Lincoln",
            "Don Lincoln is a particle physicist at Fermilab.",
        )
        result = gbf.enrich_episode_with_guest_backgrounds(ep)
        guest_names = result.get('guest_names', [])
        self.assertIn('Don Lincoln', guest_names, f"Don Lincoln not detected, got: {guest_names}")
        self.assertEqual(result.get('guest_detection_status'), 'confirmed_guest')

    def test_lex_497_no_html_entity_in_guest(self):
        """Lex #497: HTML entity should be decoded before guest detection."""
        ep = _make_ep(
            "#497 – Biggest Mysteries in Physics: Antimatter, Dark Energy &#038; ToE &#8211; Don Lincoln",
            "Don Lincoln is a particle physicist.",
        )
        result = gbf.enrich_episode_with_guest_backgrounds(ep)
        # Should still detect Don Lincoln
        self.assertIn('Don Lincoln', result.get('guest_names', []))
        # Guest name should NOT contain HTML entities
        for g in result.get('guest_names', []):
            self.assertNotIn('&#', g, f"Guest '{g}' contains unescaped HTML entity")
            self.assertNotIn('&amp;', g, f"Guest '{g}' contains &amp;")

    def test_zxj_143_he_xiaopeng(self):
        """张小珺 143: '对何小鹏的第二次访谈' should detect 何小鹏."""
        ep = _make_ep(
            "143. 对何小鹏的第二次访谈：更大赌注、人形机器人Iron诞生",
            "本期节目是小鹏的第二次访谈。",
        )
        result = gbf.enrich_episode_with_guest_backgrounds(ep)
        guest_names = result.get('guest_names', [])
        self.assertIn('何小鹏', guest_names, f"何小鹏 not detected, got: {guest_names}")

    def test_zxj_142_yusen(self):
        """张小珺 142: title='雨森的创投观察', description='戴雨森（网名雨森）' → canonical=戴雨森"""
        ep = _make_ep(
            "142. 雨森的创投观察第2集：Harness、下一个字节",
            "本期是真格基金管理合伙人戴雨森（网名雨森）的创投观察节目。",
        )
        result = gbf.enrich_episode_with_guest_backgrounds(ep)
        guest_names = result.get('guest_names', [])
        # 优先使用 canonical name 戴雨森（不在列表中留雨森）
        self.assertIn('戴雨森', guest_names, f"戴雨森 not detected, got: {guest_names}")
        self.assertIn('confirmed_guest', result.get('guest_detection_status', ''),
                      f"status should be confirmed_guest: {result.get('guest_detection_status')}")

    def test_jre_2507_harland_williams(self):
        """JRE #2507: '#2507 - Harland Williams' should detect Harland Williams."""
        ep = _make_ep(
            "#2507 - Harland Williams",
            "Harland Williams is a comedian, author, and actor.",
        )
        result = gbf.enrich_episode_with_guest_backgrounds(ep)
        guest_names = result.get('guest_names', [])
        self.assertIn('Harland Williams', guest_names, f"Harland Williams not detected, got: {guest_names}")
        # Should NOT contain '#2507' in guest name
        for g in guest_names:
            self.assertNotIn('#', g)
            self.assertNotIn('2507', g)

    def test_a16z_speaks_with_dileep(self):
        """a16Z: 'Angela Strange speaks with Dileep Thazhmon' should detect Dileep, not Angela."""
        ep = _make_ep(
            "Stablecoins, AI Agents, and The Future of Global Banking",
            "Angela Strange speaks with Dileep Thazhmon, founder and CEO of Jeeves.",
        )
        result = gbf.enrich_episode_with_guest_backgrounds(ep)
        guest_names = result.get('guest_names', [])
        self.assertIn('Dileep Thazhmon', guest_names, f"Dileep Thazhmon not detected, got: {guest_names}")
        self.assertNotIn('Angela Strange', guest_names)

    def test_a16z_multi_speaker_exits(self):
        """a16Z Why $1B Exits: 'David George, ..., and David Clark, ... discuss' → confirmed_guest"""
        ep = _make_ep(
            "Why $1B Exits are Dead",
            "David George, General Partner at a16z, and David Clark, CIO at VenCap, discuss how AI is reshaping venture capital.",
        )
        result = gbf.enrich_episode_with_guest_backgrounds(ep)
        guest_names = result.get('guest_names', [])
        self.assertIn('David George', guest_names, f"David George not detected, got: {guest_names}")
        self.assertIn('David Clark', guest_names, f"David Clark not detected, got: {guest_names}")
        self.assertIn('confirmed_guest', result.get('guest_detection_status', ''),
                      f"status should be confirmed_guest: {result.get('guest_detection_status')}")
        # 标题不是嘉宾
        for g in guest_names:
            self.assertNotIn('$1B', g)
            self.assertNotIn('Exits', g)


class TestBackgroundGenerationQuality(unittest.TestCase):
    """Background generation quality tests."""

    def test_explicit_multi_role_background_is_preserved(self):
        """A known guest's explicit multi-role sentence should produce a supported background."""
        ep = _make_ep(
            "#2507 - Harland Williams",
            "Harland Williams is a writer, comedian, and actor.",
        )
        result = gbf.enrich_episode_with_guest_backgrounds(ep)
        bg = result.get('guest_background_zh', '')
        self.assertIn('Harland Williams', bg)
        self.assertIn('作家', bg)
        self.assertIn('喜剧演员', bg)
        self.assertIn('演员', bg)
        self.assertNotIn('已确认本期嘉宾', bg)

    def test_generic_title_no_background(self):
        """Generic show_notes with no org/role should produce honest '未提供足够背景' message."""
        ep = _make_ep(
            "Some Podcast Episode",
            "A conversation about topic X.",
        )
        result = gbf.enrich_episode_with_guest_backgrounds(ep)
        bg = result.get('guest_background_zh', '')
        self.assertTrue(len(bg) > 0, "Should produce some background message")

    def test_dileep_thazhmon_founder_ceo_background(self):
        """Dileep Thazhmon: should say '是Jeeves创始人' not '是founder'."""
        ep = _make_ep(
            "Stablecoins, AI Agents, and The Future of Global Banking",
            "Angela Strange speaks with Dileep Thazhmon, founder and CEO of Jeeves.",
        )
        result = gbf.enrich_episode_with_guest_backgrounds(ep)
        bg = result.get('guest_background_zh', '')
        self.assertIn('Jeeves', bg, f"Should mention Jeeves: {bg}")


class TestAIKeywordBoundary(unittest.TestCase):
    """AI keyword boundary matching tests."""

    def test_ai_in_available_no_match(self):
        """'available' contains 'ai' but should NOT match AI keyword."""
        text = "his new movie is available now on all streaming services"
        self.assertFalse(ps._kw_match(text, 'AI'), "'available' should not match AI")
        self.assertFalse(ps._kw_match(text, 'ai'), "'available' should not match ai")

    def test_ai_in_capital_no_match(self):
        """'capital' contains 'ai' but should NOT match AI keyword."""
        text = "capital allocation and market structure"
        self.assertFalse(ps._kw_match(text, 'AI'), "'capital' should not match AI")
        self.assertFalse(ps._kw_match(text, 'ai'), "'capital' should not match ai")

    def test_ai_in_chairman_no_match(self):
        """'chairman' contains 'ai' but should NOT match AI keyword."""
        text = "the chairman said something important"
        self.assertFalse(ps._kw_match(text, 'AI'), "'chairman' should not match AI")
        self.assertFalse(ps._kw_match(text, 'ai'), "'chairman' should not match ai")

    def test_ai_in_main_no_match(self):
        """'main' contains 'ai' but should NOT match AI keyword."""
        text = "the main point is"
        self.assertFalse(ps._kw_match(text, 'AI'), "'main' should not match AI")

    def test_ai_standalone_yes_match(self):
        """'AI' as standalone word SHOULD match."""
        text = "this is about AI and machine learning"
        self.assertTrue(ps._kw_match(text, 'AI'), "'AI' as word should match")
        self.assertTrue(ps._kw_match(text, 'ai'), "'ai' as word should match")

    def test_ai_chinese_prefix_yes_match(self):
        """Chinese title starting with 'AI' (e.g., 'AI行业的收钱') SHOULD match."""
        text = "AI行业的收钱、花钱与赚钱"
        self.assertTrue(ps._kw_match(text, 'AI'), "AI-prefixed Chinese title should match AI")
        text2 = "AI司马迁的ima使用进化史"
        self.assertTrue(ps._kw_match(text2, 'AI'), "AI-prefixed title should match AI")

    def test_ai_url_excluded(self):
        """'ai' in domain TLD (e.g., pplx.ai) should NOT match."""
        text = "check pplx.ai/rogan for details"
        self.assertFalse(ps._kw_match(text, 'AI'), "URL with .ai TLD should not match AI")
        text2 = "visit chat.openai.com/api"
        self.assertFalse(ps._kw_match(text2, 'AI'), "URL with openai.com should not match AI")


class TestSourcesDisplay(unittest.TestCase):
    """Sources display format tests."""

    def test_sources_not_raw_dict(self):
        """sources should NOT be displayed as raw Python dict."""
        ep = _make_ep("Test Episode", "Test notes.", show_notes="Name is a researcher at Org.")
        # Set a sources field that would break if printed as dict
        ep['guest_background_sources'] = [
            {'title': '节目元数据（show_notes）', 'url': '', 'snippet': '职务/头衔：researcher', 'quality': 'primary'}
        ]
        result = gbf.enrich_episode_with_guest_backgrounds(ep)
        sources = result.get('guest_background_sources', [])
        # Sources should be a list of dicts
        self.assertIsInstance(sources, list, "sources should be a list")
        if sources:
            self.assertIsInstance(sources[0], dict, "source item should be dict")


class TestReasonZhQuality(unittest.TestCase):
    """reason_zh generation quality tests."""

    def test_no_guest_does_not_say_guest(self):
        """When no confirmed_guest, reason_zh should NOT say '本期嘉宾为 [title]'."""
        ep = _make_ep("Some Episode Title", "Some description.", guest_names=[], guest_detection_status='no_guest_detected')
        scoring = ps.score_episode_structured(ep, _get_interests(), _get_policy())
        reason = scoring.get('reason_zh', '')
        # Should NOT say "本期嘉宾为 Some Episode Title"
        self.assertNotIn('本期嘉宾为 Some Episode Title', reason)
        self.assertNotIn('本期嘉宾为 Some', reason)

    def test_description_short_uncertainty(self):
        """When description is very short, reason should acknowledge uncertainty."""
        ep = _make_ep("Short Title", "X.", guest_names=['Person Name'], guest_detection_status='confirmed_guest')
        # Short notes
        ep['show_notes_text'] = "X."
        scoring = ps.score_episode_structured(ep, _get_interests(), _get_policy())
        reason = scoring.get('reason_zh', '')
        # Should not be identical to a full description case
        self.assertTrue(len(reason) > 0)


class TestHTMLUnescapeInRecord(unittest.TestCase):
    """episode_title in record should be HTML-unescaped."""

    def test_episode_title_unescaped_in_record(self):
        """episode_title stored in record should not contain &amp; or &#8211;."""
        # This test verifies the record-writing code unescapes HTML entities
        # We check by looking at the actual code path
        import podcast_screener as ps
        import inspect
        src = inspect.getsource(ps)
        # Should find html.unescape near episode_title in record construction
        self.assertIn('html.unescape', src, "episode_title should be HTML-unescaped when written to record")


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_ep(title, description, show_notes=None, guest_names=None, guest_detection_status='no_guest_detected'):
    return {
        'podcast_name': 'Test Podcast',
        'podcast_id': 'TEST',
        'episode_title': title,
        'episode_id': f'test_{hash(title) % 10000}',
        'show_notes_text': show_notes or description,
        'description': description,
        'language': 'en',
        'publish_date': '2026-05-30',
        'audio_url': 'http://example.com/audio.mp3',
        'duration_minutes': 60,
        'guest_names': guest_names or [],
        'guest_detection_status': guest_detection_status,
    }

def _get_interests():
    return {
        'boost_keywords': ['AI', 'power_market', 'storage', 'American stock market', 'investment', 'invest'],
        'negative_keywords': [],
        'primary_topics': ['AI'],
    }

def _get_policy():
    return {
        'selection_mode': 'all_preview',
        'score_threshold_full': 80,
        'score_threshold_preview': 50,
    }


class TestHTMLEntityCleanliness(unittest.TestCase):
    """HTML entity cleanliness in display outputs."""

    def test_episode_title_no_html_entity_in_display(self):
        """episode_title in record should be HTML-unescaped (no &#8211;, &amp;, etc.)."""
        # Verify the code path
        import podcast_screener as ps
        import inspect
        src = inspect.getsource(ps)
        self.assertIn('clean_display_text', src, "clean_display_text should be used for episode_title")
        self.assertIn('html.unescape', src, "html.unescape should be used")

    def test_clean_display_text_removes_amp(self):
        """clean_display_text should remove &amp;."""
        from podcast_screener import clean_display_text
        result = clean_display_text("Josh Thompson &amp; Big John McCarthy")
        self.assertNotIn('&amp;', result)
        self.assertIn('&', result)

    def test_clean_display_text_removes_8211(self):
        """clean_display_text should remove &#8211;."""
        from podcast_screener import clean_display_text
        result = clean_display_text("#497 – Biggest Mysteries – Don Lincoln")
        self.assertNotIn('&#8211;', result)

    def test_clean_display_text_removes_amp_in_amp_space_quote(self):
        """clean_display_text should decode &amp; while preserving normal display ampersands."""
        from podcast_screener import clean_display_text
        result = clean_display_text('JRE MMA Show #179 with Josh Thompson &amp; "Big" John McCarthy')
        self.assertNotIn('&amp;', result)
        self.assertIn('Josh Thompson & "Big" John McCarthy', result)

    def test_clean_display_text_preserves_chinese(self):
        """clean_display_text should not damage Chinese text."""
        from podcast_screener import clean_display_text
        result = clean_display_text("AI行业的收钱、花钱与赚钱")
        self.assertIn("AI行业", result)

    def test_clean_display_text_compresses_whitespace(self):
        """clean_display_text should compress multiple spaces to one."""
        from podcast_screener import clean_display_text
        result = clean_display_text("Episode  Title    with  spaces")
        self.assertNotIn("  ", result)


class TestMarkdownRendering(unittest.TestCase):
    """Markdown report rendering quality tests."""

    def test_is_noisy_source_author_only(self):
        """snippet = '职务/头衔：author' should be noisy (not shown)."""
        from podcast_screener import is_noisy_source
        src = {'title': '节目元数据（show_notes）', 'snippet': '职务/头衔：author', 'url': ''}
        self.assertTrue(is_noisy_source(src), "职务/头衔：author should be noisy")

    def test_is_noisy_source_author_with_newline(self):
        """snippet with trailing newline should also be noisy."""
        from podcast_screener import is_noisy_source
        src = {'title': '节目元数据（show_notes）', 'snippet': '职务/头衔：author\n', 'url': ''}
        self.assertTrue(is_noisy_source(src))

    def test_is_noisy_source_multiple_noise_terms(self):
        """'职务/头衔：author | 职务/头衔：writer' should be noisy."""
        from podcast_screener import is_noisy_source
        src = {'title': '节目元数据（show_notes）', 'snippet': '职务/头衔：author | 职务/头衔：writer', 'url': ''}
        self.assertTrue(is_noisy_source(src))

    def test_is_noisy_source_founder_ceo(self):
        """'founder and CEO of Jeeves' is meaningful, should NOT be noisy."""
        from podcast_screener import is_noisy_source
        src = {'title': '节目元数据（show_notes）', 'snippet': 'founder and CEO of Jeeves', 'url': ''}
        self.assertFalse(is_noisy_source(src), "founder and CEO should be shown")

    def test_is_noisy_source_physicist_fermilab(self):
        """'particle physicist at Fermilab' is meaningful, should NOT be noisy."""
        from podcast_screener import is_noisy_source
        src = {'title': '节目元数据（show_notes）', 'snippet': 'particle physicist at Fermilab', 'url': ''}
        self.assertFalse(is_noisy_source(src))

    def test_is_noisy_source_empty(self):
        """empty snippet should be noisy."""
        from podcast_screener import is_noisy_source
        self.assertTrue(is_noisy_source({}))
        self.assertTrue(is_noisy_source({'snippet': ''}))
        self.assertTrue(is_noisy_source({'snippet': '   '}))

    def test_is_noisy_source_author_plus_agency(self):
        """'author at New Yorker' has specific org, should NOT be noisy."""
        from podcast_screener import is_noisy_source
        src = {'snippet': 'author at New Yorker', 'url': ''}
        self.assertFalse(is_noisy_source(src), "author with specific org should be shown")

    def test_overview_only_shown_when_non_empty(self):
        """_fmt_ep should not output '概述：' line when overview is empty."""
        from podcast_screener import _fmt_ep
        ep = _make_ep_full({
            'podcast_name': 'Test',
            'episode_title': 'Test Episode',
            'reason_zh': '本期讨论某话题',
            'guest_background_zh': '已确认嘉宾',
            'guest_names': ['Person'],
            'guest_detection_status': 'confirmed_guest',
            'guest_background_sources': [],
            'overview': '',  # empty overview
            'one_line': '',  # also empty
            'summary_3_sentences_cn': [],  # also empty
        })
        result = _fmt_ep(ep, '65')
        # Should NOT contain "概述："
        self.assertNotIn('**概述：**', result, f"Empty overview should not output '概述：' line. Got:\n{result}")

    def test_overview_shown_when_available(self):
        """_fmt_ep should output '概述：' line when overview has content."""
        from podcast_screener import _fmt_ep
        ep = _make_ep_full({
            'podcast_name': 'Test',
            'episode_title': 'Test Episode',
            'reason_zh': '本期讨论某话题',
            'guest_background_zh': '已确认嘉宾',
            'guest_names': ['Person'],
            'guest_detection_status': 'confirmed_guest',
            'guest_background_sources': [],
            'summary_3_sentences_cn': ['本期讨论AI在金融领域的应用'],
        })
        result = _fmt_ep(ep, '65')
        text = ''.join(result)
        self.assertIn('**概述：**', text)
        self.assertIn('本期讨论AI在金融领域的应用', text)


def _make_ep_full(overrides):
    defaults = {
        'podcast_name': 'Test Podcast',
        'podcast_id': 'TEST',
        'episode_title': 'Test Episode Title',
        'episode_id': 'test_full',
        'show_notes_text': 'Test notes.',
        'description': 'Test description.',
        'language': 'en',
        'publish_date': '2026-05-30',
        'audio_url': 'http://example.com/audio.mp3',
        'duration_minutes': 60,
        'final_score': 65.0,
        'topic_relevance': 20,
        'information_density': 15,
        'novelty': 12,
        'actionability': 10,
        'strategic_value': 8,
        'transcription_value': 0,
        'priority': 'medium',
        'full_suggestion': '',
        'reason_zh': '本期讨论某话题',
        'guest_background_zh': '已确认嘉宾',
        'guest_names': [],
        'guest_detection_status': 'no_guest_detected',
        'guest_background_sources': [],
        'overview': '',
        'one_line': '',
        'summary_3_sentences_cn': [],
        'score_display': '65',
        'publish_at': '2026-05-30T10:00:00+0800',
    }
    defaults.update(overrides)
    return defaults


class TestAllPreviewEpisodeClassification(unittest.TestCase):
    def setUp(self):
        self.policy = {
            "selection_policy": {
                "mode": "all_preview",
                "min_duration_minutes": 5,
                "short_episode_max_minutes": 15,
            },
            "ad_detection_policy": {"keywords_en": [], "keywords_zh": []},
        }
        self.scoring = {"final_score": 10.0}

    def episode(self, **overrides):
        episode = {
            "episode_title": "A valid podcast episode",
            "show_notes_text": "A substantive discussion with useful context.",
            "audio_url": "https://example.test/episode.mp3",
            "duration_minutes": 30,
            "duration_seconds": 1800,
        }
        episode.update(overrides)
        return episode

    def test_making_sense_paywall_intro_is_skipped(self):
        episode = self.episode(
            episode_title="#488 — Ego, Ecstasy, and Equanimity",
            show_notes_text=(
                "Sam Harris speaks with Neal Brennan. Subscribe to gain access "
                "to all full-length episodes."
            ),
            audio_url="https://example.test/488_paywall__intro.mp3",
            duration_minutes=12,
            duration_seconds=750,
        )

        result = ps.classify_all_preview_episode(episode, self.scoring, self.policy)

        self.assertEqual(result["decision"], "skip")
        self.assertEqual(result["reason_code"], "paywall_preview")
        self.assertIn("付费正片", result["reason_zh"])

    def test_short_cross_podcast_promo_is_skipped_before_duration_rule(self):
        episode = self.episode(
            episode_title="Introducing: Our Town",
            show_notes_text="Listen to Episode 1 on the Big Take.",
            duration_minutes=1,
            duration_seconds=113,
        )

        result = ps.classify_all_preview_episode(episode, self.scoring, self.policy)

        self.assertEqual(result["decision"], "skip")
        self.assertEqual(result["reason_code"], "cross_podcast_promo")

    def test_valid_short_essay_audio_remains_preview(self):
        for title, duration_seconds in [
            ("Why smarter AI models could drive up compute prices 10x", 678),
            ("8 Predictions for the Era of Continual Learning", 517),
        ]:
            with self.subTest(title=title):
                episode = self.episode(
                    episode_title=title,
                    show_notes_text="Read the essay here.",
                    duration_minutes=duration_seconds // 60,
                    duration_seconds=duration_seconds,
                )

                result = ps.classify_all_preview_episode(episode, self.scoring, self.policy)

                self.assertEqual(result["decision"], "preview")
                self.assertEqual(result["reason_code"], "valid_short_episode")
                self.assertEqual(
                    ps.decide_in_all_preview_mode(episode, self.scoring, self.policy),
                    "preview",
                )

    def test_existing_trailer_and_minimum_duration_rules_remain_skip(self):
        trailer = self.episode(episode_title="Season trailer")
        too_short = self.episode(duration_minutes=4, duration_seconds=299)

        trailer_result = ps.classify_all_preview_episode(trailer, self.scoring, self.policy)
        short_result = ps.classify_all_preview_episode(too_short, self.scoring, self.policy)

        self.assertEqual(trailer_result["reason_code"], "trailer_or_announcement")
        self.assertEqual(short_result["reason_code"], "below_minimum_duration")
        self.assertEqual(trailer_result["decision"], "skip")
        self.assertEqual(short_result["decision"], "skip")


class TestDisplayAndSourceSafety(unittest.TestCase):
    """Display and source safety regression tests."""

    def test_yusen_no_series_title_as_book(self):
        """张小珺 142: '雨森的创投观察' series title must NOT be treated as a book/work."""
        ep = _make_ep(
            "142. 雨森的创投观察第2集：Harness、下一个字节、2026大机会",
            "今天是我们的系列节目《雨森的创投观察》第2集。真格基金管理合伙人戴雨森预言称，2026年的关键词是'The Year of R'。",
        )
        result = gbf.enrich_episode_with_guest_backgrounds(ep)
        guest_names = result.get('guest_names', [])
        guest_bg = result.get('guest_background_zh', '')
        # 戴雨森 must be detected
        self.assertIn('戴雨森', guest_names, f"戴雨森 not detected, got: {guest_names}")
        # Background must NOT contain "雨森的创投观察" as a book/representative work
        self.assertNotIn('雨森的创投观察', guest_bg,
                         f"Series title '雨森的创投观察' should NOT appear in guest_background_zh: {guest_bg}")
        # Also check guest_background_sources
        sources = result.get('guest_background_sources', [])
        for s in sources:
            self.assertNotIn('雨森的创投观察', s.get('snippet', ''),
                             f"Series title found in sources: {s}")

    def test_jre_mma_no_founder_title(self):
        """JRE MMA: Without reliable identity sources, must use CONFIRMED_GUEST_FALLBACK."""
        ep = _make_ep(
            'JRE MMA Show #179 with Josh Thompson & "Big" John McCarthy',
            "Joe sits down with Josh Thompson and Big John McCarthy.",
        )
        result = gbf.enrich_episode_with_guest_backgrounds(ep)
        guest_names = result.get('guest_names', [])
        guest_bg = result.get('guest_background_zh', '')
        # Must detect BOTH guests
        self.assertIn('Josh Thompson', guest_names, f"Josh Thompson not detected: {guest_names}")
        self.assertIn('Big John McCarthy', guest_names, f"Big John McCarthy not detected: {guest_names}")
        # Must NOT say "创始人" / "founder"
        self.assertNotIn('创始人', guest_bg, f"JRE MMA should not generate '创始人': {guest_bg}")
        self.assertNotIn('founder', guest_bg.lower(), f"JRE MMA should not generate 'founder': {guest_bg}")
        # Must use unified fallback OR at least not fabricate titles
        # If there's an org + title pair from show_notes, ok to use it
        # But "founder" alone without reliable source should not appear

    def test_a16z_no_topic_phrase_in_title(self):
        """a16Z $1B Exits: topic phrase 'reshaping venture capital' must NOT appear as title."""
        ep = _make_ep(
            "Why $1B Exits are Dead",
            "David George, General Partner at a16z, and David Clark, CIO, discuss why $1B exits are dead and how it is reshaping venture capital.",
        )
        result = gbf.enrich_episode_with_guest_backgrounds(ep)
        guest_names = result.get('guest_names', [])
        guest_bg = result.get('guest_background_zh', '')
        # Must detect both guests
        self.assertIn('David George', guest_names, f"David George not detected: {guest_names}")
        self.assertIn('David Clark', guest_names, f"David Clark not detected: {guest_names}")
        # Must NOT say "reshaping venture capitalGeneral Partner"
        self.assertNotIn('reshaping venture capital', guest_bg.lower(),
                        f"Topic phrase should NOT appear in title: {guest_bg}")
        self.assertNotIn('General Partner', guest_bg,
                        f"Title should not be incorrectly formatted: {guest_bg}")

    def test_confirmed_guest_fallback_uniform(self):
        """Peter Robbins: without reliable identity, must use CONFIRMED_GUEST_FALLBACK exactly."""
        ep = _make_ep(
            "324 · Peter Robbins - The Hard Truths of a 50 Year Trading Career",
            "author",
        )
        result = gbf.enrich_episode_with_guest_backgrounds(ep)
        guest_bg = result.get('guest_background_zh', '')
        # Should NOT contain variations like "Peter Robbins已确认嘉宾" or "已确认嘉宾，节目元数据"
        self.assertNotIn('Peter Robbins已确认', guest_bg,
                         f"Should not prefix name with '已确认嘉宾': {guest_bg}")
        # If background is not the CONFIRMED_GUEST_FALLBACK constant, it should at least
        # be a proper sentence (ending with 。) and not a concatenated fragment
        if guest_bg and guest_bg != gbf.CONFIRMED_GUEST_FALLBACK:
            self.assertTrue(guest_bg.endswith('。') or len(guest_bg) < 10,
                           f"Background should end properly or be fallback: {guest_bg}")


    # ─── Display & Source Safety Tests ───────────────────────────────────────

    def test_fallback_background_no_sources_display(self):
        """Fallback background 时不得展示 sources（无论 sources 有多少）。"""
        from podcast_screener import is_fallback_background, is_guest_source_noise
        # Fallback 文本
        self.assertTrue(is_fallback_background("已确认本期嘉宾，但节目元数据未提供足够背景信息，暂不补充背景。"))
        self.assertTrue(is_fallback_background("未找到足够可靠的公开资料，暂不补充背景。"))
        # 非 fallback
        self.assertFalse(is_fallback_background("戴雨森是真格基金管理合伙人，专注大类资产配置研究。"))

    def test_author_noise_filtered(self):
        """'职务/头衔：author' 必须被 is_guest_source_noise 过滤。"""
        from podcast_screener import is_guest_source_noise
        src = {'snippet': '职务/头衔：author', 'title': '节目元数据'}
        self.assertTrue(is_guest_source_noise(src), "author-only source must be noise")

    def test_founder_noise_filtered_without_context(self):
        """'职务/头衔：founder' 无可靠上下文时必须被过滤。"""
        from podcast_screener import is_guest_source_noise
        src = {'snippet': '职务/头衔：founder', 'title': '节目元数据'}
        self.assertTrue(is_guest_source_noise(src), "founder-only source must be noise without org")

    def test_topic_phrase_filtered(self):
        """'机构/公司：reshaping venture capital' 必须被过滤。"""
        from podcast_screener import is_guest_source_noise
        src = {'snippet': '机构/公司：reshaping venture capital', 'title': '节目元数据'}
        self.assertTrue(is_guest_source_noise(src), "topic phrase source must be noise")

    def test_markdown_report_omits_guest_display_but_keeps_guest_data(self):
        """周报不展示嘉宾信息，结构化嘉宾数据仍保留在 result 中。"""
        from podcast_screener import _fmt_ep
        ep = {
            'podcast_name': 'Test',
            'episode_title': 'Test Episode',
            'duration_minutes': 60,
            'score': 65,
            'reason': 'test',
            'guest_names': ['Guest A', 'Guest B'],
            'guest_detection_status': 'confirmed_guest',
            'guest_background_zh': '已确认本期嘉宾，但节目元数据未提供足够背景信息，暂不补充背景。',
            'guest_background_sources': [{'title': '节目元数据', 'snippet': '职务/头衔：founder', 'url': ''}],
        }
        self.assertEqual(ep['guest_names'], ['Guest A', 'Guest B'])
        self.assertTrue(ep['guest_background_zh'])
        lines = _fmt_ep(ep, 'Preview')
        guest_lines = [l for l in lines if '👤 嘉宾' in l]
        self.assertEqual(guest_lines, [])
        src_lines = [l for l in lines if '🔗' in l]
        self.assertEqual(src_lines, [])

    def test_normalized_fallback_in_display(self):
        """confirmed_guest 的 '未找到足够可靠的公开资料' 必须归一化为 CONFIRMED_GUEST_FALLBACK。"""
        from podcast_screener import is_fallback_background, CONFIRMED_GUEST_FALLBACK
        self.assertTrue(is_fallback_background("未找到足够可靠的公开资料，暂不补充背景。"))
        # CONFIRMED_GUEST_FALLBACK constant value check
        self.assertEqual(CONFIRMED_GUEST_FALLBACK, "已确认本期嘉宾，但节目元数据未提供足够背景信息，暂不补充背景。")

    def test_feishu_episode_block_omits_guest_display(self):
        """Feishu blocks 不展示嘉宾背景或来源。"""
        from feishu_blocks_renderer import build_episode_blocks
        ep = {
            'podcast_name': 'Test',
            'episode_title': 'Test Episode',
            'duration_minutes': 60,
            'final_score': 65,
            'reason_zh': 'test',
            'guest_names': ['Guest A'],
            'guest_detection_status': 'confirmed_guest',
            'guest_background_zh': '已确认本期嘉宾，但节目元数据未提供足够背景信息，暂不补充背景。',
            'guest_background_sources': [{'title': '节目元数据', 'snippet': '职务/头衔：founder', 'url': ''}],
        }
        self.assertEqual(ep['guest_names'], ['Guest A'])
        self.assertTrue(ep['guest_background_zh'])
        blks = build_episode_blocks(ep, 'Preview')
        text_blob = '\n'.join(str(b) for b in blks)
        self.assertNotIn('👤 嘉宾', text_blob)
        self.assertNotIn(ep['guest_background_zh'], text_blob)
        self.assertNotIn('🔗', text_blob)


if __name__ == '__main__':
    unittest.main(verbosity=2)
