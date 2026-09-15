"""Regression tests for webapp.py - the Telegram Mini App server that
replaces kanji_drip.py's old image+spoiler+button-row card with a single
"Review" button that opens the card in-app (see webapp.py's module
docstring).

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


class ReviewServerTest(unittest.TestCase):
    """Starts the real server on loopback and drives it with real HTTP
    requests - covers routing, the kanji_drip.py lookup, and the
    initData -> srs.record_review path together."""

    ALLOWED_CHAT_ID = "555"

    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.mkdtemp()
        cls.srs_path = os.path.join(cls.tmp_dir, "kanji_srs.json")
        os.environ["KANJI_SRS_PATH"] = cls.srs_path
        cls.port = 8098
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.server = webapp.start_server(BOT_TOKEN, cls.ALLOWED_CHAT_ID, port=cls.port)
        time.sleep(0.2)  # let the background thread actually start listening

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        del os.environ["KANJI_SRS_PATH"]

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

    def test_review_page_serves_a_known_kanji(self):
        resp = urllib.request.urlopen(f"{self.base}/review?kind=k&key=%E6%B1%BA")  # 決
        body = resp.read().decode()
        self.assertEqual(resp.status, 200)
        self.assertIn("submitRating", body)
        self.assertIn("/review/image?key=", body)

    def test_review_page_404s_for_unknown_kanji(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f"{self.base}/review?kind=k&key=%E4%B8%8D")  # 不 - not in fixture
        self.assertEqual(ctx.exception.code, 404)

    def test_review_page_400s_without_key(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f"{self.base}/review?kind=k")
        self.assertEqual(ctx.exception.code, 400)

    def test_review_image_is_a_valid_png(self):
        resp = urllib.request.urlopen(f"{self.base}/review/image?key=%E6%B1%BA")
        self.assertEqual(resp.status, 200)
        self.assertTrue(resp.read().startswith(b"\x89PNG\r\n\x1a\n"))

    def test_submit_with_valid_init_data_records_review_and_returns_interval(self):
        init_data = _sign({"auth_date": str(int(time.time())), "user": json.dumps({"id": int(self.ALLOWED_CHAT_ID)})})
        status, body = self._post_submit({"initData": init_data, "kind": "k", "key": "決", "action": "good"})
        self.assertEqual(status, 200)
        self.assertEqual(body["label"], "Good")
        with open(self.srs_path, encoding="utf-8") as f:
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
