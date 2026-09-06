"""Regression tests for vocab_drip.parse_entries - the parser for the
hidden ---DATA--- JSON block the model appends after the vocab text.

It must never raise: a malformed block just yields [] (history not
updated, Notion sink skipped), and the Telegram drip is unaffected.

Run: python3 -m unittest tests.test_vocab_drip -v   (from simple_agent/)
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scheduled"))
import vocab_drip as vd  # noqa: E402

_ROWS = [
    {"word": "遠慮", "reading": "えんりょ", "meaning": "restraint", "examples": ["a — b"]},
    {"word": "締め切り", "reading": "しめきり", "meaning": "deadline", "examples": ["c — d"]},
]


class ParseEntriesTest(unittest.TestCase):
    def test_plain_json_array(self):
        self.assertEqual(vd.parse_entries(json.dumps(_ROWS)), _ROWS)

    def test_leading_whitespace_and_newline(self):
        self.assertEqual(vd.parse_entries("\n\n  " + json.dumps(_ROWS)), _ROWS)

    def test_strips_json_code_fence(self):
        fenced = "```json\n" + json.dumps(_ROWS) + "\n```"
        self.assertEqual(vd.parse_entries(fenced), _ROWS)

    def test_malformed_json_returns_empty_list(self):
        self.assertEqual(vd.parse_entries("[{'word': broken"), [])

    def test_non_list_json_returns_empty_list(self):
        self.assertEqual(vd.parse_entries('{"word": "x"}'), [])

    def test_entries_without_a_word_are_dropped(self):
        mixed = json.dumps([{"word": "猫", "reading": "ねこ"}, {"reading": "だけ"}, {"word": "  "}])
        self.assertEqual(vd.parse_entries(mixed), [{"word": "猫", "reading": "ねこ"}])

    def test_empty_string_returns_empty_list(self):
        self.assertEqual(vd.parse_entries(""), [])


if __name__ == "__main__":
    unittest.main()
