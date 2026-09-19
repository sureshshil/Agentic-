"""Unit tests for scheduled/jlpt_sinks.py (Google Doc append for JLPT instructor).

Run: python3 -m unittest tests.test_jlpt_sinks -v (from simple_agent/)
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scheduled"))
import jlpt_sinks as sinks  # noqa: E402


class FormatInstructorDayParagraphsTest(unittest.TestCase):
    def test_preamble_and_headers_included_when_empty(self):
        plan_result = {
            "estimated_minutes": 35,
            "blocks": [
                {"kind": "ADVANCE", "source_id": "V001", "item": "場合", "type": "vocab"},
                {"kind": "ADVANCE", "source_id": "K001", "item": "決", "type": "kanji"},
            ],
        }
        reading_item = {"id": "R-N3-001", "visible": "文章テキスト", "answers": "ANSWER1: A"}
        grammar_practice = [("paragraph", "文法練習")]

        paras = sinks._format_instructor_day_paragraphs(
            plan_result, reading_item, grammar_practice, "2026-09-19",
            preamble=True, leading_blank=False,
        )
        texts = [p[0] for p in paras]
        self.assertIn(sinks._GDOC_PREAMBLE_TITLE, texts)
        self.assertIn("JLPT Study Plan — 2026-09-19", texts)
        self.assertIn("- V001 場合 (ADVANCE)", texts)
        self.assertIn("- K001 決 (ADVANCE)", texts)
        self.assertIn("文章テキスト", texts)
        self.assertIn("ANSWER1: A", texts)

    def test_no_blocks_placeholders(self):
        plan_result = {"estimated_minutes": 10, "blocks": []}
        paras = sinks._format_instructor_day_paragraphs(
            plan_result, {}, [], "2026-09-19",
            preamble=False, leading_blank=True,
        )
        texts = [p[0] for p in paras]
        self.assertIn("No vocabulary scheduled today.", texts)
        self.assertIn("No kanji scheduled today.", texts)
        self.assertIn("No grammar scheduled today.", texts)


class GdocAppendInstructorDayTest(unittest.TestCase):
    def test_skipped_when_env_vars_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            res = sinks.gdoc_append_instructor_day({}, {}, [], "2026-09-19")
            self.assertTrue(res.startswith("Google Doc: skipped"))

    @patch("requests.post")
    @patch("requests.get")
    @patch.object(sinks, "_gdoc_access_token", return_value="fake-token")
    def test_successful_append(self, mock_token, mock_get, mock_post):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "body": {"content": [{"endIndex": 10}]}
        }
        mock_post.return_value.status_code = 200

        env = {"JLPT_GDOC_ID": "doc123", "GOOGLE_SERVICE_ACCOUNT_JSON": "/tmp/sa.json"}
        plan_result = {"estimated_minutes": 20, "blocks": []}
        with patch.dict(os.environ, env):
            res = sinks.gdoc_append_instructor_day(plan_result, {}, [], "2026-09-19")
        self.assertIn("Google Doc: appended instructor plan for 2026-09-19", res)
        self.assertEqual(mock_post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
