"""Browser-based SRS review server - runs as a background thread inside
telegram_bot.py's own process (see start_server(), called from main()),
so reviewing a batch of cards doesn't need a separate deployment or
process to keep alive. Fronted by Caddy on the VPS (auto HTTPS via Let's
Encrypt for a free sslip.io hostname - see deploy/cron-notifications.md);
this server itself only ever binds 127.0.0.1, never the public interface
directly.

Why a review link instead of kanji_drip.py's old image + <tg-spoiler>
card + Again/Hard/Good/Easy row (still there as the fallback when
WEBAPP_BASE_URL is unset): that's three chunky messages *per item*,
which clutters the chat fast, especially once a run sends several items
at once (vocab's whole point). This collapses one run's whole batch into
ONE short message with a single "Open in browser" button (send_review_
link_card in telegram_bot.py); tapping it launches the system browser
(Safari/Chrome) to a swipeable flashcard deck - one card visible at a
time, swipe or Prev/Next between them, tap to reveal, rate to advance.
Each rating submits straight to /api/submit instead of round-tripping
through a Telegram callback_query.

(This used to also offer a Telegram-native Mini App button - a `web_app`
inline button that opened the same page inside Telegram's own in-app
webview, authenticated via Telegram's signed `initData`. Dropped for
being an extra, rarely-used auth path and code branch for what's the
same page either way - browser-only now, one auth mechanism.)

Since there's no Telegram-supplied identity outside Telegram itself,
each link is signed instead (see _sign_link / _verify_link): an HMAC
over kind+keys+an expiry, keyed by the bot token, appended as `exp`/
`sig` query params by build_review_url. The page embeds those same
values so a submit can echo them back as proof the request originated
from a genuine, unexpired link.

kanji_drip.py / grammar_drip.py / vocab_drip.py all send one link per
run's batch (see each module's find_row / build_body_lines, which this
module imports lazily - see _load_drip_module - to avoid a circular
import with telegram_bot.py at process startup).
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
import llm_enrich

_SIMPLE_AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCHEDULED_DIR = os.path.join(_SIMPLE_AGENT_DIR, "scheduled")

WEBAPP_HOST = "127.0.0.1"
WEBAPP_PORT = int(os.environ.get("WEBAPP_PORT") or "8080")
# How long a review link stays valid (see _sign_link) - generous, since
# an SRS card may sit unrated in the chat for a while before you get to it.
LINK_TTL_SECONDS = 30 * 24 * 3600

# kind -> everything webapp.py needs to render that deck's cards and
# record a rating, without importing kanji_drip.py/grammar_drip.py/
# vocab_drip.py at module load time (see _load_drip_module).
_KIND_CONFIG = {
    "k": {
        "module": "kanji_drip", "env_var": "KANJI_SRS_PATH", "default_path": ".kanji_srs.json",
        "box_hours": srs.BOX_HOURS_KANJI, "front_field": "kanji", "has_image": True, "label": "Kanji",
        "enrich_env_var": "KANJI_ENRICH_CACHE_PATH", "enrich_default_path": ".kanji_enrich_cache.json",
    },
    "g": {
        "module": "grammar_drip", "env_var": "GRAMMAR_SRS_PATH", "default_path": ".grammar_srs.json",
        "box_hours": srs.BOX_HOURS_GRAMMAR, "front_field": "grammar", "has_image": False, "label": "Grammar",
        "enrich_env_var": "GRAMMAR_ENRICH_CACHE_PATH", "enrich_default_path": ".grammar_enrich_cache.json",
    },
    "v": {
        "module": "vocab_drip", "env_var": "VOCAB_SRS_PATH", "default_path": ".vocab_srs.json",
        "box_hours": srs.BOX_HOURS_VOCAB, "front_field": "word", "has_image": False, "label": "Vocab",
    },
}


def _load_drip_module(kind: str):
    if _SCHEDULED_DIR not in sys.path:
        sys.path.insert(0, _SCHEDULED_DIR)
    return importlib.import_module(_KIND_CONFIG[kind]["module"])


def _srs_state_path(kind: str) -> str:
    cfg = _KIND_CONFIG[kind]
    return os.environ.get(cfg["env_var"]) or os.path.join(_SIMPLE_AGENT_DIR, cfg["default_path"])


def _enrich_cache(kind: str) -> dict:
    """The kanji_drip.py/grammar_drip.py enrichment cache (see
    llm_enrich.py) for `kind`, or {} for a deck with no enrichment
    support (currently vocab) - so a card renders with its curated
    content alone, exactly as if enrichment were never generated."""
    cfg = _KIND_CONFIG[kind]
    if "enrich_env_var" not in cfg:
        return {}
    path = os.environ.get(cfg["enrich_env_var"]) or os.path.join(_SIMPLE_AGENT_DIR, cfg["enrich_default_path"])
    return llm_enrich.load_cache(path)


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


def _sign_link(kind: str, keys_raw: str, exp: int, bot_token: str) -> str:
    """HMAC over exactly what a request can present back (kind, the raw
    comma-joined keys string as it appears in the URL, and the expiry) -
    keyed by the bot token. Used both to mint a link (build_review_url)
    and to check one presented later (_verify_link)."""
    payload = f"{kind}|{keys_raw}|{exp}"
    return hmac.new(bot_token.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _verify_link(kind: str, keys_raw: str, exp: str, sig: str, bot_token: str) -> bool:
    try:
        exp_int = int(exp)
    except (TypeError, ValueError):
        return False
    if time.time() > exp_int:
        return False
    expected = _sign_link(kind, keys_raw, exp_int, bot_token)
    return hmac.compare_digest(expected, sig or "")


def build_review_url(base_url: str, kind: str, keys: list, bot_token: str) -> str:
    """One link for a whole batch - kanji_drip.py / grammar_drip.py /
    vocab_drip.py all call this instead of sending one message per item.
    `keys` order is preserved as the card order in the deck. Signed (see
    _sign_link) so the link works as its own bearer credential (see
    module docstring)."""
    keys_raw = ",".join(keys)
    exp = int(time.time()) + LINK_TTL_SECONDS
    sig = _sign_link(kind, keys_raw, exp, bot_token)
    keys_param = urllib.parse.quote(keys_raw, safe=",")
    return f"{base_url.rstrip('/')}/review?kind={kind}&keys={keys_param}&exp={exp}&sig={sig}"


# __KIND_LABEL__/__CARDS_JSON__ are replaced with .replace(), not
# str.format() - the inline JS below is full of literal { } braces.
PAGE_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Review</title>
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
  var CARDS = JSON.parse(document.getElementById('cards-data').textContent);
  var KIND = "__KIND__";
  // The link's own signature is the credential (see module docstring) -
  // echoed back on every submit as proof this came from a genuine link.
  var KEYS_RAW = __KEYS_RAW__;
  var EXP = __EXP__;
  var SIG = __SIG__;
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
        keysRaw: KEYS_RAW,
        exp: EXP,
        sig: SIG,
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
    root.innerHTML = '<div class="finished">All done for this batch 🎉<br><span style="font-size:14px;opacity:0.6;">You can close this tab now.</span></div>';
    document.querySelector('.nav').style.display = 'none';
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
        keys_raw = (qs.get("keys") or [""])[0]
        keys = [k for k in keys_raw.split(",") if k]
        exp = (qs.get("exp") or [""])[0]
        sig = (qs.get("sig") or [""])[0]
        if kind not in _KIND_CONFIG or not keys:
            self._plain(400, "bad request")
            return
        if not _verify_link(kind, keys_raw, exp, sig, self.bot_token):
            self._plain(401, "invalid or expired link")
            return

        cfg = _KIND_CONFIG[kind]
        module = _load_drip_module(kind)
        state_path = _srs_state_path(kind)
        state = srs.load_state(state_path)
        enrich_cache = _enrich_cache(kind)

        cards = []
        for key in keys:
            row = module.find_row(key)
            if row is None:
                continue  # e.g. content CSV changed since the link was sent
            rec = state.get(key, {})
            # NOT marked seen here - just opening the deck shouldn't start
            # the unrated-resurface timer for every card in it (see
            # srs.mark_seen). That only happens once you actually rate a
            # specific card, in _handle_submit below.
            body_html = "\n".join(module.build_body_lines(row, enrich_cache.get(key))).replace("\n", "<br>")
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
        page = (
            PAGE_TEMPLATE.replace("__KIND__", kind)
            .replace("__CARDS_JSON__", cards_json)
            .replace("__KEYS_RAW__", json.dumps(keys_raw))
            .replace("__EXP__", json.dumps(exp))
            .replace("__SIG__", json.dumps(sig))
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

        kind = payload.get("kind", "")
        key = payload.get("key", "")
        action = payload.get("action", "")
        if kind not in _KIND_CONFIG or not key or action not in srs.ACTIONS:
            self._json(400, {"error": "bad request"})
            return

        # The link's own signature is the credential (see module
        # docstring / _verify_link). The key being rated must actually be
        # one this specific link named, so a valid-but-unrelated link
        # can't be reused to rate something else.
        keys_raw = payload.get("keysRaw", "")
        exp = payload.get("exp", "")
        sig = payload.get("sig", "")
        if not _verify_link(kind, keys_raw, exp, sig, self.bot_token) or key not in keys_raw.split(","):
            self._json(401, {"error": "invalid or expired link"})
            return

        box_hours = _KIND_CONFIG[kind]["box_hours"]
        path = _srs_state_path(kind)
        state = srs.load_state(path)
        now = srs.now_utc()
        # First time this specific card is actually rated - see
        # srs.mark_seen. record_review below sets "due" regardless, which
        # takes priority over the unrated-resurface path anyway, but this
        # keeps first_seen_at accurate for a card that's rated without
        # ever having been "seen" some other way.
        srs.mark_seen(state, key, now)
        interval_hours = srs.record_review(state, key, action, box_hours, now)
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


def start_server(bot_token: str, port: int = WEBAPP_PORT) -> ThreadingHTTPServer:
    """Starts the review server in a daemon background thread and returns
    immediately - called once from telegram_bot.py's main(), alongside
    (not instead of) the existing getUpdates long-polling loop. Binds
    127.0.0.1 only; Caddy is what actually exposes it over HTTPS (see
    deploy/cron-notifications.md)."""
    ReviewHandler.bot_token = bot_token
    server = ThreadingHTTPServer((WEBAPP_HOST, port), ReviewHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[webapp] review server listening on {WEBAPP_HOST}:{port}")
    return server
