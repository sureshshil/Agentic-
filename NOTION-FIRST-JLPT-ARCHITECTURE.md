# Notion-first JLPT instructor architecture

## Background

This is a port of a ChatGPT/Codex "cowork" project
(`continue-my-jlpt-n3-n2-project`) into this repo, after it hit ChatGPT's
cost limit. That project ran a JLPT N3/N2 adaptive-instructor workflow
twice daily (07:00 / 23:00 Asia/Tokyo): read a Notion daily-plan page,
reconcile it against local evidence, generate one dated Notion lesson row
(vocabulary/grammar/kanji with source IDs and time caps), send one Gmail
summary per Japan-date (morning only), and in the evening ingest reported
results without emailing again. The project's own later architectural
decision (captured here rather than lost) was to make the **instructor**,
not Anki, the sole owner of INTRODUCE/REVIEW/REPAIR/ADVANCE decisions -
Anki's own scheduler had become a second decision-maker and caused a
G02/G14 item mismatch.

The implementation living in this repo (`simple_agent/scheduled/
jlpt_item_bank.py`, `jlpt_notion.py`, `jlpt_instructor.py`) is a fresh,
separate module - it does not modify or depend on `kanji_sinks.py` /
`grammar_sinks.py` / `vocab_sinks.py` or the Telegram SRS drips
(`kanji_drip.py` / `grammar_drip.py` / `vocab_drip.py`). See
`simple_agent/deploy/cron-notifications.md`'s "JLPT adaptive instructor"
section for env vars and cron setup.

## Why a second, independent scheduler (not srs.py)

This repo already has a Leitner-style scheduler (`srs.py`) behind the
Telegram drips, reading the same `N3_vocab_batch*.tsv` /
`N3_kanji_batch*.csv` / `n3_grammar_batch*.csv` source files. It would be
tempting to have the instructor read `.kanji_srs.json` etc. directly -
but that's exactly the "second decision-maker" problem the original
project's pivot away from Anki was about: the Telegram drip's box/due
logic optimizes for a passive push-and-rate habit, not for a bounded,
prioritized daily lesson plan. `jlpt_item_bank.py` therefore owns its own
state (`.jlpt_item_bank.json`), completely separate from `.kanji_srs.json`
/ `.grammar_srs.json` / `.vocab_srs.json`. Both consume the same source
CSV/TSVs; neither reads the other's state. An item you've seen via the
Telegram drip is not "introduced" as far as the instructor is concerned,
and vice versa - they are two independent study surfaces over the same
catalog.

## Item bank schema

One record per `source_id` - **rank-based** (`"V011"` / `"K004"` /
`"G002"`, not word/character text), matching the id convention already
used in the live Notion database's `Vocabulary`/`Grammar`/`Kanji`
property text (e.g. `"3 new cards: K004 増; K005 減; K006 選"`) - in
`.jlpt_item_bank.json`:

| Field | Meaning |
|---|---|
| `type` | `vocab` \| `grammar` \| `kanji` |
| `item` | the headword/character/pattern |
| `jlpt_level` | e.g. `"N3"` |
| `state` | `LEARNING` \| `REVIEW` \| `REPAIR` \| `DONE` |
| `introduced_date` | set once, the day it was first taught |
| `next_review` | when it's next due, by the cadence table below |
| `cadence_index` | index into `CADENCE_DAYS`, advanced/shortened by evidence |
| `open_errors` | free-text notes plus `"NEEDS_INTERVENTION"` on repeated fail |
| `evidence` | append-only list of `{date, result, note}` |
| `source_id` | same as the dict key |
| `first_pass_target` | true until the item's first evidence comes in |

Each item page conceptually separates **LEARN THIS** / **UNDERSTAND BUT
DON'T MEMORIZE** / **OPTIONAL** content, per the original project's
example (方法 as a word to learn plus the `V-る方法` pattern to
understand-not-memorize; 増 taught as the core "increase" concept plus
増える/増やす, without requiring every on/kun reading). In this port that
distinction lives in the lesson content the LLM composes from each
catalog row (`jlpt_item_bank.load_catalog`'s `row` field carries the
full curated CSV/TSV row - mnemonic, nuance, contrast, trap columns, etc.
- exactly as `kanji_drip.py`/`grammar_drip.py` already use it), not as a
separate schema field.

## Hard invariants

- **Study complete != Mastered.** `ingest_evidence()`'s `session`
  aggregate (e.g. "8 new cards done") only ever updates session
  bookkeeping (`state["sessions"]`) - it never infers that any specific
  item was studied, retained, or mastered. Only named, explicit per-item
  evidence moves that item's `state`/`next_review`.
- **NEW STUDY happens once.** The moment `plan()` introduces an item
  (`ADVANCE`), it gets a record and can never be re-introduced - later
  reappearance is `REVIEW`, `REPAIR`, or context only. Enforced by
  `plan()` skipping any `source_id` already present in `state["items"]`.
- **UNKNOWN is preserved, not dropped.** A report with no clear result
  is appended as `"unknown"` evidence rather than silently ignored, and
  it changes nothing about the item's state - a missing report reads as
  "we don't know," never as an implicit pass.
- **Evidence is append-only.** `ingest_evidence()` never replaces or
  truncates `evidence[]`; corrections are new entries, not edits.

## Review cadence

`CADENCE_DAYS = [1, 3, 7, 14, 30]`, modified by evidence quality:

- **pass** -> advance one step (shorter -> longer interval)
- **partial** -> shorten one step
- **fail** -> reset to `REPAIR`, `cadence_index = 0`; two fails in a row
  appends `"NEEDS_INTERVENTION"` to `open_errors` instead of just queuing
  another repair pass

## Daily planning: CARRY_FORWARD > REPAIR > REVIEW > ADVANCE

`plan()` fills up to `MAX_BLOCKS` (3) lesson blocks in strict priority
order, bounded to `CAPACITY_MINUTES` (40, including a fixed 5-minute
retrieval-probe allowance) and at most `MAX_REPAIR_TARGETS` (2) repair
items:

1. **CARRY_FORWARD** - items selected in the previous plan that never got
   evidence back (`state["pending"]`, cleared by `ingest_evidence()` once
   a result arrives) - unfinished work is carried forward rather than
   piled on top of a fresh assignment.
2. **REPAIR** - items currently in `REPAIR` state (failed evidence).
3. **REVIEW** - items due today by `next_review`.
4. **ADVANCE** - brand-new items, capped by `NEW_CEILINGS`
   (`{"vocab": 10, "grammar": 3, "kanji": 3}`) - **ceilings, not quotas**:
   a busy carry-forward/repair/review day can legitimately introduce zero
   new items.

## Notion structure

Targets the **existing "JLPT Daily Plan" Notion database** the original
project already used and pre-populated with a 42-day curriculum (Day
01..Day 42, one row per day, most not yet dated). This is a structured
database with real properties - `Day` (title), `Date`, `Status`,
`Vocabulary`/`Grammar`/`Kanji`/`Review Task` (rich_text, already using the
rank-based id convention `V011`, `K004`, `G002` this port's item bank
also uses), and `Instructor Notes` (rich_text, evening feedback) - not a
flat database-row-per-flashcard the way `kanji_sinks.py` etc. write, and
not freeform page body blocks either. `simple_agent/scheduled/
jlpt_notion.py`'s `resolve_todays_row()` finds today's row by exact
`Date` match if an earlier run today already activated one, otherwise
activates the next undated row in "Day NN" order from the pre-planned
curriculum. `update_row()` writes `Vocabulary`/`Grammar`/`Kanji`/
`Review Task`/`Date`/`Status` directly; `Instructor Notes` is only ever
touched via `append_instructor_notes()`'s read-modify-write, since a
Notion property PATCH replaces the value wholesale and the user's prior
entries must never be overwritten. Content with no dedicated property
(the composed reading passage, the audio-pending notice) is posted as a
Notion **comment** rather than inventing a new database column.
`verify_row_saved()` is a read-back check run right after every morning
write, per the "verify the saved row" requirement - a failed verify
aborts the run before any email is sent, it never gets silently
swallowed.

## Coexistence with Anki / the Telegram SRS drip

Nothing here deletes or touches the Telegram drip's decks or state.
Anki (in the original ChatGPT/Codex project) and the Telegram drip (in
this repo) both stay available as optional, secondary review surfaces
sharing the same source catalog - neither is fed by or feeds into the
instructor's own item bank. The instructor is the sole owner of
INTRODUCE/REVIEW/REPAIR/ADVANCE for its own tracked items; a word/kanji/
pattern being "reviewed" via the Telegram drip has no bearing on its
state in `.jlpt_item_bank.json`, and vice versa.

## Once-per-Japan-date email gate

`jlpt_instructor.py` keeps `.jlpt_email_ledger.json`, keyed by JST date,
with status `PENDING` (reserved, send in flight) or `SENT` (message id +
Notion URL recorded). `PENDING` or `SENT` forbids another send for that
date. The morning branch only reserves *after* the Notion row is written
and verified, so a Notion failure never burns that date's one-email
allowance. A crashed run mid-send leaves a `PENDING` entry, which also
blocks a retry until it's investigated - deliberately conservative,
matching the ported spec's "never claim success on an uncertain send."
