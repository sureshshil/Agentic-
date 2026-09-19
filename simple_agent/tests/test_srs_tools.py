"""Regression tests for telegram_bot.py's chat-facing SRS tools
(srs_deck_stats / srs_due_items / srs_record_review) and the memory
topic tagging on remember/recall - the "let the conversational agent
reach into SRS state and quiz the user itself" pieces, as opposed to
the inline-button flow covered by test_srs_callback.py.

No live API calls.

Run: python3 -m unittest tests.test_srs_tools -v   (from simple_agent/)
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import srs  # noqa: E402
import telegram_bot as tb  # noqa: E402


class SrsToolsTest(unittest.TestCase):
    def setUp(self):
        fd, self.kanji_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.kanji_path)
        self.addCleanup(lambda: os.path.exists(self.kanji_path) and os.unlink(self.kanji_path))
        patcher = patch.dict(tb.SRS_KIND_PATHS, {"k": (self.kanji_path, [1, 3, 8, 20])}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_deck_stats_on_an_unknown_deck_errors(self):
        self.assertTrue(tb.srs_deck_stats("nonsense").startswith("Error:"))

    def test_deck_stats_on_an_empty_deck(self):
        self.assertIn("no items introduced yet", tb.srs_deck_stats("kanji"))

    def test_deck_stats_reflects_state(self):
        state = {"決": {"box": 1, "reps": 1, "lapses": 1, "due": "2020-01-01T00:00:00+00:00"}}
        srs.save_state(self.kanji_path, state)
        summary = tb.srs_deck_stats("kanji")
        self.assertIn("1 item(s) total", summary)
        self.assertIn("1 lifetime lapse(s)", summary)

    def test_due_items_lists_nothing_for_an_empty_deck(self):
        self.assertIn("nothing to quiz on", tb.srs_due_items("kanji"))

    def test_due_items_surfaces_the_most_overdue_first(self):
        state = {
            "決": {"box": 1, "reps": 1, "lapses": 0, "due": "2020-01-01T00:00:00+00:00"},
            "作": {"box": 1, "reps": 1, "lapses": 0, "due": "2019-01-01T00:00:00+00:00"},
        }
        srs.save_state(self.kanji_path, state)
        lines = tb.srs_due_items("kanji").splitlines()
        self.assertTrue(lines[0].startswith("作"))

    def test_due_items_includes_the_most_recent_note(self):
        state = {"決": {"box": 1, "reps": 1, "lapses": 1, "notes": [{"note": "mixed up with 快い", "at": "x"}]}}
        srs.save_state(self.kanji_path, state)
        self.assertIn("mixed up with 快い", tb.srs_due_items("kanji"))

    def test_record_review_refuses_an_item_not_in_state(self):
        result = tb.srs_record_review("kanji", "決", "good")
        self.assertTrue(result.startswith("Error:"))
        self.assertEqual(srs.load_state(self.kanji_path), {})

    def test_record_review_refuses_an_unknown_rating(self):
        srs.save_state(self.kanji_path, {"決": {"box": 0, "reps": 0, "lapses": 0}})
        result = tb.srs_record_review("kanji", "決", "meh")
        self.assertTrue(result.startswith("Error:"))

    def test_record_review_applies_the_rating_and_persists_it(self):
        srs.save_state(self.kanji_path, {"決": {"box": 0, "reps": 0, "lapses": 0}})
        result = tb.srs_record_review("kanji", "決", "good")
        self.assertIn("next review in", result)
        state = srs.load_state(self.kanji_path)
        self.assertEqual(state["決"]["box"], 1)
        self.assertEqual(state["決"]["last_result"], "good")

    def test_record_review_with_a_note_saves_it_on_the_item(self):
        srs.save_state(self.kanji_path, {"決": {"box": 0, "reps": 0, "lapses": 0}})
        tb.srs_record_review("kanji", "決", "again", note="confused with 快い")
        state = srs.load_state(self.kanji_path)
        self.assertEqual(state["決"]["notes"][-1]["note"], "confused with 快い")


class MemoryTopicTest(unittest.TestCase):
    def setUp(self):
        fd, self.memory_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.memory_path)
        self.addCleanup(lambda: os.path.exists(self.memory_path) and os.unlink(self.memory_path))
        self._orig_memory_path = tb.MEMORY_PATH
        tb.MEMORY_PATH = self.memory_path
        self.addCleanup(lambda: setattr(tb, "MEMORY_PATH", self._orig_memory_path))

    def test_remember_defaults_to_general_topic(self):
        tb.remember("likes tea")
        self.assertEqual(tb.load_memory()[0]["topic"], "general")

    def test_remember_stores_the_given_topic(self):
        tb.remember("always confuses 快い/怠い", topic="jlpt")
        self.assertEqual(tb.load_memory()[0]["topic"], "jlpt")

    def test_recall_filters_by_topic(self):
        tb.remember("likes tea", topic="general")
        tb.remember("always confuses 快い/怠い", topic="jlpt")
        result = tb.recall("jlpt")
        self.assertIn("快い", result)
        self.assertNotIn("tea", result)

    def test_recall_numbering_stays_stable_across_topics(self):
        tb.remember("likes tea", topic="general")
        tb.remember("always confuses 快い/怠い", topic="jlpt")
        result = tb.recall("jlpt")
        self.assertTrue(result.startswith("2."))

    def test_recall_with_no_matches_for_a_topic(self):
        tb.remember("likes tea", topic="general")
        self.assertIn("No memories stored under topic", tb.recall("nonexistent"))

    def test_old_entries_without_a_topic_default_to_general_on_recall(self):
        import json

        with open(self.memory_path, "w") as f:
            json.dump([{"fact": "old fact", "remembered_at": "2020-01-01"}], f)
        self.assertIn("(general)", tb.recall())


if __name__ == "__main__":
    unittest.main()
