"""Optional extra sinks for the kanji drip: a Notion database row and a
Google Doc append, both alongside the Telegram push in kanji_drip.py -
same shape as vocab_sinks.py (see that file's docstring for the full
rationale), just with kanji's own fields (onyomi/kunyomi/component/
confusable/words) instead of vocab's (particles/type).

Both are BEST-EFFORT: every function here catches its own errors and
returns a short status string (an "Error: ..." string on failure) instead
of raising, so a Notion/Docs outage never stops the Telegram drip that is
the whole point of kanji_drip.py.

Env vars (only read when kanji_drip.py calls in, i.e. when set):
  NOTION_API_KEY               internal integration token (secret_...),
  NOTION_KANJI_DB_ID            shared with vocab's - see cron-notifications.md
      Database needs a title property named 'Kanji' plus, to be
      populated, properties with these exact names/types (any missing
      are just skipped):
        Reading    - rich_text  (onyomi / kunyomi, slash-joined)
        Meaning    - rich_text
        Component  - rich_text  (構成 - what it's built from)
        Confusable - rich_text  (似ている - easily-confused kanji)
        Words      - rich_text  (compound examples)
        Example    - rich_text  (one example sentence, JP + EN)
        JLPT       - select
  GOOGLE_SERVICE_ACCOUNT_JSON  path to a service-account key file, shared
  KANJI_GDOC_ID                 with vocab's - target Google Doc id
      Share the Doc with the service account's client_email as Editor.
"""

import os
import re
from datetime import datetime

import requests

NOTION_VERSION = "2022-06-28"
NOTION_MAX_RICH_TEXT_CHARS = 2000
NOTION_MAX_SELECT_CHARS = 100

_TIMEOUT = 15


def _raise_for_api(resp: requests.Response, what: str) -> None:
    if resp.status_code >= 400:
        raise RuntimeError(f"{what} -> HTTP {resp.status_code}: {resp.text[:300]}")


# ---- Notion ------------------------------------------------------------

def _rich_text_value(content: str) -> dict:
    return {
        "rich_text": [
            {"type": "text", "text": {"content": content[:NOTION_MAX_RICH_TEXT_CHARS]}}
        ]
    }


def _row_properties(entry: dict, level: str) -> dict:
    props = {
        "Kanji": {"title": [{"text": {"content": str(entry.get("kanji", "")).strip()}}]}
    }
    if entry.get("reading"):
        props["Reading"] = _rich_text_value(str(entry["reading"]).strip())
    if entry.get("meaning"):
        props["Meaning"] = _rich_text_value(str(entry["meaning"]).strip())
    if entry.get("component"):
        props["Component"] = _rich_text_value(str(entry["component"]).strip())
    if entry.get("confusable"):
        props["Confusable"] = _rich_text_value(str(entry["confusable"]).strip())
    if entry.get("words"):
        props["Words"] = _rich_text_value(" / ".join(entry["words"]))
    if entry.get("example"):
        example = entry["example"]
        text = f"{example} — {entry['example_en']}" if entry.get("example_en") else example
        props["Example"] = _rich_text_value(text)
    if level:
        props["JLPT"] = {"select": {"name": level[:NOTION_MAX_SELECT_CHARS]}}
    return props


def notion_add_row(entries: list, level: str) -> str:
    """One Notion row per kanji in `entries` (each a dict with kanji /
    reading / meaning / component / confusable / words / example /
    example_en). Best-effort per row."""
    if not entries:
        return "Notion: no kanji entries to add"

    api_key = os.environ["NOTION_API_KEY"]
    database_id = os.environ["NOTION_KANJI_DB_ID"]
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
            _raise_for_api(resp, f"Notion create row for {entry.get('kanji')!r}")
            added += 1
        except Exception as exc:  # best-effort per row
            failures.append(str(exc))

    if not failures:
        return f"Notion: added {added}/{len(entries)} kanji rows"
    return (
        f"Error: Notion added {added}/{len(entries)} kanji rows; "
        f"{len(failures)} failed ({failures[0]})"
    )


# ---- Google Doc ------------------------------------------------------------

_GDOC_PREAMBLE_TITLE = "JLPT Japanese Kanji Log"
_GDOC_PREAMBLE_BODY = (
    "This document is an ongoing, growing collection of Japanese kanji "
    "(JLPT level), with one new kanji added automatically every so often. "
    "Each entry gives the kanji itself, its on'yomi/kun'yomi readings, the "
    "English meaning, what it's built from (構成), kanji it's easily "
    "confused with (似ている), example compound words, and one example "
    "sentence in Japanese and English. Use this as a source for "
    "flashcards, spaced-repetition decks, mnemonics, and study guides."
)

_BRACKET_RE = re.compile(r"\[[぀-ヿ]+\]")


def _strip_furigana(text: str) -> str:
    return _BRACKET_RE.sub("", text).strip()


def _gdoc_paragraphs(entries: list, level: str, when: datetime, *, preamble: bool,
                     leading_blank: bool) -> list:
    paras: list = []
    if leading_blank:
        paras.append(("", None))
    if preamble:
        paras.append((_GDOC_PREAMBLE_TITLE, "HEADING_1"))
        paras.append((_GDOC_PREAMBLE_BODY, None))
        paras.append(("", None))
    paras.append((f"Added {when:%Y-%m-%d %H:%M} UTC · {level}", None))

    for entry in entries:
        kanji = str(entry.get("kanji", "")).strip()
        reading = str(entry.get("reading", "")).strip()
        paras.append((f"{kanji} — {reading}" if reading else kanji, "HEADING_2"))
        if entry.get("meaning"):
            paras.append((f"Meaning: {str(entry['meaning']).strip()}", None))
        if entry.get("component"):
            paras.append((f"Component (構成): {_strip_furigana(str(entry['component']))}", None))
        if entry.get("confusable"):
            paras.append((f"Confusable (似ている): {_strip_furigana(str(entry['confusable']))}", None))
        if entry.get("words"):
            paras.append((f"Words: {' / '.join(entry['words'])}", None))
        if entry.get("example"):
            paras.append(("Example:", None))
            paras.append((f"  {_strip_furigana(str(entry['example']))}", None))
            if entry.get("example_en"):
                paras.append((f"  {str(entry['example_en']).strip()}", None))
        paras.append(("", None))
    return paras


def _paragraphs_to_requests(paras: list, insert_at: int):
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
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        service_account_json, scopes=["https://www.googleapis.com/auth/documents"]
    )
    creds.refresh(Request())
    return creds.token


def gdoc_append(entries: list, level: str, when: datetime) -> str:
    """Append one drip (the structured `entries`) to the kanji Doc, with a
    one-time preamble if the doc is empty. Best-effort."""
    if not entries:
        return "Google Doc: no kanji entries to append"

    doc_id = os.environ["KANJI_GDOC_ID"]
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
            f"Google Doc: appended {len(entries)} kanji ({len(text)} chars) "
            f"at index {insert_at}"
        )
    except Exception as exc:  # best-effort - never break the Telegram drip
        return f"Error: Google Doc append failed ({exc})"
