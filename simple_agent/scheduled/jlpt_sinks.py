"""JLPT Adaptive Instructor sinks - Google Doc append sink for NotebookLM
source ingestion alongside Notion and email delivery in jlpt_instructor.py.

Env vars:
  GOOGLE_SERVICE_ACCOUNT_JSON   path to a service-account key file
  JLPT_GDOC_ID                  target Google Doc id (from its URL)
"""

import os
import re
from datetime import datetime

import requests

_TIMEOUT = 15

_GDOC_PREAMBLE_TITLE = "JLPT Japanese Adaptive Instructor Daily Log"
_GDOC_PREAMBLE_BODY = (
    "This document is an ongoing collection of daily JLPT study plans, "
    "generated reading passages, comprehension questions, and grammar practice "
    "sets from the adaptive instructor. Use this as a source for NotebookLM "
    "audio overviews, flashcard generation, and deep study."
)


def _raise_for_api(resp: requests.Response, what: str) -> None:
    if resp.status_code >= 400:
        raise RuntimeError(f"{what} -> HTTP {resp.status_code}: {resp.text[:300]}")


def _gdoc_access_token(service_account_json: str) -> str:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        service_account_json, scopes=["https://www.googleapis.com/auth/documents"]
    )
    creds.refresh(Request())
    return creds.token


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


def _format_instructor_day_paragraphs(
    plan_result: dict, reading_item: dict, grammar_practice: list, date_str: str,
    *, preamble: bool, leading_blank: bool,
) -> list:
    paras = []
    if leading_blank:
        paras.append(("", None))
    if preamble:
        paras.append((_GDOC_PREAMBLE_TITLE, "HEADING_1"))
        paras.append((_GDOC_PREAMBLE_BODY, None))
        paras.append(("", None))

    paras.append((f"JLPT Study Plan — {date_str}", "HEADING_1"))
    paras.append((f"Estimated duration: ~{plan_result.get('estimated_minutes', 40)} minutes", None))

    # Blocks breakdown
    blocks = plan_result.get("blocks", [])
    grouped = {"vocab": [], "grammar": [], "kanji": []}
    for b in blocks:
        grouped.get(b.get("type"), []).append(b)

    paras.append(("Vocabulary", "HEADING_2"))
    if grouped["vocab"]:
        for b in grouped["vocab"]:
            paras.append((f"- {b['source_id']} {b['item']} ({b['kind']})", None))
    else:
        paras.append(("No vocabulary scheduled today.", None))

    paras.append(("Kanji", "HEADING_2"))
    if grouped["kanji"]:
        for b in grouped["kanji"]:
            paras.append((f"- {b['source_id']} {b['item']} ({b['kind']})", None))
    else:
        paras.append(("No kanji scheduled today.", None))

    paras.append(("Grammar Practice", "HEADING_2"))
    if grammar_practice:
        for kind, text in grammar_practice:
            style = "HEADING_3" if kind == "heading" else None
            paras.append((text, style))
    else:
        paras.append(("No grammar scheduled today.", None))

    if reading_item and reading_item.get("visible"):
        paras.append((f"Reading Passage ({reading_item.get('id', 'Reading')})", "HEADING_2"))
        paras.append((reading_item["visible"], None))
        if reading_item.get("answers"):
            paras.append(("Answer Key:", None))
            paras.append((reading_item["answers"], None))

    paras.append(("", None))
    return paras


def gdoc_append_instructor_day(
    plan_result: dict, reading_item: dict, grammar_practice: list, date_str: str
) -> str:
    """Appends today's JLPT instructor plan and generated content to the target Google Doc.
    Best-effort function: returns a status string on success or error string on failure."""
    doc_id = os.environ.get("JLPT_GDOC_ID")
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not doc_id or not sa_json:
        return "Google Doc: skipped (JLPT_GDOC_ID or GOOGLE_SERVICE_ACCOUNT_JSON not set)"

    try:
        token = _gdoc_access_token(sa_json)
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

        paras = _format_instructor_day_paragraphs(
            plan_result, reading_item, grammar_practice, date_str,
            preamble=empty, leading_blank=not empty
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
        return f"Google Doc: appended instructor plan for {date_str} ({len(text)} chars) at index {insert_at}"
    except Exception as exc:
        return f"Error: Google Doc append failed ({exc})"
