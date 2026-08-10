#!/usr/bin/env python3
"""
guest_background_fetcher.py — Phase 2
嘉宾背景搜索，使用 OpenClaw 可用能力（web_search / web_fetch / 模型），不再硬编码 MiniMax API。
Phase 2 升级：
  1. 扩展中文/英文嘉宾识别规则（保持高精度）
  2. 新增 host exclusion 机制（config/podcast_hosts.yaml）
  3. evidence 格式升级为结构化对象（source/pattern/matched_text/decision/reason）
  4. 背景生成质量升级（字数限制，结构化输出）
  5. source_quality 分级（primary/secondary/weak）
  6. confidence 规则（依赖 source_quality）
  7. 缓存 TTL 规则（confirmed=90天，not_confirmed=30天）
  8. 搜索/模型失败不影响主流程（best-effort，降级处理）
"""

import html
import os
import json
import re
import yaml
import subprocess
from datetime import datetime, timezone, timedelta
from typing import Optional
from pipeline_paths import get_pipeline_paths

from guest_cache_store import (
    load_cache as cache_store_load_cache,
    save_cache as cache_store_save_cache,
    guest_key as cache_store_guest_key,
    get_cache_entry as cache_store_get_cache_entry,
    write_cache_entry as cache_store_write_cache_entry,
)
from guest_background_generator import (
    build_background_prompt,
    generate_background_from_show_notes,
    is_safe_guest_org_candidate,
    run_background_model,
    should_accept_model_background,
)
from guest_search_adapter import (
    search_guest_background_openclaw as search_adapter_search_guest_background_openclaw,
)
from guest_source_quality import (
    PRIMARY_DOMAINS,
    SECONDARY_DOMAINS,
    WEAK_INDICATORS,
    classify_source_quality as source_quality_classify_source_quality,
    rate_overall_source_quality as source_quality_rate_overall_source_quality,
)

_RUNTIME_PATHS = get_pipeline_paths()
PIPELINE_DIR = str(_RUNTIME_PATHS.pipeline_dir)
STATE_DIR = str(_RUNTIME_PATHS.state_dir)
CONFIG_DIR = str(_RUNTIME_PATHS.config_dir)
CACHE_FILE = os.path.join(STATE_DIR, "guest_profiles_cache.json")
CACHE_TTL_DAYS_CONFIRMED = 90
CACHE_TTL_DAYS_NOT_CONFIRMED = 30
TZ_SH = timezone(timedelta(hours=8))

# ─────────────────────────────────────────────────────────────────────
# 缓存
# ─────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    return cache_store_load_cache(CACHE_FILE)


def _save_cache(cache: dict):
    return cache_store_save_cache(CACHE_FILE, cache)


def _guest_key(guest_name: str, affiliation_hint: str = "", podcast_title: str = "") -> str:
    """缓存 key 包含 guest_name + affiliation_hint + podcast_title"""
    return cache_store_guest_key(guest_name, affiliation_hint, podcast_title)


def _cache_entry(key: str) -> Optional[dict]:
    """返回缓存条目，若不存在或过期返回 None。"""
    return cache_store_get_cache_entry(
        CACHE_FILE,
        key,
        now=datetime.now(TZ_SH),
        confirmed_ttl_days=CACHE_TTL_DAYS_CONFIRMED,
        other_ttl_days=CACHE_TTL_DAYS_NOT_CONFIRMED,
    )


def _write_cache_entry(key: str, data: dict):
    return cache_store_write_cache_entry(CACHE_FILE, key, data, now=datetime.now(TZ_SH))


# ─────────────────────────────────────────────────────────────────────
# Phase 2 Safety Constants
# ─────────────────────────────────────────────────────────────────────

# 统一 fallback：当 confirmed_guest 但背景不足时使用此常量
CONFIRMED_GUEST_FALLBACK = "已确认本期嘉宾，但节目元数据未提供足够背景信息，暂不补充背景。"

# 已知职位白名单：只有来自此名单的职位才能输出"是X"
KNOWN_TITLE_PHRASES = {
    "General Partner", "Partner", "CIO", "CEO", "CFO", "CTO", "COO",
    "Founder", "Co-founder", "Cofounder", "Co-founder",
    "Chair", "Chairman", "Co-chair",
    "Investor", "Managing Partner", "Senior Partner", "Principal",
    "Professor", "Research Scientist", "Director", "Head",
}

# 主题词黑名单：这些词不能作为职位/机构输出
TOPIC_PHRASES = {
    "reshaping venture capital", "venture capital", "private markets",
    "software repricing", "capital allocation", "capital allocation strategy",
    "investment strategy", "market commentary", "fund performance",
}

# ─────────────────────────────────────────────────────────────────────
# Step 0: host exclusion 配置加载
# ─────────────────────────────────────────────────────────────────────

def _load_podcast_hosts() -> dict:
    """加载 config/podcast_hosts.yaml，返回 {podcast_name: [host_names]}"""
    hosts_path = os.path.join(CONFIG_DIR, "podcast_hosts.yaml")
    if not os.path.exists(hosts_path):
        return {}
    try:
        with open(hosts_path) as f:
            data = yaml.safe_load(f) or {}
        result = {}
        for pod_name, cfg in data.items():
            if isinstance(cfg, dict) and "hosts" in cfg:
                result[pod_name] = [h.lower().strip() for h in cfg["hosts"]]
            elif isinstance(cfg, list):
                result[pod_name] = [h.lower().strip() for h in cfg]
        return result
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────
# Step 1: 嘉宾状态检测（Phase 2 核心）
# ─────────────────────────────────────────────────────────────────────

DETECTION_STATUSES = {
    "confirmed_guest",  # 明确是本期嘉宾，可以补背景
    "possible_guest",   # 可能是嘉宾，证据不足，默认不补
    "mentioned_entity", # 只是节目讨论对象，不补嘉宾背景
    "no_guest_detected", # 未识别到嘉宾
    "ambiguous",        # 同名或上下文不清
}

# Phase 2: 标题中出现的词（历史人物/艺术家/作者/公司/书名）
# 不得默认识别为嘉宾；需要 description 中有明确 guest 标记才能 override
TITLE_ENTITY_WORDS = {
    # 历史人物/艺术家
    "frida", "frida kahlo", "kahlo", "gaudi", "gauguin", "van gogh",
    "monet", "picasso", "dali", "dalí", "warhol", "okeeffe",
    "kandinsky", "matisse", "rembrandt", "michelangelo", "lego",
    # 常见英文人名（太通用，不能直接当嘉宾）
    "freda", "fred", "john", "jane", "alice", "bob", "mike", "tom",
    "jack", "lucy", "sarah", "emma", "oliver", "david", "sophie",
    # 公司/品牌
    "apple", "google", "meta", "amazon", "tesla", "nvidia", "openai",
    "microsoft", "facebook", "twitter", "x corp",
    # 书名/作品关键词
    "capital", "principles", "war", "peace", "genesis", "odin",
}

# Phase 2: 明确表示嘉宾的英文短语（按优先级排序）
EXPLICIT_GUEST_PATTERNS_EN = [
    # 高优先级：guest: / featuring guest / joined by（独立成行的格式）
    (r'\bguest\s*:\s*([^,\n。]{2,40})', 'guest_colon'),
    (r'\bfeaturing\s+guest\s+([^,\n。]{2,40})', 'featuring_guest'),
    (r'\bjoined\s+by\s+([^,\n。]{2,40})', 'joined_by'),
    # 标准模式
    (r'\bin\s+conversation\s+with\s+([^,\n。]{2,40})', 'in_conversation_with'),
    (r'\ba\s+conversation\s+with\s+([^,\n。]{2,40})', 'a_conversation_with'),
    (r'\btalks?\s+with\s+([^,\n。]{2,40})', 'talks_with'),
    (r'\bspeaks?\s+with\s+([^,\n。]{2,40})', 'speaks_with'),
    (r'\binterview\s+with\s+([^,\n。]{2,40})', 'interview_with'),
    (r'\btalking\s+to\s+([^,\n。]{2,40})', 'talking_to'),
    # 低优先级：单独的 with / featuring（需要 description 上下文确认）
    (r'\bwith\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b', 'with_pattern'),
    (r'\bfeaturing\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b', 'featuring_pattern'),
    # 低优先级：X joins Y（podcast 描述风格：guest joins host for a conversation）
    # 例: "Marc Andreessen joins Joe Rogan for a conversation"
    (r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s+joins\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s+(?:for|to|today|on)', 'guest_joins'),
]

# Phase 2: 明确表示嘉宾的中文短语
EXPLICIT_GUEST_PATTERNS_ZH = [
    # 高优先级：明确嘉宾标记
    (r'本期请到\s*([^，。、；!?\n]{2,30})', '本期请到'),
    (r'本期邀请\s*([^，。、；!?\n]{2,30})', '本期邀请'),
    (r'我们邀请了\s*([^，。、；!?\n]{2,30})', '我们邀请了'),
    (r'本期嘉宾[：:]\s*([^，。、；!?\n]{2,30})', '本期嘉宾'),
    (r'嘉宾[：:]\s*([^，。、；!?\n]{2,30})', '嘉宾'),
    (r'特邀嘉宾\s*([^，。、；!?\n]{2,30})', '特邀嘉宾'),
    (r'访谈嘉宾\s*([^，。、；!?\n]{2,30})', '访谈嘉宾'),
    (r'对谈嘉宾\s*([^，。、；!?\n]{2,30})', '对谈嘉宾'),
    (r'主播对话\s*([^，。、；!?\n]{2,30})', '主播对话'),
    # 中优先级：邀请类
    (r'邀请\s*([^，。、；!?\n]{2,30})\s*(?:来做客|来聊|来对谈|参加)', '邀请来做客'),
    # 低优先级：需要 description 确认
    (r'和\s*([^，。、；!?\n]{2,30})\s*聊', '和XXX聊'),
    (r'跟\s*([^，。、；!?\n]{2,30})\s*聊', '跟XXX聊'),
    (r'与\s*([^，。、；!?\n]{2,30})\s*(?:对谈|访谈|交流)', '与XXX对谈'),
    (r'([^，。、；!?\n]{2,30})\s*做客', 'XXX做客'),
    (r'([^，。、；!?\n]{2,30})\s*来做客', 'XXX来做客'),
    (r'嘉宾是\s*([^，。、；!?\n]{2,30})', '嘉宾是'),
]

ROLE_BEFORE_NAME_ZH = [
    (
        r'(?:本期(?:对话)?的?嘉宾是|嘉宾是)'
        r'(?:我的好朋友)?'
        r'(?:[\u4e00-\u9fffA-Za-z0-9&. ]{1,30}的)?'
        r'(?:创始人兼CEO|创始人|CEO|投资人|合伙人)\s*'
        r'([\u4e00-\u9fff]{2,4})(?=[。；，,、\s]|$|他|她|是)',
        'chinese_role_before_name',
    ),
    (
        r'(?:本期(?:对话)?的?嘉宾是|嘉宾是)(?:我的好朋友)?\s*'
        r'([\u4e00-\u9fff]{2,4})(?=[。；，,、\s]|$|他|她|是)',
        'chinese_guest_intro_name',
    ),
    (
        r'(?:本期嘉宾|本期对话嘉宾)\s*'
        r'([\u4e00-\u9fff]{2,4})(?=是|，|。|$)',
        'chinese_guest_intro_name',
    ),
]

CHINESE_NAME_ENGLISH_ALIAS = (
    r'(?:[\u4e00-\u9fffA-Za-z0-9&. ]{0,40}'
    r'(?:嘉宾|工程师|创始人|CEO|投资人|合伙人)\s*)?'
    r'([\u4e00-\u9fff]{2,4})\s*'
    r'[（(]\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\s*[）)]'
)

# 噪声短语
BAD_TITLE_WORDS = {
    "trailer", "preview", "announcement", "full episode", "episode info",
    "show info", "subscribe", "intro", "outro", "opening", "closing",
    "ad ", "advertisement", "sponsored", "bonus", "omnibus",
}

# Phase 2: 英文描述格式（Title Case Name + Title + is a...）
# 例: "David Paulides is a writer, investigator..."
# 例: "Marc Andreessen is a co-founder and general partner..."
DESC_GUEST_EN = [
    # "X is a..." 描述模式 — 名字在句首，后面紧跟 is a/was/are
    # 排除 "The X is a..." 格式（"The Bittersweet Age"等标题党）
    (r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s+(?:is|was|are)\s+(?:a|an|the)\s+', 'desc_is_a'),
    # "X, PhD, is..." / "X, MD, is..." 描述模式
    (r'(?:^|\n)([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}),\s+(?:Ph\.?D\.?|MD|MBA|CFA),\s+is\b', 'desc_credentialed_is'),
]

ROLE_BEFORE_NAME_EN = [
    (
        r'\b(?:[A-Z][A-Za-z0-9&.-]{1,30}\s+)?(?:co-founder|cofounder|founder)\s+and\s+CEO\s+'
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b',
        'english_role_before_name',
    ),
    (
        r'\b(?:[A-Z][A-Za-z0-9&.-]{1,30}\s+)?(?:founder|researcher|writer|author|trader)\s+'
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b',
        'english_role_before_name',
    ),
    (
        r'\b(?:economists?|Substack writer|writer|author|trader|researcher)'
        r'(?:\s+and\s+(?:economists?|Substack writer|writer|author|trader|researcher))*\s+'
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b',
        'english_role_before_name',
    ),
]

APPOSITIVE_MULTI_GUEST_EN = (
    r'\b(?:speaks?|talks?)\s+with\s+'
    r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}),\s+[^,\n]{2,120},\s+and\s+'
    r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}),\s+[^,\n]{2,120}(?=,|\.)'
)

COORDINATED_MULTI_GUEST_EN = (
    r'\b(?:(?:speaks?|talks?)\s+with|interviews)\s+'
    r'(?:economists?|analysts?|investors?|founders?|researchers?|writers?|authors?)\s+'
    r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\s+and\s+'
    r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b'
)

BARE_COORDINATED_MULTI_GUEST_EN = (
    r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\s+and\s+'
    r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\s+'
    r'(?:team\s+up|discuss|talk\s+about|explain|join)\b'
)

# Phase 2: 中文描述格式（中文播客 show_notes 中）
# 例: "Freda Duan在湾区做投资，是Altimeter Capital的合伙人"
# 例: "——张三，某投资机构创始人"
# 例: "恽雷@南方基金，基金经理"
# 例: "🎤 本期嘉宾恽雷@南方基金"
DESC_GUEST_ZH = [
    # 破折号/省略号后的人名："——Freda Duan" 或 "Freda Duan，..."
    (r'[——~…]{1,2}\s*([A-Z][a-zA-Z]{1,30}(?:\s+[A-Za-z]{1,20}){0,2})(?=[，,，\s]|$)', 'dash_name'),
    # 引出语+人名："先介绍一下——Freda Duan"
    (r'[先](?:先)?\s*介绍[一下]*[：:—~-]*\s*([A-Z][a-zA-Z]{1,30}(?:\s+[A-Za-z]{1,20}){0,2})(?=[，,，\s]|$)', 'intro_name'),
    # 中文句子中人名+是/在/为+职务（英文名在中文上下文中）
    (r'([A-Z][a-zA-Z]{1,30}(?:\s+[A-Za-z]{1,20}){0,2})(?:在|是|为|氏)\s*[^，。、；!?\n]{2,40}', 'zh_name_desc'),
    # emoji+关键词+人名："🎤 本期嘉宾恽雷" 或 "🎤 恽雷"
    (r'[🎤🎧🎙️]\s*(?:本期嘉宾\s*)?([A-Za-z\u4e00-\u9fff]{2,20})(?=[@，,，\s]|$)', 'emoji_guest'),
    # "本期嘉宾X"（无冒号）
    (r'本期嘉宾\s*([A-Za-z\u4e00-\u9fff]{2,20})(?=[@，,，\s]|$)', 'benqi_guoke'),
]


def _is_title_entity(name: str) -> bool:
    """
    判断名字是否是"标题中出现的历史人物/艺术家/作者/公司/书名"而非本期嘉宾。
    只在名字本身（去除空格后）完全匹配或在 title 中单独出现时判定为 entity。
    不做子串匹配（避免 'Freda Smith' 因包含 'freda' 而被误判）。
    """
    n_lower = name.lower().strip()
    if n_lower in TITLE_ENTITY_WORDS:
        return True
    words = n_lower.split()
    if len(words) == 1:
        return n_lower in TITLE_ENTITY_WORDS
    return False


def _is_likely_noise(candidate: str) -> bool:
    """判断候选嘉宾名是否是噪声。"""
    c = candidate.strip()
    c_lower = c.lower()
    if len(c) < 2 or len(c) > 60:
        return True
    if c_lower in BAD_TITLE_WORDS:
        return True
    if re.match(r'^[\d.,/#\s\[\]\(\){}【】（）]+$', c):
        return True
    if re.match(r'^(episode|ep|part|pt)\s*\d+', c_lower):
        return True
    if re.match(r'^\d{1,2}:\d{2}', c):
        return True
    if c in {"她多次", "他多次", "它多次"}:
        return True
    # Phase 2: 过滤纯停用词
    stop_words_set = {"the", "a", "an", "of", "and", "or", "but", "in", "on", "at", "to", "for", "with", "about", "from"}
    words = c_lower.split()
    if len(words) <= 2 and all(w in stop_words_set for w in words):
        return True
    return False


def canonicalize_guest_name_phase2(name: str) -> str:
    """Normalize light role-prefix pollution without changing ordinary names."""
    cleaned = re.sub(r'\s+', ' ', str(name or "").strip())
    if not cleaned:
        return ""
    if re.search(r'[\u4e00-\u9fff]', cleaned):
        zh_patterns = [
            r'^我的好朋友([\u4e00-\u9fff]{2,4})$',
            (
                r'^(?:[\u4e00-\u9fffA-Za-z0-9&. ]{1,30}的)?'
                r'(?:创始人兼CEO|创始人|CEO|投资人|合伙人)'
                r'([\u4e00-\u9fff]{2,4})$'
            ),
            r'^嘉宾是([\u4e00-\u9fff]{2,4})$',
        ]
        for pattern in zh_patterns:
            match = re.match(pattern, cleaned)
            if match:
                return match.group(1).strip()
        return cleaned

    role_prefixes = (
        "tech analyst",
        "analysts",
        "analyst",
        "economists",
        "economist",
        "investors",
        "investor",
        "founders",
        "founder",
        "researchers",
        "researcher",
        "substack writer",
        "writers",
        "writer",
        "traders",
        "trader",
        "authors",
        "author",
    )
    for prefix in role_prefixes:
        pattern = r'(?i)^' + re.escape(prefix) + r'\s+([A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*){0,2})$'
        match = re.match(pattern, cleaned)
        if match:
            return match.group(1).strip()
    return cleaned


def is_guest_name_noise_phase2(name: str) -> bool:
    """Return whether a guest candidate is a known sponsor/noise token."""
    cleaned = re.sub(r'\s+', ' ', str(name or "").strip())
    if not cleaned:
        return True
    normalized = cleaned.lower()
    exact_noise = {
        "author",
        "authors",
        "ceo",
        "co-founder",
        "cofounder",
        "code rogan",
        "promo code rogan",
        "rogan",
        "draftkings",
        "analyst",
        "analysts",
        "economist",
        "economists",
        "founder",
        "founders",
        "investor",
        "investors",
        "betterhelp",
        "visible",
        "visible+ pro",
        "armra",
        "bluechew",
        "chime",
        "perplexity",
        "researcher",
        "researchers",
        "trade the pool",
        "trader",
        "traders",
        "writer",
        "writers",
    }
    return normalized in exact_noise


def prune_redundant_single_token_guest_names_phase2(names: list[str]) -> list[str]:
    """Drop residual fragments when a cleaner full guest name is also present."""
    cleaned_names = [re.sub(r'\s+', ' ', str(name or "").strip()) for name in names]
    full_english_first_names = {
        name.split()[0].lower()
        for name in cleaned_names
        if re.match(r'^[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)+$', name)
    }
    clean_chinese_names = {
        name
        for name in cleaned_names
        if re.match(r'^[\u4e00-\u9fff]{2,4}$', name)
    }

    result = []
    for name in cleaned_names:
        if not name:
            continue
        if re.match(r'^[A-Z][A-Za-z]+$', name) and name.lower() in full_english_first_names:
            continue
        if "的访" in name and any(name.startswith(clean_name) for clean_name in clean_chinese_names):
            continue
        result.append(name)
    return result


def prune_redundant_guest_candidates_phase2(candidates: list[dict]) -> list[dict]:
    """Apply final cross-candidate cleanup while preserving candidate metadata."""
    keep_names = set(prune_redundant_single_token_guest_names_phase2([c.get("name", "") for c in candidates]))
    return [c for c in candidates if c.get("name", "") in keep_names]


def _clean_name(name: str) -> str:
    """清理嘉宾名：去除噪声后缀，去除多余空白"""
    name = re.sub(r'\s+', ' ', name.strip())
    # 去除末尾的 "and" / "&" 或 "and X" / "& X"（multi-guest 残留）
    name = re.sub(r'\s+and\s+\S+(?:\s+\S+)*\s*$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+&\s+\S+(?:\s+\S+)*\s*$', '', name)
    # 去除末尾的 "about..." 等
    name = re.sub(r'\s+about\s+\S+(?:\s+\S+)*\s*$', '', name, flags=re.IGNORECASE)
    # 去除末尾标点
    name = name.rstrip('.,;:')
    # 去除末尾残留的引号（如 "Name" 尾部的 "）
    name = name.translate(str.maketrans("", "", chr(34) + chr(39)))
    return canonicalize_guest_name_phase2(name.strip())


def _is_host_name(name: str, podcast_name: str, hosts_config: dict) -> bool:
    """检查 name 是否是指定播客的已知主播（host exclusion）"""
    n_lower = name.lower().strip()
    # 完全匹配
    if podcast_name in hosts_config:
        host_list = hosts_config[podcast_name]
        if n_lower in host_list:
            return True
        # 部分匹配（名字至少 3 个字符）
        if len(n_lower) >= 3:
            for h in host_list:
                if len(h) >= 3 and (h in n_lower or n_lower in h):
                    return True
    return False


def _parse_multi_guest(text: str, base_name: str) -> list[str]:
    """
    从 "with Michael Duffey and Dino Mavrookas" 中解析多个嘉宾。
    策略：检测 'and' 分隔符，返回 base_name + and 后的名字。
    """
    names = [_clean_name(base_name)]
    # 找 "and" 后的下一个名字
    rest = re.search(
        r'(?i)\bwith\s+' + re.escape(base_name) + r'\s+and\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})',
        text
    )
    if rest:
        second = _clean_name(rest.group(1))
        if second and not _is_likely_noise(second):
            names.append(second)
    return names


def extract_guest_names_phase2(ep: dict, hosts_config: dict) -> list[dict]:
    """
    Phase 2：从 episode metadata 中提取嘉宾名单及结构化 evidence。
    返回 list[dict]，每项包含 name, source, pattern, matched_text, decision。
    """
    # HTML unescape FIRST so that entity codes like &#8211; and &amp; are decoded before parsing
    title = html.unescape(ep.get("episode_title", ""))
    notes = ep.get("show_notes_text", "") or ep.get("show_notes", "") or ""
    description = ep.get("description", "") or ""
    feed_guests = ep.get("guests", [])
    podcast_name = ep.get("podcast_name", "")

    candidates = []
    texts_to_search = [
        (notes, "description"),
        (description, "description"),
    ]

    # ── 0a. 中文姓名 + 英文别名：洪力德（Lewis Hong）───────────────
    for text_src, src_label in texts_to_search:
        for m in re.finditer(CHINESE_NAME_ENGLISH_ALIAS, text_src, re.MULTILINE):
            name = _clean_name(m.group(1).strip())
            if name.startswith("某"):
                continue
            if _is_likely_noise(name):
                continue
            if _is_host_name(name, podcast_name, hosts_config):
                continue
            if _is_title_entity(name):
                continue
            candidates.append({
                "name": name,
                "source": src_label,
                "pattern": "chinese_name_english_alias",
                "matched_text": m.group(0)[:100],
                "decision": "confirmed_guest",
                "reason": "中文姓名 + 英文别名格式识别为嘉宾",
            })

    # ── 0. 中文 role-before-name / 嘉宾介绍句 ────────────────────────
    for pat_str, pat_name in ROLE_BEFORE_NAME_ZH:
        for text_src, src_label in texts_to_search:
            for m in re.finditer(pat_str, text_src, re.MULTILINE):
                name = _clean_name(m.group(1).strip())
                if name.startswith("某"):
                    continue
                if _is_likely_noise(name):
                    continue
                if _is_host_name(name, podcast_name, hosts_config):
                    continue
                if _is_title_entity(name):
                    continue
                candidates.append({
                    "name": name,
                    "source": src_label,
                    "pattern": pat_name,
                    "matched_text": m.group(0)[:100],
                    "decision": "confirmed_guest",
                    "reason": "中文 role-before-name / 嘉宾介绍句识别为嘉宾",
                })

    # ── 1. 高优先级中文明确短语 ─────────────────────────────────────
    for pat_str, pat_name in EXPLICIT_GUEST_PATTERNS_ZH[:8]:  # 高优先级前8个
        for text_src, src_label in texts_to_search:
            for m in re.finditer(pat_str, text_src, re.MULTILINE):
                raw_name = m.group(1).strip()
                name = _clean_name(raw_name)
                if _is_likely_noise(name):
                    continue
                if _is_host_name(name, podcast_name, hosts_config):
                    candidates.append({
                        "name": name,
                        "source": src_label,
                        "pattern": pat_name,
                        "matched_text": m.group(0)[:100],
                        "decision": "rejected_host",
                        "reason": f"'{name}'是已知主播，跳过",
                    })
                    continue
                if _is_title_entity(name) and pat_name not in ("本期嘉宾", "嘉宾", "特邀嘉宾", "访谈嘉宾"):
                    candidates.append({
                        "name": name,
                        "source": src_label,
                        "pattern": pat_name,
                        "matched_text": m.group(0)[:100],
                        "decision": "mentioned_entity",
                        "reason": f"'{name}'是历史人物/标题实体，不是嘉宾",
                    })
                    continue
                candidates.append({
                    "name": name,
                    "source": src_label,
                    "pattern": pat_name,
                    "matched_text": m.group(0)[:100],
                    "decision": "confirmed_guest",
                    "reason": f"匹配中文嘉宾短语「{pat_name}」",
                })

    # ── 2. 低优先级中文短语（只在有 description 确认时提升为 confirmed） ─
    for pat_str, pat_name in EXPLICIT_GUEST_PATTERNS_ZH[8:]:
        for text_src, src_label in texts_to_search:
            for m in re.finditer(pat_str, text_src, re.MULTILINE):
                raw_name = m.group(1).strip()
                name = _clean_name(raw_name)
                if _is_likely_noise(name):
                    continue
                if _is_host_name(name, podcast_name, hosts_config):
                    # host exclusion 有效，除非 description 明确说"本期嘉宾：XXX"
                    if pat_name not in ("本期嘉宾", "嘉宾", "特邀嘉宾"):
                        candidates.append({
                            "name": name,
                            "source": src_label,
                            "pattern": pat_name,
                            "matched_text": m.group(0)[:100],
                            "decision": "rejected_host",
                            "reason": f"'{name}'是已知主播",
                        })
                        continue
                candidates.append({
                    "name": name,
                    "source": src_label,
                    "pattern": pat_name,
                    "matched_text": m.group(0)[:100],
                    "decision": "possible_guest",
                    "reason": f"匹配「{pat_name}」，但需要描述上下文确认",
                })

    # ── 3. 英文明确短语（高优先级） ─────────────────────────────────
    for pat_str, pat_name in EXPLICIT_GUEST_PATTERNS_EN[:8]:
        for text_src, src_label in texts_to_search:
            for m in re.finditer(pat_str, text_src, re.MULTILINE | re.IGNORECASE):
                raw_name = m.group(1).strip()
                name = _clean_name(raw_name)
                if _is_likely_noise(name):
                    continue
                if _is_host_name(name, podcast_name, hosts_config):
                    candidates.append({
                        "name": name,
                        "source": src_label,
                        "pattern": pat_name,
                        "matched_text": m.group(0)[:100],
                        "decision": "rejected_host",
                        "reason": f"'{name}'是已知主播，跳过",
                    })
                    continue
                if _is_title_entity(name):
                    candidates.append({
                        "name": name,
                        "source": src_label,
                        "pattern": pat_name,
                        "matched_text": m.group(0)[:100],
                        "decision": "mentioned_entity",
                        "reason": f"'{name}'是标题实体/历史人物",
                    })
                    continue
                candidates.append({
                    "name": name,
                    "source": src_label,
                    "pattern": pat_name,
                    "matched_text": m.group(0)[:100],
                    "decision": "confirmed_guest",
                    "reason": f"匹配英文嘉宾短语「{pat_name}」",
                })

    # ── 4. 低优先级英文 with/featuring 模式（需 description 确认） ────
    for pat_str, pat_name in EXPLICIT_GUEST_PATTERNS_EN[8:]:
        for text_src, src_label in texts_to_search:
            for m in re.finditer(pat_str, text_src, re.MULTILINE | re.IGNORECASE):
                raw_name = m.group(1).strip()
                name = _clean_name(raw_name)
                if _is_likely_noise(name):
                    continue
                if _is_host_name(name, podcast_name, hosts_config):
                    continue  # host exclusion，不作为 possible_guest
                # description 确认：同一文本中是否有"is a"/"is an"
                ctx = text_src[max(0, m.start()-20):m.end()+100]
                has_desc_confirm = bool(re.search(
                    r'(?i)' + re.escape(name) + r'\s+(?:is|was|are)\s+(?:a|an|the)',
                    ctx
                ))
                if has_desc_confirm:
                    candidates.append({
                        "name": name,
                        "source": src_label,
                        "pattern": pat_name,
                        "matched_text": m.group(0)[:100],
                        "decision": "confirmed_guest",
                        "reason": f"匹配「{pat_name}」+描述确认",
                    })
                else:
                    candidates.append({
                        "name": name,
                        "source": src_label,
                        "pattern": pat_name,
                        "matched_text": m.group(0)[:100],
                        "decision": "possible_guest",
                        "reason": f"匹配「{pat_name}」但需描述确认",
                    })

    # ── 5. 英文描述格式：Name is a/was a/are...（JRE 等风格） ────────
    # 例: "David Paulides is a writer, investigator..." → David Paulides 是嘉宾
    for text_src, src_label in texts_to_search:
        for m in re.finditer(DESC_GUEST_EN[0][0], text_src, re.MULTILINE):
            name = _clean_name(m.group(1).strip())
            if _is_likely_noise(name):
                continue
            if _is_host_name(name, podcast_name, hosts_config):
                continue
            if _is_title_entity(name):
                continue
            # 提取完整句子中的身份描述
            full_sentence = m.group(0)
            candidates.append({
                "name": name,
                "source": src_label,
                "pattern": "desc_is_a",
                "matched_text": full_sentence[:100],
                "decision": "confirmed_guest",
                "reason": f"描述格式「Name is a...」识别为嘉宾",
            })

    # ── 5a. 英文 credentials 格式：Name, PhD/MD/MBA/CFA, is... ────────
    for text_src, src_label in texts_to_search:
        for m in re.finditer(DESC_GUEST_EN[1][0], text_src, re.MULTILINE | re.IGNORECASE):
            name = _clean_name(m.group(1).strip())
            if _is_likely_noise(name):
                continue
            if _is_host_name(name, podcast_name, hosts_config):
                continue
            if _is_title_entity(name):
                continue
            candidates.append({
                "name": name,
                "source": src_label,
                "pattern": "desc_credentialed_is",
                "matched_text": m.group(0)[:100],
                "decision": "confirmed_guest",
                "reason": "描述格式「Name, credentials, is...」识别为嘉宾",
            })

    # ── 5b. 英文 role-before-name 格式：Role Person... ────────────────
    # 例: "Brex co-founder and CEO Pedro Franceschi..." → Pedro Franceschi
    # 例: "economist and Substack writer Noah Smith..." → Noah Smith
    for pat_str, pat_name in ROLE_BEFORE_NAME_EN:
        for text_src, src_label in texts_to_search:
            for m in re.finditer(pat_str, text_src, re.MULTILINE):
                name = _clean_name(m.group(1).strip())
                if _is_likely_noise(name):
                    continue
                if _is_host_name(name, podcast_name, hosts_config):
                    continue
                if _is_title_entity(name):
                    continue
                candidates.append({
                    "name": name,
                    "source": src_label,
                    "pattern": pat_name,
                    "matched_text": m.group(0)[:120],
                    "decision": "confirmed_guest",
                    "reason": "英文 role-before-name 格式识别为嘉宾",
                })

    # ── 5c. 英文 appositive 多嘉宾格式：speaks/talks with X, role, and Y, role ──
    for text_src, src_label in texts_to_search:
        for m in re.finditer(APPOSITIVE_MULTI_GUEST_EN, text_src):
            for gi in [1, 2]:
                name = _clean_name(m.group(gi).strip()) if m.group(gi) else ""
                if not name or _is_likely_noise(name) or _is_host_name(name, podcast_name, hosts_config):
                    continue
                if _is_title_entity(name):
                    continue
                candidates.append({
                    "name": name,
                    "source": src_label,
                    "pattern": "english_appositive_multi_guest",
                    "matched_text": m.group(0)[:150],
                    "decision": "confirmed_guest",
                    "reason": "英文 appositive multi-guest 格式识别为嘉宾",
                })

    # ── 5d. 英文 coordinated 多嘉宾格式：speaks with role X and Y ──────
    for text_src, src_label in texts_to_search:
        for m in re.finditer(COORDINATED_MULTI_GUEST_EN, text_src):
            for gi in [1, 2]:
                name = _clean_name(m.group(gi).strip()) if m.group(gi) else ""
                if not name or _is_likely_noise(name) or _is_host_name(name, podcast_name, hosts_config):
                    continue
                if _is_title_entity(name):
                    continue
                candidates.append({
                    "name": name,
                    "source": src_label,
                    "pattern": "english_coordinated_multi_guest",
                    "matched_text": m.group(0)[:150],
                    "decision": "confirmed_guest",
                    "reason": "英文 coordinated multi-guest 格式识别为嘉宾",
                })

    # ── 5e. 英文 bare coordinated 多嘉宾格式：X and Y team up/discuss ──
    for text_src, src_label in texts_to_search:
        for m in re.finditer(BARE_COORDINATED_MULTI_GUEST_EN, text_src):
            for gi in [1, 2]:
                name = _clean_name(m.group(gi).strip()) if m.group(gi) else ""
                if not name or _is_likely_noise(name) or _is_host_name(name, podcast_name, hosts_config):
                    continue
                if _is_title_entity(name):
                    continue
                candidates.append({
                    "name": name,
                    "source": src_label,
                    "pattern": "english_bare_coordinated_multi_guest",
                    "matched_text": m.group(0)[:150],
                    "decision": "confirmed_guest",
                    "reason": "英文 bare coordinated multi-guest 格式识别为嘉宾",
                })

    # ── 5f. Description multi-speaker pattern ─────────────────────────
    # 例: "David George, General Partner at a16z, and David Clark, CIO, discuss..."
    # 例: "X, Title at Org, and Y, Title, talk about..."
    # 识别两个嘉宾 + discuss/talk/explain 语义，必须有 title/role
    for text_src, src_label in texts_to_search:
        for m in re.finditer(
            r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}),\s+[^,]+,\s+and\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}),\s+[^,]+,\s+(?:discuss|talk|explain|share)',
            text_src, re.IGNORECASE
        ):
            for gi in [1, 2]:
                name = _clean_name(m.group(gi).strip()) if m.group(gi) else ""
                if not name or _is_likely_noise(name) or _is_host_name(name, podcast_name, hosts_config):
                    continue
                if _is_title_entity(name):
                    continue
                candidates.append({
                    "name": name,
                    "source": src_label,
                    "pattern": "description_multi_speaker",
                    "matched_text": m.group(0)[:150],
                    "decision": "confirmed_guest",
                    "reason": "描述 multi-speaker 格式识别为嘉宾",
                })

    # ── 5g. 中文描述格式（中文播客 show_notes 中的人名+职务）──────────
    # 例: "——Freda Duan在湾区做投资，是Altimeter Capital的合伙人"
    # 例: "那还是先介绍一下——Freda Duan在湾区做投资"
    for pat_str, pat_name in DESC_GUEST_ZH:
        for text_src, src_label in texts_to_search:
            for m in re.finditer(pat_str, text_src, re.MULTILINE):
                raw_name = m.group(1).strip()
                name = _clean_name(raw_name)
                if _is_likely_noise(name):
                    continue
                if _is_host_name(name, podcast_name, hosts_config):
                    continue
                if _is_title_entity(name):
                    continue
                # 检查上下文中是否有明确嘉宾信号（介绍语/破折号后）
                ctx_start = max(0, m.start() - 30)
                ctx = text_src[ctx_start:m.end()]
                has_intro_signal = any(s in ctx for s in ["——", "~", "…", "先介绍", "请到", "邀请", "嘉宾", "来做客"])
                if has_intro_signal or pat_name == "dash_name":
                    candidates.append({
                        "name": name,
                        "source": src_label,
                        "pattern": pat_name,
                        "matched_text": m.group(0)[:100],
                        "decision": "confirmed_guest",
                        "reason": f"中文描述格式「{pat_name}」识别为嘉宾",
                    })
                else:
                    candidates.append({
                        "name": name,
                        "source": src_label,
                        "pattern": pat_name,
                        "matched_text": m.group(0)[:100],
                        "decision": "possible_guest",
                        "reason": f"匹配中文描述「{pat_name}」，需进一步确认",
                    })

    # ── 6. 从 feed metadata 中提取 ───────────────────────────────────
    for g in feed_guests:
        if isinstance(g, str):
            name = g.strip()
        elif isinstance(g, dict):
            name = g.get("name", "").strip()
            if g.get("role") in {"host", "anchor", "presenter"}:
                continue
        else:
            continue
        if name and not _is_likely_noise(name):
            candidates.append({
                "name": _clean_name(name),
                "source": "feed_metadata",
                "pattern": "feed_guest",
                "matched_text": str(g)[:80],
                "decision": "confirmed_guest",
                "reason": "来自 feed guests/contributors 字段",
            })

    # ── 7. 从标题中提取（明确的 #数字 - Guest Name 格式）──────────────
    # JRE 普通格式: "#2507 - Harland Williams" → Harland Williams
    # JRE MMA 格式: "JRE MMA Show #179 with Josh Thompson & "Big" John McCarthy"
    # Lex Fridman 格式: "#497 – Title – Don Lincoln"
    title_guest_patterns = [
        # JRE 普通格式: "#2507 - Harland Williams"
        (r'^#(\d+)\s*[-–—]\s*(?!The\b|A\b|An\b)([A-Za-z]+(?:\s+[A-Za-z]+){0,2})$', 'title_jre_format'),
        # JRE MMA 格式: "JRE MMA Show #179 with Josh Thompson & "Big" John McCarthy"
        # 支持两种分隔符: & "Name" (引号包裹) 和 & Name (无引号)
        (r'^JRE\s+MMA\s+Show\s+#(\d+)\s+with\s+([^&]+?)(?:\s+&\s+(.+))?$', 'title_jre_mma'),
        # Lex Fridman 格式: "#497 – Title – Don Lincoln"
        # Capture: everything up to last dash, then last word(s) as guest name
        (r'^[^-\uff0d–—]*[-–—]\s*[\s\S]*?[-–—]\s+([A-Z][a-z]+(?:\s+[A-Z]\.?[a-z]+)?)\s*$', 'title_lex_format'),
        # [英文] Title - Guest Name (must have 2+ word name after dash)
        (r'[-–—]\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\s*$', 'title_dash_guest'),
        # 中文数字. 名字的...格式: "143. 对何小鹏的第二次访谈"
        # Handles: [数字]. 对[名字]的[主题] / [数字]. [名字]之[主题]
        (r'^(\d+)\.\s*(?:对)?([\u4e00-\u9fff]{2,4})(?:的|之)', 'title_cn_num_name'),
    ]
    for pat_str, pat_name in title_guest_patterns:
        m = re.match(pat_str, title.strip())
        if m:
            if pat_name == 'title_jre_mma':
                # Extract 2 guests: group(2)=first guest (before &), group(3)=second guest (after &)
                g1 = m.group(2).strip().strip('"').strip()
                g2 = m.group(3).strip().strip('"').strip() if m.group(3) else None
                for nr in [g1, g2]:
                    if nr:
                        name = _clean_name(nr)
                        if not _is_likely_noise(name) and not _is_host_name(name, podcast_name, hosts_config) and not _is_title_entity(name):
                            candidates.append({
                                "name": name,
                                "source": "title",
                                "pattern": pat_name,
                                "matched_text": title.strip()[:100],
                                "decision": "confirmed_guest",
                                "reason": "标题 JRE MMA 格式识别为嘉宾",
                            })
                continue
            # Skip title_dash_guest for JRE MMA (already handled by title_jre_mma pattern above)
            if pat_name == 'title_dash_guest' and title.strip().startswith('JRE MMA Show #'):
                continue
            if pat_name == 'title_lex_format':
                name = _clean_name(m.group(1).strip())
                if not _is_likely_noise(name) and not _is_host_name(name, podcast_name, hosts_config) and not _is_title_entity(name):
                    candidates.append({
                        "name": name,
                        "source": "title",
                        "pattern": pat_name,
                        "matched_text": title.strip()[:100],
                        "decision": "confirmed_guest",
                                "reason": "标题 Lex Fridman 格式识别为嘉宾",
                            })
                continue
            if pat_name == 'title_cn_num_name':
                name = _clean_name(m.group(2))
                if not _is_likely_noise(name) and not _is_host_name(name, podcast_name, hosts_config):
                    # ── 7b. 短中文名 → canonical 扩展（如"雨森"→"戴雨森"）─────
                    # 如果 title 识别出 2-3 字中文名，且 description/show_notes 中有"戴雨森"，
                    # 则用 canonical name 替换，并记录 alias
                    if len(name) <= 4:
                        canonical = None
                        search_text = (notes + " " + description).lower()
                        # 常见"雨森"类短名 → 全名映射
                        alias_map = {
                            "雨森": "戴雨森",
                            "小鹏": "何小鹏",
                            "马斯克": "埃隆·马斯克",
                            "佩奇": "拉里·佩奇",
                            "布林": "谢尔盖·布林",
                        }
                        alias_target = alias_map.get(name)
                        if alias_target and alias_target.lower() in search_text:
                            canonical = alias_target
                        # 通用：直接搜索"雨森XXX"（目标名在后的复合词），防止前缀干扰
                        # 例："雨森"（title匹配）但 notes 中出现"戴雨森"（canonical）
                        # 只保留"目标名 + 后缀"形式，不要含前缀的匹配结果
                        # ⚠️ 只对 ≤2 字短名做扩展（"雨森"=2，"小鹏"=2），≥3 字 Chinese name 通常已完整
                        if not canonical and len(name) <= 2:
                            for cname in re.findall(re.escape(name) + r'[\u4e00-\u9fff]{0,2}(?:先生|女士|博士|教授)?', notes + description):
                                if cname.endswith(("会怎", "认为", "也是", "多次")):
                                    continue
                                if cname != name and len(cname) >= len(name):
                                    canonical = cname
                                    break
                        # 如果 name 本身就是完整姓名（≥3字符），且未找到 canonical，不再扩展
                        # 直接使用 title 匹配的 name，不做强制 alias
                        resolved_name = canonical if canonical else name
                        resolved_name = canonical if canonical else name
                        reason_note = (
                            f"标题中文数字.名字格式识别为嘉宾（{name}为{resolved_name}的简称）"
                            if canonical and canonical != name
                            else "标题中文数字.名字格式识别为嘉宾"
                        )
                        candidates.append({
                            "name": resolved_name,
                            "source": "title",
                            "pattern": pat_name,
                            "matched_text": title.strip()[:100],
                            "decision": "confirmed_guest",
                            "reason": reason_note,
                            "_alias": name if (canonical and canonical != name) else None,
                        })
                    else:
                        candidates.append({
                            "name": name,
                            "source": "title",
                            "pattern": pat_name,
                            "matched_text": title.strip()[:100],
                            "decision": "confirmed_guest",
                            "reason": "标题中文数字.名字格式识别为嘉宾",
                        })
                    continue
            name = _clean_name(m.group(1).strip())
            if not _is_likely_noise(name) and not _is_host_name(name, podcast_name, hosts_config) and not _is_title_entity(name):
                candidates.append({
                    "name": name,
                    "source": "title",
                    "pattern": pat_name,
                    "matched_text": title.strip()[:100],
                    "decision": "confirmed_guest",
                    "reason": f"标题明确嘉宾格式「{pat_name}」",
                })

    # ── 8. 去重（保留最高 decision 优先级）────────────────────────────
    # 优先级：confirmed_guest > possible_guest > mentioned_entity > rejected_host > ambiguous
    decision_priority = {
        "confirmed_guest": 4,
        "possible_guest": 3,
        "mentioned_entity": 2,
        "rejected_host": 1,
        "ambiguous": 0,
    }
    seen = {}
    result = []
    for c in candidates:
        if is_guest_name_noise_phase2(c["name"]):
            continue
        norm = c["name"].lower()
        if norm not in seen:
            seen[norm] = c
            result.append(c)
        else:
            # 保留更高优先级的 decision
            existing = seen[norm]
            if decision_priority.get(c["decision"], 0) > decision_priority.get(existing["decision"], 0):
                seen[norm] = c

    return prune_redundant_guest_candidates_phase2(list(seen.values()))


def detect_guest_status_phase2(ep: dict) -> dict:
    """
    Phase 2 核心检测函数。返回：
      status: guest_detection_status 枚举之一
      guest_names: list[str] 识别到的嘉宾名（不管 status）
      evidence: list[dict] 结构化证据列表（每项: source/pattern/matched_text/decision/reason）
    """
    hosts_config = _load_podcast_hosts()
    candidates = extract_guest_names_phase2(ep, hosts_config)
    podcast_name = ep.get("podcast_name", "")

    # 过滤：rejected_host 不进入后续判断
    confirmed = [c for c in candidates if c["decision"] == "confirmed_guest"]
    possible = [c for c in candidates if c["decision"] == "possible_guest"]
    mentioned = [c for c in candidates if c["decision"] == "mentioned_entity"]

    if not candidates:
        # 标题中有名字格式（如 "X. Y的..."）但无明确 guest 标记
        title = ep.get("episode_title", "")
        m = re.match(r'^\d+\.\s*([A-Za-z]{2,30}?)\s*的', title)
        if m:
            name_in_title = m.group(1).strip()
            if not _is_likely_noise(name_in_title) and not _is_title_entity(name_in_title):
                if not _is_host_name(name_in_title, podcast_name, hosts_config):
                    return {
                        "status": "mentioned_entity",
                        "guest_names": [name_in_title],
                        "evidence": [{
                            "source": "title",
                            "pattern": "title_name_of",
                            "matched_text": title[:80],
                            "decision": "mentioned_entity",
                            "reason": f"标题出现'{name_in_title}'但无明确嘉宾标记",
                        }],
                    }
        return {
            "status": "no_guest_detected",
            "guest_names": [],
            "evidence": [{
                "source": "none",
                "pattern": "none",
                "matched_text": "",
                "decision": "no_guest_detected",
                "reason": "元数据中未发现明确嘉宾标记",
            }],
        }

    if len(confirmed) == 1:
        c = confirmed[0]
        # 检测 multi-guest（"with X and Y"）
        names = [c["name"]]
        matched_text = c["matched_text"]
        if re.search(r'\bwith\s+\S+\s+and\s+[A-Z][a-z]+', matched_text, re.IGNORECASE):
            extra = _parse_multi_guest(matched_text, c["name"])
            if len(extra) > 1:
                names = extra
        return {
            "status": "confirmed_guest",
            "guest_names": names,
            "evidence": [c] + ([possible[0]] if possible else []),
        }

    if len(confirmed) > 1:
        # 如果多个 confirmed 嘉宾都来自同一个 pattern（如 multi-speaker 或 JRE MMA 双嘉宾），不视为 ambiguous
        patterns = {c["pattern"] for c in confirmed}
        sources = {c["source"] for c in confirmed}
        if len(patterns) == 1 and len(sources) == 1:
            single_pat = list(patterns)[0]
            if "multi_speaker" in single_pat or single_pat == "title_jre_mma":
                return {
                    "status": "confirmed_guest",
                    "guest_names": [c["name"] for c in confirmed],
                    "evidence": confirmed[:3],
                }
        return {
            "status": "ambiguous",
            "guest_names": [c["name"] for c in confirmed],
            "evidence": confirmed[:3],
        }

    if len(possible) == 1 and not mentioned:
        c = possible[0]
        return {
            "status": "possible_guest",
            "guest_names": [c["name"]],
            "evidence": [c],
        }

    if len(mentioned) == 1 and not possible and not confirmed:
        c = mentioned[0]
        return {
            "status": "mentioned_entity",
            "guest_names": [c["name"]],
            "evidence": [c],
        }

    return {
        "status": "ambiguous",
        "guest_names": [c["name"] for c in candidates[:3]],
        "evidence": candidates[:3],
    }


# 兼容 Phase 1 接口
def detect_guest_status(ep: dict) -> dict:
    """Phase 1 兼容接口（返回 string evidence）"""
    result = detect_guest_status_phase2(ep)
    # 把 evidence list 转换为字符串
    evidence_str = " | ".join(
        f"[{e['source']}/{e['pattern']}] {e['reason']}" for e in result["evidence"]
    )
    return {
        "status": result["status"],
        "guest_names": result["guest_names"],
        "evidence": evidence_str,
    }


# ─────────────────────────────────────────────────────────────────────
# Step 2: 使用 OpenClaw web_search 搜索嘉宾背景
# ─────────────────────────────────────────────────────────────────────

def search_guest_background_openclaw(guest_name: str, affiliation_hint: str = "") -> list[dict]:
    """
    使用 OpenClaw 可用能力（subprocess 调用 openclaw）搜索嘉宾背景。
    返回搜索结果列表（dict: title, url, snippet）或有错误时返回空列表。
    Phase 2: 返回结果增加 source_quality 字段。
    """
    return search_adapter_search_guest_background_openclaw(guest_name, affiliation_hint)


# ─────────────────────────────────────────────────────────────────────
# Step 3: source_quality 分级（Phase 2）
# ─────────────────────────────────────────────────────────────────────

def classify_source_quality(result: dict) -> str:
    return source_quality_classify_source_quality(result)


def rate_overall_source_quality(results: list[dict]) -> dict:
    return source_quality_rate_overall_source_quality(results)


# ─────────────────────────────────────────────────────────────────────
# Step 4: 从 show_notes 中提取嘉宾身份信息（Phase 2 新增）
# ─────────────────────────────────────────────────────────────────────

def extract_guest_info_from_show_notes(guest_name: str, show_notes: str) -> dict:
    """
    从 episode show_notes / description 中提取该嘉宾的身份信息。
    只在 confirmed_guest 已成立时调用。
    返回 {text, patterns_found} 或空 dict。
    新增 source_type = 'episode_show_notes'。
    """
    if not show_notes or not guest_name:
        return {}
    name_lower = guest_name.lower()
    idx = -1
    # 找 show_notes 中包含该嘉宾名的位置
    try:
        idx = show_notes.lower().index(name_lower)
    except ValueError:
        # 尝试只姓或只名
        parts = name_lower.split()
        if len(parts) >= 2:
            first = parts[0]
            last = parts[-1]
            for p in [first, last]:
                try:
                    idx = show_notes.lower().index(p)
                    break
                except ValueError:
                    continue
        if idx < 0:
            return {}

    # 提取该位置前后最多 300 字作为上下文
    ctx_start = max(0, idx - 100)
    ctx_end = min(len(show_notes), idx + 200)
    context = show_notes[ctx_start:ctx_end]

    patterns_found = []

    # 匹配身份信息模式
    # 1. 职位/头衔（中英文）
    title_patterns = [
        # 中文职务
        r'(?:、|,|\s)([A-Za-z\u4e00-\u9fff]{1,20}(?:合伙人|创始人|CEO|CTO|CFO|COO|VP|董事|基金经理|研究总监|总裁|副总裁|教授|研究员|作家|调查记者|制片人|导演|顾问))(?:$|[，,，\s])',
        r'(?:^|\s)([A-Za-z\u4e00-\u9fff]{1,20}(?:合伙人|创始人|CEO|CTO|CFO|COO|VP|董事|基金经理|研究总监|总裁|副总裁|教授|研究员|作家|调查记者|制片人|导演|顾问))(?:$|[，,，\s])',
        # 英文职务（co-founder, CEO, etc.）
        r'\b(co-?founder|founder|co-?creator)\b',
        r'\b(CEO|CTO|CFO|COO|VP|general\s+partner)\b',
        r'\b(author|writer|investor|researcher|scientist|engineer)\b',
    ]
    for pat in title_patterns:
        for m in re.finditer(pat, context, re.IGNORECASE):
            val = m.group(1).strip()
            if val and len(val) >= 2:
                patterns_found.append(f"职务/头衔：{val}")

    # 2. 机构/公司名（中英文）
    org_patterns = [
        # 中文公司后缀（允许后面跟 的）
        r'(?:在|是|为|氏)\s*([A-Za-z\u4e00-\u9fff]{2,30}?(?:资本|基金|投资|集团|公司|研究所|实验室|大学|学院|媒体|出版社|机构))(?:$|[，,，\s]|(?=的))',
        r'@([A-Za-z\u4e00-\u9fff]{2,30}?(?:资本|基金|投资|集团|公司|研究所|实验室|大学|学院|媒体|出版社|机构))(?:$|[@，,，\s])',
        # 英文公司名（Capital/Fund/Investment/etc.结尾，不加尾部\b以避免被中文字符阻断）
        r'\b([A-Za-z]{1,20}\s+[A-Za-z]{1,20}?\s*(?:Capital|Fund|Investment|Group|Corp|Ventures|Partners|Horowitz))\b',
        # "X Capital的Y" 型（处理 "Altimeter Capital的合伙人"）
        r'([A-Za-z]{2,20}\s+Capital|Fund|Investment|Group|Ventures|Partners|Horowitz)(?=[\s的]|$)',
        # "是X Capital的Y" → 提取 X Capital 作为公司
        r'(?:是|在|为)\s*([A-Za-z]{2,30}\s+Capital|Fund|Investment|Group|Ventures|Partners|Horowitz)\b(?=[\s的])',
        # "co-founder of X" / "CEO of X" → extract X as company
        r'(?:co-?founder|founder|CEO|CTO|COO|CFO|VP|president|partner)\s+of\s+([A-Za-z][A-Za-z0-9&]{3,30}(?:\s+[A-Za-z][A-Za-z0-9&]{2,15}){0,2})',
        # (a16Z) 风格括号公司名
        r'\(([a-z]\d{1,2}[a-z])\)',
    ]
    # 停用词表（英语常见非公司词组）
    EN_ORG_STOPWORDS = {
        "this episode", "the episode", "an episode", "our episode",
        "today show", "the show", "our show", "the podcast",
        "new episode", "latest episode", "previous episode",
        "of andreessen", "of horowitz", "of the", "of this",
    }
    for pat in org_patterns:
        for m in re.finditer(pat, context, re.IGNORECASE):
            val = m.group(1).strip()
            if val and not _is_likely_noise(val) and is_safe_guest_org_candidate(val):
                if val.lower() not in EN_ORG_STOPWORDS and not val.lower().startswith("of "):
                    patterns_found.append(f"机构/公司：{val}")
                    break  # 只取第一个，避免泛化

    # 3. 职位/头衔补充（英文 "is X of Y" / "is a X" 型）
    en_title_org_patterns = [
        # "is partner at/in X" / "is co-founder of X"
        r'\b(?:is|was)\s+(?:a\s+)?(?:co-?)?founder\s+of\s+([A-Za-z][A-Za-z0-9\s&]{3,30}(?:\s+[A-Za-z][A-Za-z0-9]{2,15}){0,2})\b',
        r'\b(?:is|was)\s+(?:a\s+)?(?:partner|executive|director)\s+(?:at|in|of)\s+([A-Za-z][A-Za-z0-9\s&]{3,30}(?:\s+[A-Za-z][A-Za-z0-9]{2,15}){0,2})\b',
        # "is the X of Y" (e.g., "is the CEO of Hugging Face")
        r'\b(?:is|was)\s+(?:the\s+)?(CEO|CTO|COO|CFO|president|founder|partner)\s+(?:of|at)\s+([A-Za-z][A-Za-z0-9\s&]{3,30}(?:\s+[A-Za-z][A-Za-z0-9]{2,15}){0,2})\b',
    ]
    for pat in en_title_org_patterns:
        for m in re.finditer(pat, context, re.IGNORECASE):
            title_val = m.group(1).strip()
            org_val = m.group(2).strip()
            if title_val and len(title_val) >= 2:
                patterns_found.append(f"职务/头衔：{title_val}")
            if org_val and not _is_likely_noise(org_val) and is_safe_guest_org_candidate(org_val):
                if org_val.lower() not in EN_ORG_STOPWORDS:
                    patterns_found.append(f"机构/公司：{org_val}")
            if title_val or org_val:
                break  # 只取第一个匹配

    # 3b. 中文/英文混合型公司+职务："X的title"（处理 "Altimeter Capital的合伙人"）
    cn_en_mixed_patterns = [
        # 公司名（英文）+ 的 + 职务（中文）
        r'([A-Za-z]{2,20}\s+Capital|Fund|Investment|Group|Ventures|Partners|Horowitz)的(合伙人|创始人)',
        r'([A-Za-z]{2,20}\s+Capital|Fund|Investment|Group|Ventures|Partners|Horowitz)的(CEO|CTO|COO|CFO)',
    ]
    for pat in cn_en_mixed_patterns:
        for m in re.finditer(pat, context, re.IGNORECASE):
            org_val = m.group(1).strip()
            title_val = m.group(2).strip()
            if org_val and not _is_likely_noise(org_val) and is_safe_guest_org_candidate(org_val):
                patterns_found.append(f"机构/公司：{org_val}")
            if title_val and len(title_val) >= 2:
                patterns_found.append(f"职务/头衔：{title_val}")
            if org_val or title_val:
                break

    # 3c. 中文机构+职务连写："X基金经理"（无停顿的结构，如"南方基金基金经理"）
    cn_title_adjacent_patterns = [
        r'([A-Za-z\u4e00-\u9fff]{2,20}?基金)(基金经理|研究总监|教授|研究员|作家|调查记者|制片人|导演|顾问)',
        r'([A-Za-z\u4e00-\u9fff]{2,20}?资本|投资|集团|研究所|实验室)(基金经理|研究总监|教授|研究员|作家|调查记者|制片人|导演|顾问)',
    ]
    for pat in cn_title_adjacent_patterns:
        for m in re.finditer(pat, context):
            org_val = m.group(1).strip()
            title_val = m.group(2).strip()
            if org_val and not _is_likely_noise(org_val) and is_safe_guest_org_candidate(org_val):
                patterns_found.append(f"机构/公司：{org_val}")
            if title_val and len(title_val) >= 2:
                patterns_found.append(f"职务/头衔：{title_val}")
            if org_val or title_val:
                break

    # 4. 研究方向/专长
    research_patterns = [
        r'(?:专注|研究|擅长|主攻|从事)\s*([^，。、；!?\n]{4,40})',
        r'(?:研究方向|专长|领域)\s*[：:]\s*([^，。、；!?\n]{4,40})',
    ]
    for pat in research_patterns:
        for m in re.finditer(pat, context):
            val = m.group(1).strip()
            if val and len(val) >= 4:
                patterns_found.append(f"专长/研究：{val}")

    # 4. 代表著作/产品/案例
    book_patterns = [
        r'《([^》]{2,30})》',
        r'(?:著作|作品|代表作|作者)\s*[：:]\s*([A-Za-z\u4e00-\u9fff]{2,40})',
        r'(?:投资案例|代表案例)\s*[：:]\s*([A-Za-z\u4e00-\u9fff]{2,40})',
        r'\b(Missing\s*\d+)\b',
    ]
    for pat in book_patterns:
        for m in re.finditer(pat, context, re.IGNORECASE):
            val = m.group(1).strip()
            if val and len(val) >= 2:
                patterns_found.append(f"著作/案例：{val}")

    if not patterns_found:
        return {}

    patterns_found = list(dict.fromkeys(patterns_found))

    return {
        "text": context,
        "patterns_found": patterns_found,
        "source_type": "episode_show_notes",
        "source_quality": "primary",
    }


# ─────────────────────────────────────────────────────────────────────
# Step 4b: 使用 OpenClaw 模型生成背景（Phase 2 升级，支持 show_notes）
# ─────────────────────────────────────────────────────────────────────

def generate_background_via_model_phase2(guest_name: str, sources: list[dict],
                                          show_notes_info: dict = None) -> dict:
    """
    Phase 2: 使用 OpenClaw 当前可用模型能力生成嘉宾背景描述。
    sources: list of {title, url, snippet, quality}
    show_notes_info: optional dict from extract_guest_info_from_show_notes()
    返回 {background_zh: str, confidence: str, note: str, source_quality: str,
          source_quality_summary: str}
    """
    # 优先使用 show_notes_info（source_quality = primary）
    if show_notes_info and show_notes_info.get("patterns_found"):
        show_notes_result = generate_background_from_show_notes(
            guest_name,
            sources,
            show_notes_info,
        )
        if show_notes_result is not None:
            return show_notes_result

    if not sources:
        return {
            "background_zh": "未找到足够可靠的公开资料，暂不补充背景。",
            "sources": [],
            "confidence": "unknown",
            "note": "搜索失败，无来源",
            "source_quality": "weak",
            "source_quality_summary": "weak",
        }

    # Phase 2: source_quality 评估
    sq = rate_overall_source_quality(sources)

    # Phase 2: Wikipedia only 检测
    wiki_only = all(
        "wikipedia" in r.get("title", "").lower() or "wiki" in r.get("url", "").lower()
        for r in sources if r.get("title") or r.get("url")
    )

    # Phase 2: 高质量 prompt（字数限制，结构化要求）
    prompt = build_background_prompt(guest_name, sources)

    try:
        model_output = run_background_model(prompt)
        if model_output and should_accept_model_background(model_output):
            # Phase 2: confidence 规则
            if sq["quality"] == "primary":
                confidence = "high"
            elif sq["quality"] == "secondary":
                confidence = "medium" if len(model_output) >= 40 else "low"
            else:
                confidence = "low"
            return {
                "background_zh": model_output,
                "confidence": confidence,
                "note": "OpenClaw 模型生成",
                "source_quality": sq["quality"],
                "source_quality_summary": sq["quality"],
            }
        if model_output and len(model_output) > 5 and ("信息不足" in model_output or "无法生成" in model_output):
            confidence = "low" if sq["quality"] == "secondary" else "unknown"
            return {
                "background_zh": "未找到足够可靠的公开资料，暂不补充背景。",
                "confidence": confidence,
                "note": "搜索结果不足以生成可靠背景",
                "source_quality": sq["quality"],
                "source_quality_summary": sq["quality"],
            }
        if model_output and len(model_output) > 5 and len(model_output) < 20:
            return {
                "background_zh": "未找到足够可靠的公开资料，暂不补充背景。",
                "confidence": "low",
                "note": "模型输出过短，可能是 Wikipedia 标题而非描述",
                "source_quality": sq["quality"],
                "source_quality_summary": sq["quality"],
            }
    except Exception:
        pass

    # 降级
    if sq["quality"] == "primary":
        confidence = "medium"
    elif sq["quality"] == "secondary":
        confidence = "low"
    else:
        confidence = "unknown"
    return {
        "background_zh": "未找到足够可靠的公开资料，暂不补充背景。",
        "confidence": confidence,
        "note": "模型生成不可用，降级处理",
        "source_quality": sq["quality"],
        "source_quality_summary": sq["quality"],
    }


# ─────────────────────────────────────────────────────────────────────
# Step 5: 主流程（Phase 2）
# ─────────────────────────────────────────────────────────────────────

def _build_cache_key(guest_name: str, podcast_title: str, affiliation_hint: str = "") -> str:
    return _guest_key(guest_name, affiliation_hint, podcast_title)


def get_guest_background_phase2(guest_name: str, podcast_title: str = "",
                                  affiliation_hint: str = "",
                                  show_notes: str = "") -> dict:
    """
    Phase 2 主入口：检查缓存 → 有则返回 → 无则搜索+生成 → 写缓存 → 返回。
    任何步骤失败均返回降级默认值，不抛出异常。

    show_notes: episode show_notes text，优先从中提取嘉宾身份信息（source_type = episode_show_notes）。
    """
    if not guest_name or len(guest_name.strip()) < 2:
        return {
            "background_zh": "未能从节目元数据中确认本期嘉宾，暂不补充背景。",
            "sources": [],
            "confidence": "unknown",
            "note": "嘉宾名解析失败",
            "source_quality": "weak",
        }

    key = _build_cache_key(guest_name.strip(), podcast_title, affiliation_hint)
    cached = _cache_entry(key)
    if cached:
        return {
            "background_zh": cached.get("background_zh", ""),
            "sources": cached.get("sources", []),
            "confidence": cached.get("confidence", "unknown"),
            "note": cached.get("note", "来自缓存"),
            "source_quality": cached.get("source_quality_summary", "unknown"),
        }

    # ── 1. 优先从 show_notes 中提取嘉宾身份信息 ─────────────────────────
    sn_info = {}
    if show_notes:
        sn_info = extract_guest_info_from_show_notes(guest_name.strip(), show_notes)

    # 搜索（show_notes 有足够信息时仍搜索，但 show_notes 优先）
    search_results = search_guest_background_openclaw(guest_name, affiliation_hint)
    sources = []
    for r in search_results:
        sq = classify_source_quality(r)
        sources.append({
            "title": r["title"],
            "url": r["url"],
            "snippet": r["snippet"],
            "quality": sq,
        })

    # ── 2. 如果 show_notes 有身份信息，直接生成背景 ──────────────────────
    if sn_info.get("patterns_found"):
        gen_result = generate_background_via_model_phase2(guest_name, sources, sn_info)
        result = {
            "background_zh": gen_result["background_zh"],
            "sources": gen_result.get("sources", sources[:5]),
            "confidence": gen_result["confidence"],
            "note": gen_result["note"],
            "source_quality": gen_result.get("source_quality", "primary"),
            "source_quality_summary": gen_result.get("source_quality_summary", "primary (episode_show_notes)"),
        }
        _write_cache_entry(key, {
            "guest_name": guest_name.strip(),
            "detection_status": "confirmed_guest",
            **result,
        })
        return result

    # ── 3. 只有 weak 来源时降级 ────────────────────────────────────────────
    sq = rate_overall_source_quality(sources)
    if sq["quality"] == "weak":
        result = {
            "background_zh": "未找到足够可靠的公开资料，暂不补充背景。",
            "sources": sources[:5],
            "confidence": "low",
            "note": "仅有弱来源（聚合页/标题片段），无法生成可靠背景",
            "source_quality": "weak",
            "source_quality_summary": "weak",
        }
        _write_cache_entry(key, {
            "guest_name": guest_name.strip(),
            "detection_status": "confirmed_guest",
            **result,
        })
        return result

    # 生成
    gen_result = generate_background_via_model_phase2(guest_name, sources, None)
    result = {
        "background_zh": gen_result["background_zh"],
        "sources": sources[:5],
        "confidence": gen_result["confidence"],
        "note": gen_result["note"],
        "source_quality": gen_result.get("source_quality", sq["quality"]),
        "source_quality_summary": gen_result.get("source_quality_summary", sq["quality"]),
    }
    _write_cache_entry(key, {
        "guest_name": guest_name.strip(),
        "detection_status": "confirmed_guest",
        **result,
    })
    return result


# 兼容 Phase 1
def get_guest_background(guest_name: str, podcast_title: str = "",
                          affiliation_hint: str = "") -> dict:
    return get_guest_background_phase2(guest_name, podcast_title, affiliation_hint)


# ─────────────────────────────────────────────────────────────────────
# Step 6: 批量处理（对外接口）
# ─────────────────────────────────────────────────────────────────────

def enrich_episode_with_guest_backgrounds(ep: dict) -> dict:
    """
    Phase 2：给定 episode dict，检测嘉宾状态，只对 confirmed_guest 查询背景。
    返回增加了 guest_detection_status, guest_names, guest_background_* 字段的 episode copy。
    evidence 字段升级为 list[dict] 结构（Phase 2），同时也提供 guest_detection_evidence 字符串兼容字段。
    """
    ep = dict(ep)
    hosts_config = _load_podcast_hosts()

    # Phase 2: 使用新的检测函数
    detection = detect_guest_status_phase2(ep)
    ep["guest_detection_status"] = detection["status"]
    ep["guest_names"] = detection["guest_names"]
    # Phase 2: 结构化 evidence（list）和字符串 evidence（兼容）
    ep["guest_detection_evidence"] = detection["evidence"]
    podcast_title = ep.get("podcast_name", "")
    guest_names = detection["guest_names"]
    show_notes = ep.get("show_notes_text", "") or ep.get("show_notes", "") or ep.get("description", "") or ""

    # 默认降级值
    ep["guest_background_zh"] = "未能从节目元数据中确认本期嘉宾，暂不补充背景。"
    ep["guest_background_sources"] = []
    ep["guest_background_confidence"] = "unknown"
    ep["guest_background_note"] = ""
    ep["source_quality_summary"] = "unknown"

    if detection["status"] != "confirmed_guest":
        status_notes = {
            "no_guest_detected": "未识别到嘉宾",
            "mentioned_entity": f"'{guest_names[0]}'是节目讨论对象，非本期嘉宾" if guest_names else "讨论对象非本期嘉宾",
            "possible_guest": f"'{guest_names[0]}'可能是嘉宾但证据不足，默认不补" if guest_names else "可能是嘉宾但证据不足",
            "ambiguous": f"多候选嘉宾 {guest_names}，同名风险，默认不补",
        }
        ep["guest_background_note"] = status_notes.get(detection["status"], "")
        return ep

    # confirmed_guest：查询每位嘉宾背景（最多2位）
    bg_parts = []
    all_sources = []
    notes_parts = []
    confidence_set = set()

    for gname in guest_names[:2]:  # 最多2位
        bg = get_guest_background_phase2(gname, podcast_title, show_notes=show_notes)
        # Phase 2: 背景长度限制（不超过300字）
        bg_text = bg["background_zh"]
        if len(bg_text) > 300:
            bg_text = bg_text[:297] + "..."
        bg_parts.append(bg_text)
        all_sources.extend(bg["sources"])
        confidence_set.add(bg["confidence"])
        notes_parts.append(bg["note"])

    # 综合 confidence
    if "high" in confidence_set:
        overall_conf = "high"
    elif "medium" in confidence_set:
        overall_conf = "medium"
    elif "low" in confidence_set:
        overall_conf = "low"
    else:
        overall_conf = "unknown"

    ep["guest_background_zh"] = "；".join(bg_parts)
    ep["guest_background_sources"] = all_sources[:5]
    ep["guest_background_confidence"] = overall_conf
    ep["guest_background_note"] = " | ".join(notes_parts)
    # 综合 source_quality_summary（取最高质量）
    sq_set = set()
    for src in all_sources[:5]:
        q = src.get("quality", "secondary")
        sq_set.add(q)
    if "primary" in sq_set:
        ep["source_quality_summary"] = "primary (episode_show_notes)"
    elif "secondary" in sq_set:
        ep["source_quality_summary"] = "secondary"
    else:
        ep["source_quality_summary"] = "weak"

    return ep


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python guest_background_fetcher.py <guest_name>")
        sys.exit(1)
    result = get_guest_background(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False, indent=2))
