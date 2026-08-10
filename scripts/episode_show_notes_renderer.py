#!/usr/bin/env python3
"""Pure display helpers for episode show notes.

This module intentionally has no filesystem, network, subprocess, RSS, Feishu,
or guest-cache side effects.
"""

from __future__ import annotations

import html
import re


SHOW_NOTES_PLACEHOLDER = "暂无节目介绍。"
SHOW_NOTES_TRANSLATED_HEADING = "节目介绍 / Show Notes（中文翻译，原文已保留）"
SHOW_NOTES_DISPLAY_SNAPSHOT_VERSION = "show_notes_display_v1"
DEFAULT_SHOW_NOTES_CHUNK_CHARS = 1200
MIN_LANGUAGE_SIGNAL_CHARS = 80


_BOILERPLATE_LINE_PATTERNS = (
    r'learn more about your ad choices',
    r'podcastchoices\.com/adchoices',
    r'hosted by simplecast',
    r'adswizz',
    r'pcm\.adswizz\.com',
    r'for more details please see a16z\.com/disclosures',
    r'^stay updated:',
    r'find a16z on youtube',
    r'listen to the a16z show on spotify',
    r'^perplexity:\s*download the app or ask perplexity',
    r'download the app or ask perplexity',
    r'bluechew',
    r'use code rogan',
    r'promo code rogan',
    r'draftkings',
    r'dkng\.co/rogan',
    r'armra\.com/rogan',
    r'visible\.com',
    r'visible\+ pro',
    r'betterhelp\.com/jre',
    r'chime\.com/rogan',
    r'open an account in minutes at',
    r'ziprecruiter\.com/rogan',
    r'thefarmersdog\.com/rogan',
    r'wildpastures\.com/rogan',
    r'^become a colossus member\b',
    r'ramp\.com/invest',
    r'vanta\.com/invest',
    r'\bworkos is infrastructure for b2b and ai-native companies\b',
    r'\bworkos.*\binfrastructure\b.*\bsell to enterprise\b',
    r'^learn more about workos\b',
    r'workos\.com',
    r'rogo\.ai/invest',
    r'ridgeline\.ai',
    r'\bthe podcast consultant\b',
    r'^find a16z on x\b',
    r'^find a16z on linkedin\b',
    r'^listen to the a16z (?:show|podcast) on apple podcasts\b',
    r'^follow our host:',
    r'^only bloomberg\.com subscribers\b',
    r'^subscribe to the odd lots newsletter\b',
    r'^join the conversation:\s*discord\.gg/oddlots\b',
    r'omnystudio\.com/listener',
    r'^subscribe to the verge to access the ad-free version of decoder\b',
    r'^decoder is a production of the verge\b',
    r'^subscribe today at nytimes\.com/podcasts\b',
    r'^this is a public episode\b.*www\.volts\.wtf/subscribe',
    r'^sponsor of chat with traders podcast:',
    r'^trade the pool:',
    r'^trading disclaimer:',
    r'trading in the financial markets involves a risk of loss',
    r'do not constitute trading or investment recommendations or advice',
)

_SPONSOR_BLOCK_LINE_PATTERNS = (
    r'\bthis episode is sponsored by\b',
    r'\bthis episode is brought to you by\b',
    r'\bthis video is sponsored by\b',
    r'\bbrought to you by\b',
    r'\bsponsored by\b',
    r'\bthanks to our sponsor\b',
    r'\bthanks to our sponsors\b',
    r'\bour sponsor\b',
    r'\bour sponsors\b',
    r'\buse code\b',
    r'\bpromo code\b',
    r'\bcoupon code\b',
    r'\badvertising partner\b',
    r'感谢.*对本期节目的赞助播出',
)

_SPONSOR_SECTION_HEADER_PATTERNS = (
    r'^sponsors?:?$',
    r'^our sponsors?:?$',
)

_SPONSOR_CONTINUATION_PATTERNS = (
    r'^learn more\b',
    r'^learn more at\b',
    r'^visit\b',
    r'^sign up\b',
    r'^check out\b',
    r'^get started\b',
    r'^use code\b',
    r'^promo code\b',
    r'^go to\b',
    r'^subscribe\b',
    r'^关于这款\b',
    r'^如果你对.*感兴趣',
)

_SPONSOR_CONTINUATION_STOP_PATTERNS = (
    r'^outline:?$',
    r'^timestamps?:?$',
    r'^chapters?:?$',
    r'^resources?:?$',
    r'^links(?:\s*\+\s*resources)?:?$',
    r'^read more:?$',
    r'^本期内容相关资料',
    r'^📁\s*本期内容相关资料',
)

_DISPLAY_FILTER_KEPT_CATEGORIES = {"body", "resources", "chapters"}
_DISPLAY_FILTER_REMOVED_CATEGORIES = {"credits", "cta", "sponsor", "privacy"}
_DISPLAY_SECTION_HEADER_PATTERNS = {
    "resources": (
        r'^(?:resources?|links(?:\s*\+\s*resources)?|additional reading|related reading|read more)\s*[:：]?\s*$',
        r'^(?:本期内容相关资料|相关资料|延伸阅读|资料链接)[:：]?$',
    ),
    "chapters": (
        r'^(?:chapters?|timestamps?|timecodes?|outline)\s*[:：]?\s*$',
        r'^(?:章节|时间轴|时间戳)[:：]?$',
    ),
    "credits": (
        r'^(?:credits?|production credits?):?$',
        r'^(?:制作团队|制作人员|制作名单)[:：]?$',
    ),
    "cta": (
        r'^(?:stay updated|follow us|contact us|subscribe|learn more):?$',
        r'^(?:关注我们|联系我们|订阅|了解更多)[:：]?$',
    ),
    "sponsor": (
        r'^(?:sponsors?|our sponsors?):?$',
        r'^(?:赞助|本期赞助)[:：]?$',
    ),
}

_DISPLAY_PRIVACY_BLOCK_PATTERNS = (
    r'\bprivacy policy\b',
    r'\bcalifornia privacy notice\b',
    r'\bprivacy information\b',
    r'learn more about your ad choices',
    r'podcastchoices\.com/adchoices',
    r'hosted by simplecast',
    r'\badswizz\b',
    r'a16z\.com/disclosures',
)

_DISPLAY_SPONSOR_BLOCK_PATTERNS = (
    r'\bthis (?:episode|video) is sponsored by\b',
    r'\b(?:this episode is )?brought to you by\b',
    r'\bthanks to our sponsors?\b',
    r'\bto support this podcast\b.*\bsponsors?\b',
    r'\bpromo code\b',
    r'\buse code\b',
    r'\bdraftkings\b',
    r'\bbetterhelp\b',
    r'\bget watch party snacks and groceries on uber eats\b',
    r'\bonx offroad:\s*try onx offroad\b',
    r'\btry alex\b.*\btryalex\.',
    r'感谢.*对本期节目的赞助播出',
)

_DISPLAY_CTA_BLOCK_PATTERNS = (
    r'^live long and prosper[.!]?$',
    r'\bwe want to hear from you\b.*\bemail us\b',
    r'\bfind [^\n.]+ on youtube and tiktok\b',
    r'\bget full access to .*?(?:subscribe|subscription)',
    r'\bmaking sense podcast logo\b.*\bsubscribe\b',
    r'\bsubscribe today at\b',
    r'\bsubscribe to the mailing list\b',
    r'\baccess transcript with premium membership\b',
    r'\bfollow ted on\b',
    r'\bonly bloomberg\.com subscribers\b',
    r'\bsubscribe to the odd lots newsletter\b',
    r'\bsubscribe to the verge\b',
    r'欢迎关注.*公众号',
    r'加入知识星球',
)

_DISPLAY_CREDITS_BLOCK_PATTERNS = (
    r'^credits?:',
    r'\bproduced (?:and edited )?by\b.*\bedited by\b',
    r'\boriginal music (?:and engineering )?by\b',
    r'\bthe .* music is by\b',
    r'\bediting and post-production work .* provided by\b',
)


def clean_show_notes_display_text(text: object) -> str:
    """Clean show-notes text for display without summarizing or truncating it."""
    if text is None or isinstance(text, (dict, list, tuple, set)):
        return ""
    value = html.unescape(str(text))
    if not value.strip():
        return ""

    value = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', '', value)
    value = re.sub(r'(?i)<\s*br\s*/?\s*>', '\n', value)
    value = re.sub(r'(?i)</\s*(p|div|li|h[1-6]|blockquote)\s*>', '\n', value)
    value = re.sub(r'(?i)<\s*(p|div|li|h[1-6]|blockquote)[^>]*>', '', value)
    value = re.sub(r'<[^>]*>', '', value)
    value = value.replace('<', '').replace('>', '')
    value = re.sub(r'[\xa0\u1680\u2000-\u200a\u202f\u205f\u3000\ufeff\r]+', ' ', value)
    value = re.sub(r'[ \t]{2,}', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()


def get_show_notes_display_snapshot(ep: object) -> dict | None:
    """Return a validated persisted display snapshot for renderer reuse."""
    if not isinstance(ep, dict):
        return None
    snapshot = ep.get("show_notes_display_snapshot")
    if not isinstance(snapshot, dict):
        return None
    if snapshot.get("version") != SHOW_NOTES_DISPLAY_SNAPSHOT_VERSION:
        return None
    heading = snapshot.get("heading")
    sections = snapshot.get("sections")
    if heading not in {"full", "translated"} or not isinstance(sections, list):
        return None
    cleaned_sections = [section for section in sections if isinstance(section, str) and section.strip()]
    if not cleaned_sections:
        return None
    return {
        "version": SHOW_NOTES_DISPLAY_SNAPSHOT_VERSION,
        "heading": heading,
        "sections": cleaned_sections,
    }


def _show_notes_language_counts(text: object) -> tuple[int, int, int]:
    cleaned = clean_show_notes_display_text(text)
    if not cleaned:
        return 0, 0, 0

    cleaned = re.sub(r'https?://\S+|www\.\S+', ' ', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\b[\w.+-]+@[\w.-]+\.\w+\b', ' ', cleaned)
    cjk_count = len(re.findall(r'[\u4e00-\u9fff]', cleaned))
    latin_count = len(re.findall(r'[A-Za-z]', cleaned))
    effective_count = cjk_count + latin_count
    return cjk_count, latin_count, effective_count


def analyze_translated_show_notes_residual_english(text: object) -> dict:
    """Return conservative evidence that a Chinese translation retained English prose."""
    if not isinstance(text, str):
        cleaned = ""
    else:
        cleaned = clean_show_notes_display_text(text)
    cleaned = re.split(
        r'(?m)^延伸阅读（原文）：\s*$',
        cleaned,
        maxsplit=1,
    )[0].rstrip()
    if not cleaned:
        return {
            "latin_word_count": 0,
            "latin_character_count": 0,
            "cjk_character_count": 0,
            "residual_latin_ratio": 0.0,
            "suspected_incomplete_translation": False,
        }

    prose = re.sub(r'https?://\S+|www\.\S+', ' ', cleaned, flags=re.IGNORECASE)
    prose = re.sub(r'\(?\b\d{1,2}:\d{2}(?::\d{2})?\b\)?', ' ', prose)
    latin_words = re.findall(r"\b[A-Za-z][A-Za-z'-]*\b", prose)
    latin_character_count = sum(len(word.replace("'", "")) for word in latin_words)
    cjk_character_count = len(re.findall(r'[\u4e00-\u9fff]', prose))
    signal_count = latin_character_count + cjk_character_count
    residual_latin_ratio = latin_character_count / signal_count if signal_count else 0.0
    suspected = len(latin_words) >= 20 and residual_latin_ratio >= 0.55
    return {
        "latin_word_count": len(latin_words),
        "latin_character_count": latin_character_count,
        "cjk_character_count": cjk_character_count,
        "residual_latin_ratio": round(residual_latin_ratio, 4),
        "suspected_incomplete_translation": suspected,
    }


def diagnose_show_notes_source_completeness(
    ep: object,
    *,
    source_text: object = None,
    filtered_text: object = None,
) -> dict:
    """Return evidence of likely upstream Show Notes truncation without repairing text."""
    episode = ep if isinstance(ep, dict) else {}
    source = (
        clean_show_notes_display_text(source_text)
        if source_text is not None
        else get_episode_show_notes_text(episode)
    )
    filtered = (
        clean_show_notes_display_text(filtered_text)
        if filtered_text is not None
        else filter_show_notes_boilerplate_for_display(source)
    )

    def non_negative_int(value: object) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    candidate_lengths = {
        "description": non_negative_int(episode.get("rss_description_len")),
        "content_encoded": non_negative_int(episode.get("rss_content_encoded_len")),
        "itunes_summary": non_negative_int(episode.get("rss_itunes_summary_len")),
    }
    terminal_punctuation_present = bool(
        re.search(r'[.!?。！？…][\]\)）}\"\'’”]*$', source)
    )
    reasons = []
    if bool(episode.get("show_notes_truncated")):
        reasons.append("upstream_truncated")
    if len(source) >= 500 and not terminal_punctuation_present:
        reasons.append("long_unterminated_source")

    return {
        "source_field": str(episode.get("show_notes_source") or "unknown"),
        "source_length": len(source),
        "filtered_length": len(filtered),
        "reported_source_length": non_negative_int(episode.get("show_notes_text_len")),
        "upstream_truncated": bool(episode.get("show_notes_truncated")),
        "candidate_lengths": candidate_lengths,
        "terminal_punctuation_present": terminal_punctuation_present,
        "suspected_source_truncation": bool(reasons),
        "reasons": reasons,
    }


def detect_show_notes_display_language(text: object) -> str:
    """Detect show-notes display language without external dependencies."""
    cjk_count, latin_count, effective_count = _show_notes_language_counts(text)
    if effective_count <= 0:
        return "unknown"

    cjk_ratio = cjk_count / effective_count
    latin_ratio = latin_count / effective_count

    if cjk_count >= 20 and cjk_ratio >= 0.25:
        return "zh"
    if latin_count >= 80 and latin_ratio >= 0.65:
        return "en"
    if effective_count < MIN_LANGUAGE_SIGNAL_CHARS:
        return "unknown"
    if cjk_count >= 10 and latin_count >= 40:
        return "mixed"
    return "unknown"


def should_translate_show_notes_for_display(text: object, source_language: str = "") -> bool:
    """Return whether show notes should be translated for display."""
    source = (source_language or "").strip().lower()
    if source in {"zh", "cn", "chinese", "中文"}:
        return False

    detected = detect_show_notes_display_language(text)
    if source in {"en", "eng", "english"}:
        if detected == "en":
            return True
        if detected in {"zh", "mixed"}:
            return False
        cjk_count, latin_count, effective_count = _show_notes_language_counts(text)
        latin_ratio = latin_count / effective_count if effective_count else 0.0
        return latin_count >= 40 and latin_ratio >= 0.75 and cjk_count < 10
    return detected == "en"


def _display_section_category(first_line: str) -> str:
    normalized = first_line.strip().lower()
    for category, patterns in _DISPLAY_SECTION_HEADER_PATTERNS.items():
        if any(re.search(pattern, normalized) for pattern in patterns):
            return category
    return ""


def _classify_removed_display_block(block: str) -> tuple[str, str]:
    normalized = block.strip().lower()
    lines = [line.strip().lower() for line in block.splitlines() if line.strip()]
    candidate = normalized if len(lines) <= 1 else lines[0]

    def starts_as_boilerplate(patterns: tuple[str, ...]) -> bool:
        for pattern in patterns:
            match = re.search(pattern, candidate, flags=re.S)
            if match and match.start() <= 120:
                return True
        return False

    if starts_as_boilerplate(_DISPLAY_PRIVACY_BLOCK_PATTERNS):
        return "privacy", "privacy_marker"
    if starts_as_boilerplate(_DISPLAY_SPONSOR_BLOCK_PATTERNS):
        return "sponsor", "sponsor_marker"
    if starts_as_boilerplate(_DISPLAY_CTA_BLOCK_PATTERNS):
        return "cta", "cta_marker"
    if starts_as_boilerplate(_DISPLAY_CREDITS_BLOCK_PATTERNS):
        return "credits", "credits_marker"
    return "", ""


def _is_removed_section_continuation(block: str, category: str) -> bool:
    normalized = block.strip().lower()
    direct_category, _ = _classify_removed_display_block(block)
    if direct_category == category:
        return True
    if category == "sponsor":
        return bool(
            re.match(r'^(?:[*\-•]\s+|\d+[.)]\s+)', block.strip())
            or re.match(r'^[A-Z][A-Za-z0-9&.+\- ]{1,40}:\s+\S', block.strip())
            or re.search(r'^(?:learn more|visit|sign up|check out|get started|use code|promo code|go to)\b', normalized)
        )
    if category == "cta":
        return bool(re.search(r'^(?:find|follow|listen|subscribe|email|join|download|访问|关注|订阅|加入)\b', normalized))
    if category == "credits":
        return bool(re.search(r'\b(?:produced|edited|music|engineering|post-production|executive editor)\b', normalized))
    if category == "privacy":
        return direct_category == "privacy"
    return False


def build_show_notes_display_filter_result(text: object) -> dict:
    """Classify logical display blocks and remove high-confidence non-content blocks."""
    cleaned = clean_show_notes_display_text(text)
    if not cleaned:
        return {
            "text": "",
            "kept_category_counts": {},
            "removed_category_counts": {},
            "removed_reasons": [],
            "diagnostic_error": "",
        }

    blocks = [block.strip() for block in re.split(r'\n\s*\n', cleaned) if block.strip()]
    kept_blocks = []
    kept_counts = {}
    removed_counts = {}
    removed_reasons = []
    active_section = ""

    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        first_line = lines[0] if lines else ""
        header_category = _display_section_category(first_line)
        direct_category, direct_reason = _classify_removed_display_block(block)

        if header_category:
            active_section = header_category
            category = header_category
            reason = f"{header_category}_section"
        elif direct_category:
            category = direct_category
            reason = direct_reason
        elif active_section in _DISPLAY_FILTER_REMOVED_CATEGORIES:
            if _is_removed_section_continuation(block, active_section):
                category = active_section
                reason = f"{active_section}_section"
            else:
                active_section = ""
                category = "body"
                reason = ""
        elif active_section:
            category = active_section
            reason = f"{active_section}_section"
        else:
            category = "body"
            reason = ""

        if category in _DISPLAY_FILTER_REMOVED_CATEGORIES:
            removed_counts[category] = removed_counts.get(category, 0) + 1
            if reason and reason not in removed_reasons:
                removed_reasons.append(reason)
            continue

        if category not in _DISPLAY_FILTER_KEPT_CATEGORIES:
            category = "body"
        kept_counts[category] = kept_counts.get(category, 0) + 1
        kept_blocks.append(block)

    classified_text = "\n\n".join(kept_blocks)
    legacy_filtered = _filter_show_notes_boilerplate_legacy(classified_text)
    if legacy_filtered != classified_text:
        removed_counts["sponsor"] = removed_counts.get("sponsor", 0) + 1
        if "legacy_line_filter" not in removed_reasons:
            removed_reasons.append("legacy_line_filter")

    return {
        "text": legacy_filtered,
        "kept_category_counts": kept_counts,
        "removed_category_counts": removed_counts,
        "removed_reasons": removed_reasons,
        "diagnostic_error": "",
    }


def filter_show_notes_boilerplate_for_display(text: str) -> str:
    """Return display text from the structured high-confidence block filter."""
    return build_show_notes_display_filter_result(text)["text"]


def _filter_show_notes_boilerplate_legacy(text: str) -> str:
    """Apply the established line-level filter as a compatibility fallback."""
    cleaned = clean_show_notes_display_text(text)
    if not cleaned:
        return ""

    cleaned = re.sub(
        r'(?is)please note that the content here is for informational purposes only.*?'
        r'(?:for more details please see\s+)?a16z\.com/disclosures\.?',
        '',
        cleaned,
    )
    cleaned = re.sub(
        r'(?is)a16z and its affiliates may maintain investments.*?a16z\.com/disclosures\.?',
        '',
        cleaned,
    )
    cleaned = re.sub(
        r'(?is)learn more about your ad choices\.?\s*'
        r'(?:visit\s+)?podcastchoices\.com/adchoices\.?',
        '',
        cleaned,
    )

    kept_lines = []
    in_sponsor_section = False
    sponsor_continuation_lines = 0
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            if in_sponsor_section:
                continue
            if kept_lines and kept_lines[-1] != "":
                kept_lines.append("")
            continue
        normalized = stripped.lower()
        if any(re.search(pattern, normalized) for pattern in _SPONSOR_CONTINUATION_STOP_PATTERNS):
            in_sponsor_section = False
            sponsor_continuation_lines = 0
            kept_lines.append(stripped)
            continue
        if any(re.search(pattern, normalized) for pattern in _BOILERPLATE_LINE_PATTERNS):
            continue
        if any(re.search(pattern, normalized) for pattern in _SPONSOR_SECTION_HEADER_PATTERNS):
            in_sponsor_section = True
            sponsor_continuation_lines = 0
            continue
        if in_sponsor_section:
            if re.match(r'^(?:[*\-•]\s+|\d+[.)]\s+)', stripped):
                continue
            in_sponsor_section = False
        if any(re.search(pattern, normalized) for pattern in _SPONSOR_BLOCK_LINE_PATTERNS):
            sponsor_continuation_lines = 2 if re.search(r'感谢.*对本期节目的赞助播出', normalized) else 3
            continue
        if sponsor_continuation_lines > 0 and any(
            re.search(pattern, normalized) for pattern in _SPONSOR_CONTINUATION_PATTERNS
        ):
            sponsor_continuation_lines -= 1
            continue
        sponsor_continuation_lines = 0
        kept_lines.append(stripped)

    filtered = "\n".join(kept_lines)
    filtered = re.sub(r'[ \t]{2,}', ' ', filtered)
    filtered = re.sub(r' *\n *', '\n', filtered)
    filtered = re.sub(r'\n{3,}', '\n\n', filtered)
    return filtered.strip()


def get_episode_show_notes_text(ep: dict) -> str:
    """Return the best available episode introduction text for display."""
    if not isinstance(ep, dict):
        return ""
    for key in ("show_notes_text", "show_notes", "description"):
        cleaned = clean_show_notes_display_text(ep.get(key))
        if cleaned:
            return cleaned

    summary = ep.get("summary_3_sentences_cn")
    if isinstance(summary, list):
        cleaned_items = [clean_show_notes_display_text(item) for item in summary]
        cleaned = "\n".join(item for item in cleaned_items if item)
        if cleaned:
            return cleaned
    else:
        cleaned = clean_show_notes_display_text(summary)
        if cleaned:
            return cleaned

    return clean_show_notes_display_text(ep.get("one_line_summary_cn"))


def split_show_notes_text(text: str, max_chars: int = DEFAULT_SHOW_NOTES_CHUNK_CHARS) -> list[str]:
    """Split display text into chunks without dropping, summarizing, or truncating."""
    cleaned = clean_show_notes_display_text(text)
    if not cleaned:
        return []
    if max_chars <= 0:
        return [cleaned]

    def split_long_piece(piece: str) -> list[str]:
        piece = piece.strip()
        if not piece:
            return []
        if len(piece) <= max_chars:
            return [piece]
        if not re.search(r'[\s。！？.!?]', piece):
            return [piece]

        sentence_parts = re.findall(r'.+?[。！？.!?]+|.+$', piece, flags=re.S)
        if any(len(sentence) > max_chars for sentence in sentence_parts if sentence.strip()):
            return [piece[start:start + max_chars] for start in range(0, len(piece), max_chars)]

        pieces = []
        current = ""
        for sentence in sentence_parts:
            if not sentence.strip():
                continue
            candidate = sentence if not current else current + sentence
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    pieces.append(current)
                current = sentence
        if current:
            pieces.append(current)
        return pieces

    paragraphs = [p.strip() for p in re.split(r'\n{2,}', cleaned) if p.strip()]
    chunks: list[str] = []
    for paragraph in paragraphs:
        chunks.extend(split_long_piece(paragraph))

    return [chunk for chunk in chunks if chunk]


def build_show_notes_sections(
    ep: dict,
    max_chars: int = DEFAULT_SHOW_NOTES_CHUNK_CHARS,
    *,
    translation_enabled: bool = False,
    translation_options: dict | None = None,
) -> list[str]:
    """Return pure text blocks for an episode's show notes display section."""
    result = build_show_notes_display_result(
        ep,
        max_chars=max_chars,
        translation_enabled=translation_enabled,
        translation_options=translation_options,
    )
    if result["heading"] == "translated":
        return [SHOW_NOTES_TRANSLATED_HEADING] + result["sections"]
    return result["sections"]


def build_show_notes_display_result(
    ep: dict,
    max_chars: int = DEFAULT_SHOW_NOTES_CHUNK_CHARS,
    *,
    translation_enabled: bool = False,
    translation_options: dict | None = None,
) -> dict:
    """Return display sections plus non-content translation and source diagnostics."""
    episode = ep if isinstance(ep, dict) else {}
    source_text = get_episode_show_notes_text(episode)
    try:
        filter_result = build_show_notes_display_filter_result(source_text)
        display_text = filter_result["text"]
        display_filter = {
            key: filter_result[key]
            for key in (
                "kept_category_counts",
                "removed_category_counts",
                "removed_reasons",
                "diagnostic_error",
            )
        }
    except Exception as exc:
        display_text = clean_show_notes_display_text(source_text)
        display_filter = {
            "kept_category_counts": {},
            "removed_category_counts": {},
            "removed_reasons": [],
            "diagnostic_error": f"{type(exc).__name__}: {exc}"[:500],
        }
    fallback_sections = split_show_notes_text(display_text, max_chars=max_chars)
    if not fallback_sections:
        fallback_sections = [SHOW_NOTES_PLACEHOLDER]

    try:
        source_completeness = diagnose_show_notes_source_completeness(
            episode,
            source_text=source_text,
            filtered_text=display_text,
        )
        source_completeness["diagnostic_error"] = ""
    except Exception as exc:
        source_completeness = {
            "source_field": str(episode.get("show_notes_source") or "unknown"),
            "source_length": len(source_text),
            "filtered_length": len(display_text),
            "reported_source_length": 0,
            "upstream_truncated": bool(episode.get("show_notes_truncated")),
            "candidate_lengths": {},
            "terminal_punctuation_present": False,
            "suspected_source_truncation": False,
            "reasons": [],
            "diagnostic_error": f"{type(exc).__name__}: {exc}"[:500],
        }

    options = dict(translation_options or {})
    source_language = (
        options.get("source_language")
        or episode.get("language")
        or episode.get("source_language")
        or ""
    )
    detected_language = detect_show_notes_display_language(display_text)
    eligible = should_translate_show_notes_for_display(
        display_text,
        source_language=source_language,
    )
    if not translation_enabled:
        return {
            "sections": fallback_sections,
            "heading": "full",
            "translation": _show_notes_translation_metadata(
                {
                    "status": "disabled",
                    "source_language": detected_language,
                    "target_language": "zh",
                    "should_translate": eligible,
                    "cache_hit": False,
                    "cache_key": "",
                    "source_hash": "",
                    "chunk_count": 0,
                    "translated_chunk_count": 0,
                    "model": str(options.get("model_name") or ""),
                    "errors": [],
                },
                translated_text="",
            ),
            "source_completeness": source_completeness,
            "display_filter": display_filter,
        }

    kwargs = {
        "podcast_id": options.get("podcast_id")
        or episode.get("podcast_id")
        or episode.get("podcast")
        or episode.get("podcast_name")
        or "",
        "episode_id": options.get("episode_id") or episode.get("episode_id") or episode.get("guid") or episode.get("id") or "",
        "episode_url": options.get("episode_url") or episode.get("url") or episode.get("episode_url") or episode.get("link") or "",
        "show_notes_text": display_text,
        "source_language": source_language,
        "cache_enabled": options.get("cache_enabled", True),
        "max_chunk_chars": options.get("max_chunk_chars", 1800),
    }
    for key in (
        "cache_root",
        "translation_version",
        "model_name",
        "translate_chunk",
        "validate_translation_completeness",
        "max_translation_attempts",
    ):
        if key in options:
            kwargs[key] = options[key]
    try:
        from show_notes_translation_orchestrator import translate_show_notes_for_display
        translation_result = translate_show_notes_for_display(**kwargs)
    except Exception as exc:
        translation_result = {
            "status": "failed",
            "source_language": detected_language,
            "target_language": "zh",
            "should_translate": eligible,
            "cache_hit": False,
            "cache_key": "",
            "source_hash": "",
            "chunk_count": 0,
            "translated_chunk_count": 0,
            "model": str(options.get("model_name") or ""),
            "errors": [{
                "stage": "display_orchestration_failed",
                "type": type(exc).__name__,
                "message": str(exc)[:500],
            }],
            "translated_text": "",
        }

    translated_text = clean_show_notes_display_text(translation_result.get("translated_text"))
    translation_metadata = _show_notes_translation_metadata(
        translation_result,
        translated_text=translated_text,
    )
    displayable_translation_statuses = {
        "translated",
        "cache_hit",
        "partial_translated",
        "partial_cache_hit",
    }
    if (
        translation_result.get("status") in displayable_translation_statuses
        and translated_text
    ):
        translated_sections = split_show_notes_text(translated_text, max_chars=max_chars)
        if translated_sections:
            return {
                "sections": translated_sections,
                "heading": "translated",
                "translation": translation_metadata,
                "source_completeness": source_completeness,
                "display_filter": display_filter,
            }

    return {
        "sections": fallback_sections,
        "heading": "full",
        "translation": translation_metadata,
        "source_completeness": source_completeness,
        "display_filter": display_filter,
    }


def _show_notes_translation_metadata(result: object, *, translated_text: str) -> dict:
    value = result if isinstance(result, dict) else {}
    return {
        "status": str(value.get("status") or "failed"),
        "source_language": str(value.get("source_language") or "unknown"),
        "target_language": str(value.get("target_language") or "zh"),
        "should_translate": bool(value.get("should_translate")),
        "cache_hit": bool(value.get("cache_hit")),
        "cache_key": str(value.get("cache_key") or ""),
        "source_hash": str(value.get("source_hash") or ""),
        "chunk_count": int(value.get("chunk_count") or 0),
        "translated_chunk_count": int(value.get("translated_chunk_count") or 0),
        "model": str(value.get("model") or ""),
        "errors": list(value.get("errors") or []),
        "localized_fallback_chunk_indices": list(
            value.get("localized_fallback_chunk_indices") or []
        ),
        "residual_english": analyze_translated_show_notes_residual_english(translated_text),
    }
