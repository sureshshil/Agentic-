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
  VOCAB_BATCH_SIZE          optional, default 7 (words per push)
  VOCAB_NEW_PER_DAY         optional, default 16 (new words introduced/UTC day)
  VOCAB_UNRATED_RESURFACE_HOURS  optional, default 3 (see srs.pick_next -
                            a sent-but-never-tapped word comes back around
                            as a reminder instead of vanishing forever)
  VOCAB_TSV_GLOB            optional, default ../N3_vocab_batch*.tsv
  VOCAB_SRS_PATH            optional, default ../.vocab_srs.json

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

from telegram_bot import send_srs_card, send_telegram_reply  # noqa: E402 - needs sys.path insert above
from vocab_sinks import gdoc_append, notion_add_row  # noqa: E402 - needs sys.path insert above
import srs  # noqa: E402 - needs sys.path insert above

VOCAB_LEVEL = os.environ.get("VOCAB_LEVEL") or "N3"
VOCAB_BATCH_SIZE = int(os.environ.get("VOCAB_BATCH_SIZE") or "7")
VOCAB_NEW_PER_DAY = int(os.environ.get("VOCAB_NEW_PER_DAY") or "16")
VOCAB_UNRATED_RESURFACE_HOURS = float(os.environ.get("VOCAB_UNRATED_RESURFACE_HOURS") or "3")
VOCAB_TSV_GLOB = os.environ.get("VOCAB_TSV_GLOB") or os.path.join(
    _SIMPLE_AGENT_DIR, "N3_vocab_batch*.tsv"
)
VOCAB_SRS_PATH = os.environ.get("VOCAB_SRS_PATH") or os.path.join(
    _SIMPLE_AGENT_DIR, ".vocab_srs.json"
)

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


def format_word_block(row: dict, status: str) -> str:
    """One word's HTML (parse_mode=HTML) block within a batch message:
    the word itself visible, everything else under its own <tg-spoiler>
    (each spoiler in a message reveals independently on tap). `status` is
    "new" / "due" / "reminder" - see kanji_drip.format_card."""
    entry = row_to_entry(row)
    lines = [
        f"{html.escape(entry['reading'])} — {html.escape(entry['meaning'])}"
    ]
    if entry["nepali"]:
        lines.append(f"\U0001f1f3\U0001f1f5 {html.escape(entry['nepali'])}")
    if entry["particles"]:
        lines.append(f"助詞: {html.escape(entry['particles'])}")
    if entry["examples"]:
        lines.append("")
        lines.append("例文:")
        for i, example in enumerate(entry["examples"], 1):
            lines.append(f"{i}. {html.escape(example)}")

    marker = _STATUS_MARKERS[status]
    return f"{marker} <b>{html.escape(entry['word'])}</b>\n<tg-spoiler>{chr(10).join(lines)}</tg-spoiler>"


def build_message(blocks: list) -> str:
    return "\n\n".join(blocks)


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
    picks = srs.pick_batch(
        keys, state, now, VOCAB_BATCH_SIZE, VOCAB_NEW_PER_DAY,
        unrated_resurface_hours=VOCAB_UNRATED_RESURFACE_HOURS,
    )

    if not picks:
        print(
            f"[vocab] nothing due and today's {VOCAB_NEW_PER_DAY}-new-word cap is "
            "reached - skipping this run."
        )
        return

    srs.save_state(VOCAB_SRS_PATH, state)  # pick_batch already recorded any new intros

    blocks = []
    item_keys = []  # one send_srs_card call per word, same order as blocks
    entries = []  # for the optional Notion/Google Doc sinks, same shape as before
    statuses = []
    for key, is_new in picks:
        rec = state.get(key, {})
        status = "new" if is_new else ("due" if rec.get("due") else "reminder")
        row = row_by_key[key]
        blocks.append(format_word_block(row, status))
        item_keys.append(key)
        entries.append(row_to_entry(row))
        statuses.append(f"{key} ({status})")

    print(build_message(blocks))  # cron log only - not what gets sent

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = str(os.environ["TELEGRAM_ALLOWED_CHAT_ID"])
    api_base = f"https://api.telegram.org/bot{token}"

    send_telegram_reply(
        api_base, chat_id, f"\U0001f4d8 {VOCAB_LEVEL} Vocabulary ({len(picks)} words)"
    )
    for key, block in zip(item_keys, blocks):
        send_srs_card(api_base, chat_id, "v", key, block)

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
