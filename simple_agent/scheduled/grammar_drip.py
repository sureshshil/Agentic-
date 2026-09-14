"""Every-2-hours single-grammar-pattern active-recall drip via Telegram,
spaced-repetition version - sibling to kanji_drip.py (same srs.py engine,
same telegram_bot.py callback handler), just on a slower cadence and a
different deck. See kanji_drip.py's docstring for the full rationale
(active recall behind a spoiler tag, Leitner review intervals, due
reviews before new introductions, a daily cap on new items).

Grammar content comes from the hand-curated CSVs next to this repo
(../n3_grammar_batch*.csv by default, override with GRAMMAR_CSV_GLOB) -
NOT from an LLM call. Button taps are handled entirely by
telegram_bot.py's long-polling loop; this script only ever sends. State
lives in .grammar_srs.json (gitignored).

Env vars (loaded via python-dotenv from ../telegram_bot.env and
../deploy/scheduled.env, real environment variables always win):
  TELEGRAM_BOT_TOKEN         required, from ../telegram_bot.env
  TELEGRAM_ALLOWED_CHAT_ID   required, from ../telegram_bot.env
  GRAMMAR_LEVEL              optional, default "N3" (label only)
  GRAMMAR_NEW_PER_DAY        optional, default 4 (new patterns/UTC day)
  GRAMMAR_UNRATED_RESURFACE_HOURS  optional, default 3 (see srs.pick_next -
                             a sent-but-never-rated pattern comes back
                             around as a "reminder" instead of vanishing)
  GRAMMAR_CSV_GLOB           optional, default ../n3_grammar_batch*.csv
  GRAMMAR_SRS_PATH           optional, default ../.grammar_srs.json

Run this only during hours you're actually reachable - crontab is the
place for that (see ../deploy/cron-notifications.md), not this script.
"""

import csv
import glob
import html
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

_SCHEDULED_DIR = os.path.dirname(os.path.abspath(__file__))
_SIMPLE_AGENT_DIR = os.path.dirname(_SCHEDULED_DIR)
sys.path.insert(0, _SIMPLE_AGENT_DIR)
sys.path.insert(0, _SCHEDULED_DIR)

load_dotenv(os.path.join(_SIMPLE_AGENT_DIR, "telegram_bot.env"))
load_dotenv(os.path.join(_SIMPLE_AGENT_DIR, "deploy", "scheduled.env"))

from telegram_bot import send_srs_card  # noqa: E402 - needs sys.path insert above
from grammar_sinks import gdoc_append, notion_add_row  # noqa: E402 - needs sys.path insert above
import srs  # noqa: E402 - needs sys.path insert above

GRAMMAR_LEVEL = os.environ.get("GRAMMAR_LEVEL") or "N3"
GRAMMAR_NEW_PER_DAY = int(os.environ.get("GRAMMAR_NEW_PER_DAY") or "4")
GRAMMAR_UNRATED_RESURFACE_HOURS = float(os.environ.get("GRAMMAR_UNRATED_RESURFACE_HOURS") or "3")
GRAMMAR_CSV_GLOB = os.environ.get("GRAMMAR_CSV_GLOB") or os.path.join(
    _SIMPLE_AGENT_DIR, "n3_grammar_batch*.csv"
)
GRAMMAR_SRS_PATH = os.environ.get("GRAMMAR_SRS_PATH") or os.path.join(
    _SIMPLE_AGENT_DIR, ".grammar_srs.json"
)


def load_rows(glob_pattern: str) -> list:
    """Every data row across the CSVs matching `glob_pattern`, as a list
    of dicts, in rank order (see kanji_drip.load_rows - same dedup rule,
    keyed by the 'grammar' column instead of 'kanji')."""
    rows: list = []
    seen: set = set()
    for path in sorted(glob.glob(glob_pattern)):
        with open(path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                pattern = (row.get("grammar") or "").strip()
                if not pattern or pattern in seen:
                    continue
                seen.add(pattern)
                rows.append(row)
    return rows


def row_to_entry(row: dict) -> dict:
    """One CSV row -> the structured dict the Notion / Google Doc sinks
    expect (pattern / formation / meaning / nuance / contrast /
    examples)."""
    examples = []
    for jp_col, en_col in (("ex1", "ex1_en"), ("ex2", "ex2_en"), ("ex3", "ex3_en")):
        japanese = (row.get(jp_col) or "").strip()
        english = (row.get(en_col) or "").strip()
        if not japanese:
            continue
        examples.append(f"{japanese} — {english}" if english else japanese)
    return {
        "pattern": (row.get("grammar") or "").strip(),
        "formation": (row.get("formation") or "").strip(),
        "meaning": (row.get("meaning_en") or "").strip(),
        "meaning_ne": (row.get("meaning_ne") or "").strip(),
        "nuance": (row.get("nuance") or "").strip(),
        "contrast": (row.get("contrast") or "").strip(),
        "examples": examples,
    }


_STATUS_LABELS = {
    "new": "\U0001f210 New {level} grammar",
    "due": "\U0001f501 Review ({level}, box {box}/{top})",
    "reminder": "⏰ Still waiting on your rating ({level})",
}


def format_card(row: dict, status: str, box: int, level: str) -> str:
    """The HTML (parse_mode=HTML) message text for one grammar pattern:
    the pattern itself visible, everything else under a spoiler. `status`
    is "new" / "due" / "reminder" - see kanji_drip.format_card."""
    pattern = (row.get("grammar") or "").strip()
    header = _STATUS_LABELS[status].format(
        level=html.escape(level), box=box + 1, top=len(srs.BOX_HOURS_GRAMMAR)
    )

    body = []
    if (row.get("formation") or "").strip():
        body.append(f"形: {html.escape(row['formation'].strip())}")

    body.append(html.escape((row.get("meaning_en") or "").strip()))
    if (row.get("meaning_ne") or "").strip():
        body.append(f"\U0001f1f3\U0001f1f5 {html.escape(row['meaning_ne'].strip())}")

    if (row.get("nuance") or "").strip():
        body.append(f"ニュアンス: {html.escape(row['nuance'].strip())}")
    if (row.get("contrast") or "").strip():
        body.append(f"対比: {html.escape(row['contrast'].strip())}")

    ex_jp = (row.get("ex1") or "").strip()
    if ex_jp:
        body.append("")
        example_lines = [f"例文: {html.escape(ex_jp)}"]
        ex_reading = (row.get("ex1_reading") or "").strip()
        if ex_reading:
            example_lines.append(html.escape(ex_reading))
        ex_en = (row.get("ex1_en") or "").strip()
        if ex_en:
            example_lines.append(f"— {html.escape(ex_en)}")
        body.append("\n".join(example_lines))

    return f"{header}\n\n<b>{html.escape(pattern)}</b>\n\n<tg-spoiler>{chr(10).join(body)}</tg-spoiler>"


def main() -> None:
    missing = [
        name
        for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_ID")
        if not os.environ.get(name)
    ]
    if missing:
        raise SystemExit(f"Missing required env var(s): {', '.join(missing)}.")

    rows = load_rows(GRAMMAR_CSV_GLOB)
    if not rows:
        raise SystemExit(f"No grammar rows found matching {GRAMMAR_CSV_GLOB!r}.")

    row_by_key = {row["grammar"].strip(): row for row in rows}
    keys = list(row_by_key.keys())

    state = srs.load_state(GRAMMAR_SRS_PATH)
    now = srs.now_utc()
    key, is_new = srs.pick_next(
        keys, state, now, GRAMMAR_NEW_PER_DAY, unrated_resurface_hours=GRAMMAR_UNRATED_RESURFACE_HOURS
    )

    if key is None:
        print(
            f"[grammar] nothing due and today's {GRAMMAR_NEW_PER_DAY}-new-pattern cap "
            "is reached - skipping this run."
        )
        return

    if is_new:
        srs.save_state(GRAMMAR_SRS_PATH, state)  # pick_next already recorded the intro

    rec = state.get(key, {})
    box = rec.get("box", 0)
    status = "new" if is_new else ("due" if rec.get("due") else "reminder")
    row = row_by_key[key]
    html_text = format_card(row, status, box, GRAMMAR_LEVEL)
    print(html_text)

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = str(os.environ["TELEGRAM_ALLOWED_CHAT_ID"])
    api_base = f"https://api.telegram.org/bot{token}"
    send_srs_card(api_base, chat_id, "g", key, html_text)

    print(f"\n[grammar] sent {key!r} ({status}) from {GRAMMAR_CSV_GLOB} ({len(rows)} total)")

    # Optional extra sinks - after Telegram (the primary channel) and the
    # SRS state save, so a slow or failing API here never delays the
    # phone push or risks re-picking the same pattern next run. Each
    # returns a status string (never raises); print it for the cron log.
    now_dt = datetime.now(timezone.utc)
    entries = [row_to_entry(row)]
    if os.environ.get("NOTION_API_KEY") and os.environ.get("NOTION_GRAMMAR_DB_ID"):
        print(notion_add_row(entries, GRAMMAR_LEVEL))
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") and os.environ.get("GRAMMAR_GDOC_ID"):
        print(gdoc_append(entries, GRAMMAR_LEVEL, now_dt))


if __name__ == "__main__":
    main()
