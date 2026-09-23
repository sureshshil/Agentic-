"""Regression tests for srs.py's Leitner-style scheduler: box transitions
on each rating, due-before-new ordering, and the daily new-item cap.

Run: python3 -m unittest tests.test_srs -v   (from simple_agent/)
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import srs  # noqa: E402


BOX_HOURS = [1, 3, 8, 20]
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class RecordReviewTest(unittest.TestCase):
    def test_good_advances_one_box(self):
        state = {}
        interval = srs.record_review(state, "決", "good", BOX_HOURS, NOW)
        self.assertEqual(state["決"]["box"], 1)
        self.assertEqual(interval, BOX_HOURS[1])

    def test_easy_advances_two_boxes(self):
        state = {}
        srs.record_review(state, "決", "easy", BOX_HOURS, NOW)
        self.assertEqual(state["決"]["box"], 2)

    def test_easy_is_capped_at_the_top_box(self):
        state = {"決": {"box": len(BOX_HOURS) - 1, "reps": 5, "lapses": 0}}
        srs.record_review(state, "決", "easy", BOX_HOURS, NOW)
        self.assertEqual(state["決"]["box"], len(BOX_HOURS) - 1)

    def test_again_halves_the_box_and_counts_a_lapse(self):
        state = {"決": {"box": 3, "reps": 5, "lapses": 0}}
        srs.record_review(state, "決", "again", BOX_HOURS, NOW)
        self.assertEqual(state["決"]["box"], 1)
        self.assertEqual(state["決"]["lapses"], 1)

    def test_again_from_box_one_lands_in_box_zero(self):
        state = {"決": {"box": 1, "reps": 1, "lapses": 0}}
        srs.record_review(state, "決", "again", BOX_HOURS, NOW)
        self.assertEqual(state["決"]["box"], 0)

    def test_hard_keeps_the_box(self):
        state = {"決": {"box": 2, "reps": 3, "lapses": 0}}
        interval = srs.record_review(state, "決", "hard", BOX_HOURS, NOW)
        self.assertEqual(state["決"]["box"], 2)
        self.assertEqual(interval, BOX_HOURS[2])

    def test_good_on_time_gets_no_late_credit(self):
        last = (NOW - timedelta(hours=BOX_HOURS[1])).isoformat()
        state = {"決": {"box": 1, "reps": 1, "lapses": 0, "last_reviewed": last}}
        srs.record_review(state, "決", "good", BOX_HOURS, NOW)
        self.assertEqual(state["決"]["box"], 2)

    def test_good_after_a_long_gap_credits_the_boxes_survived(self):
        # Box 1 (3h) item recalled 10h later - past box 2's 8h too.
        last = (NOW - timedelta(hours=10)).isoformat()
        state = {"決": {"box": 1, "reps": 1, "lapses": 0, "last_reviewed": last}}
        srs.record_review(state, "決", "good", BOX_HOURS, NOW)
        self.assertEqual(state["決"]["box"], 3)

    def test_hard_after_a_long_gap_gets_no_late_credit(self):
        last = (NOW - timedelta(hours=10)).isoformat()
        state = {"決": {"box": 1, "reps": 1, "lapses": 0, "last_reviewed": last}}
        srs.record_review(state, "決", "hard", BOX_HOURS, NOW)
        self.assertEqual(state["決"]["box"], 1)

    def test_long_intervals_are_fuzzed_within_ten_percent(self):
        class FixedRng:
            def uniform(self, a, b):
                return b

        state = {"決": {"box": 0, "reps": 1, "lapses": 0}}
        interval = srs.record_review(state, "決", "good", [1, 100], NOW, rng=FixedRng())
        self.assertEqual(interval, 110)
        self.assertEqual(datetime.fromisoformat(state["決"]["due"]), NOW + timedelta(hours=110))

    def test_short_intervals_are_not_fuzzed(self):
        class FixedRng:
            def uniform(self, a, b):
                return b

        state = {}
        interval = srs.record_review(state, "決", "good", BOX_HOURS, NOW, rng=FixedRng())
        self.assertEqual(interval, BOX_HOURS[1])

    def test_due_date_is_now_plus_the_new_box_interval(self):
        state = {}
        srs.record_review(state, "決", "good", BOX_HOURS, NOW)
        due = datetime.fromisoformat(state["決"]["due"])
        self.assertEqual(due, NOW + timedelta(hours=BOX_HOURS[1]))

    def test_reps_increments_every_call(self):
        state = {}
        srs.record_review(state, "決", "good", BOX_HOURS, NOW)
        srs.record_review(state, "決", "hard", BOX_HOURS, NOW)
        self.assertEqual(state["決"]["reps"], 2)

    def test_unknown_action_raises(self):
        with self.assertRaises(ValueError):
            srs.record_review({}, "決", "meh", BOX_HOURS, NOW)


class MarkSeenTest(unittest.TestCase):
    def test_sets_first_seen_at_and_reports_it_changed(self):
        state = {"決": {"box": 0, "reps": 0, "lapses": 0}}
        changed = srs.mark_seen(state, "決", NOW)
        self.assertTrue(changed)
        self.assertEqual(state["決"]["first_seen_at"], NOW.isoformat())

    def test_second_call_is_a_no_op(self):
        state = {"決": {"box": 0, "reps": 0, "lapses": 0}}
        srs.mark_seen(state, "決", NOW)
        changed = srs.mark_seen(state, "決", NOW + timedelta(hours=1))
        self.assertFalse(changed)
        self.assertEqual(state["決"]["first_seen_at"], NOW.isoformat())  # unchanged

    def test_unknown_key_is_a_no_op(self):
        state = {}
        changed = srs.mark_seen(state, "決", NOW)
        self.assertFalse(changed)
        self.assertEqual(state, {})


class PickNextTest(unittest.TestCase):
    def test_never_seen_item_is_introduced_in_rank_order(self):
        state = {}
        key, is_new = srs.pick_next(["決", "作", "動"], state, NOW, new_per_day=5)
        self.assertEqual(key, "決")
        self.assertTrue(is_new)
        self.assertIn("決", state)  # pick_next records the intro itself
        self.assertEqual(state["決"]["box"], 0)

    def test_due_review_takes_priority_over_a_new_item(self):
        state = {
            "作": {"box": 1, "reps": 1, "lapses": 0, "due": (NOW - timedelta(hours=1)).isoformat()},
        }
        key, is_new = srs.pick_next(["決", "作", "動"], state, NOW, new_per_day=5)
        self.assertEqual(key, "作")
        self.assertFalse(is_new)

    def test_not_yet_due_review_does_not_block_a_new_introduction(self):
        state = {
            "作": {"box": 1, "reps": 1, "lapses": 0, "due": (NOW + timedelta(hours=1)).isoformat()},
        }
        key, is_new = srs.pick_next(["決", "作", "動"], state, NOW, new_per_day=5)
        self.assertEqual(key, "決")
        self.assertTrue(is_new)

    def test_most_overdue_review_wins_when_several_are_due(self):
        state = {
            "作": {"box": 1, "reps": 1, "lapses": 0, "due": (NOW - timedelta(hours=1)).isoformat()},
            "動": {"box": 1, "reps": 1, "lapses": 0, "due": (NOW - timedelta(hours=5)).isoformat()},
        }
        key, is_new = srs.pick_next(["決", "作", "動"], state, NOW, new_per_day=5)
        self.assertEqual(key, "動")

    def test_introduced_but_unrated_item_is_not_due_before_the_resurface_timeout(self):
        state = {}
        srs.pick_next(["決"], state, NOW, new_per_day=5)
        srs.mark_seen(state, "決", NOW)
        second_key, is_new = srs.pick_next(
            ["決"], state, NOW + timedelta(hours=2), new_per_day=5, unrated_resurface_hours=3.0
        )
        self.assertIsNone(second_key)
        self.assertIsNone(is_new)

    def test_unopened_item_never_resurfaces_on_a_timer_alone(self):
        # Never actually opened the card at all (not even mark_seen) -
        # re-pushing it as a "reminder" would just be noise, not a
        # helpful nudge, so it waits indefinitely instead of timing out.
        state = {}
        srs.pick_next(["決"], state, NOW, new_per_day=5)
        key, is_new = srs.pick_next(
            ["決"], state, NOW + timedelta(days=30), new_per_day=5, unrated_resurface_hours=3.0
        )
        self.assertIsNone(key)
        self.assertIsNone(is_new)

    def test_unrated_item_resurfaces_as_due_after_the_timeout_once_seen(self):
        # Opened it (mark_seen) but never rated - now it's fair to treat
        # as an actual reminder instead of silently wasting that day's
        # new-item slot forever.
        state = {}
        srs.pick_next(["決"], state, NOW, new_per_day=5)
        srs.mark_seen(state, "決", NOW + timedelta(hours=1))
        key, is_new = srs.pick_next(
            ["決"], state, NOW + timedelta(hours=5), new_per_day=5, unrated_resurface_hours=3.0
        )
        self.assertEqual(key, "決")
        self.assertFalse(is_new)
        # still unrated - no "due" field yet, distinguishing a reminder
        # from an ordinary rated-then-due review
        self.assertNotIn("due", state["決"])

    def test_resurfaced_unrated_item_still_blocks_a_new_introduction(self):
        state = {}
        srs.pick_next(["決"], state, NOW, new_per_day=5)
        srs.mark_seen(state, "決", NOW)
        key, is_new = srs.pick_next(
            ["決", "作"], state, NOW + timedelta(hours=4), new_per_day=5, unrated_resurface_hours=3.0
        )
        self.assertEqual(key, "決")  # the overdue reminder wins over introducing 作

    def test_daily_new_item_cap_is_respected(self):
        state = {}
        srs.pick_next(["決"], state, NOW, new_per_day=1)
        key, is_new = srs.pick_next(["決", "作"], state, NOW + timedelta(hours=1), new_per_day=1)
        self.assertIsNone(key)
        self.assertIsNone(is_new)

    def test_cap_resets_on_a_new_utc_day(self):
        # 決 was introduced AND rated on day 1 (due far in the future, so
        # it's neither an overdue review nor an unrated reminder) - isolates
        # the cap-reset check from due/resurface interference.
        state = {
            "決": {
                "box": 0,
                "reps": 1,
                "lapses": 0,
                "introduced_at": NOW.isoformat(),
                "due": (NOW + timedelta(hours=100)).isoformat(),
            }
        }
        next_day = NOW + timedelta(days=1)
        key, is_new = srs.pick_next(["決", "作"], state, next_day, new_per_day=1)
        self.assertEqual(key, "作")
        self.assertTrue(is_new)

    def test_nothing_left_to_introduce_returns_none(self):
        state = {"決": {"box": 0, "reps": 0, "lapses": 0, "introduced_at": NOW.isoformat()}}
        key, is_new = srs.pick_next(["決"], state, NOW, new_per_day=5)
        self.assertIsNone(key)
        self.assertIsNone(is_new)


class PickBatchTest(unittest.TestCase):
    def test_fills_the_batch_with_new_items_in_rank_order_when_nothing_is_due(self):
        state = {}
        picks = srs.pick_batch(["決", "作", "動", "成"], state, NOW, batch_size=3, new_per_day=5)
        self.assertEqual([k for k, _ in picks], ["決", "作", "動"])
        self.assertTrue(all(is_new for _, is_new in picks))

    def test_due_items_are_not_picked_twice_within_one_batch(self):
        state = {
            "作": {"box": 1, "reps": 1, "lapses": 0, "due": (NOW - timedelta(hours=1)).isoformat()},
        }
        picks = srs.pick_batch(["決", "作", "動"], state, NOW, batch_size=3, new_per_day=5)
        self.assertEqual([k for k, _ in picks].count("作"), 1)

    def test_stops_early_once_the_new_item_cap_is_hit(self):
        state = {}
        picks = srs.pick_batch(["決", "作", "動", "成"], state, NOW, batch_size=10, new_per_day=2)
        self.assertEqual([k for k, _ in picks], ["決", "作"])

    def test_due_items_fill_the_batch_before_any_new_ones(self):
        state = {
            "作": {"box": 1, "reps": 1, "lapses": 0, "due": (NOW - timedelta(hours=1)).isoformat()},
            "動": {"box": 1, "reps": 1, "lapses": 0, "due": (NOW - timedelta(hours=2)).isoformat()},
        }
        picks = srs.pick_batch(["決", "作", "動", "成"], state, NOW, batch_size=2, new_per_day=5)
        self.assertEqual([k for k, is_new in picks], ["動", "作"])
        self.assertTrue(all(not is_new for _, is_new in picks))


class FormatIntervalTest(unittest.TestCase):
    def test_hours_under_a_day(self):
        self.assertEqual(srs.format_interval(3), "3h")

    def test_days_at_or_above_24_hours(self):
        self.assertEqual(srs.format_interval(48), "2d")

    def test_fractional_days(self):
        self.assertEqual(srs.format_interval(36), "1.5d")


class AddNoteTest(unittest.TestCase):
    def test_appends_a_note_to_an_existing_item(self):
        state = {"決": {"box": 1, "reps": 1, "lapses": 0}}
        changed = srs.add_note(state, "決", "confused with 快い", NOW)
        self.assertTrue(changed)
        self.assertEqual(state["決"]["notes"][-1]["note"], "confused with 快い")
        self.assertEqual(state["決"]["notes"][-1]["at"], NOW.isoformat())

    def test_unknown_key_is_a_no_op(self):
        state = {}
        changed = srs.add_note(state, "決", "note", NOW)
        self.assertFalse(changed)
        self.assertEqual(state, {})

    def test_keeps_only_the_most_recent_ten_notes(self):
        state = {"決": {"box": 1, "reps": 1, "lapses": 0}}
        for i in range(12):
            srs.add_note(state, "決", f"note {i}", NOW)
        notes = state["決"]["notes"]
        self.assertEqual(len(notes), 10)
        self.assertEqual(notes[0]["note"], "note 2")
        self.assertEqual(notes[-1]["note"], "note 11")


class DueItemsTest(unittest.TestCase):
    def test_returns_due_items_most_overdue_first(self):
        state = {
            "決": {"box": 1, "reps": 1, "lapses": 0, "due": (NOW - timedelta(hours=1)).isoformat()},
            "作": {"box": 1, "reps": 1, "lapses": 0, "due": (NOW - timedelta(hours=5)).isoformat()},
            "動": {"box": 1, "reps": 1, "lapses": 0, "due": (NOW + timedelta(hours=1)).isoformat()},
        }
        self.assertEqual(srs.due_items(state, NOW), ["作", "決"])

    def test_respects_the_limit(self):
        state = {
            k: {"box": 0, "reps": 0, "lapses": 0, "due": (NOW - timedelta(hours=1)).isoformat()}
            for k in ("決", "作", "動")
        }
        self.assertEqual(len(srs.due_items(state, NOW, limit=2)), 2)


class WeakestItemsTest(unittest.TestCase):
    def test_orders_by_lapses_then_box(self):
        state = {
            "決": {"box": 2, "reps": 3, "lapses": 1},
            "作": {"box": 0, "reps": 5, "lapses": 3},
            "動": {"box": 0, "reps": 1, "lapses": 0},
        }
        self.assertEqual(srs.weakest_items(state), ["作", "決"])

    def test_leeches_come_first(self):
        state = {
            "決": {"box": 5, "reps": 20, "lapses": 8},  # many lapses, but now stable
            "作": {"box": 1, "reps": 9, "lapses": srs.LEECH_LAPSES},
        }
        self.assertEqual(srs.weakest_items(state), ["作", "決"])


class LeechTest(unittest.TestCase):
    def test_enough_lapses_in_a_low_box_is_a_leech(self):
        self.assertTrue(srs.is_leech({"box": 0, "lapses": srs.LEECH_LAPSES}))

    def test_too_few_lapses_is_not(self):
        self.assertFalse(srs.is_leech({"box": 0, "lapses": srs.LEECH_LAPSES - 1}))

    def test_climbing_past_the_clear_box_stops_being_one(self):
        self.assertFalse(srs.is_leech({"box": srs.LEECH_CLEAR_BOX, "lapses": srs.LEECH_LAPSES}))


class FormatIntervalTest(unittest.TestCase):
    def test_days_are_rounded_to_one_decimal(self):
        self.assertEqual(srs.format_interval(130), "5.4d")
        self.assertEqual(srs.format_interval(720), "30d")
        self.assertEqual(srs.format_interval(8), "8h")


class ReviewCandidatesTest(unittest.TestCase):
    def test_due_items_come_before_weak_items(self):
        state = {
            "決": {"box": 1, "reps": 1, "lapses": 0, "due": (NOW - timedelta(hours=1)).isoformat()},
            "作": {"box": 0, "reps": 5, "lapses": 3},
        }
        self.assertEqual(srs.review_candidates(state, NOW, limit=5), ["決", "作"])

    def test_falls_back_to_weak_items_when_nothing_is_due(self):
        state = {"作": {"box": 0, "reps": 5, "lapses": 3}}
        self.assertEqual(srs.review_candidates(state, NOW, limit=5), ["作"])

    def test_no_due_or_weak_items_returns_empty(self):
        state = {"決": {"box": 1, "reps": 1, "lapses": 0}}
        self.assertEqual(srs.review_candidates(state, NOW, limit=5), [])


class DeckStatsTest(unittest.TestCase):
    def test_counts_total_due_and_lapses(self):
        state = {
            "決": {"box": 1, "reps": 1, "lapses": 1, "due": (NOW - timedelta(hours=1)).isoformat()},
            "作": {"box": 1, "reps": 1, "lapses": 0, "due": (NOW + timedelta(hours=1)).isoformat()},
            "動": {"box": 0, "reps": 0, "lapses": 0},
        }
        stats = srs.deck_stats(state, NOW)
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["due_now"], 1)
        self.assertEqual(stats["total_lapses"], 1)
        self.assertEqual(stats["box_counts"], {1: 2, 0: 1})
        self.assertEqual(stats["leeches"], 0)

    def test_empty_state(self):
        stats = srs.deck_stats({}, NOW)
        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["due_now"], 0)


if __name__ == "__main__":
    unittest.main()
