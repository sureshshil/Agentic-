"""Regression tests for the JLPT instructor cron entrypoint
(scheduled/jlpt_instructor.py): morning/evening branch selection by JST
time, the once-per-Japan-date email ledger gate, rank-based evidence
parsing (V011/K004/G002) out of Notion content, and that a run never
claims success on an unverified Notion save or a failed email send.

No live Notion/Gmail/Vertex calls: jlpt_item_bank, jlpt_notion, Agent and
send_email are all mocked/patched.

Run: python3 -m unittest tests.test_jlpt_instructor -v   (from simple_agent/)
"""

import contextlib
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scheduled"))
import jlpt_instructor as inst  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")


class FormatFieldTest(unittest.TestCase):
    def test_uses_plain_language_not_raw_engine_kind(self):
        blocks = [{"source_id": "V001", "item": "場合", "kind": "ADVANCE", "type": "vocab"}]
        text = inst._format_field(blocks)
        self.assertIn("(new)", text)
        self.assertNotIn("ADVANCE", text)

    def test_covers_every_kind_the_planner_can_emit(self):
        for kind in ("CARRY_FORWARD", "REPAIR", "REVIEW", "ADVANCE"):
            blocks = [{"source_id": "V001", "item": "場合", "kind": kind, "type": "vocab"}]
            text = inst._format_field(blocks)
            self.assertEqual(text, f"V001 場合 ({inst._KIND_LABELS[kind]})")

    def test_no_blocks_message(self):
        self.assertEqual(inst._format_field([]), "No items scheduled.")


class BranchSelectionTest(unittest.TestCase):
    def test_before_noon_is_morning(self):
        self.assertEqual(inst.determine_branch(datetime(2026, 9, 17, 7, 0, tzinfo=JST)), "morning")

    def test_after_noon_is_evening(self):
        self.assertEqual(inst.determine_branch(datetime(2026, 9, 17, 23, 0, tzinfo=JST)), "evening")

    def test_noon_itself_is_evening(self):
        self.assertEqual(inst.determine_branch(datetime(2026, 9, 17, 12, 0, tzinfo=JST)), "evening")


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(
            os.environ.get("CLAUDE_JOB_DIR", "/tmp"), "jlpt_email_ledger_test.json"
        )
        if os.path.exists(self.path):
            os.remove(self.path)

    tearDown = setUp

    def test_first_reservation_succeeds(self):
        ledger = {}
        self.assertTrue(inst.reserve_email(ledger, "2026-09-17"))
        self.assertEqual(ledger["2026-09-17"]["status"], "PENDING")

    def test_pending_forbids_a_second_reservation(self):
        ledger = {"2026-09-17": {"status": "PENDING"}}
        self.assertFalse(inst.reserve_email(ledger, "2026-09-17"))

    def test_sent_forbids_a_second_reservation(self):
        ledger = {"2026-09-17": {"status": "SENT"}}
        self.assertFalse(inst.reserve_email(ledger, "2026-09-17"))

    def test_a_new_date_is_independent(self):
        ledger = {"2026-09-16": {"status": "SENT"}}
        self.assertTrue(inst.reserve_email(ledger, "2026-09-17"))

    def test_save_and_load_round_trip(self):
        ledger = {}
        inst.reserve_email(ledger, "2026-09-17")
        inst.record_sent(ledger, "2026-09-17", "Email sent to x.", "https://notion.so/x")
        inst.save_ledger(self.path, ledger)
        reloaded = inst.load_ledger(self.path)
        self.assertEqual(reloaded["2026-09-17"]["status"], "SENT")
        self.assertEqual(reloaded["2026-09-17"]["notion_url"], "https://notion.so/x")


class ParseReadingAttemptedTest(unittest.TestCase):
    def test_matches_once_the_blank_is_filled_in(self):
        ids = inst.parse_reading_attempted(["Reading R-N3-002 attempted: yes (change to yes once you've done it)", "unrelated"])
        self.assertEqual(ids, {"R-N3-002"})

    def test_unfilled_placeholder_does_not_match(self):
        # The template's own unfilled blank ("___") must never read as attempted.
        ids = inst.parse_reading_attempted(["Reading R-N3-002 attempted: ___ (change to yes once you've done it, else leave as-is)"])
        self.assertEqual(ids, set())

    def test_no_match_returns_empty_set(self):
        self.assertEqual(inst.parse_reading_attempted(["nothing about reading here"]), set())


class SplitReadingContentTest(unittest.TestCase):
    def test_answer_lines_are_stripped_from_visible_text(self):
        raw = "PASSAGE: 昨日映画を見た。\nQ1: what?\nA) x\nB) y\nANSWER1: B\nQ2: what2?\nANSWER2: A"
        visible, answers = inst._split_reading_content(raw)
        self.assertNotIn("ANSWER1", visible)
        self.assertNotIn("ANSWER2", visible)
        self.assertIn("ANSWER1: B", answers)
        self.assertIn("ANSWER2: A", answers)

    def test_passage_label_is_stripped(self):
        visible, _ = inst._split_reading_content("PASSAGE: 昨日映画を見た。\nQ1: what?")
        self.assertNotIn("PASSAGE:", visible)
        self.assertTrue(visible.startswith("昨日映画を見た。"))


class ReadingContentBlocksTest(unittest.TestCase):
    def test_passage_and_each_question_become_separate_blocks(self):
        visible = "昨日映画を見た。\nQ1: what?\nA) x\nB) y\nQ2: what2?\nA) z\nB) w"
        blocks = inst._reading_content_blocks(visible)
        self.assertEqual(len(blocks), 3)
        self.assertTrue(all(kind == "paragraph" for kind, _ in blocks))
        self.assertEqual(blocks[0][1], "昨日映画を見た。")
        self.assertTrue(blocks[1][1].startswith("Q1:"))
        self.assertTrue(blocks[2][1].startswith("Q2:"))

    def test_no_question_lines_yields_a_single_block(self):
        blocks = inst._reading_content_blocks("just a passage, no questions.")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0], ("paragraph", "just a passage, no questions."))


class PassageOnlyTest(unittest.TestCase):
    def test_strips_trailing_questions(self):
        visible = "昨日映画を見た。とても面白かった。\nQ1: what?\nA) x\nB) y"
        self.assertEqual(inst._passage_only(visible), "昨日映画を見た。とても面白かった。")

    def test_no_questions_returns_the_whole_text(self):
        self.assertEqual(inst._passage_only("just a passage."), "just a passage.")

    def test_strips_furigana_annotations_for_narration(self):
        visible = "図書館（としょかん）から、お知らせ（おしらせ）です。\nQ1: what?"
        self.assertEqual(inst._passage_only(visible), "図書館から、お知らせです。")


class StripFuriganaTest(unittest.TestCase):
    def test_removes_fullwidth_paren_annotations(self):
        self.assertEqual(inst._strip_furigana("図書館（としょかん）から"), "図書館から")

    def test_removes_multiple_annotations(self):
        self.assertEqual(
            inst._strip_furigana("変わります（かわります）ようになる（ようになる）"),
            "変わりますようになる",
        )

    def test_text_without_furigana_is_unchanged(self):
        self.assertEqual(inst._strip_furigana("plain text"), "plain text")


class GenerateReadingAudioMp3Test(unittest.TestCase):
    def test_returns_the_saved_files_bytes(self):
        async def fake_save(self_communicate, path, *_args, **_kwargs):
            with open(path, "wb") as f:
                f.write(b"FAKE MP3 BYTES")

        with patch.object(inst.edge_tts.Communicate, "save", new=fake_save):
            mp3_bytes = inst._generate_reading_audio_mp3("こんにちは")
        self.assertEqual(mp3_bytes, b"FAKE MP3 BYTES")

    def test_propagates_a_generation_failure_after_retries(self):
        with patch.object(inst.edge_tts.Communicate, "save", new=AsyncMock(side_effect=RuntimeError("network error"))):
            with patch.object(inst.asyncio, "sleep", new=AsyncMock()):
                with self.assertRaises(RuntimeError):
                    inst._generate_reading_audio_mp3("text")


class ParseLabeledBlocksTest(unittest.TestCase):
    def test_examples_become_numbered_others_become_paragraphs(self):
        raw = (
            "EXAMPLE: 一つ目の文。\n"
            "EXAMPLE: 二つ目の文。\n"
            "QUESTION: どちらが正しいですか。\n"
            "EXPLANATION: ニュアンスの違いです。"
        )
        blocks = inst._parse_labeled_blocks(raw, "grammar practice")
        self.assertEqual(
            blocks,
            [
                ("numbered", "一つ目の文。"),
                ("numbered", "二つ目の文。"),
                ("paragraph", "どちらが正しいですか。"),
                ("paragraph", "ニュアンスの違いです。"),
            ],
        )

    def test_unlabeled_output_falls_back_to_one_raw_paragraph(self):
        blocks = inst._parse_labeled_blocks("the model just wrote free text instead", "grammar practice")
        self.assertEqual(blocks, [("paragraph", "the model just wrote free text instead")])

    def test_empty_output_falls_back_to_a_placeholder(self):
        blocks = inst._parse_labeled_blocks("", "grammar practice")
        self.assertEqual(blocks, [("paragraph", "(no grammar practice generated)")])


class ComposeGrammarPracticeBlocksTest(unittest.TestCase):
    def test_no_grammar_scheduled_skips_the_agent_call(self):
        with patch.object(inst, "Agent") as mock_agent_cls:
            blocks = inst._compose_grammar_practice_blocks([])
        mock_agent_cls.assert_not_called()
        self.assertEqual(blocks, [("paragraph", "No grammar scheduled today.")])

    def test_parses_the_agents_labeled_response(self):
        with patch.object(inst, "Agent") as mock_agent_cls:
            mock_agent_cls.return_value.send.return_value = "EXAMPLE: 文。\nQUESTION: 質問？\nEXPLANATION: 説明。"
            blocks = inst._compose_grammar_practice_blocks([{"source_id": "G001", "item": "ことにする"}])
        self.assertEqual(
            blocks,
            [("numbered", "文。"), ("paragraph", "質問？"), ("paragraph", "説明。")],
        )


class ExtractIdsTest(unittest.TestCase):
    def test_single_ids_are_found(self):
        self.assertEqual(inst._extract_ids("G001 ようになる contrast with G013 ようにする"), {"G001", "G013"})

    def test_a_written_range_is_expanded(self):
        self.assertEqual(
            inst._extract_ids("10 new cards: V001–V010 場合、結果"),
            {f"V{n:03d}" for n in range(1, 11)},
        )

    def test_hyphen_range_also_works(self):
        self.assertEqual(inst._extract_ids("K001-K003"), {"K001", "K002", "K003"})

    def test_empty_text_yields_no_ids(self):
        self.assertEqual(inst._extract_ids(""), set())


class MarkPublishedTest(unittest.TestCase):
    def setUp(self):
        self.catalog = {
            "V001": {"type": "vocab", "item": "場合", "jlpt_level": "N3", "row": {}},
            "K001": {"type": "kanji", "item": "決", "jlpt_level": "N3", "row": {}},
        }

    def test_registers_ids_mentioned_in_the_row_with_no_evidence(self):
        state = {"items": {}}
        row = {"Vocabulary": "10 new cards: V001", "Grammar": "", "Kanji": "3 new cards: K001"}
        added = inst._mark_published(state, self.catalog, row, "2026-09-14")
        self.assertEqual(added, 2)
        self.assertEqual(state["items"]["V001"]["evidence"], [])
        self.assertEqual(state["items"]["V001"]["introduced_date"], "2026-09-14")
        self.assertEqual(state["items"]["V001"]["state"], "REVIEW")

    def test_already_known_items_are_left_untouched(self):
        state = {"items": {"V001": {"source_id": "V001", "state": "REPAIR", "evidence": ["already has evidence"]}}}
        row = {"Vocabulary": "V001", "Grammar": "", "Kanji": ""}
        added = inst._mark_published(state, self.catalog, row, "2026-09-14")
        self.assertEqual(added, 0)
        self.assertEqual(state["items"]["V001"]["state"], "REPAIR")  # unchanged

    def test_ids_not_in_the_catalog_are_skipped(self):
        state = {"items": {}}
        row = {"Vocabulary": "V999", "Grammar": "", "Kanji": ""}
        added = inst._mark_published(state, self.catalog, row, "2026-09-14")
        self.assertEqual(added, 0)
        self.assertNotIn("V999", state["items"])


class GetOrStartReadingTest(unittest.TestCase):
    def test_generates_a_new_item_when_bank_is_empty(self):
        state = {}
        plan_result = {"blocks": [{"item": "決"}]}
        with patch.object(inst, "Agent") as mock_agent_cls:
            mock_agent_cls.return_value.send.return_value = "PASSAGE: text\nQ1: q\nANSWER1: A"
            item = inst._get_or_start_reading(state, plan_result)
        self.assertEqual(item["id"], "R-N3-001")
        self.assertFalse(item["attempted"])
        self.assertEqual(len(state["reading_bank"]), 1)

    def test_reuses_an_unattempted_item_without_calling_the_agent(self):
        state = {"reading_bank": [{"id": "R-N3-001", "visible": "old passage", "answers": "", "attempted": False}]}
        with patch.object(inst, "Agent") as mock_agent_cls:
            item = inst._get_or_start_reading(state, {"blocks": []})
        mock_agent_cls.assert_not_called()
        self.assertEqual(item["id"], "R-N3-001")

    def test_generates_a_new_item_once_the_prior_one_is_attempted(self):
        state = {"reading_bank": [{"id": "R-N3-001", "visible": "old", "answers": "", "attempted": True}]}
        with patch.object(inst, "Agent") as mock_agent_cls:
            mock_agent_cls.return_value.send.return_value = "PASSAGE: new text"
            item = inst._get_or_start_reading(state, {"blocks": []})
        self.assertEqual(item["id"], "R-N3-002")
        self.assertEqual(len(state["reading_bank"]), 2)

    def test_genre_cycles_by_reading_bank_index(self):
        num_genres = len(inst._READING_GENRES)
        existing_count = num_genres + 1  # one full cycle plus one, so the wrap is exercised
        state = {"reading_bank": [
            {"id": f"R-N3-{i:03d}", "visible": "x", "answers": "", "attempted": True}
            for i in range(existing_count)
        ]}
        with patch.object(inst, "Agent") as mock_agent_cls:
            mock_agent_cls.return_value.send.return_value = "PASSAGE: text"
            inst._get_or_start_reading(state, {"blocks": []})
        prompt = mock_agent_cls.return_value.send.call_args.args[0]
        expected_genre = inst._READING_GENRES[existing_count % num_genres]
        self.assertIn(expected_genre, prompt)


class DayPropagationTest(unittest.TestCase):
    """Day N only checks day N-1 (not full history) - jlpt_notion.
    find_previous_dated_row + the ingested_dates idempotency guard in
    run_morning."""

    def setUp(self):
        self.env = {
            "NOTION_API_KEY": "secret_test",
            "JLPT_NOTION_COLLECTION_ID": "coll-id",
            "GCP_PROJECT_ID": "proj",
            "EMAIL_ADDRESS": "me@example.com",
            "EMAIL_APP_PASSWORD": "app-pass",
        }
        self.now = datetime(2026, 9, 17, 7, 0, tzinfo=JST)
        self.catalog = {"G001": {"type": "grammar", "item": "ようになる", "jlpt_level": "N3", "row": {}}}
        self.state = {
            "items": {
                "G001": {
                    "source_id": "G001", "type": "grammar", "item": "ようになる", "jlpt_level": "N3",
                    "state": "LEARNING", "introduced_date": "2026-09-16", "next_review": "2026-09-16",
                    "cadence_index": 0, "open_errors": [], "evidence": [], "first_pass_target": True,
                },
            },
            "pending": ["G001"],
            "ingested_dates": [],
        }
        self.prev_row = {"page_id": "prev", "url": "u", "title": "2026-09-16 — Day 03", "date": "2026-09-16",
                          "status": "Done", "Vocabulary": "", "Grammar": "", "Kanji": "", "Review Task": "",
                          "Instructor Notes": "G001 fail - mixed up with ようにする"}
        self.today_row = {"page_id": "today", "url": "u2", "title": "2026-09-17 — Day 04", "date": "2026-09-17",
                           "status": "In progress", "Vocabulary": "", "Grammar": "", "Kanji": "", "Review Task": "",
                           "Instructor Notes": ""}

    def _run(self):
        with patch.dict(os.environ, self.env), \
             patch.object(inst.bank, "load_catalog", return_value=self.catalog), \
             patch.object(inst.bank, "load_state", return_value=self.state), \
             patch.object(inst.bank, "save_state"), \
             patch.object(inst.notion, "find_previous_dated_row", return_value=self.prev_row), \
             patch.object(inst.notion, "list_dated_rows_before", return_value=[]), \
             patch.object(inst.notion, "read_body_text", return_value=[]), \
             patch.object(inst.notion, "read_comments", return_value=[]), \
             patch.object(inst.notion, "find_row_for_date", return_value=self.today_row), \
             patch.object(inst.notion, "update_row"), \
             patch.object(inst, "_get_or_start_reading", return_value={"id": "R-N3-001", "visible": "p", "answers": "", "attempted": False}), \
             patch.object(inst, "_compose_grammar_practice_blocks", return_value=[("paragraph", "practice")]), \
             patch.object(inst, "_generate_reading_audio_mp3", return_value=b"MP3DATA"), \
             patch.object(inst.notion, "upload_audio"), \
             patch.object(inst.notion, "append_body_blocks"), \
             patch.object(inst.notion, "verify_row_saved", return_value=True), \
             patch.object(inst, "send_email", return_value="Email sent."):
            inst.run_morning(self.now)

    def test_previous_day_evidence_is_folded_in_before_planning(self):
        self._run()
        self.assertEqual(self.state["items"]["G001"]["state"], "REPAIR")
        self.assertIn("2026-09-16", self.state["ingested_dates"])

    def test_previous_day_is_not_reingested_once_marked(self):
        self.state["ingested_dates"] = ["2026-09-16"]
        self._run()
        # Already-ingested day N-1 is skipped - G001 is untouched by it,
        # and today's (empty) row contributes no evidence either.
        self.assertEqual(self.state["items"]["G001"]["state"], "LEARNING")
        self.assertEqual(self.state["ingested_dates"].count("2026-09-16"), 1)


class ParseEvidenceTest(unittest.TestCase):
    def test_tagged_line_is_parsed(self):
        reports = inst.parse_evidence(["K004 pass", "some unrelated comment"])
        self.assertEqual(reports["items"], [{"source_id": "K004", "result": "pass", "note": ""}])

    def test_note_after_result_is_captured(self):
        reports = inst.parse_evidence(["K004: fail - mixed up with 快"])
        self.assertEqual(reports["items"][0]["result"], "fail")
        self.assertEqual(reports["items"][0]["note"], "mixed up with 快")

    def test_multiple_ids_on_one_line(self):
        reports = inst.parse_evidence(["G002 pass, G014 fail - swapped meaning"])
        ids = {r["source_id"]: r["result"] for r in reports["items"]}
        self.assertEqual(ids, {"G002": "pass", "G014": "fail"})

    def test_untagged_lines_produce_no_items(self):
        reports = inst.parse_evidence(["Great session today!", "Finished 8 new cards."])
        self.assertEqual(reports["items"], [])

    def test_the_generated_learner_report_never_self_parses_as_evidence(self):
        # Regression: an earlier version of _format_learner_report wrote a
        # literal "e.g. \"G001 pass\"" example, which this exact parser
        # then matched as if the learner had reported it the moment the
        # row got read back - fabricating evidence out of the instructions
        # meant for a human. The whole checklist, as actually generated,
        # must never itself look like a report.
        plan_result = {
            "date": "2026-09-17", "estimated_minutes": 38,
            "blocks": [
                {"kind": "CARRY_FORWARD", "source_id": "G001", "item": "〜ようになる", "type": "grammar"},
                {"kind": "ADVANCE", "source_id": "V011", "item": "決まる", "type": "vocab"},
                {"kind": "ADVANCE", "source_id": "K004", "item": "増", "type": "kanji"},
            ],
        }
        reading_item = {"id": "R-N3-001", "visible": "text", "answers": "", "attempted": False}
        lines = inst._format_learner_report(plan_result, reading_item)
        self.assertEqual(inst.parse_evidence(lines)["items"], [])
        # Same failure mode, same fix, for the reading-attempted marker.
        self.assertEqual(inst.parse_reading_attempted(lines), set())


class RunMorningTest(unittest.TestCase):
    def setUp(self):
        self.env = {
            "NOTION_API_KEY": "secret_test",
            "JLPT_NOTION_COLLECTION_ID": "coll-id",
            "GCP_PROJECT_ID": "proj",
            "EMAIL_ADDRESS": "me@example.com",
            "EMAIL_APP_PASSWORD": "app-pass",
        }
        self.now = datetime(2026, 9, 17, 7, 0, tzinfo=JST)
        self.new_row = {"page_id": "p1", "url": "https://notion.so/p1", "title": "2026-09-17 — Day 04",
                         "status": "In progress", "date": "2026-09-17", "Vocabulary": "", "Grammar": "", "Kanji": "",
                         "Review Task": "", "Instructor Notes": ""}

    def test_creates_a_new_dated_row_never_reusing_the_acquisition_track(self):
        plan_result = {"date": "2026-09-17", "blocks": [], "estimated_minutes": 10, "new_items": {}, "repair_targets": []}
        with patch.dict(os.environ, self.env), \
             patch.object(inst.notion, "find_row_for_date", return_value=None), \
             patch.object(inst.notion, "find_previous_dated_row", return_value=None), \
             patch.object(inst.notion, "list_dated_rows_before", return_value=[]), \
             patch.object(inst.notion, "next_dated_day_number", return_value=4) as mock_next_num, \
             patch.object(inst.notion, "create_daily_row", return_value=self.new_row) as mock_create, \
             patch.object(inst.bank, "load_catalog", return_value={}), \
             patch.object(inst.bank, "load_state", return_value={"items": {}, "pending": []}), \
             patch.object(inst.bank, "validate", return_value=[]), \
             patch.object(inst.bank, "plan", return_value=plan_result), \
             patch.object(inst.bank, "save_state"), \
             patch.object(inst.notion, "update_row"), \
             patch.object(inst, "_get_or_start_reading", return_value={"id": "R-N3-001", "visible": "passage", "answers": "", "attempted": False}), \
             patch.object(inst, "_compose_grammar_practice_blocks", return_value=[("paragraph", "practice")]), \
             patch.object(inst, "_generate_reading_audio_mp3", return_value=b"MP3DATA"), \
             patch.object(inst.notion, "upload_audio"), \
             patch.object(inst.notion, "append_body_blocks"), \
             patch.object(inst.notion, "verify_row_saved", return_value=True), \
             patch.object(inst, "send_email", return_value="Email sent to me@example.com."):
            inst.run_morning(self.now)
        mock_next_num.assert_called_once_with("coll-id")
        mock_create.assert_called_once_with("coll-id", "2026-09-17", 4)

    def test_aborts_before_sending_if_item_bank_invalid(self):
        with patch.dict(os.environ, self.env), \
             patch.object(inst.notion, "find_row_for_date", return_value=None), \
             patch.object(inst.notion, "find_previous_dated_row", return_value=None), \
             patch.object(inst.notion, "list_dated_rows_before", return_value=[]), \
             patch.object(inst.notion, "next_dated_day_number", return_value=4), \
             patch.object(inst.notion, "create_daily_row", return_value=self.new_row), \
             patch.object(inst.bank, "load_catalog", return_value={}), \
             patch.object(inst.bank, "load_state", return_value={"items": {}}), \
             patch.object(inst.bank, "validate", return_value=["broken"]), \
             patch.object(inst, "send_email") as mock_send:
            with self.assertRaises(inst.InstructorError):
                inst.run_morning(self.now)
        mock_send.assert_not_called()

    def test_never_sends_email_when_notion_verify_fails(self):
        plan_result = {"date": "2026-09-17", "blocks": [], "estimated_minutes": 10, "new_items": {}, "repair_targets": []}
        with patch.dict(os.environ, self.env), \
             patch.object(inst.notion, "find_row_for_date", return_value=None), \
             patch.object(inst.notion, "find_previous_dated_row", return_value=None), \
             patch.object(inst.notion, "list_dated_rows_before", return_value=[]), \
             patch.object(inst.notion, "next_dated_day_number", return_value=4), \
             patch.object(inst.notion, "create_daily_row", return_value=self.new_row), \
             patch.object(inst.bank, "load_catalog", return_value={}), \
             patch.object(inst.bank, "load_state", return_value={"items": {}, "pending": []}), \
             patch.object(inst.bank, "validate", return_value=[]), \
             patch.object(inst.bank, "plan", return_value=plan_result), \
             patch.object(inst.bank, "save_state"), \
             patch.object(inst.notion, "update_row"), \
             patch.object(inst, "_get_or_start_reading", return_value={"id": "R-N3-001", "visible": "passage", "answers": "", "attempted": False}), \
             patch.object(inst, "_compose_grammar_practice_blocks", return_value=[("paragraph", "practice")]), \
             patch.object(inst, "_generate_reading_audio_mp3", return_value=b"MP3DATA"), \
             patch.object(inst.notion, "upload_audio"), \
             patch.object(inst.notion, "append_body_blocks"), \
             patch.object(inst.notion, "verify_row_saved", return_value=False), \
             patch.object(inst, "send_email") as mock_send:
            with self.assertRaises(inst.InstructorError):
                inst.run_morning(self.now)
        mock_send.assert_not_called()

    def test_successful_run_sends_exactly_one_email_and_records_ledger(self):
        plan_result = {
            "date": "2026-09-17",
            "blocks": [{"kind": "ADVANCE", "source_id": "K004", "item": "増", "type": "kanji"}],
            "estimated_minutes": 10, "new_items": {"kanji": ["K004"]}, "repair_targets": [],
        }
        ledger_path = os.path.join(os.environ.get("CLAUDE_JOB_DIR", "/tmp"), "jlpt_ledger_run_test.json")
        if os.path.exists(ledger_path):
            os.remove(ledger_path)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, self.env))
            stack.enter_context(patch.object(inst, "EMAIL_LEDGER_PATH", ledger_path))
            stack.enter_context(patch.object(inst, "EMAIL_DELIVERY_LOG", ledger_path + ".md"))
            stack.enter_context(patch.object(inst.notion, "find_row_for_date", side_effect=[None, self.new_row]))
            stack.enter_context(patch.object(inst.notion, "find_previous_dated_row", return_value=None))
            stack.enter_context(patch.object(inst.notion, "list_dated_rows_before", return_value=[]))
            stack.enter_context(patch.object(inst.notion, "next_dated_day_number", return_value=4))
            stack.enter_context(patch.object(inst.notion, "create_daily_row", return_value=self.new_row))
            stack.enter_context(patch.object(inst.bank, "load_catalog", return_value={}))
            stack.enter_context(patch.object(inst.bank, "load_state", return_value={"items": {}, "pending": []}))
            stack.enter_context(patch.object(inst.bank, "validate", return_value=[]))
            stack.enter_context(patch.object(inst.bank, "plan", return_value=plan_result))
            stack.enter_context(patch.object(inst.bank, "save_state"))
            stack.enter_context(patch.object(inst.notion, "update_row"))
            stack.enter_context(patch.object(
                inst, "_get_or_start_reading",
                return_value={"id": "R-N3-001", "visible": "passage", "answers": "", "attempted": False},
            ))
            stack.enter_context(patch.object(inst, "_compose_grammar_practice_blocks", return_value=[("paragraph", "practice")]))
            stack.enter_context(patch.object(inst, "_generate_reading_audio_mp3", return_value=b"MP3DATA"))
            stack.enter_context(patch.object(inst.notion, "upload_audio"))
            stack.enter_context(patch.object(inst.notion, "append_body_blocks"))
            stack.enter_context(patch.object(inst.notion, "read_comments", return_value=[]))
            stack.enter_context(patch.object(inst.notion, "read_body_text", return_value=[]))
            stack.enter_context(patch.object(inst.notion, "verify_row_saved", return_value=True))
            mock_send = stack.enter_context(patch.object(inst, "send_email", return_value="Email sent to me@example.com."))

            inst.run_morning(self.now)
            self.assertEqual(mock_send.call_count, 1)
            # A second morning run the same Japan-date must not send again.
            inst.run_morning(self.now)
            self.assertEqual(mock_send.call_count, 1)


if __name__ == "__main__":
    unittest.main()
