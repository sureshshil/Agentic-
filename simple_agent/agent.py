"""A simple Gemini-powered agent (via Vertex AI) with a manual tool-use loop."""

import datetime
import json
import logging
import os
import sys

from google import genai
from google.genai import types

# We do manual tool-calling (execute_tool below), not the SDK's automatic
# function calling - that's expected, so silence its per-call "Tools ...
# are not compatible with automatic function calling" notice.
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

MODEL = "gemini-3.7-flash"
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "1024"))

# gemini-3.7-flash pricing, $/1M tokens (source: ai.google.dev/gemini-api/docs/pricing,
# "Standard" tier - Vertex AI generally bills the same per-token rate, but a
# spending cap depends on this being right, so double-check against Cloud
# Billing before trusting it). These are the introductory rates through
# 2026-12-31; they rise to $1.50 / $7.50 on 2027-01-01 - bump these then.
# Update both if you switch models.
INPUT_COST_PER_MTOK = 0.75
OUTPUT_COST_PER_MTOK = 3.75  # includes thinking tokens

# Hard spending cap for a single Agent instance (i.e. one process run).
MAX_COST_USD = float(os.environ.get("AGENT_MAX_COST_USD", "0.40"))


class BudgetExceededError(RuntimeError):
    pass


SYSTEM_PROMPT = (
    "You are a helpful personal assistant with access to tools: a calculator, "
    "the current time, web search, and long-term memory. When the user shares "
    "a fact or preference worth keeping for future conversations, call "
    "'remember'. Use web search for current events or anything you're not "
    "sure about. Otherwise reply directly."
)

DEFAULT_MEMORY_PATH = ".agent_memory.json"

TOOLS = [
    {
        "name": "get_current_time",
        "description": "Get the current date and time.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "calculator",
        "description": "Evaluate a basic arithmetic expression, e.g. '2 + 2 * 3'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "An arithmetic expression using +, -, *, /, and parentheses.",
                },
            },
            "required": ["expression"],
        },
    },
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
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]

# Gemini's built-in Google Search grounding tool - the closest equivalent to
# Claude's server-side web_search tool. The model searches and folds results
# into its answer entirely server-side, so - unlike the function tools above -
# there's no function_call block and no execute_tool round trip for this one.
SEARCH_TOOL = types.Tool(google_search=types.GoogleSearch())


def get_current_time() -> str:
    return datetime.datetime.now().isoformat()


def calculator(expression: str) -> str:
    allowed = set("0123456789+-*/(). ")
    if not set(expression) <= allowed:
        return "Error: expression contains disallowed characters."
    try:
        return str(eval(expression, {"__builtins__": {}}, {}))
    except Exception as exc:
        return f"Error: {exc}"


def load_memory(memory_path: str) -> list:
    if os.path.exists(memory_path):
        with open(memory_path) as f:
            return json.load(f)
    return []


def save_memory(memory_path: str, memory: list) -> None:
    with open(memory_path, "w") as f:
        json.dump(memory, f, indent=2)


def remember(fact: str, memory_path: str) -> str:
    memory = load_memory(memory_path)
    memory.append(fact)
    save_memory(memory_path, memory)
    return f"Remembered: {fact}"


def recall(memory_path: str) -> str:
    memory = load_memory(memory_path)
    if not memory:
        return "No memories stored yet."
    return "\n".join(f"- {fact}" for fact in memory)


def execute_tool(name: str, tool_input: dict, memory_path: str) -> str:
    if name == "get_current_time":
        return get_current_time()
    if name == "calculator":
        return calculator(tool_input["expression"])
    if name == "remember":
        return remember(tool_input["fact"], memory_path)
    if name == "recall":
        return recall(memory_path)
    return f"Error: unknown tool '{name}'"


def build_client() -> genai.Client:
    """Authenticates via Application Default Credentials - set
    GOOGLE_APPLICATION_CREDENTIALS to a service account key file, or run
    `gcloud auth application-default login` for a user login instead.
    GCP_PROJECT_ID is required; GCP_LOCATION defaults to "global" (some
    models are only enabled in specific regions on a given project - see
    https://cloud.google.com/vertex-ai/generative-ai/docs/learn/locations)."""
    return genai.Client(
        vertexai=True,
        project=os.environ["GCP_PROJECT_ID"],
        location=os.environ.get("GCP_LOCATION", "global"),
    )


def build_tools() -> list:
    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name=t["name"], description=t["description"], parameters=t["input_schema"]
                )
                for t in TOOLS
            ]
        ),
        SEARCH_TOOL,
    ]


class Agent:
    """A minimal conversational agent that can call tools in a loop."""

    def __init__(
        self,
        client: genai.Client | None = None,
        memory_path: str | None = None,
    ):
        self.client = client or build_client()
        self.messages: list[dict] = []
        self.memory_path = memory_path or os.environ.get(
            "AGENT_MEMORY_PATH", DEFAULT_MEMORY_PATH
        )
        self.total_cost_usd = 0.0
        self.tools = build_tools()

    def _system_prompt(self) -> str:
        facts = load_memory(self.memory_path)
        if not facts:
            return SYSTEM_PROMPT
        facts_block = "\n".join(f"- {fact}" for fact in facts)
        return f"{SYSTEM_PROMPT}\n\nThings you remember about the user:\n{facts_block}"

    def send(self, user_input: str) -> str:
        turn_start = len(self.messages)
        self.messages.append({"role": "user", "parts": [{"text": user_input}]})

        while True:
            if self.total_cost_usd >= MAX_COST_USD:
                raise BudgetExceededError(
                    f"Session cost ${self.total_cost_usd:.4f} has reached the "
                    f"${MAX_COST_USD:.4f} cap (AGENT_MAX_COST_USD). Raise the "
                    "cap or start a new session to continue."
                )

            response = self.client.models.generate_content(
                model=MODEL,
                contents=self.messages,
                config=types.GenerateContentConfig(
                    system_instruction=self._system_prompt(),
                    tools=self.tools,
                    max_output_tokens=MAX_TOKENS,
                ),
            )
            usage = response.usage_metadata
            self.total_cost_usd += (
                ((usage.prompt_token_count or 0) + (usage.tool_use_prompt_token_count or 0))
                * INPUT_COST_PER_MTOK
                + ((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0))
                * OUTPUT_COST_PER_MTOK
            ) / 1_000_000
            self.messages.append(
                response.candidates[0].content.model_dump(mode="json", exclude_none=True)
            )

            function_calls = response.function_calls
            if not function_calls:
                break

            tool_results = []
            for call in function_calls:
                result = execute_tool(call.name, call.args or {}, self.memory_path)
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

        reply = response.text or ""

        # Collapse this turn's function_call/function_response exchanges down
        # to a plain {user, model} text pair - keeps the resent history small
        # since the API is stateless and this process never restarts the
        # conversation on its own.
        self.messages[turn_start:] = [
            {"role": "user", "parts": [{"text": user_input}]},
            {"role": "model", "parts": [{"text": reply}]},
        ]
        return reply


def load_state(state_path: str) -> dict:
    if not os.path.exists(state_path):
        return {"messages": [], "total_cost_usd": 0.0}
    with open(state_path) as f:
        data = json.load(f)
    if isinstance(data, list):  # legacy format: a bare list of messages
        return {"messages": data, "total_cost_usd": 0.0}
    return data


def save_state(state_path: str, messages: list, total_cost_usd: float) -> None:
    with open(state_path, "w") as f:
        json.dump({"messages": messages, "total_cost_usd": total_cost_usd}, f)


def run_single_turn(user_input: str, state_path: str) -> tuple[str, float]:
    """Send one message, persisting conversation history and spend to
    state_path so separate process invocations continue the same
    conversation and the same spending cap."""
    state = load_state(state_path)
    agent = Agent()
    agent.messages = state["messages"]
    agent.total_cost_usd = state["total_cost_usd"]
    try:
        reply = agent.send(user_input)
        return reply, agent.total_cost_usd
    finally:
        save_state(state_path, agent.messages, agent.total_cost_usd)


def main():
    if len(sys.argv) > 1:
        state_path = os.environ.get("AGENT_STATE_PATH", ".agent_state.json")
        user_input = " ".join(sys.argv[1:])
        try:
            reply, cost = run_single_turn(user_input, state_path)
        except BudgetExceededError as exc:
            print(f"Agent: [stopped] {exc}")
            return
        print(f"Agent: {reply}\n(this run cost ~${cost:.4f})")
        return

    agent = Agent()
    print(
        f"Simple agent ready (cap ${MAX_COST_USD:.4f}/session). Type 'exit' to quit."
    )
    while True:
        try:
            user_input = input("You: ")
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.strip().lower() in {"exit", "quit"}:
            break
        try:
            reply = agent.send(user_input)
        except BudgetExceededError as exc:
            print(f"Agent: [stopped] {exc}")
            break
        print(f"Agent: {reply}  (session cost so far: ~${agent.total_cost_usd:.4f})")


if __name__ == "__main__":
    main()
