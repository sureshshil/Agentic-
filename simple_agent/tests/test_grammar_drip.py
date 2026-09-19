"""Regression tests for grammar_drip.py's rendering: build_body_lines
must still show the curated CSV content exactly as before when no
enrichment is given, and only ADD the AI practice block (via
llm_enrich.format_blocks) - never replace anything - when
llm_enrich.enrich_grammar() did produce a result. See
test_kanji_drip.py for the equivalent kanji test.

Run: python3 -m unittest tests.test_grammar_drip -v   (from simple_agent/)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scheduled"))
import grammar_drip as gd  # noqa: E402

_ROW = {
    "grammar": "〜ようになる",
    "formation": "V-る + ようになる",
    "meaning_en": "come to do; reach the point of",
    "meaning_ne": "",
    "nuance": "",
    "contrast": "",
    "ex1": "日本語[にほんご]が話[はな]せるようになった。",
    "ex1_reading": "にほんごが はなせるように なった。",
    "ex1_en": "I came to be able to speak Japanese.",
}


class BuildBodyLinesTest(unittest.TestCase):
    def test_without_enrichment_matches_the_original_curated_output(self):
        lines = gd.build_body_lines(_ROW)
        joined = "\n".join(lines)
        self.assertIn("日本語[にほんご]が話[はな]せるようになった。", joined)
        self.assertNotIn("\U0001f916", joined)  # no AI-labelled line

    def test_with_enrichment_appends_the_ai_practice_block(self):
        enrichment = {
            "examples": [{"jp": "彼[かれ]は毎朝[まいあさ]走[はし]るようになった。", "en": "He came to run every morning."}],
            "explanation": "Often implies a gradual, natural change rather than a deliberate one.",
            "dialogue": [{"speaker": "A", "jp": "最近[さいきん]走[はし]ってる？", "en": "Have you been running lately?"}],
            "practice_question": {"question": "q", "options": ["a", "b"], "answer": "a"},
        }
        lines = gd.build_body_lines(_ROW, enrichment)
        joined = "\n".join(lines)
        self.assertIn("日本語[にほんご]が話[はな]せるようになった。", joined)  # curated example still present
        self.assertIn("彼[かれ]は毎朝[まいあさ]走[はし]るようになった。", joined)
        self.assertIn("He came to run every morning.", joined)
        self.assertIn("gradual, natural change", joined)
        self.assertIn("最近[さいきん]走[はし]ってる？", joined)
        self.assertIn("\U0001f916", joined)

    def test_format_card_passes_enrichment_through(self):
        enrichment = {"examples": [{"jp": "彼[かれ]は毎朝[まいあさ]走[はし]るようになった。", "en": "He came to run every morning."}]}
        html_text = gd.format_card(_ROW, "new", 0, "N3", enrichment)
        self.assertIn("彼[かれ]は毎朝[まいあさ]走[はし]るようになった。", html_text)

    def test_format_card_without_enrichment_is_unchanged(self):
        html_text = gd.format_card(_ROW, "new", 0, "N3")
        self.assertNotIn("\U0001f916", html_text)


if __name__ == "__main__":
    unittest.main()
