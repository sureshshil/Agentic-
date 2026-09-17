"""JLPT adaptive-instructor cron entrypoint - the port of the ChatGPT/
Codex "cowork" workflow (see ../NOTION-FIRST-JLPT-ARCHITECTURE.md) into
this repo. Determines the current Japan date/time and runs the morning
branch (before noon JST) or the evening branch (from noon JST); never
replays a missed morning email at night.

Morning: find or create today's row in the existing "JLPT Daily Plan"
Notion database - a row already dated today (an earlier run today), or
else a BRAND NEW row titled to continue the real dated sequence
("YYYY-MM-DD — Day NN" - jlpt_notion.next_dated_day_number /
create_daily_row; the database's separate, disconnected "Day NN —
Acquisition NN" template track is never read or repurposed for this).
Reconcile actual reported evidence into the item bank
(jlpt_item_bank.ingest_evidence), validate it, plan today's bounded
lesson (jlpt_item_bank.plan), write the plan's Vocabulary/Grammar/Kanji/
Review Task fields plus a structured page body matching the existing
dated rows' own shape (Block 1 due/repair, Block 2 grammar contrast
practice, Block 3 reading - reusing an unattempted reading item from a
prior day via cross-day carryover rather than generating a new one every
run, Block 4 optional new items, a Listening section, and an editable
"Learner report" checklist - never touching Instructor Notes), verify the
row actually saved, then - gated by a local once-per-Japan-date ledger -
send one Gmail summary via telegram_bot's send_email and record the
delivery.

Evening: read the day's actual Notion row body (the Learner report
checklist lives there, not a property) + Instructor Notes + comments,
ingest that evidence, append concise instructor feedback to Instructor
Notes (read-modify-write, never overwriting prior entries). Never sends
email.

Deliberately a SEPARATE module from kanji_sinks.py / grammar_sinks.py /
vocab_sinks.py and the Telegram drip scripts - none of those are touched
or imported here (only jlpt_item_bank.py and jlpt_notion.py, both new).
Reuses telegram_bot.py's Agent (for composing the reading passage) and
send_email exactly as news_digest_agent.py already does.

Env vars (loaded via python-dotenv from ../telegram_bot.env and
../deploy/scheduled.env, same fill-in-what's-unset pattern as the other
scheduled scripts):
  NOTION_API_KEY               required - reused from the existing sinks
  JLPT_NOTION_COLLECTION_ID    required - the "JLPT Daily Plan" database id
  GCP_PROJECT_ID               required - for the reading-passage Agent call
  EMAIL_ADDRESS / EMAIL_APP_PASSWORD   required for the morning email
  JLPT_EMAIL_TO                 optional, default EMAIL_ADDRESS itself
  JLPT_ITEM_BANK_PATH           optional, default ../.jlpt_item_bank.json
  JLPT_EMAIL_LEDGER_PATH        optional, default ../.jlpt_email_ledger.json
  JLPT_VOCAB_TSV_GLOB / JLPT_KANJI_CSV_GLOB / JLPT_GRAMMAR_CSV_GLOB
                                 optional, default the repo's N3_*_batch* files

Never claims a successful Notion save or email send that didn't actually
happen - a concrete failure is printed to stderr and the process exits
non-zero so cron logs surface it. Routine successful runs print nothing
beyond a one-line summary (quiet by design).
"""

import json
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

_SCHEDULED_DIR = os.path.dirname(os.path.abspath(__file__))
_SIMPLE_AGENT_DIR = os.path.dirname(_SCHEDULED_DIR)
sys.path.insert(0, _SIMPLE_AGENT_DIR)
sys.path.insert(0, _SCHEDULED_DIR)

load_dotenv(os.path.join(_SIMPLE_AGENT_DIR, "telegram_bot.env"))
load_dotenv(os.path.join(_SIMPLE_AGENT_DIR, "deploy", "scheduled.env"))

from telegram_bot import Agent, send_email  # noqa: E402 - needs sys.path insert above
import jlpt_item_bank as bank  # noqa: E402
import jlpt_notion as notion  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")

ITEM_BANK_PATH = os.environ.get("JLPT_ITEM_BANK_PATH") or os.path.join(
    _SIMPLE_AGENT_DIR, ".jlpt_item_bank.json"
)
EMAIL_LEDGER_PATH = os.environ.get("JLPT_EMAIL_LEDGER_PATH") or os.path.join(
    _SIMPLE_AGENT_DIR, ".jlpt_email_ledger.json"
)
OUTPUTS_DIR = os.path.join(_SIMPLE_AGENT_DIR, "outputs")
EMAIL_DELIVERY_LOG = os.path.join(OUTPUTS_DIR, "Email-delivery.md")
PROGRESS_LOG = os.path.join(OUTPUTS_DIR, "Progress-log.md")

VOCAB_TSV_GLOB = os.environ.get("JLPT_VOCAB_TSV_GLOB") or os.path.join(
    _SIMPLE_AGENT_DIR, "N3_vocab_batch*.tsv"
)
KANJI_CSV_GLOB = os.environ.get("JLPT_KANJI_CSV_GLOB") or os.path.join(
    _SIMPLE_AGENT_DIR, "N3_kanji_batch*.csv"
)
GRAMMAR_CSV_GLOB = os.environ.get("JLPT_GRAMMAR_CSV_GLOB") or os.path.join(
    _SIMPLE_AGENT_DIR, "n3_grammar_batch*.csv"
)


class InstructorError(RuntimeError):
    """A concrete, reportable failure - never swallowed into a false
    "success"."""


# ---- ledger (once-per-Japan-date email gate) -------------------------------

def load_ledger(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_ledger(path: str, ledger: dict) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def reserve_email(ledger: dict, date_str: str) -> bool:
    """True if `date_str` was free (now reserved as PENDING in `ledger`,
    mutated in place - caller must save). False if PENDING or SENT
    already exists for that date - forbids another send."""
    existing = ledger.get(date_str, {}).get("status")
    if existing in ("PENDING", "SENT"):
        return False
    ledger[date_str] = {"status": "PENDING"}
    return True


def record_sent(ledger: dict, date_str: str, message: str, notion_url: str) -> None:
    ledger[date_str] = {
        "status": "SENT",
        "message": message,
        "notion_url": notion_url,
        "sent_at": datetime.now(JST).isoformat(),
    }


# ---- local logs -------------------------------------------------------------

def _append_log(path: str, line: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")


def append_email_delivery_log(date_str: str, message: str, notion_url: str) -> None:
    _append_log(EMAIL_DELIVERY_LOG, f"- {date_str}: {message} | {notion_url}")


def append_progress_log(date_str: str, text: str) -> None:
    _append_log(PROGRESS_LOG, f"## {date_str}\n{text}\n")


# ---- branch selection -------------------------------------------------------

def determine_branch(now_jst: datetime) -> str:
    """"morning" before noon JST, "evening" from noon on."""
    return "morning" if now_jst.hour < 12 else "evening"


# ---- evening: parsing reported evidence out of Notion content -------------

# Rank-based ids (V011, K004, G002 - jlpt_item_bank.load_catalog's
# source_id scheme, matching the ids already used in the live Vocabulary/
# Grammar/Kanji property text) reported back with a pass/partial/fail
# verdict, e.g. "V011 pass" or "K004: fail - mixed up with 快".
_EVIDENCE_LINE_RE = re.compile(
    r"\b(?P<source_id>[VKG]\d{3})\b\s*[:\-]?\s*(?P<result>pass|partial|fail)\b\s*[:\-]?\s*"
    r"(?P<note>.*?)(?=(?:,\s*)?\b[VKG]\d{3}\b|$)",
    re.IGNORECASE,
)


def parse_evidence(lines: list) -> dict:
    """Notion body/Instructor Notes/comment lines -> jlpt_item_bank.
    ingest_evidence's `reports["items"]` shape. Anything not matching the
    tagged-line convention is ignored here (not evidence for any specific
    item) - session-level completion counts are reported separately and
    are not inferred from free text."""
    items = []
    for line in lines:
        for m in _EVIDENCE_LINE_RE.finditer(line):
            items.append({
                "source_id": m.group("source_id").upper(),
                "result": m.group("result").lower(),
                "note": m.group("note").strip(),
            })
    return {"items": items}


# Reported directly in the Learner report checklist, e.g. "READING
# R-N3-002 attempted" - the only way a carried-over reading item is ever
# marked done (there's no automated grading of the embedded questions).
_READING_ATTEMPTED_RE = re.compile(r"READING\s+(R-N3-\d+)\s+attempted", re.IGNORECASE)


def parse_reading_attempted(lines: list) -> set:
    return {m.group(1).upper() for line in lines for m in [_READING_ATTEMPTED_RE.search(line)] if m}


# ---- the "already published" map -----------------------------------------
#
# Separate from evidence/repair propagation (which only ever needs day
# N-1 - see jlpt_notion.find_previous_dated_row): knowing which items
# were EVER introduced has no such shortcut. Without this, plan()'s
# ADVANCE phase (whose only "already taught" guard is "not already in
# state["items"]") would have no way to know about items a prior day
# already introduced (before this system started tracking), and would
# eventually re-offer them as brand-new. Cheap and idempotent - just
# regex over property text, no LLM call, and re-processing an
# already-known id is a no-op (membership check).

_ID_SINGLE_RE = re.compile(r"\b([VKG]\d{3})\b")
_ID_RANGE_RE = re.compile(r"\b([VKG])(\d{3})\s*[–\-]\s*(?:[VKG])?(\d{3})\b")


def _extract_ids(text: str) -> set:
    """Every V/K/G id mentioned in a property string, including ids
    implied by a written range like "V011–V020" (only the endpoints are
    spelled out) - not just the ones literally written out."""
    ids = set(_ID_SINGLE_RE.findall(text))
    for letter, start, end in _ID_RANGE_RE.findall(text):
        start_n, end_n = int(start), int(end)
        if 0 <= end_n - start_n <= 100:  # sane range only - ignore garbled/reversed matches
            ids.update(f"{letter}{n:03d}" for n in range(start_n, end_n + 1))
    return ids


def _mark_published(state: dict, catalog: dict, row: dict, date_str: str) -> int:
    """Registers every V/K/G id mentioned in `row`'s Vocabulary/Grammar/
    Kanji text as already-introduced, with NO evidence (mastery stays
    unknown - this only records that it was taught, never how it went).
    Returns how many new ids were added."""
    ids = set()
    for field in ("Vocabulary", "Grammar", "Kanji"):
        ids |= _extract_ids(row.get(field, ""))
    added = 0
    for sid in ids:
        if sid in state["items"]:
            continue
        entry = catalog.get(sid)
        if entry is None:
            continue
        state["items"][sid] = {
            "type": entry["type"], "item": entry["item"], "jlpt_level": entry.get("jlpt_level", "N3"),
            "state": "REVIEW", "introduced_date": date_str, "next_review": date_str,
            "cadence_index": 0, "open_errors": [], "evidence": [],
            "source_id": sid, "first_pass_target": False,
        }
        added += 1
    return added


# ---- lesson content -----------------------------------------------------

def _grouped_by_type(blocks: list) -> dict:
    grouped = {"vocab": [], "grammar": [], "kanji": []}
    for b in blocks:
        grouped[b["type"]].append(b)
    return grouped


def _format_field(blocks: list) -> str:
    if not blocks:
        return "No items scheduled."
    return "; ".join(f"{b['source_id']} {b['item']} ({b['kind']})" for b in blocks)


def _format_lesson_fields(plan_result: dict) -> dict:
    """plan_result -> {"Vocabulary", "Grammar", "Kanji", "Review Task"}
    text, in the same "<ids> <words> (<kind>)" style the existing
    ChatGPT-authored rows already use."""
    grouped = _grouped_by_type(plan_result["blocks"])
    review_task = (
        f"CARRY_FORWARD/REPAIR/REVIEW first, then up to "
        f"{bank.NEW_CEILINGS['vocab']}/{bank.NEW_CEILINGS['grammar']}/{bank.NEW_CEILINGS['kanji']} "
        f"new vocab/grammar/kanji - ~{plan_result['estimated_minutes']} min total. Report actual work."
    )
    return {
        "Vocabulary": _format_field(grouped["vocab"]),
        "Grammar": _format_field(grouped["grammar"]),
        "Kanji": _format_field(grouped["kanji"]),
        "Review Task": review_task,
    }


# Fixed per-block minute caps matching the original ChatGPT/Codex-
# authored rows' own breakdown (Day 03: 15/8/10/7 = 40, the same total
# as jlpt_item_bank.CAPACITY_MINUTES) - presentation-layer only; the
# engine's own capacity accounting (jlpt_item_bank.plan) is unaffected.
BLOCK_MINUTES = {"due_repair": 15, "grammar": 8, "reading": 10, "optional_new": 7}


_READING_ANSWER_RE = re.compile(r"^ANSWER\d*:\s*[A-D]\b", re.IGNORECASE)
_PASSAGE_LABEL_RE = re.compile(r"^PASSAGE:\s*", re.IGNORECASE)


def _split_reading_content(raw: str) -> tuple:
    """(learner_visible_text, answer_key_text) - strips the ANSWER lines
    out of what gets shown in the row body (keeping them only in local
    state, per "keep answers/transcripts separate from first attempts")
    and the leading "PASSAGE:" label the prompt asks the model for."""
    visible, answers = [], []
    for line in raw.splitlines():
        if _READING_ANSWER_RE.match(line.strip()):
            answers.append(line.strip())
        else:
            visible.append(_PASSAGE_LABEL_RE.sub("", line))
    return "\n".join(visible).strip(), "\n".join(answers)


def _get_or_start_reading(state: dict, plan_result: dict) -> dict:
    """Reuses an unattempted reading item from a previous day (cross-day
    carryover) instead of generating a fresh one every run, matching
    "carry unfinished work forward without piling on new assignments."
    Mutates state["reading_bank"] when a new item is generated - caller
    must bank.save_state() afterwards. Answers are kept in state only,
    never written to the Notion row."""
    reading_bank = state.setdefault("reading_bank", [])
    for item in reading_bank:
        if not item.get("attempted"):
            return item

    items_line = ", ".join(b["item"] for b in plan_result["blocks"]) or "basic daily-life vocabulary"
    agent = Agent()
    raw = agent.send(
        "Write one short original JLPT N3-level Japanese reading passage "
        f"(3-5 sentences) naturally using some of these items: {items_line}, "
        "followed by exactly 2 comprehension questions in English, each "
        "with 4 answer choices labeled A-D and one correct answer. Use "
        "exactly this plain-text format, no markdown, no extra commentary:\n"
        "PASSAGE: <passage>\n"
        "Q1: <question>\nA) <opt>\nB) <opt>\nC) <opt>\nD) <opt>\nANSWER1: <letter>\n"
        "Q2: <question>\nA) <opt>\nB) <opt>\nC) <opt>\nD) <opt>\nANSWER2: <letter>"
    )
    visible, answers = _split_reading_content(raw)
    item = {
        "id": f"R-N3-{len(reading_bank) + 1:03d}",
        "visible": visible,
        "answers": answers,
        "attempted": False,
    }
    reading_bank.append(item)
    return item


def _compose_grammar_practice(grammar_blocks: list) -> str:
    """One short contrast/practice block (example sentence(s) + an
    embedded question + a one-line nuance note) for today's grammar
    items, via a single Agent turn - matching Day 03's embedded
    ことにする/ことになる contrast practice, not just a bare id list."""
    if not grammar_blocks:
        return "No grammar scheduled today."
    items_line = "; ".join(f"{b['source_id']} {b['item']}" for b in grammar_blocks)
    agent = Agent()
    return agent.send(
        f"For this JLPT N3 grammar practice set: {items_line}\n"
        "Write ONE short contrast/practice block: one or two example "
        "Japanese sentences using the pattern(s) naturally, one short "
        "embedded question testing whether the learner can tell them "
        "apart or use them correctly, and a one-sentence nuance "
        "explanation. Plain text, no markdown, no headings, concise."
    )


def _format_learner_report(plan_result: dict, reading_item: dict) -> list:
    """The editable self-report checklist - matching Day 03's "Learner
    report — edit this row" section. This is the loop's only real
    mechanism for getting evidence back: the evening branch's
    parse_evidence/parse_reading_attempted read exactly this section
    once the learner has filled it in."""
    grouped = _grouped_by_type(plan_result["blocks"])
    return [
        f"Total minutes: ___ / {plan_result['estimated_minutes']} max",
        "Due reviews/repair: minutes ___; remaining ___",
        f"Grammar ({_format_field(grouped['grammar'])}): report pass/partial/fail per id, e.g. \"G001 pass\"",
        f"Reading {reading_item['id']}: report your answers, or write \"READING {reading_item['id']} attempted\" once done",
        f"New vocabulary studied ({_format_field(grouped['vocab'])}): report pass/partial/fail per id",
        f"New kanji studied ({_format_field(grouped['kanji'])}): report pass/partial/fail per id",
        "Difficulty or reason for stopping: ___",
    ]


def _format_lesson_body(plan_result: dict, reading_item: dict, grammar_practice: str, date_str: str) -> list:
    """(text, style) tuples for the row's page BODY - the same 4-block +
    Listening + Learner-report structure the original ChatGPT/Codex-
    authored rows already use (see Day 03's 29 body blocks), not just
    the short Vocabulary/Grammar/Kanji property text. Each block also
    lists items with their rank-based ids so the evening branch's
    parse_evidence can match reported results back against them."""
    due_repair_review = [b for b in plan_result["blocks"] if b["kind"] in ("CARRY_FORWARD", "REPAIR", "REVIEW")]
    advance = [b for b in plan_result["blocks"] if b["kind"] == "ADVANCE"]

    paragraphs = [
        ("heading", f"Today's assignment — {date_str}"),
        ("paragraph", f"Instructor-generated plan (~{plan_result['estimated_minutes']} min total)."),

        ("heading", f"Block 1 — Due reviews & repair ({BLOCK_MINUTES['due_repair']} minutes maximum)"),
        ("paragraph", _format_field(due_repair_review) if due_repair_review else "Nothing due or in repair today."),

        ("heading", f"Block 2 — Grammar ({BLOCK_MINUTES['grammar']} minutes maximum)"),
        ("paragraph", grammar_practice),

        ("heading", f"Block 3 — Reading practice ({BLOCK_MINUTES['reading']} minutes maximum)"),
        ("paragraph", f"{reading_item['id']} (generated training - not official JLPT material)."),
        ("paragraph", reading_item["visible"]),
        ("paragraph", "Answers kept separate - see instructor feedback after your first attempt."),

        ("heading", f"Block 4 — Optional new items ({BLOCK_MINUTES['optional_new']} minutes maximum)"),
        ("paragraph", _format_field(advance) if advance else "None scheduled today."),

        ("heading", "Listening"),
        ("paragraph", "Audio: pending - not yet verified as accessible for this row. Achievable alternative: read today's grammar/reading sentences aloud once each."),

        ("heading", "Learner report — edit this row"),
        *[("bullet", line) for line in _format_learner_report(plan_result, reading_item)],
        ("paragraph", "Set Status to Done when the capped session is complete, In progress if you stop early, or Not started if you did not study."),
    ]
    style_map = {"heading": "heading_2", "paragraph": "paragraph", "bullet": "bulleted_list_item"}
    return [(text, style_map[kind]) for kind, text in paragraphs]


# ---- morning branch ----------------------------------------------------

def _ingest_row_evidence(state: dict, catalog: dict, row: dict, now_jst: datetime) -> dict:
    """Gathers a row's Instructor Notes + body (the Learner report
    checklist lives there, not a property) + comments, parses tagged
    evidence and reading-attempted markers out of it, and folds both into
    `state` (mutated in place). Returns the parsed reports dict."""
    lines = [row.get("Instructor Notes", "")] + notion.read_body_text(row["page_id"]) + notion.read_comments(row["page_id"])
    reports = parse_evidence(lines)
    bank.ingest_evidence(state, catalog, reports, now_jst)
    for reading_id in parse_reading_attempted(lines):
        for item in state.get("reading_bank", []):
            if item["id"] == reading_id:
                item["attempted"] = True
    return reports


def run_morning(now_jst: datetime) -> str:
    date_str = now_jst.date().isoformat()
    collection_id = os.environ["JLPT_NOTION_COLLECTION_ID"]
    catalog = bank.load_catalog(VOCAB_TSV_GLOB, KANJI_CSV_GLOB, GRAMMAR_CSV_GLOB)
    state = bank.load_state(ITEM_BANK_PATH)
    state.setdefault("items", {})

    # The "already published" map - unlike evidence (below), this needs
    # the whole prior dated history at least once, not just day N-1: it's
    # what stops plan()'s ADVANCE phase from re-offering an item a prior
    # day already introduced before this system existed. Cheap/idempotent
    # (regex over property text, no LLM, no Notion write).
    for prior_row in notion.list_dated_rows_before(collection_id, date_str):
        _mark_published(state, catalog, prior_row, prior_row["date"])

    # Day N only ever needs to check day N-1, not the whole history - once
    # a day's evidence has been folded in (here, or by that day's own
    # evening run), it's already reflected in `state` from then on.
    # `ingested_dates` guards against re-ingesting the same day's evidence
    # twice (which would double up append-only evidence entries) across
    # repeated morning runs/cron ticks.
    ingested_dates = state.setdefault("ingested_dates", [])
    prev_row = notion.find_previous_dated_row(collection_id, date_str)
    if prev_row is not None and prev_row["date"] not in ingested_dates:
        _ingest_row_evidence(state, catalog, prev_row, now_jst)
        ingested_dates.append(prev_row["date"])

    row = notion.find_row_for_date(collection_id, date_str)
    if row is not None:
        # Already created (e.g. an earlier run today) - reconcile whatever's
        # been reported since, same as the evening branch does.
        _ingest_row_evidence(state, catalog, row, now_jst)
    else:
        # A brand-new row, titled to continue the real dated sequence -
        # never a row borrowed from the separate Acquisition template track.
        day_number = notion.next_dated_day_number(collection_id)
        row = notion.create_daily_row(collection_id, date_str, day_number)

    problems = bank.validate(state)
    if problems:
        raise InstructorError(f"item bank failed validation, aborting: {'; '.join(problems)}")

    plan_result = bank.plan(state, catalog, now_jst)
    reading_item = _get_or_start_reading(state, plan_result)
    bank.save_state(ITEM_BANK_PATH, state)

    fields = _format_lesson_fields(plan_result)
    notion.update_row(row["page_id"], date_str=date_str, status="In progress", fields=fields)

    grammar_blocks = [b for b in plan_result["blocks"] if b["type"] == "grammar"]
    grammar_practice = _compose_grammar_practice(grammar_blocks)
    notion.append_body_blocks(
        row["page_id"], _format_lesson_body(plan_result, reading_item, grammar_practice, date_str)
    )

    expect = [fields["Vocabulary"][:40]] if plan_result["blocks"] else [date_str]
    if not notion.verify_row_saved(collection_id, date_str, expect):
        raise InstructorError(f"could not verify today's Notion row was saved ({date_str})")

    page_url = row["url"]
    ledger = load_ledger(EMAIL_LEDGER_PATH)
    if not reserve_email(ledger, date_str):
        return f"[jlpt] {date_str}: Notion row saved and verified; email already {ledger[date_str]['status']} - not resending."
    save_ledger(EMAIL_LEDGER_PATH, ledger)  # PENDING, before the send attempt

    body = (
        f"Today's JLPT plan (~{plan_result['estimated_minutes']} min):\n"
        f"Vocabulary: {fields['Vocabulary']}\n"
        f"Grammar: {fields['Grammar']}\n"
        f"Kanji: {fields['Kanji']}\n"
        f"\nFull lesson: {page_url}"
    )
    result = send_email(
        os.environ.get("JLPT_EMAIL_TO") or os.environ["EMAIL_ADDRESS"],
        f"JLPT study — {date_str}",
        body,
    )
    if result.startswith("Error"):
        raise InstructorError(f"Notion row saved but email send failed: {result}")

    record_sent(ledger, date_str, result, page_url)
    save_ledger(EMAIL_LEDGER_PATH, ledger)
    append_email_delivery_log(date_str, result, page_url)
    return f"[jlpt] {date_str}: morning run complete - {result}"


# ---- evening branch ----------------------------------------------------

def run_evening(now_jst: datetime) -> str:
    date_str = now_jst.date().isoformat()
    collection_id = os.environ["JLPT_NOTION_COLLECTION_ID"]
    catalog = bank.load_catalog(VOCAB_TSV_GLOB, KANJI_CSV_GLOB, GRAMMAR_CSV_GLOB)
    state = bank.load_state(ITEM_BANK_PATH)

    row = notion.find_row_for_date(collection_id, date_str)
    if row is None:
        append_progress_log(date_str, "Evening run: no dated row found for today - nothing to reconcile.")
        return f"[jlpt] {date_str}: no Notion row for today, evening run skipped."

    reports = _ingest_row_evidence(state, catalog, row, now_jst)
    ingested_dates = state.setdefault("ingested_dates", [])
    if date_str not in ingested_dates:
        ingested_dates.append(date_str)  # so tomorrow's day-N-1 check is a no-op
    bank.save_state(ITEM_BANK_PATH, state)

    if reports["items"]:
        feedback_lines = [
            f"{r['source_id']} {r['result']}" + (f" - {r['note']}" if r["note"] else "")
            for r in reports["items"]
        ]
    else:
        feedback_lines = ["No reported evidence found for today - results stay unknown, not assumed mastered."]
    notion.append_instructor_notes(
        row["page_id"], row.get("Instructor Notes", ""),
        [f"{date_str} evening:"] + feedback_lines,
    )
    notion.update_row(row["page_id"], status="Done")

    append_progress_log(date_str, "\n".join(feedback_lines))
    return f"[jlpt] {date_str}: evening run complete - {len(reports['items'])} item(s) reconciled."


# ---- entrypoint -------------------------------------------------------------

def main() -> None:
    missing = [
        name for name in ("NOTION_API_KEY", "JLPT_NOTION_COLLECTION_ID", "GCP_PROJECT_ID")
        if not os.environ.get(name)
    ]
    if missing:
        raise SystemExit(f"Missing required env var(s): {', '.join(missing)}.")

    now_jst = datetime.now(JST)
    branch = determine_branch(now_jst)
    try:
        if branch == "morning":
            print(run_morning(now_jst))
        else:
            print(run_evening(now_jst))
    except InstructorError as exc:
        raise SystemExit(f"[jlpt] {branch} run failed: {exc}")


if __name__ == "__main__":
    main()
