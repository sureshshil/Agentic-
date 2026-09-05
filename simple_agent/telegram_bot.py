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
side effects (sending email, spending your Anthropic budget), so the bot
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
                 history becomes what gets sent to Claude from now on
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
match, case-insensitive) instead of piling up duplicates. 'recall' lists
facts numbered; 'forget <number>' (a tool the model calls, not a Telegram
command) removes one by that number.

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
  ANTHROPIC_API_KEY         required
  ANTHROPIC_WORKSPACE_ID    optional - see README's workspace-id section
  TELEGRAM_BOT_TOKEN        required - from @BotFather (see notebook 08)
  TELEGRAM_ALLOWED_CHAT_ID  required - your own chat_id (message the bot
                            once, then check notebook 08's discover_chat_id
                            or https://api.telegram.org/bot<token>/getUpdates)
  TAVILY_API_KEY            optional - enables the web_search tool
  EMAIL_ADDRESS             optional - enables send_email (Gmail address)
  EMAIL_APP_PASSWORD        optional - Gmail App Password, see notebook 05
  AGENT_MAX_COST_USD        optional, default 1.00 - a running total that
                            never resets on its own since this process
                            keeps running; delete BOT_STATE_PATH or raise
                            this to keep going once it's hit
  BOT_STATE_PATH            optional, default ".telegram_bot_state.json"
                            - the session index, see "Session files" above
  BOT_SESSIONS_DIR          optional, default ".telegram_bot_sessions"
                            - one JSON file per session's messages

Run: python telegram_bot.py
This has to keep running somewhere to be useful - see README for hosting
options (it long-polls, so no public URL/webhook is needed).
"""

import json
import os
import smtplib
import time
from email.mime.text import MIMEText

import anthropic
import requests
from dotenv import load_dotenv

# Loads telegram_bot.env from this script's own directory, regardless of
# the process's working directory - so the bot picks up secrets the same
# way whether it's started by systemd (which already injects them via
# EnvironmentFile=) or run manually from a shell. Existing environment
# variables always win; this only fills in what isn't already set.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "telegram_bot.env"))

MODEL = "claude-haiku-4-5"
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "1024"))

# claude-haiku-4-5 pricing, $/1M tokens - update if you switch models.
INPUT_COST_PER_MTOK = 1.00
OUTPUT_COST_PER_MTOK = 5.00

# Unlike the notebooks (one cap per kernel session), this cap covers the
# entire lifetime of the running process, since it never restarts on its
# own between messages.
MAX_COST_USD = float(os.environ.get("AGENT_MAX_COST_USD", "1.00"))

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
MAX_PAUSE_RESUMES = 10
TAVILY_MAX_RESULTS = 3

# Typed in Telegram to start a new conversation - conversation history only
# ever grows otherwise (see README's "Managing conversation history"
# section), so this is the way to bound it without SSHing in to delete
# session files by hand. Doesn't touch long-term memory (remember/recall)
# or the running cost total - those are meant to persist across sessions.
RESET_COMMANDS = {"/reset", "/new"}

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


SYSTEM_PROMPT_BASE = (
    "You are the user's personal assistant, reachable over Telegram. You "
    "have tools for long-term memory (remember/recall) and current weather "
    "(get_weather). Only call 'get_weather' when the user explicitly asks "
    "about weather or conditions somewhere - don't reach for it for "
    "anything else. When the user shares a fact or preference worth "
    "keeping for future conversations, call 'remember'. Keep replies "
    "short - they're read on a phone."
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
    if not os.path.exists(MEMORY_PATH):
        return []
    try:
        with open(MEMORY_PATH) as f:
            return json.load(f)
    except json.JSONDecodeError:
        print(f"Warning: {MEMORY_PATH} is corrupted; ignoring it.")
        return []


def save_memory(memory: list) -> None:
    with open(MEMORY_PATH, "w") as f:
        json.dump(memory, f, indent=2)


def remember(fact: str) -> str:
    memory = load_memory()
    if any(existing.strip().lower() == fact.strip().lower() for existing in memory):
        return f"Already remembered: {fact}"
    memory.append(fact)
    save_memory(memory)
    return f"Remembered: {fact}"


def recall() -> str:
    memory = load_memory()
    if not memory:
        return "No memories stored yet."
    return "\n".join(f"{i}. {fact}" for i, fact in enumerate(memory, start=1))


def forget(index: int) -> str:
    memory = load_memory()
    if not 1 <= index <= len(memory):
        return f"Error: no fact numbered {index}. Use 'recall' to see valid numbers."
    removed = memory.pop(index - 1)
    save_memory(memory)
    return f"Forgot: {removed}"


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


def web_search(query: str) -> str:
    """Client-side search via Tavily - see notebook 03 for why (plain JSON,
    no per-result verification blob to worry about)."""
    api_key = os.environ["TAVILY_API_KEY"]
    try:
        resp = _request_with_retry(
            "POST",
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "query": query,
                "max_results": TAVILY_MAX_RESULTS,
                "chunks_per_source": 1,
                "include_answer": "basic",
            },
        )
    except requests.RequestException as exc:
        return f"Error: web search failed ({exc})"

    data = resp.json()
    lines = []
    if data.get("answer"):
        lines.append("Answer: " + data["answer"])
    for r in data.get("results", []):
        lines.append("- " + r["title"] + " (" + r["url"] + "): " + r["content"])
    return "\n".join(lines) if lines else "No results found."


def send_email(to: str, subject: str, body: str) -> str:
    """Send via Gmail SMTP using an App Password - see notebook 05."""
    sender = os.environ.get("EMAIL_ADDRESS")
    app_password = os.environ.get("EMAIL_APP_PASSWORD")
    if not sender or not app_password:
        return "Error: EMAIL_ADDRESS / EMAIL_APP_PASSWORD not set."

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
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
    if os.environ.get("TAVILY_API_KEY"):
        tools.append(
            {
                "name": "web_search",
                "description": "Search the web for current information. Returns a short synthesized answer plus a few source snippets.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The search query."},
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
    return tools


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
        return web_search(tool_input["query"])
    if name == "send_email":
        return send_email(tool_input["to"], tool_input["subject"], tool_input["body"])
    return f"Error: unknown tool '{name}'"


# ---- agent core (same pattern as agent.py / the notebooks) ------------

def build_client() -> anthropic.Anthropic:
    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    if workspace_id:
        return anthropic.Anthropic(default_headers={"anthropic-workspace-id": workspace_id})
    return anthropic.Anthropic()


class Agent:
    def __init__(self, client: anthropic.Anthropic | None = None):
        self.client = client or build_client()
        self.messages: list[dict] = []
        # Lifetime, across every session - this is what AGENT_MAX_COST_USD
        # caps, and it's stored in the index file, not any one session file.
        self.total_cost_usd = 0.0
        # Just the active session - reset to 0.0 by /reset, stored inside
        # that session's own file. Purely informational, no cap of its own.
        self.session_cost_usd = 0.0
        self.tools = build_tools()

    def _system_prompt(self) -> str:
        parts = [SYSTEM_PROMPT_BASE]
        if os.environ.get("TAVILY_API_KEY"):
            parts.append(
                "'web_search' is your default tool for anything factual, "
                "current, or that you're not fully sure about - reach for "
                "it before answering from memory or guessing. Use "
                "'get_weather' and 'send_email' only for what they're each "
                "explicitly for, not as a substitute for web_search."
            )
        if os.environ.get("EMAIL_ADDRESS") and os.environ.get("EMAIL_APP_PASSWORD"):
            parts.append(
                "Only call 'send_email' when the user explicitly asks you to "
                "send or email something - never on your own initiative."
            )
        facts = load_memory()
        if facts:
            facts_block = "\n".join(f"- {fact}" for fact in facts)
            parts.append(f"Things you remember about the user:\n{facts_block}")
        return "\n\n".join(parts)

    def _run_turn(self):
        resumes = 0
        while True:
            if self.total_cost_usd >= MAX_COST_USD:
                raise BudgetExceededError(
                    f"Lifetime cost ${self.total_cost_usd:.4f} has reached the "
                    f"${MAX_COST_USD:.4f} cap (AGENT_MAX_COST_USD). Raise the "
                    "cap to continue - this is a lifetime total, not scoped "
                    "to the current session, so /reset won't clear it."
                )

            response = self.client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=self._system_prompt(),
                tools=self.tools,
                messages=self.messages,
            )
            cost_delta = (
                response.usage.input_tokens * INPUT_COST_PER_MTOK
                + response.usage.output_tokens * OUTPUT_COST_PER_MTOK
            ) / 1_000_000
            self.total_cost_usd += cost_delta
            self.session_cost_usd += cost_delta
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "pause_turn":
                resumes += 1
                if resumes > MAX_PAUSE_RESUMES:
                    return response
                continue

            if response.stop_reason != "tool_use":
                return response

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    try:
                        result = execute_tool(block.name, block.input)
                    except Exception as exc:
                        result = f"Error: tool '{block.name}' failed ({exc})"
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": result}
                    )
            self.messages.append({"role": "user", "content": tool_results})

    def send(self, user_input: str) -> str:
        turn_start = len(self.messages)
        self.messages.append({"role": "user", "content": user_input})

        try:
            response = self._run_turn()
        except Exception:
            # Roll back the unanswered user turn so a failed call (budget
            # cap, API error, etc.) never leaves messages ending on
            # "user" - the next send() would otherwise append a second
            # consecutive user message, which the API rejects outright.
            self.messages = self.messages[:turn_start]
            raise

        reply = "".join(block.text for block in response.content if block.type == "text")

        # Same turn-collapsing as the notebooks - keeps the resent history
        # small since the API is stateless and this process never restarts
        # the conversation on its own.
        self.messages[turn_start:] = [
            {"role": "user", "content": user_input},
            {"role": "assistant", "content": reply},
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

def _serialize_content(content):
    if isinstance(content, list):
        return [c.to_dict() if hasattr(c, "to_dict") else c for c in content]
    return content


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
        "messages": data.get("messages", []),
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
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    with open(_session_path(session_id), "w") as f:
        json.dump(
            {
                "messages": index.get("messages", []),
                "session_cost_usd": lifetime_cost_so_far,
            },
            f,
        )
    return {
        "active_session_id": session_id,
        "messages": index.get("messages", []),
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
    serializable_messages = [
        {"role": m["role"], "content": _serialize_content(m["content"])} for m in messages
    ]
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    with open(_session_path(session_id), "w") as f:
        json.dump(
            {"messages": serializable_messages, "session_cost_usd": session_cost_usd}, f
        )
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


def send_telegram_reply(api_base: str, chat_id: str, text: str) -> None:
    try:
        _request_with_retry(
            "POST",
            f"{api_base}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )
    except requests.RequestException as exc:
        print(f"Warning: failed to send Telegram reply ({exc})")


# Telegram caps a single sendMessage's text at 4096 chars; leave headroom
# for whatever prefix (e.g. "Switched to session N...") gets prepended.
RECAP_CHAR_LIMIT = 3500


MAX_SESSIONS_LISTED = 30


def format_session_list(entries: list, current_session_id: str) -> str:
    if not entries:
        return "No sessions yet."
    total_count = len(entries)
    entries = entries[:MAX_SESSIONS_LISTED]
    lines = []
    for i, (session_id, mtime) in enumerate(entries, start=1):
        session = _load_session(session_id)
        messages = session["messages"]
        preview = messages[0]["content"] if messages else "(empty)"
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
    lines = [f"{'You' if m['role'] == 'user' else 'Bot'}: {m['content']}" for m in messages]
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
                except BudgetExceededError as exc:
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
