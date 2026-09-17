"""Regression tests for the JLPT instructor's Notion access
(scheduled/jlpt_notion.py) against the real "JLPT Daily Plan" database
shape: finding today's row (already dated, or the next unscheduled row
in the pre-planned curriculum), reading/updating its rich-text
properties without touching Instructor Notes via update_row, the
read-modify-write append for Instructor Notes, and verify_row_saved's
failure modes.

No live API calls: requests is mocked.

Run: python3 -m unittest tests.test_jlpt_notion -v   (from simple_agent/)
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scheduled"))
import jlpt_notion as jn  # noqa: E402

ENV = {"NOTION_API_KEY": "secret_test"}


def _resp(status=200, body=None):
    m = MagicMock()
    m.status_code = status
    m.text = "error" if status >= 400 else "ok"
    m.json.return_value = body or {}
    return m


def _page(day_title, date=None, status="Not started", vocabulary="", instructor_notes="", page_id="p1"):
    return {
        "id": page_id,
        "url": f"https://notion.so/{page_id}",
        "properties": {
            "Day": {"type": "title", "title": [{"plain_text": day_title}]},
            "Date": {"type": "date", "date": {"start": date} if date else None},
            "Status": {"type": "status", "status": {"name": status} if status else None},
            "Vocabulary": {"type": "rich_text", "rich_text": [{"plain_text": vocabulary}] if vocabulary else []},
            "Grammar": {"type": "rich_text", "rich_text": []},
            "Kanji": {"type": "rich_text", "rich_text": []},
            "Review Task": {"type": "rich_text", "rich_text": []},
            "Instructor Notes": {"type": "rich_text", "rich_text": [{"plain_text": instructor_notes}] if instructor_notes else []},
        },
    }


class ReadRowTest(unittest.TestCase):
    def test_extracts_all_fields(self):
        page = _page("2026-09-16 — Day 03", date="2026-09-16", status="Done", vocabulary="V021-V025")
        row = jn.read_row(page)
        self.assertEqual(row["title"], "2026-09-16 — Day 03")
        self.assertEqual(row["date"], "2026-09-16")
        self.assertEqual(row["status"], "Done")
        self.assertEqual(row["Vocabulary"], "V021-V025")


class FindRowForDateTest(unittest.TestCase):
    def test_exact_date_match(self):
        pages = [_page("Day 04 — Acquisition 04"), _page("2026-09-17 — Day 04", date="2026-09-17", page_id="p2")]
        with patch.dict(os.environ, ENV), patch.object(jn, "requests") as rq:
            rq.post.return_value = _resp(200, {"results": pages, "has_more": False})
            row = jn.find_row_for_date("db-id", "2026-09-17")
        self.assertEqual(row["page_id"], "p2")

    def test_no_match_returns_none(self):
        pages = [_page("Day 05 — Acquisition 05")]
        with patch.dict(os.environ, ENV), patch.object(jn, "requests") as rq:
            rq.post.return_value = _resp(200, {"results": pages, "has_more": False})
            row = jn.find_row_for_date("db-id", "2026-09-17")
        self.assertIsNone(row)


class FindPreviousDatedRowTest(unittest.TestCase):
    def test_returns_the_single_most_recent_prior_dated_row(self):
        pages = [
            _page("2026-09-14 — Day 01", date="2026-09-14", page_id="p1"),
            _page("2026-09-15 — Day 02", date="2026-09-15", page_id="p2"),
            _page("2026-09-16 — Day 03", date="2026-09-16", page_id="p3"),
        ]
        with patch.dict(os.environ, ENV), patch.object(jn, "requests") as rq:
            rq.post.return_value = _resp(200, {"results": pages, "has_more": False})
            row = jn.find_previous_dated_row("db-id", "2026-09-17")
        self.assertEqual(row["page_id"], "p3")

    def test_ignores_the_undated_acquisition_track(self):
        pages = [
            _page("2026-09-14 — Day 01", date="2026-09-14", page_id="p1"),
            _page("Day 42 — Weekly review 7", page_id="template"),
        ]
        with patch.dict(os.environ, ENV), patch.object(jn, "requests") as rq:
            rq.post.return_value = _resp(200, {"results": pages, "has_more": False})
            row = jn.find_previous_dated_row("db-id", "2026-09-17")
        self.assertEqual(row["page_id"], "p1")

    def test_none_when_no_prior_dated_row_exists(self):
        with patch.dict(os.environ, ENV), patch.object(jn, "requests") as rq:
            rq.post.return_value = _resp(200, {"results": [], "has_more": False})
            row = jn.find_previous_dated_row("db-id", "2026-09-17")
        self.assertIsNone(row)


class NextDatedDayNumberTest(unittest.TestCase):
    def test_continues_the_real_dated_sequence(self):
        pages = [
            _page("2026-09-14 — Day 01", date="2026-09-14"),
            _page("2026-09-15 — Day 02", date="2026-09-15"),
            _page("2026-09-16 — Day 03", date="2026-09-16"),
        ]
        with patch.dict(os.environ, ENV), patch.object(jn, "requests") as rq:
            rq.post.return_value = _resp(200, {"results": pages, "has_more": False})
            self.assertEqual(jn.next_dated_day_number("db-id"), 4)

    def test_ignores_the_undated_acquisition_template_track(self):
        # These must never be mistaken for real dated history, however
        # high their own "Day NN" numbers run.
        pages = [
            _page("2026-09-14 — Day 01", date="2026-09-14"),
            _page("Day 11 — Acquisition 10"),
            _page("Day 42 — Weekly review 7"),
        ]
        with patch.dict(os.environ, ENV), patch.object(jn, "requests") as rq:
            rq.post.return_value = _resp(200, {"results": pages, "has_more": False})
            self.assertEqual(jn.next_dated_day_number("db-id"), 2)

    def test_starts_at_one_with_no_dated_rows_yet(self):
        pages = [_page("Day 01 — Acquisition 01")]
        with patch.dict(os.environ, ENV), patch.object(jn, "requests") as rq:
            rq.post.return_value = _resp(200, {"results": pages, "has_more": False})
            self.assertEqual(jn.next_dated_day_number("db-id"), 1)


class CreateDailyRowTest(unittest.TestCase):
    def test_creates_a_new_page_with_the_dated_title_convention(self):
        created = _page("2026-09-17 — Day 04", date="2026-09-17", page_id="new-page")
        with patch.dict(os.environ, ENV), patch.object(jn, "requests") as rq:
            rq.post.return_value = _resp(200, created)
            row = jn.create_daily_row("db-id", "2026-09-17", 4)
        self.assertEqual(row["page_id"], "new-page")
        payload = rq.post.call_args.kwargs["json"]
        self.assertEqual(payload["parent"], {"database_id": "db-id"})
        title_text = payload["properties"][jn.TITLE_PROPERTY]["title"][0]["text"]["content"]
        self.assertEqual(title_text, "2026-09-17 — Day 04")
        self.assertEqual(payload["properties"]["Date"], {"date": {"start": "2026-09-17"}})

    def test_never_targets_an_acquisition_style_title(self):
        created = _page("2026-09-17 — Day 01", date="2026-09-17", page_id="new-page")
        with patch.dict(os.environ, ENV), patch.object(jn, "requests") as rq:
            rq.post.return_value = _resp(200, created)
            jn.create_daily_row("db-id", "2026-09-17", 1)
        title_text = rq.post.call_args.kwargs["json"]["properties"][jn.TITLE_PROPERTY]["title"][0]["text"]["content"]
        self.assertNotIn("Acquisition", title_text)


class UpdateRowTest(unittest.TestCase):
    def test_sends_only_the_given_properties(self):
        with patch.dict(os.environ, ENV), patch.object(jn, "requests") as rq:
            rq.patch.return_value = _resp(200, {})
            jn.update_row("page-1", date_str="2026-09-17", status="In progress", fields={"Vocabulary": "V061-V080"})
        payload = rq.patch.call_args.kwargs["json"]["properties"]
        self.assertEqual(payload["Date"], {"date": {"start": "2026-09-17"}})
        self.assertEqual(payload["Status"], {"status": {"name": "In progress"}})
        self.assertIn("Vocabulary", payload)
        self.assertNotIn("Instructor Notes", payload)

    def test_refuses_instructor_notes_via_update_row(self):
        with patch.dict(os.environ, ENV):
            with self.assertRaises(ValueError):
                jn.update_row("page-1", fields={"Instructor Notes": "sneaky overwrite"})


class AppendInstructorNotesTest(unittest.TestCase):
    def test_appends_rather_than_replaces(self):
        with patch.dict(os.environ, ENV), patch.object(jn, "requests") as rq:
            rq.patch.return_value = _resp(200, {})
            jn.append_instructor_notes("page-1", "Sept 15 evening: Done.", ["Sept 16 evening: also done."])
        new_text = rq.patch.call_args.kwargs["json"]["properties"]["Instructor Notes"]["rich_text"][0]["text"]["content"]
        self.assertIn("Sept 15 evening: Done.", new_text)
        self.assertIn("Sept 16 evening: also done.", new_text)

    def test_empty_existing_notes_just_uses_the_new_text(self):
        with patch.dict(os.environ, ENV), patch.object(jn, "requests") as rq:
            rq.patch.return_value = _resp(200, {})
            jn.append_instructor_notes("page-1", "", ["first entry"])
        new_text = rq.patch.call_args.kwargs["json"]["properties"]["Instructor Notes"]["rich_text"][0]["text"]["content"]
        self.assertEqual(new_text, "first entry")


class VerifyRowSavedTest(unittest.TestCase):
    def test_returns_false_when_expected_content_missing(self):
        page = _page("2026-09-17 — Day 04", date="2026-09-17", vocabulary="V061-V080")
        with patch.dict(os.environ, ENV), patch.object(jn, "requests") as rq:
            rq.post.return_value = _resp(200, {"results": [page], "has_more": False})
            ok = jn.verify_row_saved("db-id", "2026-09-17", ["V999"])
        self.assertFalse(ok)

    def test_returns_true_when_content_present(self):
        page = _page("2026-09-17 — Day 04", date="2026-09-17", vocabulary="V061-V080")
        with patch.dict(os.environ, ENV), patch.object(jn, "requests") as rq:
            rq.post.return_value = _resp(200, {"results": [page], "has_more": False})
            ok = jn.verify_row_saved("db-id", "2026-09-17", ["V061"])
        self.assertTrue(ok)

    def test_returns_false_on_api_error_rather_than_raising(self):
        with patch.dict(os.environ, ENV), patch.object(jn, "requests") as rq:
            rq.post.return_value = _resp(500)
            ok = jn.verify_row_saved("db-id", "2026-09-17", ["x"])
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
