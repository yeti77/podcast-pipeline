#!/usr/bin/env python3
"""
Guest background search adapter.

This module has no cache, model, source-quality, or reporting responsibilities.
External calls are contained in `search_guest_background_openclaw()` and should
be mocked in tests.
"""

import json
import re
import subprocess
import urllib.parse
import urllib.request


def build_guest_search_queries(guest_name: str, affiliation_hint: str = "") -> list[str]:
    query_en = f"{guest_name} biography profile"
    query_zh = f"{guest_name} 嘉宾 简介 背景"
    return [query_zh, query_en]


def _normalize_result_item(item: dict) -> dict:
    return {
        "title": item.get("title", ""),
        "url": item.get("url", ""),
        "snippet": item.get("snippet", item.get("description", "")),
    }


def is_openclaw_help_or_unavailable_output(text: str) -> bool:
    output = (text or "").strip().lower()
    if not output:
        return False
    unavailable_markers = (
        "unknown command",
        "unknown option",
        "usage: openclaw",
        "commands:",
        "docs.openclaw.ai",
        "display help for command",
    )
    return any(marker in output for marker in unavailable_markers)


def parse_openclaw_results(stdout: str, max_results: int = 5) -> list[dict]:
    results = []
    if is_openclaw_help_or_unavailable_output(stdout):
        return results
    try:
        data = json.loads(stdout)
        if isinstance(data, dict):
            results_key = "results" if "results" in data else "items" if "items" in data else None
            if results_key:
                for item in data[results_key][:max_results]:
                    if isinstance(item, dict):
                        results.append(_normalize_result_item(item))
        elif isinstance(data, list):
            for item in data[:max_results]:
                if isinstance(item, dict):
                    results.append(_normalize_result_item(item))
    except json.JSONDecodeError:
        pass
    return results


def parse_duckduckgo_results(html: str, max_results: int = 5) -> list[dict]:
    results = []
    titles = re.findall(r'<a[^>]*class="result__a"[^>]*>([^<]+)</a>', html)
    urls = re.findall(r'<a[^>]*class="result__a"[^>]*href="([^"]*)"', html)
    for i, title in enumerate(titles[:max_results]):
        title_clean = re.sub(r'<[^>]+>', '', title).strip()
        if title_clean:
            url = urls[i] if i < len(urls) else ""
            results.append({
                "title": title_clean,
                "url": url,
                "snippet": "",
            })
    return results


def search_guest_background_openclaw(
    guest_name: str,
    affiliation_hint: str = "",
    max_results: int = 5,
) -> list[dict]:
    all_results = []
    queries = build_guest_search_queries(guest_name, affiliation_hint)

    for query in queries:
        try:
            result = subprocess.run(
                ["openclaw", "web-search", "--query", query, "--limit", str(max_results)],
                capture_output=True, text=True, timeout=30,
            )
            stdout = result.stdout or ""
            stderr = getattr(result, "stderr", "") or ""
            if result.returncode == 0 and stdout and not is_openclaw_help_or_unavailable_output(stdout):
                all_results.extend(parse_openclaw_results(result.stdout, max_results=max_results))
            elif is_openclaw_help_or_unavailable_output(stdout) or is_openclaw_help_or_unavailable_output(stderr):
                continue
        except Exception:
            pass

    return all_results
