#!/usr/bin/env python3
"""Hermetic tests for show-notes translation chunking helpers."""

import os
import tempfile
import unittest

from show_notes_translation_chunker import (
    DEFAULT_TRANSLATION_CHUNK_CHARS,
    split_show_notes_for_translation,
)


def _joined(chunks):
    return "\n\n".join(chunks)


class TestShowNotesTranslationChunker(unittest.TestCase):
    def test_empty_and_non_string_inputs_return_empty_list(self):
        self.assertEqual(split_show_notes_for_translation(None), [])
        self.assertEqual(split_show_notes_for_translation({}), [])
        self.assertEqual(split_show_notes_for_translation([]), [])
        self.assertEqual(split_show_notes_for_translation(""), [])
        self.assertEqual(split_show_notes_for_translation("   "), [])

    def test_short_text_is_not_split_and_preserves_paragraphs(self):
        text = (
            "Today, I’m talking with the CEO of a leading energy technology company.\n\n"
            "We discuss the power grid, AI data centers, electricity demand, and clean energy."
        )

        chunks = split_show_notes_for_translation(text, max_chars=500)

        self.assertEqual(chunks, [text])
        self.assertIn("Today, I’m talking", chunks[0])
        self.assertIn("\n\nWe discuss", chunks[0])

    def test_short_paragraphs_are_merged_then_split_by_max_chars(self):
        paragraphs = [
            "Intro to the episode and guest.",
            "A discussion of AI data centers.",
            "The power grid is under pressure.",
            "Clean energy demand keeps rising.",
            "Storage and batteries become strategic.",
            "Policy and capital allocation matter.",
        ]
        text = "\n\n".join(paragraphs)

        chunks = split_show_notes_for_translation(text, max_chars=120)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 120 for chunk in chunks))
        self.assertEqual(_joined(chunks), text)

    def test_long_paragraph_is_split_on_sentence_boundaries(self):
        sentences = [
            "First sentence explains why AI changes electricity demand.",
            "Second sentence covers grid bottlenecks and permitting timelines.",
            "Third sentence discusses batteries and long-duration storage.",
            "Fourth sentence summarizes what investors should watch.",
        ]
        text = " ".join(sentences)

        chunks = split_show_notes_for_translation(text, max_chars=95)

        self.assertGreater(len(chunks), 1)
        self.assertEqual(" ".join(chunks), text)
        for sentence in sentences:
            self.assertIn(sentence, " ".join(chunks))

    def test_url_is_not_split(self):
        url = "https://www.example.com/very/long/path?with=query&x=1"
        text = f"Read more at {url} and subscribe at www.example.com/newsletter."

        chunks = split_show_notes_for_translation(text, max_chars=45)

        self.assertTrue(any(url in chunk for chunk in chunks))
        self.assertFalse(any("https://www.example.com/very" in chunk and url not in chunk for chunk in chunks))
        self.assertTrue(any("www.example.com/newsletter" in chunk for chunk in chunks))

    def test_markdown_link_is_not_split(self):
        link = "[the full report](https://example.com/full/report?x=1)"
        text = f"See {link} for details about the energy transition."

        chunks = split_show_notes_for_translation(text, max_chars=35)

        self.assertTrue(any(link in chunk for chunk in chunks))
        self.assertEqual(" ".join(chunks), text)

    def test_timestamps_and_bullets_are_preserved(self):
        text = (
            "Timestamps:\n"
            "(00:00:00) – What is driving AI progress?\n"
            "(00:03:11) – Comparing human vs AI sample efficiency\n\n"
            "- The power grid supply crunch\n"
            "- Why electricity prices may surge\n"
            "- How batteries scale"
        )

        chunks = split_show_notes_for_translation(text, max_chars=95)
        combined = _joined(chunks)

        self.assertIn("Timestamps:", combined)
        self.assertIn("(00:00:00) – What is driving AI progress?", combined)
        self.assertIn("(00:03:11) – Comparing human vs AI sample efficiency", combined)
        self.assertIn("- The power grid supply crunch", combined)
        self.assertIn("- Why electricity prices may surge", combined)
        self.assertIn("- How batteries scale", combined)
        self.assertTrue(all(len(chunk) <= 95 for chunk in chunks))
        self.assertLess(combined.index("(00:00:00)"), combined.index("(00:03:11)"))
        self.assertLess(combined.index("- The power grid"), combined.index("- How batteries scale"))

    def test_realistic_w25_style_show_notes_preserve_structure(self):
        text = (
            "The guest explains why electricity demand is entering a new phase as AI data centers expand.\n\n"
            "Links:\n"
            "Read the analysis at https://example.com/energy/supercycle and subscribe for updates.\n\n"
            "Credits:\n"
            "Produced by Example Studios.\n\n"
            "Subscribe wherever you get podcasts."
        )

        chunks = split_show_notes_for_translation(text, max_chars=110)
        combined = _joined(chunks)

        self.assertIn("Links:", combined)
        self.assertIn("Credits:", combined)
        self.assertIn("https://example.com/energy/supercycle", combined)
        self.assertIn("AI data centers expand", combined)
        self.assertEqual(combined, text)

    def test_short_volts_body_and_chapters_use_separate_chunks(self):
        text = (
            "Most energy decisions are made in state legislatures, where new lawmakers "
            "must evaluate rapidly changing technology.\n\n"
            "Chapters:\n\n"
            "00:00 Introduction\n\n"
            "03:39 How electrification clicked: the 2008 iPhone moment\n\n"
            "06:10 The education gap: why the initiative exists"
        )

        chunks = split_show_notes_for_translation(text, max_chars=1800)

        self.assertEqual(len(chunks), 2)
        self.assertTrue(chunks[0].startswith("Most energy decisions"))
        self.assertTrue(chunks[1].startswith("Chapters:"))
        self.assertIn("00:00 Introduction", chunks[1])
        self.assertIn("06:10 The education gap", chunks[1])
        self.assertEqual(_joined(chunks), text)

    def test_retained_resource_and_correction_headings_start_new_chunks(self):
        for heading in ("Links:", "Additional Reading:", "Resources:", "CORRECTION: Adds disclaimer:"):
            with self.subTest(heading=heading):
                text = f"Translated body candidate.\n\n{heading}\n\nA human-readable line that must be translated"

                chunks = split_show_notes_for_translation(text, max_chars=1800)

                self.assertEqual(len(chunks), 2)
                self.assertTrue(chunks[1].startswith(heading))
                self.assertEqual(_joined(chunks), text)

    def test_w30_hard_fork_spaced_additional_reading_starts_new_chunk(self):
        text = (
            "This week, OpenAI reported that two models escaped their testing sandbox.\n\n"
            "Additional Reading :\n\n"
            "OpenAI Says Its A.I. Models Went Rogue and Attacked a Digital Library\n\n"
            "China Has a New Top Model"
        )

        chunks = split_show_notes_for_translation(text, max_chars=1800)

        self.assertEqual(len(chunks), 2)
        self.assertTrue(chunks[0].startswith("This week"))
        self.assertTrue(chunks[1].startswith("Additional Reading :"))
        self.assertIn("China Has a New Top Model", chunks[1])
        self.assertEqual(_joined(chunks), text)

    def test_spaced_ascii_and_chinese_colons_start_structural_chunks(self):
        for heading in ("Links :", "Additional Reading :", "Resources ：", "Chapters :"):
            with self.subTest(heading=heading):
                text = f"Translated body candidate.\n\n{heading}\n\nA retained line"

                chunks = split_show_notes_for_translation(text, max_chars=1800)

                self.assertEqual(len(chunks), 2)
                self.assertTrue(chunks[1].startswith(heading))

    def test_non_positive_max_chars_falls_back_to_default(self):
        text = "Short show notes."

        self.assertEqual(
            split_show_notes_for_translation(text, max_chars=0),
            split_show_notes_for_translation(text, max_chars=DEFAULT_TRANSLATION_CHUNK_CHARS),
        )
        self.assertEqual(
            split_show_notes_for_translation(text, max_chars=-10),
            split_show_notes_for_translation(text, max_chars=DEFAULT_TRANSLATION_CHUNK_CHARS),
        )

    def test_chunker_is_pure_and_deterministic(self):
        text = "Paragraph one.\n\nParagraph two with https://example.com."

        with tempfile.TemporaryDirectory() as tmpdir:
            before = set(os.listdir(tmpdir))
            first = split_show_notes_for_translation(text, max_chars=40)
            second = split_show_notes_for_translation(text, max_chars=40)
            after = set(os.listdir(tmpdir))

        self.assertEqual(first, second)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
