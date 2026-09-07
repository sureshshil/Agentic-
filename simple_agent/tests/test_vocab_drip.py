"""Regression tests for vocab_drip's TSV-backed word source: parsing the
Anki-style TSV files, picking the next batch (never-sent first, then
least-recently-sent), and formatting a word for Telegram / the sinks.

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


class SelectWordsTest(unittest.TestCase):
    def setUp(self):
        self.rows = vd.load_rows(_write_tsv_and_track(self))

    def test_never_sent_words_come_first_in_rank_order(self):
        picked = vd.select_words(self.rows, history=[], count=2)
        self.assertEqual([r["word"] for r in picked], ["場合", "関係"])

    def test_sent_words_are_skipped_until_the_pool_is_exhausted(self):
        picked = vd.select_words(self.rows, history=["場合"], count=2)
        self.assertEqual([r["word"] for r in picked], ["関係", "直接"])

    def test_once_all_sent_least_recently_sent_comes_back_first(self):
        # history order = oldest first, so 関係 is the stalest
        picked = vd.select_words(self.rows, history=["関係", "直接", "場合"], count=1)
        self.assertEqual([r["word"] for r in picked], ["関係"])

    def test_count_larger_than_pool_returns_everything(self):
        self.assertEqual(len(vd.select_words(self.rows, [], count=99)), 3)


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


class FormatWordTest(unittest.TestCase):
    def test_block_shape_matches_the_old_drip_plus_a_nepali_line(self):
        entry = {
            "word": "場合",
            "reading": "ばあい",
            "meaning": "case; situation",
            "particles": "〜の場合（は）",
            "nepali": "अवस्था",
            "examples": ["雨[あめ]の場合[ばあい]。 — In case of rain."],
        }
        block = vd.format_word(entry)
        self.assertTrue(block.startswith("場合 (ばあい) — case; situation\n"))
        self.assertIn("\U0001f1f3\U0001f1f5 अवस्था", block)
        self.assertIn("助詞: 〜の場合（は）", block)
        self.assertIn("例文:\n1. 雨[あめ]の場合[ばあい]。 — In case of rain.", block)

    def test_divider_between_words(self):
        entries = [
            {"word": "A", "reading": "a", "meaning": "m", "particles": "", "examples": []},
            {"word": "B", "reading": "b", "meaning": "n", "particles": "", "examples": []},
        ]
        self.assertIn("\n\n----\n\n", vd.build_message(entries))


class DespaceTest(unittest.TestCase):
    def test_drops_word_spacing_between_japanese_chars(self):
        self.assertEqual(
            vd._despace_japanese("部屋[へや]を 片付[かたづ]けて 友達[ともだち]"),
            "部屋[へや]を片付[かたづ]けて友達[ともだち]",
        )

    def test_keeps_spaces_around_ascii(self):
        self.assertEqual(vd._despace_japanese("JLPT N3 の 試験[しけん]"), "JLPT N3 の試験[しけん]")


def _write_tsv_and_track(case: unittest.TestCase) -> str:
    path = _write_tsv(_TSV)
    case.addCleanup(os.unlink, path)
    return path


if __name__ == "__main__":
    unittest.main()
