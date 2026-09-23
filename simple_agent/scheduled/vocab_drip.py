"""JLPT-level Japanese vocabulary drip, spaced-repetition version - same
thin-entry-point cron pattern as rain_alert_cron.py and news_digest_agent.py
in this directory, and now the same active-recall shape as kanji_drip.py /
grammar_drip.py (see kanji_drip.py's docstring for the full rationale:
spoiler-hidden answers, a Leitner review interval per item via srs.py, due
reviews before new introductions, a daily cap on new items, unrated items
resurfacing instead of vanishing).

The one real difference from kanji/grammar: this pushes VOCAB_BATCH_SIZE
words per run instead of one, since 700 words is a much bigger deck to get
through and a single word per push would take far too long to cycle. Each
word is sent as its OWN Telegram message (send_srs_card, same call
kanji_drip.py/grammar_drip.py make, just once per word here) rather than
bundled into one long message - Telegram attaches an inline keyboard only
to the bottom of whatever message it's sent with, so bundling words would
mean every word's buttons piling up in one block below all the text,
forcing a scroll back down through the whole batch to rate word #1. One
message per word instead puts each word's Again/Hard/Good/Easy row right
under it, no scrolling. Skip a word entirely (don't tap anything) and it
resurfaces later as a reminder, same mechanism as kanji/grammar's unrated
timeout. A short header message announces the batch first.

The word content comes from the hand-curated TSV files next to this repo
(../N3_vocab_batch*.tsv by default, override with VOCAB_TSV_GLOB) - NOT
from an LLM call. The TSV column layout (Anki-style header lines starting
with '#', then a '#columns:' line naming the fields, then one
tab-separated row per word):

  word  rank  word_furigana  reading  type  pos  english  nepali
  sentence1  sentence1_en  sentence2  sentence2_en  sentence3  sentence3_en
  frame  note  tags

`frame` is the 助詞 / particle-pattern line; `note` is unused here. The
three sentences already carry inline [furigana].

Two card types per word, Anki-style recognition/production split (see
2026-09 discussion): a RECOGNITION card (front = word+furigana, everything
else behind the spoiler) is what every word gets from the start and is
the only thing that drives new-word introduction (VOCAB_NEW_PER_DAY). Once
a word's recognition card has survived enough real reviews to reach
VOCAB_PRODUCTION_MIN_BOX, a second PRODUCTION card unlocks for that same
word (front = meaning only, back = word+furigana+reading+particles+
examples) - the harder "given the meaning, produce the word and use it
correctly" direction. Production cards are gated on maturity and capped at
VOCAB_PRODUCTION_PER_DAY/day, deliberately separate from
VOCAB_NEW_PER_DAY, so testing production on words you already recognize
never competes with the budget for meeting brand-new words - see
_unlock_production_cards. Both card types for a word share one row, one
TSV lookup, and one cached enrichment block, but get their OWN independent
srs.py state entry (key vs. key+PRODUCTION_SUFFIX), so each is scheduled
on its own actual forgetting curve instead of one rating trying to cover
both skills at once.

Optionally, ON TOP of that curated content, a full AI practice block
(2-3 example sentences, a short explanation, a mini dialogue, and a
multiple-choice practice question) gets generated ONCE per word, the
first time it's ever picked, via Gemini (see ../llm_enrich.py) and shown
as extra "AI" sections - same mechanism as kanji_drip.py/grammar_drip.py.
This is where sentence variety comes from: when `frame` lists more than
one particle-affinity pattern, the curated TSV still only ships one
fixed sentence per pattern, so enrich_vocab is told about all the listed
patterns and asked to generate examples that actually exercise each one
- rather than hand-editing the TSV (the source of truth) for variety.
This block is generated once and then permanently cached
(VOCAB_ENRICH_CACHE_PATH), reused on every later review of that word -
never regenerated - so it becomes as stable a memory aid as the curated
card itself instead of showing different examples on every review; see
llm_enrich.load_cache's docstring for the full rationale. This is
additive and fails soft, so a model outage never removes the curated
card, and webapp.py's review page reads the same cache, so it always
shows this exact content.

Button taps are handled by telegram_bot.py's own long-polling loop
(handle_srs_callback), not by this script - this script only ever sends;
it never listens. State lives in .vocab_srs.json (gitignored, replacing
the old .vocab_sent_words.json history file - delete that old file
whenever, it's no longer read).

See ../deploy/cron-notifications.md for VPS cron setup, including the
quiet-hours window (there's no quiet-hours logic in this script itself).

Env vars (loaded via python-dotenv from ../telegram_bot.env and
../deploy/scheduled.env, same fill-in-what's-unset behavior as
telegram_bot.py - real environment variables always win):
  TELEGRAM_BOT_TOKEN        required, from ../telegram_bot.env
  TELEGRAM_ALLOWED_CHAT_ID  required, from ../telegram_bot.env
  VOCAB_LEVEL               optional, default "N3" (JLPT level, label only)
  VOCAB_BATCH_SIZE          optional, default 10 (words per push)
  VOCAB_NEW_PER_DAY         optional, default 16 (new words introduced/UTC day)
  VOCAB_PRODUCTION_MIN_BOX  optional, default 3 (srs.py box a word's
                            recognition card must reach before its
                            production card unlocks - see module docstring)
  VOCAB_PRODUCTION_PER_DAY  optional, default 5 (production cards
                            unlocked/UTC day - own budget, separate from
                            VOCAB_NEW_PER_DAY)
  VOCAB_UNRATED_RESURFACE_HOURS  optional, default 3 (see srs.pick_next).
                            Only relevant to the plain-text fallback
                            below (WEBAPP_BASE_URL unset): a sent-but-
                            never-tapped word comes back around as a
                            reminder instead of vanishing forever. With
                            WEBAPP_BASE_URL set, seen is only recorded
                            the moment you actually rate a word via the
                            review link - one you never rate just waits
                            until you go rate that same link)
  VOCAB_TSV_GLOB            optional, default ../N3_vocab_batch*.tsv
  VOCAB_SRS_PATH            optional, default ../.vocab_srs.json
  VOCAB_ENRICH_CACHE_PATH   optional, default ../.vocab_enrich_cache.json
  GCP_PROJECT_ID            optional - enables the per-push LLM practice
                            block (see ../llm_enrich.py); unset means the
                            drip behaves exactly as before.
  DRIP_ENRICH_MAX_COST_USD  optional, default 1.00 - lifetime cap shared
                            with kanji_drip.py/grammar_drip.py's
                            enrichment - see ../llm_enrich.py.

Optional extra sinks (each best-effort - a failure is logged and the
Telegram drip still goes out; skipped entirely unless its vars are set).
See scheduled/vocab_sinks.py and ../deploy/cron-notifications.md:
  NOTION_API_KEY               add one Notion row per word (both required)
  NOTION_VOCAB_DB_ID
  GOOGLE_SERVICE_ACCOUNT_JSON  also append each drip to a Google Doc,
  VOCAB_GDOC_ID                formatted as a NotebookLM source (both required)
"""

import csv
import glob
import html
import os
import re
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

# telegram_bot.py lives one directory up from this scheduled/ script;
# vocab_sinks.py and srs.py sit right next to it (srs.py) or next to this
# file (vocab_sinks.py).
_SCHEDULED_DIR = os.path.dirname(os.path.abspath(__file__))
_SIMPLE_AGENT_DIR = os.path.dirname(_SCHEDULED_DIR)
sys.path.insert(0, _SIMPLE_AGENT_DIR)
sys.path.insert(0, _SCHEDULED_DIR)

load_dotenv(os.path.join(_SIMPLE_AGENT_DIR, "telegram_bot.env"))
load_dotenv(os.path.join(_SIMPLE_AGENT_DIR, "deploy", "scheduled.env"))

from telegram_bot import send_srs_card, send_telegram_reply, send_review_link_card  # noqa: E402 - needs sys.path insert above
from vocab_sinks import gdoc_append, notion_add_row  # noqa: E402 - needs sys.path insert above
import llm_enrich  # noqa: E402 - needs sys.path insert above
import furigana  # noqa: E402
import srs  # noqa: E402 - needs sys.path insert above
import webapp  # noqa: E402 - needs sys.path insert above

VOCAB_LEVEL = os.environ.get("VOCAB_LEVEL") or "N3"
VOCAB_BATCH_SIZE = int(os.environ.get("VOCAB_BATCH_SIZE") or "10")
VOCAB_NEW_PER_DAY = int(os.environ.get("VOCAB_NEW_PER_DAY") or "16")
VOCAB_PRODUCTION_MIN_BOX = int(os.environ.get("VOCAB_PRODUCTION_MIN_BOX") or "3")
VOCAB_PRODUCTION_PER_DAY = int(os.environ.get("VOCAB_PRODUCTION_PER_DAY") or "5")
VOCAB_UNRATED_RESURFACE_HOURS = float(os.environ.get("VOCAB_UNRATED_RESURFACE_HOURS") or "3")
VOCAB_TSV_GLOB = os.environ.get("VOCAB_TSV_GLOB") or os.path.join(
    _SIMPLE_AGENT_DIR, "N3_vocab_batch*.tsv"
)
VOCAB_SRS_PATH = os.environ.get("VOCAB_SRS_PATH") or os.path.join(
    _SIMPLE_AGENT_DIR, ".vocab_srs.json"
)
VOCAB_ENRICH_CACHE_PATH = os.environ.get("VOCAB_ENRICH_CACHE_PATH") or os.path.join(
    _SIMPLE_AGENT_DIR, ".vocab_enrich_cache.json"
)
# See kanji_drip.py's WEBAPP_BASE_URL comment - same opt-in switch. When
# set, the whole batch goes out as one browser review link (swipeable
# flashcard deck) instead of one Telegram message per word.
WEBAPP_BASE_URL = (os.environ.get("WEBAPP_BASE_URL") or "").rstrip("/")

# The '#columns:<tab-separated names>' line in each Anki-style TSV header.
_COLUMN_HEADER_PREFIX = "#columns:"
# Sentence columns, paired (japanese, english), in display order.
_SENTENCE_COLUMNS = [
    ("sentence1", "sentence1_en"),
    ("sentence2", "sentence2_en"),
    ("sentence3", "sentence3_en"),
]
# Anki's furigana-helper word spacing: a space whose left neighbour is a
# non-ASCII char or a furigana-closing ']' and whose right neighbour is a
# non-ASCII char. Drop it so sentences read the way the old LLM drip wrote
# them ("部屋[へや]を片付[かたづ]けて", not "部屋[へや]を 片付[かたづ]けて") - real
# spaces around ASCII words ("JLPT N3") are left alone.
_JP_WORD_SPACE_RE = re.compile(r"(?<=\]|[^\x00-\x7f])[ \t]+(?=[^\x00-\x7f])")

def _fx(text: str) -> str:
    """HTML-escaped text with inline furigana added to any bare kanji."""
    return html.escape(furigana.annotate(text))


_STATUS_MARKERS = {"new": "\U0001f210", "due": "\U0001f501", "reminder": "⏰"}


def _despace_japanese(text: str) -> str:
    return _JP_WORD_SPACE_RE.sub("", text).strip()


def load_rows(glob_pattern: str) -> list:
    """Every data row across the TSV files matching `glob_pattern`, as a
    list of dicts keyed by the '#columns:' header. Files are read in
    sorted-name order; a word seen in an earlier file wins (batch1 covers
    the lower ranks), so duplicates across files are dropped. Row order
    within/across files IS rank order."""
    rows: list = []
    seen: set = set()
    for path in sorted(glob.glob(glob_pattern)):
        columns = None
        data_lines: list = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith(_COLUMN_HEADER_PREFIX):
                    columns = line.rstrip("\n")[len(_COLUMN_HEADER_PREFIX):].split("\t")
                elif line.strip() and not line.startswith("#"):
                    data_lines.append(line)
        if columns is None:
            raise SystemExit(f"{path}: no '{_COLUMN_HEADER_PREFIX}' header line found")
        for values in csv.reader(data_lines, delimiter="\t", quoting=csv.QUOTE_NONE):
            row = dict(zip(columns, values))
            word = (row.get("word") or "").strip()
            if not word or word in seen:
                continue
            seen.add(word)
            rows.append(row)
    return rows


# Appended to a word to make its production-card srs.py state key (see
# module docstring) - "::" can't appear in a TSV word column, and is safe
# inside telegram_bot.py's "|"-delimited SRS callback_data (handle_srs_
# callback splits on "|" only, so this never collides with that parsing).
PRODUCTION_SUFFIX = "::production"


def production_key(word: str) -> str:
    return word + PRODUCTION_SUFFIX


def is_production_key(key: str) -> bool:
    return key.endswith(PRODUCTION_SUFFIX)


def base_word(key: str) -> str:
    """`key` with any production suffix stripped - the TSV word either
    card type's content actually comes from."""
    return key[: -len(PRODUCTION_SUFFIX)] if is_production_key(key) else key


def find_row(key: str) -> dict:
    """The TSV row for one word (or its production-card key - see
    base_word), or None - used by webapp.py to look up a card's content
    from its review link (see main()'s WEBAPP_BASE_URL branch)."""
    word = base_word(key)
    for row in load_rows(VOCAB_TSV_GLOB):
        if (row.get("word") or "").strip() == word:
            return row
    return None


def row_to_entry(row: dict) -> dict:
    """One TSV row -> the structured dict both format_word_block (below)
    and the Notion / Google Doc sinks expect (word / reading / meaning /
    type / particles / examples), plus a couple of extra keys the sinks
    ignore for now (nepali / note)."""
    examples = []
    for jp_col, en_col in _SENTENCE_COLUMNS:
        japanese = _despace_japanese(row.get(jp_col, ""))
        english = (row.get(en_col) or "").strip()
        if not japanese:
            continue
        examples.append(f"{japanese} — {english}" if english else japanese)
    return {
        "word": (row.get("word") or "").strip(),
        "reading": (row.get("reading") or "").strip(),
        "meaning": (row.get("english") or "").strip(),
        "type": (row.get("type") or row.get("pos") or "").strip(),
        "particles": (row.get("frame") or "").strip(),
        "examples": examples,
        "nepali": (row.get("nepali") or "").strip(),
        "note": (row.get("note") or "").strip(),
    }


def card_front(key: str, row: dict) -> str:
    """Plain-text front-card prompt for `key` - the meaning alone for a
    production card (see is_production_key: everything else, including
    the word itself, is what production is testing), or the ordinary
    word+furigana front for a ordinary recognition card. Used directly by
    webapp.py's browser flow; format_word_block below builds the
    HTML-escaped equivalent for the direct-Telegram-message flow."""
    entry = row_to_entry(row)
    if is_production_key(key):
        return entry["meaning"] or entry["word"]
    return row.get("word_furigana") or entry["word"]


def build_body_lines(row: dict, enrichment: dict | None = None, key: str | None = None) -> list:
    """Reading/meaning/particles/examples for one word, as a list of
    HTML-escaped lines (see kanji_drip.build_body_lines - same
    join-with-'\\n' convention, shared by format_word_block's
    <tg-spoiler> body and webapp.py's browser-rendered review page).
    `enrichment` is an optional llm_enrich.enrich_vocab() result - when
    present, its AI-generated practice block (examples, explanation,
    dialogue, practice question - see llm_enrich.format_blocks) is
    appended after the curated content; the curated content itself is
    never replaced. `key` - pass the SRS key being rendered so a
    production card's spoiler leads with the word+furigana itself (the
    one thing its front, unlike a recognition card's, doesn't already
    show - see card_front)."""
    entry = row_to_entry(row)
    lines = []
    if key is not None and is_production_key(key):
        lines.append(f"\U0001f210 <b>{_fx(row.get('word_furigana') or entry['word'])}</b>")
    lines.append(
        f"\U0001f524 <b>{html.escape(entry['reading'])}</b> · {html.escape(entry['meaning'])}"
    )
    if entry["nepali"]:
        lines.append(f"\U0001f1f3\U0001f1f5 {html.escape(entry['nepali'])}")
    if entry["particles"]:
        patterns = llm_enrich.vocab_patterns(entry["particles"])
        lines.append("")
        lines.append("\U0001f9e9 <b>Particles</b>")
        if len(patterns) > 1:
            # Numbered to match the AI examples below 1:1 (see
            # llm_enrich.enrich_vocab/_align_vocab_examples) - each
            # Example's pattern tag corresponds to the same-numbered line
            # here, so a learner can see at a glance which pattern each
            # example demonstrates.
            for i, p in enumerate(patterns, 1):
                lines.append(f"{i}. <code>{_fx(p)}</code>")
        else:
            lines.append(" ｜ ".join(f"<code>{_fx(p)}</code>" for p in patterns))
    if entry["examples"]:
        lines.append("")
        lines.append(llm_enrich.SECTION_RULE)
        lines.append("\U0001f4dd <b>Examples</b>")
        for i, example in enumerate(entry["examples"], 1):
            jp, _, en = example.partition(" — ")
            lines.append(f"{i}. {html.escape(jp)}")
            if en:
                lines.append(f"<i>{html.escape(en)}</i>")

    if enrichment:
        for block in llm_enrich.format_blocks(enrichment):
            lines.append("")
            lines.append(block)

    return lines


def format_word_block(row: dict, status: str, enrichment: dict | None = None, key: str | None = None) -> str:
    """One word's HTML (parse_mode=HTML) block within a batch message:
    the front (word+furigana, or just the meaning for a production card -
    see card_front) visible, everything else under its own <tg-spoiler>
    (each spoiler in a message reveals independently on tap). `status` is
    "new" / "due" / "reminder" - see kanji_drip.format_card. `enrichment`,
    `key` - see build_body_lines."""
    entry = row_to_entry(row)
    lines = build_body_lines(row, enrichment, key=key)
    marker = _STATUS_MARKERS[status]
    if key is not None and is_production_key(key):
        front = html.escape(entry["meaning"] or entry["word"])
    else:
        front = _fx(row.get("word_furigana") or entry["word"])
    return f"{marker} <b>{front}</b>\n<tg-spoiler>{chr(10).join(lines)}</tg-spoiler>"


def build_message(blocks: list) -> str:
    return "\n\n".join(blocks)


# Max stale cached practice blocks regenerated per run (see main()).
VOCAB_STALE_REGEN_PER_RUN = 3


def _unlock_production_cards(keys: list, state: dict, now) -> list:
    """Creates a fresh srs.py state entry (box 0, no "due" yet - same
    shape pick_next gives a brand-new item) for every word whose
    RECOGNITION card has reached VOCAB_PRODUCTION_MIN_BOX and doesn't
    already have a production card, capped at VOCAB_PRODUCTION_PER_DAY
    per UTC day. Mutates `state` in place; returns the list of newly
    created production keys so the caller can fold them into this run's
    picks (they're never picked via srs.pick_batch's own new-item path,
    which only ever considers `keys` themselves - see main()). A
    deliberately separate daily counter from VOCAB_NEW_PER_DAY: unlocking
    production for an already-known word is a maturity-gated second pass,
    not a brand-new introduction, so it must never eat into the budget
    for meeting words for the first time."""
    today = now.date().isoformat()
    unlocked_today = sum(
        1
        for key, rec in state.items()
        if is_production_key(key) and rec.get("introduced_at", "")[:10] == today
    )
    newly_unlocked = []
    for word in keys:
        if unlocked_today >= VOCAB_PRODUCTION_PER_DAY:
            break
        rec = state.get(word)
        if not rec or rec.get("box", 0) < VOCAB_PRODUCTION_MIN_BOX:
            continue
        pkey = production_key(word)
        if pkey in state:
            continue
        state[pkey] = {"box": 0, "reps": 0, "lapses": 0, "introduced_at": now.isoformat()}
        newly_unlocked.append(pkey)
        unlocked_today += 1
    return newly_unlocked


def main() -> None:
    missing = [
        name
        for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_ID")
        if not os.environ.get(name)
    ]
    if missing:
        raise SystemExit(f"Missing required env var(s): {', '.join(missing)}.")

    rows = load_rows(VOCAB_TSV_GLOB)
    if not rows:
        raise SystemExit(f"No vocab rows found matching {VOCAB_TSV_GLOB!r}.")

    row_by_key = {row["word"].strip(): row for row in rows}
    keys = list(row_by_key.keys())

    state = srs.load_state(VOCAB_SRS_PATH)
    now = srs.now_utc()

    # Unlock production cards for any word whose recognition card has
    # matured enough (own budget - see _unlock_production_cards) BEFORE
    # picking this run's batch, so a freshly unlocked one can go out
    # straight away instead of waiting for a future run to notice it.
    newly_unlocked = _unlock_production_cards(keys, state, now)

    # Already-unlocked production keys need to be in the pool pick_batch
    # scans for DUE reviews (it only ever treats a key as "new" when it's
    # not already in `state`, which every production key we pass here
    # already is - by construction, from a previous run's unlock - so
    # this can never double-introduce one or double-count it against
    # VOCAB_NEW_PER_DAY). `newly_unlocked` keys are handled separately
    # below, not through this pool, since they must count against
    # VOCAB_PRODUCTION_PER_DAY, never VOCAB_NEW_PER_DAY.
    existing_production_keys = [
        k for k in state if is_production_key(k) and k not in newly_unlocked
    ]
    picks = srs.pick_batch(
        keys + existing_production_keys, state, now, VOCAB_BATCH_SIZE, VOCAB_NEW_PER_DAY,
        unrated_resurface_hours=VOCAB_UNRATED_RESURFACE_HOURS,
    )
    picks += [(pkey, True) for pkey in newly_unlocked]

    if not picks:
        print(
            f"[vocab] nothing due and today's {VOCAB_NEW_PER_DAY}-new-word cap is "
            "reached - skipping this run."
        )
        return

    srs.save_state(VOCAB_SRS_PATH, state)  # pick_batch/_unlock_production_cards already recorded any new intros

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = str(os.environ["TELEGRAM_ALLOWED_CHAT_ID"])
    api_base = f"https://api.telegram.org/bot{token}"
    picked_keys = [key for key, _ in picks]
    # One entry per unique WORD touched this run (recognition and
    # production keys for the same word collapse to one), preserving
    # first-seen order - both card types share the same TSV row/
    # enrichment/sink entry, there's nothing production-specific to add.
    words_this_run = list(dict.fromkeys(base_word(key) for key in picked_keys))
    entries = [row_to_entry(row_by_key[word]) for word in words_this_run]

    # A practice block generated ONCE per word, the first time it's ever
    # picked (by either card type), then reused on every later review
    # from the persistent cache - see kanji_drip.py's identical comment /
    # llm_enrich.py. No-op (skipped/None) when GCP_PROJECT_ID isn't set or
    # the lifetime cost cap has been reached; either way the curated TSV
    # content below is unaffected.
    enrich_cache = llm_enrich.load_cache(VOCAB_ENRICH_CACHE_PATH)
    enrichments = {}
    regenerated = 0
    for word in words_this_run:
        cached = enrich_cache.get(word)
        stale = cached is not None and llm_enrich.vocab_entry_is_stale(row_by_key[word], cached)
        if cached is not None:
            enrichments[word] = cached  # also the fallback if a stale entry can't be refreshed
        # Refreshing a stale (pre full-particle-coverage) entry is a
        # backlog, not urgent: cap it per run so a batch of 10 old
        # entries can't fire 10 back-to-back Gemini calls and hit
        # Vertex's 429 rate limit (which would also starve brand-new
        # words of their first enrichment).
        if stale and regenerated >= VOCAB_STALE_REGEN_PER_RUN:
            continue
        if cached is not None and not stale:
            continue
        if stale:
            regenerated += 1
        result = llm_enrich.enrich_vocab(row_by_key[word], VOCAB_LEVEL)
        if result:
            enrichments[word] = result
            llm_enrich.save_cache_entry(VOCAB_ENRICH_CACHE_PATH, word, result)

    if WEBAPP_BASE_URL:
        # One short message with a single "Open in browser" button that
        # opens the whole batch as a swipeable flashcard deck (webapp.py)
        # instead of a header message plus one spoiler card per word
        # piling up in the chat.
        review_url = webapp.build_review_url(WEBAPP_BASE_URL, "v", picked_keys, token)
        header = f"\U0001f4d8 {VOCAB_LEVEL} vocabulary review ({len(picks)} card{'s' if len(picks) != 1 else ''})"
        send_review_link_card(api_base, chat_id, header, "\U0001f310 Open in browser", review_url)
        print(f"\n[vocab] sent {len(picks)} words via review link from {VOCAB_TSV_GLOB}: {', '.join(picked_keys)}")
    else:
        blocks = []
        statuses = []
        for key, is_new in picks:
            rec = state.get(key, {})
            status = "new" if is_new else ("due" if rec.get("due") else "reminder")
            word = base_word(key)
            row = row_by_key[word]
            blocks.append(format_word_block(row, status, enrichments.get(word), key=key))
            statuses.append(f"{key} ({status})")

        print(build_message(blocks))  # cron log only - not what gets sent
        send_telegram_reply(
            api_base, chat_id, f"\U0001f4d8 {VOCAB_LEVEL} Vocabulary ({len(picks)} words)"
        )
        for key, block in zip(picked_keys, blocks):
            send_srs_card(api_base, chat_id, "v", key, block)
        # Unlike the review-link flow (marked seen only when webapp.py's
        # POST /api/submit records an actual rating - see srs.mark_seen),
        # a card sent this way is already fully visible the moment it
        # lands in the chat, so it counts as seen right now - otherwise
        # an unrated word would never resurface as a reminder (see
        # srs.pick_next).
        now = srs.now_utc()
        if any([srs.mark_seen(state, key, now) for key in picked_keys]):
            srs.save_state(VOCAB_SRS_PATH, state)
        print(f"\n[vocab] sent {len(picks)} words from {VOCAB_TSV_GLOB}: {', '.join(statuses)}")

    # Optional extra sinks - after Telegram (the primary channel) and the
    # SRS state save, so a slow or failing API here never delays the
    # phone push or risks re-picking the same words next run. Each
    # returns a status string (never raises); print it for the cron log.
    now_dt = datetime.now(timezone.utc)
    if os.environ.get("NOTION_API_KEY") and os.environ.get("NOTION_VOCAB_DB_ID"):
        print(notion_add_row(entries, VOCAB_LEVEL))
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") and os.environ.get("VOCAB_GDOC_ID"):
        print(gdoc_append(entries, VOCAB_LEVEL, now_dt))


if __name__ == "__main__":
    main()
