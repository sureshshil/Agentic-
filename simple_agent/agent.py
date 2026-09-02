"""A simple Claude-powered agent with a manual tool-use loop."""

import datetime
import json
import os
import sys

import anthropic

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 16000

SYSTEM_PROMPT = (
    "You are a helpful assistant with access to tools. "
    "Use them when they help answer the user's request; otherwise reply directly."
)

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


def execute_tool(name: str, tool_input: dict) -> str:
    if name == "get_current_time":
        return get_current_time()
    if name == "calculator":
        return calculator(tool_input["expression"])
    return f"Error: unknown tool '{name}'"


class Agent:
    """A minimal conversational agent that can call tools in a loop."""

    def __init__(self, client: anthropic.Anthropic | None = None):
        self.client = client or anthropic.Anthropic()
        self.messages: list[dict] = []

    def send(self, user_input: str) -> str:
        self.messages.append({"role": "user", "content": user_input})

        while True:
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=self.messages,
            )
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                break

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_tool(block.name, block.input)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }
                    )
            self.messages.append({"role": "user", "content": tool_results})

        return next(
            (block.text for block in response.content if block.type == "text"), ""
        )


def _serialize_content(content):
    if isinstance(content, list):
        return [c.to_dict() if hasattr(c, "to_dict") else c for c in content]
    return content


def load_history(state_path: str) -> list:
    if os.path.exists(state_path):
        with open(state_path) as f:
            return json.load(f)
    return []


def save_history(state_path: str, messages: list) -> None:
    serializable = [
        {"role": m["role"], "content": _serialize_content(m["content"])}
        for m in messages
    ]
    with open(state_path, "w") as f:
        json.dump(serializable, f)


def run_single_turn(user_input: str, state_path: str) -> str:
    """Send one message, persisting conversation history to state_path so
    separate process invocations can continue the same conversation."""
    agent = Agent()
    agent.messages = load_history(state_path)
    reply = agent.send(user_input)
    save_history(state_path, agent.messages)
    return reply


def main():
    if len(sys.argv) > 1:
        state_path = os.environ.get("AGENT_STATE_PATH", ".agent_state.json")
        user_input = " ".join(sys.argv[1:])
        print(f"Agent: {run_single_turn(user_input, state_path)}")
        return

    agent = Agent()
    print("Simple agent ready. Type 'exit' to quit.")
    while True:
        try:
            user_input = input("You: ")
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.strip().lower() in {"exit", "quit"}:
            break
        reply = agent.send(user_input)
        print(f"Agent: {reply}")


if __name__ == "__main__":
    main()
