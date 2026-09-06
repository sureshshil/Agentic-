"""JLPT-level Japanese vocabulary drip, delivered straight to your
Telegram chat every couple of hours via cron - same thin-entry-point
pattern as rain_alert_cron.py and news_digest_agent.py in this directory.

Uses a fresh, throwaway telegram_bot.Agent() instance to generate the
content (same model as the interactive bot, but its own cost tracking)
and telegram_bot.send_telegram_reply() to push it directly via the Bot
API. Deliberately does NOT touch .telegram_bot_state.json or any session
file - these drips just show up as their own messages in the chat and
never mix into or interrupt whatever you're chatting about interactively.

Keeps a small local history (../.vocab_sent_words.json, gitignored like
the other *.json state files) of headwords already sent, so the prompt
can tell the model what to avoid repeating. Capped at HISTORY_LIMIT
entries (oldest dropped first) so old words eventually cycle back around
for review instead of demanding lifetime novelty forever.

Also tracks cumulative token usage/cost across all runs in
../.vocab_usage.json (separate from telegram_bot.py's own lifetime
AGENT_MAX_COST_USD, since this script always uses a fresh, throwaway
Agent() with no persisted cost) - each run prints its own tokens/cost
plus the running lifetime total, so `cat ../.vocab_usage.json` or the
cron log (see cron-notifications.md) shows exactly what this drip has
spent.

See ../deploy/cron-notifications.md for VPS cron setup.

Env vars (loaded via python-dotenv from ../telegram_bot.env and
../deploy/scheduled.env, same fill-in-what's-unset behavior as
telegram_bot.py - real environment variables always win):
  ANTHROPIC_API_KEY         required, from ../telegram_bot.env
  TELEGRAM_BOT_TOKEN        required, from ../telegram_bot.env
  TELEGRAM_ALLOWED_CHAT_ID  required, from ../telegram_bot.env
  VOCAB_LEVEL               optional, default "N3" (JLPT level)
  VOCAB_COUNT               optional, default 4 (words per drop)

Optional extra sinks (each best-effort - a failure is logged and the
Telegram drip still goes out; skipped entirely unless its vars are set).
See scheduled/vocab_sinks.py and ../deploy/cron-notifications.md:
  NOTION_API_KEY               add one Notion row per word (from the
  NOTION_VOCAB_DB_ID           DATA_DELIMITER JSON; both required)
  GOOGLE_SERVICE_ACCOUNT_JSON  also append each drip to a Google Doc,
  VOCAB_GDOC_ID                formatted as a NotebookLM source (both required)
"""

import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

# telegram_bot.py lives one directory up from this scheduled/ script;
# vocab_sinks.py sits right next to it in scheduled/.
_SCHEDULED_DIR = os.path.dirname(os.path.abspath(__file__))
_SIMPLE_AGENT_DIR = os.path.dirname(_SCHEDULED_DIR)
sys.path.insert(0, _SIMPLE_AGENT_DIR)
sys.path.insert(0, _SCHEDULED_DIR)

load_dotenv(os.path.join(_SIMPLE_AGENT_DIR, "telegram_bot.env"))
load_dotenv(os.path.join(_SIMPLE_AGENT_DIR, "deploy", "scheduled.env"))

from telegram_bot import Agent, send_telegram_reply  # noqa: E402 - needs sys.path insert above
from vocab_sinks import gdoc_append, notion_add_row  # noqa: E402 - needs sys.path insert above

VOCAB_LEVEL = os.environ.get("VOCAB_LEVEL") or "N3"
VOCAB_COUNT = int(os.environ.get("VOCAB_COUNT") or "4")

HISTORY_PATH = os.path.join(_SIMPLE_AGENT_DIR, ".vocab_sent_words.json")
HISTORY_LIMIT = 300
# The model appends a machine-readable block after this line: a JSON array
# with one object per word (word / reading / meaning / type / particles /
# examples). It's stripped before the Telegram send and drives both the
# local history file and the per-word Notion rows. Falling back to no
# entries (bad/missing JSON) just means history isn't updated and the
# Notion sink is skipped that run - the Telegram drip is unaffected.
DATA_DELIMITER = "---DATA---"

# Cumulative token/cost usage across all vocab_drip.py runs - separate from
# telegram_bot.py's own lifetime AGENT_MAX_COST_USD tracking, since this
# script always uses a fresh, throwaway Agent() with no persisted cost.
USAGE_PATH = os.path.join(_SIMPLE_AGENT_DIR, ".vocab_usage.json")


def _load_usage() -> dict:
    try:
        with open(USAGE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"runs": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}


def _save_usage(usage: dict) -> None:
    with open(USAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(usage, f, ensure_ascii=False, indent=2)


def _load_history() -> list:
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_history(words: list) -> None:
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(words[-HISTORY_LIMIT:], f, ensure_ascii=False, indent=2)


def build_prompt(avoid: list) -> str:
    avoid_clause = (
        f"Do not reuse any of these words sent recently: {', '.join(avoid)}.\n"
        if avoid
        else ""
    )
    return f"""Give me {VOCAB_COUNT} Japanese vocabulary words at JLPT {VOCAB_LEVEL} level, formatted \
for a Telegram message read on a phone. {avoid_clause}
For EACH word, use exactly this structure - short and plain, no extra commentary, no markdown \
headers/bold/asterisks, no nuance paragraphs, no confusable-word notes:

<kanji/kana> (<reading in hiragana>) — <English meaning>
助詞: <every particle pattern this word is actually commonly used with - if there's more than \
one, e.g. "が/を + 心配する", "を + 参加する / に参加する", list them all separated by " / ", don't \
collapse to just one if the word genuinely takes more than one>

例文:
1. <Japanese sentence with inline [furigana] after every kanji compound, using this exact word> \
— <English translation>
2. <Japanese sentence with inline [furigana] after every kanji compound, using this exact word> \
— <English translation>
3. <Japanese sentence with inline [furigana] after every kanji compound, using this exact word> \
— <English translation>

Keep the 3 example sentences genuinely contextual to this specific word - each one should show \
a distinct, natural situation where this word is actually used, not generic filler sentences. \
Pick the 3 most common, everyday usage patterns for this word - not obscure or rare phrasing - \
these are meant to show a learner how the word is actually used most of the time.

If the 助詞 line lists more than one particle pattern, the example sentences must collectively \
demonstrate each listed pattern at least once (one sentence per pattern) instead of reusing the \
same particle in all 3 sentences - the examples should make every pattern in 助詞 visible in \
context. If 助詞 lists only one pattern, all 3 sentences will naturally use that one.

Furigana rules: in the 3 example sentences ONLY, write furigana inline in [brackets] right after \
each kanji word/compound - e.g. "部屋[へや]を片付[かたづ]けてから友達[ともだち]を呼[よ]んだ。" - every \
kanji compound in the sentence needs its own [furigana], not just the target word, and don't add \
a separate reading at the end of the sentence since it's now inline. The headword line at the top \
keeps its plain "(reading)" in regular parentheses instead - no [brackets] there, and don't print \
this "Furigana rules" instruction or any section label mentioning it in your output.

The very first thing on the headword line must be the real Japanese word itself (kanji and/or \
kana) - never an English gloss standing in for it.

Separate each word's block with a blank line and a "----" divider line.

After all {VOCAB_COUNT} words, on its own line output exactly:
{DATA_DELIMITER}
then a JSON array and nothing else - one object per word above, in the same order:
[{{"word": "<kanji/kana headword>", "reading": "<hiragana reading>", "meaning": "<English \
meaning>", "type": "<part of speech: noun, godan verb, ichidan verb, する-verb, \
な-adjective, い-adjective, adverb, expression, etc.>", "particles": "<the 助詞 line's \
content, verbatim>", "examples": ["<example sentence 1 exactly as written above, keeping \
its inline [furigana]>", "<sentence 2>", "<sentence 3>"]}}]
This block is for my records only and is stripped before the message is sent - never mention \
it, or any JSON/data section, anywhere in the vocabulary content above."""


def parse_entries(raw: str) -> list:
    """The text after DATA_DELIMITER -> a list of word dicts. Tolerates a
    ```json fence and returns [] (with a warning) on anything malformed,
    so a formatting slip never crashes the run or blocks the Telegram
    drip - it just skips the history write and the Notion sink."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"Warning: could not parse {DATA_DELIMITER} JSON ({exc}); "
              "history not updated and Notion sink skipped this run.")
        return []
    if not isinstance(data, list):
        return []
    return [e for e in data if isinstance(e, dict) and str(e.get("word", "")).strip()]


def main() -> None:
    missing = [
        name
        for name in ("ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_ID")
        if not os.environ.get(name)
    ]
    if missing:
        raise SystemExit(f"Missing required env var(s): {', '.join(missing)}.")

    history = _load_history()
    agent = Agent()
    response = agent.send(build_prompt(history))

    if DATA_DELIMITER in response:
        message, data_part = response.split(DATA_DELIMITER, 1)
        entries = parse_entries(data_part)
    else:
        message, entries = response, []
    new_words = [str(e["word"]).strip() for e in entries]

    message = message.strip()
    print(message)

    usage = _load_usage()
    usage["runs"] += 1
    usage["input_tokens"] += agent.session_input_tokens
    usage["output_tokens"] += agent.session_output_tokens
    usage["cost_usd"] += agent.session_cost_usd
    _save_usage(usage)
    print(
        f"\n[usage] this run: {agent.session_input_tokens} in / "
        f"{agent.session_output_tokens} out tokens, ${agent.session_cost_usd:.4f} "
        f"| lifetime ({usage['runs']} runs): {usage['input_tokens']} in / "
        f"{usage['output_tokens']} out tokens, ${usage['cost_usd']:.4f}"
    )

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = str(os.environ["TELEGRAM_ALLOWED_CHAT_ID"])
    api_base = f"https://api.telegram.org/bot{token}"
    send_telegram_reply(api_base, chat_id, f"\U0001f4d8 {VOCAB_LEVEL} Vocabulary\n\n{message}")

    if new_words:
        _save_history(history + new_words)

    # Optional extra sinks - after Telegram (the primary channel) and the
    # local history write, so a slow or failing API here never delays the
    # phone push or risks repeating words next run. Each returns a status
    # string (never raises); print it for the cron log.
    now = datetime.now(timezone.utc)
    if os.environ.get("NOTION_API_KEY") and os.environ.get("NOTION_VOCAB_DB_ID"):
        print(notion_add_row(entries, VOCAB_LEVEL))
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") and os.environ.get("VOCAB_GDOC_ID"):
        print(gdoc_append(entries, VOCAB_LEVEL, now))


if __name__ == "__main__":
    main()
