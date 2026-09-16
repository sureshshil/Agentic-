"""Regression tests for llm_enrich.py - the optional per-push Gemini
enrichment shared by kanji_drip.py/grammar_drip.py. Covers the parts with
real correctness risk: skipping cleanly when GCP_PROJECT_ID isn't set,
enforcing the lifetime cost cap, parsing a successful response into the
usage file, and falling back to None (never raising) on any failure -
kanji_drip.py/grammar_drip.py rely on that last guarantee to keep the
cron job running through a model outage. Also covers format_blocks,
which renders the shared enrich_kanji()/enrich_grammar() result shape
(examples/explanation/dialogue/practice_question) into HTML-escaped
blocks.

The Vertex AI client itself is never actually called - llm_enrich._client
is monkeypatched with a fake object, so these run offline with no
credentials needed, same as the rest of this repo's tests.

Run: python3 -m unittest tests.test_llm_enrich -v   (from simple_agent/)
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import llm_enrich  # noqa: E402


def _fake_response(payload: dict, prompt_tokens=100, output_tokens=50, thoughts_tokens=0):
    response = MagicMock()
    response.text = json.dumps(payload)
    response.usage_metadata = MagicMock(
        prompt_token_count=prompt_tokens,
        candidates_token_count=output_tokens,
        thoughts_token_count=thoughts_tokens,
    )
    return response


class GenerateTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.usage_path = os.path.join(self.tmp_dir, "usage.json")
        self._orig_usage_path = llm_enrich.USAGE_PATH
        self._orig_cap = llm_enrich.ENRICH_MAX_COST_USD
        self._orig_client = llm_enrich._client
        self._orig_project_id = os.environ.get("GCP_PROJECT_ID")
        llm_enrich.USAGE_PATH = self.usage_path
        os.environ["GCP_PROJECT_ID"] = "test-project"

    def tearDown(self):
        llm_enrich.USAGE_PATH = self._orig_usage_path
        llm_enrich.ENRICH_MAX_COST_USD = self._orig_cap
        llm_enrich._client = self._orig_client
        if self._orig_project_id is None:
            os.environ.pop("GCP_PROJECT_ID", None)
        else:
            os.environ["GCP_PROJECT_ID"] = self._orig_project_id

    def test_skips_the_call_entirely_when_gcp_project_id_is_unset(self):
        os.environ.pop("GCP_PROJECT_ID", None)
        client = MagicMock()
        llm_enrich._client = client
        result = llm_enrich._generate("prompt", {"type": "OBJECT", "properties": {}})
        self.assertIsNone(result)
        client.models.generate_content.assert_not_called()

    def test_skips_the_call_once_the_lifetime_cost_cap_is_reached(self):
        llm_enrich.ENRICH_MAX_COST_USD = 0.01
        llm_enrich._save_usage({"calls": 5, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.02})
        client = MagicMock()
        llm_enrich._client = client
        result = llm_enrich._generate("prompt", {"type": "OBJECT", "properties": {}})
        self.assertIsNone(result)
        client.models.generate_content.assert_not_called()

    def test_successful_call_returns_parsed_json_and_records_usage(self):
        client = MagicMock()
        client.models.generate_content.return_value = _fake_response(
            {"example_jp": "例", "example_en": "example"}, prompt_tokens=1000, output_tokens=500
        )
        llm_enrich._client = client
        result = llm_enrich._generate("prompt", {"type": "OBJECT", "properties": {}})
        self.assertEqual(result, {"example_jp": "例", "example_en": "example"})

        usage = llm_enrich._load_usage()
        self.assertEqual(usage["calls"], 1)
        self.assertEqual(usage["input_tokens"], 1000)
        self.assertEqual(usage["output_tokens"], 500)
        expected_cost = (1000 * llm_enrich.INPUT_COST_PER_MTOK + 500 * llm_enrich.OUTPUT_COST_PER_MTOK) / 1_000_000
        self.assertAlmostEqual(usage["cost_usd"], expected_cost)

    def test_repeated_calls_accumulate_cost_in_the_usage_file(self):
        client = MagicMock()
        client.models.generate_content.return_value = _fake_response(
            {"a": "b"}, prompt_tokens=1000, output_tokens=1000
        )
        llm_enrich._client = client
        llm_enrich._generate("prompt", {"type": "OBJECT", "properties": {}})
        llm_enrich._generate("prompt", {"type": "OBJECT", "properties": {}})
        usage = llm_enrich._load_usage()
        self.assertEqual(usage["calls"], 2)
        self.assertEqual(usage["input_tokens"], 2000)

    def test_client_exception_returns_none_instead_of_raising(self):
        client = MagicMock()
        client.models.generate_content.side_effect = RuntimeError("quota exceeded")
        llm_enrich._client = client
        result = llm_enrich._generate("prompt", {"type": "OBJECT", "properties": {}})
        self.assertIsNone(result)

    def test_non_json_response_returns_none_instead_of_raising(self):
        client = MagicMock()
        response = MagicMock()
        response.text = "not valid json"
        response.usage_metadata = MagicMock(
            prompt_token_count=10, candidates_token_count=5, thoughts_token_count=0
        )
        client.models.generate_content.return_value = response
        llm_enrich._client = client
        result = llm_enrich._generate("prompt", {"type": "OBJECT", "properties": {}})
        self.assertIsNone(result)


class EnrichKanjiGrammarTest(unittest.TestCase):
    """enrich_kanji/enrich_grammar build the prompt+schema and delegate to
    _generate - covered here by patching _generate directly rather than
    re-testing its internals."""

    def setUp(self):
        self._orig_generate = llm_enrich._generate

    def tearDown(self):
        llm_enrich._generate = self._orig_generate

    def test_enrich_kanji_passes_kanji_fields_into_the_prompt(self):
        captured = {}

        def fake_generate(prompt, schema):
            captured["prompt"] = prompt
            captured["schema"] = schema
            return {"examples": [], "explanation": "x", "dialogue": [], "practice_question": {}}

        llm_enrich._generate = fake_generate
        row = {"kanji": "決", "onyomi": "ケツ", "kunyomi": "き.める", "meaning_en": "decide"}
        result = llm_enrich.enrich_kanji(row, "N3")
        self.assertEqual(result["explanation"], "x")
        self.assertIn("決", captured["prompt"])
        self.assertIn("decide", captured["prompt"])
        self.assertEqual(captured["schema"], llm_enrich._KANJI_SCHEMA)

    def test_enrich_grammar_passes_pattern_fields_into_the_prompt(self):
        captured = {}

        def fake_generate(prompt, schema):
            captured["prompt"] = prompt
            captured["schema"] = schema
            return {"examples": [], "explanation": "x", "dialogue": [], "practice_question": {}}

        llm_enrich._generate = fake_generate
        row = {"grammar": "〜ようになる", "formation": "V-る + ようになる", "meaning_en": "come to do"}
        result = llm_enrich.enrich_grammar(row, "N3")
        self.assertEqual(result["explanation"], "x")
        self.assertIn("〜ようになる", captured["prompt"])
        self.assertIn("N3", captured["prompt"])
        self.assertEqual(captured["schema"], llm_enrich._GRAMMAR_SCHEMA)


class FormatBlocksTest(unittest.TestCase):
    def test_renders_numbered_examples_with_reading(self):
        enrichment = {
            "examples": [
                {"jp": "決めた。", "reading": "きめた。", "en": "I decided."},
                {"jp": "決まった？", "reading": "きまった？", "en": "Is it decided?"},
            ],
        }
        blocks = llm_enrich.format_blocks(enrichment)
        self.assertEqual(len(blocks), 2)
        self.assertIn("例文1", blocks[0])
        self.assertIn("決めた。", blocks[0])
        self.assertIn("きめた。", blocks[0])
        self.assertIn("I decided.", blocks[0])
        self.assertIn("例文2", blocks[1])

    def test_renders_examples_without_reading_field(self):
        enrichment = {"examples": [{"jp": "決めた。", "en": "I decided."}]}
        blocks = llm_enrich.format_blocks(enrichment)
        self.assertEqual(len(blocks), 1)
        self.assertIn("決めた。", blocks[0])
        self.assertIn("I decided.", blocks[0])

    def test_renders_explanation_block(self):
        blocks = llm_enrich.format_blocks({"explanation": "Uses casual speech because it's a diary entry."})
        self.assertEqual(len(blocks), 1)
        self.assertIn("解説", blocks[0])
        self.assertIn("diary entry", blocks[0])

    def test_renders_dialogue_block_with_all_turns(self):
        enrichment = {
            "dialogue": [
                {"speaker": "A", "jp": "もう決めた？", "en": "Have you decided yet?"},
                {"speaker": "B", "jp": "うん、決めたよ。", "en": "Yeah, I decided."},
            ]
        }
        blocks = llm_enrich.format_blocks(enrichment)
        self.assertEqual(len(blocks), 1)
        self.assertIn("会話", blocks[0])
        self.assertIn("もう決めた？", blocks[0])
        self.assertIn("うん、決めたよ。", blocks[0])

    def test_renders_practice_question_with_lettered_options_and_answer(self):
        enrichment = {
            "practice_question": {
                "question": "彼は進路を＿＿＿。",
                "options": ["決めた", "決まった", "決めている", "決められた"],
                "answer": "決めた",
            }
        }
        blocks = llm_enrich.format_blocks(enrichment)
        self.assertEqual(len(blocks), 1)
        self.assertIn("クイズ", blocks[0])
        self.assertIn("A) 決めた", blocks[0])
        self.assertIn("D) 決められた", blocks[0])
        self.assertIn("答え: 決めた", blocks[0])

    def test_missing_sections_are_simply_omitted(self):
        self.assertEqual(llm_enrich.format_blocks({}), [])

    def test_full_enrichment_produces_one_block_per_section(self):
        enrichment = {
            "examples": [{"jp": "a", "en": "b"}, {"jp": "c", "en": "d"}],
            "explanation": "e",
            "dialogue": [{"speaker": "A", "jp": "f", "en": "g"}],
            "practice_question": {"question": "h", "options": ["i", "j"], "answer": "i"},
        }
        blocks = llm_enrich.format_blocks(enrichment)
        self.assertEqual(len(blocks), 5)  # 2 examples + explanation + dialogue + question


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp_dir, "cache.json")

    def test_missing_cache_file_loads_as_empty(self):
        self.assertEqual(llm_enrich.load_cache(self.path), {})

    def test_save_then_load_round_trips(self):
        llm_enrich.save_cache_entry(self.path, "決", {"example_jp": "x"})
        self.assertEqual(llm_enrich.load_cache(self.path), {"決": {"example_jp": "x"}})

    def test_saving_a_second_entry_preserves_the_first(self):
        llm_enrich.save_cache_entry(self.path, "決", {"example_jp": "a"})
        llm_enrich.save_cache_entry(self.path, "続", {"example_jp": "b"})
        cache = llm_enrich.load_cache(self.path)
        self.assertEqual(set(cache), {"決", "続"})

    def test_saving_the_same_key_again_overwrites_it(self):
        llm_enrich.save_cache_entry(self.path, "決", {"example_jp": "old"})
        llm_enrich.save_cache_entry(self.path, "決", {"example_jp": "new"})
        self.assertEqual(llm_enrich.load_cache(self.path)["決"]["example_jp"], "new")


if __name__ == "__main__":
    unittest.main()
