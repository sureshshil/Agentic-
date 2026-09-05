"""Thin entry point that runs rain_alert.py's unmodified main() with env
vars loaded via python-dotenv instead of requiring the crontab command to
`source` a file into the shell first - same pattern telegram_bot.py uses.

rain_alert.py itself is untouched: it reads ALERT_LOCATION and
RAIN_THRESHOLD_MM as module-level constants at import time, so
load_dotenv() has to run *before* `import rain_alert` below, which is
why this can't just be "add load_dotenv to rain_alert.py" without
touching that file.

See ../deploy/cron-notifications.md for VPS cron setup.

Env vars (loaded from ../deploy/scheduled.env - real environment
variables always win, this just fills in what isn't already set):
  NTFY_TOPIC          required - see rain_alert.py's docstring
  ALERT_LOCATION      optional, default "Tokyo, Japan"
  RAIN_THRESHOLD_MM   optional, default 7.5
"""

import os

from dotenv import load_dotenv

load_dotenv(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy", "scheduled.env")
)

import rain_alert  # noqa: E402 - must import after load_dotenv() above

if __name__ == "__main__":
    rain_alert.main()
