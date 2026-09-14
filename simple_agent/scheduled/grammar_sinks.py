"""Optional extra sinks for the grammar drip: a Notion database row and a
Google Doc append, both alongside the Telegram push in grammar_drip.py -
same shape as vocab_sinks.py (see that file's docstring for the full
rationale), just with grammar's own fields (formation/nuance/contrast)
instead of vocab's (particles/type).

Both are BEST-EFFORT: every function here catches its own errors and
returns a short status string (an "Error: ..." string on failure) instead
of raising, so a Notion/Docs outage never stops the Telegram drip that is
the whole point of grammar_drip.py.

Env vars (only read when grammar_drip.py calls in, i.e. when set):
  NOTION_API_KEY                internal integration token (secret_...),
  NOTION_GRAMMAR_DB_ID           shared with vocab's - see cron-notifications.md
      Database needs a title property named 'Pattern' plus, to be
      populated, properties with these exact names/types (any missing
      are just skipped):
        Formation  - rich_text  (形 - how the pattern attaches)
        Meaning    - rich_text
        Nuance     - rich_text
        Contrast   - rich_text  (対比 - similar patterns and how they differ)
        Example 1, Example 2, Example 3 - rich_text
        JLPT       - select
  GOOGLE_SERVICE_ACCOUNT_JSON  path to a service-account key file, shared
  GRAMMAR_GDOC_ID                with vocab's - target Google Doc id
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


NOTION_EXAMPLE_COLUMNS = ["Example 1", "Example 2", "Example 3"]


def _row_properties(entry: dict, level: str) -> dict:
    props = {
        "Pattern": {"title": [{"text": {"content": str(entry.get("pattern", "")).strip()}}]}
    }
    if entry.get("formation"):
        props["Formation"] = _rich_text_value(str(entry["formation"]).strip())
    if entry.get("meaning"):
        props["Meaning"] = _rich_text_value(str(entry["meaning"]).strip())
    if entry.get("nuance"):
        props["Nuance"] = _rich_text_value(str(entry["nuance"]).strip())
    if entry.get("contrast"):
        props["Contrast"] = _rich_text_value(str(entry["contrast"]).strip())
    for column, example in zip(NOTION_EXAMPLE_COLUMNS, entry.get("examples") or []):
        props[column] = _rich_text_value(str(example))
    if level:
        props["JLPT"] = {"select": {"name": level[:NOTION_MAX_SELECT_CHARS]}}
    return props


def notion_add_row(entries: list, level: str) -> str:
    """One Notion row per pattern in `entries` (each a dict with pattern /
    formation / meaning / nuance / contrast / examples). Best-effort per
    row."""
    if not entries:
        return "Notion: no grammar entries to add"

    api_key = os.environ["NOTION_API_KEY"]
    database_id = os.environ["NOTION_GRAMMAR_DB_ID"]
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
            _raise_for_api(resp, f"Notion create row for {entry.get('pattern')!r}")
            added += 1
        except Exception as exc:  # best-effort per row
            failures.append(str(exc))

    if not failures:
        return f"Notion: added {added}/{len(entries)} grammar rows"
    return (
        f"Error: Notion added {added}/{len(entries)} grammar rows; "
        f"{len(failures)} failed ({failures[0]})"
    )


# ---- Google Doc ------------------------------------------------------------

_GDOC_PREAMBLE_TITLE = "JLPT Japanese Grammar Log"
_GDOC_PREAMBLE_BODY = (
    "This document is an ongoing, growing collection of Japanese grammar "
    "patterns (JLPT level), with one new pattern added automatically every "
    "so often. Each entry gives the pattern itself, how it's formed (形), "
    "the English meaning, its nuance, how it contrasts with similar "
    "patterns (対比), and example sentences in Japanese and English. Use "
    "this as a source for flashcards, spaced-repetition decks, and study "
    "guides."
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
        pattern = str(entry.get("pattern", "")).strip()
        paras.append((pattern, "HEADING_2"))
        if entry.get("formation"):
            paras.append((f"Formation (形): {_strip_furigana(str(entry['formation']))}", None))
        if entry.get("meaning"):
            paras.append((f"Meaning: {str(entry['meaning']).strip()}", None))
        if entry.get("nuance"):
            paras.append((f"Nuance (ニュアンス): {str(entry['nuance']).strip()}", None))
        if entry.get("contrast"):
            paras.append((f"Contrast (対比): {_strip_furigana(str(entry['contrast']))}", None))
        examples = entry.get("examples") or []
        if examples:
            paras.append(("Examples:", None))
            for i, example in enumerate(examples, 1):
                paras.append((f"{i}. {_strip_furigana(str(example))}", None))
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
    """Append one drip (the structured `entries`) to the grammar Doc, with
    a one-time preamble if the doc is empty. Best-effort."""
    if not entries:
        return "Google Doc: no grammar entries to append"

    doc_id = os.environ["GRAMMAR_GDOC_ID"]
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
            f"Google Doc: appended {len(entries)} pattern(s) ({len(text)} chars) "
            f"at index {insert_at}"
        )
    except Exception as exc:  # best-effort - never break the Telegram drip
        return f"Error: Google Doc append failed ({exc})"
