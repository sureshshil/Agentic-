# Weather alert, news digest & vocab drip as VPS cron jobs

`rain_alert.py` and `news_digest.py` (in `simple_agent/scheduled/`) were
built to run via GitHub Actions, pulling secrets from repo settings. This
runs them as cron jobs on the same VPS that already hosts
`telegram_bot.py` instead - same ntfy.sh notification channel as before,
nothing about delivery has changed.

Three new files, neither touching the originals:

- `rain_alert_cron.py` - a thin entry point that calls `rain_alert.py`'s
  unmodified `main()`, after loading env vars via `load_dotenv()` (same
  pattern `telegram_bot.py` itself uses) instead of requiring the
  crontab command to `source` a file into the shell.
- `news_digest_agent.py` - generates the digest using the real
  `telegram_bot.Agent` (same tools, same system prompt as your
  interactive bot) instead of `news_digest.py`'s bespoke Tavily+Claude
  call, but still delivers over ntfy.sh via `news_digest.py`'s own
  `send_notification()`, imported unmodified. Also uses `load_dotenv()`
  directly - no shell sourcing needed. `news_digest.py` itself is left
  as-is and untouched - run it directly instead (with the same
  shell-export approach as GitHub Actions used, since it doesn't call
  `load_dotenv()`) if you'd rather keep the simpler, cheaper, non-agent
  version.
- `vocab_drip.py` / `kanji_drip.py` / `grammar_drip.py` - active-recall
  cards drawn from the hand-curated TSV/CSV files in `simple_agent/` (no
  LLM call), all three sharing one Leitner-style engine
  (`scheduled/srs.py`): the word/character/pattern is shown plain,
  everything else (reading, meaning, mnemonic, compounds/nuance,
  examples) hidden under a Telegram spoiler tag so you have to actually
  try to recall it before revealing, plus inline button(s) to rate it.
  Due reviews always go out before any brand-new item, and at most
  `VOCAB_NEW_PER_DAY` / `KANJI_NEW_PER_DAY` / `GRAMMAR_NEW_PER_DAY` new
  items get introduced per UTC day so the due-queue can't outrun what
  the cadence can actually clear. If a card is never rated it doesn't
  vanish - after `*_UNRATED_RESURFACE_HOURS` (default 3h each) it
  resurfaces as a reminder card instead of silently wasting that day's
  new-item slot.

  The one difference between them: kanji/grammar send **one** item per
  push; vocab's 700-word deck is much bigger and a single word per push
  would take far too long to cycle through, so `vocab_drip.py` sends
  `VOCAB_BATCH_SIZE` words (default 7) per run instead - but each word
  still goes out as its OWN Telegram message (a short header message
  announces the batch first). Telegram only ever attaches a keyboard to
  the bottom of the message it's sent with, so bundling several words
  into one message would pile every word's buttons into one block below
  all the text, forcing a scroll back up/down to match a button to its
  word. One message per word instead puts each word's Again/Hard/Good/
  Easy row right under it - no scrolling. Skip a word entirely and it
  resurfaces later as a reminder, same mechanism as above.

  (`../.vocab_sent_words.json`, the old plain-round-robin history file
  from before this SRS rewrite, is no longer read - delete it whenever.)
  There's no quiet-hours logic in the scripts themselves - that's a cron
  scheduling choice (see step 3 below); the example crontab only fires
  during 07:00-23:00 JST. State lives in `../.vocab_srs.json` /
  `../.kanji_srs.json` / `../.grammar_srs.json` (gitignored). **The
  button taps are handled by `telegram_bot.py`'s own long-polling loop**
  (`handle_srs_callback`), not by these scripts - `telegram_bot.py` must
  actually be running (e.g. as `telegram-bot.service`) for ratings to be
  recorded; the drip scripts themselves only ever send.

All new scripts load env vars from two files, same
fill-in-what's-unset behavior as `telegram_bot.py`'s own `load_dotenv()`
call (real environment variables always win):

- `../telegram_bot.env` - reuses `GOOGLE_APPLICATION_CREDENTIALS`,
  `GCP_PROJECT_ID`, `BRAVE_API_KEY`, `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_ALLOWED_CHAT_ID`, etc. that are already there for the bot.
- `../deploy/scheduled.env` - the handful of vars only these scripts
  need (`NTFY_TOPIC`, `ALERT_LOCATION`, `RAIN_THRESHOLD_MM`,
  `NEWS_QUERY`, `VOCAB_LEVEL`, `VOCAB_COUNT`) - see
  `scheduled.env.example`.

## Setup

1. Copy the example and fill in your ntfy.sh topic (see notebook 07 if
   you haven't set one up):
   ```bash
   cp simple_agent/deploy/scheduled.env.example simple_agent/deploy/scheduled.env
   # then edit simple_agent/deploy/scheduled.env
   ```

2. Test both manually first - no need to source anything, `load_dotenv()`
   handles it:
   ```bash
   cd /path/to/Agentic-/simple_agent/scheduled
   python3 rain_alert_cron.py
   python3 news_digest_agent.py
   ```
   ```bash
   python3 vocab_drip.py
   python3 kanji_drip.py
   python3 grammar_drip.py
   ```
   `rain_alert_cron.py` only sends a notification if rain above
   threshold is actually forecast within the next
   `ALERT_LOOKAHEAD_HOURS` - lower `RAIN_THRESHOLD_MM` temporarily in
   `scheduled.env` if you want to force a test push. Note that a
   successful send starts the `ALERT_COOLDOWN_MIN` cooldown (default 180
   min), so a second test run right afterwards will deliberately stay
   quiet; delete `../.rain_alert_state.json` to clear it.
   `news_digest_agent.py` always sends. `vocab_drip.py` / `kanji_drip.py`
   / `grammar_drip.py` send unless nothing is due AND today's new-item
   cap is already used up (rare on a fresh deck - delete
   `../.vocab_srs.json` / `../.kanji_srs.json` / `../.grammar_srs.json`
   to reset and force a send). Tapping their inline buttons only does
   anything if `telegram_bot.py` is already running and listening.

3. Add cron entries. Edit the crontab for whichever user runs
   `telegram_bot.py` (`crontab -e`, or `sudo -u <user> crontab -e` if it
   runs under a dedicated service user per `telegram-bot.service`):

   ```cron
   # Weather alert - every 30 minutes
   cron */30 * * * * cd /home/projects/Agentic-/simple_agent/scheduled && python3 rain_alert_cron.py >> /var/log/weather-alert.log 2>&1

   # News digest - once a day at 08:00 (agent-generated; swap in
   # news_digest.py for the simpler, cheaper, non-agent version instead -
   # but that one still needs the shell-export approach below since it
   # doesn't call load_dotenv())
   0 8 * * * cd /home/projects/Agentic-/simple_agent/scheduled && python3 news_digest_agent.py >> /var/log/news-digest.log 2>&1

   # Vocab active-recall drip (7-word batch) - every 2 hours, but only
   # 07:00-23:00 JST (0,2,4,...,22 minus the overnight hours) - adjust
   # this hour list to your own waking hours/timezone; there's no
   # quiet-hours logic in the script itself.
   0 0,2,4,6,8,10,12,14,22 * * * cd /home/projects/Agentic-/simple_agent/scheduled && python3 vocab_drip.py >> /var/log/vocab-drip.log 2>&1

   # Kanji active-recall drip (one card) - every hour within that same window
   0 22,23,0-14 * * * cd /home/projects/Agentic-/simple_agent/scheduled && python3 kanji_drip.py >> /var/log/kanji-drip.log 2>&1

   # Grammar active-recall drip (one card) - every 2 hours, offset 30min
   # from vocab so the two never land in the same minute
   30 0,2,4,6,8,10,12,14,22 * * * cd /home/projects/Agentic-/simple_agent/scheduled && python3 grammar_drip.py >> /var/log/grammar-drip.log 2>&1
   ```

   Replace `/path/to/Agentic-` with the repo's actual path on the VPS,
   and `python3` with a full interpreter path (`which python3`) if
   you're using a virtualenv.

Cron has no journal the way systemd does - that's what the `>> ... 2>&1`
redirect above is for; check those log files if an expected run doesn't
show up as an ntfy.sh push. If you'd rather have `systemctl
status`/`journalctl` visibility instead (consistent with
`telegram-bot.service`), systemd timers are a reasonable alternative,
just noticeably more boilerplate for two short-lived scripts than plain
cron.

## Optional: archive each drip to Notion and a Google Doc

Each of `vocab_drip.py` / `kanji_drip.py` / `grammar_drip.py` can push its
drip to two more places after the Telegram message, handled by its own
sink module (`scheduled/vocab_sinks.py` / `kanji_sinks.py` /
`grammar_sinks.py` - same shape, one per deck since each has its own
fields and its own Notion database, but they all share one
`NOTION_API_KEY` and one `GOOGLE_SERVICE_ACCOUNT_JSON`):

- **Notion database** - a browsable archive: one row **per item**, columns
  matching that deck's own fields (JLPT set on all three). The data comes
  straight from that run's TSV/CSV row(s); if the Notion API call fails
  the run's row(s) are just skipped and the Telegram drip is unaffected.
  - vocab: `Reading` / `Meaning` / `Particle` (助詞) / `Example 1-3` / `Type` / `JLPT`
  - kanji: `Reading` / `Meaning` / `Component` (構成) / `Confusable` (似ている) / `Words` / `Example` / `JLPT`
  - grammar: `Formation` (形) / `Meaning` / `Nuance` / `Contrast` (対比) / `Example 1-3` / `JLPT`
- **Google Doc** - one ever-growing doc per deck to use as a
  **NotebookLM** source for audio overviews, flashcards, infographics.
  NotebookLM re-syncs a Drive doc on demand, so this is one source you
  refresh, not a file you re-upload. When you only want the latest batch,
  steer the generation at "today's entries". Each doc is formatted for
  the model, not a person: a one-time preamble paragraph (added when the
  doc is empty) explaining what the document is and what it's for, a real
  `Heading 2` per item, and explicit English field labels. Vocab's
  example sentences are written three ways (normal Japanese, kana-only
  for pronunciation, English); kanji/grammar keep the furigana stripped
  but don't split out a kana-only line.

All six sinks are **best-effort and independent**: each is skipped unless
its own deck's env vars are set, and if one fails it's logged to the cron
log while the Telegram drip (and every other sink) still go through.
Nothing here touches `telegram_bot.py` or the other scheduled scripts.

Add the vars to `scheduled.env` (see `scheduled.env.example`).

### Notion setup (one-time, per deck)

1. Create an internal integration at
   <https://www.notion.so/my-integrations> - copy its token into
   `NOTION_API_KEY` (starts `secret_`). One integration/token covers all
   three decks.
2. Create a database (a full-page database is easiest) with the title
   property and columns listed above for that deck - exact names, any
   missing ones are just skipped. `select` columns (`Type`, `JLPT`) get
   their options created automatically as needed.
3. Open the database as a full page → `•••` menu → **Connections** →
   add your integration. Note: an internal integration's API token can
   only create a *new* database via the API under a page it already has
   access to - since these were created and shared from the Notion UI
   directly, that's not a blocker here, it just means a brand-new deck's
   database has to be created and shared the same way (UI first), not
   spun up by asking Claude to call the API cold.
4. Copy the database id from its URL - the 32-hex-char chunk before the
   `?v=` (the view id), with or without hyphens - into `NOTION_VOCAB_DB_ID`
   / `NOTION_KANJI_DB_ID` / `NOTION_GRAMMAR_DB_ID`.

### Google Doc setup (one-time, per deck)

1. In a Google Cloud project, enable the **Google Docs API**.
2. Create a **service account** and download a JSON key. Put the file on
   the VPS somewhere the cron user can read (e.g.
   `/home/deploy/vocab-gdoc-sa.json`) - it's a secret, keep it out of
   git (the repo's `*.json` ignore rule already covers it if it lands
   under the repo). Point `GOOGLE_SERVICE_ACCOUNT_JSON` at it - one
   service account covers all three decks.
3. Create each target Google Doc (blank is fine - the preamble is added
   automatically on its first append). **Share** each with the service
   account's `client_email` (from the JSON key) as **Editor**.
4. Copy each doc id from its URL (`docs.google.com/document/d/<id>/edit`)
   into `VOCAB_GDOC_ID` / `KANJI_GDOC_ID` / `GRAMMAR_GDOC_ID`.
5. `pip install google-auth` (it's in `requirements.txt`) - this is the
   only extra dependency, and only this sink needs it.

Test with a manual run - it prints one status line per configured sink:

```bash
cd /home/projects/Agentic-/simple_agent/scheduled && python3 vocab_drip.py
# ...
# Notion: added 4/4 word rows
# Google Doc: appended 4 words (2410 chars) at index 5120

python3 kanji_drip.py
# Notion: added 1/1 kanji rows
# Google Doc: appended 1 kanji (612 chars) at index 812

python3 grammar_drip.py
# Notion: added 1/1 grammar rows
# Google Doc: appended 1 pattern(s) (740 chars) at index 940
```

## Optional: SRS review via Telegram Mini App instead of chat clutter

By default each drip sends one chunky set of messages *per item* (kanji:
image + `<tg-spoiler>` card + button row; grammar: pattern + spoiler card
+ button row; vocab: a header message plus one spoiler block per word).
`webapp.py` replaces that with a Telegram **Mini App**: each run's whole
batch goes out as ONE short message with a single "Review" button that
opens a swipeable flashcard deck inside Telegram itself - one card at a
time (image for kanji, big text for grammar/vocab), tap to reveal, swipe
or Prev/Next between cards, rate to advance, auto-closes once every card
in the batch is rated. No browser tab, far less scrollback. Runs as a
background thread inside `telegram_bot.py`'s own process (same systemd
service, nothing new to manage or restart separately). Batch sizes are
each deck's own `*_BATCH_SIZE` env var (defaults: kanji 3, grammar 3,
vocab 10 - see `scheduled.env.example`).

Telegram only allows a Mini App button to open an **HTTPS** URL with a
real certificate - no plain HTTP, no self-signed cert - so this needs a
public HTTPS front door pointed at the VPS. If you don't already own a
domain, [sslip.io](https://sslip.io) gives you one for free with zero
signup: `<your-ip-with-dashes>.sslip.io` resolves straight to your VPS's
own public IP, and [Caddy](https://caddyserver.com) will get it a real
Let's Encrypt certificate automatically.

1. **Install Caddy** (official apt repo - see
   <https://caddyserver.com/docs/install#debian-ubuntu-raspbian>), then
   point its Caddyfile (`/etc/caddy/Caddyfile`) at
   `telegram_bot.py`'s webapp server, which only ever binds
   `127.0.0.1`:
   ```
   <your-ip-with-dashes>.sslip.io {
       reverse_proxy localhost:8080
   }
   ```
   `systemctl reload caddy` picks it up; Caddy fetches the cert on the
   first real request.
2. **Open ports 80/443** (needed for the cert challenge and for HTTPS
   itself) alongside 22 for SSH - e.g. with `ufw`:
   ```bash
   ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable
   ```
3. **Set `WEBAPP_BASE_URL`** (the public origin from step 1) and
   optionally `WEBAPP_PORT` (default `8080`) in `../telegram_bot.env` -
   loaded by `telegram_bot.py` (which binds the port) and all three drip
   scripts (which use the base URL to build their batch's Review link).
   Leave `WEBAPP_BASE_URL` unset to keep each script's old per-item
   message flow - they all fall back automatically.
4. `systemctl restart telegram-bot` - watch for `[webapp] Mini App
   review server listening on 127.0.0.1:8080` in `journalctl -u
   telegram-bot`, then run `python3 kanji_drip.py` (or `grammar_drip.py`
   / `vocab_drip.py`) manually to send a test batch.

Auth: the Mini App page can't be opened or forged by a stranger who
finds the URL - Telegram signs each page load's `initData` with an HMAC
keyed on the bot token (`webapp.py`'s `verify_init_data`), and
`/api/submit` checks the signed Telegram user id against
`TELEGRAM_ALLOWED_CHAT_ID` before touching any SRS state file, the same
guarantee `TELEGRAM_ALLOWED_CHAT_ID` already gives the polling loop.

All three decks (`kind="k"`/`"g"`/`"v"`) are wired up - see
`_KIND_CONFIG` in `webapp.py` if you add a fourth deck later.
