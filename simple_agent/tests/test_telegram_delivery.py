"""Regression tests for delivering long replies to Telegram.

Telegram's sendMessage rejects any text over 4096 chars outright. A reply
that size used to fail silently: send_telegram_reply() swallowed the HTTP
error into a printed warning, while the reply was still persisted to the
session file - so the bot looked like it had answered, but the message
never reached the user. See _chunk_for_telegram / send_telegram_reply.

No live API calls: requests.request is mocked.

Run: python3 -m unittest tests.test_telegram_delivery -v   (from simple_agent/)
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import telegram_bot as tb


class ChunkForTelegramTest(unittest.TestCase):
    def test_short_text_is_a_single_chunk(self):
        self.assertEqual(tb._chunk_for_telegram("hello"), ["hello"])

    def test_long_text_is_split_under_the_limit(self):
        text = ("paragraph one. " * 50 + "\n\n") * 20  # well over 4096 chars
        chunks = tb._chunk_for_telegram(text)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= tb.TELEGRAM_MESSAGE_CHAR_LIMIT for c in chunks))

    def test_splitting_does_not_lose_content(self):
        text = "word " * 2000  # ~10000 chars, no newlines to split on
        chunks = tb._chunk_for_telegram(text)
        self.assertEqual("".join(chunks).split(), text.split())

    def test_exactly_at_limit_is_not_split(self):
        text = "a" * tb.TELEGRAM_MESSAGE_CHAR_LIMIT
        self.assertEqual(tb._chunk_for_telegram(text), [text])


class SendTelegramReplyTest(unittest.TestCase):
    def test_long_reply_is_sent_as_multiple_messages(self):
        """The exact failure this guards against: a legitimate, detailed
        answer (e.g. a requested multi-week study plan) must reach the user
        in full, not get dropped for being one character over the limit."""
        text = "line\n" * 1500  # > 4096 chars
        with patch.object(tb, "_request_with_retry") as mock_request:
            tb.send_telegram_reply("https://api.telegram.org/botX", "123", text)

        self.assertGreater(mock_request.call_count, 1)
        sent_texts = [call.kwargs["json"]["text"] for call in mock_request.call_args_list]
        self.assertTrue(all(len(t) <= tb.TELEGRAM_MESSAGE_CHAR_LIMIT for t in sent_texts))
        self.assertEqual("".join(sent_texts), "".join(tb._chunk_for_telegram(text)))

    def test_short_reply_is_sent_as_one_message(self):
        with patch.object(tb, "_request_with_retry") as mock_request:
            tb.send_telegram_reply("https://api.telegram.org/botX", "123", "hi")
        self.assertEqual(mock_request.call_count, 1)

    def test_failure_on_first_chunk_stops_remaining_chunks(self):
        """Don't send chunk 2 after chunk 1 failed - that would deliver the
        reply out of order (or duplicated on a later retry)."""
        text = "line\n" * 1500
        with patch.object(
            tb, "_request_with_retry", side_effect=tb.requests.RequestException("boom")
        ) as mock_request:
            tb.send_telegram_reply("https://api.telegram.org/botX", "123", text)
        self.assertEqual(mock_request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
