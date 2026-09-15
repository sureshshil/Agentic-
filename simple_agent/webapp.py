"""Telegram Mini App server for SRS review cards - runs as a background
thread inside telegram_bot.py's own process (see start_server(), called
from main()), so reviewing a card doesn't need a separate deployment or
process to keep alive. Fronted by Caddy on the VPS (auto HTTPS via Let's
Encrypt for a free sslip.io hostname - see deploy/cron-notifications.md);
this server itself only ever binds 127.0.0.1, never the public interface
directly.

Why a Mini App instead of kanji_drip.py's old image + <tg-spoiler> card +
Again/Hard/Good/Easy row: that's three chunky messages per item, which
clutters the chat fast. A Mini App collapses that to one short message
with a single "Review" button (send_web_app_card in telegram_bot.py);
tapping it opens this server's page inside Telegram itself (no browser
tab), and the rating is submitted straight to this server's /api/submit
instead of round-tripping through a Telegram callback_query.

Auth: Telegram signs the page's `initData` (see verify_init_data) with an
HMAC keyed off the bot token, so the page can't be opened by anyone but
you and can't be spoofed by hand-crafting a request - same guarantee
TELEGRAM_ALLOWED_CHAT_ID gives the polling loop. For a private chat with
the bot, the WebApp's user id IS your chat id, so that's what gets
compared against TELEGRAM_ALLOWED_CHAT_ID.

Only kanji_drip.py (kind="k") is wired up so far; grammar/vocab can
follow the same shape once ready - see _load_drip_module.
"""

import hashlib
import hmac
import html
import importlib
import json
import os
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import srs
import kanji_image

_SIMPLE_AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCHEDULED_DIR = os.path.join(_SIMPLE_AGENT_DIR, "scheduled")

WEBAPP_HOST = "127.0.0.1"
WEBAPP_PORT = int(os.environ.get("WEBAPP_PORT") or "8080")
INIT_DATA_MAX_AGE_SECONDS = 3600

# kind -> (drip module name under scheduled/, SRS state path env var,
# default state filename, that deck's box-hours table)
_KIND_CONFIG = {
    "k": ("kanji_drip", "KANJI_SRS_PATH", ".kanji_srs.json", srs.BOX_HOURS_KANJI),
}


def verify_init_data(init_data: str, bot_token: str, max_age_seconds: int = INIT_DATA_MAX_AGE_SECONDS):
    """Validates a Telegram WebApp `initData` string per Telegram's
    documented check (https://core.telegram.org/bots/webapps#validating-
    data-received-via-the-mini-app): HMAC-SHA256 over the sorted
    key=value pairs (all but `hash`), keyed by HMAC-SHA256("WebAppData",
    bot_token). Returns the parsed field dict (with `user` JSON-decoded)
    if genuine and fresher than `max_age_seconds`, else None."""
    try:
        pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    data = dict(pairs)
    received_hash = data.pop("hash", None)
    if not received_hash:
        return None

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    try:
        auth_date = int(data.get("auth_date", 0))
    except ValueError:
        return None
    if time.time() - auth_date > max_age_seconds:
        return None

    if "user" in data:
        try:
            data["user"] = json.loads(data["user"])
        except json.JSONDecodeError:
            return None
    return data


def _load_drip_module(kind: str):
    if _SCHEDULED_DIR not in sys.path:
        sys.path.insert(0, _SCHEDULED_DIR)
    return importlib.import_module(_KIND_CONFIG[kind][0])


def _srs_state_path(kind: str) -> str:
    _, env_var, default_name, _ = _KIND_CONFIG[kind]
    return os.environ.get(env_var) or os.path.join(_SIMPLE_AGENT_DIR, default_name)


# __KANJI__/__KEY__/__KIND__/__BODY_HTML__ are replaced with .replace(),
# not str.format() - the inline JS below is full of literal { } braces.
PAGE_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Review</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 16px; padding-bottom: calc(16px + env(safe-area-inset-bottom, 0px));
    background: #fafaf7; color: #1a1a1a;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  .card { max-width: 480px; margin: 0 auto; }
  .status { font-size: 15px; opacity: 0.75; margin-bottom: 12px; }
  .kanji-img { width: 100%; max-width: 280px; display: block; margin: 0 auto 20px; border-radius: 12px; }
  .answer {
    white-space: pre-line; line-height: 1.6; font-size: 16px;
    padding: 16px; border-radius: 12px; background: #fff; border: 1px solid #e5e5e0;
    filter: blur(6px); user-select: none; cursor: pointer; transition: filter 0.15s;
  }
  .answer.revealed { filter: none; user-select: text; cursor: text; }
  .hint { text-align: center; font-size: 13px; opacity: 0.6; margin: 8px 0 20px; }
  .ratings { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
  .ratings button {
    padding: 12px 4px; border: none; border-radius: 10px; font-size: 14px; font-weight: 600;
    cursor: pointer; color: #fff;
  }
  .ratings button:disabled { opacity: 0.5; }
  #again { background: #d64545; }
  #hard { background: #d68c2e; }
  #good { background: #3a9152; }
  #easy { background: #2e7dd6; }
  .toast { text-align: center; margin-top: 16px; font-size: 15px; min-height: 20px; }
</style>
</head>
<body>
<div class="card">
  <div class="status">__KIND_LABEL__</div>
  <img class="kanji-img" src="/review/image?key=__KEY_Q__" alt="__KANJI__">
  <div class="answer" id="answer">__BODY_HTML__</div>
  <div class="hint" id="hint">Tap the card to reveal, then rate your recall</div>
  <div class="ratings">
    <button id="again" onclick="submitRating('again')" disabled>Again</button>
    <button id="hard" onclick="submitRating('hard')" disabled>Hard</button>
    <button id="good" onclick="submitRating('good')" disabled>Good</button>
    <button id="easy" onclick="submitRating('easy')" disabled>Easy</button>
  </div>
  <div class="toast" id="toast"></div>
</div>
<script>
  var tg = window.Telegram && window.Telegram.WebApp;
  if (tg) { tg.ready(); tg.expand(); }

  var answerEl = document.getElementById('answer');
  var hintEl = document.getElementById('hint');
  var buttons = document.querySelectorAll('.ratings button');
  var revealed = false;

  answerEl.addEventListener('click', function () {
    if (revealed) return;
    revealed = true;
    answerEl.classList.add('revealed');
    hintEl.textContent = 'Rate your recall';
    buttons.forEach(function (b) { b.disabled = false; });
  });

  function submitRating(action) {
    buttons.forEach(function (b) { b.disabled = true; });
    fetch('/api/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        initData: tg ? tg.initData : '',
        kind: '__KIND__',
        key: '__KEY__',
        action: action
      })
    })
      .then(function (r) { return r.json().then(function (body) { return { ok: r.ok, body: body }; }); })
      .then(function (res) {
        var toast = document.getElementById('toast');
        if (!res.ok) {
          toast.textContent = 'Error: ' + (res.body.error || 'failed to save');
          buttons.forEach(function (b) { b.disabled = false; });
          return;
        }
        toast.textContent = res.body.label + ' — next review in ' + res.body.interval;
        setTimeout(function () { if (tg) tg.close(); }, 900);
      })
      .catch(function () {
        document.getElementById('toast').textContent = 'Network error — try again';
        buttons.forEach(function (b) { b.disabled = false; });
      });
  }
</script>
</body>
</html>
"""

_KIND_LABELS = {"k": "\U0001f210 Kanji review"}


class ReviewHandler(BaseHTTPRequestHandler):
    bot_token = None
    allowed_chat_id = None

    def log_message(self, fmt, *args):
        print("[webapp] " + (fmt % args))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/review":
            self._handle_review_page(parsed)
        elif parsed.path == "/review/image":
            self._handle_review_image(parsed)
        else:
            self._plain(404, "not found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/submit":
            self._handle_submit()
        else:
            self._plain(404, "not found")

    def _handle_review_page(self, parsed):
        qs = urllib.parse.parse_qs(parsed.query)
        kind = (qs.get("kind") or [""])[0]
        key = (qs.get("key") or [""])[0]
        if kind not in _KIND_CONFIG or not key:
            self._plain(400, "bad request")
            return

        module = _load_drip_module(kind)
        row = module.find_row(key)
        if row is None:
            self._plain(404, "card not found")
            return

        body_html = "\n".join(module.build_body_lines(row)).replace("\n", "<br>")
        page = (
            PAGE_TEMPLATE.replace("__KIND_LABEL__", _KIND_LABELS.get(kind, "Review"))
            .replace("__KANJI__", html.escape(key))
            .replace("__KEY_Q__", urllib.parse.quote(key))
            .replace("__KIND__", kind)
            .replace("__KEY__", key)
            .replace("__BODY_HTML__", body_html)
        )
        self._html(200, page)

    def _handle_review_image(self, parsed):
        qs = urllib.parse.parse_qs(parsed.query)
        key = (qs.get("key") or [""])[0]
        if not key:
            self._plain(400, "bad request")
            return
        png_bytes = kanji_image.render_kanji_png(key)
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(png_bytes)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(png_bytes)

    def _handle_submit(self):
        length = int(self.headers.get("Content-Length") or "0")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "malformed JSON body"})
            return

        verified = verify_init_data(payload.get("initData", ""), self.bot_token)
        if verified is None:
            self._json(401, {"error": "invalid or expired initData"})
            return

        user = verified.get("user") or {}
        if str(user.get("id", "")) != self.allowed_chat_id:
            self._json(403, {"error": "not allowed"})
            return

        kind = payload.get("kind", "")
        key = payload.get("key", "")
        action = payload.get("action", "")
        if kind not in _KIND_CONFIG or not key or action not in srs.ACTIONS:
            self._json(400, {"error": "bad request"})
            return

        box_hours = _KIND_CONFIG[kind][3]
        path = _srs_state_path(kind)
        state = srs.load_state(path)
        interval_hours = srs.record_review(state, key, action, box_hours, srs.now_utc())
        srs.save_state(path, state)
        print(f"[webapp] SRS: {kind}:{key} rated {action}, next due in {srs.format_interval(interval_hours)}")

        self._json(200, {"label": srs.LABELS[action], "interval": srs.format_interval(interval_hours)})

    def _plain(self, status, text):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, status, page):
        body = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_server(bot_token: str, allowed_chat_id: str, port: int = WEBAPP_PORT) -> ThreadingHTTPServer:
    """Starts the Mini App server in a daemon background thread and
    returns immediately - called once from telegram_bot.py's main(),
    alongside (not instead of) the existing getUpdates long-polling loop.
    Binds 127.0.0.1 only; Caddy is what actually exposes it over HTTPS
    (see deploy/cron-notifications.md)."""
    ReviewHandler.bot_token = bot_token
    ReviewHandler.allowed_chat_id = allowed_chat_id
    server = ThreadingHTTPServer((WEBAPP_HOST, port), ReviewHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[webapp] Mini App review server listening on {WEBAPP_HOST}:{port}")
    return server
