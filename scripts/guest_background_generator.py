#!/usr/bin/env python3
from __future__ import annotations

"""
Guest background generation helpers.

Most helpers here are pure. run_background_model() is an isolated subprocess
adapter and is not wired into production callers yet.
"""

import os
import re
import subprocess

from guest_source_quality import rate_overall_source_quality
from show_notes_openclaw_translation_runner import extract_openclaw_translation_text


OPENCLAW_BACKGROUND_MODEL = "minimax-portal/MiniMax-M2.7"
BACKGROUND_MODEL_TIMEOUT_SECONDS = 60

CONFIRMED_GUEST_FALLBACK = "已确认本期嘉宾，但节目元数据未提供足够背景信息，暂不补充背景。"

TOPIC_PHRASES = {
    "reshaping venture capital", "venture capital", "private markets",
    "software repricing", "capital allocation", "capital allocation strategy",
    "investment strategy", "market commentary", "fund performance",
    "studying top fund", "top fund managers",
}

GENERIC_ORG_FRAGMENTS = {
    "capital", "fund", "group", "investment", "investments", "partners",
    "asset", "assets", "management", "company", "organization",
}


def is_safe_guest_org_candidate(value: object) -> bool:
    """Return whether an extracted organization is specific enough to display."""
    if not isinstance(value, str):
        return False
    cleaned = re.sub(r"\s+", " ", value).strip(" \t\r\n,.;:()[]{}")
    if len(cleaned) < 2 or len(cleaned) > 80:
        return False
    normalized = cleaned.lower()
    if normalized in GENERIC_ORG_FRAGMENTS:
        return False
    if re.match(r"^(?:and|or|but|of|with|for|from|in|on|at|the)\b", normalized):
        return False
    if any(phrase in normalized for phrase in TOPIC_PHRASES):
        return False
    if len(re.findall(r"[A-Za-z\u4e00-\u9fff]", cleaned)) < 2:
        return False
    return True


def parse_english_guest_role_list_from_show_notes(guest_name: str, show_notes: str) -> dict | None:
    """Parse narrow English show-notes role-list snippets for one known guest."""
    guest = (guest_name or "").strip()
    text = (show_notes or "").strip()
    if not guest or not text:
        return None

    escaped_guest = re.escape(guest)
    credential_pattern = re.compile(
        rf"\b{escaped_guest},\s*(?P<credential>Ph\.?D\.?|MD|MBA|CFA),\s+is\s+"
        r"(?P<title>.+?)\s+at\s+(?P<org>[^.,]+?)(?:,|\.|$)"
    )
    credential_match = credential_pattern.search(text)
    if credential_match:
        credential = credential_match.group("credential").replace(".", "")
        return {
            "guest_name": guest,
            "roles": [],
            "host_of": None,
            "org": re.sub(r"^the\s+", "", credential_match.group("org").strip(), flags=re.IGNORECASE),
            "title": credential_match.group("title").strip(),
            "credential": credential,
            "source": "show_notes_credentialed_title_org",
        }

    supported_title = (
        r"President\s+and\s+COO|Founder|CEO|CTO|CFO|COO|President|"
        r"associate\s+professor|professor|director|partner|general\s+partner"
    )
    title_org_patterns = (
        re.compile(
            rf"\b{escaped_guest}\s+is\s+(?:the|a|an)\s+"
            rf"(?P<title>{supported_title})\s+(?:of|at)\s+"
            r"(?P<org>[A-Za-z][A-Za-z0-9&'’ .-]{1,79}?)(?=,|\.|$)",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{escaped_guest},\s+(?:the|a|an)\s+"
            rf"(?P<title>{supported_title})\s+(?:of|at)\s+"
            r"(?P<org>[A-Za-z][A-Za-z0-9&'’ .-]{1,79}?)(?=,|\.|$)",
            re.IGNORECASE,
        ),
    )
    for pattern in title_org_patterns:
        match = pattern.search(text)
        if not match:
            continue
        org = match.group("org").strip()
        if is_safe_guest_org_candidate(org):
            return {
                "guest_name": guest,
                "roles": [],
                "host_of": None,
                "org": org,
                "title": match.group("title").strip(),
                "credential": None,
                "source": "show_notes_title_org",
            }

    role_parts: list[str] = []
    host_of = None
    host_match = re.search(
        r'host of (?:the\s+)?podcasts?\s+["“](?P<host>[^"”]+)["”]',
        text,
        flags=re.IGNORECASE,
    )
    if host_match:
        host_of = host_match.group("host").strip().rstrip(".,")

    guest_parts = guest.split()
    first_name = guest_parts[0] if len(guest_parts) >= 2 else ""
    last_name = guest_parts[-1] if len(guest_parts) >= 2 else ""
    name_alternatives = [escaped_guest]
    if first_name:
        name_alternatives.append(re.escape(first_name))
    if last_name and last_name != first_name:
        name_alternatives.append(re.escape(last_name))
    sentence_pattern = re.compile(
        rf"(?:\b(?:{'|'.join(name_alternatives)})|He|She)\s+is\s+(?:also\s+)?(?P<body>[^.]+)(?:\.|$)",
        re.IGNORECASE,
    )
    for match in sentence_pattern.finditer(text):
        body = match.group("body").strip()
        if match.group(0).lower().startswith(("he is", "she is")) and not role_parts:
            continue
        body = re.sub(r"\s+whose\b.*$", "", body, flags=re.IGNORECASE)
        body = re.sub(
            r'(?:,\s*)?(?:and\s+)?host of (?:the\s+)?podcasts?\s+["“].*$',
            "",
            body,
            flags=re.IGNORECASE,
        )
        body = re.sub(r'(?:,\s*)?(?:and\s+)?co-host\b.*$', "", body, flags=re.IGNORECASE)
        body = re.sub(r'(?:,\s*)?(?:and\s+)?author of\s+["“].*$', "", body, flags=re.IGNORECASE)

        body = re.sub(r"^(?:a|an|the)\s+", "", body, flags=re.IGNORECASE).strip()
        if not body:
            continue
        for part in re.split(r",\s*|\s+and\s+", body):
            role = re.sub(r"^(?:a|an|the)\s+", "", part.strip(), flags=re.IGNORECASE)
            role = re.sub(r"^and\s+", "", role, flags=re.IGNORECASE).strip()
            if role and role.lower() not in {"host of the podcast"}:
                role_parts.append(role)

    if not role_parts:
        return None

    roles = []
    for role in role_parts:
        if role not in roles:
            roles.append(role)
    return {
        "guest_name": guest,
        "roles": roles,
        "host_of": host_of,
        "org": None,
        "title": None,
        "credential": None,
        "source": "show_notes_is_a_role_list",
    }


ENGLISH_ROLE_ZH = {
    "comedian": "喜剧演员",
    "public speaker": "公共演说家",
    "stand-up comedian": "单口喜剧演员",
    "actor": "演员",
    "writer": "作家",
    "retired NFL quarterback": "退役 NFL 四分卫",
    "sports analyst": "体育评论员",
    "musician": "音乐人",
    "bowhunter": "弓猎者",
    "outdoorsman": "户外运动者",
    "endurance athlete": "耐力运动员",
    "author": "作者",
    "entrepreneur": "企业家",
    "commentator": "评论员",
    "founder": "创始人",
    "president": "总裁",
    "president and COO": "总裁兼 COO",
    "COO": "COO",
    "CEO": "CEO",
    "CTO": "CTO",
    "CFO": "CFO",
    "associate professor": "副教授",
    "professor": "教授",
    "director": "主任",
    "partner": "合伙人",
    "general partner": "普通合伙人",
    "trader": "交易员",
    "system designer": "系统设计师",
    "money manager": "资金管理人",
    "market strategist": "市场策略师",
    "Chief Scientist": "首席科学家",
}


def _translate_english_role(value: str) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    for role, translated in ENGLISH_ROLE_ZH.items():
        if role.casefold() == normalized.casefold():
            return translated
    return normalized


def build_background_zh_from_english_role_list(parsed: dict) -> str:
    """Build a narrow Chinese background sentence from parsed English roles."""
    guest_name = parsed.get("guest_name", "")
    title = parsed.get("title")
    org = parsed.get("org")
    if title and org:
        title_zh = _translate_english_role(title)
        return f"{guest_name} 是 {org} 的{title_zh}。"

    roles = parsed.get("roles") or []
    for role in roles:
        if role != "co-author of the new Market Wizards book" and role not in ENGLISH_ROLE_ZH:
            return ""
    translated_roles = [_translate_english_role(role) for role in roles]
    coauthor = None
    if translated_roles and translated_roles[0] == "co-author of the new Market Wizards book":
        coauthor = "新版 Market Wizards 的合著者"
        translated_roles = translated_roles[1:]

    def _join(items: list[str]) -> str:
        if len(items) <= 1:
            return "".join(items)
        return "、".join(items[:-1]) + "和" + items[-1]

    clauses = []
    if coauthor:
        clauses.append(f"{guest_name} 是{coauthor}")
        if translated_roles:
            clauses.append(f"也是一名{_join(translated_roles)}")
    elif translated_roles:
        clauses.append(f"{guest_name} 是{_join(translated_roles)}")

    host_of = parsed.get("host_of")
    if host_of:
        clauses.append(f"也是播客 {host_of} 的主持人")

    return "，".join(clauses) + "。" if clauses else ""


def build_background_prompt(guest_name: str, sources: list[dict]) -> str:
    """Build the current OpenClaw background prompt from search sources."""
    source_texts = []
    for i, s in enumerate(sources[:3]):
        snippet = s.get("snippet", "")
        title = s.get("title", "")
        url = s.get("url", "")
        quality = s.get("quality", "secondary")
        quality_tag = f"[{quality}]" if quality else ""
        if snippet:
            source_texts.append(f"[{i+1}{quality_tag}] {title}\n   摘要：{snippet[:200]}")
        else:
            source_texts.append(f"[{i+1}{quality_tag}] {title} ({url})" if url else f"[{i+1}{quality_tag}] {title}")

    search_context = "\n".join(source_texts)
    return f"""你是一个人物背景研究助手。请根据以下搜索结果，为嘉宾「{guest_name}」生成一段80-150字的中文背景介绍。

要求：
- 只基于搜索结果总结，不要编造信息
- 结构：（1）一句话身份；（2）与本期主题相关的经历/专长；（3）为什么他的背景有助于理解本期讨论
- 每位嘉宾背景单独成段，总字数不超过150字
- 禁止：夸大形容（"著名/顶级/权威"），职务写死但来源不新，私生活八卦

搜索结果：
{search_context}

背景介绍："""


def should_accept_model_background(text: str) -> bool:
    """Return whether model output matches the current acceptance rules."""
    model_output = (text or "").strip()
    if not model_output:
        return False
    if "信息不足" in model_output or "无法生成" in model_output:
        return False
    return len(model_output) >= 20


def run_background_model(
    prompt: str,
    model: str = OPENCLAW_BACKGROUND_MODEL,
    timeout: int = BACKGROUND_MODEL_TIMEOUT_SECONDS,
    env: dict | None = None,
) -> str | None:
    """Run the OpenClaw background model and return extracted stdout text.

    OpenClaw 2026.3.24 does not support top-level ``--model`` / ``eval`` /
    ``--prompt``. Model/account selection is managed by OpenClaw agent/profile
    configuration. The ``model`` parameter is retained for compatibility and
    metadata only.
    """
    base_env = os.environ if env is None else env
    run_env = dict(base_env)
    try:
        result = subprocess.run(
            [
                "openclaw",
                "agent",
                "--agent",
                "main",
                "--message",
                prompt,
                "--json",
                "--timeout",
                str(timeout),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )
    except Exception:
        return None

    if result.returncode != 0:
        return None
    output = (result.stdout or "").strip()
    if not output:
        return None
    try:
        return extract_openclaw_translation_text(output)
    except RuntimeError:
        return None


def generate_background_from_show_notes(
    guest_name: str,
    sources: list[dict],
    show_notes_info: dict | None = None,
):
    """
    Generate a guest background only from already-extracted show-notes evidence.

    Returns a result dict matching the show-notes branch of
    generate_background_via_model_phase2(), or None when there is not enough
    evidence and the caller should continue to the existing model path.
    """
    if not show_notes_info or not show_notes_info.get("patterns_found"):
        return None

    sq_override = "primary"
    note = "来自节目元数据（show_notes）"
    sn_sources = [{
        "title": "节目元数据（show_notes）",
        "url": "",
        "snippet": " | ".join(show_notes_info["patterns_found"]),
        "quality": "primary",
        "source_type": "episode_show_notes",
    }]

    if sources:
        all_sources = sn_sources + list(sources[:2])
        sq = rate_overall_source_quality(sources)
        if sq["quality"] in ("secondary", "primary"):
            sq_override = "secondary"
            note = "来自节目元数据 + 网络搜索"
    else:
        all_sources = sn_sources
        sq_override = "primary"
        note = "来自节目元数据（show_notes）"

    role_list = parse_english_guest_role_list_from_show_notes(
        guest_name,
        show_notes_info.get("text", ""),
    )
    if role_list:
        bg_text = build_background_zh_from_english_role_list(role_list)
        if bg_text:
            if len(bg_text) > 150:
                bg_text = bg_text[:147] + "..."
            return {
                "background_zh": bg_text,
                "sources": all_sources[:2],
                "confidence": "medium",
                "note": note,
                "source_quality": sq_override,
                "source_quality_summary": (
                    f"{sq_override} (episode_show_notes)"
                    if show_notes_info.get("source_type") == "episode_show_notes"
                    else sq_override
                ),
            }

    patterns = show_notes_info["patterns_found"]
    titles = [p.replace("职务/头衔：", "") for p in patterns if p.startswith("职务/头衔：")]
    orgs_raw = [p.replace("机构/公司：", "") for p in patterns if p.startswith("机构/公司：")]
    research = [p.replace("专长/研究：", "") for p in patterns if p.startswith("专长/研究：")]
    books = [p.replace("著作/案例：", "") for p in patterns if p.startswith("著作/案例：")]

    ep_title_for_filter = show_notes_info.get("text", "")[:300] if show_notes_info else ""
    books_clean = []
    for b in books:
        if ep_title_for_filter and b in ep_title_for_filter:
            continue
        series_patterns = ["创投观察", "创投笔记", "观察第", "集", "第2集", "系列节目"]
        if any(p in b for p in series_patterns):
            continue
        if b and len(b) >= 2:
            books_clean.append(b)
    books = books_clean

    def _is_better_org(a, b):
        kw = ["Andreessen", "Horowitz", "Capital", "Investment", "Ventures", "a16Z", "Hugging", "Fund", "Investment"]
        a_kw = any(k.lower() in a.lower() for k in kw)
        b_kw = any(k.lower() in b.lower() for k in kw)
        if a_kw != b_kw:
            return a_kw
        return len(a) > len(b)

    orgs = []
    for o in orgs_raw:
        if not is_safe_guest_org_candidate(o):
            continue
        replaced = False
        for i, existing in enumerate(orgs):
            if existing.lower() in o.lower() and len(existing) < len(o):
                if _is_better_org(o, existing):
                    orgs[i] = o
                    replaced = True
                    break
        if not replaced and not any(o.lower() in e.lower() or e.lower() in o.lower() for e in orgs):
            orgs.append(o)

    kw_list = ["Andreessen", "Horowitz", "Capital", "Ventures", "a16Z", "Hugging", "Investment", "Fund"]
    orgs = sorted(orgs, key=lambda x: (-len(x), -any(k.lower() in x.lower() for k in kw_list)))

    has_paired = any(
        p.startswith("机构/公司：") and any(
            c in p for c in ["Capital", "Fund", "Investment", "Group", "Ventures", "Horowitz", "a16Z", "Hugging"]
        )
        for p in patterns
    )

    en_title_map = {
        "co founder": "联合创始人", "cofounder": "联合创始人",
        "co-founder": "联合创始人", "cofounder": "联合创始人",
        "creator": "创始人",
        "founder": "创始人", "president": "总裁", "coo": "COO",
        "ceo": "CEO", "cto": "CTO", "cfo": "CFO",
        "president and coo": "总裁兼 COO",
        "author": "作家", "writer": "作家",
        "investor": "投资人", "researcher": "研究员",
        "scientist": "科学家", "engineer": "工程师",
    }

    def _translate_title(t: str) -> str:
        key = t.lower().replace("-", " ").strip()
        return en_title_map.get(key, _translate_english_role(t))

    en_paired = any(
        p.startswith("机构/公司：") and
        any(gp.startswith("职务/头衔：") and
            gp[6:].lower().replace("-", " ").strip() in en_title_map
            for gp in patterns)
        for p in patterns
    )

    best_title = _translate_title(titles[0]) if titles else ""
    best_org = orgs[0] if orgs else ""

    generic_titles = {"作家", "作者", "investor", "researcher", "scientist", "engineer", "founder"}
    if best_title in generic_titles and (best_org or research or books):
        best_title = ""

    orgs = [o for o in orgs if is_safe_guest_org_candidate(o)]
    best_org = orgs[0] if orgs else ""

    sentences = []
    if has_paired or en_paired:
        paired_org = next(
            (o for o in orgs
             if any(c in o for c in ["Capital", "Fund", "Investment", "Group", "Ventures", "Horowitz", "a16Z", "Hugging"])
            ), best_org if best_org else None
        )
        if paired_org and best_title:
            sentences.append(f"{guest_name} 是 {paired_org} 的{best_title}")
        elif paired_org:
            sentences.append(f"{guest_name} 任职于 {paired_org}")
        elif best_title:
            sentences.append(f"{guest_name} 是{best_title}")
    elif best_org and best_title:
        sentences.append(f"{guest_name} 是 {best_org} 的{best_title}")
    elif best_title:
        if best_org:
            sentences.append(f"{guest_name} 任职于 {best_org}")
        else:
            sentences.append(CONFIRMED_GUEST_FALLBACK)
    elif best_org:
        sentences.append(f"{guest_name} 任职于 {best_org}")

    if research:
        if len(sentences) > 0 and guest_name not in sentences[0]:
            sentences[0] = f"{guest_name}{sentences[0]}"
            sentences.append(f"专注{research[0]}")
        elif len(sentences) == 0:
            sentences.append(f"{guest_name}专注{research[0]}")
        elif research and (best_org or best_title):
            if len(sentences) > 0 and not sentences[-1].endswith("，"):
                sentences[-1] = sentences[-1] + "，"
            sentences.append(f"专注{research[0]}")

    if books:
        if len(sentences) > 0 and not sentences[-1].endswith("，"):
            sentences[-1] = sentences[-1] + "，"
        sentences.append(f"代表作{books[0]}")

    if not sentences:
        return None

    bg_text = "".join(sentences)
    if bg_text and not bg_text.endswith(("。", "！", "？", "...")):
        bg_text += "。"
    if len(bg_text) > 150:
        bg_text = bg_text[:147] + "..."

    info_count = sum([bool(best_org), bool(best_title), bool(research), bool(books)])
    confidence = "medium" if info_count >= 2 else "low"
    return {
        "background_zh": bg_text,
        "sources": all_sources[:2],
        "confidence": confidence,
        "note": note,
        "source_quality": sq_override,
        "source_quality_summary": f"{sq_override} (episode_show_notes)" if show_notes_info.get("source_type") == "episode_show_notes" else sq_override,
    }
