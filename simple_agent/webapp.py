"""Telegram Mini App server for SRS review cards - runs as a background
thread inside telegram_bot.py's own process (see start_server(), called
from main()), so reviewing a batch of cards doesn't need a separate
deployment or process to keep alive. Fronted by Caddy on the VPS (auto
HTTPS via Let's Encrypt for a free sslip.io hostname - see
deploy/cron-notifications.md); this server itself only ever binds
127.0.0.1, never the public interface directly.

Why a Mini App instead of kanji_drip.py's old image + <tg-spoiler> card +
Again/Hard/Good/Easy row (still there as the fallback when
WEBAPP_BASE_URL is unset): that's three chunky messages *per item*,
which clutters the chat fast, especially once a run sends several items
at once (vocab's whole point). This collapses one run's whole batch into
ONE short message with a single "Review" button; tapping it opens a
swipeable flashcard deck inside Telegram itself (a Mini App) - one card
visible at a time, swipe or Prev/Next between them, tap to reveal, rate
to advance. Each rating submits straight to /api/submit instead of
round-tripping through a Telegram callback_query.

Auth: Telegram signs the page's `initData` (see verify_init_data) with
an HMAC keyed off the bot token, so the page can't be opened by anyone
but you and can't be spoofed by hand-crafting a request - same guarantee
TELEGRAM_ALLOWED_CHAT_ID gives the polling loop. For a private chat with
the bot, the WebApp's user id IS your chat id, so that's what gets
compared against TELEGRAM_ALLOWED_CHAT_ID.

kanji_drip.py / grammar_drip.py / vocab_drip.py all send one Mini App
link per run's batch (see each module's find_row / build_body_lines,
which this module imports lazily - see _load_drip_module - to avoid a
circular import with telegram_bot.py at process startup).
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

# kind -> everything webapp.py needs to render that deck's cards and
# record a rating, without importing kanji_drip.py/grammar_drip.py/
# vocab_drip.py at module load time (see _load_drip_module).
_KIND_CONFIG = {
    "k": {
        "module": "kanji_drip", "env_var": "KANJI_SRS_PATH", "default_path": ".kanji_srs.json",
        "box_hours": srs.BOX_HOURS_KANJI, "front_field": "kanji", "has_image": True, "label": "Kanji",
    },
    "g": {
        "module": "grammar_drip", "env_var": "GRAMMAR_SRS_PATH", "default_path": ".grammar_srs.json",
        "box_hours": srs.BOX_HOURS_GRAMMAR, "front_field": "grammar", "has_image": False, "label": "Grammar",
    },
    "v": {
        "module": "vocab_drip", "env_var": "VOCAB_SRS_PATH", "default_path": ".vocab_srs.json",
        "box_hours": srs.BOX_HOURS_VOCAB, "front_field": "word", "has_image": False, "label": "Vocab",
    },
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
    return importlib.import_module(_KIND_CONFIG[kind]["module"])


def _srs_state_path(kind: str) -> str:
    cfg = _KIND_CONFIG[kind]
    return os.environ.get(cfg["env_var"]) or os.path.join(_SIMPLE_AGENT_DIR, cfg["default_path"])


def _status_badge(rec: dict, box_hours: list) -> str:
    """A card's status can't be trusted to the URL (the link may be
    opened well after send time, or re-opened later), so this is
    recomputed straight from the deck's current SRS state instead: no
    "due" timestamp yet means never rated (a brand-new item), otherwise
    it's a review at whatever box it's currently in."""
    if not rec or not rec.get("due"):
        return "\U0001f210 New"
    box = rec.get("box", 0)
    return f"\U0001f501 Review (box {box + 1}/{len(box_hours)})"


def build_review_url(base_url: str, kind: str, keys: list) -> str:
    """One Mini App link for a whole batch - kanji_drip.py / grammar_
    drip.py / vocab_drip.py all call this instead of sending one message
    per item. `keys` order is preserved as the card order in the deck."""
    keys_param = urllib.parse.quote(",".join(keys), safe=",")
    return f"{base_url.rstrip('/')}/review?kind={kind}&keys={keys_param}"


# __KIND_LABEL__/__CARDS_JSON__ are replaced with .replace(), not
# str.format() - the inline JS below is full of literal { } braces.
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
  .deck { max-width: 480px; margin: 0 auto; }
  .progress { text-align: center; font-size: 14px; opacity: 0.65; margin-bottom: 12px; }
  .card { display: none; }
  .card.active { display: block; }
  .badge { font-size: 15px; opacity: 0.75; margin-bottom: 12px; }
  .kanji-img { width: 100%; max-width: 260px; display: block; margin: 0 auto 18px; border-radius: 12px; }
  .front-text {
    font-size: 32px; font-weight: 700; text-align: center; padding: 28px 12px;
    margin-bottom: 18px; background: #fff; border: 1px solid #e5e5e0; border-radius: 12px;
  }
  .answer {
    white-space: pre-line; line-height: 1.6; font-size: 16px;
    padding: 16px; border-radius: 12px; background: #fff; border: 1px solid #e5e5e0;
    filter: blur(6px); user-select: none; cursor: pointer; transition: filter 0.15s;
    min-height: 24px;
  }
  .answer.revealed { filter: none; user-select: text; cursor: text; }
  .hint { text-align: center; font-size: 13px; opacity: 0.6; margin: 8px 0 20px; }
  .ratings { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
  .ratings button {
    padding: 12px 4px; border: none; border-radius: 10px; font-size: 14px; font-weight: 600;
    cursor: pointer; color: #fff;
  }
  .ratings button:disabled { opacity: 0.4; }
  .again { background: #d64545; }
  .hard { background: #d68c2e; }
  .good { background: #3a9152; }
  .easy { background: #2e7dd6; }
  .nav { display: flex; justify-content: space-between; margin-top: 20px; }
  .nav button {
    padding: 10px 18px; border: 1px solid #ddd; border-radius: 10px; background: #fff;
    font-size: 14px; cursor: pointer;
  }
  .nav button:disabled { opacity: 0.35; }
  .done-check { text-align: center; font-size: 13px; color: #3a9152; margin-top: 10px; min-height: 18px; }
  .toast { text-align: center; margin-top: 16px; font-size: 15px; min-height: 20px; }
  .finished { text-align: center; padding: 60px 12px; font-size: 18px; }
</style>
</head>
<body>
<div class="deck">
  <div class="progress" id="progress"></div>
  <div id="deckRoot"></div>
  <div class="nav">
    <button id="prevBtn" onclick="go(-1)">&#8249; Prev</button>
    <button id="nextBtn" onclick="go(1)">Next &#8250;</button>
  </div>
  <div class="toast" id="toast"></div>
</div>
<script type="application/json" id="cards-data">__CARDS_JSON__</script>
<script>
  var tg = window.Telegram && window.Telegram.WebApp;
  if (tg) { tg.ready(); tg.expand(); }

  var CARDS = JSON.parse(document.getElementById('cards-data').textContent);
  var KIND = "__KIND__";
  var index = 0;
  var revealed = CARDS.map(function () { return false; });
  var rated = CARDS.map(function () { return false; });

  var root = document.getElementById('deckRoot');
  var progress = document.getElementById('progress');
  var toast = document.getElementById('toast');

  CARDS.forEach(function (card, i) {
    var el = document.createElement('div');
    el.className = 'card';
    el.id = 'card-' + i;

    var badge = document.createElement('div');
    badge.className = 'badge';
    badge.textContent = card.badge;
    el.appendChild(badge);

    if (card.image) {
      var img = document.createElement('img');
      img.className = 'kanji-img';
      img.src = card.image;
      img.alt = card.front;
      el.appendChild(img);
    } else {
      var front = document.createElement('div');
      front.className = 'front-text';
      front.textContent = card.front;
      el.appendChild(front);
    }

    var answer = document.createElement('div');
    answer.className = 'answer';
    answer.innerHTML = card.body_html;
    answer.addEventListener('click', function () { reveal(i); });
    el.appendChild(answer);

    var hint = document.createElement('div');
    hint.className = 'hint';
    hint.textContent = 'Tap the card to reveal, then rate your recall';
    el.appendChild(hint);

    var ratings = document.createElement('div');
    ratings.className = 'ratings';
    [['again', 'Again'], ['hard', 'Hard'], ['good', 'Good'], ['easy', 'Easy']].forEach(function (pair) {
      var btn = document.createElement('button');
      btn.className = pair[0];
      btn.textContent = pair[1];
      btn.disabled = true;
      btn.onclick = function () { submitRating(i, pair[0]); };
      ratings.appendChild(btn);
    });
    el.appendChild(ratings);

    var doneCheck = document.createElement('div');
    doneCheck.className = 'done-check';
    el.appendChild(doneCheck);

    root.appendChild(el);
  });

  function elFor(i) { return document.getElementById('card-' + i); }

  function reveal(i) {
    if (revealed[i]) return;
    revealed[i] = true;
    var el = elFor(i);
    el.querySelector('.answer').classList.add('revealed');
    el.querySelector('.hint').textContent = 'Rate your recall';
    if (!rated[i]) {
      el.querySelectorAll('.ratings button').forEach(function (b) { b.disabled = false; });
    }
  }

  function render() {
    CARDS.forEach(function (_, i) { elFor(i).classList.toggle('active', i === index); });
    progress.textContent = 'Card ' + (index + 1) + ' of ' + CARDS.length;
    document.getElementById('prevBtn').disabled = index === 0;
    document.getElementById('nextBtn').disabled = index === CARDS.length - 1;
    toast.textContent = '';
  }

  function go(delta) {
    var next = index + delta;
    if (next < 0 || next >= CARDS.length) return;
    index = next;
    render();
  }

  function submitRating(i, action) {
    var el = elFor(i);
    el.querySelectorAll('.ratings button').forEach(function (b) { b.disabled = true; });
    fetch('/api/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        initData: tg ? tg.initData : '',
        kind: KIND,
        key: CARDS[i].key,
        action: action
      })
    })
      .then(function (r) { return r.json().then(function (body) { return { ok: r.ok, body: body }; }); })
      .then(function (res) {
        if (!res.ok) {
          toast.textContent = 'Error: ' + (res.body.error || 'failed to save');
          el.querySelectorAll('.ratings button').forEach(function (b) { b.disabled = false; });
          return;
        }
        rated[i] = true;
        el.querySelector('.done-check').textContent =
          '✓ ' + res.body.label + ' — next review in ' + res.body.interval;
        setTimeout(function () {
          if (index < CARDS.length - 1) {
            go(1);
          } else if (rated.every(function (r) { return r; })) {
            finish();
          }
        }, 500);
      })
      .catch(function () {
        toast.textContent = 'Network error — try again';
        el.querySelectorAll('.ratings button').forEach(function (b) { b.disabled = false; });
      });
  }

  function finish() {
    root.innerHTML = '<div class="finished">All done for this batch 🎉</div>';
    document.querySelector('.nav').style.display = 'none';
    setTimeout(function () { if (tg) tg.close(); }, 1200);
  }

  var touchStartX = null;
  document.addEventListener('touchstart', function (e) { touchStartX = e.changedTouches[0].screenX; });
  document.addEventListener('touchend', function (e) {
    if (touchStartX === null) return;
    var deltaX = e.changedTouches[0].screenX - touchStartX;
    if (Math.abs(deltaX) > 50) go(deltaX < 0 ? 1 : -1);
    touchStartX = null;
  });

  render();
</script>
</body>
</html>
"""


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
        keys = [k for k in (qs.get("keys") or [""])[0].split(",") if k]
        if kind not in _KIND_CONFIG or not keys:
            self._plain(400, "bad request")
            return

        cfg = _KIND_CONFIG[kind]
        module = _load_drip_module(kind)
        state = srs.load_state(_srs_state_path(kind))

        cards = []
        for key in keys:
            row = module.find_row(key)
            if row is None:
                continue  # e.g. content CSV changed since the link was sent
            rec = state.get(key, {})
            body_html = "\n".join(module.build_body_lines(row)).replace("\n", "<br>")
            card = {
                "key": key,
                "badge": _status_badge(rec, cfg["box_hours"]),
                "front": (row.get(cfg["front_field"]) or "").strip(),
                "body_html": body_html,
            }
            if cfg["has_image"]:
                card["image"] = f"/review/image?key={urllib.parse.quote(key)}"
            cards.append(card)

        if not cards:
            self._plain(404, "no cards found")
            return

        cards_json = json.dumps(cards, ensure_ascii=False).replace("</", "<\\/")
        page = PAGE_TEMPLATE.replace("__KIND__", kind).replace("__CARDS_JSON__", cards_json)
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

        box_hours = _KIND_CONFIG[kind]["box_hours"]
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
