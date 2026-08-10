#!/usr/bin/env python3
"""RSS network adapter for the podcast screener."""

import html
import json
import os
import re
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo
from pipeline_paths import get_pipeline_paths


TZ_SH = ZoneInfo("Asia/Shanghai")

PIPELINE_DIR = str(get_pipeline_paths().pipeline_dir)
STDERR_FILE = os.path.join(PIPELINE_DIR, "screener_stderr.log")
RUN_ID = datetime.now(TZ_SH).strftime("%Y%m%d_%H%M%S")


def log_stderr(msg: str, level: str = "ERROR"):
    ts = datetime.now(TZ_SH).strftime("%Y-%m-%d %H:%M:%S %Z")
    line = f"[{ts}] [{level}] [{RUN_ID}] {msg}"
    print(line, flush=True)
    with open(STDERR_FILE, "a") as f:
        f.write(line + "\n")


def fetch_feed(url: str, feed_type: str, network_mode: str) -> str:
    proxy = os.environ.get("https_proxy") or os.environ.get("http_proxy") or ""
    cmd = ["curl", "-sL", "--max-time", "30"]
    if proxy and network_mode == "proxy":
        cmd += ["--proxy", proxy]
    cmd.append(url)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
        return result.stdout
    except Exception as e:
        log_stderr(f"FETCH_ERROR {url}: {e}")
        return ""


SHOW_NOTES_MAX_CHARS = 5000


def clean_show_notes(text: str) -> str:
    """
    彻底清洗 HTML 标签、残片、实体、特殊空白字符为纯文本
    策略：html.unescape → 保留结构性标签边界 → re.sub(其它标签) → 去除剩余<>
    """
    if not text:
        return ""
    # 1. 解所有 HTML 实体（包括数字实体 &#38; &#x26; 等）
    text = html.unescape(text)
    # 2. 先保留 HTML 结构边界，避免英文句子、章节、URL 粘连
    text = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', text)
    text = re.sub(r'(?i)<\s*br\s*/?\s*>', '\n', text)
    text = re.sub(r'(?i)</\s*(p|div|li|ul|ol|h[1-6]|blockquote)\s*>', '\n', text)
    text = re.sub(r'(?i)<\s*(li|p|div|ul|ol|h[1-6]|blockquote)[^>]*>', '\n', text)
    # 3. 其它 inline 标签用空格替代，避免 <a> 删除后 URL/正文贴在一起
    text = re.sub(r'<[^>]*>', ' ', text)
    # 4. 去掉 <word... 类残缺标签片段（无 > 的 <... 开头）
    text = re.sub(r'<\w+[^<>]*', ' ', text)
    # 5. 去掉所有剩余单独 < 和 >
    text = text.replace('<', '').replace('>', '')
    # 6. 对少量常见 URL 粘连做保护，如 sentence.www / competitors.https
    text = re.sub(r'([。！？.!?”"）)])(?=(?:https?://|www\.))', r'\1 ', text)
    # 7. 去掉各类特殊空白字符
    text = re.sub(r'[\xa0\u1680\u2000-\u200a\u202f\u205f\u3000\ufeff\r]+', ' ', text)
    # 8. 合并多余空格，但保留段落/列表换行
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def select_best_show_notes(entry: dict) -> dict:
    """
    给定一个 entry 的多个 RSS 原始字段，选择最完整的 show_notes。
    返回 dict 包含：
      show_notes_text      : clean 后的最长文本（最多 SHOW_NOTES_MAX_CHARS）
      show_notes_raw       : 原始 HTML（最长那个字段的 raw）
      show_notes_source   : 字段来源名
      show_notes_text_len  : clean 后的实际长度
      show_notes_truncated : 是否被截断到上限
    """
    # 各字段的 raw 值（可能是 HTML，带 CDATA 或不带）
    raw_desc   = entry.get("rss_description_raw", "") or ""
    raw_enc   = entry.get("rss_content_encoded_raw", "") or ""
    raw_summ  = entry.get("rss_itunes_summary_raw", "") or ""
    raw_sub   = entry.get("rss_itunes_subtitle_raw", "") or ""

    # 清洗各字段
    clean_desc  = clean_show_notes(raw_desc)
    clean_enc   = clean_show_notes(raw_enc)
    clean_summ  = clean_show_notes(raw_summ)
    clean_sub   = clean_show_notes(raw_sub)

    # 长度
    len_desc  = len(clean_desc)
    len_enc   = len(clean_enc)
    len_summ  = len(clean_summ)
    len_sub   = len(clean_sub)

    # 记录字段长度审计信息
    field_lengths = {
        "description":       len_desc,
        "content_encoded":   len_enc,
        "itunes_summary":    len_summ,
        "itunes_subtitle":   len_sub,
    }

    # 选择最长 clean 文本对应的原始字段
    candidates = [
        ("description",     clean_desc,  raw_desc),
        ("content_encoded", clean_enc,   raw_enc),
        ("itunes_summary",  clean_summ,  raw_summ),
        ("itunes_subtitle", clean_sub,   raw_sub),
    ]
    # 按 clean 文本长度降序
    candidates.sort(key=lambda x: len(x[1]), reverse=True)

    best_label  = candidates[0][0]
    best_clean  = candidates[0][1]
    best_raw    = candidates[0][2]

    # 如果最长字段文本 < 20 字节，降级到 description
    if len(best_clean) < 20 and raw_desc:
        best_label = "description"
        best_clean = clean_desc
        best_raw   = raw_desc

    # 截断到上限
    truncated = len(best_clean) > SHOW_NOTES_MAX_CHARS
    if truncated:
        best_clean = best_clean[:SHOW_NOTES_MAX_CHARS]

    return {
        "show_notes_text":       best_clean,
        "show_notes_raw":        best_raw,
        "show_notes_source":     best_label,
        "show_notes_text_len":   len(best_clean),
        "show_notes_truncated":  truncated,
        "rss_description_len":        len_desc,
        "rss_content_encoded_len":     len_enc,
        "rss_itunes_summary_len":      len_summ,
        "rss_itunes_subtitle_len":     len_sub,
    }


def parse_duration_to_seconds(dur: str) -> int:
    value = str(dur or "").strip()
    if not value:
        return 0

    iso_match = re.fullmatch(
        r"P(?:\d+D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?",
        value,
        re.IGNORECASE,
    )
    if iso_match and any(part is not None for part in iso_match.groups()):
        hours, minutes, seconds = (int(part or 0) for part in iso_match.groups())
        return hours * 3600 + minutes * 60 + seconds

    if ':' in value:
        parts = value.split(':')
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
        except (ValueError, IndexError):
            return 0
        return 0

    try:
        return max(0, int(value))
    except ValueError:
        return 0


def parse_duration_to_minutes(dur: str) -> int:
    return parse_duration_to_seconds(dur) // 60


def parse_rss_episodes(xml_content: str, podcast_name: str) -> list:
    episodes = []
    items = re.findall(r'<item>(.*?)</item>', xml_content, re.DOTALL)
    for item in items:
        title_m = re.search(r'<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>', item)
        title = str(title_m.group(1) or title_m.group(2) or "").strip() if title_m else ""
        pub_m = re.search(r'<pubDate>(.*?)</pubDate>', item)
        pub_date = str(pub_m.group(1).strip()) if pub_m else ""
        dur_m = re.search(r'<itunes:duration>(.*?)</itunes:duration>', item)
        duration = str(dur_m.group(1).strip()) if dur_m else ""

        # RSS 原始字段读取（Ingestion Fix）
        # description — allow whitespace between tag and CDATA
        desc_m = re.search(r'<description[^>]*>\s*<!\[CDATA\[(.*?)\]\]>\s*</description>|<description[^>]*>(.*?)</description>', item, re.DOTALL)
        rss_description_raw = str((desc_m.group(1) if desc_m else "") or (desc_m.group(2) if desc_m else "") or "").strip()
        # content:encoded — handle whitespace before CDATA
        enc_m = re.search(r'<content:encoded[^>]*>\s*<!\[CDATA\[(.*?)\]\]>\s*</content:encoded>|<content:encoded[^>]*>(.*?)</content:encoded>', item, re.DOTALL)
        rss_content_encoded_raw = str((enc_m.group(1) if enc_m else "") or (enc_m.group(2) if enc_m else "") or "").strip()
        # itunes:summary
        summ_m = re.search(r'<itunes:summary[^>]*>\s*<!\[CDATA\[(.*?)\]\]>\s*</itunes:summary>|<itunes:summary[^>]*>(.*?)</itunes:summary>', item, re.DOTALL)
        rss_itunes_summary_raw = str((summ_m.group(1) if summ_m else "") or (summ_m.group(2) if summ_m else "") or "").strip()
        # itunes:subtitle
        sub_m = re.search(r'<itunes:subtitle[^>]*>\s*<!\[CDATA\[(.*?)\]\]>\s*</itunes:subtitle>|<itunes:subtitle[^>]*>(.*?)</itunes:subtitle>', item, re.DOTALL)
        rss_itunes_subtitle_raw = str((sub_m.group(1) if sub_m else "") or (sub_m.group(2) if sub_m else "") or "").strip()

        enc = re.search(r'<enclosure[^>]+url="([^"]+)"', item)
        audio_url = str(enc.group(1)) if enc else ""

        publish_date = ""
        pub_datetime = ""   # timezone-aware ISO string
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(pub_date)
            publish_date = dt.strftime("%Y-%m-%d")
            pub_datetime = dt.strftime("%Y-%m-%dT%H:%M:%S%z")
        except:
            pass

        dur_seconds = parse_duration_to_seconds(duration)
        dur_min = dur_seconds // 60

        # 构建 entry 给 select_best_show_notes
        entry_raw = {
            "rss_description_raw":       rss_description_raw,
            "rss_content_encoded_raw":   rss_content_encoded_raw,
            "rss_itunes_summary_raw":    rss_itunes_summary_raw,
            "rss_itunes_subtitle_raw":   rss_itunes_subtitle_raw,
        }
        best = select_best_show_notes(entry_raw)

        episodes.append({
            "podcast_name": podcast_name,
            "episode_title": title,
            "publish_date": publish_date,
            "pub_datetime": pub_datetime,
            "pub_date_raw": pub_date,
            "duration_seconds": dur_seconds,
            "duration_minutes": dur_min,
            "show_notes": best["show_notes_text"],       # 完整 clean 文本（最多 5000 字符）
            "show_notes_raw": best["show_notes_raw"],    # raw HTML
            "show_notes_source": best["show_notes_source"],
            "show_notes_text_len": best["show_notes_text_len"],
            "show_notes_truncated": best["show_notes_truncated"],
            "rss_description_len":       best["rss_description_len"],
            "rss_content_encoded_len":    best["rss_content_encoded_len"],
            "rss_itunes_summary_len":     best["rss_itunes_summary_len"],
            "rss_itunes_subtitle_len":    best["rss_itunes_subtitle_len"],
            "audio_url": audio_url,
        })
    return episodes


def extract_jsonld(html_content: str) -> dict:
    m = re.search(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html_content, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except:
        return {}


def _parse_jsonld_episodes_common(data: dict, podcast_name: str) -> list:
    episodes = []
    for ep in data.get("workExample", []):
        if not isinstance(ep, dict):
            continue
        name = str(ep.get("name") or "")
        date = str(ep.get("datePublished") or "")
        dur = str(ep.get("duration") or "")
        desc = str(ep.get("description") or "")
        audio_url = ""
        offers = ep.get("offers", [])
        if isinstance(offers, list) and offers and isinstance(offers[0], dict):
            audio_url = str(offers[0].get("url") or "")
        dur_seconds = parse_duration_to_seconds(dur)
        dur_min = dur_seconds // 60

        # JSON-LD 只有 description 字段，走单一字段路径
        entry_raw = {
            "rss_description_raw":       desc,
            "rss_content_encoded_raw":   "",
            "rss_itunes_summary_raw":     "",
            "rss_itunes_subtitle_raw":    "",
        }
        best = select_best_show_notes(entry_raw)

        episodes.append({
            "podcast_name": podcast_name,
            "episode_title": name,
            "publish_date": date[:10] if len(date) >= 10 else date,
            "pub_datetime": date,  # JSON-LD 的 datePublished 通常是 naive，保留原值
            "pub_date_raw": date,
            "duration_seconds": dur_seconds,
            "duration_minutes": dur_min,
            "show_notes": best["show_notes_text"],
            "show_notes_raw": best["show_notes_raw"],
            "show_notes_source": best["show_notes_source"],
            "show_notes_text_len": best["show_notes_text_len"],
            "show_notes_truncated": best["show_notes_truncated"],
            "rss_description_len":       best["rss_description_len"],
            "rss_content_encoded_len":    best["rss_content_encoded_len"],
            "rss_itunes_summary_len":     best["rss_itunes_summary_len"],
            "rss_itunes_subtitle_len":    best["rss_itunes_subtitle_len"],
            "audio_url": audio_url,
        })
    return episodes
