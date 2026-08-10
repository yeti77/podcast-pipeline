#!/usr/bin/env python3
"""Pure episode-duration display helpers."""

from __future__ import annotations


DEFAULT_SHORT_EPISODE_MAX_MINUTES = 15


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def format_episode_duration(
    episode: dict,
    *,
    short_episode_max_minutes: int = DEFAULT_SHORT_EPISODE_MAX_MINUTES,
) -> str:
    """Format exact short durations while keeping legacy records compatible."""
    seconds = _nonnegative_int(episode.get("duration_seconds"))
    if seconds:
        if seconds < max(1, short_episode_max_minutes) * 60:
            minutes, remainder = divmod(seconds, 60)
            return f"{minutes}分{remainder:02d}秒（短节目）"
        return f"{seconds // 60}分钟"

    minutes = _nonnegative_int(episode.get("duration_minutes"))
    return f"{minutes}分钟" if minutes else "时长未知"
