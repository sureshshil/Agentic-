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
- `vocab_drip.py` - generates a batch of JLPT-level Japanese vocabulary
  (word, part of speech, nuance, confusable pairs, particle affinity, 3
  example sentences each) using a fresh `telegram_bot.Agent` instance,
  and delivers it via `telegram_bot.send_telegram_reply()` straight to
  your Telegram chat - *not* over ntfy.sh, since this content is long-form
  and reads better as a chat message than a push notification. It calls
  `send_telegram_reply()` directly rather than going through the
  interactive bot's session loop, so these drips never touch
  `.telegram_bot_state.json` or any session file - they show up as their
  own messages and don't interrupt or mix into whatever you're chatting
  about with the bot. Keeps a small history file
  (`../.vocab_sent_words.json`, gitignored like the other state files) of
  words already sent so it doesn't repeat itself.

All three new scripts load env vars from two files, same
fill-in-what's-unset behavior as `telegram_bot.py`'s own `load_dotenv()`
call (real environment variables always win):

- `../telegram_bot.env` - reuses `ANTHROPIC_API_KEY`, `BRAVE_API_KEY`,
  `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_ID`, etc. that are already
  there for the bot.
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
   ```
   `rain_alert_cron.py` only sends a notification if current conditions
   are actually above threshold - lower `RAIN_THRESHOLD_MM` temporarily
   in `scheduled.env` if you want to force a test push.
   `news_digest_agent.py` and `vocab_drip.py` always send.

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

   # Japanese vocab drip - every 4 hours
   0 */4 * * * cd /home/projects/Agentic-/simple_agent/scheduled && python3 vocab_drip.py >> /var/log/vocab-drip.log 2>&1
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

## Optional: archive the vocab drip to Notion and a Google Doc

`vocab_drip.py` can push each drip to two more places after the Telegram
message, handled by `scheduled/vocab_sinks.py`:

- **Notion database** - a browsable archive: one row **per word**, with
  `Reading` / `Meaning` / `Particle` (助詞) / `Type` / `JLPT` columns and
  the 3 example sentences in `Example 1` / `Example 2` / `Example 3`. The
  data comes from a hidden JSON block the model appends after the vocab
  text (`---DATA---`), which also drives the local history file; a
  formatting slip there just skips that run's rows, the Telegram drip is
  unaffected.
- **Google Doc** - a single ever-growing doc to use as a **NotebookLM**
  source for audio overviews, flashcards, infographics. NotebookLM
  re-syncs a Drive doc on demand, so this is one source you refresh, not
  a file you re-upload. When you only want the latest batch, steer the
  generation at "today's entries". The doc is formatted for the model,
  not a person: a one-time preamble paragraph (added when the doc is
  empty) explaining what the document is and what it's for, a real
  `Heading 2` per word, explicit English labels (`Meaning:`,
  `Part of speech:`, `Particle patterns:`), and every example sentence
  three ways - normal Japanese, kana-only for pronunciation, English.

Both are **best-effort and independent**: each is skipped unless its env
vars are set, and if one fails it's logged to the cron log while the
Telegram drip (and the other sink) still go through. Nothing here touches
`telegram_bot.py` or the other scheduled scripts.

Add the vars to `scheduled.env` (see `scheduled.env.example`).

### Notion setup (one-time)

1. Create an internal integration at
   <https://www.notion.so/my-integrations> - copy its token into
   `NOTION_API_KEY` (starts `secret_`).
2. Create a database (a full-page database is easiest). It needs a title
   property named **`Word`**; add any of these you want populated, with
   exactly these names/types (missing ones are just skipped):
   - `Reading` - rich_text
   - `Meaning` - rich_text
   - `Particle` - rich_text  (助詞 patterns)
   - `Example 1`, `Example 2`, `Example 3` - rich_text  (one sentence each)
   - `Type` - select  (part of speech; new options are created as needed)
   - `JLPT` - select  (set to `VOCAB_LEVEL`)
3. Open the database as a full page → `•••` menu → **Connections** →
   add your integration.
4. Copy the database id from its URL - the 32-hex-char chunk before the
   `?v=` (the view id), with or without hyphens - into `NOTION_VOCAB_DB_ID`.

### Google Doc setup (one-time)

1. In a Google Cloud project, enable the **Google Docs API**.
2. Create a **service account** and download a JSON key. Put the file on
   the VPS somewhere the cron user can read (e.g.
   `/home/deploy/vocab-gdoc-sa.json`) - it's a secret, keep it out of
   git (the repo's `*.json` ignore rule already covers it if it lands
   under the repo). Point `GOOGLE_SERVICE_ACCOUNT_JSON` at it.
3. Create the target Google Doc. **Share** it with the service account's
   `client_email` (from the JSON key) as **Editor**.
4. Copy the doc id from its URL
   (`docs.google.com/document/d/<id>/edit`) into `VOCAB_GDOC_ID`.
5. `pip install google-auth` (it's in `requirements.txt`) - this is the
   only extra dependency, and only this sink needs it.

Test with a manual run - it prints one status line per configured sink:

```bash
cd /home/projects/Agentic-/simple_agent/scheduled && python3 vocab_drip.py
# ...
# Notion: added 4/4 word rows
# Google Doc: appended 4 words (2410 chars) at index 5120
```
