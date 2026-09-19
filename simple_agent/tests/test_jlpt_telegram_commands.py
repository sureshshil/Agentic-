"""Unit tests for Telegram bot JLPT commands (/jlpt and /report) in telegram_bot.py.

Run: python3 -m unittest tests.test_jlpt_telegram_commands -v (from simple_agent/)
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import telegram_bot as tb  # noqa: E402


class HandleJlptCommandTest(unittest.TestCase):
    @patch("scheduled.jlpt_item_bank.load_state")
    @patch("scheduled.jlpt_item_bank.load_catalog")
    def test_handle_jlpt_command_returns_formatted_plan(self, mock_catalog, mock_state):
        mock_catalog.return_value = {"V001": {"type": "vocab", "item": "場合"}}
        mock_state.return_value = {
            "pending": ["V001"],
            "items": {
                "V001": {
                    "source_id": "V001",
                    "item": "場合",
                    "type": "vocab",
                    "state": "LEARNING",
                    "cadence_index": 0,
                    "introduced_date": "2026-09-19",
                }
            },
        }

        res = tb.handle_jlpt_command()
        self.assertIn("JLPT Instructor Plan", res)
        self.assertIn("Pending Items (1): V001", res)
        self.assertIn("V001 場合 [vocab]", res)
        self.assertIn("/report <ID> <pass|partial|fail>", res)


class HandleJlptReportCommandTest(unittest.TestCase):
    def test_invalid_syntax_returns_usage(self):
        res = tb.handle_jlpt_report_command("/report V001")
        self.assertTrue(res.startswith("Usage:"))

    def test_invalid_verdict_returns_error(self):
        res = tb.handle_jlpt_report_command("/report V001 invalid_verdict")
        self.assertIn("Invalid verdict 'invalid_verdict'", res)

    @patch("scheduled.jlpt_item_bank.save_state")
    @patch("scheduled.jlpt_item_bank.ingest_evidence")
    @patch("scheduled.jlpt_item_bank.load_state")
    @patch("scheduled.jlpt_item_bank.load_catalog")
    def test_valid_report_records_evidence_and_returns_status(
        self, mock_catalog, mock_state, mock_ingest, mock_save
    ):
        mock_catalog.return_value = {"V011": {"type": "vocab", "item": "決まる"}}
        mock_state.return_value = {
            "items": {
                "V011": {
                    "source_id": "V011",
                    "state": "REVIEW",
                    "next_review": "2026-09-22",
                }
            }
        }

        with patch.dict(os.environ, {}, clear=True):
            res = tb.handle_jlpt_report_command("/report V011 pass good progress")

        self.assertIn("Recorded V011 as PASS", res)
        self.assertIn("State: REVIEW", res)
        self.assertIn("Next review: 2026-09-22", res)
        mock_ingest.assert_called_once()
        mock_save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
