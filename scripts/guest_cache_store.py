#!/usr/bin/env python3
"""
Hermetic guest background cache helpers.

This module does not define production paths and does not create directories on
import. Callers pass the cache file explicitly.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


TZ_SH = timezone(timedelta(hours=8))


def load_cache(cache_file: str) -> dict:
    if not cache_file:
        return {}
    try:
        with open(cache_file, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(cache_file: str, data: dict) -> None:
    Path(cache_file).expanduser().parent.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def guest_key(guest_name: str, affiliation_hint: str = "", podcast_title: str = "") -> str:
    raw = f"{guest_name.lower().strip()}|{affiliation_hint.lower().strip()}|{podcast_title.lower().strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def is_cache_entry_valid(
    entry: dict,
    now=None,
    confirmed_ttl_days: int = 90,
    other_ttl_days: int = 30,
) -> bool:
    cached_at = entry.get("cached_at", "") if entry else ""
    if not cached_at:
        return False
    if now is None:
        now = datetime.now(TZ_SH)
    try:
        cached_dt = datetime.fromisoformat(cached_at)
        age_days = (now - cached_dt).days
        detection_status = entry.get("detection_status", "confirmed_guest")
        ttl = confirmed_ttl_days if detection_status == "confirmed_guest" else other_ttl_days
        return age_days <= ttl
    except Exception:
        return False


def get_cache_entry(
    cache_file: str,
    key: str,
    now=None,
    confirmed_ttl_days: int = 90,
    other_ttl_days: int = 30,
) -> Optional[dict]:
    cache = load_cache(cache_file)
    entry = cache.get(key)
    if not entry:
        return None
    if not is_cache_entry_valid(
        entry,
        now=now,
        confirmed_ttl_days=confirmed_ttl_days,
        other_ttl_days=other_ttl_days,
    ):
        return None
    return entry


def write_cache_entry(cache_file: str, key: str, data: dict, now=None) -> None:
    if now is None:
        now = datetime.now(TZ_SH)
    cache = load_cache(cache_file)
    cache[key] = {
        "cached_at": now.isoformat(),
        **data,
    }
    save_cache(cache_file, cache)
