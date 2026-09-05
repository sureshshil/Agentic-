"""Regression tests for the Agent core loop in telegram_bot.py.

No live API calls: anthropic.Anthropic().messages.create is replaced with a
MagicMock so these run offline and don't need ANTHROPIC_API_KEY.

Run: python3 -m unittest tests.test_agent_loop -v   (from simple_agent/)
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import telegram_bot as tb


def make_response(content_blocks, stop_reason, in_tok=10, out_tok=10):
    r = MagicMock()
    r.content = content_blocks
    r.stop_reason = stop_reason
    r.usage.input_tokens = in_tok
    r.usage.output_tokens = out_tok
    return r


def text_block(t):
    b = MagicMock()
    b.type = "text"
    b.text = t
    return b


def tool_block(name, tool_input, block_id="tool_1"):
    b = MagicMock()
    b.type = "tool_use"
    b.name = name
    b.input = tool_input
    b.id = block_id
    return b


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
        self.client.messages.create.side_effect = [
            make_response(
                [text_block(reasoning), tool_block("get_weather", {"location": "Paris"})],
                "tool_use",
            ),
            make_response([text_block("It's 18C and cloudy in Paris.")], "end_turn"),
        ]

        with unittest.mock.patch.object(tb, "execute_tool", return_value="Weather: cloudy, 18C"):
            reply = self.agent.send("what's the weather in paris")

        self.assertEqual(reply, "It's 18C and cloudy in Paris.")
        self.assertNotIn(reasoning, reply)
        self.assertTrue(
            all(reasoning not in str(m["content"]) for m in self.agent.messages),
            "reasoning text leaked into persisted message history",
        )

    def test_multi_tool_turn_collapses_to_single_exchange(self):
        """A turn with several tool round-trips still ends up as exactly
        one {user, assistant} pair in history, not one entry per round-trip."""
        self.client.messages.create.side_effect = [
            make_response([tool_block("recall", {}, "t1")], "tool_use"),
            make_response([tool_block("get_weather", {"location": "Rome"}, "t2")], "tool_use"),
            make_response([text_block("No memories yet; Rome is sunny.")], "end_turn"),
        ]

        with unittest.mock.patch.object(tb, "execute_tool", return_value="ok"):
            reply = self.agent.send("recall and check rome weather")

        self.assertEqual(reply, "No memories yet; Rome is sunny.")
        self.assertEqual(len(self.agent.messages), 2)
        self.assertEqual(self.agent.messages[0], {"role": "user", "content": "recall and check rome weather"})
        self.assertEqual(self.agent.messages[1], {"role": "assistant", "content": reply})

    def test_failed_turn_rolls_back_dangling_user_message(self):
        """If _run_turn raises (API error, budget cap, ...), the just-appended
        user message must be rolled back - otherwise the next send() would
        append a second consecutive 'user' message, which the API rejects."""
        self.client.messages.create.side_effect = RuntimeError("simulated API failure")

        with self.assertRaises(RuntimeError):
            self.agent.send("hello")

        self.assertEqual(self.agent.messages, [])

    def test_tool_loop_limit_stops_a_runaway_loop(self):
        """A model stuck calling tools forever must be cut off, not looped
        on indefinitely or billed without bound."""
        tb.MAX_TOOL_ITERATIONS = 2
        self.client.messages.create.side_effect = lambda **kwargs: make_response(
            [tool_block("recall", {})], "tool_use"
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

        self.client.messages.create.assert_not_called()
        self.assertEqual(self.agent.messages, [])


if __name__ == "__main__":
    unittest.main()
