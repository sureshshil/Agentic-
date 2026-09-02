"""A simple Claude-powered agent with a manual tool-use loop."""

import datetime
import json
import os
import sys

import anthropic

MODEL = "claude-haiku-4-5"
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "1024"))

# claude-haiku-4-5 pricing, $/1M tokens - update if you switch models.
INPUT_COST_PER_MTOK = 1.00
OUTPUT_COST_PER_MTOK = 5.00

# Hard spending cap for a single Agent instance (i.e. one process run).
MAX_COST_USD = float(os.environ.get("AGENT_MAX_COST_USD", "0.20"))


class BudgetExceededError(RuntimeError):
    pass


SYSTEM_PROMPT = (
    "You are a helpful personal assistant with access to tools: a calculator, "
    "the current time, web search, and long-term memory. When the user shares "
    "a fact or preference worth keeping for future conversations, call "
    "'remember'. Use 'web_search' for current events or anything you're not "
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
    {
        "type": "web_search_20260209",
        "name": "web_search",
        "max_uses": 3,
        "allowed_callers": ["direct"],
    },
]


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


MAX_PAUSE_RESUMES = 10


class Agent:
    """A minimal conversational agent that can call tools in a loop."""

    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        memory_path: str | None = None,
    ):
        self.client = client or anthropic.Anthropic()
        self.messages: list[dict] = []
        self.memory_path = memory_path or os.environ.get(
            "AGENT_MEMORY_PATH", DEFAULT_MEMORY_PATH
        )
        self.total_cost_usd = 0.0

    def _system_prompt(self) -> str:
        facts = load_memory(self.memory_path)
        if not facts:
            return SYSTEM_PROMPT
        facts_block = "\n".join(f"- {fact}" for fact in facts)
        return f"{SYSTEM_PROMPT}\n\nThings you remember about the user:\n{facts_block}"

    def send(self, user_input: str) -> str:
        self.messages.append({"role": "user", "content": user_input})

        resumes = 0
        while True:
            if self.total_cost_usd >= MAX_COST_USD:
                raise BudgetExceededError(
                    f"Session cost ${self.total_cost_usd:.4f} has reached the "
                    f"${MAX_COST_USD:.4f} cap (AGENT_MAX_COST_USD). Raise the "
                    "cap or start a new session to continue."
                )

            response = self.client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=self._system_prompt(),
                tools=TOOLS,
                messages=self.messages,
            )
            self.total_cost_usd += (
                response.usage.input_tokens * INPUT_COST_PER_MTOK
                + response.usage.output_tokens * OUTPUT_COST_PER_MTOK
            ) / 1_000_000
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "pause_turn":
                # A server-side tool (e.g. web_search) hit its per-turn
                # iteration limit; resend as-is to let Claude continue.
                resumes += 1
                if resumes > MAX_PAUSE_RESUMES:
                    break
                continue

            if response.stop_reason != "tool_use":
                break

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_tool(block.name, block.input, self.memory_path)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }
                    )
            self.messages.append({"role": "user", "content": tool_results})

        return "".join(
            block.text for block in response.content if block.type == "text"
        )


def _serialize_content(content):
    if isinstance(content, list):
        return [c.to_dict() if hasattr(c, "to_dict") else c for c in content]
    return content


def load_state(state_path: str) -> dict:
    if not os.path.exists(state_path):
        return {"messages": [], "total_cost_usd": 0.0}
    with open(state_path) as f:
        data = json.load(f)
    if isinstance(data, list):  # legacy format: a bare list of messages
        return {"messages": data, "total_cost_usd": 0.0}
    return data


def save_state(state_path: str, messages: list, total_cost_usd: float) -> None:
    serializable_messages = [
        {"role": m["role"], "content": _serialize_content(m["content"])}
        for m in messages
    ]
    with open(state_path, "w") as f:
        json.dump(
            {"messages": serializable_messages, "total_cost_usd": total_cost_usd}, f
        )


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
