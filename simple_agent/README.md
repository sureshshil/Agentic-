# Simple Agent

A minimal Claude-powered agent with a manual tool-use loop. Serves as a
starting point for agent development in this repo.

## `agent.py` — the original combined script

A single-file, single-process version with every tool combined
(calculator, time, memory, web search) and a CLI. Kept as a frozen
reference / something you can run outside a notebook (e.g. in a Codespace);
new feature work happens in `notebooks/` instead (see below).

```bash
pip install -r requirements.txt
cp .env.example .env  # then fill in ANTHROPIC_API_KEY
export $(cat .env | xargs)
python agent.py                    # interactive REPL
python agent.py "What's 12 * 7?"   # one message at a time, state persisted to a local JSON file
```

## `notebooks/` — one focused notebook per feature

Going forward, each new capability gets its own self-contained notebook
instead of being bolted onto one growing file. The point: you only load
the tools relevant to what you're testing, so you're not paying for (or
reasoning about) unrelated tool schemas while working on one thing.

Every notebook shares the same small core, copied into each one rather
than imported from a shared module (so each notebook stays runnable on
its own, top to bottom, with nothing external to set up):

- A `getpass` cell for API keys — never saved into the notebook file.
- **A hard spending cap** (`MAX_COST_USD`, default $0.20, override via
  `AGENT_MAX_COST_USD`): every response's real token usage is priced
  against `claude-haiku-4-5` rates and accumulated; once the session hits
  the cap, further calls raise `BudgetExceededError` instead of hitting
  the API again.
- **Turn-collapsing**: once a turn finishes, `Agent.send` replaces the
  whole tool_use/tool_result exchange with a plain `{user, assistant-text}`
  pair. Necessary for `web_search` (Anthropic's built-in tool attaches an
  opaque per-result verification blob that measured ~21K characters for
  one reply — and since the API is stateless, that gets resent on *every*
  future turn if you don't strip it); harmless everywhere else.
- `max_tokens` (per response) defaults to **1024**, override via
  `AGENT_MAX_TOKENS`.

Current notebooks:

| Notebook | Demonstrates | Extra setup |
|---|---|---|
| `01_basics.ipynb` | Calculator + current time — the smallest possible loop | None |
| `02_memory.ipynb` | `remember` / `recall`, file-backed, persists across kernel restarts | None |
| `03_web_search_tavily.ipynb` | Web search via [Tavily](https://tavily.com)'s API instead of Anthropic's built-in `web_search` | Free Tavily API key |
| `04_weather.ipynb` | Current weather via [Open-Meteo](https://open-meteo.com) (geocode city → fetch conditions) | None — fully keyless |
| `05_weather_email.ipynb` | `get_weather` + `send_email` — a tool with a real side effect (actually sends mail via Gmail SMTP) | Gmail address + App Password |
| `06_gmail_oauth.ipynb` | Same `get_weather` + `send_email` combo, but via the real Gmail API with OAuth (`gmail.send` scope only) instead of SMTP | Google Cloud project + OAuth client (see below) |
| `07_weather_notification.ipynb` | `get_weather` + `send_notification` — pushes to your phone via [ntfy.sh](https://ntfy.sh) instead of email/SMS | Free ntfy app + a topic name, no signup |
| `08_weather_telegram.ipynb` | `get_weather` + `send_telegram_message` via the official [Telegram Bot API](https://core.telegram.org/bots/api) | Free bot via @BotFather, no signup beyond that |

**`08_weather_telegram.ipynb`** — another free, reliable, no-cost-per-message
channel, via a bot you create in ~30 seconds through Telegram's own
@BotFather. The notebook auto-discovers your `chat_id` by reading back
the first message you send the bot (Telegram bots can't message you
first — you have to message them once so they know who you are).

**Why push notification instead of SMS:** real SMS APIs (Twilio) charge
per message and per phone number, and pricing varies a lot by country —
e.g. Japan is ~$0.089/message plus an international number rental, over
10x the US rate. Free carrier email-to-SMS gateways are a well-known US
convention (`number@txt.att.net`, etc.) but Japan and many other
countries don't have an equivalent reliable enough to build on. ntfy.sh
sidesteps this: it's free, doesn't care what country you're in, needs no
phone number, and delivers straight to a phone app via one plain HTTP
POST. Setup: install the ntfy app, subscribe to a topic name you pick
(treat it like a password — anyone who knows it can publish to or read
it, since there's no login at all).

**`06_gmail_oauth.ipynb` — proper OAuth setup:**

1. In [Google Cloud Console](https://console.cloud.google.com), create a
   project (or use an existing one) and enable the **Gmail API**
   (APIs & Services → Library → search "Gmail API" → Enable).
2. Under APIs & Services → Credentials → **Create Credentials → OAuth
   client ID**, choose application type **Desktop app**, and download the
   resulting JSON.
3. Download **[`get_token.py`](get_token.py)** from this repo onto your
   own computer (**not** the Codespace) and put your `client_secret.json`
   next to it.
4. On that computer:
   ```bash
   pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
   python get_token.py
   ```
   This opens your real local browser — sign in and grant access. It
   writes `gmail_token.json` next to the script.

   **Why it has to be your own computer, not the Codespace browser flow:**
   the redirect Google sends the browser to is hardcoded to literal
   `http://localhost:PORT` (Google only allows `localhost`/`127.0.0.1`
   redirects for this client type — it can't be rewritten to a forwarded
   `https://...app.github.dev` URL). That only resolves correctly when
   the browser and the listening server are the same machine. Unless
   you're driving the Codespace through VS Code Desktop (which quietly
   tunnels `localhost` to the container), a browser-based Codespace UI
   has no such tunnel, and the redirect will fail with
   `ERR_CONNECTION_REFUSED` — not a bug to keep retrying, just the wrong
   environment for this particular step.
5. Upload the resulting `gmail_token.json` into `simple_agent/notebooks/`
   in the Codespace (drag into the file explorer, or right-click the
   folder → Upload). It's gitignored — never commit it. The notebook's
   OAuth cell checks for this file first and will skip the browser flow
   entirely once it exists, so this is a one-time step.

Scope used is `https://www.googleapis.com/auth/gmail.send` only — the
integration can send mail as you, but can't read your inbox.

**05 vs. 06 — which to use:** 05 (App Password/SMTP) is far less setup
and fine for personal scripts. 06 (OAuth) is the direction a real
production integration would take — narrower scope, revocable access,
and the pattern you'd extend toward "send on behalf of other users."
Both are kept as separate notebooks so you can compare them directly.

**`05_weather_email.ipynb` — the first "reactive" tool:** every notebook up
to this point only ever answers in chat. `send_email` actually does
something in the world. Two things worth knowing:

- **Setup uses an App Password, not OAuth or your real Gmail password.**
  Enable 2-Step Verification on your Google account, then generate one at
  [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
  Much less setup than the full Gmail API OAuth flow, at the cost of only
  working for your own Gmail account (not "send on behalf of any user"
  the way a real OAuth integration would).
- **The system prompt explicitly says "only send email when asked."** A
  tool with a side effect firing on the model's own initiative (e.g.
  deciding unprompted that a search result was "worth emailing") is a
  real risk once tools can act, not just answer — worth keeping in mind
  as you add more tools with side effects (file writes, calendar events,
  purchases, etc.).

**Why Brave Search instead of Anthropic's built-in `web_search`:** the
built-in tool is zero-setup (same API key, no extra account) but its
results carry that large verification blob you can't shrink or opt out
of. Brave's Web Search API is a plain client-side tool (like the
calculator) — plain JSON back, and we control the size directly:
`count=5` short title/url/description results per search. Trade-off: it
needs its own API key and account instead of riding on your existing
Anthropic key. (An earlier version of this bot used Tavily instead, which
adds a synthesized `answer` field on top of the same kind of snippets;
Brave has a larger free tier and lower per-call cost at this bot's
volume, at the cost of doing the synthesis yourself in the prompt instead
of getting one field to lean on.)

`telegram_bot.py`'s `web_search` tool exposes `freshness` as a per-call
argument — the model can ask for `"pd"`/`"pw"`/`"pm"`/`"py"` (past
day/week/month/year) on a query that's genuinely time-sensitive (current
office holders, recent results, live prices) so recent pages actually
outrank old ones, while everything else searches with no time filter at
all.

## `telegram_bot.py` — a real, two-way personal assistant

Every notebook above (and the `scheduled/` scripts below) either only
answers when you run a cell, or only ever pushes *to* you on a timer. This
is different: it's a long-running process that **listens** for your
Telegram messages and replies through the same tool-use loop — a real chat
interface you can use from your phone, no notebook or terminal open. It
combines memory, weather, web search, and email into one assistant instead
of one tool per notebook.

**How it works:** Telegram's `getUpdates` endpoint supports long-polling —
the bot asks "any new messages?" and Telegram holds the connection open for
up to 30 seconds before replying, so there's no need for a public URL or
webhook. The loop just asks again immediately after each response.

**Setup:**
1. You already have a bot token and `chat_id` if you did notebook 08's
   setup — reuse them. Otherwise: message **@BotFather** → `/newbot`, then
   send your new bot any message so it knows who you are.
2. Set env vars (locally, in a Codespace, or on whatever host you pick
   below):
   ```bash
   export ANTHROPIC_API_KEY=...
   export TELEGRAM_BOT_TOKEN=...
   export TELEGRAM_ALLOWED_CHAT_ID=...   # your chat_id - see below
   # optional:
   export BRAVE_API_KEY=...              # enables web_search
   export EMAIL_ADDRESS=you@gmail.com    # enables send_email
   export EMAIL_APP_PASSWORD=...
   ```
   Find your `chat_id` by messaging the bot once, then visiting
   `https://api.telegram.org/bot<token>/getUpdates` in a browser (or reuse
   notebook 08's `discover_chat_id()` cell).
3. Run it: `python simple_agent/telegram_bot.py`. Message your bot from
   Telegram — it replies through the real agent loop.

**Security: `TELEGRAM_ALLOWED_CHAT_ID` is not optional.** Telegram bots are
discoverable by username — anyone could find yours and message it. Every
incoming message is checked against this one chat_id; anything else is
silently ignored, printed to the log, and never reaches the model or your
tools (several of which have real side effects, like sending email as you).

**Cost cap works differently here than in the notebooks.** The notebooks
reset their `MAX_COST_USD` cap every kernel session; this process doesn't
restart on its own, so `AGENT_MAX_COST_USD` (default `1.00`) is a running
total for as long as it stays up. Once hit, the bot keeps listening but
replies with a "[stopped]" message instead of calling the model — delete
`BOT_STATE_PATH` (default `.telegram_bot_state.json`) or raise the cap to
continue.

**Managing conversation history — sessions.** Conversation state is split
across two kinds of file. `BOT_STATE_PATH` (default
`.telegram_bot_state.json`) is just an *index*: which session is
currently active, plus the two things shared across every session —
`total_cost_usd` (**lifetime** spend, across all sessions ever — this is
what `AGENT_MAX_COST_USD` caps) and the Telegram `update_offset`. The
actual messages for each session, plus that session's own
`session_cost_usd` (what's been spent just in it — informational, no cap
of its own), live in their own file under `BOT_SESSIONS_DIR` (default
`.telegram_bot_sessions/`), named by session id.

This matters because nothing trims a session's history automatically —
every turn you've sent in it stays there, and each reply resends that
*entire* history to the API (it's stateless — there's no server-side
session to trim for you). Input cost creeps up as a session goes on, and
eventually its history can exceed the model's context window, after
which every message in *that* session fails the same way.

Send `/reset` or `/new` from Telegram at any time to start a fresh
session — a new, empty session file with `session_cost_usd` back at $0,
and the old one left exactly as it was (nothing is deleted, so you can
look back at a past session's messages *and* what it cost by reading its
file under `BOT_SESSIONS_DIR` directly; the reset confirmation message
also reports what the session you just left cost). Long-term memory
(`remember`/`recall`) and the lifetime cost total aren't session-scoped —
both carry over regardless of how many times you reset.

**Switching back to a past session.** Send `/sessions` to list every
session file (most recently active first), numbered, with a preview of
its first message, its cost, and when it was last active — the current
one is marked. Send `/switch <n>` to make session `n` the active one:
its history replaces what's currently in memory (and what gets sent to
the API from then on), and its own `session_cost_usd` picks up where it
left off rather than resetting.

Telegram doesn't give a bot any way to filter or hide its own chat's
scrollback, so switching can't make old messages disappear from what
you see in the app the way separate tabs/threads would. Instead,
`/switch` immediately replays the target session's conversation back
into the chat as a recap, so you can see what it contains right after
switching without scrolling back through everything. A genuinely
separate visual history per session would require moving to a Telegram
group with Forum Topics enabled — a much larger change (topic creation,
`message_thread_id` handling, a different security model) that this bot
doesn't currently implement.

*Upgrading from an older version:* if `BOT_STATE_PATH` still has the old
flat format (messages and cost stored directly in it, no
`active_session_id`), the bot migrates it automatically on first run —
that history becomes your first session file, credited with whatever
`total_cost_usd` had already accumulated (it was the only conversation
that existed at the time), and the lifetime total/offset carry over
unchanged so you don't lose spend tracking or replay old messages.

**Hosting — this needs to stay running, unlike everything else in this
repo.** GitHub Actions (used for `scheduled/` below) only runs on a
schedule/trigger and can't host a persistent process. Quickest way to try
it: keep a terminal open (a Codespace, your own machine) — free, but stops
the moment you close it. For something that stays up permanently, see
"Hosting on a cheap VPS" below.

### Hosting on a cheap VPS

Unlike everything else in this repo, `telegram_bot.py` needs to run
continuously somewhere. **Oracle Cloud's Always Free tier turned out too
flaky in practice** (their free-tier capacity, especially the ARM shapes,
is notoriously oversubscribed and can be reclaimed/throttled without
warning) - not worth fighting for something meant to run every day. A
small paid VPS costs a few dollars a month and just works:

| Provider | Cost | Notes |
|---|---|---|
| [Hetzner Cloud](https://www.hetzner.com/cloud/) | ~€4/mo (~$4.50) | Cheapest reliable option, EU data centers |
| [DigitalOcean](https://www.digitalocean.com/) | ~$4-6/mo | Polished dashboard/docs, US + EU regions |
| [Vultr](https://www.vultr.com/) | ~$5/mo | Similar to DigitalOcean |

Any of these work identically from here on - pick whichever's signup is
easiest for you. This bot barely uses any CPU or RAM (polling Telegram
every ~30 seconds), so their smallest/cheapest instance size is plenty.
No inbound ports need to be opened either: the bot only makes outbound
requests (long-polling Telegram, calling Anthropic/Brave/Gmail), so you
can leave the default firewall as-is.

**1. Create the account and VM:**
1. Sign up with your chosen provider and add a payment method.
2. Create a new VM/droplet/instance: smallest size, **Ubuntu 22.04 or
   24.04** as the image, and either upload your own SSH public key or
   download the generated private key during creation.
3. Note the instance's public IP once it's running.

**2. SSH in and set up the project:**
```bash
ssh -i /path/to/your/private_key root@<instance-public-ip>
# (Hetzner/DigitalOcean/Vultr's Ubuntu images log you in as "root" by
# default - adjust the deploy/telegram-bot.service User= field below if
# you create a separate non-root user instead)

apt update && apt install -y python3-venv git

mkdir -p /home/projects && cd /home/projects
git clone https://github.com/sureshshil/Agentic-.git
cd Agentic-
git checkout claude/simple-agent-creation-qys564
cd simple_agent

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

**3. Add your secrets** (never committed - `telegram_bot.env` is
gitignored):
```bash
cp deploy/telegram_bot.env.example telegram_bot.env
nano telegram_bot.env   # fill in your real values
```

**4. Install it as a systemd service**, so it survives you logging out and
restarts automatically on crash or VM reboot. `deploy/telegram-bot.service`
defaults to a `root` user at `/home/projects/Agentic-` - if your setup
differs, edit `User=`, `WorkingDirectory=`, `EnvironmentFile=`, and
`ExecStart=` first to match:
```bash
nano deploy/telegram-bot.service   # fix the paths/User= for your setup
cp deploy/telegram-bot.service /etc/systemd/system/telegram-bot.service
systemctl daemon-reload
systemctl enable --now telegram-bot
```

**5. Check it's running and message your bot:**
```bash
sudo systemctl status telegram-bot     # should show "active (running)"
sudo journalctl -u telegram-bot -f     # live logs - watch for "You: ..." / "Agent: ..."
```

**Updating later:** `git pull`, then `sudo systemctl restart telegram-bot`.

## `scheduled/` + GitHub Actions — automatic, unattended notifications

Everything above only runs when you open a notebook and execute cells.
For something to fire on its own — a rain alert every 30 minutes, a
daily news digest — whether or not the Codespace is even open, the code
needs to run somewhere that isn't your notebook session. **GitHub
Actions** does this: the workflow files under `.github/workflows/` tell
GitHub to run a script in the cloud on a cron schedule, for free (public
repos get unlimited free minutes; private repos get a generous free
tier).

| Script | Runs | Uses Claude? | Approx. cost/run |
|---|---|---|---|
| `scheduled/rain_alert.py` | Every 30 min (`*/30 * * * *`) | No | $0 — plain MET Norway + ntfy.sh, no LLM call at all |
| `scheduled/news_digest.py` | Daily, 23:00 UTC = 08:00 JST (`0 23 * * *`) | Yes, once per run | ~$0.001–0.003 (one fixed Tavily search + one Claude summarization call, not an open-ended agent loop) |

**Why `rain_alert.py` skips Claude entirely:** "is heavy rain coming"
is a deterministic check (a forecast precipitation figure crossing a
threshold), not something that needs judgment. Reaching for an LLM here
would just add cost and a point of failure for zero benefit — plain
Python is the correct tool.

**Why it reads the forecast, not current conditions:** the first version
of this script checked what was falling *right now*, which meant the
notification arrived once you were already out in it. It now asks MET
Norway for the next `ALERT_LOOKAHEAD_HOURS` (default 6) and reports the
worst hour in that window with its lead time — "Heavy rain in ~3h
(around 04:00) - 14.1 mm expected" — which is early enough to be worth
acting on.

**Why MET Norway rather than Open-Meteo:** the notebooks use Open-Meteo
for current conditions and it's fine at that. The alert needs hourly
precipitation forecasts with a severity symbol per hour, which MET
Norway's `locationforecast` returns directly, still with no API key or
signup. Their terms ask for a `User-Agent` naming the app and a contact
address — that's `ALERT_CONTACT`. Geocoding is still Open-Meteo's
geocoder, which is a separate service from their forecast API.

**Two things that made the original useless, worth knowing about:**

- `geocode()` returned `results[0]` with no disambiguation. "Hanahata"
  matches both a neighbourhood in Adachi-ku, Tokyo *and* one in Fukuoka
  900 km away — so the alert quietly reported the wrong city's weather.
  Set `ALERT_LAT`/`ALERT_LON` to pin a location exactly; the script now
  also prints a warning when a name is ambiguous.
- `RAIN_THRESHOLD_MM=0` made `precipitation >= threshold` true on clear,
  dry days, so it pushed a notification on every single run. A threshold
  of 0 now means "any measurable rain" (0.1 mm), not "always".

It also keeps a cooldown (`ALERT_COOLDOWN_MIN`, default 180) in a small
gitignored state file, so a storm parked over the city for three hours
is one push rather than six. GitHub Actions runners are ephemeral and
can't persist that file between runs, so the workflow sets it to `0`.

**Why `news_digest.py` does use Claude:** summarizing search results
into a readable digest is exactly what an LLM is good at, unlike the
rain check. It's still just one fixed request per run (not a tool-use
loop), so cost stays small and predictable without needing the
notebooks' `BudgetExceededError` machinery.

### Setup

1. In your GitHub repo: **Settings → Secrets and variables → Actions**.
2. Under **Secrets**, add (only what each workflow needs):
   - `NTFY_TOPIC` — same topic from notebook 07 (both scripts use it)
   - `ANTHROPIC_API_KEY`, and `ANTHROPIC_WORKSPACE_ID` if you needed one
     (news digest only)
   - `TAVILY_API_KEY` (news digest only)
3. Under **Variables** (not secret, but optional overrides):
   - `ALERT_LAT` + `ALERT_LON` — exact coordinates, and `ALERT_LABEL` for
     what to call the place in the message. Prefer these over
     `ALERT_LOCATION` for any place name more than one town shares.
   - `ALERT_LOCATION` (default `Tokyo, Japan` if unset) — geocoded, and
     only used when `ALERT_LAT`/`ALERT_LON` aren't set
   - `ALERT_CONTACT` — contact address for the MET Norway `User-Agent`
   - `RAIN_THRESHOLD_MM` (default `7.5`), `ALERT_LOOKAHEAD_HOURS`
     (default `6`)
   - `NEWS_QUERY` (default `top world news today` if unset)
4. Commit and push — the workflows activate automatically once they're on
   the repo's default branch. (Scheduled workflows only run from the
   default branch, not from feature branches like this one — merge before
   expecting the cron to fire.)
5. **Test without waiting for the schedule:** go to the **Actions** tab →
   select "Rain Alert" or "News Digest" → **Run workflow**. This uses the
   same `workflow_dispatch` trigger both files include specifically for
   manual testing.

All cron times are UTC — adjust the `cron:` line in the `.yml` files for
your own schedule preference; GitHub's schedule syntax is standard 5-field
cron.

### Adding a new feature notebook

Copy the "install → keys → tools/Agent class → create agent → try it →
optional chat loop" structure from an existing notebook, swap in just the
new tool(s), and keep the cost-cap + turn-collapsing core intact.

### Troubleshooting: `anthropic-workspace-id is required...`

If you see:

```
BadRequestError: ... 'anthropic-workspace-id is required when authenticating
with an identity-linked API key; send the id of the workspace this request
acts in.'
```

Your API key is a personal/service-account key that isn't scoped to a
single workspace, so every request needs to say which workspace it acts
in. Two ways to fix it:

1. **Simplest — rescope the key.** In the [Claude Console](https://platform.claude.com/settings/keys),
   create (or edit) the key with a specific workspace selected. No code
   changes needed after that.
2. **Or set the workspace ID yourself.** Find it under
   [Settings → Workspaces](https://platform.claude.com/settings/workspaces)
   (the ID column), then either set `ANTHROPIC_WORKSPACE_ID` as an
   environment variable (or Codespaces secret) before running, or just
   type it in when a notebook's key-setup cell prompts for it (leave
   blank if you don't need it). `agent.py` and every notebook's
   `build_client()` picks it up automatically and attaches it as the
   `anthropic-workspace-id` header.
