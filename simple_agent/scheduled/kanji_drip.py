"""Hourly single-kanji active-recall drip via Telegram, spaced-repetition
version - same thin-entry-point cron pattern as vocab_drip.py next to this
file, but a different learning shape on purpose:

vocab_drip pushes a batch of words every couple hours and just cycles
through them (never-sent first, then least-recently-sent) - fine for a
passive read. This is meant to be a habit: ONE kanji an hour, character
shown plain, everything else (reading, meaning, mnemonic, compounds,
example) hidden behind a Telegram spoiler tag so you have to actually try
to recall it before revealing, then rate your own recall with an inline
Again / Hard / Good / Easy keyboard. That rating drives a Leitner-style
per-kanji review interval (srs.py, shared with grammar_drip.py) - reviews
that are actually due always go out before any brand-new kanji, and only
up to KANJI_NEW_PER_DAY new kanji get introduced per UTC day so the due
queue can't outrun what one-an-hour can actually clear.

The button taps are handled by telegram_bot.py's own long-polling loop
(handle_srs_callback), not by this script - this script only ever sends;
it never listens. State lives in .kanji_srs.json (gitignored, shared with
that handler).

Kanji content comes from the hand-curated CSVs next to this repo
(../N3_kanji_batch*.csv by default, override with KANJI_CSV_GLOB) - NOT
from an LLM call.

Env vars (loaded via python-dotenv from ../telegram_bot.env and
../deploy/scheduled.env, same fill-in-what's-unset behavior as
telegram_bot.py - real environment variables always win):
  TELEGRAM_BOT_TOKEN        required, from ../telegram_bot.env
  TELEGRAM_ALLOWED_CHAT_ID  required, from ../telegram_bot.env
  KANJI_LEVEL               optional, default "N3" (label only)
  KANJI_NEW_PER_DAY         optional, default 6 (new kanji introduced/UTC day)
  KANJI_UNRATED_RESURFACE_HOURS  optional, default 3 (see srs.pick_next -
                            a sent-but-never-rated kanji comes back around
                            as a "reminder" instead of vanishing forever)
  KANJI_CSV_GLOB            optional, default ../N3_kanji_batch*.csv
  KANJI_SRS_PATH            optional, default ../.kanji_srs.json

Run this only during hours you're actually reachable - crontab is the
place for that (see ../deploy/cron-notifications.md), not this script;
an hourly cron restricted to your waking hours is simplest.
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

from telegram_bot import send_photo, send_srs_card  # noqa: E402 - needs sys.path insert above
from kanji_sinks import gdoc_append, notion_add_row  # noqa: E402 - needs sys.path insert above
import kanji_image  # noqa: E402 - needs sys.path insert above
import srs  # noqa: E402 - needs sys.path insert above

KANJI_LEVEL = os.environ.get("KANJI_LEVEL") or "N3"
KANJI_NEW_PER_DAY = int(os.environ.get("KANJI_NEW_PER_DAY") or "6")
KANJI_UNRATED_RESURFACE_HOURS = float(os.environ.get("KANJI_UNRATED_RESURFACE_HOURS") or "3")
KANJI_CSV_GLOB = os.environ.get("KANJI_CSV_GLOB") or os.path.join(
    _SIMPLE_AGENT_DIR, "N3_kanji_batch*.csv"
)
KANJI_SRS_PATH = os.environ.get("KANJI_SRS_PATH") or os.path.join(
    _SIMPLE_AGENT_DIR, ".kanji_srs.json"
)


def load_rows(glob_pattern: str) -> list:
    """Every data row across the CSVs matching `glob_pattern`, as a list
    of dicts. Files are read in sorted-name order; a kanji seen in an
    earlier file wins (batch1 covers the lower ranks), so duplicates
    across files are dropped. Row order within/across files IS rank
    order - the CSVs are named/numbered sequentially."""
    rows: list = []
    seen: set = set()
    for path in sorted(glob.glob(glob_pattern)):
        with open(path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                kanji = (row.get("kanji") or "").strip()
                if not kanji or kanji in seen:
                    continue
                seen.add(kanji)
                rows.append(row)
    return rows


def _split_words(words_field: str) -> list:
    """The 'words' column is '<br>'-joined compounds, each
    'compound — meaning_en — meaning_ne'. Drop the per-compound Nepali
    (the headword's own Nepali line already covers that) and keep just
    'compound(meaning_en)'."""
    entries = []
    for chunk in (words_field or "").split("<br>"):
        parts = [p.strip() for p in chunk.split(" — ")]
        if not parts or not parts[0]:
            continue
        if len(parts) >= 2 and parts[1]:
            entries.append(f"{parts[0]}({parts[1]})")
        else:
            entries.append(parts[0])
    return entries


def row_to_entry(row: dict) -> dict:
    """One CSV row -> the structured dict the Notion / Google Doc sinks
    expect (kanji / reading / meaning / component / confusable / words /
    example / example_en)."""
    readings = " / ".join(
        r.strip() for r in (row.get("onyomi", ""), row.get("kunyomi", "")) if r.strip()
    )
    return {
        "kanji": (row.get("kanji") or "").strip(),
        "reading": readings,
        "meaning": (row.get("meaning_en") or "").strip(),
        "meaning_ne": (row.get("meaning_ne") or "").strip(),
        "component": (row.get("component") or "").strip(),
        "confusable": (row.get("confusable") or "").strip(),
        "words": _split_words(row.get("words", "")),
        "example": (row.get("example") or "").strip(),
        "example_en": (row.get("example_en") or "").strip(),
    }


_STATUS_LABELS = {
    "new": "\U0001f210 New {level} kanji",
    "due": "\U0001f501 Review ({level}, box {box}/{top})",
    "reminder": "⏰ Still waiting on your rating ({level})",
}


def format_card(row: dict, status: str, box: int, level: str) -> str:
    """The HTML (parse_mode=HTML) message text sent under the rendered
    kanji image (see kanji_image.render_kanji_png / main()): a status
    header, then everything else under a spoiler. `status` is "new"
    (never sent before), "due" (a rated item's review interval elapsed),
    or "reminder" (sent before but never rated, resurfaced after srs.py's
    unrated-resurface timeout - see main())."""
    header = _STATUS_LABELS[status].format(
        level=html.escape(level), box=box + 1, top=len(srs.BOX_HOURS_KANJI)
    )

    readings = " / ".join(
        r.strip() for r in (row.get("onyomi", ""), row.get("kunyomi", "")) if r.strip()
    )
    body = [f"{html.escape(readings)} — {html.escape((row.get('meaning_en') or '').strip())}"]

    if (row.get("meaning_ne") or "").strip():
        body.append(f"\U0001f1f3\U0001f1f5 {html.escape(row['meaning_ne'].strip())}")

    if (row.get("component") or "").strip():
        body.append(f"構成: {html.escape(row['component'].strip())}")
    if (row.get("confusable") or "").strip():
        body.append(f"似ている: {html.escape(row['confusable'].strip())}")

    words = _split_words(row.get("words", ""))
    if words:
        body.append("")
        body.append(f"単語: {html.escape(' / '.join(words))}")

    example = (row.get("example") or "").strip()
    if example:
        body.append("")
        line = f"例文: {html.escape(example)}"
        example_en = (row.get("example_en") or "").strip()
        if example_en:
            line += f"\n— {html.escape(example_en)}"
        body.append(line)

    return f"{header}\n\n<tg-spoiler>{chr(10).join(body)}</tg-spoiler>"


def main() -> None:
    missing = [
        name
        for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_ID")
        if not os.environ.get(name)
    ]
    if missing:
        raise SystemExit(f"Missing required env var(s): {', '.join(missing)}.")

    rows = load_rows(KANJI_CSV_GLOB)
    if not rows:
        raise SystemExit(f"No kanji rows found matching {KANJI_CSV_GLOB!r}.")

    row_by_key = {row["kanji"].strip(): row for row in rows}
    keys = list(row_by_key.keys())

    state = srs.load_state(KANJI_SRS_PATH)
    now = srs.now_utc()
    key, is_new = srs.pick_next(
        keys, state, now, KANJI_NEW_PER_DAY, unrated_resurface_hours=KANJI_UNRATED_RESURFACE_HOURS
    )

    if key is None:
        print(
            f"[kanji] nothing due and today's {KANJI_NEW_PER_DAY}-new-kanji cap is "
            "reached - skipping this run."
        )
        return

    if is_new:
        srs.save_state(KANJI_SRS_PATH, state)  # pick_next already recorded the intro

    rec = state.get(key, {})
    box = rec.get("box", 0)
    status = "new" if is_new else ("due" if rec.get("due") else "reminder")
    row = row_by_key[key]
    html_text = format_card(row, status, box, KANJI_LEVEL)
    print(html_text)

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = str(os.environ["TELEGRAM_ALLOWED_CHAT_ID"])
    api_base = f"https://api.telegram.org/bot{token}"

    # The kanji itself goes out as a large rendered glyph, not plain Unicode
    # text - Telegram clients render bare CJK text small/thin, which made the
    # character genuinely hard to read at a glance. Sent as its own message
    # (no caption/keyboard) right before the spoiler card that carries those.
    send_photo(api_base, chat_id, kanji_image.render_kanji_png(key), filename="kanji.png")
    send_srs_card(api_base, chat_id, "k", key, html_text)

    print(f"\n[kanji] sent {key} ({status}) from {KANJI_CSV_GLOB} ({len(rows)} total)")

    # Optional extra sinks - after Telegram (the primary channel) and the
    # SRS state save, so a slow or failing API here never delays the
    # phone push or risks re-picking the same kanji next run. Each returns
    # a status string (never raises); print it for the cron log.
    now_dt = datetime.now(timezone.utc)
    entries = [row_to_entry(row)]
    if os.environ.get("NOTION_API_KEY") and os.environ.get("NOTION_KANJI_DB_ID"):
        print(notion_add_row(entries, KANJI_LEVEL))
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") and os.environ.get("KANJI_GDOC_ID"):
        print(gdoc_append(entries, KANJI_LEVEL, now_dt))


if __name__ == "__main__":
    main()
