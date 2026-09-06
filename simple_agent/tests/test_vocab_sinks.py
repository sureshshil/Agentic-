"""Regression tests for vocab_drip's optional Notion / Google Doc sinks.

No live API calls: requests and the Google-auth token fetch are mocked.
The load-bearing property both sinks must have: they are BEST-EFFORT - a
failure returns an "Error: ..." string, never raises, so the Telegram
drip in vocab_drip.py always still goes out. The Notion sink is one row
per word (3 examples in their own columns) and best-effort per row: one
bad word doesn't stop the others.

Run: python3 -m unittest tests.test_vocab_sinks -v   (from simple_agent/)
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scheduled"))
import vocab_sinks as vs  # noqa: E402

WHEN = datetime(2026, 9, 6, 8, 0, tzinfo=timezone.utc)

ENTRIES = [
    {
        "word": "遠慮",
        "reading": "えんりょ",
        "meaning": "restraint, holding back",
        "type": "する-verb",
        "particles": "を + 遠慮する / に + 遠慮する",
        "examples": [
            "写真[しゃしん]はご遠慮[えんりょ]ください。 — Please refrain from taking photos.",
            "彼[かれ]は遠慮[えんりょ]がちに入[はい]ってきた。 — He came in hesitantly.",
            "遠慮[えんりょ]しないで食[た]べてね。 — Don't hold back, eat up.",
        ],
    },
    {
        "word": "締め切り",
        "reading": "しめきり",
        "meaning": "deadline",
        "particles": "までに",
        "examples": ["締[し]め切[き]りは金曜[きんよう]です。 — The deadline is Friday."],
    },
]


def _resp(status=200, body=None):
    r = MagicMock()
    r.status_code = status
    r.text = "" if body is None else str(body)
    r.json.return_value = body or {}
    return r


class EntryExamplesTest(unittest.TestCase):
    def test_returns_the_non_empty_sentences(self):
        self.assertEqual(len(vs._entry_examples(ENTRIES[0])), 3)

    def test_a_bare_string_becomes_one_item(self):
        self.assertEqual(vs._entry_examples({"examples": "just one — x"}), ["just one — x"])

    def test_missing_or_blank_examples_is_empty_list(self):
        self.assertEqual(vs._entry_examples({"word": "猫"}), [])
        self.assertEqual(vs._entry_examples({"examples": ["", "  "]}), [])


class RowPropertiesTest(unittest.TestCase):
    def test_maps_each_field_and_the_three_example_columns(self):
        props = vs._row_properties(ENTRIES[0], "N3")

        def rt(name):
            return props[name]["rich_text"][0]["text"]["content"]

        self.assertEqual(props["Word"]["title"][0]["text"]["content"], "遠慮")
        self.assertEqual(rt("Reading"), "えんりょ")
        self.assertEqual(rt("Meaning"), "restraint, holding back")
        self.assertEqual(rt("Particle"), "を + 遠慮する / に + 遠慮する")
        self.assertEqual(props["Type"]["select"]["name"], "する-verb")
        self.assertEqual(props["JLPT"]["select"]["name"], "N3")
        self.assertTrue(rt("Example 1").startswith("写真"))
        self.assertTrue(rt("Example 2").startswith("彼"))
        self.assertTrue(rt("Example 3").startswith("遠慮"))

    def test_fewer_than_three_examples_leaves_trailing_columns_unset(self):
        props = vs._row_properties(ENTRIES[1], "N3")
        self.assertIn("Example 1", props)
        self.assertNotIn("Example 2", props)
        self.assertNotIn("Example 3", props)

    def test_more_than_three_examples_are_dropped(self):
        entry = {"word": "x", "examples": ["a — 1", "b — 2", "c — 3", "d — 4"]}
        props = vs._row_properties(entry, "N3")
        self.assertEqual(set(props) & {"Example 1", "Example 2", "Example 3"},
                         {"Example 1", "Example 2", "Example 3"})
        self.assertNotIn("Example 4", props)

    def test_optional_columns_omitted_when_absent(self):
        props = vs._row_properties({"word": "猫"}, "N5")
        self.assertEqual(set(props), {"Word", "JLPT"})

    def test_rich_text_capped_at_2000_chars(self):
        props = vs._row_properties({"word": "x", "meaning": "m" * 5000}, "N3")
        self.assertEqual(
            len(props["Meaning"]["rich_text"][0]["text"]["content"]),
            vs.NOTION_MAX_RICH_TEXT_CHARS,
        )


class NotionAddRowTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {"NOTION_API_KEY": "secret_x", "NOTION_VOCAB_DB_ID": "db123"},
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_one_row_per_word(self):
        with patch.object(vs, "requests") as rq:
            rq.post.return_value = _resp(body={"id": "p"})
            result = vs.notion_add_row(ENTRIES, "N3")

        self.assertEqual(rq.post.call_count, len(ENTRIES))
        for call in rq.post.call_args_list:
            (url,) = call.args
            self.assertEqual(url, "https://api.notion.com/v1/pages")
            self.assertEqual(call.kwargs["json"]["parent"], {"database_id": "db123"})
        props = rq.post.call_args_list[0].kwargs["json"]["properties"]
        self.assertEqual(props["Word"]["title"][0]["text"]["content"], "遠慮")
        self.assertIn("Example 3", props)  # the word's 3 sentences in one row
        self.assertEqual(result, "Notion: added 2/2 word rows")

    def test_empty_entries_is_a_noop(self):
        with patch.object(vs, "requests") as rq:
            result = vs.notion_add_row([], "N3")
        rq.post.assert_not_called()
        self.assertEqual(result, "Notion: no word entries to add")

    def test_one_bad_row_does_not_stop_the_others(self):
        with patch.object(vs, "requests") as rq:
            rq.post.side_effect = [_resp(status=400, body="bad"), _resp(body={"id": "p"})]
            result = vs.notion_add_row(ENTRIES, "N3")

        self.assertEqual(rq.post.call_count, 2)
        self.assertTrue(result.startswith("Error: Notion added 1/2 word rows"))
        self.assertIn("1 failed", result)

    def test_network_exception_returns_error_string_and_does_not_raise(self):
        import requests as real_requests

        with patch.object(vs, "requests") as rq:
            rq.post.side_effect = real_requests.RequestException("boom")
            result = vs.notion_add_row(ENTRIES, "N3")
        self.assertTrue(result.startswith("Error: Notion added 0/2 word rows"))


class FuriganaTest(unittest.TestCase):
    S = "写真[しゃしん]撮影[さつえい]はご遠慮[えんりょ]ください。"

    def test_strip_furigana_keeps_kanji_drops_brackets(self):
        self.assertEqual(vs._strip_furigana(self.S), "写真撮影はご遠慮ください。")

    def test_reading_only_replaces_kanji_with_its_reading(self):
        self.assertEqual(vs._reading_only(self.S), "しゃしんさつえいはごえんりょください。")

    def test_a_kana_only_sentence_is_unchanged_by_both(self):
        self.assertEqual(vs._strip_furigana("これはペンです。"), "これはペンです。")
        self.assertEqual(vs._reading_only("これはペンです。"), "これはペンです。")


class GdocParagraphsTest(unittest.TestCase):
    def test_each_word_gets_a_heading_2_and_labelled_fields(self):
        paras = vs._gdoc_paragraphs(ENTRIES, "N3", WHEN, preamble=False, leading_blank=False)
        headings = [t for t, style in paras if style == "HEADING_2"]
        self.assertEqual(headings, ["遠慮 — えんりょ", "締め切り — しめきり"])
        flat = "\n".join(t for t, _ in paras)
        self.assertIn("Meaning: restraint, holding back", flat)
        self.assertIn("Part of speech: する-verb", flat)
        self.assertIn("Particle patterns (助詞): を + 遠慮する / に + 遠慮する", flat)

    def test_examples_are_written_three_ways(self):
        paras = vs._gdoc_paragraphs([ENTRIES[0]], "N3", WHEN, preamble=False, leading_blank=False)
        flat = "\n".join(t for t, _ in paras)
        self.assertIn("1. 写真はご遠慮ください。", flat)          # clean Japanese
        self.assertIn("   しゃしんはごえんりょください。", flat)     # kana
        self.assertIn("   Please refrain from taking photos.", flat)  # English

    def test_preamble_only_when_asked(self):
        with_p = vs._gdoc_paragraphs([ENTRIES[0]], "N3", WHEN, preamble=True, leading_blank=False)
        self.assertEqual(with_p[0], (vs._GDOC_PREAMBLE_TITLE, "HEADING_1"))
        without = vs._gdoc_paragraphs([ENTRIES[0]], "N3", WHEN, preamble=False, leading_blank=False)
        self.assertNotIn("HEADING_1", [s for _, s in without])


class ParagraphsToRequestsTest(unittest.TestCase):
    def test_heading_ranges_land_on_the_right_text(self):
        paras = [("intro", None), ("HEAD", "HEADING_2"), ("body", None)]
        text, style_reqs = vs._paragraphs_to_requests(paras, insert_at=10)
        self.assertEqual(text, "intro\nHEAD\nbody\n")
        self.assertEqual(len(style_reqs), 1)
        rng = style_reqs[0]["updateParagraphStyle"]["range"]
        # "HEAD\n" sits at offset 6..11 within text -> 16..21 after insert_at
        self.assertEqual((rng["startIndex"], rng["endIndex"]), (16, 21))
        self.assertEqual(text[6:10], "HEAD")


class GdocAppendTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {"GOOGLE_SERVICE_ACCOUNT_JSON": "/x/sa.json", "VOCAB_GDOC_ID": "doc123"},
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def _run(self, doc_content):
        with patch.object(vs, "_gdoc_access_token", return_value="tok"), patch.object(
            vs, "requests"
        ) as rq:
            rq.get.return_value = _resp(body={"body": {"content": doc_content}})
            rq.post.return_value = _resp(body={})
            result = vs.gdoc_append(ENTRIES, "N3", WHEN)
        return rq, result

    def test_non_empty_doc_appends_with_leading_blank_no_preamble(self):
        rq, result = self._run([{"endIndex": 1}, {"endIndex": 57}])
        reqs = rq.post.call_args.kwargs["json"]["requests"]
        insert = reqs[0]["insertText"]
        self.assertEqual(insert["location"]["index"], 56)  # endIndex - 1
        self.assertTrue(insert["text"].startswith("\n"))
        self.assertNotIn(vs._GDOC_PREAMBLE_TITLE, insert["text"])
        self.assertIn("Added 2026-09-06 08:00 UTC · N3", insert["text"])
        # heading style requests follow the insertText
        self.assertTrue(any("updateParagraphStyle" in r for r in reqs[1:]))
        self.assertTrue(result.startswith("Google Doc: appended 2 words"))

    def test_empty_doc_gets_preamble_and_no_leading_blank(self):
        rq, _ = self._run([{"endIndex": 2}])
        reqs = rq.post.call_args.kwargs["json"]["requests"]
        insert = reqs[0]["insertText"]
        self.assertEqual(insert["location"]["index"], 1)
        self.assertFalse(insert["text"].startswith("\n"))
        self.assertTrue(insert["text"].startswith(vs._GDOC_PREAMBLE_TITLE))
        self.assertTrue(any(
            r.get("updateParagraphStyle", {}).get("paragraphStyle", {}).get("namedStyleType")
            == "HEADING_1"
            for r in reqs[1:]
        ))

    def test_no_entries_is_a_noop(self):
        with patch.object(vs, "_gdoc_access_token") as tok, patch.object(vs, "requests") as rq:
            result = vs.gdoc_append([], "N3", WHEN)
        tok.assert_not_called()
        rq.get.assert_not_called()
        self.assertEqual(result, "Google Doc: no word entries to append")

    def test_token_failure_returns_error_string_and_does_not_raise(self):
        with patch.object(vs, "_gdoc_access_token", side_effect=RuntimeError("bad key")):
            result = vs.gdoc_append(ENTRIES, "N3", WHEN)
        self.assertTrue(result.startswith("Error: Google Doc append failed"))

    def test_batchupdate_http_error_returns_error_string(self):
        with patch.object(vs, "_gdoc_access_token", return_value="tok"), patch.object(
            vs, "requests"
        ) as rq:
            rq.get.return_value = _resp(body={"body": {"content": [{"endIndex": 5}]}})
            rq.post.return_value = _resp(status=403, body="forbidden")
            result = vs.gdoc_append(ENTRIES, "N3", WHEN)
        self.assertTrue(result.startswith("Error: Google Doc append failed"))


if __name__ == "__main__":
    unittest.main()
