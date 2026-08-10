#!/usr/bin/env python3
"""OpenClaw subprocess adapter for translating one Show Notes chunk.

This module is intentionally not wired into production config/rendering yet.
Callers must inject fake subprocess runners in tests; production use requires a
separate dry-run and explicit approval.
"""

from __future__ import annotations

import json
import subprocess

from show_notes_translation_runner import append_missing_source_urls_to_translation


DEFAULT_OPENCLAW_SHOW_NOTES_TRANSLATION_MODEL = "minimax-portal/MiniMax-M2.7"
DEFAULT_OPENCLAW_TRANSLATION_TIMEOUT_SECONDS = 120


def build_openclaw_show_notes_translation_prompt(
    chunk: str,
    target_language: str = "zh",
) -> str:
    language_label = "中文" if target_language == "zh" else target_language
    return (
        f"请将以下 Show Notes 翻译成{language_label}。\n\n"
        "要求：\n"
        "- 只输出译文，不输出说明、前言、总结或注释。\n"
        "- 不总结，不删减，不新增解释。\n"
        "- 保留 URL，必须逐字复制原文中的每一个 URL，不要删除、不要翻译、不要改写或替换链接。\n"
        "- 保留时间戳。\n"
        "- 保留项目符号、编号、段落和列表结构。\n"
        "- 保留人名、公司名、播客名、产品名、技术词和专有名词。\n"
        "- 章节标题、链接标题、资源标题、更正和免责声明的说明文字也必须翻译，不得原样照抄英文。\n"
        "- 文章、书籍、报告和链接标题必须翻译成中文；“标题 | 出版方”只保留出版方原名。\n"
        "- 如果广告、免责声明、footer 已被上游过滤，不要重新补充。\n"
        "- 无法确定的专有名词保留原文。\n\n"
        "待翻译 Show Notes：\n"
        f"{chunk}"
    )


def translate_show_notes_chunk_with_openclaw(
    chunk: str,
    target_language: str = "zh",
    *,
    openclaw_command: list[str] | None = None,
    agent_id: str = "",
    model_name: str = DEFAULT_OPENCLAW_SHOW_NOTES_TRANSLATION_MODEL,
    timeout_seconds: int = DEFAULT_OPENCLAW_TRANSLATION_TIMEOUT_SECONDS,
    run_subprocess=subprocess.run,
) -> str:
    """Translate one Show Notes chunk via OpenClaw and return translated text.

    Current OpenClaw CLI does not accept top-level ``--model``. Model and
    account selection is managed by OpenClaw profile/agent configuration.
    ``model_name`` is retained for metadata and future mapping only.
    """
    if not isinstance(chunk, str) or not chunk.strip():
        raise ValueError("chunk must be a non-empty string")

    prompt = build_openclaw_show_notes_translation_prompt(chunk, target_language=target_language)
    if openclaw_command is None:
        command = ["openclaw", "agent"]
    else:
        command = list(openclaw_command)
    if agent_id and "--agent" not in command:
        command.extend(["--agent", agent_id])
    command = command + ["--message", prompt, "--json", "--timeout", str(timeout_seconds)]

    try:
        result = run_subprocess(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"OpenClaw show notes translation timed out: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"OpenClaw show notes translation failed: {exc}") from exc

    stderr = (getattr(result, "stderr", "") or "").strip()
    stdout = (getattr(result, "stdout", "") or "").strip()
    returncode = getattr(result, "returncode", 0)
    if returncode != 0:
        raise RuntimeError(
            f"OpenClaw show notes translation failed with returncode {returncode}; "
            f"stderr_excerpt={_truncate_for_error(stderr)}; "
            f"stdout_schema={summarize_openclaw_json_stdout_schema(stdout)}; "
            f"stdout_excerpt={_truncate_for_error(stdout)}"
        )

    try:
        translated_text = extract_openclaw_translation_text(stdout)
    except RuntimeError as exc:
        raise RuntimeError(
            f"{exc}; "
            f"stdout_schema={summarize_openclaw_json_stdout_schema(stdout)}; "
            f"stdout_excerpt={_truncate_for_error(stdout)}; "
            f"stderr_excerpt={_truncate_for_error(stderr)}"
        ) from exc
    return append_missing_source_urls_to_translation(chunk, translated_text)


def extract_openclaw_translation_text(stdout: str) -> str:
    """Extract translated text from OpenClaw JSON stdout or plain text stdout."""
    output = (stdout or "").strip()
    if not output:
        raise RuntimeError("OpenClaw show notes translation returned empty stdout")

    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return output

    text = _extract_text_from_json_value(parsed)
    if text:
        return text
    raise RuntimeError("OpenClaw show notes translation returned no translation text")


def summarize_openclaw_json_stdout_schema(stdout: str) -> str:
    """Return a safe structural summary of OpenClaw stdout without values."""
    output = (stdout or "").strip()
    if not output:
        return "empty stdout"
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return "non-json stdout"

    if isinstance(parsed, dict):
        parts = [f"json dict keys={_sorted_keys(parsed)}"]
        for key in ("message", "data", "result", "response", "output"):
            nested = parsed.get(key)
            if isinstance(nested, dict):
                parts.append(f"{key} keys={_sorted_keys(nested)}")
                payloads = nested.get("payloads")
                if isinstance(payloads, list):
                    parts.append(f"{key}.payloads len={len(payloads)}")
                    first = payloads[0] if payloads else None
                    if isinstance(first, dict):
                        parts.append(f"{key}.payloads[0] first_keys={_sorted_keys(first)}")
                    elif first is not None:
                        parts.append(f"{key}.payloads[0] first_type={type(first).__name__}")
        return "; ".join(parts)
    if isinstance(parsed, list):
        first = parsed[0] if parsed else None
        first_type = type(first).__name__
        if isinstance(first, dict):
            return f"json list len={len(parsed)} first_type=dict first_keys={_sorted_keys(first)}"
        return f"json list len={len(parsed)} first_type={first_type}"
    return f"json {type(parsed).__name__}"


def _truncate_for_error(value: object, max_chars: int = 1200) -> str:
    try:
        text = "" if value is None else str(value)
    except Exception:
        text = "<unprintable>"
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...<truncated>"


def _sorted_keys(value: dict) -> list[str]:
    return sorted(str(key) for key in value.keys())


def _extract_text_from_json_value(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        for item in value:
            text = _extract_text_from_json_value(item)
            if text:
                return text
        return ""
    if not isinstance(value, dict):
        return ""

    for key in ("text", "reply", "message", "content", "output", "response"):
        text = _extract_text_from_json_value(value.get(key))
        if text:
            return text
    payloads = value.get("payloads")
    if isinstance(payloads, list):
        text = _extract_text_from_json_value(payloads)
        if text:
            return text
    for key in ("data", "result"):
        text = _extract_text_from_json_value(value.get(key))
        if text:
            return text
    return ""
