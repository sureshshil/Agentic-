"""Regression tests for webapp.py - the Telegram Mini App server that
replaces kanji_drip.py/grammar_drip.py/vocab_drip.py's old per-item
messages (image+spoiler+button-row, or a spoiler card per word) with one
"Review" button that opens a whole batch as a swipeable flashcard deck
in-app (see webapp.py's module docstring).

verify_init_data is tested against hand-signed initData strings (same
HMAC construction Telegram itself uses - no live Telegram call needed).
The HTTP handler is tested by actually starting the server on a loopback
port and issuing real requests - simplest way to catch routing/wiring
bugs without mocking BaseHTTPRequestHandler's internals.

Run: python3 -m unittest tests.test_webapp -v   (from simple_agent/)
"""

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import webapp  # noqa: E402

BOT_TOKEN = "TEST:TOKEN"


def _sign(fields: dict, bot_token: str = BOT_TOKEN) -> str:
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode({**fields, "hash": signature})


class VerifyInitDataTest(unittest.TestCase):
    def test_correctly_signed_data_verifies(self):
        init_data = _sign({"auth_date": str(int(time.time())), "user": json.dumps({"id": 42})})
        result = webapp.verify_init_data(init_data, BOT_TOKEN)
        self.assertIsNotNone(result)
        self.assertEqual(result["user"]["id"], 42)

    def test_tampered_hash_is_rejected(self):
        init_data = _sign({"auth_date": str(int(time.time())), "user": json.dumps({"id": 42})})
        tampered = init_data[:-4] + "0000"
        self.assertIsNone(webapp.verify_init_data(tampered, BOT_TOKEN))

    def test_wrong_bot_token_is_rejected(self):
        init_data = _sign({"auth_date": str(int(time.time())), "user": json.dumps({"id": 42})})
        self.assertIsNone(webapp.verify_init_data(init_data, "OTHER:TOKEN"))

    def test_stale_auth_date_is_rejected(self):
        init_data = _sign({"auth_date": str(int(time.time()) - 999999), "user": json.dumps({"id": 42})})
        self.assertIsNone(webapp.verify_init_data(init_data, BOT_TOKEN))

    def test_missing_hash_is_rejected(self):
        self.assertIsNone(webapp.verify_init_data("auth_date=123&user=%7B%7D", BOT_TOKEN))


class BuildReviewUrlTest(unittest.TestCase):
    def test_batch_of_keys_round_trips_through_the_query_string(self):
        url = webapp.build_review_url("https://example.sslip.io", "k", ["決", "続", "増"])
        self.assertTrue(url.startswith("https://example.sslip.io/review?kind=k&keys="))
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(qs["keys"][0].split(","), ["決", "続", "増"])

    def test_trailing_slash_on_base_url_is_not_duplicated(self):
        url = webapp.build_review_url("https://example.sslip.io/", "k", ["決"])
        self.assertTrue(url.startswith("https://example.sslip.io/review?"))
        self.assertNotIn("//review", url.split("://", 1)[1])


class ReviewServerTest(unittest.TestCase):
    """Starts the real server on loopback and drives it with real HTTP
    requests - covers routing, each drip module's find_row lookup, and
    the initData -> srs.record_review path together, for all three
    kinds."""

    ALLOWED_CHAT_ID = "555"

    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.mkdtemp()
        cls.srs_paths = {
            "KANJI_SRS_PATH": os.path.join(cls.tmp_dir, "kanji_srs.json"),
            "GRAMMAR_SRS_PATH": os.path.join(cls.tmp_dir, "grammar_srs.json"),
            "VOCAB_SRS_PATH": os.path.join(cls.tmp_dir, "vocab_srs.json"),
        }
        for env_var, path in cls.srs_paths.items():
            os.environ[env_var] = path
        cls.port = 8098
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.server = webapp.start_server(BOT_TOKEN, cls.ALLOWED_CHAT_ID, port=cls.port)
        time.sleep(0.2)  # let the background thread actually start listening

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        for env_var in cls.srs_paths:
            del os.environ[env_var]

    def _post_submit(self, payload: dict):
        req = urllib.request.Request(
            f"{self.base}/api/submit",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req)
            return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def _get(self, path_and_query):
        try:
            resp = urllib.request.urlopen(f"{self.base}{path_and_query}")
            return resp.status, resp.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def test_review_page_serves_a_batch_of_kanji_as_one_deck(self):
        url = webapp.build_review_url("", "k", ["決", "続", "増"])
        status, body = self._get(url)
        self.assertEqual(status, 200)
        self.assertIn("submitRating", body)
        self.assertIn("/review/image?key=", body)
        # all three cards' JSON payloads landed in the page, in order
        cards_json = body.split('id="cards-data">', 1)[1].split("</script>", 1)[0]
        cards = json.loads(cards_json)
        self.assertEqual([c["key"] for c in cards], ["決", "続", "増"])
        self.assertTrue(all(c["image"] for c in cards))

    def test_review_page_serves_a_grammar_batch_without_images(self):
        url = webapp.build_review_url("", "g", ["〜ようになる"])
        status, body = self._get(url)
        self.assertEqual(status, 200)
        cards_json = body.split('id="cards-data">', 1)[1].split("</script>", 1)[0]
        cards = json.loads(cards_json)
        self.assertEqual(cards[0]["key"], "〜ようになる")
        self.assertEqual(cards[0]["front"], "〜ようになる")
        self.assertNotIn("image", cards[0])

    def test_review_page_serves_a_vocab_batch(self):
        url = webapp.build_review_url("", "v", ["関係"])
        status, body = self._get(url)
        self.assertEqual(status, 200)
        cards_json = body.split('id="cards-data">', 1)[1].split("</script>", 1)[0]
        cards = json.loads(cards_json)
        self.assertEqual(cards[0]["key"], "関係")

    def test_review_page_skips_unknown_keys_but_keeps_known_ones(self):
        url = webapp.build_review_url("", "k", ["決", "不明"])  # 不明 not in fixture
        status, body = self._get(url)
        self.assertEqual(status, 200)
        cards_json = body.split('id="cards-data">', 1)[1].split("</script>", 1)[0]
        cards = json.loads(cards_json)
        self.assertEqual([c["key"] for c in cards], ["決"])

    def test_review_page_404s_when_every_key_is_unknown(self):
        url = webapp.build_review_url("", "k", ["不明"])
        status, _ = self._get(url)
        self.assertEqual(status, 404)

    def test_review_page_400s_without_keys(self):
        status, _ = self._get("/review?kind=k")
        self.assertEqual(status, 400)

    def test_review_page_400s_for_unknown_kind(self):
        status, _ = self._get("/review?kind=x&keys=%E6%B1%BA")
        self.assertEqual(status, 400)

    def test_review_image_is_a_valid_png(self):
        resp = urllib.request.urlopen(f"{self.base}/review/image?key=%E6%B1%BA")
        self.assertEqual(resp.status, 200)
        self.assertTrue(resp.read().startswith(b"\x89PNG\r\n\x1a\n"))

    def test_submit_with_valid_init_data_records_review_and_returns_interval(self):
        init_data = _sign({"auth_date": str(int(time.time())), "user": json.dumps({"id": int(self.ALLOWED_CHAT_ID)})})
        status, body = self._post_submit({"initData": init_data, "kind": "k", "key": "決", "action": "good"})
        self.assertEqual(status, 200)
        self.assertEqual(body["label"], "Good")
        with open(self.srs_paths["KANJI_SRS_PATH"], encoding="utf-8") as f:
            state = json.load(f)
        self.assertIn("決", state)
        self.assertEqual(state["決"]["last_result"], "good")

    def test_submit_rejects_unauthorized_user(self):
        init_data = _sign({"auth_date": str(int(time.time())), "user": json.dumps({"id": 111})})
        status, body = self._post_submit({"initData": init_data, "kind": "k", "key": "決", "action": "good"})
        self.assertEqual(status, 403)

    def test_submit_rejects_invalid_init_data(self):
        status, body = self._post_submit({"initData": "not-signed-at-all", "kind": "k", "key": "決", "action": "good"})
        self.assertEqual(status, 401)

    def test_submit_rejects_unknown_action(self):
        init_data = _sign({"auth_date": str(int(time.time())), "user": json.dumps({"id": int(self.ALLOWED_CHAT_ID)})})
        status, body = self._post_submit({"initData": init_data, "kind": "k", "key": "決", "action": "whoops"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
