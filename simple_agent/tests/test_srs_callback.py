"""Regression tests for telegram_bot.py's SRS callback handling: routing
an Again/Hard/Good/Easy button tap (kanji_drip.py / grammar_drip.py's
inline keyboard, see srs.py) into the right deck's state file, rejecting
anything from an unauthorized chat, and ignoring malformed callback_data
instead of crashing the bot's long-polling loop over one bad update.

No live API calls: requests.request is mocked.

Run: python3 -m unittest tests.test_srs_callback -v   (from simple_agent/)
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import srs  # noqa: E402
import telegram_bot as tb  # noqa: E402

ALLOWED_CHAT_ID = "555"


def _callback(
    data: str, chat_id: str = ALLOWED_CHAT_ID, message_id: int = 42, reply_markup: dict | None = None
) -> dict:
    message = {"chat": {"id": int(chat_id)}, "message_id": message_id}
    if reply_markup is not None:
        message["reply_markup"] = reply_markup
    return {"id": "cb1", "data": data, "message": message}


class SendSrsCardTest(unittest.TestCase):
    def test_builds_one_row_of_all_four_actions(self):
        with patch.object(tb, "_request_with_retry") as mock_request:
            tb.send_srs_card("https://api.telegram.org/botX", "555", "v", "場合", "text")
        keyboard = mock_request.call_args.kwargs["json"]["reply_markup"]["inline_keyboard"]
        self.assertEqual(len(keyboard), 1)
        self.assertEqual(
            [btn["callback_data"] for btn in keyboard[0]],
            ["srs|v|場合|again", "srs|v|場合|hard", "srs|v|場合|good", "srs|v|場合|easy"],
        )


class HandleSrsCallbackTest(unittest.TestCase):
    def setUp(self):
        fd, self.kanji_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.kanji_path)  # srs.load_state must handle "doesn't exist yet"
        self.addCleanup(lambda: os.path.exists(self.kanji_path) and os.unlink(self.kanji_path))
        patcher = patch.dict(
            tb.SRS_KIND_PATHS, {"k": (self.kanji_path, [1, 3, 8, 20])}, clear=False
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_good_rating_is_persisted_to_the_deck_state_file(self):
        with patch.object(tb, "_request_with_retry") as mock_request:
            tb.handle_srs_callback("https://api.telegram.org/botX", _callback("srs|k|決|good"), ALLOWED_CHAT_ID)
        state = srs.load_state(self.kanji_path)
        self.assertEqual(state["決"]["box"], 1)
        self.assertEqual(state["決"]["last_result"], "good")
        # answerCallbackQuery + editMessageReplyMarkup, both fire-and-forget POSTs
        self.assertEqual(mock_request.call_count, 2)
        answer_call = mock_request.call_args_list[0]
        self.assertIn("answerCallbackQuery", answer_call.args[1])
        self.assertIn("next review in", answer_call.kwargs["json"]["text"])

    def test_buttons_are_stripped_from_the_original_message(self):
        with patch.object(tb, "_request_with_retry") as mock_request:
            tb.handle_srs_callback("https://api.telegram.org/botX", _callback("srs|k|決|easy", message_id=99), ALLOWED_CHAT_ID)
        edit_call = mock_request.call_args_list[1]
        self.assertIn("editMessageReplyMarkup", edit_call.args[1])
        self.assertEqual(edit_call.kwargs["json"]["message_id"], 99)
        self.assertEqual(edit_call.kwargs["json"]["reply_markup"]["inline_keyboard"], [])

    def test_unauthorized_chat_is_ignored_without_touching_state(self):
        with patch.object(tb, "_request_with_retry") as mock_request:
            tb.handle_srs_callback(
                "https://api.telegram.org/botX", _callback("srs|k|決|good", chat_id="999"), ALLOWED_CHAT_ID
            )
        self.assertEqual(srs.load_state(self.kanji_path), {})
        # still acknowledges the callback so Telegram stops spinning, but no state write
        self.assertEqual(mock_request.call_count, 1)

    def test_malformed_callback_data_is_ignored(self):
        with patch.object(tb, "_request_with_retry") as mock_request:
            tb.handle_srs_callback("https://api.telegram.org/botX", _callback("not-an-srs-payload"), ALLOWED_CHAT_ID)
        self.assertEqual(srs.load_state(self.kanji_path), {})
        self.assertEqual(mock_request.call_count, 1)  # just the empty acknowledgement

    def test_unknown_deck_kind_is_ignored(self):
        with patch.object(tb, "_request_with_retry") as mock_request:
            tb.handle_srs_callback("https://api.telegram.org/botX", _callback("srs|x|決|good"), ALLOWED_CHAT_ID)
        self.assertEqual(srs.load_state(self.kanji_path), {})

    def test_unknown_action_is_ignored(self):
        with patch.object(tb, "_request_with_retry") as mock_request:
            tb.handle_srs_callback("https://api.telegram.org/botX", _callback("srs|k|決|whoops"), ALLOWED_CHAT_ID)
        self.assertEqual(srs.load_state(self.kanji_path), {})

    def test_multi_row_keyboard_tap_only_removes_that_row_not_the_others(self):
        # Every current card has exactly one row, but the removal logic
        # is generic - if a message ever carries several rows, tapping
        # one must leave the others tappable rather than clearing all.
        keyboard = [
            [{"text": "✓ 決", "callback_data": "srs|k|決|good"}],
            [{"text": "✓ 作", "callback_data": "srs|k|作|good"}],
            [{"text": "✓ 動", "callback_data": "srs|k|動|good"}],
        ]
        with patch.object(tb, "_request_with_retry") as mock_request:
            tb.handle_srs_callback(
                "https://api.telegram.org/botX",
                _callback("srs|k|決|good", reply_markup={"inline_keyboard": keyboard}),
                ALLOWED_CHAT_ID,
            )
        edit_call = mock_request.call_args_list[1]
        remaining = edit_call.kwargs["json"]["reply_markup"]["inline_keyboard"]
        self.assertEqual(len(remaining), 2)
        self.assertNotIn("srs|k|決|good", [row[0]["callback_data"] for row in remaining])
        self.assertIn("srs|k|作|good", [row[0]["callback_data"] for row in remaining])


if __name__ == "__main__":
    unittest.main()
