"""Regression tests for the JLPT instructor's item-bank engine
(scheduled/jlpt_item_bank.py): evidence ingestion (append-only, UNKNOWN
preserved), the CARRY_FORWARD > REPAIR > REVIEW > ADVANCE priority order,
new-item ceilings-not-quotas, and the NEW-happens-once invariant.

No file/network I/O: state dicts are built in memory.

Run: python3 -m unittest tests.test_jlpt_item_bank -v   (from simple_agent/)
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scheduled"))
import jlpt_item_bank as bank  # noqa: E402

NOW = datetime(2026, 9, 16, 1, 0, tzinfo=timezone.utc)

CATALOG = {
    "vocab:食べる": {"type": "vocab", "item": "食べる", "jlpt_level": "N3", "row": {}},
    "vocab:飲む": {"type": "vocab", "item": "飲む", "jlpt_level": "N3", "row": {}},
    "kanji:決": {"type": "kanji", "item": "決", "jlpt_level": "N3", "row": {}},
    "kanji:変": {"type": "kanji", "item": "変", "jlpt_level": "N3", "row": {}},
    "grammar:〜ことにする": {"type": "grammar", "item": "〜ことにする", "jlpt_level": "N3", "row": {}},
}


def fresh_state():
    return {"items": {}, "pending": [], "sessions": []}


class ValidateTest(unittest.TestCase):
    def test_empty_state_is_valid(self):
        self.assertEqual(bank.validate(fresh_state()), [])

    def test_bad_state_value_is_flagged(self):
        state = fresh_state()
        state["items"]["vocab:食べる"] = {
            "source_id": "vocab:食べる", "type": "vocab", "state": "NOT_A_STATE",
            "introduced_date": "2026-09-16", "evidence": [],
        }
        problems = bank.validate(state)
        self.assertTrue(any("invalid state" in p for p in problems))

    def test_missing_introduced_date_is_flagged(self):
        state = fresh_state()
        state["items"]["vocab:食べる"] = {
            "source_id": "vocab:食べる", "type": "vocab", "state": "LEARNING",
            "introduced_date": "", "evidence": [],
        }
        problems = bank.validate(state)
        self.assertTrue(any("introduced_date" in p for p in problems))


class PlanAdvanceTest(unittest.TestCase):
    def test_new_items_respect_per_type_ceilings(self):
        with patch.dict(bank.NEW_CEILINGS, {"vocab": 1, "grammar": 1, "kanji": 1}):
            state = fresh_state()
            result = bank.plan(state, CATALOG, NOW)
        types_introduced = [b["type"] for b in result["blocks"] if b["kind"] == "ADVANCE"]
        self.assertLessEqual(types_introduced.count("vocab"), 1)
        self.assertLessEqual(types_introduced.count("kanji"), 1)
        self.assertLessEqual(types_introduced.count("grammar"), 1)

    def test_new_items_are_a_ceiling_not_a_quota(self):
        # Enough due REVIEW items to exhaust the whole CAPACITY_MINUTES
        # budget on their own - ADVANCE can legitimately contribute zero
        # new items once nothing is left to spend.
        state = fresh_state()
        num_review_items = int(bank.CAPACITY_MINUTES // bank.MINUTES_PER_ITEM["kanji"]) + 3
        for i in range(num_review_items):
            sid = f"kanji:x{i}"
            state["items"][sid] = {
                "source_id": sid, "type": "kanji", "item": f"x{i}", "jlpt_level": "N3",
                "state": "REVIEW", "introduced_date": "2026-09-01", "next_review": "2026-09-16",
                "cadence_index": 1, "open_errors": [], "evidence": [],
                "first_pass_target": False,
            }
        result = bank.plan(state, CATALOG, NOW)
        self.assertEqual(result["new_items"], {"vocab": [], "grammar": [], "kanji": []})

    def test_advance_round_robins_across_types_instead_of_starving_by_id_sort(self):
        # Regression: a plain sorted-by-source_id walk exhausts "G..." ids
        # (alphabetically first) before ever reaching "K.../V..." ones -
        # caught live when a real run introduced 3 grammar items and zero
        # vocab/kanji on an otherwise-empty day.
        catalog = {
            "grammar:g1": {"type": "grammar", "item": "G-A", "jlpt_level": "N3", "row": {}},
            "grammar:g2": {"type": "grammar", "item": "G-B", "jlpt_level": "N3", "row": {}},
            "grammar:g3": {"type": "grammar", "item": "G-C", "jlpt_level": "N3", "row": {}},
            "kanji:k1": {"type": "kanji", "item": "K-A", "jlpt_level": "N3", "row": {}},
            "vocab:v1": {"type": "vocab", "item": "V-A", "jlpt_level": "N3", "row": {}},
        }
        state = fresh_state()
        result = bank.plan(state, catalog, NOW)
        types_introduced = {b["type"] for b in result["blocks"] if b["kind"] == "ADVANCE"}
        self.assertEqual(types_introduced, {"vocab", "kanji", "grammar"})

    def test_item_is_never_introduced_twice(self):
        state = fresh_state()
        first = bank.plan(state, CATALOG, NOW)
        introduced_first = {b["source_id"] for b in first["blocks"] if b["kind"] == "ADVANCE"}
        self.assertTrue(introduced_first)
        # A second plan() call (e.g. re-run) must never re-ADVANCE the same item.
        second = bank.plan(state, CATALOG, NOW)
        introduced_second = {b["source_id"] for b in second["blocks"] if b["kind"] == "ADVANCE"}
        self.assertEqual(introduced_first & introduced_second, set())


class PlanPriorityTest(unittest.TestCase):
    def _repair_item(self, sid):
        return {
            "source_id": sid, "type": "kanji", "item": sid.split(":")[1], "jlpt_level": "N3",
            "state": "REPAIR", "introduced_date": "2026-09-01", "next_review": "2026-09-16",
            "cadence_index": 0, "open_errors": ["error"], "evidence": [], "first_pass_target": False,
        }

    def _review_item(self, sid, next_review="2026-09-16"):
        return {
            "source_id": sid, "type": "grammar", "item": sid.split(":")[1], "jlpt_level": "N3",
            "state": "REVIEW", "introduced_date": "2026-09-01", "next_review": next_review,
            "cadence_index": 1, "open_errors": [], "evidence": [], "first_pass_target": False,
        }

    def test_pending_carry_forward_outranks_everything(self):
        state = fresh_state()
        state["items"]["kanji:決"] = self._repair_item("kanji:決")
        state["items"]["vocab:食べる"] = {
            **self._repair_item("vocab:食べる"), "source_id": "vocab:食べる",
            "type": "vocab", "item": "食べる", "state": "LEARNING",
        }
        state["pending"] = ["vocab:食べる"]
        result = bank.plan(state, CATALOG, NOW)
        self.assertEqual(result["blocks"][0]["kind"], "CARRY_FORWARD")
        self.assertEqual(result["blocks"][0]["source_id"], "vocab:食べる")

    def test_repair_outranks_review(self):
        state = fresh_state()
        state["items"]["kanji:決"] = self._repair_item("kanji:決")
        state["items"]["grammar:〜ことにする"] = self._review_item("grammar:〜ことにする")
        result = bank.plan(state, CATALOG, NOW)
        kinds_by_source = {b["source_id"]: b["kind"] for b in result["blocks"]}
        self.assertEqual(kinds_by_source["kanji:決"], "REPAIR")

    def test_review_not_yet_due_is_skipped(self):
        state = fresh_state()
        state["items"]["grammar:〜ことにする"] = self._review_item(
            "grammar:〜ことにする", next_review="2099-01-01"
        )
        result = bank.plan(state, CATALOG, NOW)
        self.assertNotIn(
            "grammar:〜ことにする", {b["source_id"] for b in result["blocks"] if b["kind"] == "REVIEW"}
        )

    def test_repair_targets_capped(self):
        state = fresh_state()
        for i in range(bank.MAX_REPAIR_TARGETS + 3):
            sid = f"kanji:r{i}"
            state["items"][sid] = self._repair_item(sid)
        result = bank.plan(state, CATALOG, NOW)
        self.assertLessEqual(len(result["repair_targets"]), bank.MAX_REPAIR_TARGETS)


class IngestEvidenceTest(unittest.TestCase):
    def test_evidence_is_appended_not_replaced(self):
        state = fresh_state()
        state["items"]["kanji:決"] = {
            "source_id": "kanji:決", "type": "kanji", "item": "決", "jlpt_level": "N3",
            "state": "LEARNING", "introduced_date": "2026-09-15", "next_review": "2026-09-16",
            "cadence_index": 0, "open_errors": [], "evidence": [{"date": "x", "result": "pass", "note": ""}],
            "first_pass_target": True,
        }
        reports = {"items": [{"source_id": "kanji:決", "result": "partial", "note": "slow"}]}
        bank.ingest_evidence(state, CATALOG, reports, NOW)
        self.assertEqual(len(state["items"]["kanji:決"]["evidence"]), 2)

    def test_unknown_result_preserved_and_does_not_change_state(self):
        state = fresh_state()
        state["items"]["kanji:決"] = {
            "source_id": "kanji:決", "type": "kanji", "item": "決", "jlpt_level": "N3",
            "state": "LEARNING", "introduced_date": "2026-09-15", "next_review": "2026-09-16",
            "cadence_index": 0, "open_errors": [], "evidence": [], "first_pass_target": True,
        }
        reports = {"items": [{"source_id": "kanji:決", "result": "unknown", "note": ""}]}
        bank.ingest_evidence(state, CATALOG, reports, NOW)
        rec = state["items"]["kanji:決"]
        self.assertEqual(rec["state"], "LEARNING")  # unchanged
        self.assertEqual(rec["evidence"][-1]["result"], "unknown")

    def test_fail_moves_item_to_repair(self):
        state = fresh_state()
        state["items"]["kanji:決"] = {
            "source_id": "kanji:決", "type": "kanji", "item": "決", "jlpt_level": "N3",
            "state": "REVIEW", "introduced_date": "2026-09-01", "next_review": "2026-09-16",
            "cadence_index": 2, "open_errors": [], "evidence": [], "first_pass_target": False,
        }
        reports = {"items": [{"source_id": "kanji:決", "result": "fail", "note": "mixed up with 快"}]}
        bank.ingest_evidence(state, CATALOG, reports, NOW)
        rec = state["items"]["kanji:決"]
        self.assertEqual(rec["state"], "REPAIR")
        self.assertEqual(rec["cadence_index"], 0)
        self.assertIn("mixed up with 快", rec["open_errors"])

    def test_repeated_fail_flags_intervention(self):
        state = fresh_state()
        state["items"]["kanji:決"] = {
            "source_id": "kanji:決", "type": "kanji", "item": "決", "jlpt_level": "N3",
            "state": "REPAIR", "introduced_date": "2026-09-01", "next_review": "2026-09-16",
            "cadence_index": 0, "open_errors": [], "evidence": [{"date": "x", "result": "fail", "note": ""}],
            "first_pass_target": False,
        }
        reports = {"items": [{"source_id": "kanji:決", "result": "fail", "note": "again"}]}
        bank.ingest_evidence(state, CATALOG, reports, NOW)
        self.assertIn("NEEDS_INTERVENTION", state["items"]["kanji:決"]["open_errors"])

    def test_pass_advances_cadence_and_clears_pending(self):
        state = fresh_state()
        state["pending"] = ["kanji:決"]
        state["items"]["kanji:決"] = {
            "source_id": "kanji:決", "type": "kanji", "item": "決", "jlpt_level": "N3",
            "state": "LEARNING", "introduced_date": "2026-09-15", "next_review": "2026-09-16",
            "cadence_index": 0, "open_errors": [], "evidence": [], "first_pass_target": True,
        }
        reports = {"items": [{"source_id": "kanji:決", "result": "pass", "note": ""}]}
        bank.ingest_evidence(state, CATALOG, reports, NOW)
        rec = state["items"]["kanji:決"]
        self.assertEqual(rec["state"], "REVIEW")
        self.assertEqual(rec["cadence_index"], 1)
        self.assertNotIn("kanji:決", state["pending"])

    def test_session_aggregate_never_touches_named_items(self):
        state = fresh_state()
        reports = {"items": [], "session": {"new_cards_done": 8}}
        bank.ingest_evidence(state, CATALOG, reports, NOW)
        self.assertEqual(state["items"], {})
        self.assertEqual(len(state["sessions"]), 1)
        self.assertEqual(state["sessions"][0]["new_cards_done"], 8)

    def test_evidence_for_unknown_item_creates_record_from_catalog(self):
        state = fresh_state()
        reports = {"items": [{"source_id": "kanji:決", "result": "pass", "note": ""}]}
        bank.ingest_evidence(state, CATALOG, reports, NOW)
        self.assertIn("kanji:決", state["items"])
        self.assertEqual(state["items"]["kanji:決"]["item"], "決")

    def test_pass_at_top_cadence_graduates_to_done(self):
        state = fresh_state()
        state["items"]["kanji:決"] = {
            "source_id": "kanji:決", "type": "kanji", "item": "決", "jlpt_level": "N3",
            "state": "REVIEW", "introduced_date": "2026-01-01", "next_review": "2026-09-16",
            "cadence_index": len(bank.CADENCE_DAYS) - 1, "open_errors": [], "evidence": [],
            "first_pass_target": False,
        }
        reports = {"items": [{"source_id": "kanji:決", "result": "pass", "note": ""}]}
        bank.ingest_evidence(state, CATALOG, reports, NOW)
        rec = state["items"]["kanji:決"]
        self.assertEqual(rec["state"], "DONE")
        self.assertNotIn("next_review", rec)

    def test_partial_at_top_cadence_does_not_graduate(self):
        state = fresh_state()
        state["items"]["kanji:決"] = {
            "source_id": "kanji:決", "type": "kanji", "item": "決", "jlpt_level": "N3",
            "state": "REVIEW", "introduced_date": "2026-01-01", "next_review": "2026-09-16",
            "cadence_index": len(bank.CADENCE_DAYS) - 1, "open_errors": [], "evidence": [],
            "first_pass_target": False,
        }
        reports = {"items": [{"source_id": "kanji:決", "result": "partial", "note": ""}]}
        bank.ingest_evidence(state, CATALOG, reports, NOW)
        self.assertEqual(state["items"]["kanji:決"]["state"], "REVIEW")

    def test_done_items_are_never_replanned_as_review(self):
        state = fresh_state()
        state["items"]["kanji:決"] = {
            "source_id": "kanji:決", "type": "kanji", "item": "決", "jlpt_level": "N3",
            "state": "DONE", "introduced_date": "2026-01-01", "open_errors": [], "evidence": [],
            "first_pass_target": False,
        }
        result = bank.plan(state, CATALOG, NOW)
        self.assertNotIn("kanji:決", {b["source_id"] for b in result["blocks"]})


class PlanVolumeTest(unittest.TestCase):
    def test_a_fresh_day_introduces_close_to_the_full_new_ceilings(self):
        # A completely empty state (nothing due, nothing in repair) should
        # spend most of CAPACITY_MINUTES on ADVANCE, reaching close to the
        # full "10 vocab + 3 grammar + 3 kanji" normal-day target instead
        # of stopping after some fixed small number of items regardless
        # of type (the old MAX_BLOCKS=3-total behavior).
        catalog = {}
        for i in range(bank.NEW_CEILINGS["vocab"]):
            catalog[f"vocab:v{i}"] = {"type": "vocab", "item": f"V{i}", "jlpt_level": "N3", "row": {}}
        for i in range(bank.NEW_CEILINGS["grammar"]):
            catalog[f"grammar:g{i}"] = {"type": "grammar", "item": f"G{i}", "jlpt_level": "N3", "row": {}}
        for i in range(bank.NEW_CEILINGS["kanji"]):
            catalog[f"kanji:k{i}"] = {"type": "kanji", "item": f"K{i}", "jlpt_level": "N3", "row": {}}

        state = fresh_state()
        result = bank.plan(state, catalog, NOW)
        self.assertGreater(len(result["blocks"]), 3)
        self.assertEqual(len(result["new_items"]["vocab"]), bank.NEW_CEILINGS["vocab"])


if __name__ == "__main__":
    unittest.main()
