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

**Why Tavily instead of Anthropic's built-in `web_search`:** the built-in
tool is zero-setup (same API key, no extra account) but its results carry
that large verification blob you can't shrink or opt out of. Tavily is a
plain client-side tool (like the calculator) — we get plain JSON back and
control the size directly: `max_results=3`, `chunks_per_source=1` (≤500
chars/snippet), and a short synthesized `answer` field instead of raw
pages. Trade-off: it needs its own API key and account instead of riding
on your existing Anthropic key.

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
| `scheduled/rain_alert.py` | Every 30 min (`*/30 * * * *`) | No | $0 — plain Open-Meteo + ntfy.sh, no LLM call at all |
| `scheduled/news_digest.py` | Daily, 23:00 UTC = 08:00 JST (`0 23 * * *`) | Yes, once per run | ~$0.001–0.003 (one fixed Tavily search + one Claude summarization call, not an open-ended agent loop) |

**Why `rain_alert.py` skips Claude entirely:** "is it raining heavily"
is a deterministic check (a WMO weather code or precipitation reading
crossing a threshold), not something that needs judgment. Reaching for
an LLM here would just add cost and a point of failure for zero benefit
— plain Python is the correct tool. It only sends a notification when
conditions actually cross the "heavy" threshold (`RAIN_THRESHOLD_MM`,
default 7.5mm/hour, or specific storm/thunderstorm codes) — checking
every 30 minutes doesn't mean 48 notifications a day, only sporadic ones
when something is actually happening. Note: as written, it re-alerts on
every run while heavy conditions persist (no dedup/cooldown) - fine for
"tell me it's still storming," easy to add a cooldown later if it gets
noisy.

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
   - `ALERT_LOCATION` (default `Tokyo, Japan` if unset)
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
