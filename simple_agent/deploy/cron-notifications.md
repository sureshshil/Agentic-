# Weather alert & news digest as VPS cron jobs

`rain_alert.py` and `news_digest.py` (in `simple_agent/scheduled/`) were
built to run via GitHub Actions, pulling secrets from repo settings. This
runs the exact same, unmodified scripts as cron jobs on the same VPS
that already hosts `telegram_bot.py` - same ntfy.sh notification channel
as before, nothing about how they work has changed.

Neither script calls `load_dotenv()` itself (they expect real env vars,
the way GitHub Actions' `env:` block provides them), so on a VPS the
crontab command needs to export them into the shell before running
Python. Reuse credentials already in `telegram_bot.env`
(`ANTHROPIC_API_KEY`, `ANTHROPIC_WORKSPACE_ID`, `TAVILY_API_KEY`) plus a
new `scheduled.env` for the handful of vars only these two scripts need
(`NTFY_TOPIC`, `ALERT_LOCATION`, `RAIN_THRESHOLD_MM`, `NEWS_QUERY`) - see
`scheduled.env.example`.

## Setup

1. Copy the example and fill in your ntfy.sh topic (see notebook 07 if
   you haven't set one up):
   ```bash
   cp simple_agent/deploy/scheduled.env.example simple_agent/deploy/scheduled.env
   # then edit simple_agent/deploy/scheduled.env
   ```

2. Test both manually first, from the `simple_agent/scheduled/`
   directory, sourcing both env files:
   ```bash
   cd /path/to/Agentic-/simple_agent/scheduled
   set -a
   source ../telegram_bot.env
   source ../deploy/scheduled.env
   set +a
   python3 rain_alert.py
   python3 news_digest.py
   ```
   `rain_alert.py` only sends a notification if current conditions are
   actually above threshold - lower `RAIN_THRESHOLD_MM` temporarily if
   you want to force a test push. `news_digest.py` always sends.

3. Add cron entries. Edit the crontab for whichever user runs
   `telegram_bot.py` (`crontab -e`, or `sudo -u <user> crontab -e` if it
   runs under a dedicated service user per `telegram-bot.service`):

   ```cron
   # Weather alert - every 30 minutes
   */30 * * * * /bin/bash -lc 'set -a; source /path/to/Agentic-/simple_agent/telegram_bot.env; source /path/to/Agentic-/simple_agent/deploy/scheduled.env; set +a; cd /path/to/Agentic-/simple_agent/scheduled && python3 rain_alert.py' >> /var/log/weather-alert.log 2>&1

   # News digest - once a day at 08:00
   0 8 * * * /bin/bash -lc 'set -a; source /path/to/Agentic-/simple_agent/telegram_bot.env; source /path/to/Agentic-/simple_agent/deploy/scheduled.env; set +a; cd /path/to/Agentic-/simple_agent/scheduled && python3 news_digest.py' >> /var/log/news-digest.log 2>&1
   ```

   Replace `/path/to/Agentic-` with the repo's actual path on the VPS,
   and `python3` with a full interpreter path (`which python3`) if
   you're using a virtualenv. `-lc` (not just `-c`) so bash reads a login
   profile - matters if `python3`/`pip` packages are only on `PATH`
   inside one.

Cron has no journal the way systemd does - that's what the `>> ... 2>&1`
redirect above is for; check those log files if an expected run doesn't
show up as an ntfy.sh push. If you'd rather have `systemctl
status`/`journalctl` visibility instead (consistent with
`telegram-bot.service`), systemd timers are a reasonable alternative,
just noticeably more boilerplate for two short-lived scripts than plain
cron.
