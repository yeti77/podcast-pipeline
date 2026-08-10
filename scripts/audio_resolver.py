#!/usr/bin/env python3
"""
audio_resolver.py — 从标准播客 RSS <enclosure> 获取真实音频直链
Phase 2 音频获取层，独立模块
"""

import os
import re
import json
import yaml
import subprocess
import sys
from typing import Optional
from pipeline_paths import get_pipeline_paths

_RUNTIME_PATHS = get_pipeline_paths()
PIPELINE_DIR = str(_RUNTIME_PATHS.pipeline_dir)
CONFIG_DIR = str(_RUNTIME_PATHS.config_dir)

# ── RSS Feed 注册表（优先从配置文件读，运行时缓存） ──────────────────────
_RSS_TABLE = {}          # podcast_name → rss_url
_ITUNES_CACHE = {}       # podcast_name → rss_url (from iTunes API)

def _load_rss_feeds() -> dict:
    """从 podcasts.yaml 加载 rss_url 和 network_mode"""
    path = os.path.join(CONFIG_DIR, "podcasts.yaml")
    try:
        with open(path) as f:
            cfg = yaml.safe_load(f)
        for pod in cfg.get("podcasts", []):
            if pod.get("rss_url"):
                _RSS_TABLE[pod["name"]] = pod["rss_url"]
                _RSS_TABLE[f"__{pod['name']}__mode"] = pod.get("network_mode", "auto")
    except Exception:
        pass
    return _RSS_TABLE

def _fetch_rss_feed_xml(rss_url: str, network_mode: str = "auto") -> str:
    """
    请求 RSS XML，尝试多次（直连 → 代理 → iTunes fallback）
    返回 raw XML 字符串，失败返回空字符串
    """
    cmd = ["curl", "-sL", "--max-time", "30"]

    if network_mode == "proxy":
        cmd += ["--proxy", "http://127.0.0.1:7890"]
    elif network_mode == "direct":
        cmd += ["--noproxy", "*"]

    cmd.append(rss_url)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except Exception:
        pass
    return ""

def _parse_episodes_from_rss(xml: str) -> list:
    """解析 RSS XML，返回 [{title, pub_date, enclosure_url, guid}, ...]"""
    episodes = []

    # 匹配每个 <item>...</item>
    items = re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)
    for item in items:
        # title（支持 CDATA）
        t = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>", item)
        title = (t.group(1) or t.group(2) or "").strip()

        # pubDate
        d = re.search(r"<pubDate>(.*?)</pubDate>", item)
        pub_date = ""
        if d:
            pub_raw = d.group(1).strip()
            try:
                # RFC 2822 → YYYY-MM-DD
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(pub_raw)
                pub_date = dt.strftime("%Y-%m-%d")
            except Exception:
                pub_date = pub_raw[:16]  # fallback

        # enclosure
        e = re.search(r'<enclosure[^>]+url="([^"]+)"', item)
        enclosure_url = e.group(1) if e else ""

        # guid
        g = re.search(r"<guid[^>]*>(.*?)</guid>", item, re.DOTALL)
        guid = (g.group(1) or "").strip() if g else enclosure_url  # fallback to URL

        if enclosure_url:
            episodes.append({
                "title": title,
                "pub_date": pub_date,
                "enclosure_url": enclosure_url,
                "guid": guid,
            })

    return episodes

def _search_itunes_rss(podcast_name: str) -> Optional[str]:
    """
    通过 iTunes Search API 获取播客 RSS URL（仅首次或缓存miss时）
    """
    if podcast_name in _ITUNES_CACHE:
        return _ITUNES_CACHE[podcast_name]

    encoded = subprocess.run(
        ["python3", "-c", f"import urllib.parse; print(urllib.parse.quote('{podcast_name}'))"],
        capture_output=True, text=True
    ).stdout.strip()

    url = f"https://itunes.apple.com/search?term={encoded}&media=podcast&limit=3"
    result = subprocess.run(
        ["curl", "-sL", "--max-time", "15", url],
        capture_output=True, text=True
    )

    try:
        data = json.loads(result.stdout)
        for item in data.get("results", []):
            feed = item.get("feedUrl", "")
            if feed:
                _ITUNES_CACHE[podcast_name] = feed
                return feed
    except Exception:
        pass
    return None

def _best_match(episodes: list, episode_hint: str = "", publish_date: str = "") -> dict:
    """
    从 episodes 列表中找出最匹配的一集
    优先：publish_date 精确匹配 > title 关键词匹配 > 返回最近一期
    """
    if not episodes:
        return {}

    candidates = []

    for ep in episodes:
        score = 0
        # publish_date 精确匹配
        if publish_date and ep.get("pub_date", "").startswith(publish_date[:10]):
            score += 100
        # title 包含 hint
        if episode_hint:
            hint_lower = episode_hint.lower()
            title_lower = ep.get("title", "").lower()
            # 关键词重叠
            hint_words = set(re.findall(r'\w+', hint_lower))
            title_words = set(re.findall(r'\w+', title_lower))
            overlap = len(hint_words & title_words)
            score += overlap * 5
            # hint in title
            if hint_lower in title_lower:
                score += 20
            # 连续字符匹配
            for n in range(3, len(hint_lower)):
                substr = hint_lower[:n]
                if substr in title_lower:
                    score += 3
                    break
        candidates.append((score, ep))

    # 优先返回最高分；同分取 pub_date 最新
    candidates.sort(key=lambda x: (x[0], x[1].get("pub_date", "")), reverse=True)
    return candidates[0][1] if candidates else episodes[0]

# ── 主入口函数 ─────────────────────────────────────────────────────────
def resolve(podcast_name: str, episode_hint: str = "", publish_date: str = "") -> dict:
    """
    解析真实音频 URL

    参数：
        podcast_name   - 播客名称（精确匹配 podcasts.yaml 的 name 字段）
        episode_hint   - 节目标题或集数（如 "E151" / "Marc Andreessen on..."）
        publish_date   - 发布日期（YYYY-MM-DD），可选

    返回：
        {"audio_url": str, "title": str, "pub_date": str, "source": str}
        出错时 {"audio_url": "", "error": str}
    """
    _load_rss_feeds()

    # Step 1：找 RSS URL（podcasts.yaml → iTunes API）
    rss_url = _RSS_TABLE.get(podcast_name, "")

    if not rss_url:
        # 从 iTunes API 查
        itunes_rss = _search_itunes_rss(podcast_name)
        if itunes_rss:
            rss_url = itunes_rss
            _RSS_TABLE[podcast_name] = rss_url  # 缓存

    if not rss_url:
        return {"audio_url": "", "error": f"No RSS feed found for: {podcast_name}"}

    # Step 2：请求 RSS XML（根据 podcasts.yaml 的 network_mode 选择策略）
    net_mode = _RSS_TABLE.get(f"__{podcast_name}__mode", "auto")
    if net_mode == "proxy":
        xml = _fetch_rss_feed_xml(rss_url, network_mode="proxy")
    elif net_mode == "direct":
        xml = _fetch_rss_feed_xml(rss_url, network_mode="direct")
    else:
        # auto：先直连，失败则代理
        xml = _fetch_rss_feed_xml(rss_url, network_mode="direct")
        if not xml:
            xml = _fetch_rss_feed_xml(rss_url, network_mode="proxy")

    if not xml:
        return {"audio_url": "", "error": f"Failed to fetch RSS: {rss_url}"}

    # Step 3：解析所有 episode
    episodes = _parse_episodes_from_rss(xml)
    if not episodes:
        return {"audio_url": "", "error": "No episodes found in RSS feed"}

    # Step 4：匹配最佳 episode
    best = _best_match(episodes, episode_hint, publish_date)
    if not best or not best.get("enclosure_url"):
        return {"audio_url": "", "error": "No matching episode found"}

    return {
        "audio_url": best["enclosure_url"],
        "title": best.get("title", ""),
        "pub_date": best.get("pub_date", ""),
        "source": "rss_enclosure",
        "rss_url": rss_url,
    }

# ── CLI 测试入口 ───────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 audio_resolver.py <podcast_name> [episode_hint]")
        print("Example: python3 audio_resolver.py a16Z 'Marc Andreessen on AI'")
        sys.exit(1)

    podcast = sys.argv[1]
    hint = sys.argv[2] if len(sys.argv) > 2 else ""
    date = sys.argv[3] if len(sys.argv) > 3 else ""

    print(f"Resolving audio for: {podcast} | hint={hint!r} | date={date!r}")
    result = resolve(podcast, hint, date)

    if result.get("audio_url"):
        print(f"✅ audio_url: {result['audio_url']}")
        print(f"   title: {result.get('title','')}")
        print(f"   pub_date: {result.get('pub_date','')}")
        print(f"   source: {result.get('source','')}")
    else:
        print(f"❌ Error: {result.get('error', 'unknown')}")
