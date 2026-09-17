"""Notion access for the JLPT instructor (jlpt_instructor.py) - reads and
writes rows in the actual "JLPT Daily Plan" Notion DATABASE the ChatGPT/
Codex agent already used. This is NOT a freeform page with body blocks -
it's a structured database with real properties:

  Day               title      "2026-09-14 — Day 01"
  Date              date       set when the row is created
  Status            status     "Not started" / "In progress" / "Done"
  Vocabulary        rich_text  e.g. "10 new cards: V011-V020 決まる、..."
  Grammar           rich_text  e.g. "3 new cards maximum: G002 ... G014 ..."
  Kanji             rich_text  e.g. "3 new cards: K004 増; K005 減; K006 選"
  Review Task       rich_text  the due/carry-forward instructions
  Instructor Notes  rich_text  evening feedback - READ-MODIFY-WRITE only,
                                never replaced wholesale, so prior entries
                                are preserved

The database also holds a SEPARATE, unrelated 42-row "Day NN —
Acquisition NN" template track with its own, disconnected numbering -
this module never reads, dates, or repurposes those rows. Today's row is
always a BRAND NEW page, titled to match the real dated rows' own
convention ("YYYY-MM-DD — Day NN", NN continuing the actual dated
history via next_dated_day_number()) - never an existing row hijacked
and renamed, however tempting an unused-looking row might look.

Deliberately separate from kanji_sinks.py / grammar_sinks.py /
vocab_sinks.py: those only ever POST a NEW row (one row per drip item) to
a different database shape. This module instead creates ONE new dated
row per day (or finds it if an earlier run today already created it).

Item ids used in Vocabulary/Grammar/Kanji/Review Task text and reported
back in Instructor Notes/comments are RANK-BASED ("V011", "K004", "G002")
matching jlpt_item_bank.load_catalog's source_id scheme - not word text.

Env vars:
  NOTION_API_KEY               reused from the existing sinks
  JLPT_NOTION_COLLECTION_ID    the "JLPT Daily Plan" database id

Every function here can raise (RuntimeError on a non-2xx Notion
response) - unlike the best-effort sinks, jlpt_instructor.py's morning
gate needs to see REAL failures rather than a swallowed error string, so
it never claims a successful save/send that didn't happen.
"""

import os
import re

import requests

NOTION_VERSION = "2022-06-28"
_TIMEOUT = 15
NOTION_MAX_RICH_TEXT_CHARS = 2000

_RICH_TEXT_PROPERTIES = ("Vocabulary", "Grammar", "Kanji", "Review Task", "Instructor Notes")
TITLE_PROPERTY = os.environ.get("JLPT_NOTION_TITLE_PROPERTY") or "Day"

# Only matches the DATED convention actually used by real instructor-
# authored rows ("2026-09-14 — Day 01") - deliberately does NOT match the
# separate, pre-seeded "Day 04 — Acquisition 04" template track (no date
# prefix), so counting/activating today's row never confuses the two.
_DATED_DAY_TITLE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\D+Day\s+0*(\d+)", re.IGNORECASE)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['NOTION_API_KEY']}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _raise_for_api(resp: requests.Response, what: str) -> None:
    if resp.status_code >= 400:
        raise RuntimeError(f"{what} -> HTTP {resp.status_code}: {resp.text[:300]}")


def _rich_text_plain(rich_text: list) -> str:
    return "".join(rt.get("plain_text", "") for rt in rich_text)


# ---- reading rows -----------------------------------------------------

def query_all_rows(database_id: str) -> list:
    """Every row in the database, paginated. Small enough (~42 rows) to
    just hold in memory - jlpt_instructor.py calls this at most twice per
    run."""
    rows = []
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        resp = requests.post(
            f"https://api.notion.com/v1/databases/{database_id}/query",
            headers=_headers(),
            json=body,
            timeout=_TIMEOUT,
        )
        _raise_for_api(resp, "Notion query JLPT Daily Plan database")
        data = resp.json()
        rows.extend(data.get("results", []))
        if not data.get("has_more"):
            return rows
        cursor = data.get("next_cursor")


def read_row(page: dict) -> dict:
    """A Notion page object (from query_all_rows / a single-page GET) ->
    a plain dict of the fields jlpt_instructor.py actually reads/writes."""
    props = page.get("properties", {})
    title_prop = next((p for p in props.values() if p.get("type") == "title"), {"title": []})
    status_prop = props.get("Status", {}).get("status")
    date_prop = props.get("Date", {}).get("date")
    result = {
        "page_id": page["id"],
        "url": page.get("url"),
        "title": _rich_text_plain(title_prop.get("title", [])),
        "status": status_prop["name"] if status_prop else None,
        "date": date_prop["start"] if date_prop else None,
    }
    for name in _RICH_TEXT_PROPERTIES:
        prop = props.get(name)
        result[name] = _rich_text_plain(prop.get("rich_text", [])) if prop else ""
    return result


def find_row_for_date(database_id: str, date_str: str) -> dict:
    """The row whose Date property == date_str exactly, or None - used to
    detect "today's row was already activated earlier today" (e.g. a
    same-day re-run, or the evening branch looking for what the morning
    branch wrote)."""
    for page in query_all_rows(database_id):
        row = read_row(page)
        if row["date"] == date_str:
            return row
    return None


def find_previous_dated_row(database_id: str, before_date_str: str) -> dict:
    """The most recently DATED row strictly before `before_date_str` -
    "day N-1" - or None if there isn't one. Only ever needs to look one
    day back: once the instructor has run for day N-1, its evidence is
    already folded into the item bank state, so day N only needs to
    check the single day immediately before it, not the whole history."""
    candidates = [
        row for row in (read_row(page) for page in query_all_rows(database_id))
        if row["date"] and row["date"] < before_date_str
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda row: row["date"])


def list_dated_rows_before(database_id: str, before_date_str: str) -> list:
    """Every DATED row strictly before `before_date_str`, oldest first -
    used only to build the "what's already been published" map
    (jlpt_instructor._mark_published): unlike evidence/repair
    reconciliation (which only ever needs day N-1, since each day's
    result already carries forward from then on), knowing which items
    were EVER introduced has no such shortcut - it has to see the whole
    prior history at least once. Safe and cheap to call every run: it's
    read-only and the caller's own membership check makes re-processing
    an already-known row a no-op."""
    rows = [
        row for row in (read_row(page) for page in query_all_rows(database_id))
        if row["date"] and row["date"] < before_date_str
    ]
    rows.sort(key=lambda row: row["date"])
    return rows


def next_dated_day_number(database_id: str) -> int:
    """1 + the highest "Day NN" number among rows that actually follow
    the dated "YYYY-MM-DD — Day NN" convention (real instructor-authored
    rows) - 1 if there are none yet. Never looks at the separate
    undated "Day NN — Acquisition NN" template track, so a gap or
    mismatch in that track can't throw off the real sequence."""
    numbers = [
        int(m.group(1))
        for page in query_all_rows(database_id)
        for m in [_DATED_DAY_TITLE_RE.match(read_row(page)["title"] or "")]
        if m
    ]
    return max(numbers, default=0) + 1


# ---- writing ------------------------------------------------------------

def _rich_text_prop(text: str) -> dict:
    return {"rich_text": [{"type": "text", "text": {"content": text[:NOTION_MAX_RICH_TEXT_CHARS]}}]}


def create_daily_row(database_id: str, date_str: str, day_number: int) -> dict:
    """Creates a BRAND NEW row for today - "YYYY-MM-DD — Day NN", matching
    the real dated rows' own title convention - rather than repurposing
    any row from the separate Acquisition template track. Returns the
    same shape as read_row() (rich-text fields empty - fill them via
    update_row/append_body_blocks right after)."""
    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=_headers(),
        json={
            "parent": {"database_id": database_id},
            "properties": {
                TITLE_PROPERTY: {"title": [{"text": {"content": f"{date_str} — Day {day_number:02d}"}}]},
                "Date": {"date": {"start": date_str}},
                "Status": {"status": {"name": "In progress"}},
            },
        },
        timeout=_TIMEOUT,
    )
    _raise_for_api(resp, f"Notion create daily row for {date_str}")
    return read_row(resp.json())


def update_row(
    page_id: str,
    date_str: str = None,
    status: str = None,
    fields: dict = None,
) -> None:
    """PATCHes the given properties on one existing row. `fields` maps a
    subset of _RICH_TEXT_PROPERTIES to their new text - properties left
    out of `fields` are untouched (Notion only overwrites the properties
    you send). Never touches "Instructor Notes" here - use
    append_instructor_notes for that, since it must be a read-then-append,
    not a blind overwrite."""
    properties = {}
    if date_str is not None:
        properties["Date"] = {"date": {"start": date_str}}
    if status is not None:
        properties["Status"] = {"status": {"name": status}}
    for name, text in (fields or {}).items():
        if name == "Instructor Notes":
            raise ValueError("use append_instructor_notes for Instructor Notes")
        properties[name] = _rich_text_prop(text)

    resp = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=_headers(),
        json={"properties": properties},
        timeout=_TIMEOUT,
    )
    _raise_for_api(resp, "Notion update row")


def append_instructor_notes(page_id: str, existing_notes: str, new_lines: list) -> None:
    """Read-modify-write: appends `new_lines` to whatever's already in
    Instructor Notes rather than replacing it, so the user's/prior
    evening's entries are preserved (Notion PATCHes replace a property's
    value wholesale - there's no native "append to rich_text" call)."""
    addition = "\n".join(new_lines)
    combined = f"{existing_notes}\n\n{addition}" if existing_notes else addition
    resp = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=_headers(),
        json={"properties": {"Instructor Notes": _rich_text_prop(combined)}},
        timeout=_TIMEOUT,
    )
    _raise_for_api(resp, "Notion append instructor notes")


def _body_block(text: str, style: str = "paragraph") -> dict:
    return {
        "object": "block",
        "type": style,
        style: {"rich_text": [{"type": "text", "text": {"content": text[:NOTION_MAX_RICH_TEXT_CHARS]}}]},
    }


def append_body_blocks(page_id: str, paragraphs: list) -> None:
    """Appends real page BODY content (not just a property) - each row is
    itself a page and can carry the same "Block 1 / Block 2 / Block 3"
    structured content the original ChatGPT/Codex-authored rows already
    have (headings, detailed instructions, the reading passage embedded
    inline with comprehension questions), not just short property text.
    `paragraphs` is a list of (text, style) tuples, style one of
    "paragraph"/"heading_2"/"heading_3"/"bulleted_list_item". Always
    APPENDS - never deletes or edits existing blocks, so re-running this
    for the same row adds to its history rather than erasing it."""
    resp = requests.patch(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        headers=_headers(),
        json={"children": [_body_block(text, style) for text, style in paragraphs]},
        timeout=_TIMEOUT,
    )
    _raise_for_api(resp, "Notion append body blocks")


def read_body_text(page_id: str) -> list:
    """All body block plain text (paragraph/heading/bulleted/numbered
    list items), in reading order, paginated - used by the evening
    branch to read back the "Learner report" checklist the user edits
    directly in the row body (not a property), same as the original
    ChatGPT/Codex-authored rows' "Learner report — edit this row"
    section."""
    texts = []
    cursor = None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        resp = requests.get(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=_headers(),
            params=params,
            timeout=_TIMEOUT,
        )
        _raise_for_api(resp, "Notion read body blocks")
        data = resp.json()
        for block in data.get("results", []):
            btype = block.get("type")
            payload = block.get(btype, {})
            if "rich_text" in payload:
                text = _rich_text_plain(payload["rich_text"])
                if text:
                    texts.append(text)
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return texts


def read_comments(page_id: str) -> list:
    comments = []
    cursor = None
    while True:
        params = {"block_id": page_id, "page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        resp = requests.get(
            "https://api.notion.com/v1/comments",
            headers=_headers(),
            params=params,
            timeout=_TIMEOUT,
        )
        _raise_for_api(resp, "Notion read comments")
        data = resp.json()
        for c in data.get("results", []):
            text = _rich_text_plain(c.get("rich_text", []))
            if text:
                comments.append(text)
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return comments


def add_comment(page_id: str, text: str) -> None:
    """Used for supplementary content that has no dedicated property yet
    (the reading passage, audio-pending notices) - added as a comment
    rather than silently inventing a new database column."""
    resp = requests.post(
        "https://api.notion.com/v1/comments",
        headers=_headers(),
        json={"parent": {"page_id": page_id}, "rich_text": [{"text": {"content": text[:NOTION_MAX_RICH_TEXT_CHARS]}}]},
        timeout=_TIMEOUT,
    )
    _raise_for_api(resp, "Notion add comment")


def verify_row_saved(database_id: str, date_str: str, expect_substrings: list) -> bool:
    """Re-reads today's row and checks it exists (dated today) and its
    combined property text contains every string in `expect_substrings`.
    Returns False (never raises) on a verify failure, distinct from a
    hard Notion API error - the spec's "verify the saved row"
    requirement."""
    try:
        row = find_row_for_date(database_id, date_str)
    except RuntimeError:
        return False
    if row is None:
        return False
    combined = "\n".join(row.get(name, "") for name in _RICH_TEXT_PROPERTIES)
    return all(s in combined for s in expect_substrings)
