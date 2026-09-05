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

STATE_PATH = os.environ.get("BOT_STATE_PATH", ".telegram_bot_state.json")
MEMORY_PATH = os.environ.get("BOT_MEMORY_PATH", ".telegram_bot_memory.json")
MAX_PAUSE_RESUMES = 10
TAVILY_MAX_RESULTS = 3

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
    "(get_weather). When the user shares a fact or preference worth keeping "
    "for future conversations, call 'remember'. Keep replies short - "
    "they're read on a phone."
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
    if os.path.exists(MEMORY_PATH):
        with open(MEMORY_PATH) as f:
            return json.load(f)
    return []


def save_memory(memory: list) -> None:
    with open(MEMORY_PATH, "w") as f:
        json.dump(memory, f, indent=2)


def remember(fact: str) -> str:
    memory = load_memory()
    memory.append(fact)
    save_memory(memory)
    return f"Remembered: {fact}"


def recall() -> str:
    memory = load_memory()
    if not memory:
        return "No memories stored yet."
    return "\n".join(f"- {fact}" for fact in memory)


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
            "description": "List everything currently stored in long-term memory.",
            "input_schema": {"type": "object", "properties": {}},
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
        self.total_cost_usd = 0.0
        self.tools = build_tools()

    def _system_prompt(self) -> str:
        parts = [SYSTEM_PROMPT_BASE]
        if os.environ.get("TAVILY_API_KEY"):
            parts.append("Use 'web_search' for current events or anything you're not sure about.")
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

    def send(self, user_input: str) -> str:
        turn_start = len(self.messages)
        self.messages.append({"role": "user", "content": user_input})

        resumes = 0
        while True:
            if self.total_cost_usd >= MAX_COST_USD:
                raise BudgetExceededError(
                    f"Session cost ${self.total_cost_usd:.4f} has reached the "
                    f"${MAX_COST_USD:.4f} cap (AGENT_MAX_COST_USD). Raise the "
                    "cap or clear the state file to continue."
                )

            response = self.client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=self._system_prompt(),
                tools=self.tools,
                messages=self.messages,
            )
            self.total_cost_usd += (
                response.usage.input_tokens * INPUT_COST_PER_MTOK
                + response.usage.output_tokens * OUTPUT_COST_PER_MTOK
            ) / 1_000_000
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "pause_turn":
                resumes += 1
                if resumes > MAX_PAUSE_RESUMES:
                    break
                continue

            if response.stop_reason != "tool_use":
                break

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

        reply = "".join(block.text for block in response.content if block.type == "text")

        # Same turn-collapsing as the notebooks - keeps the resent history
        # small since the API is stateless and this process never restarts
        # the conversation on its own.
        self.messages[turn_start:] = [
            {"role": "user", "content": user_input},
            {"role": "assistant", "content": reply},
        ]
        return reply


# ---- persistence --------------------------------------------------------

def _serialize_content(content):
    if isinstance(content, list):
        return [c.to_dict() if hasattr(c, "to_dict") else c for c in content]
    return content


def load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {"messages": [], "total_cost_usd": 0.0, "update_offset": 0}
    with open(STATE_PATH) as f:
        return json.load(f)


def save_state(messages: list, total_cost_usd: float, update_offset: int) -> None:
    serializable_messages = [
        {"role": m["role"], "content": _serialize_content(m["content"])} for m in messages
    ]
    with open(STATE_PATH, "w") as f:
        json.dump(
            {
                "messages": serializable_messages,
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


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    allowed_chat_id = str(os.environ["TELEGRAM_ALLOWED_CHAT_ID"])
    api_base = f"https://api.telegram.org/bot{token}"

    state = load_state()
    agent = Agent()
    agent.messages = state["messages"]
    agent.total_cost_usd = state["total_cost_usd"]
    offset = state.get("update_offset", 0)

    print(f"Telegram bot ready (cap ${MAX_COST_USD:.2f}, allowed chat_id {allowed_chat_id}).")
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
                try:
                    reply = agent.send(text)
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
                save_state(agent.messages, agent.total_cost_usd, offset)


if __name__ == "__main__":
    main()
