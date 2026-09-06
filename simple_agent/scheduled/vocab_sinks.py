"""Optional extra sinks for the vocab drip: per-word Notion database rows
and a Google Doc append, both alongside the Telegram push in vocab_drip.py.

Why two:
  - Notion database  - a browsable, filterable archive. One row PER WORD,
    with Reading / Meaning / Particle / Type / JLPT columns and the 3
    example sentences in their own Example 1 / Example 2 / Example 3
    columns - a flat table you can sort, filter by level or part of
    speech, and search.
  - Google Doc        - a single, ever-growing doc used as a NotebookLM
    source. NotebookLM re-syncs a Drive doc on demand, so this stays one
    source you refresh rather than a file you re-upload every day. Steer
    the audio-overview / flashcard generation at "today's entries" when
    you only want the latest batch.

    This doc is formatted FOR the model, not for a person: a one-time
    preamble paragraph explaining what the document is, a real Heading 2
    per word, explicit English field labels (Meaning / Part of speech /
    Particle patterns), and every example sentence written three ways -
    normal Japanese, kana-only for pronunciation, and English. The
    inline [furigana] from the drip is split out into the kana line.

Both are BEST-EFFORT: every function here catches its own errors and
returns a short status string (an "Error: ..." string on failure) instead
of raising, so a Notion/Docs outage never stops the Telegram drip that is
the whole point of vocab_drip.py. The Notion sink is also best-effort
per row - one bad row doesn't stop the others. vocab_drip.py prints
whatever comes back, so the cron log (see cron-notifications.md) shows
what happened.

Each function reads its own config from the environment (same style as
telegram_bot.py's tools) and is only called by vocab_drip.py when the
relevant vars are set:

  NOTION_API_KEY               internal integration token (secret_...)
  NOTION_VOCAB_DB_ID           target database id (32 hex chars, from its URL)
      The database needs a title property named 'Word' plus, to be
      populated, properties with these exact names/types (any that are
      missing are just skipped):
        Reading    - rich_text   Particle  - rich_text  (助詞 patterns)
        Meaning    - rich_text   Type      - select
        Example 1  - rich_text   JLPT      - select
        Example 2  - rich_text
        Example 3  - rich_text
      Then share the database with the integration.
  GOOGLE_SERVICE_ACCOUNT_JSON  path to a service-account key file
  VOCAB_GDOC_ID                target Google Doc id (from its URL)
      Share the Doc with the service account's client_email as Editor.

Only one new dependency, for the Google Doc path: google-auth (used just
to mint an access token; the Docs REST API itself is called with
requests, like the rest of this codebase). The import is lazy so the
Notion path and the tests don't need it installed.
"""

import os
import re
from datetime import datetime

import requests

# Notion caps each rich-text run at 2000 chars.
NOTION_VERSION = "2022-06-28"
NOTION_MAX_RICH_TEXT_CHARS = 2000
NOTION_MAX_SELECT_CHARS = 100

_TIMEOUT = 15


def _raise_for_api(resp: requests.Response, what: str) -> None:
    if resp.status_code >= 400:
        raise RuntimeError(f"{what} -> HTTP {resp.status_code}: {resp.text[:300]}")


def _header_line(level: str, when: datetime) -> str:
    return f"{level} · {when:%Y-%m-%d %H:%M} UTC"


# ---- Notion ------------------------------------------------------------

def _rich_text_value(content: str) -> dict:
    return {
        "rich_text": [
            {"type": "text", "text": {"content": content[:NOTION_MAX_RICH_TEXT_CHARS]}}
        ]
    }


# The 3 example sentences go in their own columns (the drip prompt asks
# for exactly 3). More than 3 -> the extras are dropped; fewer -> the
# trailing columns are left empty.
NOTION_EXAMPLE_COLUMNS = ["Example 1", "Example 2", "Example 3"]


def _entry_examples(entry: dict) -> list:
    """The example sentences for a word, as a list of non-empty strings."""
    examples = entry.get("examples")
    if isinstance(examples, str):
        examples = [examples]
    elif not isinstance(examples, list):
        examples = []
    return [str(x).strip() for x in examples if str(x).strip()]


def _row_properties(entry: dict, level: str) -> dict:
    """Properties for one word's row: its fields plus up to 3 example
    columns. Only the title ('Word') is always sent; everything else is
    included only when it has a value, so a DB missing an optional column
    still works for the columns it does have."""
    props = {
        "Word": {"title": [{"text": {"content": str(entry.get("word", "")).strip()}}]}
    }
    if entry.get("reading"):
        props["Reading"] = _rich_text_value(str(entry["reading"]).strip())
    if entry.get("meaning"):
        props["Meaning"] = _rich_text_value(str(entry["meaning"]).strip())
    if entry.get("particles"):
        props["Particle"] = _rich_text_value(str(entry["particles"]).strip())
    for column, sentence in zip(NOTION_EXAMPLE_COLUMNS, _entry_examples(entry)):
        props[column] = _rich_text_value(sentence)
    if entry.get("type"):
        props["Type"] = {"select": {"name": str(entry["type"]).strip()[:NOTION_MAX_SELECT_CHARS]}}
    if level:
        props["JLPT"] = {"select": {"name": level[:NOTION_MAX_SELECT_CHARS]}}
    return props


def notion_add_row(entries: list, level: str) -> str:
    """One Notion row per word in `entries` (each a dict with word /
    reading / meaning / particles / type / examples). Best-effort per
    row - one bad row doesn't stop the others."""
    if not entries:
        return "Notion: no word entries to add"

    api_key = os.environ["NOTION_API_KEY"]
    database_id = os.environ["NOTION_VOCAB_DB_ID"]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

    added = 0
    failures = []
    for entry in entries:
        try:
            resp = requests.post(
                "https://api.notion.com/v1/pages",
                headers=headers,
                json={
                    "parent": {"database_id": database_id},
                    "properties": _row_properties(entry, level),
                },
                timeout=_TIMEOUT,
            )
            _raise_for_api(resp, f"Notion create row for {entry.get('word')!r}")
            added += 1
        except Exception as exc:  # best-effort per row
            failures.append(str(exc))

    if not failures:
        return f"Notion: added {added}/{len(entries)} word rows"
    return (
        f"Error: Notion added {added}/{len(entries)} word rows; "
        f"{len(failures)} failed ({failures[0]})"
    )


# ---- Google Doc ------------------------------------------------------------
#
# The doc is written for NotebookLM (Gemini) to parse, so: a one-time
# preamble telling the model what the document is and what it's for, real
# Heading-2 paragraphs per word for chunking/navigation, explicit English
# field labels, and each example sentence three ways (Japanese / kana /
# English) so the audio overview can pronounce it. Built from the same
# structured `entries` the Notion sink uses, not the Telegram text.

_GDOC_PREAMBLE_TITLE = "JLPT Japanese Vocabulary Log"
_GDOC_PREAMBLE_BODY = (
    "This document is an ongoing, growing collection of Japanese vocabulary "
    "(JLPT level), with a few new words added automatically every couple of "
    "hours. Each entry gives the word, its kana reading, the English meaning, "
    "the part of speech, the grammatical particle patterns it takes "
    "(助詞 / joshi), and up to three example sentences - each written three "
    "ways: in normal Japanese, in kana for pronunciation, and in English. Use "
    "this as a source for flashcards, spaced-repetition decks, vocabulary "
    "quizzes, pronunciation practice, audio summaries, and study guides."
)

# "漢字[かな]" inline furigana as produced by the drip prompt. The kanji
# run before the bracket is any CJK ideograph (plus the 々 iteration mark);
# the bracket holds the hiragana/katakana reading.
_FURIGANA_RE = re.compile(r"[㐀-鿿豈-﫿々-〇]+\[([぀-ヿ]+)\]")
_BRACKET_RE = re.compile(r"\[[぀-ヿ]+\]")


def _strip_furigana(text: str) -> str:
    """'写真[しゃしん]を撮る' -> '写真を撮る' (drop the bracketed readings)."""
    return _BRACKET_RE.sub("", text).strip()


def _reading_only(text: str) -> str:
    """'写真[しゃしん]を撮[と]る' -> 'しゃしんをとる' (kanji replaced by reading)."""
    return _BRACKET_RE.sub("", _FURIGANA_RE.sub(r"\1", text)).strip()


def _gdoc_paragraphs(entries: list, level: str, when: datetime, *, preamble: bool,
                     leading_blank: bool) -> list:
    """The drip as a list of (paragraph_text, named_style_or_None)."""
    paras: list = []
    if leading_blank:
        paras.append(("", None))
    if preamble:
        paras.append((_GDOC_PREAMBLE_TITLE, "HEADING_1"))
        paras.append((_GDOC_PREAMBLE_BODY, None))
        paras.append(("", None))
    paras.append((f"Added {when:%Y-%m-%d %H:%M} UTC · {level}", None))

    for entry in entries:
        word = str(entry.get("word", "")).strip()
        reading = str(entry.get("reading", "")).strip()
        paras.append((f"{word} — {reading}" if reading else word, "HEADING_2"))
        if entry.get("meaning"):
            paras.append((f"Meaning: {str(entry['meaning']).strip()}", None))
        if entry.get("type"):
            paras.append((f"Part of speech: {str(entry['type']).strip()}", None))
        if entry.get("particles"):
            paras.append(
                (f"Particle patterns (助詞): {_strip_furigana(str(entry['particles']))}", None)
            )
        examples = _entry_examples(entry)
        if examples:
            paras.append(("Examples:", None))
            for i, example in enumerate(examples, 1):
                japanese, _, english = str(example).partition(" — ")
                clean = _strip_furigana(japanese)
                kana = _reading_only(japanese)
                paras.append((f"{i}. {clean}", None))
                if kana and kana != clean:
                    paras.append((f"   {kana}", None))
                if english.strip():
                    paras.append((f"   {english.strip()}", None))
        paras.append(("", None))
    return paras


def _paragraphs_to_requests(paras: list, insert_at: int):
    """Flatten paragraphs to one insertText plus an updateParagraphStyle
    per heading. The style ranges use post-insert indices, which is what
    the API wants for requests that follow the insertText in the batch."""
    text = ""
    style_requests = []
    for para_text, style in paras:
        start = insert_at + len(text)
        text += para_text + "\n"
        if style:
            style_requests.append(
                {
                    "updateParagraphStyle": {
                        "range": {"startIndex": start, "endIndex": insert_at + len(text)},
                        "paragraphStyle": {"namedStyleType": style},
                        "fields": "namedStyleType",
                    }
                }
            )
    return text, style_requests


def _gdoc_access_token(service_account_json: str) -> str:
    """Service-account -> short-lived access token. Split out so tests can
    patch it without mocking the whole google-auth stack."""
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        service_account_json, scopes=["https://www.googleapis.com/auth/documents"]
    )
    creds.refresh(Request())
    return creds.token


def gdoc_append(entries: list, level: str, when: datetime) -> str:
    """Append one drip (the structured `entries`) to the NotebookLM Doc,
    with a one-time preamble if the doc is empty. Best-effort."""
    if not entries:
        return "Google Doc: no word entries to append"

    doc_id = os.environ["VOCAB_GDOC_ID"]
    try:
        token = _gdoc_access_token(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
        headers = {"Authorization": f"Bearer {token}"}

        doc = requests.get(
            f"https://docs.googleapis.com/v1/documents/{doc_id}",
            headers=headers,
            timeout=_TIMEOUT,
        )
        _raise_for_api(doc, "Google Doc get")
        content = doc.json().get("body", {}).get("content", [])
        # The body always ends in a newline; valid insert indices are
        # 1..endIndex-1, so append just before that final newline.
        end_index = content[-1]["endIndex"] if content else 2
        insert_at = max(1, end_index - 1)
        empty = end_index <= 2

        paras = _gdoc_paragraphs(
            entries, level, when, preamble=empty, leading_blank=not empty
        )
        text, style_requests = _paragraphs_to_requests(paras, insert_at)

        resp = requests.post(
            f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
            headers=headers,
            json={
                "requests": [
                    {"insertText": {"location": {"index": insert_at}, "text": text}},
                    *style_requests,
                ]
            },
            timeout=_TIMEOUT,
        )
        _raise_for_api(resp, "Google Doc batchUpdate")
        return (
            f"Google Doc: appended {len(entries)} words ({len(text)} chars) "
            f"at index {insert_at}"
        )
    except Exception as exc:  # best-effort - never break the Telegram drip
        return f"Error: Google Doc append failed ({exc})"
