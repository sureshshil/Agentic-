"""Regression tests for the Agent core loop in telegram_bot.py.

No live API calls: genai.Client().models.generate_content is replaced with
a MagicMock so these run offline and don't need GOOGLE_APPLICATION_CREDENTIALS
or GCP_PROJECT_ID.

Run: python3 -m unittest tests.test_agent_loop -v   (from simple_agent/)
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import telegram_bot as tb


def make_usage(in_tok=10, out_tok=10):
    u = MagicMock()
    u.prompt_token_count = in_tok
    u.tool_use_prompt_token_count = 0
    u.candidates_token_count = out_tok
    u.thoughts_token_count = 0
    return u


def make_response(parts, function_calls, text, finish_reason="STOP", in_tok=10, out_tok=10):
    """parts/function_calls/text mirror what a real GenerateContentResponse
    exposes: .candidates[0].content (the raw Content to persist),
    .function_calls (the model's tool-call requests, if any), and .text
    (the concatenated visible reply)."""
    r = MagicMock()
    content = MagicMock()
    content.model_dump.return_value = {"role": "model", "parts": parts}
    r.candidates = [MagicMock(content=content, finish_reason=finish_reason)]
    r.function_calls = function_calls
    r.text = text
    r.usage_metadata = make_usage(in_tok, out_tok)
    return r


def text_part(t):
    return {"text": t}


def function_call(name, args, call_id="call_1"):
    fc = MagicMock()
    fc.name = name
    fc.args = args
    fc.id = call_id
    return fc


class AgentLoopTest(unittest.TestCase):
    def setUp(self):
        # load_memory()/_system_prompt() read MEMORY_PATH - point it at a
        # scratch file so tests never touch the real long-term memory file.
        self._tmp_memory = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmp_memory.close()
        os.unlink(self._tmp_memory.name)  # load_memory() treats "missing" as empty
        self._orig_memory_path = tb.MEMORY_PATH
        tb.MEMORY_PATH = self._tmp_memory.name

        self._orig_max_iterations = tb.MAX_TOOL_ITERATIONS
        self._orig_max_cost = tb.MAX_COST_USD

        self.client = MagicMock()
        self.agent = tb.Agent(client=self.client)
        self.agent.tools = tb.build_tools()

    def tearDown(self):
        tb.MEMORY_PATH = self._orig_memory_path
        tb.MAX_TOOL_ITERATIONS = self._orig_max_iterations
        tb.MAX_COST_USD = self._orig_max_cost
        if os.path.exists(self._tmp_memory.name):
            os.unlink(self._tmp_memory.name)

    def test_reasoning_before_tool_call_does_not_leak(self):
        """The prompted 'why' sentence before a tool call must never reach
        the user's reply or get persisted to history - only the final
        (non tool-calling) response's text is surfaced/saved."""
        reasoning = "Checking the weather since the user asked about Paris."
        self.client.models.generate_content.side_effect = [
            make_response(
                [text_part(reasoning), {"function_call": {"name": "get_weather", "args": {"location": "Paris"}}}],
                [function_call("get_weather", {"location": "Paris"})],
                text=None,
            ),
            make_response([text_part("It's 18C and cloudy in Paris.")], [], "It's 18C and cloudy in Paris."),
        ]

        with unittest.mock.patch.object(tb, "execute_tool", return_value="Weather: cloudy, 18C"):
            reply = self.agent.send("what's the weather in paris")

        self.assertEqual(reply, "It's 18C and cloudy in Paris.")
        self.assertNotIn(reasoning, reply)
        self.assertTrue(
            all(reasoning not in str(m["parts"]) for m in self.agent.messages),
            "reasoning text leaked into persisted message history",
        )

    def test_multi_tool_turn_collapses_to_single_exchange(self):
        """A turn with several tool round-trips still ends up as exactly
        one {user, model} pair in history, not one entry per round-trip."""
        self.client.models.generate_content.side_effect = [
            make_response([{"function_call": {"name": "recall", "args": {}}}], [function_call("recall", {}, "t1")], None),
            make_response(
                [{"function_call": {"name": "get_weather", "args": {"location": "Rome"}}}],
                [function_call("get_weather", {"location": "Rome"}, "t2")],
                None,
            ),
            make_response([text_part("No memories yet; Rome is sunny.")], [], "No memories yet; Rome is sunny."),
        ]

        with unittest.mock.patch.object(tb, "execute_tool", return_value="ok"):
            reply = self.agent.send("recall and check rome weather")

        self.assertEqual(reply, "No memories yet; Rome is sunny.")
        self.assertEqual(len(self.agent.messages), 2)
        self.assertEqual(
            self.agent.messages[0], {"role": "user", "parts": [{"text": "recall and check rome weather"}]}
        )
        self.assertEqual(self.agent.messages[1], {"role": "model", "parts": [{"text": reply}]})

    def test_failed_turn_rolls_back_dangling_user_message(self):
        """If _run_turn raises (API error, budget cap, ...), the just-appended
        user message must be rolled back - otherwise the next send() would
        append a second consecutive 'user' message, which the API rejects."""
        self.client.models.generate_content.side_effect = RuntimeError("simulated API failure")

        with self.assertRaises(RuntimeError):
            self.agent.send("hello")

        self.assertEqual(self.agent.messages, [])

    def test_tool_loop_limit_stops_a_runaway_loop(self):
        """A model stuck calling tools forever must be cut off, not looped
        on indefinitely or billed without bound."""
        tb.MAX_TOOL_ITERATIONS = 2
        self.client.models.generate_content.side_effect = lambda **kwargs: make_response(
            [{"function_call": {"name": "recall", "args": {}}}], [function_call("recall", {})], None
        )

        with unittest.mock.patch.object(tb, "execute_tool", return_value="ok"):
            with self.assertRaises(tb.ToolLoopLimitError):
                self.agent.send("keep going")

        # Rolled back: no half-finished turn left dangling in history.
        self.assertEqual(self.agent.messages, [])

    def test_budget_cap_blocks_before_spending_further(self):
        """Once lifetime cost has reached the cap, no further API call
        should be made at all."""
        tb.MAX_COST_USD = 1.00
        self.agent.total_cost_usd = 1.00

        with self.assertRaises(tb.BudgetExceededError):
            self.agent.send("hello")

        self.client.models.generate_content.assert_not_called()
        self.assertEqual(self.agent.messages, [])


if __name__ == "__main__":
    unittest.main()
