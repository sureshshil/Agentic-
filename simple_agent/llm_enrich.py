"""Optional per-push LLM enrichment for kanji_drip.py / grammar_drip.py -
adds a full practice block (multiple fresh example sentences, a short
explanation, a mini dialogue, and an active-recall practice question)
generated fresh each time an item is pushed, on top of the hand-curated
CSV content those scripts already send. The CSV content itself is
untouched and always shown; this only ever ADDS clearly-labeled "AI"
sections, so a model outage or a bad response never degrades what was
already working (see enrich_kanji/enrich_grammar - both return None
instead of raising on any failure).

Deliberately does NOT ask for a mnemonic/component-breakdown/usage-tip
for kanji, or restate nuance/contrast for grammar - both CSVs already
have hand-written versions of that (kanji: `component`, `disc_note`;
grammar: `nuance`, `contrast`, `trap`, `contrast_note`) shown directly by
each drip's build_body_lines; reinventing that with an LLM would just be
a worse, non-curated duplicate.

Same provider as the rest of this repo's scheduled/ scripts: Gemini via
Vertex AI (see CLAUDE.md's "Model provider split"), reusing agent.py's
build_client()/pricing pattern rather than importing telegram_bot.py's
heavier tool-loop Agent (this only ever needs one plain, tool-free
generate_content call).

Cost control: a LIFETIME cap (ENRICH_MAX_COST_USD, like telegram_bot.py's
own AGENT_MAX_COST_USD) tracked in .llm_enrich_usage.json next to the
other dotfile state. Once the cap is hit, enrich_kanji/enrich_grammar
just return None (fall back to CSV-only content) instead of raising -
an hourly/2-hourly cron job should never break because of this. Same
file backs both kanji and grammar (they're cheap and share one budget;
splitting it in two would just mean each drip's cap is reached twice as
fast for no benefit).

Env vars (loaded by kanji_drip.py/grammar_drip.py's existing dotenv
calls - nothing new to source):
  GCP_PROJECT_ID / GCP_LOCATION / GOOGLE_APPLICATION_CREDENTIALS
                            same as agent.py/telegram_bot.py - enrichment
                            is silently skipped (returns None) if
                            GCP_PROJECT_ID isn't set at all, so existing
                            deployments without Vertex AI configured see
                            no behavior change.
  DRIP_ENRICH_MAX_COST_USD  optional, default 1.00 - lifetime cap shared
                            by kanji + grammar enrichment. The richer
                            per-item content here (~4-6x the tokens of a
                            single example) burns through this faster
                            than the earlier one-example-only version -
                            raise it accordingly (e.g. 20.00) if you want
                            this running continuously without re-hitting
                            the cap.
"""

import html
import json
import os

_SIMPLE_AGENT_DIR = os.path.dirname(os.path.abspath(__file__))

MODEL = "gemini-3.7-flash"

# Same gemini-3.7-flash pricing as agent.py - keep both in sync if you
# switch models (see agent.py's comment on the 2027-01-01 rate change).
INPUT_COST_PER_MTOK = 0.75
OUTPUT_COST_PER_MTOK = 3.75  # includes thinking tokens

# Headroom for the JSON response itself - generous since thinking is off
# (thinking_budget=0 below) so every token here goes to the actual
# output. The full practice block (multiple examples + explanation +
# dialogue + practice question) needs meaningfully more room than a
# single example did.
MAX_OUTPUT_TOKENS = 2048

ENRICH_MAX_COST_USD = float(os.environ.get("DRIP_ENRICH_MAX_COST_USD") or "1.00")
USAGE_PATH = os.environ.get("DRIP_ENRICH_USAGE_PATH") or os.path.join(
    _SIMPLE_AGENT_DIR, ".llm_enrich_usage.json"
)

_client = None  # lazily built - only ever needed once GCP_PROJECT_ID is confirmed set


def _build_client():
    from google import genai

    return genai.Client(
        vertexai=True,
        project=os.environ["GCP_PROJECT_ID"],
        location=os.environ.get("GCP_LOCATION", "global"),
    )


def load_cache(path: str) -> dict:
    """key -> last-generated enrichment dict for one deck (kanji_drip.py
    and grammar_drip.py each keep their own file). Populated at push time
    (main()) and read back by webapp.py when it later renders the Mini
    App review page for that same batch - see each drip's main()."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cache_entry(path: str, key: str, entry: dict) -> None:
    """Overwrites any previous enrichment for `key` - each push generates
    fresh content, so only the most recent one is worth keeping. (A key
    pushed again in a later batch before an earlier link is opened will
    show the newer content in both places - acceptable given how rarely
    the unrated-resurface window and a stale link actually overlap.)"""
    cache = load_cache(path)
    cache[key] = entry
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _load_usage() -> dict:
    try:
        with open(USAGE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}


def _save_usage(usage: dict) -> None:
    with open(USAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(usage, f, ensure_ascii=False, indent=2)


def _generate(prompt: str, schema: dict) -> dict | None:
    """One tool-free generate_content call constrained to `schema` via
    response_mime_type=application/json. Returns the parsed dict, or None
    on ANY failure (missing/broken credentials, cap reached, a request
    error, a non-JSON response) - callers treat None as "skip enrichment
    for this item, send the CSV content as-is"."""
    if not os.environ.get("GCP_PROJECT_ID"):
        return None

    usage = _load_usage()
    if usage["cost_usd"] >= ENRICH_MAX_COST_USD:
        print(
            f"[llm_enrich] lifetime cost ${usage['cost_usd']:.4f} has reached the "
            f"${ENRICH_MAX_COST_USD:.4f} cap (DRIP_ENRICH_MAX_COST_USD) - skipping "
            "enrichment, sending CSV content only."
        )
        return None

    global _client
    try:
        from google.genai import types

        if _client is None:
            _client = _build_client()

        response = _client.models.generate_content(
            model=MODEL,
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                # This is a short, templated extraction task with no need
                # for multi-step reasoning - without this, gemini-3.7-flash
                # burns most/all of max_output_tokens on hidden "thinking"
                # tokens first and gets cut off (finish_reason=MAX_TOKENS)
                # before emitting the actual JSON, which then fails to parse.
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        parsed = json.loads(response.text)

        usage_metadata = response.usage_metadata
        cost_delta = (
            (usage_metadata.prompt_token_count or 0) * INPUT_COST_PER_MTOK
            + ((usage_metadata.candidates_token_count or 0) + (usage_metadata.thoughts_token_count or 0))
            * OUTPUT_COST_PER_MTOK
        ) / 1_000_000
        usage["calls"] += 1
        usage["input_tokens"] += usage_metadata.prompt_token_count or 0
        usage["output_tokens"] += usage_metadata.candidates_token_count or 0
        usage["cost_usd"] += cost_delta
        _save_usage(usage)

        return parsed
    except Exception as exc:  # noqa: BLE001 - any failure here must fall back, never break the cron run
        print(f"[llm_enrich] enrichment call failed ({exc!r}) - sending CSV content only.")
        return None


_EXAMPLE_ITEM = {
    "type": "OBJECT",
    "properties": {
        "jp": {"type": "STRING"},
        "reading": {"type": "STRING"},
        "en": {"type": "STRING"},
    },
    "required": ["jp", "reading", "en"],
}

_DIALOGUE_TURN = {
    "type": "OBJECT",
    "properties": {
        "speaker": {"type": "STRING"},
        "jp": {"type": "STRING"},
        "en": {"type": "STRING"},
    },
    "required": ["speaker", "jp", "en"],
}

_PRACTICE_QUESTION = {
    "type": "OBJECT",
    "properties": {
        "question": {"type": "STRING"},
        "options": {"type": "ARRAY", "items": {"type": "STRING"}},
        "answer": {"type": "STRING"},
    },
    "required": ["question", "options", "answer"],
}

_KANJI_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "examples": {"type": "ARRAY", "items": _EXAMPLE_ITEM},
        "explanation": {"type": "STRING"},
        "dialogue": {"type": "ARRAY", "items": _DIALOGUE_TURN},
        "practice_question": _PRACTICE_QUESTION,
    },
    "required": ["examples", "explanation", "dialogue", "practice_question"],
}

# Grammar's examples/dialogue omit the per-line `reading` field - unlike
# kanji_drip.py, grammar_drip.py's curated content already carries a
# reading only for its own ex1 (ex1_reading), and the AI content doesn't
# need to match that shape.
_GRAMMAR_EXAMPLE_ITEM = {
    "type": "OBJECT",
    "properties": {"jp": {"type": "STRING"}, "en": {"type": "STRING"}},
    "required": ["jp", "en"],
}
_GRAMMAR_DIALOGUE_TURN = {
    "type": "OBJECT",
    "properties": {
        "speaker": {"type": "STRING"},
        "jp": {"type": "STRING"},
        "en": {"type": "STRING"},
    },
    "required": ["speaker", "jp", "en"],
}

_GRAMMAR_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "examples": {"type": "ARRAY", "items": _GRAMMAR_EXAMPLE_ITEM},
        "explanation": {"type": "STRING"},
        "dialogue": {"type": "ARRAY", "items": _GRAMMAR_DIALOGUE_TURN},
        "practice_question": _PRACTICE_QUESTION,
    },
    "required": ["examples", "explanation", "dialogue", "practice_question"],
}


def enrich_kanji(row: dict, level: str) -> dict | None:
    """A full AI practice block for a kanji flashcard, different each
    call, or None (see _generate):
      - examples: 2-3 example sentences ({"jp", "reading", "en"}), each
        different in register/context (casual, formal, question, etc.)
      - explanation: why the FIRST example naturally uses this kanji
        this way (word choice/register) - not a mnemonic or component
        breakdown, which the CSV already covers (see module docstring).
      - dialogue: a short 2-4 turn natural conversation using the kanji.
      - practice_question: a multiple-choice question testing this
        kanji's reading or meaning, with 4 options and the answer.
    `row` is a kanji_drip.py CSV row."""
    kanji = (row.get("kanji") or "").strip()
    readings = " / ".join(
        r.strip() for r in (row.get("onyomi", ""), row.get("kunyomi", "")) if r.strip()
    )
    meaning = (row.get("meaning_en") or "").strip()
    prompt = (
        f"You are building a rich practice block for a JLPT {level} learner "
        f"reviewing the kanji '{kanji}' (reading: {readings}; meaning: {meaning}) "
        "via spaced-repetition flashcards. Generate:\n"
        f"1. examples: 2-3 natural example sentences at JLPT {level} difficulty "
        "using this kanji, each in a different register/context (e.g. one casual, "
        "one more formal or written, one a question) and different from typical "
        "textbook examples. Each needs the Japanese sentence (jp), its reading in "
        "hiragana/katakana (reading), and a natural English translation (en).\n"
        "2. explanation: ONE brief note (1-2 sentences) on why your FIRST example "
        "sentence naturally uses this kanji the way it does (e.g. word choice, "
        "register, or common collocation) - NOT a mnemonic or a breakdown of the "
        "kanji's components, since that's already covered elsewhere.\n"
        "3. dialogue: a short, natural 2-4 turn conversation between two speakers "
        "(A and B) that uses this kanji at least once, each turn with jp/en.\n"
        "4. practice_question: ONE multiple-choice question testing this kanji's "
        "reading or meaning in context (a cloze sentence with a blank, or 'what "
        "does X mean/how is X read'), with exactly 4 short options and the correct "
        "answer (answer must be one of the options, verbatim)."
    )
    return _generate(prompt, _KANJI_SCHEMA)


def enrich_grammar(row: dict, level: str) -> dict | None:
    """A full AI practice block for a grammar flashcard, different each
    call, or None (see _generate):
      - examples: 2-3 example sentences ({"jp", "en"}), each different in
        register/context.
      - explanation: why the FIRST example naturally uses this pattern
        this way - not a general nuance/contrast restatement, which the
        CSV already covers (see module docstring).
      - dialogue: a short 2-4 turn natural conversation using the pattern.
      - practice_question: a multiple-choice question testing this
        pattern's usage, with 4 options and the answer.
    `row` is a grammar_drip.py CSV row."""
    pattern = (row.get("grammar") or "").strip()
    formation = (row.get("formation") or "").strip()
    meaning = (row.get("meaning_en") or "").strip()
    prompt = (
        f"You are building a rich practice block for a JLPT {level} learner "
        f"reviewing the grammar pattern '{pattern}' (formation: {formation}; "
        f"meaning: {meaning}) via spaced-repetition flashcards. Generate:\n"
        f"1. examples: 2-3 natural example sentences at JLPT {level} difficulty "
        "demonstrating this pattern, each in a different register/context (e.g. "
        "one casual, one more formal or written, one a question) and different "
        "from typical textbook examples. Each needs the Japanese sentence (jp) and "
        "a natural English translation (en).\n"
        "2. explanation: ONE brief note (1-2 sentences) on why your FIRST example "
        "sentence naturally uses this pattern the way it does - NOT a general "
        "restatement of the pattern's nuance or a contrast with a similar pattern, "
        "since that's already covered elsewhere.\n"
        "3. dialogue: a short, natural 2-4 turn conversation between two speakers "
        "(A and B) that uses this pattern at least once, each turn with jp/en.\n"
        "4. practice_question: ONE multiple-choice question testing correct usage "
        "of this pattern (a cloze sentence with a blank, or 'which sentence "
        "correctly uses X'), with exactly 4 short options and the correct answer "
        "(answer must be one of the options, verbatim)."
    )
    return _generate(prompt, _GRAMMAR_SCHEMA)


def format_blocks(enrichment: dict) -> list:
    """An enrich_kanji()/enrich_grammar() result rendered into HTML-
    escaped blocks - one string per section (examples, explanation,
    dialogue, practice question), each possibly containing internal
    '\\n's. kanji_drip.py/grammar_drip.py's build_body_lines each append
    a blank-line separator before every block this returns, same
    spacing convention as their own curated sections. Shared here since
    both decks' enrichment has the same shape (grammar's examples/
    dialogue just omit the per-line `reading` field kanji's have)."""
    blocks = []

    examples = enrichment.get("examples") or []
    for i, example in enumerate(examples, start=1):
        line = f"\U0001f916 例文{i}: {html.escape(example['jp'])}"
        if example.get("reading"):
            line += f"\n{html.escape(example['reading'])}"
        line += f"\n— {html.escape(example['en'])}"
        blocks.append(line)

    if enrichment.get("explanation"):
        blocks.append(f"\U0001f916 解説: {html.escape(enrichment['explanation'])}")

    dialogue = enrichment.get("dialogue") or []
    if dialogue:
        dialogue_lines = ["\U0001f916 会話:"]
        for turn in dialogue:
            speaker = html.escape(turn.get("speaker") or "")
            dialogue_lines.append(f"{speaker}: {html.escape(turn['jp'])} ({html.escape(turn['en'])})")
        blocks.append("\n".join(dialogue_lines))

    question = enrichment.get("practice_question")
    if question:
        question_lines = [f"\U0001f916 クイズ: {html.escape(question['question'])}"]
        for letter, option in zip("ABCD", question.get("options") or []):
            question_lines.append(f"{letter}) {html.escape(option)}")
        question_lines.append(f"✅ 答え: {html.escape(question.get('answer') or '')}")
        blocks.append("\n".join(question_lines))

    return blocks
