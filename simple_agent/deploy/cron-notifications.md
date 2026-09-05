# Weather alert & news digest as VPS cron jobs

`rain_alert.py` and `news_digest.py` (in `simple_agent/scheduled/`) were
built to run via GitHub Actions, pulling secrets from repo settings. This
runs them as cron jobs on the same VPS that already hosts
`telegram_bot.py` instead - same ntfy.sh notification channel as before,
nothing about delivery has changed.

Two new files, neither touching the originals:

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

Both new scripts load env vars from two files, same fill-in-what's-unset
behavior as `telegram_bot.py`'s own `load_dotenv()` call (real
environment variables always win):

- `../telegram_bot.env` - reuses `ANTHROPIC_API_KEY`, `BRAVE_API_KEY`,
  etc. that are already there for the bot.
- `../deploy/scheduled.env` - the handful of vars only these scripts
  need (`NTFY_TOPIC`, `ALERT_LOCATION`, `RAIN_THRESHOLD_MM`,
  `NEWS_QUERY`) - see `scheduled.env.example`.

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
   `rain_alert_cron.py` only sends a notification if current conditions
   are actually above threshold - lower `RAIN_THRESHOLD_MM` temporarily
   in `scheduled.env` if you want to force a test push.
   `news_digest_agent.py` always sends.

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
