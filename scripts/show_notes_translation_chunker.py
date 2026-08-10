#!/usr/bin/env python3
"""Pure helpers for splitting show notes into translation-sized chunks."""

import re


DEFAULT_TRANSLATION_CHUNK_CHARS = 1800

_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(https?://[^)\s]+[^)]*\)")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")
_TIMESTAMP_LINE_RE = re.compile(r"^\(?\d{1,2}:\d{2}(?::\d{2})?\)?")
_BULLET_LINE_RE = re.compile(r"^\s*(?:[-*]\s+|\d+\.\s+)")
_STRUCTURAL_SECTION_HEADING_PATTERNS = (
    (
        "resources",
        re.compile(
            r"^(?:links?|additional\s+reading|resources?)\s*[:：]?\s*$",
            re.IGNORECASE,
        ),
    ),
    (
        "chapters",
        re.compile(r"^(?:chapters?|timestamps?)\s*[:：]?\s*$", re.IGNORECASE),
    ),
    ("correction", re.compile(r"^corrections?\b.*$", re.IGNORECASE)),
    ("disclaimer", re.compile(r"^disclaimers?\b.*$", re.IGNORECASE)),
)


def split_show_notes_for_translation(text: object, max_chars: int = DEFAULT_TRANSLATION_CHUNK_CHARS) -> list:
    if not isinstance(text, str):
        return []

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    if max_chars <= 0:
        max_chars = DEFAULT_TRANSLATION_CHUNK_CHARS

    chunks = []
    current = ""
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", normalized) if paragraph.strip()]

    for paragraph in paragraphs:
        pieces = _split_long_paragraph_for_translation(paragraph, max_chars)
        for piece in pieces:
            if _starts_structural_translation_section(piece) and current:
                chunks.append(current.strip())
                current = piece
                continue
            if not current:
                current = piece
                continue

            candidate = current + "\n\n" + piece
            if len(candidate) <= max_chars:
                current = candidate
            else:
                chunks.append(current.strip())
                current = piece

    if current:
        chunks.append(current.strip())

    return chunks


def _starts_structural_translation_section(text: str) -> bool:
    return bool(classify_translation_chunk_structure(text))


def classify_translation_chunk_structure(text: object) -> str:
    if not isinstance(text, str):
        return ""
    first_line = text.split("\n", 1)[0].strip()
    for category, pattern in _STRUCTURAL_SECTION_HEADING_PATTERNS:
        if pattern.fullmatch(first_line):
            return category
    return ""


def _split_long_paragraph_for_translation(paragraph: str, max_chars: int) -> list:
    paragraph = paragraph.strip()
    if not paragraph or len(paragraph) <= max_chars:
        return [paragraph] if paragraph else []

    lines = paragraph.split("\n")
    if len(lines) > 1 and _contains_protected_line(lines):
        return _pack_units([line.strip() for line in lines if line.strip()], max_chars, separator="\n")

    units = _sentence_units(paragraph)
    return _pack_units(units, max_chars, separator=" ")


def _contains_protected_line(lines: list) -> bool:
    for line in lines:
        stripped = line.strip()
        if _TIMESTAMP_LINE_RE.search(stripped) or _BULLET_LINE_RE.search(stripped):
            return True
    return False


def _sentence_units(paragraph: str) -> list:
    placeholders = {}
    protected = paragraph

    for pattern in (_MARKDOWN_LINK_RE, _URL_RE):
        protected = pattern.sub(lambda match: _store_placeholder(match.group(0), placeholders), protected)

    raw_units = [unit.strip() for unit in _SENTENCE_BOUNDARY_RE.split(protected) if unit.strip()]
    units = [_restore_placeholders(unit, placeholders) for unit in raw_units]
    return units or [paragraph]


def _store_placeholder(value: str, placeholders: dict) -> str:
    key = f"__SHOW_NOTES_TRANSLATION_PROTECTED_{len(placeholders)}__"
    placeholders[key] = value
    return key


def _restore_placeholders(value: str, placeholders: dict) -> str:
    restored = value
    for key, original in placeholders.items():
        restored = restored.replace(key, original)
    return restored


def _pack_units(units: list, max_chars: int, separator: str) -> list:
    chunks = []
    current = ""

    for unit in units:
        if not current:
            current = unit
            continue

        candidate = current + separator + unit
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current.strip())
            current = unit

    if current:
        chunks.append(current.strip())

    return chunks
