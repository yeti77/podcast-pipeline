#!/usr/bin/env python3
"""
podcast_screener.py v2.3 — 播客每周筛选器
v2.3（基于 v2.2）：
  结构化多维评分 v3.0：新增 topic_relevance/information_density/novelty/actionability/strategic_value/transcription_value 六个维度
  综合分改为 0-100 区间，阈值相应调整为 full≥75/preview≥45/skip<25
  reason_zh/uncertainty_zh 中文输出，质量检查函数 quality_check_episode()
  policy.yaml score_policy 阈值同步更新为 0-100 量表
"""

import sys
import os
import json
import re
import html
import yaml
import glob
import hashlib
import argparse
import subprocess
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Optional
from pipeline_paths import ensure_runtime_directories, get_pipeline_paths
from policy_config import load_policy_config
from latest_result_store import update_latest_pointers as store_update_latest_pointers
from episode_duration import format_episode_duration
from episode_show_notes_renderer import (
    SHOW_NOTES_DISPLAY_SNAPSHOT_VERSION,
    SHOW_NOTES_TRANSLATED_HEADING,
    build_show_notes_display_result,
)
from show_notes_openclaw_translation_runner import (
    DEFAULT_OPENCLAW_SHOW_NOTES_TRANSLATION_MODEL,
    DEFAULT_OPENCLAW_TRANSLATION_TIMEOUT_SECONDS,
    translate_show_notes_chunk_with_openclaw,
)
from show_notes_translation_runner import (
    MOCK_SHOW_NOTES_TRANSLATION_MODEL,
    mock_translate_show_notes_chunk,
)
from rss_adapter import (
    SHOW_NOTES_MAX_CHARS,
    _parse_jsonld_episodes_common as rss_parse_jsonld_episodes_common,
    clean_show_notes as rss_clean_show_notes,
    extract_jsonld as rss_extract_jsonld,
    fetch_feed as rss_fetch_feed,
    parse_duration_to_minutes as rss_parse_duration_to_minutes,
    parse_rss_episodes as rss_parse_rss_episodes,
    select_best_show_notes as rss_select_best_show_notes,
)

TZ_SH = ZoneInfo("Asia/Shanghai")

_RUNTIME_PATHS = get_pipeline_paths()
PIPELINE_DIR = str(_RUNTIME_PATHS.pipeline_dir)
CONFIG_DIR = str(_RUNTIME_PATHS.config_dir)
OUTPUT_DIR = str(_RUNTIME_PATHS.outputs_dir)
STATE_DIR = str(_RUNTIME_PATHS.state_dir)
RUNS_OUT_DIR = str(_RUNTIME_PATHS.runs_dir)
LATEST_DIR = OUTPUT_DIR

TZ_SH = ZoneInfo("Asia/Shanghai")

# ── 嘉宾背景展示安全常量 ───────────────────────────────────────────────
CONFIRMED_GUEST_FALLBACK = "已确认本期嘉宾，但节目元数据未提供足够背景信息，暂不补充背景。"
FALLBACK_PATTERNS = {
    CONFIRMED_GUEST_FALLBACK,
    "未找到足够可靠的公开资料，暂不补充背景。",
    "未能从节目元数据中确认本期嘉宾，暂不补充背景。",
    "未能从节目元数据中确认本期嘉宾，暂不补充背景。",
}
SHOW_NOTES_TRANSLATION_DEFAULT_CONFIG = {
    "enabled": False,
    "mode": "mock",
    "target_language": "zh",
    "cache_enabled": True,
    "cache_dir": "cache/show_notes_translations",
    "model": MOCK_SHOW_NOTES_TRANSLATION_MODEL,
    "max_chunk_chars": 1800,
    "timeout_seconds": DEFAULT_OPENCLAW_TRANSLATION_TIMEOUT_SECONDS,
}
NOISE_GUEST_TITLES = {
    "author", "writer", "host", "podcaster", "creator",
    "作家", "作者", "主持人", "主播", "创作者",
    "founder", "cofounder",  # 没有具体机构时，这些职务是噪音
}
GUEST_TOPIC_PHRASES = {
    "reshaping venture capital", "venture capital", "private markets",
    "software repricing", "capital allocation", "capital allocation strategy",
    "investment strategy", "market commentary", "fund performance",
}


def is_fallback_background(text: str) -> bool:
    """判断背景文本是否属于 fallback（不足以展示详细信息）。"""
    if not text:
        return True
    if text in FALLBACK_PATTERNS:
        return True
    # 如果是重复的 fallback 文案拼接（如 ";已确认本期嘉宾...；已确认本期嘉宾..."），也算 fallback
    if CONFIRMED_GUEST_FALLBACK in text and ("；" in text or ";" in text):
        # 计算 fallback 出现次数（排除单纯的重复分隔符干扰）
        # 如果 guest_background_zh 中 fallback 出现次数 >= 1 且包含分隔符，说明是拼接的 fallback
        parts = re.split(r'[；;]', text)
        if all(is_fallback_background(p.strip()) for p in parts if p.strip()):
            return True
    return False


def is_guest_source_noise(source: dict) -> bool:
    """判断嘉宾 source 是否为噪音，不值得展示。"""
    snippet = source.get("snippet", "")
    if not snippet:
        return True
    # 检查 topic phrase 混入
    snippet_lower = snippet.lower()
    if any(tp in snippet_lower for tp in GUEST_TOPIC_PHRASES):
        return True
    # 去掉前缀后检查噪音词
    clean = snippet.replace("职务/头衔：", "").replace("机构/公司：", "").strip().lower()
    parts = [p.strip() for p in re.split(r'[/,;|]', clean) if p.strip()]
    if not parts:
        return True
    # 如果所有原始 part 都是噪音词，整个 source 不展示
    if all(p in NOISE_GUEST_TITLES for p in parts):
        return True
    # 过滤噪音词后看剩余内容
    meaningful = [p for p in parts if p not in NOISE_GUEST_TITLES]
    if len(meaningful) == 0:
        return True
    return False


# ── 日志 ──────────────────────────────────────────────────────────────
LOG_FILE = os.path.join(PIPELINE_DIR, "screener_cron.log")
STDERR_FILE = os.path.join(PIPELINE_DIR, "screener_stderr.log")
RUN_ID = datetime.now(TZ_SH).strftime("%Y%m%d_%H%M%S")
RUN_TS = datetime.now(TZ_SH).strftime("%Y-%m-%dT%H:%M:%S%z")  # 真实时间戳（fix-3）


def log(msg: str):
    ts = datetime.now(TZ_SH).strftime("%Y-%m-%d %H:%M:%S %Z")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def log_stderr(msg: str, level: str = "ERROR"):
    ts = datetime.now(TZ_SH).strftime("%Y-%m-%d %H:%M:%S %Z")
    line = f"[{ts}] [{level}] [{RUN_ID}] {msg}"
    print(line, flush=True)
    with open(STDERR_FILE, "a") as f:
        f.write(line + "\n")


def clear_stderr():
    with open(STDERR_FILE, "w") as f:
        f.write(f"[{RUN_ID}] === RUN START ===\n")


def log_run_boundary():
    log_stderr(f"=== RUN {RUN_ID} BOUNDARY ===", "INFO")


# ── 业务周窗口（v2.2）───────────────────────────────────────────────────
def get_business_week_window(now_dt: datetime = None) -> tuple:
    """
    返回 (window_start, window_end, week_id)

    业务周定义：
      - 切分点：每周日 22:00:00（开区间边界，该时刻属于下一周）
      - 扫描"最近已完成的业务周窗口"

    算法（找到最近一个 <= now_dt 的周日 22:00:00 作为 window_end）：
      1. days_since_sunday = (weekday - 6) % 7，即今天距离最近周日的天数
      2. candidate_end = now - days_since_sunday → 最近周日 00:00
      3. candidate_end.replace(hour=22) → 最近周日 22:00
      4. 若 candidate_end > now_dt（本周日 22:00 还未到），再减 7 天取上一周日
      5. window_end = candidate_end，window_start = window_end - 7 days

    week_id 计算（不能用 window_start 直接的 ISO week）：
      - 因为 window_start 是周日 22:00，直接取 ISO week 会落到上一周
      - anchor = window_start + 2h → 再取 ISO week，得到正确的业务周编号

    episode_in_window 过滤规则：
      published_at >= window_start AND published_at < window_end（开区间）

    期望行为（Asia/Shanghai 时区）：
      2026-05-04 09:00 Mon  → W18 | Apr 26 22:00 → May 3 22:00
      2026-05-10 21:59:59   → W18 | Apr 26 22:00 → May 3 22:00
      2026-05-10 22:00:00   → W19 | May 3 22:00 → May 10 22:00
      2026-05-10 23:26      → W19 | May 3 22:00 → May 10 22:00
      2026-05-11 09:00 Mon  → W19 | May 3 22:00 → May 10 22:00
      2026-05-17 21:59:59   → W19 | May 3 22:00 → May 10 22:00
      2026-05-17 22:00:00   → W20 | May 10 22:00 → May 17 22:00
    """
    if now_dt is None:
        now_dt = datetime.now(TZ_SH)
    else:
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=TZ_SH)
        else:
            now_dt = now_dt.astimezone(TZ_SH)

    # Python weekday: Monday=0, Sunday=6
    # (weekday - 6) % 7 gives 0 for Sunday, 1 for Monday, ..., 6 for Saturday
    days_since_sunday = (now_dt.weekday() - 6) % 7

    candidate_end = (now_dt - timedelta(days=days_since_sunday)).replace(
        hour=22, minute=0, second=0, microsecond=0
    )

    # 若本周日 22:00 还没到（> now_dt），则取上一周日
    if candidate_end > now_dt:
        candidate_end -= timedelta(days=7)

    window_end = candidate_end
    window_start = window_end - timedelta(days=7)

    # week_id：用 window_start + 2h 的 ISO week（避免周日 22:00 落在上一周）
    anchor = window_start + timedelta(hours=2)
    iso_year, iso_week, _ = anchor.isocalendar()
    week_id = f"{iso_year}W{iso_week:02d}"

    return window_start, window_end, week_id


def business_week_id(window_start: datetime) -> str:
    """给定 window_start，计算其 business week id（用 window_start + 2h 的 ISO week）"""
    anchor = window_start + timedelta(hours=2)
    iso_year, iso_week, _ = anchor.isocalendar()
    return f"{iso_year}W{iso_week:02d}"


def validate_week_id(window_start: datetime, week_id: str) -> bool:
    """校验 week_id 与 window_start 是否自洽（fail-fast 核心）"""
    expected = business_week_id(window_start)
    return expected == week_id


def validate_output_dir_and_json(dir_name_week_id: str, json_week_id: str) -> bool:
    """校验输出目录名 week_id 与 JSON 中 week_id 是否一致"""
    return dir_name_week_id == json_week_id


# ── Episode ID ─────────────────────────────────────────────────────────
def make_episode_id(podcast_id: str, publish_date: str, episode_title: str) -> str:
    title_words = re.findall(r'[\w\u4e00-\u9fff]+', episode_title)
    stopwords = {'the','a','an','of','in','on','at','to','for','and','or','but','is','are','was','were','这','的','是','在','和','了','我','你','他','她','它','我们','你们','他们','她们','它们','之','与','或','但','为','而','于','从','到','把','被','让','给','向','跟','用','把'}
    words = [w for w in title_words if w.lower() not in stopwords and len(w) > 1]
    keyword = "_".join(words[:3]).lower()
    title_hash = hashlib.sha1(episode_title.encode()).hexdigest()[:8]
    date_str = publish_date.replace("-", "")
    return f"{podcast_id}_{date_str}_{keyword}_{title_hash}"


# ── show_notes HTML 彻底清洗（fix-2）──────────────────────────────────
def clean_show_notes(text: str) -> str:
    return rss_clean_show_notes(text)


# ── 选择最完整 show_notes（RSS Ingestion Fix）──────────────────────────
def select_best_show_notes(entry: dict) -> dict:
    return rss_select_best_show_notes(entry)


# ── 配置加载 ─────────────────────────────────────────────────────────
def load_yaml(path: str) -> Any:
    with open(path) as f:
        return yaml.safe_load(f)


def load_configs():
    log("CONFIG_LOADED")
    podcasts = load_yaml(os.path.join(CONFIG_DIR, "podcasts.yaml"))
    interests = load_yaml(os.path.join(CONFIG_DIR, "interests.yaml"))
    policy = load_policy_config(os.path.join(CONFIG_DIR, "policy.yaml"))
    return podcasts, interests, policy


def get_show_notes_translation_config(policy: Optional[dict]) -> dict:
    """Return show-notes translation config with safe default-off values."""
    config = dict(SHOW_NOTES_TRANSLATION_DEFAULT_CONFIG)
    if not isinstance(policy, dict):
        return config
    configured = policy.get("show_notes_translation")
    if isinstance(configured, dict):
        config.update(configured)
    return config


def get_current_git_commit(*, run_command=subprocess.run) -> str:
    """Return the current short commit for run diagnostics without failing the run."""
    try:
        completed = run_command(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=PIPELINE_DIR,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return "unknown"
    if getattr(completed, "returncode", 1) != 0:
        return "unknown"
    return str(getattr(completed, "stdout", "") or "").strip() or "unknown"


def build_runtime_metadata(policy: Optional[dict], *, git_commit: Optional[str] = None) -> dict:
    """Build a non-secret runtime snapshot for persisted run diagnostics."""
    config = get_show_notes_translation_config(policy)
    return {
        "git_commit": git_commit or get_current_git_commit(),
        "show_notes_translation": {
            "enabled": bool(config.get("enabled")),
            "mode": str(config.get("mode") or ""),
            "agent_id": str(config.get("agent_id") or ""),
            "model": str(config.get("model") or ""),
        },
    }


def store_show_notes_display_metadata(episode: object, metadata: object) -> None:
    """Attach JSON-safe display diagnostics to an episode result record."""
    if isinstance(episode, dict) and isinstance(metadata, dict):
        snapshot = metadata.get("display_snapshot")
        episode["show_notes_display_metadata"] = {
            key: value for key, value in metadata.items() if key != "display_snapshot"
        }
        if isinstance(snapshot, dict):
            episode["show_notes_display_snapshot"] = snapshot


def build_show_notes_translation_summary(result_data: object) -> dict:
    """Aggregate bounded run-level translation health from episode diagnostics."""
    payload = result_data if isinstance(result_data, dict) else {}
    summary = {
        "episode_count": 0,
        "eligible_count": 0,
        "translated_count": 0,
        "partial_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "cache_hit_count": 0,
        "visible_translation_count": 0,
        "failed_episodes": [],
    }
    translated_statuses = {"translated", "cache_hit"}
    partial_statuses = {"partial_translated", "partial_cache_hit"}

    for bucket in ("full", "preview", "skip"):
        for episode in payload.get(bucket, []) or []:
            if not isinstance(episode, dict):
                continue
            summary["episode_count"] += 1
            metadata = episode.get("show_notes_display_metadata") or {}
            translation = metadata.get("translation") or {}
            should_translate = bool(translation.get("should_translate"))
            status = str(translation.get("status") or "unknown")
            if not should_translate:
                summary["skipped_count"] += 1
                continue

            summary["eligible_count"] += 1
            if translation.get("cache_hit"):
                summary["cache_hit_count"] += 1
            if status in translated_statuses:
                summary["translated_count"] += 1
                summary["visible_translation_count"] += 1
            elif status in partial_statuses:
                summary["partial_count"] += 1
                summary["visible_translation_count"] += 1
            else:
                summary["failed_count"] += 1
                if len(summary["failed_episodes"]) < 20:
                    summary["failed_episodes"].append({
                        "podcast": str(episode.get("podcast_name") or episode.get("podcast") or ""),
                        "title": str(episode.get("episode_title") or episode.get("title") or ""),
                        "status": status,
                    })
    return summary


def _coerce_positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def build_show_notes_translation_render_options(
    policy: Optional[dict],
    *,
    mock_translate_chunk=mock_translate_show_notes_chunk,
    openclaw_translate_chunk=None,
) -> tuple:
    """Build renderer options for display-only show-notes translation."""
    config = get_show_notes_translation_config(policy)
    if config.get("enabled") is not True:
        return False, {}
    mode = str(config.get("mode", "")).strip().lower()
    if mode not in {"mock", "openclaw"}:
        return False, {}

    max_chunk_chars = _coerce_positive_int(
        config.get("max_chunk_chars"),
        SHOW_NOTES_TRANSLATION_DEFAULT_CONFIG["max_chunk_chars"],
    )
    if mode == "mock":
        translate_chunk = mock_translate_chunk
        model_name = str(config.get("model") or MOCK_SHOW_NOTES_TRANSLATION_MODEL)
    else:
        model_name = str(config.get("model") or DEFAULT_OPENCLAW_SHOW_NOTES_TRANSLATION_MODEL)
        agent_id = str(config.get("agent_id") or "").strip()
        timeout_seconds = _coerce_positive_int(
            config.get("timeout_seconds"),
            DEFAULT_OPENCLAW_TRANSLATION_TIMEOUT_SECONDS,
        )
        if openclaw_translate_chunk is not None:
            translate_chunk = openclaw_translate_chunk
        else:
            def translate_chunk(chunk, target_language="zh"):
                return translate_show_notes_chunk_with_openclaw(
                    chunk,
                    target_language=target_language,
                    agent_id=agent_id,
                    model_name=model_name,
                    timeout_seconds=timeout_seconds,
                )

    options = {
        "cache_enabled": bool(config.get("cache_enabled")),
        "cache_root": str(config.get("cache_dir") or SHOW_NOTES_TRANSLATION_DEFAULT_CONFIG["cache_dir"]),
        "translate_chunk": translate_chunk,
        "model_name": model_name,
        "max_chunk_chars": max_chunk_chars,
    }
    if mode == "openclaw":
        options["agent_id"] = str(config.get("agent_id") or "").strip()
        options["validate_translation_completeness"] = True
        options["max_translation_attempts"] = 2
    return True, options


# ── Feishu folder mapping（只读）──────────────────────────────────────
def load_feishu_folder_mapping() -> dict:
    path = os.path.join(STATE_DIR, "feishu_folder_mapping.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        log_stderr(f"FEISHU_MAPPING_LOAD_FAILED: {e}", "WARNING")
        return {}


# ── Episode Registry（jsonl + 写入前去重）────────────────────────────
REGISTRY_PATH = os.path.join(STATE_DIR, "episode_registry.jsonl")
MAX_STATUSES = 52


def load_registry() -> dict:
    records = {}
    if not os.path.exists(REGISTRY_PATH):
        return records
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ep_id = rec.get("episode_id", "")
                if ep_id:
                    records[ep_id] = rec
            except json.JSONDecodeError:
                continue
    return records


def write_registry_deduplicated(records: dict):
    # 追加本轮
    with open(REGISTRY_PATH, "a", encoding="utf-8") as f:
        for rec in records.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    # 全量去重（每个 episode_id 只保留最后一条）
    deduped = {}
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ep_id = rec.get("episode_id", "")
                if ep_id:
                    deduped[ep_id] = rec
            except json.JSONDecodeError:
                continue
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        for rec in deduped.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log(f"REGISTRY deduped: {len(deduped)} unique episode_ids")


# ── Weekly Runs（jsonl）──────────────────────────────────────────────
WEEKLY_RUNS_PATH = os.path.join(STATE_DIR, "weekly_runs.jsonl")


def append_weekly_run(run_meta: dict):
    with open(WEEKLY_RUNS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(run_meta, ensure_ascii=False) + "\n")


def update_latest_pointers(run_dir: str, result_path: str, report_path: str):
    """
    将 latest 指针（软链接）更新到指定 run_dir。
    必须在 result 和 report 都成功写入 run_dir 后才调用。

    校验：
    - result_path.exists()
    - report_path.exists()
    - result_json["run_id"] 与 run_dir.name 一致
    - result_json["week_id"] 与 run_dir.parent.name 一致

    使用相对软链接指向 run_dir。
    latest_screening_result.json 和 latest_screening_report.md 必须指向同一个 run。
    """
    store_update_latest_pointers(OUTPUT_DIR, run_dir, result_path, report_path)
    rel = os.path.relpath(os.path.abspath(run_dir), os.path.abspath(OUTPUT_DIR))
    print(f"[update_latest] latest → {rel}/", flush=True)


# ── RSS 获取 ─────────────────────────────────────────────────────────
def fetch_feed(url: str, feed_type: str, network_mode: str) -> str:
    return rss_fetch_feed(url, feed_type, network_mode)


def parse_duration_to_minutes(dur: str) -> int:
    return rss_parse_duration_to_minutes(dur)


def parse_rss_episodes(xml_content: str, podcast_name: str) -> list:
    return rss_parse_rss_episodes(xml_content, podcast_name)


def extract_jsonld(html_content: str) -> dict:
    return rss_extract_jsonld(html_content)


def _parse_jsonld_episodes_common(data: dict, podcast_name: str) -> list:
    return rss_parse_jsonld_episodes_common(data, podcast_name)


def parse_apple_jsonld_episodes(html_content: str, podcast_name: str) -> list:
    data = extract_jsonld(html_content)
    if not data or not data.get("workExample"):
        log_stderr(f"JSON-LD parse failed for {podcast_name}", "WARNING")
        return []
    return _parse_jsonld_episodes_common(data, podcast_name)


def parse_html_jsonld_episodes(html_content: str, podcast_name: str) -> list:
    data = extract_jsonld(html_content)
    if not data or not data.get("workExample"):
        log_stderr(f"JSON-LD parse failed for {podcast_name} (html_jsonld), trying apple fallback", "WARNING")
        return parse_apple_jsonld_episodes(html_content, podcast_name)
    return _parse_jsonld_episodes_common(data, podcast_name)


# ── 窗口过滤（fix-1：基于 timezone-aware pub_datetime）────────────────
def episode_in_window(ep: dict, window_start: datetime, window_end: datetime) -> bool:
    """
    过滤 episode 是否落在业务周窗口内。
    规则：published_at >= window_start AND published_at < window_end（开区间）
    如果 pub_datetime 为空，降级到 publish_date 字符串比较（已废弃路径）。
    """
    pub_dt_str = ep.get("pub_datetime", "")
    if not pub_dt_str:
        # 降级：fallback 到 publish_date 字符串比较（不准确但兼容旧数据）
        pub_date = ep.get("publish_date", "")
        if not pub_date:
            return False
        pub_yyyy_mm = pub_date[:10]
        wstart_yyyy_mm = window_start.strftime("%Y-%m-%d")
        wend_yyyy_mm = window_end.strftime("%Y-%m-%d")
        return wstart_yyyy_mm <= pub_yyyy_mm < wend_yyyy_mm

    # 解析 pub_datetime（可能是 +0000 / +0800 / naive）
    try:
        from email.utils import parsedate_to_datetime
        pub_dt = parsedate_to_datetime(pub_dt_str)
    except Exception:
        # naive datetime，假设为 UTC；处理 +0000 无冒号格式
        try:
            normalized = pub_dt_str.replace("Z", "+00:00").replace("+0000", "+00:00")
            pub_dt = datetime.fromisoformat(normalized)
        except Exception:
            return False

    # 统一到 UTC 比较（开区间：< window_end）
    pub_dt_utc = pub_dt.astimezone(timezone.utc)
    ws_utc = window_start.astimezone(timezone.utc)
    we_utc = window_end.astimezone(timezone.utc)

    return ws_utc <= pub_dt_utc < we_utc


# ── 评分逻辑（v2.3 结构化多维评分）─────────────────────────────────────
def _kw_match(text: str, kw: str) -> bool:
    """Word-boundary match for English keywords; substring match for Chinese keywords.
    Excludes matches in URL TLDs (e.g., pplx.ai/rogan, where 'ai' is a domain TLD).
    Handles Chinese title prefix: kw='AI' matches text starting with 'AI' (e.g., 'AI行业的收钱')."""
    if re.search(r'[\u4e00-\u9fff]', kw):
        return kw in text
    # Chinese title prefix: match regardless of case (e.g., 'ai' matches 'AI行业的收钱')
    if len(kw) >= 2 and text.lower().startswith(kw.lower()):
        return True
    # URL TLD exclusion: .ai/ or .ai? or .ai# or .ai followed by whitespace/end
    # e.g. pplx.ai/rogan → 'ai' as domain TLD should not match
    if re.search(r'\.' + re.escape(kw) + r'[/?#\s]', text, re.IGNORECASE):
        return False
    return bool(re.search(r'\b' + re.escape(kw) + r'\b', text, re.IGNORECASE))


ACTIONABILITY_KEYWORDS = (
    "交易", "策略", "买入", "卖出", "配置", "量化", "基金", "仓位", "对冲",
    "杠杆", "期权", "合约", "指标", "模型", "信号", "风险管理",
    "trading", "strategy", "buy", "sell", "portfolio", "allocation", "quant",
    "fund", "position sizing", "hedging", "leverage", "options", "contracts",
    "indicators", "model", "models", "signal", "signals", "risk management",
)

STRATEGIC_VALUE_KEYWORDS = (
    "宏观", "利率", "通胀", "政策", "监管", "能源", "电力市场", "电网", "储能",
    "光伏", "风电", "碳中和", "碳交易", "AI", "GPU", "数据中心", "基础设施",
    "半导体", "芯片", "竞争格局", "护城河", "商业模式", "壁垒",
    "macro", "interest rate", "inflation", "policy", "regulation", "energy",
    "electricity", "power market", "power grid", "storage", "solar", "wind",
    "carbon", "data center", "data centers", "infrastructure", "semiconductor",
    "semiconductors", "chip", "chips", "competition", "moat", "business model",
)


def _score_keyword_dimension(text: str, keywords: tuple[str, ...], points: int = 10) -> int:
    matches = sum(1 for keyword in keywords if _kw_match(text, keyword))
    return min(100, max(0, matches * points))


def _interpolate_density_length_score(length: int) -> int:
    points = ((0, 0), (80, 10), (200, 20), (500, 35), (1000, 45), (2500, 55), (5000, 60))
    bounded = max(0, length)
    for (left_x, left_y), (right_x, right_y) in zip(points, points[1:]):
        if bounded <= right_x:
            span = right_x - left_x
            ratio = (bounded - left_x) / span if span else 0
            return round(left_y + ratio * (right_y - left_y))
    return points[-1][1]


def calculate_information_density(notes: object) -> int:
    """Score source richness without making ordinary descriptions saturate at 100."""
    cleaned = _strip_html(str(notes or "")).strip()
    if not cleaned:
        return 0
    length_score = _interpolate_density_length_score(len(cleaned))
    sentence_count = len([
        part for part in re.split(r'[。！？.!?]+', cleaned)
        if len(re.findall(r'[A-Za-z\u4e00-\u9fff]', part)) >= 12
    ])
    sentence_score = min(25, sentence_count * 3)
    bullet_count = len(re.findall(r'(?m)^\s*(?:[-*•·]|\d+[.)])\s+', cleaned))
    timestamp_count = len(re.findall(r'\b\d{1,2}:\d{2}(?::\d{2})?\b', cleaned))
    structure_score = min(15, bullet_count * 2 + timestamp_count * 2)
    return min(100, length_score + sentence_score + structure_score)


def clean_display_text(text: str) -> str:
    """清洗用于展示的文本：HTML unescape + 多空格压缩 + strip + 清理残破引号。
    保留正常引号（如 Big John McCarthy），不破坏中文标题。"""
    if not text:
        return ""
    # 1. HTML unescape
    text = html.unescape(text)
    # 2. 清理残破引号：strip 尾部孤立的 " 或 '
    text = text.rstrip('"\'"')
    # 3. 压缩连续空白为单空格
    text = re.sub(r' {2,}', ' ', text)
    # 4. strip
    return text.strip()


def score_episode_structured(ep: dict, interests: dict, policy: dict) -> dict:
    """
    结构化多维评分（v3.0）
    所有输出字段必须为简体中文，节目标题和播客名称保留原文。

    返回 dict:
    {
        "topic_relevance": int 0-100,    # 主题与兴趣的相关度
        "information_density": int 0-100, # 信息密度
        "novelty": int 0-100,            # 新鲜度（话题/嘉宾/观点是否新鲜）
        "actionability": int 0-100,      # 行动价值（能否直接指导投资/决策）
        "strategic_value": int 0-100,    # 战略/研究价值
        "transcription_value": int 0-100, # 完整转写价值
        "final_score": float,            # 综合分 0-100
        "decision": str,                 # "full"/"preview"/"skip"
        "reason_zh": str,                # 中文推荐理由（≥30汉字）
        "uncertainty_zh": str,           # 中文不确定性说明
    }
    """
    import re
    title = clean_display_text(ep.get("episode_title", ""))
    notes = _strip_html(str(ep.get("show_notes_text") or ep.get("show_notes") or ""))
    text = (title + " " + notes).lower()

    # ── 维度评分（基于关键词）────────────────────────────────────────
    # topic_relevance: 相关主题命中
    boost_kws = [k.lower() for k in interests.get("boost_keywords", []) if k]
    neg_kws = [k.lower() for k in interests.get("negative_keywords", []) if k]
    primary = [t.lower() for t in interests.get("primary_topics", [])]

    topic_score = 0
    for kw in boost_kws:
        if _kw_match(text, kw):
            topic_score += 8
    for kw in neg_kws:
        if _kw_match(text, kw):
            topic_score -= 15
    topic_score += len([t for t in primary if _kw_match(text, t)]) * 12
    topic_relevance = max(0, min(100, topic_score))

    # information_density: 基于 notes 长度和句子完整度
    clean_notes = notes.strip()
    note_len = len(clean_notes)
    sent_count = len(re.split(r'[。！？.!?]+', clean_notes)) if clean_notes else 0
    information_density = calculate_information_density(clean_notes)

    # novelty: 基于关键词/嘉宾新鲜度
    important = [p.lower() for p in interests.get("important_people", [])]
    novelty = 0
    for person in important:
        if person in text:
            novelty += 20
    if any(_kw_match(text, kw) for kw in boost_kws[:8]):
        novelty += 15
    novelty = min(100, max(0, novelty))

    # actionability: 直接行动价值
    actionability = _score_keyword_dimension(text, ACTIONABILITY_KEYWORDS)

    # strategic_value: 宏观/行业/长期趋势
    strategic_value = _score_keyword_dimension(text, STRATEGIC_VALUE_KEYWORDS)

    # transcription_value: 长篇深度内容价值
    dur = ep.get("duration_minutes", 0)
    is_long = dur >= 60
    has_deep_content = note_len > 200 and sent_count > 5
    transcription_value = min(100, (40 if is_long else 0) + (30 if has_deep_content else 0) +
                              (15 if any(_kw_match(text, kw) for kw in STRATEGIC_VALUE_KEYWORDS) else 0))

    # final_score: 加权综合
    final_score = (
        topic_relevance * 0.25 +
        information_density * 0.15 +
        novelty * 0.15 +
        actionability * 0.15 +
        strategic_value * 0.20 +
        transcription_value * 0.10
    )
    final_score = round(max(0, min(100, final_score)), 1)

    # decision
    thresh = policy.get("score_policy", {})
    full_thresh = thresh.get("full_threshold", 80)  # 阈值已改为 0-100
    preview_thresh = thresh.get("preview_threshold", 50)
    skip_thresh = thresh.get("skip_threshold", 30)
    if final_score >= full_thresh:
        decision = "full"
    elif final_score >= preview_thresh:
        decision = "preview"
    else:
        decision = "skip"

    # Build scoring payload BEFORE reason generation (fix NameError)
    scoring = {
        "topic_relevance": topic_relevance,
        "information_density": information_density,
        "novelty": novelty,
        "actionability": actionability,
        "strategic_value": strategic_value,
        "transcription_value": transcription_value,
        "final_score": final_score,
        "decision": decision,
        "reason_zh": "",   # filled below
        "uncertainty_zh": "",  # filled below
    }

    # reason_zh: 具体中文推荐理由
    scoring["reason_zh"] = _build_reason_zh(ep, scoring, interests, text)

    # uncertainty_zh: 中文不确定性说明
    scoring["uncertainty_zh"] = _build_uncertainty_zh(
        ep, topic_relevance, information_density,
        novelty, decision, note_len
    )

    return scoring


def _build_reason_zh(ep: dict, scoring: dict, interests: dict, text: str) -> str:
    """
    生成具体中文推荐理由（v2.4，不依赖 boost_kw 命中作为主要输出）。
    reason_zh 必须使用每期 episode 自己的具体信息，结构如下：
      1. 节目概述（播客名 + 标题/嘉宾 + 核心话题）
      2. 与兴趣方向的相关性（具体说明是哪个方向）
      3. 为什么先进入 preview
      4. 是否建议 full / 为什么不建议
      5. 当前判断的不确定性
    至少 60 汉字。
    """
    import re as _re
    import html
    title = clean_display_text(ep.get("episode_title", ""))
    podcast = ep.get("podcast_name", "")
    notes = _strip_html(str(ep.get("show_notes_text") or ep.get("show_notes") or ""))
    score = scoring["final_score"]
    decision = scoring["decision"]
    topic = scoring["topic_relevance"]
    density = scoring["information_density"]
    novelty = scoring["novelty"]
    action = scoring["actionability"]
    strategic = scoring["strategic_value"]
    transcription = scoring["transcription_value"]
    full_suggestion = scoring.get("full_suggestion", "no")

    # ── 从节目标题和 show_notes 中提取核心话题词（3-8 个）────────────────
    # 提取 show_notes 中含有关键词的句子（用于判断话题）
    interest_kws = interests.get("boost_keywords", [])
    important_people = interests.get("important_people", [])
    note_sentences = _re.split(r'(?<=[。！？.!?])\s+', notes.strip()) if notes.strip() else []
    relevant_sentences = []
    for sent in note_sentences:
        sent_lower = sent.lower()
        if any(_kw_match(sent_lower, kw) for kw in interest_kws):
            relevant_sentences.append(sent.strip()[:80])

    # 从节目标题提取核心词（去除停用词后含兴趣词或嘉宾名）
    title_words = _re.findall(r'[\w\u4e00-\u9fff]+', title.lower())
    stop_words = {"the", "a", "an", "of", "and", "in", "to", "is", "for", "with", "on", "at", "by", "ep", "episode", "episode", "part", "pt", "with", "#", "-", "ep."}
    filtered_words = [w for w in title_words if w not in stop_words and len(w) > 2]
    title_kws = filtered_words[:6]

    # 识别嘉宾名：优先使用 episode 中已有的 guest_detection 结果
    guest_detection_status = ep.get("guest_detection_status", "no_guest_detected")
    guest_names = ep.get("guest_names", [])
    # title already unescaped at top of function
    # 如果有 confirmed/possible guest，用它；否则不写嘉宾
    if guest_names and guest_detection_status in ("confirmed_guest", "possible_guest"):
        guest_display = "、".join(guest_names[:2])
        guest_prefix = f"「{podcast}」本期嘉宾为 {guest_display}，节目围绕「{title[:40]}」展开。"
    elif title:
        guest_prefix = f"「{podcast}」本期主题为「{title[:40]}」。"
    else:
        guest_prefix = f"「{podcast}」本期（标题待识别）。"

    # ── 组装 reason_zh ─────────────────────────────────────────────────
    parts = [guest_prefix]

    # 2. 与兴趣方向的相关性（使用 word-boundary 匹配，避免 ai 误匹配 available 等）
    matched_kws = [kw for kw in interest_kws if _kw_match(text, kw)]
    if matched_kws:
        parts.append(f"涉及话题：{'、'.join(matched_kws[:5])}，与兴趣方向存在关联。")
    elif topic >= 40:
        parts.append(f"主题相关性中等（相关度{topic}分），未直接命中核心兴趣词。")
    else:
        parts.append(f"主题相关性较弱（{topic}分），兴趣方向匹配度一般。")

    # 3. show_notes 中提取的具体话题句
    if relevant_sentences:
        concrete_topic = relevant_sentences[0][:60]
        parts.append(f"节目中涉及：{concrete_topic}。")

    # 4. 为什么进入 preview
    if decision == "preview":
        if full_suggestion == "yes":
            parts.append(f"综合评分{score:.1f}分，强烈建议完整转写后再决定优先级。")
        elif full_suggestion == "maybe":
            parts.append(f"综合评分{score:.1f}分，建议先预览判断内容深度，再决定是否 full。")
        else:
            if strategic >= 50:
                parts.append(f"含一定战略/行业信息（{strategic}分），适合预览判断具体价值。")
            elif novelty >= 50:
                parts.append(f"话题有一定新鲜度（{novelty}分），值得预览了解。")
            elif action >= 30:
                parts.append(f"含一定行动/策略信息（{action}分），预览可辅助决策。")
            else:
                parts.append(f"信息密度{density}分，内容价值尚待预览确认。")

    # 5. 不足或不确定性
    uncertain_parts = []
    if density < 30:
        uncertain_parts.append("信息量偏少，show_notes 不足以判断完整价值")
    if novelty < 30 and decision == "preview":
        uncertain_parts.append("话题新鲜度较低，可能是已有认知的重复")
    if not notes.strip():
        uncertain_parts.append("暂无 show_notes，无法判断内容质量")
    if transcription < 30:
        uncertain_parts.append("完整转写价值有限，优先预览即可")
    if uncertain_parts:
        parts.append("不确定性：" + "；".join(uncertain_parts) + "。")
    else:
        parts.append("暂未发现明显不确定性。")

    reason = "".join(parts)

    # 保护性垫底：如果低于 60 汉字，追加内容填充
    zh_chars = len(_re.findall(r'[\u4e00-\u9fff]', reason))
    if zh_chars < 60:
        extra_parts = []
        # 只在有实际内容时补充，不重复 score 数字
        note_len = len(notes.strip())
        if note_len > 0 and note_len < 100:
            extra_parts.append("节目简介较短，具体内容价值需预览后判断。")
        elif note_len == 0:
            extra_parts.append("暂无 show_notes，内容深度无法预估。")
        if extra_parts:
            reason += "".join(extra_parts)

    return reason


def normalize_pub_datetime(ep: dict) -> str:
    """
    尝试从 episode 字典中提取并标准化 pub_datetime。
    依次尝试：published → published_parsed → updated → pubDate → itunes_releaseDate → releaseDate
    失败时返回空字符串，并写 quality_warning。
    """
    for key in ["published", "published_parsed", "updated", "updated_parsed",
                "pubDate", "itunes_releaseDate", "releaseDate"]:
        raw = ep.get(key)
        if not raw:
            continue
        if isinstance(raw, str):
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(raw)
                return dt.isoformat()
            except Exception:
                try:
                    from datetime import datetime
                    from zoneinfo import ZoneInfo
                    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    return dt.isoformat()
                except Exception:
                    continue
        elif isinstance(raw, (tuple, list)):
            # struct_time → timetuple
            try:
                from time import mktime
                from datetime import datetime
                dt = datetime.fromtimestamp(mktime(raw))
                return dt.isoformat()
            except Exception:
                continue
        elif hasattr(raw, "timetuple"):
            try:
                from time import mktime
                from datetime import datetime
                dt = datetime.fromtimestamp(mktime(raw.timetuple()))
                return dt.isoformat()
            except Exception:
                continue
    # 无法解析，返回空字符串（调用方写 quality_warning）
    return ""


def compute_priority(final_score: float, all_preview_scores: list = None,
                    sel_mode: str = "all_preview") -> str:
    """
    v2.4: all_preview 模式使用周内相对排序（top 20%=high/middle 40%=medium/bottom 40%=low）。
    score_based 模式保留原固定阈值逻辑。
    skip episode 不参与 priority 计算。
    """
    if sel_mode == "score_based":
        if final_score >= 70:
            return "high"
        if final_score >= 40:
            return "medium"
        return "low"

    # all_preview / 相对分位模式
    if not all_preview_scores or len(all_preview_scores) < 2:
        return "medium"

    sorted_scores = sorted(all_preview_scores, reverse=True)
    n = len(sorted_scores)
    p70_idx = max(0, int(n * 0.2) - 1)
    p40_idx = max(0, int(n * 0.6) - 1)
    p70 = sorted_scores[p70_idx] if p70_idx < n else sorted_scores[0]
    p40 = sorted_scores[p40_idx] if p40_idx < n else sorted_scores[-1]

    # 确保至少各有 1 条 high / medium（当 n >= 5 时）
    if n >= 5:
        if final_score >= p70:
            return "high"
        if final_score >= p40:
            return "medium"
        return "low"
    else:
        if final_score >= (p70 + p40) / 2:
            return "high"
        if final_score >= p40:
            return "medium"
        return "low"


def quality_check_episode(ep: dict, scoring: dict, all_reasons: list = None) -> list:
    """
    轻量级质量检查，返回问题列表（空列表=通过）。
    v2.4 新增：reason_zh ≥60 汉字 / reason_zh 重复检测 / show_notes 全空检测。
    """
    import re as _re
    problems = []
    reason = scoring.get("reason_zh", "")

    # a) reason_zh 必须包含足够中文字符（≥60汉字 v2.4）
    zh_chars = len(_re.findall(r'[\u4e00-\u9fff]', reason))
    if zh_chars < 45:
        problems.append(f"quality_warning: reason_zh 汉字不足45个（仅{zh_chars}个）")
    # a) reason_zh 必须包含足够中文字符（≥45汉字 v2.4，底线）
    if len(reason.strip()) < 30:
        problems.append("quality_warning: reason_zh 过短（字符数不足30）")
    en_words = len(_re.findall(r'[a-zA-Z]{4,}', reason))
    if en_words > 20:
        problems.append(f"quality_warning: reason_zh 含过多英文单词（{en_words}个），可能未正确使用中文")
    # d) reason_zh 重复检测（跨 episode）
    if all_reasons is not None:
        same_count = sum(1 for r in all_reasons if r == reason)
        if same_count > 1:
            problems.append(f"quality_warning: reason_zh 与其他{same_count}条 episode 重复，建议差异化")

    # e) 检查 show_notes 是否为空
    show_notes = _strip_html(str(ep.get("show_notes_text") or ep.get("show_notes") or ""))
    if not show_notes or len(show_notes.strip()) < 20:
        problems.append("quality_warning: show_notes 为空或过短，无法生成有效摘要")

    # f) 检查 pub_datetime 是否缺失
    if not ep.get("pub_datetime"):
        problems.append("quality_warning: pub_datetime 缺失")

    # g) 检查 duration 是否缺失
    if not ep.get("duration_minutes"):
        problems.append("quality_warning: duration_minutes 缺失")

    return problems


def _build_uncertainty_zh(ep: dict, topic: int, density: int, novelty: int,
                          decision: str, note_len: int) -> str:
    """生成中文不确定性说明"""
    uncertain_parts = []
    if note_len < 100:
        uncertain_parts.append("节目简介信息量有限，摘要可能存在偏差")
    if novelty < 40 and decision != "skip":
        uncertain_parts.append("新鲜度评分较低，内容可能不够独特")
    if density < 40 and decision == "full":
        uncertain_parts.append("信息密度一般，完整转写前建议先预览")
    if not uncertain_parts:
        uncertain_parts.append("暂无明显不确定性")
    return "；".join(uncertain_parts) + "。"


# 保留旧函数以备后用（main() 已改用 score_episode_structured）
def score_episode(ep: dict, interests: dict, policy: dict) -> float:
    """旧版单维度评分（保留兼容）"""
    title = ep.get("episode_title", "")
    notes = ep.get("show_notes", "")
    text = (title + " " + notes).lower()
    score = 5.0
    for kw in interests.get("boost_keywords", []):
        if kw.lower() in text:
            score += 0.5
    for kw in interests.get("negative_keywords", []):
        if kw and kw.lower() in text:
            score = min(score, 3.0)
    for person in interests.get("important_people", []):
        if person.lower() in text:
            score += 1.0
    for topic in interests.get("primary_topics", []):
        if topic.lower() in text:
            score += 1.0
    return max(0.0, min(10.0, score))


def classify_all_preview_episode(ep: dict, scoring: dict, policy: dict) -> dict:
    """Return the all-preview decision together with its stable reason."""
    del scoring
    ad_kws = (policy.get("ad_detection_policy", {}).get("keywords_en", []) +
              policy.get("ad_detection_policy", {}).get("keywords_zh", []))
    notes_text = _strip_html(str(ep.get("show_notes_text") or ep.get("show_notes") or "")).lower()
    title_lower = ep.get("episode_title", "").lower()
    audio_url_lower = html.unescape(str(ep.get("audio_url") or "")).lower()
    selection = policy.get("selection_policy", {})
    min_dur = int(selection.get("min_duration_minutes", 5))
    short_max = int(selection.get("short_episode_max_minutes", 15))
    duration_seconds = int(ep.get("duration_seconds") or 0)
    duration_minutes = int(ep.get("duration_minutes") or (duration_seconds // 60))

    def result(decision: str, reason_code: str, reason_zh: str) -> dict:
        return {
            "decision": decision,
            "reason_code": reason_code,
            "reason_zh": reason_zh,
        }

    if not ep.get("audio_url"):
        return result("skip", "missing_audio", "缺少可用音频链接，无法进入 Preview。")

    paywall_audio = "paywall" in audio_url_lower and any(
        marker in audio_url_lower for marker in ("intro", "preview", "trailer")
    )
    paywall_notes = bool(re.search(
        r"\bsubscribe\b.{0,160}\bfull[- ]length episodes?\b",
        notes_text,
        re.IGNORECASE | re.DOTALL,
    ))
    if paywall_audio or paywall_notes:
        return result(
            "skip",
            "paywall_preview",
            "当前音频是付费正片的试听或引子，不是完整节目。",
        )

    cross_promo = (
        title_lower.strip().startswith("introducing:")
        and bool(re.search(r"\blisten to (?:episode|the full|.+? on )", notes_text))
    )
    if cross_promo:
        return result(
            "skip",
            "cross_podcast_promo",
            "这是导流到其他节目的短推广，不是本播客正片。",
        )

    for kw in ad_kws:
        if kw.lower() in notes_text:
            return result(
                "skip",
                "ad_only",
                "Show Notes 命中广告或赞助内容规则，按非正片跳过。",
            )
    trailer_phrases = ["trailer", "preview episode", "announcement", "sneak peek",
                       "节目预告", "内容预告"]
    for phrase in trailer_phrases:
        if phrase in title_lower:
            return result(
                "skip",
                "trailer_or_announcement",
                "标题表明这是预告或公告，不是完整节目。",
            )

    if duration_minutes < min_dur:
        return result(
            "skip",
            "below_minimum_duration",
            f"节目时长低于最低 {min_dur} 分钟门槛。",
        )

    if not title_lower.strip() or (not notes_text.strip() and duration_minutes < 5):
        return result(
            "skip",
            "invalid_metadata",
            "节目标题或内容元数据不足，无法可靠进入 Preview。",
        )

    if duration_minutes < short_max:
        return result(
            "preview",
            "valid_short_episode",
            "有效短节目，保留在 Preview，并在周报中标注短节目。",
        )

    return result("preview", "valid_episode", "有效节目，进入 Preview。")


def decide_in_all_preview_mode(ep: dict, scoring: dict, policy: dict) -> str:
    """Compatibility wrapper returning only the all-preview decision."""
    return classify_all_preview_episode(ep, scoring, policy)["decision"]


def compute_full_suggestion(
    final_score: float,
    transcription_value: int,
    policy: dict,
    *,
    topic_relevance: Optional[int] = None,
    strategic_value: int = 0,
    actionability: int = 0,
) -> str:
    """基于 final_score 和 transcription_value 输出 full_suggestion（yes/maybe/no）"""
    yes_thresh = policy.get("full_suggestion_policy", {}).get("yes_thresholds", {})
    maybe_thresh = policy.get("full_suggestion_policy", {}).get("maybe_thresholds", {})
    yes_fs = yes_thresh.get("final_score", 75)
    yes_tv = yes_thresh.get("transcription_value", 75)
    maybe_fs = maybe_thresh.get("final_score", 55)
    maybe_tv = maybe_thresh.get("transcription_value", 55)

    if topic_relevance is not None and topic_relevance <= 0 and max(strategic_value, actionability) < 40:
        return "no"

    if final_score >= yes_fs or transcription_value >= yes_tv:
        return "yes"
    if final_score >= maybe_fs or transcription_value >= maybe_tv:
        return "maybe"
    return "no"


def _strip_html(text: str) -> str:
    """去除 HTML 标签"""
    import re
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&#39;', "'").replace('&quot;', '"')
    return text.strip()


# [dead-code] generate_summary_cn() 已废弃，main() 使用 score_episode_structured()
# 保留此函数仅作历史参考，请勿在新代码中调用
# 注意：内部 score >= 8 / score >= 5 仍为 0-10 量表，与当前 0-100 算法不兼容
def generate_summary_cn(ep: dict, score: float) -> dict:
    raise RuntimeError(
        "generate_summary_cn() is deprecated and unsafe for use with the current 0-100 scoring system. "
        "Use score_episode_structured() instead, which returns topic_relevance / information_density / "
        "novelty / actionability / strategic_value / transcription_value / final_score / decision / "
        "reason_zh / uncertainty_zh."
    )



def is_noisy_source(source: dict) -> bool:
    """判断 source 是否为噪音，不值得展示。委托给 is_guest_source_noise。"""
    return is_guest_source_noise(source)


# ── Markdown 格式化 helpers（module-level for testability）───────────────
def _append_show_notes_section(
    lines: list,
    r: dict,
    *,
    show_notes_translation_enabled: bool = False,
    show_notes_translation_options: Optional[dict] = None,
    show_notes_metadata_sink=None,
) -> None:
    """Append full episode show notes as display-only Markdown text blocks."""
    display_result = build_show_notes_display_result(
        r,
        translation_enabled=show_notes_translation_enabled,
        translation_options=show_notes_translation_options,
    )
    sections = display_result["sections"]
    if show_notes_metadata_sink is not None:
        metadata = {
            "heading": display_result["heading"],
            "translation": display_result["translation"],
            "source_completeness": display_result["source_completeness"],
            "display_filter": display_result["display_filter"],
            "display_snapshot": {
                "version": SHOW_NOTES_DISPLAY_SNAPSHOT_VERSION,
                "heading": display_result["heading"],
                "sections": list(sections),
            },
        }
        try:
            show_notes_metadata_sink(r, metadata)
        except Exception:
            pass
    if display_result["heading"] == "translated":
        lines.append("")
        lines.append(f"**{SHOW_NOTES_TRANSLATED_HEADING}：**")
        content_sections = sections
    else:
        lines.append("")
        lines.append("**节目介绍 / Show Notes（完整）：**")
        content_sections = sections

    for index, section in enumerate(content_sections):
        if index:
            lines.append("")
        lines.append(section)


def _fmt_ep(
    r: dict,
    decision_label: str,
    *,
    show_notes_translation_enabled: bool = False,
    show_notes_translation_options: Optional[dict] = None,
    show_notes_metadata_sink=None,
) -> list:
    """格式化单条节目为多行 Markdown（v2.5：嘉宾背景 sources 展示层截断到前2个）"""
    dur_str = format_episode_duration(r)
    score = r.get('score', r.get('score_float', 0))
    kps = r.get('key_points_cn', [])
    lines = [
        f"### {r['podcast_name']} | {r['episode_title']} | {dur_str} | {score}分 | {decision_label}",
        f"",
    ]
    # 只有非空概述才输出"概述："这一行
    overview_text = (r.get('one_line_summary_cn', '') or
                     r.get('summary_3_sentences_cn', [''])[0] if r.get('summary_3_sentences_cn') else '').strip()
    if overview_text:
        lines.append(f"**概述：**{overview_text}")
    if kps:
        # 过滤掉只含主题/发布/时长的基础条目，保留真正有内容的
        content_kps = [k for k in kps if '主题：' not in k and '发布：' not in k and '时长：' not in k]
        # 关键点从第二条开始（第一条往往等于概述，避免重复）
        if len(content_kps) > 1:
            for kp in content_kps[1:4]:
                lines.append(f"- {kp}")
        elif content_kps:
            lines.append(f"- 主题：{r['episode_title']}")
        else:
            # 没有有效 key_points 时显示主题
            lines.append(f"- 主题：{r['episode_title']}")
    _append_show_notes_section(
        lines,
        r,
        show_notes_translation_enabled=show_notes_translation_enabled,
        show_notes_translation_options=show_notes_translation_options,
        show_notes_metadata_sink=show_notes_metadata_sink,
    )
    return lines


def _fmt_skip_ep(
    r: dict,
    *,
    show_notes_translation_enabled: bool = False,
    show_notes_translation_options: Optional[dict] = None,
    show_notes_metadata_sink=None,
) -> list:
    """格式化单条 skip 节目为多行 Markdown。"""
    one_line = r.get('one_line_summary_cn', '')
    skip_reason = (
        r.get('decision_reason_zh')
        or r.get('reason_zh')
        or r.get('reason')
        or '评分不足'
    )
    lines = [
        f"### {r['podcast_name']} | {r['episode_title']} | {r.get('score', 0)}分",
    ]
    if one_line:
        lines.append(f"**概述：**{one_line}")
    lines.append(f"**跳过理由：**{skip_reason}")
    _append_show_notes_section(
        lines,
        r,
        show_notes_translation_enabled=show_notes_translation_enabled,
        show_notes_translation_options=show_notes_translation_options,
        show_notes_metadata_sink=show_notes_metadata_sink,
    )
    lines.append("")
    return lines


def _fmt_report_window_time(value) -> str:
    """格式化周报窗口时间，保持 main() 当前 Markdown 展示形态。"""
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M')
    text = str(value or "")
    if not text:
        return ""
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z"):
        try:
            return datetime.strptime(text, fmt).strftime('%Y-%m-%d %H:%M')
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text).strftime('%Y-%m-%d %H:%M')
    except ValueError:
        return text[:16].replace("T", " ")


def _infer_podcast_count(result_data: dict) -> int:
    for key in ("podcast_count", "configured_podcast_count"):
        if result_data.get(key) is not None:
            return int(result_data.get(key, 0))
    episodes = result_data.get("full", []) + result_data.get("preview", []) + result_data.get("skip", [])
    return len({r.get("podcast_id") or r.get("podcast_name") for r in episodes if r.get("podcast_id") or r.get("podcast_name")})


def build_markdown_report(
    result_data: dict,
    podcast_count: Optional[int] = None,
    *,
    show_notes_translation_enabled: bool = False,
    show_notes_translation_options: Optional[dict] = None,
    show_notes_metadata_sink=None,
) -> str:
    """从 result-like JSON 构造完整 Markdown 周报，不读写任何外部状态。"""
    full_decided = result_data.get("full", [])
    preview_decided = result_data.get("preview", [])
    skip_decided = result_data.get("skip", [])
    fetch_errors = result_data.get("fetch_errors", [])
    resolved_podcast_count = podcast_count if podcast_count is not None else _infer_podcast_count(result_data)

    report_lines = [
        f"📅 播客筛选报告 | {result_data.get('week_id', 'unknown')} | 窗口：{_fmt_report_window_time(result_data.get('window_start'))} — {_fmt_report_window_time(result_data.get('window_end'))}",
        f"总计扫描：{result_data.get('total_episodes', 0)}期节目",
        f"覆盖播客：{resolved_podcast_count}个",
        f"Fetch错误：{len(fetch_errors)}个" if fetch_errors else "",
        "",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"✅ 本周建议完整转写（Full，{len(full_decided)}期）：",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if not full_decided:
        report_lines.append("本周无 Full 推荐。")
    for r in full_decided:
        report_lines += _fmt_ep(
            r,
            "Full",
            show_notes_translation_enabled=show_notes_translation_enabled,
            show_notes_translation_options=show_notes_translation_options,
            show_notes_metadata_sink=show_notes_metadata_sink,
        )

    report_lines += [
        "",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🔍 本周建议预览转写（Preview，{len(preview_decided)}期）：",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if not preview_decided:
        report_lines.append("本周无 Preview 推荐。")
    for r in preview_decided:
        report_lines += _fmt_ep(
            r,
            "Preview",
            show_notes_translation_enabled=show_notes_translation_enabled,
            show_notes_translation_options=show_notes_translation_options,
            show_notes_metadata_sink=show_notes_metadata_sink,
        )

    report_lines += [
        "",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"⏭️ 本周跳过（Skip，{len(skip_decided)}期）：",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if not skip_decided:
        report_lines.append("本周无 Skip。")
    for r in skip_decided:
        report_lines.extend(
            _fmt_skip_ep(
                r,
                show_notes_translation_enabled=show_notes_translation_enabled,
                show_notes_translation_options=show_notes_translation_options,
                show_notes_metadata_sink=show_notes_metadata_sink,
            )
        )

    return "\n".join(report_lines)


# ── 主流程 ────────────────────────────────────────────────────────────
def main():
    ensure_runtime_directories(_RUNTIME_PATHS)
    clear_stderr()
    log_run_boundary()
    log("TASK_TRIGGERED")

    # 1. 业务周窗口（支持 --run-date 覆盖）
    import podcast_screener as _mod
    _override = getattr(_mod, '_override_now', None)
    if _override is not None:
        window_start, window_end, week_id = get_business_week_window(_override)
    else:
        window_start, window_end, week_id = get_business_week_window()
    window_start_str = window_start.strftime("%Y-%m-%dT%H:%M:%S%z")
    window_end_str = window_end.strftime("%Y-%m-%dT%H:%M:%S%z")

    # [fix-w4] 增强日志：完整窗口元数据
    log(f"WINDOW week_id={week_id} "
        f"start={window_start_str} end={window_end_str} "
        f'timezone=Asia/Shanghai '
        f'interval_rule="published_at >= window_start and published_at < window_end" '
        f'window_semantics="last_completed_business_week"')

    # [fix-w4] fail-fast：week_id 自洽性校验
    if not validate_week_id(window_start, week_id):
        expected_wid = business_week_id(window_start)
        log_stderr(
            f"WEEK_ID_MISMATCH: week_id={week_id} but "
            f"business_week_id(window_start)={expected_wid}. "
            f"window_start={window_start_str}. FAILING.",
            "ERROR"
        )
        sys.exit(1)

    # 2. 加载状态
    feishu_mapping = load_feishu_folder_mapping()
    registry = load_registry()
    log(f"REGISTRY loaded {len(registry)} unique episode_ids")

    # 3. 加载配置
    podcasts_cfg, interests, policy = load_configs()
    log("SCREENING_STARTED")

    # 4. 拉取播客
    all_episodes = []
    fetch_errors = []
    podcasts_by_name = {p["name"]: p for p in podcasts_cfg.get("podcasts", [])}

    for pod in podcasts_cfg.get("podcasts", []):
        pid = pod["id"]
        name = pod["name"]
        url = pod.get("rss_url", "")
        feed_type = pod.get("feed_type", "rss")
        network_mode = pod.get("network_mode", "auto")

        if not url:
            continue

        log(f"Fetching {pid} ({name})...")
        content = fetch_feed(url, feed_type, network_mode)

        if not content:
            fetch_errors.append(pid)
            log_stderr(f"FETCH_FAILED {pid}")
            continue

        try:
            if feed_type == "rss":
                eps = parse_rss_episodes(content, name)
            elif feed_type == "apple_jsonld":
                eps = parse_apple_jsonld_episodes(content, name)
            elif feed_type == "html_jsonld":
                eps = parse_html_jsonld_episodes(content, name)
            else:
                eps = parse_rss_episodes(content, name)
        except Exception as e:
            log_stderr(f"PARSE_EXCEPTION {pid}: {e}", "WARNING")
            eps = []

        # [fix-1] 使用 timezone-aware pub_datetime 过滤窗口
        eps = [e for e in eps if episode_in_window(e, window_start, window_end)]
        # [fix-dedup] 按 episode_id 同轮去重，防止同一 episode 被重复处理
        seen_ids = set()
        eps_deduped = []
        for e in eps:
            ep_id = make_episode_id(pid, e["publish_date"][:10], e["episode_title"])
            if ep_id not in seen_ids:
                seen_ids.add(ep_id)
                eps_deduped.append(e)
        eps = eps_deduped
        all_episodes.extend(eps)
        log(f"  → {len(eps)} episodes in window")

    log(f"Total episodes before dedup: {len(all_episodes)}")
    # [fix-dup2] 全局去重（跨 podcast）
    seen_all = set()
    all_episodes_deduped = []
    for e in all_episodes:
        ep_id_raw = e.get("episode_title", "") + e.get("publish_date", "")
        if ep_id_raw not in seen_all:
            seen_all.add(ep_id_raw)
            all_episodes_deduped.append(e)
    all_episodes = all_episodes_deduped
    log(f"Total episodes after dedup: {len(all_episodes)}")

    # 5. 评分
    results = []

    # [v2.4] 筛选策略模式
    sel_mode = policy.get("selection_policy", {}).get("mode", "all_preview")
    log(f"SELECTION_MODE={sel_mode}")

    # [v2.4] 收集 preview episode 的 final_score，供后续相对分位计算
    all_preview_scores = []

    for ep in all_episodes:
        scoring = score_episode_structured(ep, interests, policy)
        score = scoring["final_score"]
        decision = scoring["decision"]

        # [fix-pubdatetime] episode 构建时若 pub_datetime 仍为空，用 normalize_pub_datetime 兜底
        if not ep.get("pub_datetime"):
            normalized = normalize_pub_datetime(ep)
            if normalized:
                ep["pub_datetime"] = normalized
            else:
                log_stderr(f"PUB_DATETIME_MISSING episode={ep.get('episode_title','?')} podcast={ep.get('podcast_name','?')} available_keys={list(ep.keys())}", "WARNING")

        # [v2.4] decision 覆盖：all_preview 模式使用独立逻辑
        decision_result = None
        if sel_mode == "all_preview":
            decision_result = classify_all_preview_episode(ep, scoring, policy)
            decision = decision_result["decision"]

        reason_zh = scoring["reason_zh"]
        uncertainty_zh = scoring["uncertainty_zh"]

        # 质量检查
        problems = quality_check_episode(ep, scoring)
        for prob in problems:
            log_stderr(prob, "WARNING")

        # 临时 priority（占位），all_preview 模式下稍后用相对分位统一重算
        priority = compute_priority(score, sel_mode=sel_mode)
        full_suggestion = compute_full_suggestion(
            score,
            scoring["transcription_value"],
            policy,
            topic_relevance=scoring["topic_relevance"],
            strategic_value=scoring["strategic_value"],
            actionability=scoring["actionability"],
        )

        # [fix-priority] all_preview 模式：先收集 final_score
        if sel_mode == "all_preview" and decision == "preview":
            all_preview_scores.append(score)

        summary = {
            "summary_3_sentences_cn": [f"{ep.get('podcast_name','某播客')}本期主题为「{clean_display_text(ep.get('episode_title',''))}」。"],
            "one_line_summary_cn": scoring.get("one_line_summary_cn", ""),
            "key_points_cn": [],
            "why_important": reason_zh,
            "uncertainty_zh": uncertainty_zh,
        }

        pod_name = ep["podcast_name"]
        pod_cfg = podcasts_by_name.get(pod_name, {})
        pod_id = pod_cfg.get("id", pod_name.lower().replace(" ", ""))
        lang = pod_cfg.get("language", "zh")
        raw_date = ep["publish_date"]
        date_only = raw_date[:10]
        ep_id = make_episode_id(pod_id, date_only, ep["episode_title"])

        existing = registry.get(ep_id, {})
        seen_count = existing.get("seen_count", 0) + 1

        # [fix-3] first_seen: 保留首次真实发现时间（原值）；last_seen: 本轮真实时间
        first_seen = existing.get("first_seen", RUN_TS)
        first_seen_week_id = existing.get("first_seen_week_id", week_id)
        last_seen = RUN_TS  # 真实 run timestamp（不再用 window_start）

        prev_statuses = existing.get("statuses", [])
        statuses = (prev_statuses + [decision])[-MAX_STATUSES:]

        show_notes_text = clean_show_notes(ep.get("show_notes", ""))
        pub_at = ep.get("pub_datetime", date_only)

        mapping_entry = feishu_mapping.get(pod_id, {}) if feishu_mapping else {}
        delivery_targets = {
            "feishu_folder_id": mapping_entry.get("feishu_folder_id", ""),
            "feishu_folder_url": mapping_entry.get("feishu_folder_url", ""),
            "feishu_doc_url": mapping_entry.get("feishu_doc_url", ""),
        }

        # [Phase-1 guest_background] 提取嘉宾并查询背景
        try:
            sys.path.insert(0, os.path.dirname(__file__))
            from guest_background_fetcher import enrich_episode_with_guest_backgrounds
            ep_enriched = enrich_episode_with_guest_backgrounds(ep)
        except Exception as e:
            log_stderr(f"GUEST_BACKGROUND_FAILED ep={ep.get('episode_title','?')}: {e}", "WARNING")
            ep_enriched = dict(ep)
            ep_enriched["guest_names"] = []
            ep_enriched["guest_background_zh"] = ""
            ep_enriched["guest_background_sources"] = []
            ep_enriched["guest_background_confidence"] = "unknown"
            ep_enriched["guest_background_note"] = "处理失败"

        record = {
            "podcast_name": pod_name,
            "podcast_id": pod_id,
            "episode_title": clean_display_text(ep.get("episode_title", "")),
            "episode_id": ep_id,
            "publish_date": date_only,
            "publish_at": pub_at,
            "pub_datetime": ep.get("pub_datetime", ""),
            "duration_seconds": ep.get("duration_seconds", 0),
            "duration_minutes": ep.get("duration_minutes", 0),
            "audio_url": ep.get("audio_url", ""),
            "language": lang,
            "score": round(score, 1),
            "decision": decision,
            "decision_reason_code": (
                decision_result["reason_code"] if decision_result else f"score_based_{decision}"
            ),
            "decision_reason_zh": (
                decision_result["reason_zh"] if decision_result else reason_zh
            ),
            "topics": [],
            "keywords": [],
            "reason": summary["why_important"],
            # v2.3 结构化评分字段
            "topic_relevance": scoring["topic_relevance"],
            "information_density": scoring["information_density"],
            "novelty": scoring["novelty"],
            "actionability": scoring["actionability"],
            "strategic_value": scoring["strategic_value"],
            "transcription_value": scoring["transcription_value"],
            "final_score": scoring["final_score"],
            "reason_zh": reason_zh,
            "uncertainty_zh": uncertainty_zh,
            "summary_3_sentences_cn": summary["summary_3_sentences_cn"],
            "one_line_summary_cn": summary.get("one_line_summary_cn", ""),
            "key_points_cn": summary["key_points_cn"],
            "why_important": summary["why_important"],
            # v2.1+ 新增
            "show_notes_text": show_notes_text,
            # RSS Ingestion Fix 新增审计字段
            "show_notes_source": ep.get("show_notes_source", ""),
            "show_notes_text_len": ep.get("show_notes_text_len", 0),
            "show_notes_truncated": ep.get("show_notes_truncated", False),
            "rss_description_len": ep.get("rss_description_len", 0),
            "rss_content_encoded_len": ep.get("rss_content_encoded_len", 0),
            "rss_itunes_summary_len": ep.get("rss_itunes_summary_len", 0),
            # v2.4 新增字段
            "priority": priority,
            "full_suggestion": full_suggestion,
            "selection_policy_mode": sel_mode,
            # Phase-1 嘉宾背景字段（v2.5 新增 guest_detection_status）
            "guest_detection_status": ep_enriched.get("guest_detection_status", "no_guest_detected"),
            "guest_names": ep_enriched.get("guest_names", []),
            "guest_background_zh": ep_enriched.get("guest_background_zh", ""),
            "guest_background_sources": ep_enriched.get("guest_background_sources", []),
            "guest_background_confidence": ep_enriched.get("guest_background_confidence", "unknown"),
            "guest_background_note": ep_enriched.get("guest_background_note", ""),
            "guest_detection_evidence": ep_enriched.get("guest_detection_evidence", ""),
            "registry_status": {
                "first_seen": first_seen,
                "first_seen_week_id": first_seen_week_id,
                "last_seen": last_seen,
                "seen_count": seen_count,
                "previous_decision": existing.get("decision"),
                "decision_history_count": len(statuses),
            },
            "delivery_targets": delivery_targets,
        }
        results.append(record)

        reg_rec = {
            "episode_id": ep_id,
            "podcast_id": pod_id,
            "episode_title": ep["episode_title"],
            "first_seen": first_seen,
            "first_seen_week_id": first_seen_week_id,
            "last_seen": last_seen,
            "seen_count": seen_count,
            "decision": decision,
            "statuses": statuses,
        }
        registry[ep_id] = reg_rec

    # 6. 降级
    results.sort(key=lambda x: x["score"], reverse=True)

    # [fix-priority] all_preview 模式：在所有 episode 评分完成后，
    # 用相对分位重新计算所有 preview episode 的 priority
    if sel_mode == "all_preview" and all_preview_scores:
        for r in results:
            if r["decision"] == "preview":
                r["priority"] = compute_priority(
                    r["final_score"],
                    all_preview_scores,
                    sel_mode,
                )
    full_limit = policy.get("processing_policy", {}).get("weekly_full_limit", 5)
    preview_limit = policy.get("processing_policy", {}).get("weekly_preview_limit", 15)

    # [v2.4] all_preview 模式下：decision 已由 decide_in_all_preview_mode 决定，
    # 不再做基于分数的降级（full/preview 已固定）。仅保留 skip 的数量限制兜底。
    full_list = [r for r in results if r["decision"] == "full"]
    preview_list = [r for r in results if r["decision"] == "preview"]
    skip_list = [r for r in results if r["decision"] == "skip"]

    if sel_mode == "score_based":
        for r in full_list[full_limit:]:
            r["decision"] = "preview"
            r["reason"] += "（超出周限，降级为preview）"
            ep_id = r["episode_id"]
            if ep_id in registry:
                registry[ep_id]["decision"] = "preview"
                registry[ep_id]["statuses"] = registry[ep_id].get("statuses", [])[-MAX_STATUSES:] + ["preview"]

        for r in preview_list[preview_limit:]:
            r["decision"] = "skip"
            r["reason"] += "（超出周限，降级为skip）"
            ep_id = r["episode_id"]
            if ep_id in registry:
                registry[ep_id]["decision"] = "skip"
                registry[ep_id]["statuses"] = registry[ep_id].get("statuses", [])[-MAX_STATUSES:] + ["skip"]

    full_decided = [r for r in results if r["decision"] == "full"]
    preview_decided = [r for r in results if r["decision"] == "preview"]
    skip_decided = [r for r in results if r["decision"] == "skip"]

    # [fix-w4] 输出 JSON（双路径）
    today_str = datetime.now(TZ_SH).strftime("%Y-%m-%d")
    run_dir = os.path.join(RUNS_OUT_DIR, week_id, RUN_ID)
    os.makedirs(run_dir, exist_ok=True)

    # [fix-w4] fail-fast：输出目录名与 JSON 中 week_id 一致性校验
    dir_week_id = os.path.basename(os.path.dirname(run_dir))
    if not validate_output_dir_and_json(dir_week_id, week_id):
        log_stderr(
            f"OUTPUT_DIR_MISMATCH: dir_name={dir_week_id} but JSON week_id={week_id}. "
            f"run_dir={run_dir}. FAILING.",
            "ERROR"
        )
        sys.exit(1)

    output_payload = {
        "run_id": RUN_ID,
        "week_id": week_id,
        "window_start": window_start_str,
        "window_end": window_end_str,
        "timezone": "Asia/Shanghai",
        "interval_rule": "published_at >= window_start and published_at < window_end",
        "window_semantics": "last_completed_business_week",
        "scan_date": today_str,
        "total_episodes": len(all_episodes),
        "fetch_errors": fetch_errors,
        "runtime_metadata": build_runtime_metadata(policy),
        "full": full_decided,
        "preview": preview_decided,
        "skip": skip_decided,
    }

    result_path = os.path.join(run_dir, "screening_result.json")

    # 8. 报告
    configured_podcast_count = len([p for p in podcasts_cfg.get('podcasts', []) if p.get('rss_url')])
    show_notes_translation_enabled, show_notes_translation_options = build_show_notes_translation_render_options(policy)
    report = build_markdown_report(
        output_payload,
        podcast_count=configured_podcast_count,
        show_notes_translation_enabled=show_notes_translation_enabled,
        show_notes_translation_options=show_notes_translation_options,
        show_notes_metadata_sink=store_show_notes_display_metadata,
    )
    output_payload["show_notes_translation_summary"] = (
        build_show_notes_translation_summary(output_payload)
    )
    # Persist after rendering so every episode includes display diagnostics.
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, ensure_ascii=False, indent=2)
    # 只写入 run_dir，latest 由 update_latest_pointers 统一更新
    report_path = os.path.join(run_dir, "screening_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    # ── 更新 latest 指针（所有文件已就绪后） ──────────────────────────
    update_latest_pointers(run_dir, result_path, report_path)

    hist_json = os.path.join(OUTPUT_DIR, f"screening_{today_str}_{RUN_ID}.json")
    with open(hist_json, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, ensure_ascii=False, indent=2)

    # 9. 写状态文件
    write_registry_deduplicated(registry)

    run_meta = {
        "run_id": RUN_ID,
        "week_id": week_id,
        "timestamp": RUN_TS,
        "window_start": window_start_str,
        "window_end": window_end_str,
        "total": len(all_episodes),
        "full": len(full_decided),
        "preview": len(preview_decided),
        "skip": len(skip_decided),
        "fetch_errors": fetch_errors,
    }
    append_weekly_run(run_meta)

    log("SCREENING_COMPLETED")

    print("\n" + "=" * 50, flush=True)
    print(report, flush=True)
    print("=" * 50, flush=True)
    print(f"\n📁 Run dir: {run_dir}", flush=True)
    print(f"📁 JSON: {os.path.join(run_dir, 'screening_result.json')}", flush=True)
    print(f"📁 Report: {os.path.join(run_dir, 'screening_report.md')}", flush=True)

    return (len(all_episodes), len(full_decided), len(preview_decided),
            len(skip_decided), fetch_errors)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Podcast Weekly Screener")
    parser.add_argument("--run-date", dest="run_date", default=None,
                        help="Date string (YYYY-MM-DD) used as 'now' to compute business week window. "
                             "Example: --run-date 2026-04-23 runs for the week containing that date, "
                             "i.e. window Sunday 22:00 → next Sunday 21:59 Beijing.")
    args = parser.parse_args()

    # 解析并注入 override（main() 会读取此模块级变量）
    import podcast_screener as _mod
    _mod._override_now = None
    if args.run_date:
        from datetime import datetime as dt
        _mod._override_now = dt.strptime(args.run_date, "%Y-%m-%d")
        _mod._override_now = _mod._override_now.replace(tzinfo=TZ_SH)

    try:
        total, full_n, preview_n, skip_n, errors = main()
        log(f"RESULT: total={total} full={full_n} preview={preview_n} skip={skip_n} errors={len(errors)}")
    except Exception as e:
        log_stderr(f"SCREENING_FAILED: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        sys.exit(1)
