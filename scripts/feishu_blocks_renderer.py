#!/usr/bin/env python3
"""
Pure Feishu blocks renderer helpers.

This module intentionally has no Feishu API calls, latest result reads/writes,
delivery log writes, network calls, or filesystem side effects.
"""

from __future__ import annotations

import html
import re

from episode_duration import format_episode_duration
from episode_show_notes_renderer import (
    SHOW_NOTES_TRANSLATED_HEADING,
    build_show_notes_display_result,
    get_show_notes_display_snapshot,
)


CONFIRMED_GUEST_FALLBACK = "已确认本期嘉宾，但节目元数据未提供足够背景信息，暂不补充背景。"
FALLBACK_PATTERNS = {
    CONFIRMED_GUEST_FALLBACK,
    "未找到足够可靠的公开资料，暂不补充背景。",
    "未能从节目元数据中确认本期嘉宾，暂不补充背景。",
}
NOISE_GUEST_TITLES = {
    "author", "writer", "host", "podcaster", "creator",
    "作家", "作者", "主持人", "主播", "创作者",
    "founder", "cofounder",
}
GUEST_TOPIC_PHRASES = {
    "reshaping venture capital", "venture capital", "private markets",
    "software repricing", "capital allocation", "capital allocation strategy",
    "investment strategy", "market commentary", "fund performance",
}


def text_run(text: str, style: dict = None) -> dict:
    r = {"content": text}
    if style:
        r["text_element_style"] = style
    return {"type": "text_run", "text_run": r}


def h1(text: str) -> dict:
    return {"block_type": 3, "heading1": {"elements": [text_run(text)], "property": {}}}


def h2(text: str) -> dict:
    return {"block_type": 4, "heading2": {"elements": [text_run(text)], "property": {}}}


def h3(text: str) -> dict:
    return {"block_type": 5, "heading3": {"elements": [text_run(text)], "property": {}}}


def para(text: str, bold: bool = False) -> dict:
    style = {"bold": True} if bold else {}
    return {"block_type": 2, "text": {"elements": [text_run(text, style)], "property": {}}}


def bullet(text: str) -> dict:
    return {"block_type": 12, "bullet": {"elements": [text_run(text)], "property": {"indent_level": 1}}}


def divider() -> dict:
    return None


def clean_display_text(text) -> str:
    """Clean display text using the current delivery renderer behavior."""
    if not text:
        return ""
    text = html.unescape(str(text))
    text = text.translate(str.maketrans('', '', '"\''))
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()


def is_fallback_background(text: str) -> bool:
    if not text:
        return True
    if text in FALLBACK_PATTERNS:
        return True
    if CONFIRMED_GUEST_FALLBACK in text and ("；" in text or ";" in text):
        parts = re.split(r'[；;]', text)
        if all(is_fallback_background(p.strip()) for p in parts if p.strip()):
            return True
    return False


def is_guest_source_noise(source: dict) -> bool:
    snippet = source.get("snippet", "")
    if not snippet:
        return True
    snippet_lower = snippet.lower()
    if any(tp in snippet_lower for tp in GUEST_TOPIC_PHRASES):
        return True
    clean = snippet.replace("职务/头衔：", "").replace("机构/公司：", "").strip().lower()
    parts = [p.strip() for p in re.split(r'[/,;|]', clean) if p.strip()]
    if not parts:
        return True
    if all(p in NOISE_GUEST_TITLES for p in parts):
        return True
    meaningful = [p for p in parts if p not in NOISE_GUEST_TITLES]
    if len(meaningful) == 0:
        return True
    return False


def _format_pub_date(ep: dict) -> str:
    pub_dt = ep.get('pub_datetime', ep.get('publish_at', '未知'))
    if pub_dt and pub_dt != '未知':
        try:
            from email.utils import parsedate_to_datetime
            from zoneinfo import ZoneInfo
            dt = parsedate_to_datetime(pub_dt).astimezone(ZoneInfo("Asia/Shanghai"))
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    return pub_dt


def _append_show_notes_blocks(
    blocks: list[dict],
    ep: dict,
    *,
    show_notes_translation_enabled: bool = False,
    show_notes_translation_options: dict | None = None,
) -> None:
    display_result = get_show_notes_display_snapshot(ep)
    if display_result is None:
        display_result = build_show_notes_display_result(
            ep,
            translation_enabled=show_notes_translation_enabled,
            translation_options=show_notes_translation_options,
        )
    sections = display_result["sections"]
    if display_result["heading"] == "translated":
        blocks.append(h3(SHOW_NOTES_TRANSLATED_HEADING))
        content_sections = sections
    else:
        blocks.append(h3("节目介绍 / Show Notes（完整）"))
        content_sections = sections
    for section in content_sections:
        blocks.append(para(section))


def build_episode_blocks(
    ep: dict,
    decision_label: str,
    *,
    show_notes_translation_enabled: bool = False,
    show_notes_translation_options: dict | None = None,
) -> list[dict]:
    """Build Feishu blocks for one Full or Preview episode."""
    dur_str = format_episode_duration(ep)

    dims = []
    for key, label in [
        ("topic_relevance", "相关度"),
        ("information_density", "信息密度"),
        ("novelty", "新鲜度"),
        ("actionability", "行动价值"),
        ("strategic_value", "战略价值"),
        ("transcription_value", "转写价值"),
    ]:
        val = ep.get(key, 0)
        if val is not None:
            dims.append(f"{label} {val}")

    final_score = ep.get('final_score', ep.get('score', 0))
    uncertainty_zh = ep.get('uncertainty_zh', '')
    priority = ep.get('priority', 'unknown')
    full_suggestion = ep.get('full_suggestion', 'unknown')
    sel_mode = ep.get('selection_policy_mode', ep.get('selection_policy', {}).get('mode', ''))
    pub_dt = _format_pub_date(ep)

    blks = []
    score_display = f"{final_score:.1f}" if isinstance(final_score, float) else final_score
    blks.append(para(
        f"{ep['podcast_name']} | {clean_display_text(ep.get('episode_title', ''))} | {pub_dt} | {dur_str} | "
        f"{score_display}分 | {decision_label}",
        bold=True
    ))

    if priority != 'unknown':
        priority_label = {"high": "🔴 高优先级", "medium": "🟡 中优先级", "low": "⚪ 低优先级"}.get(priority, priority)
        full_label = {"yes": "✅ 建议 full", "maybe": "🤔 可考虑 full", "no": "❌ 暂不需 full"}.get(full_suggestion, full_suggestion)
        blks.append(para(f"本周关注优先级：{priority_label} | Full建议：{full_label}"))

    if sel_mode == 'all_preview':
        blks.append(para(f"处理状态：已进入 preview"))

    if dims:
        blks.append(para("评分维度：" + " | ".join(dims)))

    if uncertainty_zh:
        blks.append(para(f"不确定性：{uncertainty_zh}"))

    _append_show_notes_blocks(
        blks,
        ep,
        show_notes_translation_enabled=show_notes_translation_enabled,
        show_notes_translation_options=show_notes_translation_options,
    )
    blks.append(para(""))
    return blks


def build_skip_blocks(
    ep: dict,
    *,
    show_notes_translation_enabled: bool = False,
    show_notes_translation_options: dict | None = None,
) -> list[dict]:
    """Build Feishu blocks for one Skip episode."""
    reason_zh = (
        ep.get('decision_reason_zh')
        or ep.get('reason_zh')
        or ep.get('reason')
        or ''
    )
    uncertainty_zh = ep.get('uncertainty_zh', '')
    priority = ep.get('priority', 'unknown')
    full_suggestion = ep.get('full_suggestion', 'unknown')
    pub_dt = _format_pub_date(ep)
    dur_str = format_episode_duration(ep)
    score_display = f"{ep.get('final_score', ep.get('score', 0)):.1f}"

    blks = [
        para(f"{ep['podcast_name']} | {clean_display_text(ep.get('episode_title', ''))} | {pub_dt} | {dur_str} | {score_display}分 | skip"),
    ]
    if priority != 'unknown':
        priority_label = {"high": "🔴 高", "medium": "🟡 中", "low": "⚪ 低"}.get(priority, priority)
        blks.append(para(f"推荐优先级：{priority_label} | Full建议：{full_suggestion}"))
    if reason_zh:
        blks.append(para(f"跳过理由：{reason_zh}"))
    if uncertainty_zh:
        blks.append(para(f"注意：{uncertainty_zh}"))
    _append_show_notes_blocks(
        blks,
        ep,
        show_notes_translation_enabled=show_notes_translation_enabled,
        show_notes_translation_options=show_notes_translation_options,
    )
    blks.append(para(""))
    return blks


def build_summary_blocks(result_data: dict) -> list[dict]:
    week_id = result_data.get("week_id", "")
    window_start = result_data.get("window_start", "")
    window_end = result_data.get("window_end", "")
    full_count = len(result_data.get("full", []))
    preview_count = len(result_data.get("preview", []))
    skip_count = len(result_data.get("skip", []))
    errs = result_data.get("fetch_errors", [])
    return [
        h1(f"🎧 播客周报 {week_id}"),
        para(""),
        para(f"窗口：{window_start} → {window_end}", bold=True),
        para(f"扫描日期：{result_data.get('scan_date', '')}"),
        para(f"总计节目：{result_data.get('total_episodes', 0)}"),
        para(f"Full / Preview / Skip：{full_count} / {preview_count} / {skip_count}"),
        para(f"Fetch 错误：{len(errs)} 个" + (f" → {errs}" if errs else "")),
        divider(),
    ]


def build_feishu_blocks(
    result_data: dict,
    report_md: str = "",
    *,
    show_notes_translation_enabled: bool = False,
    show_notes_translation_options: dict | None = None,
) -> list[dict]:
    blocks = []
    blocks += build_summary_blocks(result_data)

    blocks.append(h2("✅ Full 推荐"))
    full_list = result_data.get("full", [])
    if not full_list:
        blocks.append(bullet("本周无 Full 推荐"))
    for ep in full_list:
        blocks += build_episode_blocks(
            ep,
            "Full",
            show_notes_translation_enabled=show_notes_translation_enabled,
            show_notes_translation_options=show_notes_translation_options,
        )

    blocks.append(h2("🔍 Preview 推荐"))
    preview_list = result_data.get("preview", [])
    if not preview_list:
        blocks.append(bullet("本周无 Preview 推荐"))
    for ep in preview_list:
        blocks += build_episode_blocks(
            ep,
            "Preview",
            show_notes_translation_enabled=show_notes_translation_enabled,
            show_notes_translation_options=show_notes_translation_options,
        )

    blocks.append(h2("⏭️ Skip"))
    skip_list = result_data.get("skip", [])
    if not skip_list:
        blocks.append(bullet("本周无 Skip"))
    for ep in skip_list:
        blocks += build_skip_blocks(
            ep,
            show_notes_translation_enabled=show_notes_translation_enabled,
            show_notes_translation_options=show_notes_translation_options,
        )

    blocks.append(divider())

    return [b for b in blocks if b is not None]
