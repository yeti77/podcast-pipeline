#!/usr/bin/env python3
"""Pure show-notes translation runner helpers and deterministic mock runner."""

import re

from show_notes_translation_chunker import classify_translation_chunk_structure


MOCK_SHOW_NOTES_TRANSLATION_MODEL = "mock-show-notes-translator-v1"

URL_FOR_TRANSLATION_PRESERVATION = re.compile(r"(?:https?://|www\.)[^\s<>'\"]+")
URL_TRAILING_PUNCTUATION = ".,)]），。"
LATIN_WORD_FOR_COMPLETENESS = re.compile(r"[A-Za-z][A-Za-z0-9'’.-]*")
TIMESTAMP_LINE_FOR_COMPLETENESS = re.compile(
    r"^\(?\d{1,2}:\d{2}(?::\d{2})?\)?\s*(?:[-–—:]\s*)?(?P<label>.*)$"
)
GENERIC_SINGLE_WORD_TIMESTAMP_LABELS = {
    "chapter",
    "chapters",
    "conclusion",
    "intro",
    "introduction",
    "outro",
    "sponsor",
    "sponsors",
    "summary",
    "welcome",
}


def build_show_notes_translation_prompt(chunk: str, target_language: str = "zh") -> str:
    language_label = "中文" if target_language == "zh" else target_language
    return (
        f"请将以下 Show Notes 翻译成{language_label}。\n"
        "要求：\n"
        "- 保留 URL，不要改写链接。\n"
        "- 保留时间戳。\n"
        "- 保留项目符号和列表结构。\n"
        "- 保留专有名词、人名、公司名和节目名。\n"
        "- 章节标题、链接标题、资源标题、更正和免责声明的说明文字也必须翻译。\n"
        "- 文章、书籍、报告和链接标题必须翻译成中文；“标题 | 出版方”只保留出版方原名。\n"
        "- 不要总结，不要删减，不要新增解释。\n\n"
        "待翻译内容：\n"
        f"{chunk}"
    )


def mock_translate_show_notes_chunk(chunk: str, target_language: str = "zh") -> str:
    del target_language
    return f"【中文翻译/mock】\n{chunk}"


def extract_urls_for_translation_preservation(text: object) -> list[str]:
    if not isinstance(text, str):
        return []

    urls = []
    seen = set()
    for match in URL_FOR_TRANSLATION_PRESERVATION.finditer(text):
        url = match.group(0).rstrip(URL_TRAILING_PUNCTUATION)
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def append_missing_source_urls_to_translation(
    source_text: object,
    translated_text: object,
) -> str:
    translation = translated_text.strip() if isinstance(translated_text, str) else ""
    source_urls = extract_urls_for_translation_preservation(source_text)
    if not source_urls:
        return translation

    missing_urls = [url for url in source_urls if url not in translation]
    if not missing_urls:
        return translation

    url_block = "\n".join(["原文链接：", *[f"- {url}" for url in missing_urls]])
    if not translation:
        return url_block
    return f"{translation}\n\n{url_block}"


def build_original_resource_fallback(source_chunk: object) -> str:
    if not isinstance(source_chunk, str):
        return ""
    source = source_chunk.strip()
    if not source or classify_translation_chunk_structure(source) != "resources":
        return ""

    lines = source.splitlines()
    content = "\n".join(lines[1:]).strip()
    if not content:
        return "延伸阅读（原文）："
    return f"延伸阅读（原文）：\n{content}"


def find_untranslated_source_lines(
    source_text: object,
    translated_text: object,
) -> list[str]:
    """Return significant source lines copied unchanged into a translation."""
    if not isinstance(source_text, str) or not isinstance(translated_text, str):
        return []

    translated_lines = {
        line.strip() for line in translated_text.splitlines() if line.strip()
    }
    unchanged = []
    seen = set()
    for raw_line in source_text.splitlines():
        line = raw_line.strip()
        if not line or line in seen or line not in translated_lines:
            continue
        if _is_url_only_translation_line(line):
            continue

        timestamp_match = TIMESTAMP_LINE_FOR_COMPLETENESS.match(line)
        if timestamp_match:
            label = timestamp_match.group("label")
            significant = bool(LATIN_WORD_FOR_COMPLETENESS.findall(label)) and not (
                _is_preserved_timestamp_entity_label(label)
            )
        else:
            significant = len(LATIN_WORD_FOR_COMPLETENESS.findall(line)) >= 4
        if not significant:
            continue

        seen.add(line)
        unchanged.append(line)
    return unchanged


def _is_preserved_timestamp_entity_label(label: str) -> bool:
    words = LATIN_WORD_FOR_COMPLETENESS.findall(label)
    if not words:
        return False
    normalized_words = [word.strip(".'’-—-") for word in words]
    if not all(word and (word[0].isupper() or word.isupper()) for word in normalized_words):
        return False
    if len(normalized_words) >= 2:
        return True

    word = normalized_words[0]
    return word.lower() not in GENERIC_SINGLE_WORD_TIMESTAMP_LABELS


def _is_url_only_translation_line(line: str) -> bool:
    without_urls = URL_FOR_TRANSLATION_PRESERVATION.sub("", line)
    return not LATIN_WORD_FOR_COMPLETENESS.search(without_urls)


def translate_show_notes_chunks_with_runner(
    chunks: list,
    *,
    translate_chunk,
    target_language: str = "zh",
    model_name: str = MOCK_SHOW_NOTES_TRANSLATION_MODEL,
    validate_translation_completeness: bool = False,
    max_translation_attempts: int = 1,
) -> dict:
    source_chunks = list(chunks or [])
    display_chunks = []
    translated_chunk_count = 0
    localized_fallback_chunk_indices = []
    errors = []

    if not source_chunks:
        return {
            "status": "skipped",
            "translated_text": "",
            "chunk_count": 0,
            "translated_chunk_count": 0,
            "model": model_name,
            "target_language": target_language,
            "errors": [],
            "localized_fallback_chunk_indices": [],
        }

    attempts = max(1, int(max_translation_attempts or 1))
    for index, chunk in enumerate(source_chunks):
        for attempt in range(attempts):
            try:
                translated = translate_chunk(chunk, target_language=target_language)
            except Exception as exc:
                errors.append(
                    {
                        "chunk_index": index,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                break

            if translated is None:
                translated = ""
            translated_text = append_missing_source_urls_to_translation(chunk, translated)
            if not translated_text:
                if attempt + 1 < attempts:
                    continue
                errors.append(
                    {
                        "chunk_index": index,
                        "type": "empty_translation",
                        "error": "translation runner returned empty text",
                    }
                )
                break

            unchanged_lines = (
                find_untranslated_source_lines(chunk, translated_text)
                if validate_translation_completeness
                else []
            )
            if unchanged_lines:
                if attempt + 1 < attempts:
                    continue
                error = {
                    "chunk_index": index,
                    "type": "incomplete_translation",
                    "error": "translation copied significant source lines unchanged",
                    "unchanged_source_lines": unchanged_lines[:8],
                }
                resource_fallback = build_original_resource_fallback(chunk)
                if resource_fallback:
                    error["localized_fallback"] = True
                    localized_fallback_chunk_indices.append(index)
                    display_chunks.append(resource_fallback)
                errors.append(error)
                break

            display_chunks.append(translated_text)
            translated_chunk_count += 1
            break

    if not errors:
        status = "ok"
    elif (
        translated_chunk_count
        and localized_fallback_chunk_indices
        and all(error.get("localized_fallback") for error in errors)
    ):
        status = "partial_ok"
    elif translated_chunk_count:
        status = "partial_failed"
    else:
        status = "failed"

    return {
        "status": status,
        "translated_text": "\n\n".join(display_chunks),
        "chunk_count": len(source_chunks),
        "translated_chunk_count": translated_chunk_count,
        "model": model_name,
        "target_language": target_language,
        "errors": errors,
        "localized_fallback_chunk_indices": localized_fallback_chunk_indices,
    }
