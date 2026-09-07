"""JLPT-level Japanese vocabulary drip, delivered straight to your
Telegram chat every couple of hours via cron - same thin-entry-point
pattern as rain_alert_cron.py and news_digest_agent.py in this directory.

The word content comes from the hand-curated TSV files next to this repo
(../N3_vocab_batch*.tsv by default, override with VOCAB_TSV_GLOB) - NOT
from an LLM call. Each run picks the next VOCAB_COUNT words, formats them
for a phone-sized Telegram message, and pushes it via
telegram_bot.send_telegram_reply() using the Bot API directly.
Deliberately does NOT touch .telegram_bot_state.json or any session file
- these drips just show up as their own messages in the chat and never
mix into or interrupt whatever you're chatting about interactively.

The TSV column layout (Anki-style header lines starting with '#', then a
'#columns:' line naming the fields, then one tab-separated row per word):

  word  rank  word_furigana  reading  type  pos  english  nepali
  sentence1  sentence1_en  sentence2  sentence2_en  sentence3  sentence3_en
  frame  note  tags

`frame` is the 助詞 / particle-pattern line; `note` is unused here (the
Telegram format stays terse, same as before). The three sentences already
carry inline [furigana].

Keeps a small local history (../.vocab_sent_words.json, gitignored like
the other *.json state files) of headwords already sent. Never-sent words
go out first, in rank order; once every word has been sent at least once
the least-recently-sent words come back around for review. The history is
capped at HISTORY_LIMIT entries (oldest dropped first).

See ../deploy/cron-notifications.md for VPS cron setup.

Env vars (loaded via python-dotenv from ../telegram_bot.env and
../deploy/scheduled.env, same fill-in-what's-unset behavior as
telegram_bot.py - real environment variables always win):
  TELEGRAM_BOT_TOKEN        required, from ../telegram_bot.env
  TELEGRAM_ALLOWED_CHAT_ID  required, from ../telegram_bot.env
  VOCAB_LEVEL               optional, default "N3" (JLPT level, label only)
  VOCAB_COUNT              optional, default 4 (words per drop)
  VOCAB_TSV_GLOB            optional, default ../N3_vocab_batch*.tsv

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
import json
import os
import re
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

# telegram_bot.py lives one directory up from this scheduled/ script;
# vocab_sinks.py sits right next to it in scheduled/.
_SCHEDULED_DIR = os.path.dirname(os.path.abspath(__file__))
_SIMPLE_AGENT_DIR = os.path.dirname(_SCHEDULED_DIR)
sys.path.insert(0, _SIMPLE_AGENT_DIR)
sys.path.insert(0, _SCHEDULED_DIR)

load_dotenv(os.path.join(_SIMPLE_AGENT_DIR, "telegram_bot.env"))
load_dotenv(os.path.join(_SIMPLE_AGENT_DIR, "deploy", "scheduled.env"))

from telegram_bot import send_telegram_reply  # noqa: E402 - needs sys.path insert above
from vocab_sinks import gdoc_append, notion_add_row  # noqa: E402 - needs sys.path insert above

VOCAB_LEVEL = os.environ.get("VOCAB_LEVEL") or "N3"
VOCAB_COUNT = int(os.environ.get("VOCAB_COUNT") or "4")
VOCAB_TSV_GLOB = os.environ.get("VOCAB_TSV_GLOB") or os.path.join(
    _SIMPLE_AGENT_DIR, "N3_vocab_batch*.tsv"
)

HISTORY_PATH = os.path.join(_SIMPLE_AGENT_DIR, ".vocab_sent_words.json")
HISTORY_LIMIT = 300

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


def _despace_japanese(text: str) -> str:
    return _JP_WORD_SPACE_RE.sub("", text).strip()


def _load_history() -> list:
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_history(words: list) -> None:
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(words[-HISTORY_LIMIT:], f, ensure_ascii=False, indent=2)


def load_rows(glob_pattern: str) -> list:
    """Every data row across the TSV files matching `glob_pattern`, as a
    list of dicts keyed by the '#columns:' header. Files are read in
    sorted-name order; a word seen in an earlier file wins (batch1 covers
    the lower ranks), so duplicates across files are dropped."""
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


def select_words(rows: list, history: list, count: int) -> list:
    """The next `count` rows to send. Never-sent words come first, ordered
    by rank; then, once everything has gone out at least once, the
    least-recently-sent words (earliest position in `history`)."""
    last_pos = {word: i for i, word in enumerate(history)}

    def sort_key(row):
        word = row["word"].strip()
        try:
            rank = int(row.get("rank") or "0")
        except ValueError:
            rank = 0
        if word in last_pos:
            return (1, last_pos[word], rank)
        return (0, rank, rank)

    return sorted(rows, key=sort_key)[:count]


def row_to_entry(row: dict) -> dict:
    """One TSV row -> the structured dict the Notion / Google Doc sinks
    expect (word / reading / meaning / type / particles / examples), plus
    a couple of extra keys the sinks ignore for now (nepali / note)."""
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


def format_word(entry: dict) -> str:
    """One word's block for the Telegram message - same terse shape the
    old LLM drip produced, with the Nepali gloss added as its own line."""
    lines = [f"{entry['word']} ({entry['reading']}) — {entry['meaning']}"]
    if entry.get("nepali"):
        lines.append(f"\U0001f1f3\U0001f1f5 {entry['nepali']}")
    if entry.get("particles"):
        lines.append(f"助詞: {entry['particles']}")
    if entry.get("examples"):
        lines.append("")
        lines.append("例文:")
        for i, example in enumerate(entry["examples"], 1):
            lines.append(f"{i}. {example}")
    return "\n".join(lines)


def build_message(entries: list) -> str:
    return "\n\n----\n\n".join(format_word(e) for e in entries)


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

    history = _load_history()
    picked = select_words(rows, history, VOCAB_COUNT)
    entries = [row_to_entry(row) for row in picked]
    new_words = [e["word"] for e in entries if e["word"]]

    message = build_message(entries)
    print(message)

    unseen_before = sum(1 for r in rows if r["word"].strip() not in set(history))
    print(
        f"\n[vocab] sent {len(new_words)} words from {VOCAB_TSV_GLOB} "
        f"({len(rows)} total, {unseen_before} not yet sent this cycle before this run)"
    )

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = str(os.environ["TELEGRAM_ALLOWED_CHAT_ID"])
    api_base = f"https://api.telegram.org/bot{token}"
    send_telegram_reply(api_base, chat_id, f"\U0001f4d8 {VOCAB_LEVEL} Vocabulary\n\n{message}")

    if new_words:
        _save_history(history + new_words)

    # Optional extra sinks - after Telegram (the primary channel) and the
    # local history write, so a slow or failing API here never delays the
    # phone push or risks repeating words next run. Each returns a status
    # string (never raises); print it for the cron log.
    now = datetime.now(timezone.utc)
    if os.environ.get("NOTION_API_KEY") and os.environ.get("NOTION_VOCAB_DB_ID"):
        print(notion_add_row(entries, VOCAB_LEVEL))
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") and os.environ.get("VOCAB_GDOC_ID"):
        print(gdoc_append(entries, VOCAB_LEVEL, now))


if __name__ == "__main__":
    main()
