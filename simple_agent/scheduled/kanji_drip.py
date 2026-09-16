"""Kanji active-recall drip via Telegram, spaced-repetition version - same
thin-entry-point cron pattern as vocab_drip.py next to this file. Cadence
is entirely up to crontab, not this script (see
../deploy/cron-notifications.md - currently 3x/day in this deployment,
deliberately sparse now that each push carries a full LLM practice block
rather than a single line). Each run picks up to KANJI_BATCH_SIZE kanji
(srs.pick_batch, default
3) - one at a time, character shown plain, everything else (reading,
meaning, mnemonic, compounds, example) hidden until you actively try to
recall it, then rate your own recall with Again / Hard / Good / Easy. That
rating drives a Leitner-style per-kanji review interval (srs.py, shared
with grammar_drip.py) - reviews that are actually due always go out
before any brand-new kanji, and only up to KANJI_NEW_PER_DAY new kanji
get introduced per UTC day so the due queue can't outrun what one-an-hour
can actually clear.

When WEBAPP_BASE_URL is set (see webapp.py / deploy/cron-
notifications.md), the whole batch goes out as ONE short message with a
"Review" button that opens a swipeable flashcard deck inside Telegram
itself. Unset, it falls back to the old flow: each kanji as its own
image + <tg-spoiler> card + button row.

The button taps are handled by telegram_bot.py's own long-polling loop
(handle_srs_callback), not by this script - this script only ever sends;
it never listens. State lives in .kanji_srs.json (gitignored, shared with
that handler).

Kanji content comes from the hand-curated CSVs next to this repo
(../N3_kanji_batch*.csv by default, override with KANJI_CSV_GLOB), not
from an LLM call - this includes each kanji's own mnemonic (`component`'s
"X + Y (story → meaning)" breakdown) and, for many confusable-kanji
rows, a `disc_note` distinguishing tip, both shown directly. Optionally,
ON TOP of that curated content, a full AI practice block (2-3 fresh
example sentences, a short explanation, a mini dialogue, and a
multiple-choice practice question) gets generated per push via Gemini
(see ../llm_enrich.py) and shown as extra "AI" sections - deliberately no
AI-generated mnemonic/component-breakdown, since the CSV's own is better
than anything an LLM would reinvent here. This is additive and fails
soft, so a model outage never removes the curated card. Cached in
KANJI_ENRICH_CACHE_PATH so webapp.py's Mini App page shows the exact same
generated content as whatever went out with the push, not a re-roll.

Env vars (loaded via python-dotenv from ../telegram_bot.env and
../deploy/scheduled.env, same fill-in-what's-unset behavior as
telegram_bot.py - real environment variables always win):
  TELEGRAM_BOT_TOKEN        required, from ../telegram_bot.env
  TELEGRAM_ALLOWED_CHAT_ID  required, from ../telegram_bot.env
  KANJI_LEVEL               optional, default "N3" (label only)
  KANJI_BATCH_SIZE          optional, default 3 (kanji per push - see srs.pick_batch;
                            only matters when WEBAPP_BASE_URL is set, since the old
                            per-item card flow below always sent exactly one)
  KANJI_NEW_PER_DAY         optional, default 6 (new kanji introduced/UTC day)
  KANJI_UNRATED_RESURFACE_HOURS  optional, default 3 (see srs.pick_next -
                            an opened-but-never-rated kanji comes back
                            around as a "reminder" this many hours after
                            you actually opened it (srs.mark_seen, set by
                            webapp.py's GET /review), instead of
                            vanishing forever. One you've never even
                            opened does NOT resurface on a timer - it
                            just waits until you look at it once)
  KANJI_CSV_GLOB            optional, default ../N3_kanji_batch*.csv
  KANJI_SRS_PATH            optional, default ../.kanji_srs.json
  KANJI_ENRICH_CACHE_PATH   optional, default ../.kanji_enrich_cache.json
  GCP_PROJECT_ID            optional - enables the per-push LLM example
                            sentence (see ../llm_enrich.py); unset means
                            the drip behaves exactly as before.
  DRIP_ENRICH_MAX_COST_USD  optional, default 1.00 - lifetime cap shared
                            with grammar_drip.py's enrichment - see
                            ../llm_enrich.py.

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

from telegram_bot import send_photo, send_srs_card, send_web_app_card  # noqa: E402 - needs sys.path insert above
from kanji_sinks import gdoc_append, notion_add_row  # noqa: E402 - needs sys.path insert above
import kanji_image  # noqa: E402 - needs sys.path insert above
import llm_enrich  # noqa: E402 - needs sys.path insert above
import srs  # noqa: E402 - needs sys.path insert above
import webapp  # noqa: E402 - needs sys.path insert above

KANJI_LEVEL = os.environ.get("KANJI_LEVEL") or "N3"
KANJI_BATCH_SIZE = int(os.environ.get("KANJI_BATCH_SIZE") or "3")
KANJI_NEW_PER_DAY = int(os.environ.get("KANJI_NEW_PER_DAY") or "6")
KANJI_UNRATED_RESURFACE_HOURS = float(os.environ.get("KANJI_UNRATED_RESURFACE_HOURS") or "3")
KANJI_CSV_GLOB = os.environ.get("KANJI_CSV_GLOB") or os.path.join(
    _SIMPLE_AGENT_DIR, "N3_kanji_batch*.csv"
)
KANJI_SRS_PATH = os.environ.get("KANJI_SRS_PATH") or os.path.join(
    _SIMPLE_AGENT_DIR, ".kanji_srs.json"
)
KANJI_ENRICH_CACHE_PATH = os.environ.get("KANJI_ENRICH_CACHE_PATH") or os.path.join(
    _SIMPLE_AGENT_DIR, ".kanji_enrich_cache.json"
)
# Set once Caddy + webapp.py's Mini App server are live (see
# deploy/cron-notifications.md) - switches main() from the old
# image+spoiler+4-button card to a single compact message with a "Review"
# button that opens the card in a Telegram Mini App instead.
WEBAPP_BASE_URL = (os.environ.get("WEBAPP_BASE_URL") or "").rstrip("/")


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


def find_row(key: str) -> dict:
    """The CSV row for one kanji character, or None - used by webapp.py to
    look up a card's content from the `key` query param on its Mini App
    review link (see main()'s WEBAPP_BASE_URL branch)."""
    for row in load_rows(KANJI_CSV_GLOB):
        if (row.get("kanji") or "").strip() == key:
            return row
    return None


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


def build_body_lines(row: dict, enrichment: dict | None = None) -> list:
    """Reading/meaning/component/confusable/disc_note/words/example for
    one kanji, as a list of HTML-escaped lines (join with '\\n' for a
    Telegram <tg-spoiler> body - format_card does this - or for a
    browser, render inside a `white-space: pre-line` element instead -
    webapp.py's review page does this, since Telegram's HTML parse_mode
    has no <br> tag to convert to). `enrichment` is an optional
    llm_enrich.enrich_kanji() result - when present, its AI-generated
    practice block (examples, explanation, dialogue, practice question -
    see llm_enrich.format_blocks) is appended after the curated content;
    the curated content itself is never replaced. There's deliberately no
    AI-generated mnemonic/component-breakdown - `component` and
    `disc_note` below are the curated ones for that (see
    llm_enrich.enrich_kanji's docstring)."""
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
    if (row.get("disc_note") or "").strip():
        body.append(f"\U0001f4a1 ヒント: {html.escape(row['disc_note'].strip())}")

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

    if enrichment:
        for block in llm_enrich.format_blocks(enrichment):
            body.append("")
            body.append(block)

    return body


def format_card(row: dict, status: str, box: int, level: str, enrichment: dict | None = None) -> str:
    """The HTML (parse_mode=HTML) message text sent under the rendered
    kanji image (see kanji_image.render_kanji_png / main()): a status
    header, then everything else under a spoiler. `status` is "new"
    (never sent before), "due" (a rated item's review interval elapsed),
    or "reminder" (sent before but never rated, resurfaced after srs.py's
    unrated-resurface timeout - see main()). `enrichment` - see
    build_body_lines."""
    header = _STATUS_LABELS[status].format(
        level=html.escape(level), box=box + 1, top=len(srs.BOX_HOURS_KANJI)
    )
    body = build_body_lines(row, enrichment)
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
    picks = srs.pick_batch(
        keys, state, now, KANJI_BATCH_SIZE, KANJI_NEW_PER_DAY,
        unrated_resurface_hours=KANJI_UNRATED_RESURFACE_HOURS,
    )

    if not picks:
        print(
            f"[kanji] nothing due and today's {KANJI_NEW_PER_DAY}-new-kanji cap is "
            "reached - skipping this run."
        )
        return

    srs.save_state(KANJI_SRS_PATH, state)  # pick_batch already recorded any new intros

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = str(os.environ["TELEGRAM_ALLOWED_CHAT_ID"])
    api_base = f"https://api.telegram.org/bot{token}"
    picked_keys = [key for key, _ in picks]

    # One fresh AI example sentence per picked kanji, generated now (this
    # push) and cached so the Mini App page (webapp.py, opened later)
    # shows the exact same content rather than re-rolling it - see
    # llm_enrich.py. No-op (skipped/None) when GCP_PROJECT_ID isn't set
    # or the lifetime cost cap has been reached; either way the CSV
    # content below is unaffected.
    enrichments = {}
    for key in picked_keys:
        result = llm_enrich.enrich_kanji(row_by_key[key], KANJI_LEVEL)
        if result:
            enrichments[key] = result
            llm_enrich.save_cache_entry(KANJI_ENRICH_CACHE_PATH, key, result)

    if WEBAPP_BASE_URL:
        # One short message with a single "Review" button that opens the
        # whole batch as a swipeable flashcard deck inside Telegram (a
        # Mini App - webapp.py) instead of a separate image + spoiler
        # card + button row per kanji piling up in the chat.
        review_url = webapp.build_review_url(WEBAPP_BASE_URL, "k", picked_keys, token)
        header = f"\U0001f210 {KANJI_LEVEL} kanji review ({len(picks)} card{'s' if len(picks) != 1 else ''})"
        send_web_app_card(
            api_base, chat_id, header, "\U0001f4d6 Review", review_url,
            browser_button_text="\U0001f310 Open in browser",
        )
        print(f"\n[kanji] sent {len(picks)} kanji via Mini App link from {KANJI_CSV_GLOB}: {', '.join(picked_keys)}")
    else:
        for key, is_new in picks:
            rec = state.get(key, {})
            box = rec.get("box", 0)
            status = "new" if is_new else ("due" if rec.get("due") else "reminder")
            row = row_by_key[key]
            html_text = format_card(row, status, box, KANJI_LEVEL, enrichments.get(key))
            print(html_text)
            # The kanji itself goes out as a large rendered glyph, not
            # plain Unicode text - Telegram clients render bare CJK text
            # small/thin, which made the character genuinely hard to
            # read at a glance. Sent as its own message (no caption/
            # keyboard) right before the spoiler card that carries those.
            send_photo(api_base, chat_id, kanji_image.render_kanji_png(key), filename="kanji.png")
            send_srs_card(api_base, chat_id, "k", key, html_text)
        # Unlike the Mini App flow (marked seen when webapp.py later
        # serves the review page - see srs.mark_seen), a card sent this
        # way is already fully visible the moment it lands in the chat,
        # so it counts as seen right now - otherwise an unrated kanji
        # would never resurface as a reminder (see srs.pick_next).
        now_seen = srs.now_utc()
        if any([srs.mark_seen(state, key, now_seen) for key in picked_keys]):
            srs.save_state(KANJI_SRS_PATH, state)
        print(f"\n[kanji] sent {len(picks)} kanji from {KANJI_CSV_GLOB}: {', '.join(picked_keys)}")

    # Optional extra sinks - after Telegram (the primary channel) and the
    # SRS state save, so a slow or failing API here never delays the
    # phone push or risks re-picking the same kanji next run. Each returns
    # a status string (never raises); print it for the cron log.
    now_dt = datetime.now(timezone.utc)
    entries = [row_to_entry(row_by_key[key]) for key in picked_keys]
    if os.environ.get("NOTION_API_KEY") and os.environ.get("NOTION_KANJI_DB_ID"):
        print(notion_add_row(entries, KANJI_LEVEL))
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") and os.environ.get("KANJI_GDOC_ID"):
        print(gdoc_append(entries, KANJI_LEVEL, now_dt))


if __name__ == "__main__":
    main()
