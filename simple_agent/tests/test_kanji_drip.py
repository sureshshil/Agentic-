"""Regression tests for kanji_drip.py's rendering: build_body_lines must
show the CSV's own curated mnemonic (`disc_note`) directly, and must
still show the rest of the curated content exactly as before when no
enrichment is given, only ADDING the AI practice block (via
llm_enrich.format_blocks) - never replacing anything, and never an
AI-generated mnemonic/tip (see llm_enrich.enrich_kanji's docstring for
why) - when llm_enrich.enrich_kanji() did produce a result.

Run: python3 -m unittest tests.test_kanji_drip -v   (from simple_agent/)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scheduled"))
import kanji_drip as kd  # noqa: E402

_ROW = {
    "kanji": "決",
    "onyomi": "ケツ",
    "kunyomi": "き.める",
    "meaning_en": "decide",
    "meaning_ne": "",
    "component": "",
    "confusable": "",
    "words": "決定 — decision — ",
    "example": "彼は決めた。",
    "example_en": "He decided.",
}


class BuildBodyLinesTest(unittest.TestCase):
    def test_without_enrichment_matches_the_original_curated_output(self):
        lines = kd.build_body_lines(_ROW)
        joined = "\n".join(lines)
        self.assertIn("彼は決めた。", joined)
        self.assertNotIn("\U0001f916", joined)  # no AI-labelled line

    def test_with_enrichment_appends_the_ai_practice_block(self):
        enrichment = {
            "examples": [{"jp": "決断[けつだん]した。", "reading": "けつだんした。", "en": "I made a decision."}],
            "explanation": "Uses 決断する for a weightier decision than 決める.",
            "dialogue": [{"speaker": "A", "jp": "もう決めた？", "en": "Have you decided yet?"}],
            "practice_question": {"question": "q", "options": ["a", "b"], "answer": "a"},
        }
        lines = kd.build_body_lines(_ROW, enrichment)
        joined = "\n".join(lines)
        self.assertIn("彼は決めた。", joined)  # curated example still present
        self.assertIn("決断[けつだん]した。", joined)
        self.assertIn("I made a decision.", joined)
        self.assertIn("weightier decision", joined)
        self.assertIn("もう決[き]めた？", joined)
        self.assertIn("\U0001f916", joined)

    def test_enrichment_without_examples_omits_the_example_blocks(self):
        enrichment = {"explanation": "just a note"}
        lines = kd.build_body_lines(_ROW, enrichment)
        self.assertNotIn("例文1", "\n".join(lines))

    def test_disc_note_is_shown_as_a_curated_hint_regardless_of_enrichment(self):
        row = {**_ROW, "disc_note": "決 has 夬 on the right; 快 (かい, pleasant) shares that same component."}
        lines = kd.build_body_lines(row)
        joined = "\n".join(lines)
        self.assertIn("快", joined)
        self.assertIn("ヒント", joined)
        self.assertNotIn("\U0001f916", joined)  # curated, not AI-labelled

    def test_missing_disc_note_omits_the_hint_line(self):
        lines = kd.build_body_lines(_ROW)  # no disc_note key at all
        self.assertNotIn("ヒント", "\n".join(lines))

    def test_format_card_passes_enrichment_through(self):
        enrichment = {"examples": [{"jp": "決断[けつだん]した。", "en": "I made a decision."}]}
        html_text = kd.format_card(_ROW, "new", 0, "N3", enrichment)
        self.assertIn("決断[けつだん]した。", html_text)

    def test_format_card_without_enrichment_is_unchanged(self):
        html_text = kd.format_card(_ROW, "new", 0, "N3")
        self.assertNotIn("\U0001f916", html_text)


if __name__ == "__main__":
    unittest.main()
