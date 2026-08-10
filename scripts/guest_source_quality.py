#!/usr/bin/env python3
"""
Pure source-quality rules for guest background evidence.

This module intentionally has no I/O, network, subprocess, cache, or logging
side effects. Keep return values stable because confidence and display safety
logic depend on these labels.
"""

PRIMARY_DOMAINS = {
    "xiaoyuzhoufm.com", "xiaoyuzhoufm", "小宇宙",
    "podcast官方网站", "官方主页",
}
SECONDARY_DOMAINS = {
    "wikipedia.org", "wiki", "wikidata",
    "baike.baidu.com", "baidu.com/wiki",
    "github.com", "arxiv.org", "scholar.google",
    "medium.com", "substack.com",
    "nytimes.com", "wsj.com", "reuters.com", "bloomberg.com",
    "forbes.com", "techcrunch.com", "theverge.com", "wired.com",
    "163.com", "sina.com", "sohu.com", "ifeng.com",
    "tencent.com", "qq.com",
    "youtube.com", "bilibili.com", "b站",
    "linkedin.com",
}
WEAK_INDICATORS = {
    "搜索结果", "相关推荐", "猜你喜欢", "今日热文",
    "聚合站", "列表页", "导航页",
}


def classify_source_quality(result: dict) -> str:
    """
    Phase 2: 对单个搜索结果进行 source_quality 分级。
    返回 'primary' | 'secondary' | 'weak'
    """
    title = result.get("title", "").lower()
    url = result.get("url", "").lower()
    snippet = result.get("snippet", "").lower()

    # Primary: 官方域名
    if any(d in url for d in PRIMARY_DOMAINS):
        return "primary"
    # 直接域名匹配
    for d in PRIMARY_DOMAINS:
        if d in url:
            return "primary"

    # Weak: 聚合/导航
    if any(w in title or w in url for w in WEAK_INDICATORS):
        return "weak"
    # 搜索结果页本身（无具体内容页）
    if "duckduckgo" in url or "google.com/search" in url or "bing.com/search" in url:
        return "weak"
    # snippet 极短（标题党）且无URL → weak；但 Wikipedia/Secondary 域名有 snippet 短的情况不降级
    if len(snippet) < 10 and url and not any(d in url for d in SECONDARY_DOMAINS):
        return "weak"

    # Secondary: 媒体/Wikipedia/学术
    if any(d in url for d in SECONDARY_DOMAINS):
        return "secondary"

    return "secondary"  # 默认二级（假设大多数结果是可用的）


def rate_overall_source_quality(results: list[dict]) -> dict:
    """
    Phase 2: 综合评估所有搜索结果的 source_quality。
    返回 {quality: str, primary_count: int, secondary_count: int, weak_count: int}
    """
    if not results:
        return {"quality": "weak", "primary_count": 0, "secondary_count": 0, "weak_count": 0}

    qualities = [classify_source_quality(r) for r in results]
    primary_count = sum(1 for q in qualities if q == "primary")
    secondary_count = sum(1 for q in qualities if q == "secondary")
    weak_count = sum(1 for q in qualities if q == "weak")

    if primary_count > 0:
        quality = "primary"
    elif secondary_count > 0 and weak_count == 0:
        quality = "secondary"
    elif secondary_count > weak_count:
        quality = "secondary"
    else:
        quality = "weak"

    return {
        "quality": quality,
        "primary_count": primary_count,
        "secondary_count": secondary_count,
        "weak_count": weak_count,
    }
