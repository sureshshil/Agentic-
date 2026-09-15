"""Two-way Telegram bot - a persistent chat interface for the agent.

Every other notebook only runs when you open it and execute cells by hand.
This is different: it's a long-running process that long-polls Telegram for
your messages and replies through the same tool-use loop, so you can talk to
the agent from your phone at any time, with no notebook or terminal open.
Combines memory, weather, web search, and email into one agent (see README
for why each notebook picked its own single tool - this is the "put it all
together into one real assistant" step).

Security: only responds to TELEGRAM_ALLOWED_CHAT_ID. Anyone else who
messages the bot is silently ignored - several of these tools have real
side effects (sending email, spending your Vertex AI budget), so the bot
must not act on messages from strangers who find its username.

Commands:
  /reset, /new   Start a new session - a fresh conversation with empty
                 history and its own cost counter reset to $0. Conversation
                 history only ever grows otherwise (every reply resends
                 the full history to the API - see README's "Managing
                 conversation history" section), so this is how you bound
                 it day to day. The previous session's file is kept, not
                 deleted - see "Session files" below. Doesn't affect
                 long-term memory (remember/recall) or the lifetime cost
                 total that AGENT_MAX_COST_USD caps.
  /sessions      List past sessions (most recently active first), numbered,
                 with a preview of their first message, their cost, and
                 when they were last active. Marks the current one.
  /switch <n>    Switch to session <n> from the last /sessions list - its
                 history becomes what gets sent to the model from now on
                 (replacing what's in memory), and the bot immediately
                 replays that session's conversation back into the chat so
                 you can see it (Telegram doesn't let a bot filter its own
                 chat history, so this is how you get a recap instead of
                 needing separate threads/chats per session).
  /cost          Report the current session's cost and the lifetime total
                 against the AGENT_MAX_COST_USD cap, on demand - no need to
                 wait for the hard-stop message to check spend. Replies also
                 carry an automatic warning once lifetime cost crosses 80%
                 of the cap.

Long-term memory: 'remember' skips a fact that's already stored (exact
match, case-insensitive) instead of piling up duplicates, and stamps each
fact with the date it was remembered so the model can tell which is more
current if two ever conflict. Capped at AGENT_MAX_MEMORY_FACTS facts -
once full, the oldest fact is dropped automatically so memory can't grow
the system prompt (and therefore the cost of every single turn) without
bound. 'recall' lists facts numbered with their date; 'forget <number>'
(a tool the model calls, not a Telegram command) removes one by that
number.

Session files:
  BOT_STATE_PATH is an *index* - which session is currently active, plus
  state shared across all sessions: total_cost_usd (LIFETIME spend, across
  every session ever - what AGENT_MAX_COST_USD caps) and the Telegram
  update_offset. It never holds conversation content. Each session's
  actual messages AND its own session_cost_usd (spend in just that
  session, informational only, no cap of its own) live in their own file
  under BOT_SESSIONS_DIR, named by session id - so /reset starts a new
  file instead of overwriting the old one, and old conversations (with
  what they cost) stay on disk if you want to look back (nothing
  currently prunes BOT_SESSIONS_DIR automatically).

Env vars:
  GOOGLE_APPLICATION_CREDENTIALS  required - path to a GCP service account
                            key json with Vertex AI access (or omit and use
                            `gcloud auth application-default login` instead)
  GCP_PROJECT_ID            required - the GCP project Vertex AI bills to
  GCP_LOCATION              optional, default "global" - some models are
                            only enabled in specific regions on a given
                            project
  TELEGRAM_BOT_TOKEN        required - from @BotFather (see notebook 08)
  TELEGRAM_ALLOWED_CHAT_ID  required - your own chat_id (message the bot
                            once, then check notebook 08's discover_chat_id
                            or https://api.telegram.org/bot<token>/getUpdates)
  BRAVE_API_KEY             optional - enables the web_search tool (Brave
                            Web Search API, https://api.search.brave.com)
  EMAIL_ADDRESS             optional - enables send_email (Gmail address)
  EMAIL_APP_PASSWORD        optional - Gmail App Password, see notebook 05
  AGENT_MAX_COST_USD        optional, default 2.00 - a running total that
                            never resets on its own since this process
                            keeps running; delete BOT_STATE_PATH or raise
                            this to keep going once it's hit
  AGENT_MAX_TOOL_ITERATIONS optional, default 15 - hard cap on tool_use
                            round trips within a single reply, independent
                            of AGENT_MAX_COST_USD; stops a runaway tool
                            loop fast instead of only via the cost cap
  AGENT_MAX_MEMORY_FACTS    optional, default 50 - caps long-term memory;
                            once full, 'remember' drops the oldest fact to
                            make room for the new one
  BOT_STATE_PATH            optional, default ".telegram_bot_state.json"
                            - the session index, see "Session files" above
  BOT_SESSIONS_DIR          optional, default ".telegram_bot_sessions"
                            - one JSON file per session's messages

Run: python telegram_bot.py
This has to keep running somewhere to be useful - see README for hosting
options (it long-polls, so no public URL/webhook is needed).
"""

import json
import logging
import os
import smtplib
import time
from email.mime.text import MIMEText

import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types

import srs
import webapp

# Loads telegram_bot.env from this script's own directory, regardless of
# the process's working directory - so the bot picks up secrets the same
# way whether it's started by systemd (which already injects them via
# EnvironmentFile=) or run manually from a shell. Existing environment
# variables always win; this only fills in what isn't already set.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "telegram_bot.env"))

# We do manual tool-calling (execute_tool below), not the SDK's automatic
# function calling - that's expected, so silence its per-call "Tools ...
# are not compatible with automatic function calling" notice.
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

MODEL = "gemini-3.7-flash"
# gemini-3.7-flash thinks adaptively by default, and thinking tokens are
# billed as - and capped by - the same max_output_tokens budget as the
# visible reply. A hard enough request (e.g. "plan this in detail") can
# spend the *entire* budget on invisible reasoning and leave zero tokens
# for the actual answer, truncating with finish_reason="MAX_TOKENS" and
# empty text. 4096 leaves real headroom for that on top of a normal short
# reply; thinking_level=LOW below (chat/tool-use work, not deep
# coding-style reasoning) also keeps the model from reaching for heavy
# thinking in the first place.
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "4096"))

# gemini-3.7-flash pricing, $/1M tokens (source: ai.google.dev/gemini-api/docs/pricing,
# "Standard" tier - Vertex AI generally bills the same per-token rate, but a
# spending cap depends on this being right, so double-check against Cloud
# Billing before trusting it). These are the introductory rates through
# 2026-12-31; they rise to $1.50 / $7.50 on 2027-01-01 - bump these then.
# Update both if you switch models.
INPUT_COST_PER_MTOK = 0.75
OUTPUT_COST_PER_MTOK = 3.75  # includes thinking tokens

# Unlike the notebooks (one cap per kernel session), this cap covers the
# entire lifetime of the running process, since it never restarts on its
# own between messages.
MAX_COST_USD = float(os.environ.get("AGENT_MAX_COST_USD", "2.00"))

# Once lifetime spend crosses this fraction of MAX_COST_USD, replies carry
# a warning so you see it coming instead of being cut off by the hard cap
# with no notice.
COST_WARNING_RATIO = 0.8


def cost_warning(total_cost_usd: float, cap: float) -> str:
    if total_cost_usd < cap * COST_WARNING_RATIO:
        return ""
    return (
        f"\n\n[Note: lifetime cost ${total_cost_usd:.4f} is approaching the "
        f"${cap:.2f} cap (AGENT_MAX_COST_USD). Check /cost for details.]"
    )

# STATE_PATH is now the *index* file: which session is active, plus the
# state that's shared across all sessions (cost total, Telegram offset).
# Each session's own conversation lives in its own file under SESSIONS_DIR,
# so /reset starting a new session never overwrites an older one.
STATE_PATH = os.environ.get("BOT_STATE_PATH", ".telegram_bot_state.json")
SESSIONS_DIR = os.environ.get("BOT_SESSIONS_DIR", ".telegram_bot_sessions")
MEMORY_PATH = os.environ.get("BOT_MEMORY_PATH", ".telegram_bot_memory.json")
# Bounds long-term memory so it can't grow the system prompt (and the cost
# of every single future turn) without limit - once full, 'remember' drops
# the oldest fact to make room, rather than accumulating forever.
MAX_MEMORY_FACTS = int(os.environ.get("AGENT_MAX_MEMORY_FACTS", "50"))
# Hard cap on tool_use round trips within a single turn, independent of
# MAX_COST_USD - a runaway tool loop (model never reaching end_turn) would
# otherwise only ever be stopped by burning through the *lifetime* cost
# cap, which is both slow (real, blocking API calls) and unfair to future
# turns that had nothing to do with the loop.
MAX_TOOL_ITERATIONS = int(os.environ.get("AGENT_MAX_TOOL_ITERATIONS", "15"))
BRAVE_MAX_RESULTS = 5

# Typed in Telegram to start a new conversation - conversation history only
# ever grows otherwise (see README's "Managing conversation history"
# section), so this is the way to bound it without SSHing in to delete
# session files by hand. Doesn't touch long-term memory (remember/recall)
# or the running cost total - those are meant to persist across sessions.
RESET_COMMANDS = {"/reset", "/new"}

# kanji_drip.py / grammar_drip.py send their cards with an inline
# Again/Hard/Good/Easy keyboard (callback_data "srs|<kind>|<item key>|
# <action>"); this bot's long-polling loop is the only process listening
# for the resulting callback_query, so it owns updating each deck's SRS
# state file (see srs.py) when you tap a button.
SRS_KIND_PATHS = {
    "k": (os.environ.get("KANJI_SRS_PATH") or ".kanji_srs.json", srs.BOX_HOURS_KANJI),
    "g": (os.environ.get("GRAMMAR_SRS_PATH") or ".grammar_srs.json", srs.BOX_HOURS_GRAMMAR),
    "v": (os.environ.get("VOCAB_SRS_PATH") or ".vocab_srs.json", srs.BOX_HOURS_VOCAB),
}

WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snow fall", 73: "Moderate snow fall", 75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}


class BudgetExceededError(RuntimeError):
    pass


class ToolLoopLimitError(RuntimeError):
    pass


class EmptyReplyError(RuntimeError):
    pass


SYSTEM_PROMPT_BASE = (
    "You are the user's personal assistant, reachable over Telegram. You "
    "have tools for long-term memory (remember/recall/forget) and current "
    "weather (get_weather). Only call 'get_weather' when the user explicitly "
    "asks about weather or conditions somewhere - don't reach for it for "
    "anything else. Keep replies short - they're read on a phone: one to "
    "three sentences by default. Only go longer if the user explicitly "
    "asks for more detail, a list, step-by-step instructions, or an "
    "explanation.\n\n"
    "Default to answering directly: most messages - greetings, small talk, "
    "opinions, general knowledge you're confident hasn't changed, "
    "explanations of how something works - need no tool at all. Calling a "
    "tool has a real cost and adds latency, so only reach for one when the "
    "task genuinely needs it. When your own knowledge is stable and "
    "non-time-sensitive, just answer from it - don't call a tool 'to be "
    "safe'.\n\n"
    "Formatting: reply in plain text only - no Markdown, no HTML. Telegram "
    "shows that syntax as literal characters, not rendered formatting, so "
    "asterisks, underscores, backticks, '#' headers, and tags like <b> all "
    "show up as visible clutter instead of bold/italic/headings. This "
    "applies even in longer, multi-part answers - use plain sentences, "
    "line breaks, or a hyphen for a list item instead of '**bold**' or "
    "'# heading' formatting.\n\n"
    "Tool errors: if a tool result starts with 'Error:', tell the user what "
    "went wrong in plain language - never guess, invent, or paper over a "
    "failed lookup as if it succeeded.\n\n"
    "Memory: only call 'remember' for durable facts or preferences that "
    "should still matter in a future conversation (e.g. dietary "
    "restrictions, timezone, ongoing projects) - not incidental details "
    "from a single one-off question.\n\n"
    "Reasoning before acting: whenever you're about to call a tool, first "
    "write one short sentence stating what's actually missing from your "
    "own knowledge that the tool provides - not just what the tool does "
    "(e.g. 'Checking live conditions since I don't have today's weather.' "
    "not 'Calling get_weather.'). If you can't articulate a real gap, "
    "you don't need the tool - answer directly instead. This is scratch "
    "reasoning for your own grounding between tool calls, not a reply - "
    "it's never shown to the user and never saved, so keep it brief and "
    "don't address the user in it.\n\n"
    "Answering style: once you're done calling tools and are giving your "
    "actual answer, don't carry the 'Reasoning before acting' narration "
    "into it. Lead with the direct answer in your very first sentence and "
    "stop there unless it's genuinely incomplete without one more "
    "sentence of support. Don't restate the question, don't hedge, and "
    "don't add caveats nobody asked for - default to the shortest reply "
    "that fully answers what was asked.\n\n"
    "Repeating earlier content: if the user asks to see, repeat, or resend "
    "something you already wrote earlier in this same conversation (a "
    "plan, a list, an answer) and it's still visible above in the message "
    "history, copy that earlier reply back verbatim instead of "
    "regenerating it from scratch - it's the same content either way, but "
    "regenerating it burns real tokens and cost for no benefit, and risks "
    "quietly drifting from what you said the first time. Only actually "
    "redo the work if the user is asking for a change to it, not just "
    "another look at it."
)


def _request_with_retry(method: str, url: str, attempts: int = 2, timeout: int = 15, **kwargs):
    last_exc = None
    for _ in range(attempts):
        try:
            resp = requests.request(method, url, timeout=timeout, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
    raise last_exc


# ---- tools ------------------------------------------------------------

def load_memory() -> list:
    """Each entry is {"fact": str, "remembered_at": "YYYY-MM-DD" or None}."""
    if not os.path.exists(MEMORY_PATH):
        return []
    try:
        with open(MEMORY_PATH) as f:
            raw = json.load(f)
    except json.JSONDecodeError:
        print(f"Warning: {MEMORY_PATH} is corrupted; ignoring it.")
        return []

    if raw and isinstance(raw[0], str):
        # One-time migration from the old format (a bare list of fact
        # strings, no date). Existing facts get remembered_at=None ("known
        # before dating was added") rather than a fabricated date.
        migrated = [{"fact": fact, "remembered_at": None} for fact in raw]
        save_memory(migrated)
        return migrated
    return raw


def save_memory(memory: list) -> None:
    with open(MEMORY_PATH, "w") as f:
        json.dump(memory, f, indent=2)


def remember(fact: str) -> str:
    memory = load_memory()
    if any(entry["fact"].strip().lower() == fact.strip().lower() for entry in memory):
        return f"Already remembered: {fact}"
    memory.append({"fact": fact, "remembered_at": time.strftime("%Y-%m-%d", time.gmtime())})

    evicted = None
    if len(memory) > MAX_MEMORY_FACTS:
        evicted = memory.pop(0)
    save_memory(memory)

    if evicted:
        return (
            f"Remembered: {fact}\n"
            f"(Memory was at the {MAX_MEMORY_FACTS}-fact cap, so the oldest "
            f"fact was dropped to make room: \"{evicted['fact']}\". Call "
            "'forget' proactively on facts you no longer need if you want "
            "to control what gets dropped.)"
        )
    return f"Remembered: {fact}"


def recall() -> str:
    memory = load_memory()
    if not memory:
        return "No memories stored yet."
    return "\n".join(
        f"{i}. [{entry['remembered_at'] or 'date unknown'}] {entry['fact']}"
        for i, entry in enumerate(memory, start=1)
    )


def forget(index: int) -> str:
    memory = load_memory()
    if not 1 <= index <= len(memory):
        return f"Error: no fact numbered {index}. Use 'recall' to see valid numbers."
    removed = memory.pop(index - 1)
    save_memory(memory)
    return f"Forgot: {removed['fact']}"


def get_weather(location: str) -> str:
    """Free, no-API-key weather lookup via Open-Meteo."""
    try:
        geo_resp = _request_with_retry(
            "GET",
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1},
            timeout=20,
        )
    except requests.RequestException as exc:
        return f"Error: location lookup failed ({exc})"

    results = geo_resp.json().get("results")
    if not results:
        return f"Error: could not find a location matching '{location}'."
    place = results[0]

    try:
        weather_resp = _request_with_retry(
            "GET",
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,wind_speed_10m,weather_code",
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        return f"Error: weather lookup failed ({exc})"

    current = weather_resp.json().get("current", {})
    units = weather_resp.json().get("current_units", {})
    condition = WMO_CODES.get(current.get("weather_code"), "Unknown conditions")
    place_label = place["name"] + ((", " + place["country"]) if place.get("country") else "")

    return (
        "Weather in " + place_label + ": " + condition + ", "
        + str(current.get("temperature_2m")) + units.get("temperature_2m", "°C")
        + ", wind " + str(current.get("wind_speed_10m")) + " " + units.get("wind_speed_10m", "km/h")
    )


FRESHNESS_CODES = {"pd": "past day", "pw": "past week", "pm": "past month", "py": "past year"}


def web_search(query: str, freshness: str | None = None) -> str:
    """Client-side search via the Brave Web Search API - returns raw
    title/url/description results (no synthesized answer field the way
    Tavily's include_answer worked), so the model does its own synthesis
    across snippets per the system prompt's search guidance.

    freshness narrows results to a recent time window (see FRESHNESS_CODES)
    for queries that are genuinely time-sensitive - left unset, Brave's
    normal relevance ranking across all time applies."""
    api_key = os.environ["BRAVE_API_KEY"]
    params = {"q": query, "count": BRAVE_MAX_RESULTS}
    if freshness in FRESHNESS_CODES:
        params["freshness"] = freshness
    try:
        resp = _request_with_retry(
            "GET",
            "https://api.search.brave.com/res/v1/web/search",
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
            params=params,
        )
    except requests.RequestException as exc:
        return f"Error: web search failed ({exc})"

    results = resp.json().get("web", {}).get("results", [])
    lines = [
        "- " + r["title"] + " (" + r["url"] + "): " + r.get("description", "")
        for r in results
    ]
    return "\n".join(lines) if lines else "No results found."


def send_email(to: str, subject: str, body: str) -> str:
    """Send via Gmail SMTP using an App Password - see notebook 05.

    Uses port 587 (STARTTLS) rather than 465 (implicit SSL): many VPS
    providers (Hetzner in particular, by default on new accounts) block
    outbound 465 as an anti-spam measure but leave 587 open."""
    sender = os.environ.get("EMAIL_ADDRESS")
    app_password = os.environ.get("EMAIL_APP_PASSWORD")
    if not sender or not app_password:
        return "Error: EMAIL_ADDRESS / EMAIL_APP_PASSWORD not set."

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
            server.starttls()
            server.login(sender, app_password)
            server.sendmail(sender, [to], msg.as_string())
        return f"Email sent to {to}."
    except (smtplib.SMTPException, OSError) as exc:
        return f"Error: failed to send email ({exc})"


def build_tools() -> list:
    tools = [
        {
            "name": "remember",
            "description": "Save a fact or preference about the user to long-term memory.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "fact": {
                        "type": "string",
                        "description": "A concise fact to remember, e.g. 'Prefers metric units.'",
                    },
                },
                "required": ["fact"],
            },
        },
        {
            "name": "recall",
            "description": "List everything currently stored in long-term memory, numbered.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "forget",
            "description": "Remove a fact from long-term memory by its number, as shown by 'recall'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "index": {
                        "type": "integer",
                        "description": "The 1-based number of the fact to remove, from 'recall'.",
                    },
                },
                "required": ["index"],
            },
        },
        {
            "name": "get_weather",
            "description": "Get current weather conditions for a location by city name.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City name, optionally with country, e.g. 'Paris, France'.",
                    },
                },
                "required": ["location"],
            },
        },
    ]
    if os.environ.get("BRAVE_API_KEY"):
        tools.append(
            {
                "name": "web_search",
                "description": "Search the web for current information. Returns titles, URLs, and short snippets - synthesize the answer yourself from these.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The search query."},
                        "freshness": {
                            "type": "string",
                            "enum": list(FRESHNESS_CODES),
                            "description": (
                                "Optional. Narrow results to a recent time window - "
                                "'pd' (past day), 'pw' (past week), 'pm' (past month), "
                                "'py' (past year). Set this for anything genuinely "
                                "time-sensitive (who currently holds/won something, "
                                "recent news, live prices) so stale pages don't "
                                "outrank current ones. Leave unset for queries where "
                                "recency doesn't matter."
                            ),
                        },
                    },
                    "required": ["query"],
                },
            }
        )
    if os.environ.get("EMAIL_ADDRESS") and os.environ.get("EMAIL_APP_PASSWORD"):
        tools.append(
            {
                "name": "send_email",
                "description": "Send a plain-text email via Gmail to a recipient.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Recipient email address."},
                        "subject": {"type": "string", "description": "Email subject line."},
                        "body": {"type": "string", "description": "Plain-text email body."},
                    },
                    "required": ["to", "subject", "body"],
                },
            }
        )
    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name=t["name"], description=t["description"], parameters=t["input_schema"]
                )
                for t in tools
            ]
        )
    ]


def execute_tool(name: str, tool_input: dict) -> str:
    if name == "remember":
        return remember(tool_input["fact"])
    if name == "recall":
        return recall()
    if name == "forget":
        return forget(tool_input["index"])
    if name == "get_weather":
        return get_weather(tool_input["location"])
    if name == "web_search":
        return web_search(tool_input["query"], tool_input.get("freshness"))
    if name == "send_email":
        return send_email(tool_input["to"], tool_input["subject"], tool_input["body"])
    return f"Error: unknown tool '{name}'"


# ---- agent core (same pattern as agent.py / the notebooks) ------------

def build_client() -> genai.Client:
    """Authenticates via Application Default Credentials - set
    GOOGLE_APPLICATION_CREDENTIALS to a service account key json, or run
    `gcloud auth application-default login` for a user login instead.
    GCP_PROJECT_ID is required; GCP_LOCATION defaults to "global" (some
    models are only enabled in specific regions on a given project - see
    https://cloud.google.com/vertex-ai/generative-ai/docs/learn/locations)."""
    return genai.Client(
        vertexai=True,
        project=os.environ["GCP_PROJECT_ID"],
        location=os.environ.get("GCP_LOCATION", "global"),
    )


class Agent:
    def __init__(self, client: genai.Client | None = None):
        self.client = client or build_client()
        self.messages: list[dict] = []
        # Lifetime, across every session - this is what AGENT_MAX_COST_USD
        # caps, and it's stored in the index file, not any one session file.
        self.total_cost_usd = 0.0
        # Just the active session - reset to 0.0 by /reset, stored inside
        # that session's own file. Purely informational, no cap of its own.
        self.session_cost_usd = 0.0
        # Raw token counts behind session_cost_usd above - not persisted
        # anywhere, just readable after send() for scripts (e.g.
        # scheduled/vocab_drip.py) that want token counts, not just $.
        self.session_input_tokens = 0
        self.session_output_tokens = 0
        self.tools = build_tools()

    def _system_prompt(self) -> str:
        """Vertex AI has no per-request cache_control breakpoint the way
        Claude's ephemeral system blocks did - Gemini applies its own
        implicit caching for repeated prompt prefixes automatically, with
        no code-side opt-in. So this just returns one plain string; the
        stable/dynamic split below is kept only because it's a natural way
        to build the prompt, not because it affects caching."""
        stable_parts = [SYSTEM_PROMPT_BASE]
        if os.environ.get("BRAVE_API_KEY"):
            stable_parts.append(
                "'web_search' is for things your training data can't be "
                "trusted for: current events, recent changes, prices, "
                "schedules, live scores, or a specific fact you're "
                "genuinely unsure about and that actually changes over "
                "time. Before calling it, check: would this exact answer "
                "have been just as true a year ago? If yes, it's stable - "
                "answer from what you know, don't search. It is not the "
                "default for every factual question - stable facts "
                "(history, science, how something works, well-known "
                "people or places, general how-to/advice) come straight "
                "from what you already know.\n\n"
                "Watch out for questions that sound like stable trivia but "
                "aren't: 'who won/holds/is the current X' for anything "
                "recurring or ongoing (sports championships, awards, "
                "elections, office holders, records, rankings) changes "
                "over time even when you feel confident about a past "
                "answer - your training data has a cutoff, so a "
                "remembered winner may no longer be the *current* one. "
                "Treat 'last/latest/current/most recent <recurring "
                "thing>' as a search trigger by default, not something to "
                "answer from memory just because you recall a specific "
                "instance of it - and set 'freshness' (e.g. 'pw' or 'pm') "
                "on that call so recent pages actually outrank old ones. "
                "When you're genuinely unsure whether something is "
                "time-sensitive, search rather than assume your "
                "training-data instance is still the latest one. Use "
                "'get_weather' and 'send_email' only for what they're each "
                "explicitly for, not as a substitute for web_search.\n\n"
                "Summarizing search results: the tool returns raw titles, "
                "URLs, and short snippets - there's no pre-written answer "
                "to lean on, so synthesize across the snippets yourself "
                "into one coherent, short answer. Don't pad the reply out "
                "with everything every snippet said. If snippets disagree "
                "and neither is clearly newer, say the facts are disputed "
                "rather than picking silently. If the first search's "
                "results are thin or contradictory, re-run it with a "
                "narrower or different 'freshness' setting instead of "
                "answering from a weak result.\n\n"
                "Citing sources: whenever a reply uses 'web_search' "
                "results, list every URL the tool returned at the end of "
                "the reply, one per line as 'Title: URL' - don't trim this "
                "to just one link or leave it out to keep the reply "
                "shorter. This source list is exempt from the usual "
                "brevity default; the rest of the answer above it should "
                "still stay short."
            )
        if os.environ.get("EMAIL_ADDRESS") and os.environ.get("EMAIL_APP_PASSWORD"):
            stable_parts.append(
                "Only call 'send_email' when the user explicitly asks you to "
                "send or email something - never on your own initiative."
            )

        today = time.strftime("%Y-%m-%d", time.gmtime())
        dynamic_parts = [
            f"Today's date is {today}. Your training data has a fixed cutoff "
            "well before that - possibly a year or more. Don't assume "
            "whoever/whatever was true 'currently' as of your training "
            "cutoff is still true now: office holders, champions, prices, "
            "and current versions of things may all have changed in that "
            "gap, even ones you feel confident about."
        ]
        facts = load_memory()
        if facts:
            facts_block = "\n".join(
                f"- ({entry['remembered_at'] or 'date unknown'}) {entry['fact']}"
                for entry in facts
            )
            dynamic_parts.append(
                "Things you remember about the user (dated):\n"
                f"{facts_block}\n\n"
                "If two remembered facts appear to conflict, trust the one "
                "with the more recent date and treat the older one as "
                "possibly outdated rather than silently picking one."
            )

        return "\n\n".join(stable_parts) + "\n\n" + "\n\n".join(dynamic_parts)

    def _run_turn(self):
        tool_iterations = 0
        while True:
            if self.total_cost_usd >= MAX_COST_USD:
                raise BudgetExceededError(
                    f"Lifetime cost ${self.total_cost_usd:.4f} has reached the "
                    f"${MAX_COST_USD:.4f} cap (AGENT_MAX_COST_USD). Raise the "
                    "cap to continue - this is a lifetime total, not scoped "
                    "to the current session, so /reset won't clear it."
                )

            response = self.client.models.generate_content(
                model=MODEL,
                contents=self.messages,
                config=types.GenerateContentConfig(
                    system_instruction=self._system_prompt(),
                    tools=self.tools,
                    max_output_tokens=MAX_TOKENS,
                    # This is a short-reply chat/tool-use bot, not
                    # long-horizon coding/agentic work - low thinking keeps
                    # the model from over-spending the max_output_tokens
                    # budget on hard-sounding but not actually deep requests
                    # (see MAX_TOKENS comment).
                    thinking_config=types.ThinkingConfig(
                        thinking_level=types.ThinkingLevel.LOW
                    ),
                ),
            )
            usage = response.usage_metadata
            cost_delta = (
                ((usage.prompt_token_count or 0) + (usage.tool_use_prompt_token_count or 0))
                * INPUT_COST_PER_MTOK
                + ((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0))
                * OUTPUT_COST_PER_MTOK
            ) / 1_000_000
            self.total_cost_usd += cost_delta
            self.session_cost_usd += cost_delta
            self.session_input_tokens += usage.prompt_token_count or 0
            self.session_output_tokens += usage.candidates_token_count or 0
            self.messages.append(
                response.candidates[0].content.model_dump(mode="json", exclude_none=True)
            )

            function_calls = response.function_calls
            if not function_calls:
                return response

            tool_iterations += 1
            if tool_iterations > MAX_TOOL_ITERATIONS:
                raise ToolLoopLimitError(
                    f"Stopped after {MAX_TOOL_ITERATIONS} tool calls in one "
                    "reply without reaching a final answer (AGENT_MAX_TOOL_"
                    "ITERATIONS) - this guards against a runaway tool loop, "
                    "independent of the cost cap. Try rephrasing or "
                    "breaking your request into smaller steps."
                )

            tool_results = []
            for call in function_calls:
                try:
                    result = execute_tool(call.name, call.args or {})
                except Exception as exc:
                    result = f"Error: tool '{call.name}' failed ({exc})"
                tool_results.append(
                    {
                        "function_response": {
                            "id": call.id,
                            "name": call.name,
                            "response": {"result": result},
                        }
                    }
                )
            self.messages.append({"role": "user", "parts": tool_results})

    def send(self, user_input: str) -> str:
        turn_start = len(self.messages)
        self.messages.append({"role": "user", "parts": [{"text": user_input}]})

        try:
            response = self._run_turn()
        except Exception:
            # Roll back the unanswered user turn so a failed call (budget
            # cap, API error, etc.) never leaves messages ending on
            # "user" - the next send() would otherwise append a second
            # consecutive user message, which the API rejects outright.
            self.messages = self.messages[:turn_start]
            raise

        reply = response.text or ""

        if not reply.strip():
            # Happens when adaptive thinking (see MAX_TOKENS comment above)
            # spends the whole max_output_tokens budget on invisible
            # reasoning and finish_reason="MAX_TOKENS" cuts the response off
            # before any visible text part exists. Silently sending "" to
            # Telegram would look like the bot just didn't respond - roll
            # back the turn (same as any other failed call) and surface it
            # instead.
            finish_reason = response.candidates[0].finish_reason
            self.messages = self.messages[:turn_start]
            raise EmptyReplyError(
                f"Got no reply text back (finish_reason={finish_reason!r}) "
                "- likely spent the whole AGENT_MAX_TOKENS budget on internal "
                "reasoning before writing an answer. Try again, ask for a "
                "shorter/more scoped version, or raise AGENT_MAX_TOKENS."
            )

        # Same turn-collapsing as the notebooks - keeps the resent history
        # small since the API is stateless and this process never restarts
        # the conversation on its own.
        self.messages[turn_start:] = [
            {"role": "user", "parts": [{"text": user_input}]},
            {"role": "model", "parts": [{"text": reply}]},
        ]
        return reply


# ---- persistence ---------------------------------------------------------
#
# Two kinds of file:
#   STATE_PATH (the "index")   - {"active_session_id", "total_cost_usd",
#                                 "update_offset"} - total_cost_usd here is
#                                 the LIFETIME total across every session;
#                                 never holds conversation content.
#   SESSIONS_DIR/<id>.json     - {"messages": [...], "session_cost_usd"}
#                                 for exactly one session - cost spent
#                                 just in that session. /reset starts a
#                                 new id and therefore a new file; nothing
#                                 ever overwrites an older session's file.

def _migrate_message(m: dict) -> dict:
    """Sessions persisted before the Claude->Gemini switch store
    {"role": "user"/"assistant", "content": str} - convert to the new
    {"role": "user"/"model", "parts": [{"text": ...}]} shape. Persisted
    messages are always the collapsed plain-text form (see Agent.send), so
    a bare string is all "content" can hold on disk - no tool_use/tool_result
    blocks to reconcile."""
    if "parts" in m:
        return m
    role = "model" if m["role"] == "assistant" else "user"
    return {"role": role, "parts": [{"text": m.get("content", "")}]}


def new_session_id(update_id) -> str:
    # update_id (from the triggering Telegram update) makes this unique
    # even if two resets land in the same second.
    return f"sess_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{update_id}"


def _session_path(session_id: str) -> str:
    return os.path.join(SESSIONS_DIR, f"{session_id}.json")


def _load_session(session_id: str) -> dict:
    path = _session_path(session_id)
    if not os.path.exists(path):
        return {"messages": [], "session_cost_usd": 0.0}
    with open(path) as f:
        data = json.load(f)
    return {
        "messages": [_migrate_message(m) for m in data.get("messages", [])],
        "session_cost_usd": data.get("session_cost_usd", 0.0),
    }


def list_sessions() -> list:
    """Session ids ordered most-recently-active first, by file mtime."""
    if not os.path.isdir(SESSIONS_DIR):
        return []
    entries = []
    for fname in os.listdir(SESSIONS_DIR):
        if fname.endswith(".json"):
            session_id = fname[: -len(".json")]
            mtime = os.path.getmtime(os.path.join(SESSIONS_DIR, fname))
            entries.append((session_id, mtime))
    entries.sort(key=lambda entry: entry[1], reverse=True)
    return entries


def load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        session_id = new_session_id("init")
        return {
            "active_session_id": session_id,
            "messages": [],
            "session_cost_usd": 0.0,
            "total_cost_usd": 0.0,
            "update_offset": 0,
        }

    with open(STATE_PATH) as f:
        index = json.load(f)

    if "active_session_id" in index:
        session_id = index["active_session_id"]
        session = _load_session(session_id)
        return {
            "active_session_id": session_id,
            "messages": session["messages"],
            "session_cost_usd": session["session_cost_usd"],
            "total_cost_usd": index.get("total_cost_usd", 0.0),
            "update_offset": index.get("update_offset", 0),
        }

    # One-time migration: STATE_PATH is still in the old single-file format
    # (messages stored directly in the index, no per-session cost). Move
    # its history into a new session file, crediting all cost so far to
    # it (it was the only conversation that ever existed), and keep the
    # lifetime total/offset intact so upgrading doesn't lose spend
    # tracking or replay already-answered Telegram messages.
    session_id = new_session_id("migrated")
    lifetime_cost_so_far = index.get("total_cost_usd", 0.0)
    migrated_messages = [_migrate_message(m) for m in index.get("messages", [])]
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    with open(_session_path(session_id), "w") as f:
        json.dump(
            {
                "messages": migrated_messages,
                "session_cost_usd": lifetime_cost_so_far,
            },
            f,
        )
    return {
        "active_session_id": session_id,
        "messages": migrated_messages,
        "session_cost_usd": lifetime_cost_so_far,
        "total_cost_usd": lifetime_cost_so_far,
        "update_offset": index.get("update_offset", 0),
    }


def save_state(
    session_id: str,
    messages: list,
    session_cost_usd: float,
    total_cost_usd: float,
    update_offset: int,
) -> None:
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    with open(_session_path(session_id), "w") as f:
        json.dump({"messages": messages, "session_cost_usd": session_cost_usd}, f)
    with open(STATE_PATH, "w") as f:
        json.dump(
            {
                "active_session_id": session_id,
                "total_cost_usd": total_cost_usd,
                "update_offset": update_offset,
            },
            f,
        )


# ---- Telegram long-polling loop -----------------------------------------

def get_updates(api_base: str, offset: int) -> list:
    resp = _request_with_retry(
        "GET",
        f"{api_base}/getUpdates",
        params={"timeout": 30, "offset": offset},
        timeout=40,
        attempts=1,
    )
    return resp.json().get("result", [])


# Telegram's hard per-message cap. sendMessage returns an HTTP error above
# this and nothing before this fix ever split a reply, so an agent answer
# just long enough to be genuinely useful (a detailed plan, a long
# explanation the user explicitly asked for) would silently fail to
# deliver - it still got persisted to the session file (that happens
# independently in the main loop's `finally`), so it looked like the bot
# "answered" but the user never saw it in Telegram at all.
TELEGRAM_MESSAGE_CHAR_LIMIT = 4096


def _chunk_for_telegram(text: str, limit: int = TELEGRAM_MESSAGE_CHAR_LIMIT) -> list:
    """Split text into <=limit-char pieces, preferring a paragraph/line/word
    boundary near the limit so a split doesn't land mid-sentence or mid-word.
    Every split point keeps its separator attached to the end of the chunk
    before it (rather than stripping it from the start of the next one), so
    "".join(chunks) always reconstructs the original text exactly - no
    separator dropped, no two words silently fused together at a split."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while len(text) > limit:
        split_at = limit
        for sep in ("\n\n", "\n", " "):
            idx = text.rfind(sep, 0, limit)
            if idx > 0:
                split_at = idx + len(sep)
                break
        chunks.append(text[:split_at])
        text = text[split_at:]
    if text:
        chunks.append(text)
    return chunks


def send_telegram_reply(api_base: str, chat_id: str, text: str) -> None:
    for chunk in _chunk_for_telegram(text):
        try:
            _request_with_retry(
                "POST",
                f"{api_base}/sendMessage",
                json={"chat_id": chat_id, "text": chunk},
            )
        except requests.RequestException as exc:
            print(f"Warning: failed to send Telegram reply ({exc})")
            return  # don't send later chunks out of order after a failure


def send_photo(api_base: str, chat_id: str, photo_bytes: bytes, filename: str = "image.png") -> None:
    """Sends a single image via Telegram's sendPhoto (multipart, not
    sendMessage's JSON body) - used by kanji_drip.py to show the kanji
    itself as a large rendered glyph (see kanji_image.py) rather than the
    small/thin Unicode text Telegram's own client font would otherwise
    produce."""
    try:
        _request_with_retry(
            "POST",
            f"{api_base}/sendPhoto",
            data={"chat_id": chat_id},
            files={"photo": (filename, photo_bytes, "image/png")},
        )
    except requests.RequestException as exc:
        print(f"Warning: failed to send Telegram photo ({exc})")


# ---- SRS cards (kanji_drip.py / grammar_drip.py / vocab_drip.py + this
# bot's callback handling below) --------------------------------------

def _send_card(api_base: str, chat_id: str, html_text: str, inline_keyboard: list) -> None:
    try:
        _request_with_retry(
            "POST",
            f"{api_base}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": html_text,
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": inline_keyboard},
            },
        )
    except requests.RequestException as exc:
        print(f"Warning: failed to send Telegram SRS card ({exc})")


def send_web_app_card(api_base: str, chat_id: str, html_text: str, button_text: str, url: str) -> None:
    """One short message with a single button that opens `url` inside
    Telegram itself as a Mini App (a `web_app` inline-keyboard button,
    not a plain link - Telegram only renders it as an in-app webview,
    not a browser tab, when the button carries `web_app` instead of
    `url`). Used by kanji_drip.py (see its WEBAPP_BASE_URL branch)
    instead of send_photo + send_srs_card's three-message flow, once
    webapp.py's review server is configured."""
    _send_card(api_base, chat_id, html_text, [[{"text": button_text, "web_app": {"url": url}}]])


def send_srs_card(api_base: str, chat_id: str, kind: str, item_key: str, html_text: str) -> None:
    """One active-recall card, sent as its OWN message: `html_text` (HTML
    parse_mode, expected to wrap the answer in <tg-spoiler>...</tg-spoiler>
    so it's blurred until tapped) plus an Again/Hard/Good/Easy inline
    keyboard directly below it. `kind` is "k" (kanji), "g" (grammar), or
    "v" (vocab - see SRS_KIND_PATHS). vocab_drip.py's batch of several
    words calls this once per word rather than combining them into one
    message, specifically so each word's buttons land right under it
    instead of all buttons piling up at the bottom of one long message -
    Telegram has no way to attach a keyboard mid-message."""
    buttons = [
        {"text": srs.LABELS[action], "callback_data": f"srs|{kind}|{item_key}|{action}"}
        for action in srs.ACTIONS
    ]
    _send_card(api_base, chat_id, html_text, [buttons])


def handle_srs_callback(api_base: str, callback: dict, allowed_chat_id: str) -> None:
    """A tap on one of send_srs_card's buttons. Verifies the chat, applies
    the rating to the right deck's state file, answers the callback (the
    little toast Telegram shows) with the resulting interval, and strips
    the buttons off that message so it can't be tapped twice."""
    callback_id = callback["id"]
    message = callback.get("message") or {}
    chat_id = str(message.get("chat", {}).get("id", ""))
    data = callback.get("data") or ""

    if chat_id != allowed_chat_id:
        print(f"Ignored SRS callback from unauthorized chat_id {chat_id}")
        _answer_callback_query(api_base, callback_id, "")
        return

    parts = data.split("|")
    if len(parts) != 4 or parts[0] != "srs" or parts[1] not in SRS_KIND_PATHS or parts[3] not in srs.ACTIONS:
        print(f"Ignored malformed SRS callback_data: {data!r}")
        _answer_callback_query(api_base, callback_id, "")
        return

    _, kind, item_key, action = parts
    path, box_hours = SRS_KIND_PATHS[kind]
    state = srs.load_state(path)
    interval_hours = srs.record_review(state, item_key, action, box_hours, srs.now_utc())
    srs.save_state(path, state)

    toast = f"{srs.LABELS[action]} — next review in {srs.format_interval(interval_hours)}"
    print(f"SRS: {kind}:{item_key} rated {action}, next due in {srs.format_interval(interval_hours)}")
    _answer_callback_query(api_base, callback_id, toast)

    message_id = message.get("message_id")
    if message_id is not None:
        current_keyboard = (message.get("reply_markup") or {}).get("inline_keyboard", [])
        remaining_keyboard = [
            row for row in current_keyboard if not any(btn.get("callback_data") == data for btn in row)
        ]
        _edit_message_reply_markup(api_base, chat_id, message_id, remaining_keyboard)


def _answer_callback_query(api_base: str, callback_id: str, text: str) -> None:
    try:
        _request_with_retry(
            "POST",
            f"{api_base}/answerCallbackQuery",
            json={"callback_query_id": callback_id, "text": text},
        )
    except requests.RequestException as exc:
        print(f"Warning: answerCallbackQuery failed ({exc})")


def _edit_message_reply_markup(api_base: str, chat_id: str, message_id: int, inline_keyboard: list) -> None:
    try:
        _request_with_retry(
            "POST",
            f"{api_base}/editMessageReplyMarkup",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": {"inline_keyboard": inline_keyboard},
            },
        )
    except requests.RequestException as exc:
        print(f"Warning: editMessageReplyMarkup failed ({exc})")


# Leaves headroom under TELEGRAM_MESSAGE_CHAR_LIMIT for whatever prefix
# (e.g. "Switched to session N...") gets prepended to a recap.
RECAP_CHAR_LIMIT = 3500


MAX_SESSIONS_LISTED = 30


def _text_of(message: dict) -> str:
    """Persisted messages are always the collapsed single-text-part form
    (see Agent.send), so the first part's text is the whole message."""
    parts = message.get("parts", [])
    return parts[0].get("text", "") if parts else ""


def format_session_list(entries: list, current_session_id: str) -> str:
    if not entries:
        return "No sessions yet."
    total_count = len(entries)
    entries = entries[:MAX_SESSIONS_LISTED]
    lines = []
    for i, (session_id, mtime) in enumerate(entries, start=1):
        session = _load_session(session_id)
        messages = session["messages"]
        preview = _text_of(messages[0]) if messages else "(empty)"
        if len(preview) > 40:
            preview = preview[:40] + "..."
        when = time.strftime("%b %d %H:%M", time.localtime(mtime))
        marker = " (current)" if session_id == current_session_id else ""
        lines.append(f"{i}. {when} - \"{preview}\" - ${session['session_cost_usd']:.4f}{marker}")
    if total_count > MAX_SESSIONS_LISTED:
        lines.append(f"\n...and {total_count - MAX_SESSIONS_LISTED} older session(s) not shown.")
    lines.append("\nUse /switch <number> to switch to one.")
    return "\n".join(lines)


def format_recap(messages: list, limit: int = RECAP_CHAR_LIMIT) -> str:
    if not messages:
        return "(this session has no messages yet)"
    lines = [f"{'You' if m['role'] == 'user' else 'Bot'}: {_text_of(m)}" for m in messages]
    recap = "\n".join(lines)
    if len(recap) <= limit:
        return recap

    kept, total = [], 0
    for line in reversed(lines):
        total += len(line) + 1
        if total > limit:
            break
        kept.append(line)
    kept.reverse()
    return f"[{len(lines) - len(kept)} earlier lines omitted]\n" + "\n".join(kept)


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    allowed_chat_id = str(os.environ["TELEGRAM_ALLOWED_CHAT_ID"])
    api_base = f"https://api.telegram.org/bot{token}"

    # SRS Mini App review server (see webapp.py) - runs in a background
    # thread inside this same process/systemd service, alongside (not
    # instead of) the getUpdates long-polling loop below. Binds
    # 127.0.0.1 only; Caddy is what exposes it over HTTPS.
    webapp.start_server(token, allowed_chat_id)

    state = load_state()
    agent = Agent()
    agent.messages = state["messages"]
    agent.session_cost_usd = state["session_cost_usd"]
    agent.total_cost_usd = state["total_cost_usd"]
    offset = state.get("update_offset", 0)
    session_id = state["active_session_id"]

    print(f"Telegram bot ready (cap ${MAX_COST_USD:.2f}, allowed chat_id {allowed_chat_id}).")
    print(f"Active session: {session_id}")
    print("Listening for messages (Ctrl+C to stop)...")

    while True:
        try:
            updates = get_updates(api_base, offset)
        except requests.RequestException as exc:
            print(f"Warning: getUpdates failed ({exc}); retrying in 5s")
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            try:
                callback = update.get("callback_query")
                if callback:
                    handle_srs_callback(api_base, callback, allowed_chat_id)
                    continue

                message = update.get("message")
                if not message or "text" not in message:
                    continue

                chat_id = str(message["chat"]["id"])
                if chat_id != allowed_chat_id:
                    print(f"Ignored message from unauthorized chat_id {chat_id}")
                    continue

                text = message["text"]
                print(f"You: {text}")

                if text.strip().lower() in RESET_COMMANDS:
                    prev_session_cost = agent.session_cost_usd
                    session_id = new_session_id(update["update_id"])
                    agent.messages = []
                    agent.session_cost_usd = 0.0
                    print(f"New session: {session_id}")
                    reply = (
                        f"Started a new conversation - previous session "
                        f"(cost ${prev_session_cost:.4f}) saved separately, "
                        "not deleted. (Long-term memory from 'remember' is "
                        "unaffected.)"
                    )
                    print(f"Agent: {reply}")
                    send_telegram_reply(api_base, chat_id, reply)
                    continue

                if text.strip().lower() == "/sessions":
                    entries = list_sessions()
                    reply = format_session_list(entries, session_id)
                    print(f"Agent: {reply}")
                    send_telegram_reply(api_base, chat_id, reply)
                    continue

                if text.strip().lower() == "/cost":
                    reply = (
                        f"Session cost: ${agent.session_cost_usd:.4f}\n"
                        f"Lifetime cost: ${agent.total_cost_usd:.4f} / ${MAX_COST_USD:.2f} cap"
                    )
                    print(f"Agent: {reply}")
                    send_telegram_reply(api_base, chat_id, reply)
                    continue

                if text.strip().lower().startswith("/switch"):
                    args = text.strip().split(maxsplit=1)
                    entries = list_sessions()
                    n = args[1].strip() if len(args) == 2 else ""
                    if not n.isdigit() or not (1 <= int(n) <= len(entries)):
                        reply = f"Usage: /switch <number> - see /sessions for the list (1-{len(entries)})."
                    else:
                        target_session_id, _ = entries[int(n) - 1]
                        if target_session_id == session_id:
                            reply = "Already on that session."
                        else:
                            session_id = target_session_id
                            target = _load_session(session_id)
                            agent.messages = target["messages"]
                            agent.session_cost_usd = target["session_cost_usd"]
                            print(f"Switched session: {session_id}")
                            recap = format_recap(agent.messages)
                            reply = (
                                f"Switched to session {n} (cost so far "
                                f"${agent.session_cost_usd:.4f}). Recap:\n\n{recap}"
                            )
                    print(f"Agent: {reply}")
                    send_telegram_reply(api_base, chat_id, reply)
                    continue

                try:
                    reply = agent.send(text)
                    reply += cost_warning(agent.total_cost_usd, MAX_COST_USD)
                except (BudgetExceededError, ToolLoopLimitError, EmptyReplyError) as exc:
                    reply = f"[stopped] {exc}"
                except Exception as exc:
                    print(f"Warning: agent.send failed unexpectedly ({exc})")
                    reply = "Sorry, something went wrong processing that message. Please try again."
                print(f"Agent: {reply}")
                send_telegram_reply(api_base, chat_id, reply)
            finally:
                # Save after every message, not just at the end of the batch -
                # otherwise a crash partway through a batch (e.g. a transient
                # API error) makes the process reload an older offset on
                # restart and replay messages it already replied to (and, for
                # send_email, already acted on).
                save_state(
                    session_id,
                    agent.messages,
                    agent.session_cost_usd,
                    agent.total_cost_usd,
                    offset,
                )


if __name__ == "__main__":
    main()
