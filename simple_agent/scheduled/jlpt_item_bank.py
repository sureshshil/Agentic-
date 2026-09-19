"""Item-bank engine for the JLPT instructor (jlpt_instructor.py) - a port
of the ChatGPT/Codex "Adaptive V1" design (see
../NOTION-FIRST-JLPT-ARCHITECTURE.md) into this repo. Pure functions, no
network calls, fully unit-testable (tests/test_jlpt_item_bank.py).

This is a SEPARATE state/scheduling layer from srs.py - it does not read
or write .kanji_srs.json / .grammar_srs.json / .vocab_srs.json, and the
Telegram drips (kanji_drip.py etc.) are untouched by anything here. Two
independent consumers of the same source CSV/TSV files, each owning its
own state, is deliberate: letting the Telegram drip's Leitner state also
drive the instructor would make it a second decision-maker, exactly the
"Anki as second scheduler" problem the ported spec was designed to avoid.

Item schema (one record per source_id in the state dict):
  {
    "type": "vocab" | "grammar" | "kanji",
    "item": "<headword/pattern/character>",
    "jlpt_level": "N3",
    "state": "LEARNING" | "REVIEW" | "REPAIR" | "DONE",
    "introduced_date": "YYYY-MM-DD",
    "next_review": "YYYY-MM-DD",
    "cadence_index": 0,             # index into CADENCE_DAYS
    "open_errors": ["..."],
    "evidence": [{"date": "...", "result": "pass|partial|fail|unknown",
                   "note": "..."}, ...],   # APPENDED to, never replaced
    "source_id": "<same as the dict key>",
    "first_pass_target": true,      # still on its very first exposure
  }

Hard invariants (enforced by validate() / ingest_evidence()):
  - An item is NEW STUDY exactly once: the moment it's first introduced it
    gets a record here and is never "NEW" again - later reappearance is
    REVIEW, REPAIR, or context, never a second introduction.
  - "Study complete" != "Mastered": ingest_evidence() only ever advances
    state from explicit, named per-item evidence. An aggregate session
    count (e.g. "8 new cards done") updates completion bookkeeping only
    (see `session` in ingest_evidence) and never infers which items, if
    any, were retained. MASTERED is real too, not just a schema value
    nobody reaches: a "pass" recorded while already at the top cadence
    step graduates the item to state DONE - it stops being reviewed at
    all rather than cycling at the 30-day interval forever.
  - Missing evidence stays "unknown" - it's appended as such rather than
    silently skipped, so a gap is visible in the record instead of looking
    like nothing happened.

Review cadence, in days, advanced/shortened by evidence quality:
  CADENCE_DAYS = [1, 3, 7, 14, 30]
"""

import csv
import glob
import json
import os
from datetime import date, datetime, timedelta, timezone

CADENCE_DAYS = [1, 3, 7, 14, 30]

# New-item introduction is a CEILING, not a quota - unfinished/repair work
# consumes capacity first; plan() may introduce zero new items on a busy day.
NEW_CEILINGS = {"vocab": 10, "grammar": 3, "kanji": 3}

CAPACITY_MINUTES = 40
# Minutes each item type costs to study/review once - drives how many
# items fit inside CAPACITY_MINUTES's shared budget. Replaces a flat
# per-day item-count cap (a prior MAX_BLOCKS=3 total across ALL of
# CARRY_FORWARD+REPAIR+REVIEW+ADVANCE combined), which under-delivered
# relative to the ported design's own "10 vocab + 3 grammar + 3 kanji"
# normal-day target - at these costs, a fresh day with nothing due or in
# repair spends ~15 + 12 + 7.5 = 34.5 of the 35 working minutes (after
# RETRIEVAL_PROBE_MINUTES) introducing almost exactly that many new items,
# not just 3 total regardless of type.
MINUTES_PER_ITEM = {"vocab": 1.5, "kanji": 2.5, "grammar": 4}
MAX_REPAIR_TARGETS = 2
RETRIEVAL_PROBE_MINUTES = 5

# How many of an item's most recent evidence entries in a row have to be
# "fail" before plan() flags it for explicit intervention rather than just
# another REPAIR pass.
REPEATED_FAIL_THRESHOLD = 2

STATES = ("LEARNING", "REVIEW", "REPAIR", "DONE")
RESULTS = ("pass", "partial", "fail", "unknown")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def today_str(now: datetime) -> str:
    return now.date().isoformat()


# ---- catalog: the addressable universe of items --------------------------
#
# Source IDs are RANK-BASED (V011, K004, G002), not word/character text -
# this matches the id scheme already used on the live "JLPT Daily Plan"
# Notion database (its Vocabulary/Grammar/Kanji property text already
# reads "V011-V020", "K004", "G002 ことにする" etc. from the original
# ChatGPT/Codex project), so jlpt_notion.py / jlpt_instructor.py can parse
# reported evidence straight off those existing conventions instead of
# inventing a new one the user would have to relearn.


def _load_vocab_rows(glob_pattern: str) -> dict:
    """rank (int) -> row dict, from the Anki-style TSVs ('#columns:'
    header line then tab-separated data) - same parsing as
    vocab_drip.py.load_rows, keyed by the TSV's own 'rank' column rather
    than file order, since that's the source of truth for word ordering."""
    rows: dict = {}
    for path in sorted(glob.glob(glob_pattern)):
        columns = None
        data_lines = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("#columns:"):
                    columns = line.rstrip("\n")[len("#columns:"):].split("\t")
                elif line.strip() and not line.startswith("#"):
                    data_lines.append(line)
        if columns is None:
            continue
        for values in csv.reader(data_lines, delimiter="\t", quoting=csv.QUOTE_NONE):
            row = dict(zip(columns, values))
            word = (row.get("word") or "").strip()
            rank_str = (row.get("rank") or "").strip()
            if word and rank_str.isdigit():
                rows[int(rank_str)] = row
    return rows


def _load_csv_rows_ranked(glob_pattern: str, key_field: str) -> dict:
    """1-based rank (by file-sorted row order, since kanji/grammar CSVs
    have no explicit rank column of their own - the batch files are
    already named/numbered sequentially) -> row dict."""
    rows: dict = {}
    rank = 0
    for path in sorted(glob.glob(glob_pattern)):
        with open(path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                key = (row.get(key_field) or "").strip()
                if not key:
                    continue
                rank += 1
                rows[rank] = row
    return rows


def load_catalog(
    vocab_glob: str,
    kanji_glob: str,
    grammar_glob: str,
) -> dict:
    """source_id ("V011"/"K004"/"G002") -> {"type", "item", "jlpt_level",
    "row"} for every item across all three decks. `row` is the raw
    CSV/TSV dict, kept as-is for content generation (jlpt_instructor.py
    reads its fields directly)."""
    catalog: dict = {}
    for rank, row in _load_vocab_rows(vocab_glob).items():
        sid = f"V{rank:03d}"
        catalog[sid] = {"type": "vocab", "item": (row.get("word") or "").strip(), "jlpt_level": "N3", "row": row}
    for rank, row in _load_csv_rows_ranked(kanji_glob, "kanji").items():
        sid = f"K{rank:03d}"
        catalog[sid] = {"type": "kanji", "item": (row.get("kanji") or "").strip(), "jlpt_level": row.get("jlpt") or "N3", "row": row}
    for rank, row in _load_csv_rows_ranked(grammar_glob, "grammar").items():
        sid = f"G{rank:03d}"
        catalog[sid] = {"type": "grammar", "item": (row.get("grammar") or "").strip(), "jlpt_level": "N3", "row": row}
    return catalog


# ---- state I/O -------------------------------------------------------------

def load_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"items": {}, "pending": [], "sessions": []}


def save_state(path: str, state: dict) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


# ---- validation ------------------------------------------------------------

def validate(state: dict) -> list:
    """Schema/invariant problems, as a list of strings - never raises, so
    the caller (jlpt_instructor.py) can decide whether to proceed. Empty
    list means valid."""
    problems = []
    if "items" not in state or not isinstance(state["items"], dict):
        problems.append("state missing 'items' dict")
        return problems

    for sid, rec in state["items"].items():
        if rec.get("source_id") != sid:
            problems.append(f"{sid}: source_id mismatch ({rec.get('source_id')!r})")
        if rec.get("type") not in ("vocab", "grammar", "kanji"):
            problems.append(f"{sid}: invalid type {rec.get('type')!r}")
        if rec.get("state") not in STATES:
            problems.append(f"{sid}: invalid state {rec.get('state')!r}")
        if not rec.get("introduced_date"):
            problems.append(f"{sid}: missing introduced_date (NEW-once invariant needs this set)")
        evidence = rec.get("evidence")
        if not isinstance(evidence, list):
            problems.append(f"{sid}: evidence must be a list")
        else:
            for i, entry in enumerate(evidence):
                if entry.get("result") not in RESULTS:
                    problems.append(f"{sid}: evidence[{i}] invalid result {entry.get('result')!r}")
    return problems


# ---- evidence ingestion -----------------------------------------------------

def _shift_cadence(cadence_index: int, result: str) -> int:
    if result == "pass":
        return min(cadence_index + 1, len(CADENCE_DAYS) - 1)
    if result == "partial":
        return max(cadence_index - 1, 0)
    # "fail" resets to REPAIR territory (handled by caller); "unknown"
    # leaves cadence untouched entirely.
    return cadence_index


def _recent_fail_streak(evidence: list) -> int:
    streak = 0
    for entry in reversed(evidence):
        if entry.get("result") == "fail":
            streak += 1
        elif entry.get("result") in ("pass", "partial"):
            break
    return streak


def ingest_evidence(state: dict, catalog: dict, reports: dict, now: datetime) -> dict:
    """Merge ACTUAL reported evidence into `state` (mutated in place, also
    returned). `reports` shape:
      {
        "items": [{"source_id": "...", "result": "pass|partial|fail|unknown",
                    "note": "..."}, ...],
        "session": {"new_cards_done": 7, "note": "..."},   # optional, aggregate-only
      }
    Per-item evidence is APPENDED (never overwrites prior evidence) and
    only named, explicit results move an item's state/next_review/cadence.
    `session` only updates state["sessions"] bookkeeping - it never infers
    that any specific item was studied, retained, or mastered."""
    state.setdefault("items", {})
    state.setdefault("pending", [])
    state.setdefault("sessions", [])
    today = today_str(now)

    for report in reports.get("items", []):
        sid = report.get("source_id")
        result = report.get("result") or "unknown"
        if result not in RESULTS:
            result = "unknown"
        rec = state["items"].get(sid)
        if rec is None:
            catalog_entry = catalog.get(sid)
            if catalog_entry is None:
                continue  # unknown item id - nothing to attach evidence to
            rec = {
                "type": catalog_entry["type"],
                "item": catalog_entry["item"],
                "jlpt_level": catalog_entry.get("jlpt_level", "N3"),
                "state": "LEARNING",
                "introduced_date": today,
                "next_review": today,
                "cadence_index": 0,
                "open_errors": [],
                "evidence": [],
                "source_id": sid,
                "first_pass_target": True,
            }
            state["items"][sid] = rec

        rec["evidence"].append(
            {"date": now.isoformat(), "result": result, "note": report.get("note", "")}
        )
        rec["first_pass_target"] = False
        if sid in state["pending"]:
            state["pending"].remove(sid)

        if result == "unknown":
            continue  # explicitly preserved as unknown - no state change

        if result == "fail":
            rec["state"] = "REPAIR"
            rec["cadence_index"] = 0
            rec["next_review"] = today
            if report.get("note"):
                rec["open_errors"].append(report["note"])
            if _recent_fail_streak(rec["evidence"]) >= REPEATED_FAIL_THRESHOLD:
                rec["open_errors"].append("NEEDS_INTERVENTION")
        else:  # pass / partial
            was_at_top_cadence = rec.get("cadence_index", 0) == len(CADENCE_DAYS) - 1
            rec["cadence_index"] = _shift_cadence(rec.get("cadence_index", 0), result)
            if result == "pass" and was_at_top_cadence:
                # Mastered: a "pass" recorded while already at the longest
                # cadence step graduates the item out of rotation entirely,
                # rather than cycling it at the 30-day interval forever.
                rec["state"] = "DONE"
                rec.pop("next_review", None)
            else:
                rec["state"] = "REVIEW"
                next_review_date = now.date() + timedelta(days=CADENCE_DAYS[rec["cadence_index"]])
                rec["next_review"] = next_review_date.isoformat()
            if result == "pass" and rec.get("open_errors"):
                rec["open_errors"] = [e for e in rec["open_errors"] if e != "NEEDS_INTERVENTION"]

    session = reports.get("session")
    if session:
        state["sessions"].append({"date": today, **session})

    return state


# ---- planning ----------------------------------------------------------

def _item_cost(item_type: str) -> float:
    return MINUTES_PER_ITEM.get(item_type, 1)


def plan(state: dict, catalog: dict, now: datetime) -> dict:
    """Bounded today's-lesson selection, priority CARRY_FORWARD > REPAIR >
    REVIEW > ADVANCE. Each phase spends from ONE shared CAPACITY_MINUTES
    budget at a per-item-type cost (MINUTES_PER_ITEM) - not a flat
    per-day item-COUNT cap - so e.g. many cheap due vocab reviews don't
    starve out expensive-but-important grammar repair, and a fresh day
    with nothing due can actually reach NEW_CEILINGS's full "10 vocab +
    3 grammar + 3 kanji" normal-day target instead of stopping at some
    fixed number of items regardless of type. Does not mutate `state`
    except to record the newly selected block's source_ids into
    state["pending"] (cleared by ingest_evidence once evidence comes in)
    and to register brand-new ADVANCE picks as LEARNING records (their
    one-and-only NEW STUDY). Returns a plain dict describing the lesson -
    jlpt_instructor.py is responsible for turning it into Notion content."""
    state.setdefault("items", {})
    state.setdefault("pending", [])
    today = today_str(now)
    items = state["items"]

    blocks = []
    repair_targets = []
    used_minutes = RETRIEVAL_PROBE_MINUTES

    def remaining_minutes():
        return CAPACITY_MINUTES - used_minutes

    def add_block(sid, kind):
        rec = items.get(sid)
        if rec is None:
            return False
        blocks.append({"kind": kind, "source_id": sid, "item": rec["item"], "type": rec["type"]})
        return True

    # 1. CARRY_FORWARD - selected last time, still no evidence.
    for sid in list(state["pending"]):
        rec = items.get(sid)
        if rec is None:
            continue
        cost = _item_cost(rec["type"])
        if remaining_minutes() < cost:
            break
        if add_block(sid, "CARRY_FORWARD"):
            used_minutes += cost

    # 2. REPAIR - failed items, capped at MAX_REPAIR_TARGETS (breadth is
    #    capped even though there'd be budget for more - repair benefits
    #    from a few items drilled properly, not as many as time allows).
    repair_candidates = sorted(
        sid for sid, rec in items.items()
        if rec.get("state") == "REPAIR" and sid not in {b["source_id"] for b in blocks}
    )
    for sid in repair_candidates:
        if len(repair_targets) >= MAX_REPAIR_TARGETS:
            break
        cost = _item_cost(items[sid]["type"])
        if remaining_minutes() < cost:
            break
        if add_block(sid, "REPAIR"):
            repair_targets.append(sid)
            used_minutes += cost

    # 3. REVIEW - due by cadence.
    review_candidates = sorted(
        sid for sid, rec in items.items()
        if rec.get("state") == "REVIEW"
        and rec.get("next_review", "9999-99-99") <= today
        and sid not in {b["source_id"] for b in blocks}
    )
    for sid in review_candidates:
        cost = _item_cost(items[sid]["type"])
        if remaining_minutes() < cost:
            break
        if add_block(sid, "REVIEW"):
            used_minutes += cost

    # 4. ADVANCE - brand-new items, ceilings not quotas; only fills
    #    whatever budget is left after carry-forward/repair/review.
    #    Candidates are drawn ROUND-ROBIN across types (not a flat sort
    #    over all source_ids) - "V..." < "K..." < "G..." alphabetically
    #    would otherwise let vocab silently starve grammar/kanji (or vice
    #    versa) whenever remaining budget is scarce, since a plain
    #    sorted() walk exhausts one type's candidates before touching the
    #    next.
    new_items = {"vocab": [], "grammar": [], "kanji": []}
    introduced_today = {
        t: sum(
            1 for rec in items.values()
            if rec["type"] == t and rec.get("introduced_date") == today
        )
        for t in NEW_CEILINGS
    }

    candidates_by_type = {t: [] for t in NEW_CEILINGS}
    for sid, entry in sorted(catalog.items()):
        if sid in items:
            continue  # NEW STUDY happens once - already introduced (or in progress)
        candidates_by_type.setdefault(entry["type"], []).append((sid, entry))

    cheapest_item_cost = min(MINUTES_PER_ITEM.values())
    progressed = True
    while progressed and remaining_minutes() >= cheapest_item_cost:
        progressed = False
        for t in ("vocab", "kanji", "grammar"):
            cost = _item_cost(t)
            if remaining_minutes() < cost:
                continue
            if introduced_today[t] >= NEW_CEILINGS[t] or not candidates_by_type[t]:
                continue
            sid, entry = candidates_by_type[t].pop(0)
            items[sid] = {
                "type": t,
                "item": entry["item"],
                "jlpt_level": entry.get("jlpt_level", "N3"),
                "state": "LEARNING",
                "introduced_date": today,
                "next_review": today,
                "cadence_index": 0,
                "open_errors": [],
                "evidence": [],
                "source_id": sid,
                "first_pass_target": True,
            }
            new_items[t].append(sid)
            introduced_today[t] += 1
            blocks.append({"kind": "ADVANCE", "source_id": sid, "item": entry["item"], "type": t})
            used_minutes += cost
            progressed = True

    state["pending"] = [b["source_id"] for b in blocks]

    return {
        "date": today,
        "blocks": blocks,
        "repair_targets": repair_targets,
        "new_items": new_items,
        "estimated_minutes": min(used_minutes, CAPACITY_MINUTES),
    }
