"""Shared Leitner-style spaced-repetition engine for kanji_drip.py and
grammar_drip.py, and for telegram_bot.py's inline-button callback handler
that records your Again/Hard/Good/Easy taps.

Why this exists (vs. vocab_drip.py's plain round-robin): vocab_drip just
cycles every word through in rank order, then least-recently-sent. That's
fine for a passive read-and-forget drip, but kanji/grammar drips are meant
to be an active-recall habit - one hourly kanji, spoiler-hidden until you
tap it, with a Leitner "box" per item that decides when it's due again
based on how you rated your own recall. Reviews that are actually due take
priority every run; only once none are due does a new item get introduced
(capped per day so the due-queue can't snowball past what an hourly/
2-hourly cadence can actually clear).

State shape (one JSON file per deck, e.g. .kanji_srs.json):
  {
    "<item key>": {
      "box": 2,                     index into a BOX_HOURS_* table
      "reps": 3,                    total reviews (any rating)
      "lapses": 1,                  how many times rated "again"
      "introduced_at": "<iso8601>", set once, when first sent
      "first_seen_at": "<iso8601>", set once, the first time the card was
                                     actually opened (webapp.py's GET
                                     /review - see mark_seen); absent if
                                     it's been sent but never opened
      "last_result": "good",        most recent rating
      "last_reviewed": "<iso8601>", set on every rating
      "due": "<iso8601>",           set on every rating; absent until the
                                     first rating comes in (an
                                     introduced-but-not-yet-rated item is
                                     never "due" again on its own)
      "notes": [{"note": "...", "at": "<iso8601>"}, ...]   optional, most
                                     recent 10 kept - see add_note(). Free-
                                     text context (e.g. what the user
                                     confused this item with) attached by
                                     telegram_bot.py's srs_record_review
                                     tool when the chat agent quizzes the
                                     user directly, so a repeated mix-up is
                                     visible on the item itself rather than
                                     evaporating into chat history.
      "history": [{"at": "<iso8601>", "action": "good", "box": 3}, ...]
                                     one entry per rating, appended by
                                     record_review, most recent 30 kept per
                                     item (same bounded-growth pattern as
                                     "notes" - box/reps/lapses are already
                                     the permanent lifetime counters; this
                                     is only for recent-trend reporting, so
                                     it doesn't need to be unbounded). Feeds
                                     review_events/accuracy_trend/
                                     current_streak below - absent entirely
                                     on items reviewed before this field was
                                     added, which those functions treat the
                                     same as "no history yet".
    },
    ...
  }

Box tables are hours-until-next-review at each box, tuned to each deck's
cadence (kanji hourly, grammar every 2h) so a fresh item's first review
lands roughly one cadence step later, not days out.
"""

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ACTIONS = ("again", "hard", "good", "easy")
LABELS = {"again": "Again", "hard": "Hard", "good": "Good", "easy": "Easy"}

# Index 0 = "just got it wrong" -> back to (near) square one. Growth is
# roughly the classic SM-2 ~2-2.5x per successful box, capped around a
# month so a mastered item still resurfaces occasionally.
BOX_HOURS_KANJI = [1, 3, 8, 20, 48, 120, 300, 720]
BOX_HOURS_GRAMMAR = [2, 6, 16, 40, 96, 240, 600, 1440]
# Same 2h push cadence as grammar - vocab_drip.py just sends several
# words per push instead of one, via pick_batch() below.
BOX_HOURS_VOCAB = [2, 6, 16, 40, 96, 240, 600, 1440]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def format_interval(hours: float) -> str:
    if hours < 24:
        return f"{hours:g}h"
    days = hours / 24
    return f"{days:g}d"


def load_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path: str, state: dict) -> None:
    # Atomic write - telegram_bot.py's long-running callback handler and a
    # short-lived cron drip script could otherwise race on this file.
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def _introduced_today_count(state: dict, today: str) -> int:
    return sum(
        1 for rec in state.values() if rec.get("introduced_at", "")[:10] == today
    )


def mark_seen(state: dict, key: str, now: datetime) -> bool:
    """Records that `key`'s card was actually opened (webapp.py's GET
    /review, the first time it renders that key) - gates pick_next's
    unrated-resurface reminder so an introduced-but-never-opened item
    doesn't get silently re-pushed just because time passed; it only
    resurfaces after you've actually looked at it once. A no-op if
    already recorded (first open only) or if `key` isn't in `state` at
    all. Returns whether it actually set anything, so callers only need
    to persist state when something changed."""
    rec = state.get(key)
    if rec is None or rec.get("first_seen_at"):
        return False
    rec["first_seen_at"] = now.isoformat()
    return True


def pick_next(
    rank_ordered_keys: list,
    state: dict,
    now: datetime,
    new_per_day: int,
    unrated_resurface_hours: float = 3.0,
):
    """The next item key to send this run, and whether it's a brand-new
    introduction (True) or a due/reminder pick (False). Mutates + returns
    `state` unchanged for a due/reminder pick; for a new pick it adds the
    item's initial record (box 0, introduced_at = now) so the caller just
    needs to save. Returns (None, None) if nothing is due and today's
    new-item cap (by UTC date) is already used up.

    An item that was introduced but never rated (no "due" yet - you never
    tapped a button) would otherwise vanish forever and its new-item slot
    would've been wasted for nothing. So it's treated as due again
    `unrated_resurface_hours` after you first actually opened it
    (mark_seen's first_seen_at - NOT introduced_at), same priority as an
    ordinary overdue review - the caller can tell the two apart by
    checking whether state[key]["due"] is set. If it's never been opened
    at all, it never resurfaces on a timer - it just waits, since
    re-pushing something you haven't even looked at yet would just be
    noise, not a helpful reminder."""
    now_iso = now.isoformat()
    resurface_cutoff_iso = (now - timedelta(hours=unrated_resurface_hours)).isoformat()

    def effective_due(key):
        rec = state[key]
        due = rec.get("due")
        if due:
            return due if due <= now_iso else None
        first_seen = rec.get("first_seen_at")
        return first_seen if first_seen and first_seen <= resurface_cutoff_iso else None

    due = [key for key in rank_ordered_keys if key in state and effective_due(key)]
    if due:
        due.sort(key=effective_due)
        return due[0], False

    today = now.date().isoformat()
    if _introduced_today_count(state, today) < new_per_day:
        for key in rank_ordered_keys:
            if key not in state:
                state[key] = {
                    "box": 0,
                    "reps": 0,
                    "lapses": 0,
                    "introduced_at": now_iso,
                }
                return key, True

    return None, None


def pick_batch(
    rank_ordered_keys: list,
    state: dict,
    now: datetime,
    batch_size: int,
    new_per_day: int,
    unrated_resurface_hours: float = 3.0,
) -> list:
    """Up to `batch_size` (key, is_new) picks for one push - vocab_drip.py's
    equivalent of pick_next, sized for "several words per push" instead of
    one card at a time. Repeats pick_next, excluding keys already picked
    THIS run so a single due item can't be picked twice in one batch;
    otherwise identical due-before-new priority and the same new_per_day
    cap (checked fresh each iteration, so it still holds across the whole
    batch, not just per call). Stops early - returning fewer than
    batch_size - once nothing is left to send this run, same as pick_next
    returning (None, None)."""
    picks = []
    excluded = set()
    for _ in range(batch_size):
        candidates = [key for key in rank_ordered_keys if key not in excluded]
        key, is_new = pick_next(candidates, state, now, new_per_day, unrated_resurface_hours)
        if key is None:
            break
        picks.append((key, is_new))
        excluded.add(key)
    return picks


def record_review(state: dict, key: str, action: str, box_hours: list, now: datetime) -> float:
    """Apply a rating to `key` (mutates `state` in place) and return the
    resulting interval in hours until it's next due."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action: {action!r}")

    rec = state.setdefault(key, {"box": 0, "reps": 0, "lapses": 0})
    rec.setdefault("introduced_at", now.isoformat())
    box = rec.get("box", 0)
    top = len(box_hours) - 1

    if action == "again":
        box = 0
        rec["lapses"] = rec.get("lapses", 0) + 1
    elif action == "hard":
        box = max(0, box - 1)
    elif action == "good":
        box = min(box + 1, top)
    elif action == "easy":
        box = min(box + 2, top)

    interval_hours = box_hours[box]
    rec["box"] = box
    rec["reps"] = rec.get("reps", 0) + 1
    rec["last_result"] = action
    rec["last_reviewed"] = now.isoformat()
    rec["due"] = (now + timedelta(hours=interval_hours)).isoformat()
    history = rec.setdefault("history", [])
    history.append({"at": now.isoformat(), "action": action, "box": box})
    del history[:-30]
    return interval_hours


def add_note(state: dict, key: str, note: str, now: datetime) -> bool:
    """Attaches a free-text note to an EXISTING item (e.g. "confused with
    怠い" from a chat-driven quiz - see telegram_bot.py's srs_record_review
    tool). Returns False without mutating anything if `key` isn't already
    in `state` - notes annotate a real item, they never create one. Keeps
    only the most recent 10 notes per item so this can't grow unbounded."""
    rec = state.get(key)
    if rec is None:
        return False
    notes = rec.setdefault("notes", [])
    notes.append({"note": note, "at": now.isoformat()})
    del notes[:-10]
    return True


def due_items(state: dict, now: datetime, limit: int = 5) -> list:
    """Up to `limit` item keys currently due for review, most overdue
    first - the same "due" notion pick_next uses, but listing candidates
    instead of picking one, for telegram_bot.py's on-demand quiz tool."""
    now_iso = now.isoformat()
    due = [key for key, rec in state.items() if rec.get("due") and rec["due"] <= now_iso]
    due.sort(key=lambda k: state[k]["due"])
    return due[:limit]


def weakest_items(state: dict, limit: int = 5) -> list:
    """Up to `limit` item keys with at least one lapse, worst first (most
    lapses, then lowest box) - so there's always something worth quizzing
    even when nothing is strictly due yet."""
    candidates = [(key, rec) for key, rec in state.items() if rec.get("lapses", 0) > 0]
    candidates.sort(key=lambda kv: (-kv[1].get("lapses", 0), kv[1].get("box", 0)))
    return [key for key, _rec in candidates[:limit]]


def review_candidates(state: dict, now: datetime, limit: int = 5) -> list:
    """Due items first (most overdue first), then - if there's still room -
    the highest-lapse items not already included. Backs telegram_bot.py's
    srs_due_items tool: "what should I quiz the user on right now."""
    picks = due_items(state, now, limit)
    if len(picks) < limit:
        seen = set(picks)
        for key in weakest_items(state, limit * 2):
            if key in seen:
                continue
            picks.append(key)
            seen.add(key)
            if len(picks) >= limit:
                break
    return picks


def deck_stats(state: dict, now: datetime) -> dict:
    """A quick progress summary for one deck - total items introduced, how
    many are due right now, lifetime lapse count, and a box->count
    histogram. Backs telegram_bot.py's srs_deck_stats tool and /progress
    command."""
    now_iso = now.isoformat()
    box_counts: dict = {}
    total_lapses = 0
    due_now = 0
    for rec in state.values():
        box = rec.get("box", 0)
        box_counts[box] = box_counts.get(box, 0) + 1
        total_lapses += rec.get("lapses", 0)
        due = rec.get("due")
        if due and due <= now_iso:
            due_now += 1
    return {
        "total": len(state),
        "due_now": due_now,
        "box_counts": box_counts,
        "total_lapses": total_lapses,
    }


def review_events(state: dict) -> list:
    """Every recorded rating across all items in one deck's state, as flat
    {"key", "at", "action", "box"} dicts - only as far back as each item's
    capped "history" list reaches (see record_review). Backs
    accuracy_trend/current_streak below."""
    return [
        {"key": key, **entry}
        for key, rec in state.items()
        for entry in rec.get("history", [])
    ]


def accuracy_trend(states: list, now: datetime, days: int = 14) -> list:
    """[{"date", "reviews", "correct", "accuracy"}] for each of the last
    `days` days (oldest first, today included), merged across `states` -
    pass a single deck's state in a one-element list, or several to
    combine them (e.g. kanji+grammar+vocab). "correct" counts "good"/
    "easy" ratings; `accuracy` is that as a 0-100 percentage, or None on a
    day with no reviews at all (so callers can render "no reviews" instead
    of a misleading 0%)."""
    buckets: dict = defaultdict(lambda: {"reviews": 0, "correct": 0})
    for state in states:
        for ev in review_events(state):
            bucket = buckets[ev["at"][:10]]
            bucket["reviews"] += 1
            if ev["action"] in ("good", "easy"):
                bucket["correct"] += 1

    today = now.date()
    out = []
    for i in range(days - 1, -1, -1):
        date = (today - timedelta(days=i)).isoformat()
        b = buckets.get(date, {"reviews": 0, "correct": 0})
        accuracy = (b["correct"] / b["reviews"] * 100) if b["reviews"] else None
        out.append({"date": date, "reviews": b["reviews"], "correct": b["correct"], "accuracy": accuracy})
    return out


def current_streak(states: list, now: datetime) -> int:
    """Consecutive days, ending today and counting backward, with at least
    one review recorded in ANY of `states` - 0 if today has no review yet
    (a day with no review at all breaks the streak, even if "today" isn't
    over - same semantics as any other daily-streak counter)."""
    reviewed_dates = {ev["at"][:10] for state in states for ev in review_events(state)}
    streak = 0
    day = now.date()
    while day.isoformat() in reviewed_dates:
        streak += 1
        day -= timedelta(days=1)
    return streak
