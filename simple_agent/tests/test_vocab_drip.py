"""Regression tests for vocab_drip's TSV-backed word source: parsing the
Anki-style TSV files, mapping a row to the sink entry shape, and rendering
one word's spoiler-hidden block for a batch message.

Run: python3 -m unittest tests.test_vocab_drip -v   (from simple_agent/)
"""

import os
import shutil
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scheduled"))
import vocab_drip as vd  # noqa: E402

_TSV = textwrap.dedent(
    """\
    #separator:tab
    #html:false
    #columns:word\trank\tword_furigana\treading\ttype\tpos\tenglish\tnepali\tsentence1\tsentence1_en\tsentence2\tsentence2_en\tsentence3\tsentence3_en\tframe\tnote\ttags
    場合\t1\t場合[ばあい]\tばあい\tnoun\tnoun\tcase; situation\tअवस्था\t雨[あめ]の 場合[ばあい]は 中止[ちゅうし]。\tIn case of rain, cancelled.\t遅[おく]れる 場合[ばあい]は 連絡[れんらく]。\tIf late, contact us.\t多[おお]くの 場合[ばあい]。\tIn many cases.\t〜の場合（は）\tReading is ばあい.\tN3 batch1
    関係\t3\t関係[かんけい]\tかんけい\tnoun\tnoun / suru-verb\trelationship\tसम्बन्ध\t二人[ふたり]の 関係[かんけい]。\tThe relationship.\tそれは 関係[かんけい]ない。\tNothing to do with me.\t天気[てんき]と 関係[かんけい]がある。\tRelated to the weather.\tAとBの関係\t関係ない = irrelevant.\tN3 batch1
    直接\t100\t直接[ちょくせつ]\tちょくせつ\tadverb\tadverb / noun\tdirect; directly\tसिधा\t直接[ちょくせつ] 話[はな]す。\tTalk directly.\t\t\t\t\tAに直接\t\tN3 batch1
    """
)


def _write_tsv(text: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".tsv")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _write_tsv_and_track(case: unittest.TestCase) -> str:
    path = _write_tsv(_TSV)
    case.addCleanup(os.unlink, path)
    return path


class LoadRowsTest(unittest.TestCase):
    def setUp(self):
        self.path = _write_tsv(_TSV)
        self.addCleanup(os.unlink, self.path)

    def test_parses_every_data_row_keyed_by_column_header(self):
        rows = vd.load_rows(self.path)
        self.assertEqual([r["word"] for r in rows], ["場合", "関係", "直接"])
        self.assertEqual(rows[0]["reading"], "ばあい")
        self.assertEqual(rows[0]["frame"], "〜の場合（は）")
        self.assertEqual(rows[1]["nepali"], "सम्बन्ध")

    def test_comment_lines_are_skipped(self):
        self.assertEqual(len(vd.load_rows(self.path)), 3)

    def test_missing_column_header_is_fatal(self):
        bad = _write_tsv("#separator:tab\n場合\t1\n")
        self.addCleanup(os.unlink, bad)
        with self.assertRaises(SystemExit):
            vd.load_rows(bad)

    def test_duplicate_word_across_files_keeps_the_first(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        with open(os.path.join(d, "batch1.tsv"), "w", encoding="utf-8") as f:
            f.write(_TSV)
        with open(os.path.join(d, "batch2.tsv"), "w", encoding="utf-8") as f:
            f.write(_TSV.replace("case; situation", "SECOND FILE"))
        rows = vd.load_rows(os.path.join(d, "batch*.tsv"))
        words = [r["word"] for r in rows]
        self.assertEqual(words, ["場合", "関係", "直接"])
        self.assertEqual(rows[0]["english"], "case; situation")  # batch1 wins


class RowToEntryTest(unittest.TestCase):
    def setUp(self):
        self.rows = vd.load_rows(_write_tsv_and_track(self))

    def test_maps_tsv_columns_to_sink_entry_shape(self):
        entry = vd.row_to_entry(self.rows[0])
        self.assertEqual(entry["word"], "場合")
        self.assertEqual(entry["reading"], "ばあい")
        self.assertEqual(entry["meaning"], "case; situation")
        self.assertEqual(entry["type"], "noun")
        self.assertEqual(entry["particles"], "〜の場合（は）")
        self.assertEqual(entry["nepali"], "अवस्था")
        self.assertEqual(len(entry["examples"]), 3)
        self.assertEqual(
            entry["examples"][0], "雨[あめ]の場合[ばあい]は中止[ちゅうし]。 — In case of rain, cancelled."
        )

    def test_blank_trailing_sentences_are_dropped(self):
        entry = vd.row_to_entry(self.rows[2])  # 直接 has only one example
        self.assertEqual(entry["examples"], ["直接[ちょくせつ]話[はな]す。 — Talk directly."])


class FormatWordBlockTest(unittest.TestCase):
    def setUp(self):
        self.rows = vd.load_rows(_write_tsv_and_track(self))

    def test_word_is_visible_and_rest_is_wrapped_in_a_spoiler(self):
        block = vd.format_word_block(self.rows[0], "new")
        self.assertIn("<b>場合[ばあい]</b>", block)
        self.assertIn("<tg-spoiler>", block)
        self.assertIn("<b>ばあい</b> · case; situation", block)
        self.assertIn("\U0001f1f3\U0001f1f5 अवस्था", block)
        self.assertIn("<code>〜の場合[ばあい]（は）</code>", block)
        self.assertIn("1. 雨[あめ]の場合[ばあい]は中止[ちゅうし]。\n<i>In case of rain, cancelled.</i>", block)
        # the word itself must be OUTSIDE the spoiler span (it's the recall prompt)
        self.assertLess(block.index("<b>場合[ばあい]</b>"), block.index("<tg-spoiler>"))

    def test_status_marker_varies_by_status(self):
        self.assertTrue(vd.format_word_block(self.rows[0], "new").startswith("\U0001f210"))
        self.assertTrue(vd.format_word_block(self.rows[0], "due").startswith("\U0001f501"))
        self.assertTrue(vd.format_word_block(self.rows[0], "reminder").startswith("⏰"))

    def test_html_special_characters_in_content_are_escaped(self):
        row = dict(self.rows[0])
        row["english"] = "case & effect"
        block = vd.format_word_block(row, "new")
        self.assertIn("case &amp; effect", block)
        self.assertNotIn("case & effect", block)


class BuildMessageTest(unittest.TestCase):
    def test_blocks_are_joined_with_a_blank_line(self):
        message = vd.build_message(["BLOCK_A", "BLOCK_B"])
        self.assertEqual(message, "BLOCK_A\n\nBLOCK_B")


class EnrichmentTest(unittest.TestCase):
    """build_body_lines/format_word_block must show the curated content
    exactly as before when no enrichment is given, only ADDING the AI
    practice block (via llm_enrich.format_blocks) - never replacing
    anything - when llm_enrich.enrich_vocab() did produce a result. Same
    contract as kanji_drip.py/grammar_drip.py - see test_kanji_drip.py."""

    def setUp(self):
        self.rows = vd.load_rows(_write_tsv_and_track(self))

    def test_without_enrichment_matches_the_original_curated_output(self):
        lines = vd.build_body_lines(self.rows[0])
        joined = "\n".join(lines)
        self.assertIn("雨[あめ]の場合[ばあい]は中止[ちゅうし]。", joined)
        self.assertNotIn("\U0001f916", joined)  # no AI-labelled line

    def test_with_enrichment_appends_the_ai_practice_block(self):
        enrichment = {
            "examples": [{"jp": "場合[ばあい]による。", "en": "It depends on the situation."}],
            "explanation": "Uses 場合 to hedge on a specific condition.",
            "dialogue": [{"speaker": "A", "jp": "行[い]く場合[ばあい]もある。", "en": "There are cases I'd go."}],
            "practice_question": {"question": "q", "options": ["a", "b"], "answer": "a"},
        }
        lines = vd.build_body_lines(self.rows[0], enrichment)
        joined = "\n".join(lines)
        self.assertIn("雨[あめ]の場合[ばあい]は中止[ちゅうし]。", joined)  # curated examples still present
        self.assertIn("場合[ばあい]による。", joined)
        self.assertIn("It depends on the situation.", joined)
        self.assertIn("hedge on a specific condition", joined)
        self.assertIn("行[い]く場合[ばあい]もある。", joined)
        self.assertIn("\U0001f916", joined)

    def test_enrichment_without_examples_omits_the_example_blocks(self):
        enrichment = {"explanation": "just a note"}
        lines = vd.build_body_lines(self.rows[0], enrichment)
        self.assertNotIn("Example 1", "\n".join(lines))

    def test_format_word_block_passes_enrichment_through(self):
        enrichment = {"examples": [{"jp": "場合[ばあい]による。", "en": "It depends on the situation."}]}
        block = vd.format_word_block(self.rows[0], "new", enrichment)
        self.assertIn("場合[ばあい]による。", block)

    def test_format_word_block_without_enrichment_is_unchanged(self):
        block = vd.format_word_block(self.rows[0], "new")
        self.assertNotIn("\U0001f916", block)


class DespaceTest(unittest.TestCase):
    def test_drops_word_spacing_between_japanese_chars(self):
        self.assertEqual(
            vd._despace_japanese("部屋[へや]を 片付[かたづ]けて 友達[ともだち]"),
            "部屋[へや]を片付[かたづ]けて友達[ともだち]",
        )

    def test_keeps_spaces_around_ascii(self):
        self.assertEqual(vd._despace_japanese("JLPT N3 の 試験[しけん]"), "JLPT N3 の試験[しけん]")


if __name__ == "__main__":
    unittest.main()
