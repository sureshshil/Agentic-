# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Two things live in one repo:

1. **`simple_agent/`** — a personal-assistant agent project (Telegram bot, notebooks, scheduled GitHub Actions scripts, and a Japanese N3 vocab/kanji/grammar spaced-repetition system). See `simple_agent/README.md` for the full walkthrough — it's long and detailed; read it before making non-trivial changes here rather than re-deriving setup steps.
2. **Static site build** (`build.sh` + `vercel.json` at repo root) — builds `_site/` containing an N3 vocab infographic, grammar reference, and kanji reference as static HTML, deployed via Vercel. Also separately deployed to GitHub Pages via `.github/workflows/publish-vocab.yml`.

## Commands

All Python commands run from `simple_agent/` unless noted.

```bash
pip install -r requirements.txt        # from simple_agent/

# Run a single test module (unittest, not pytest)
python3 -m unittest tests.test_srs -v
python3 -m unittest tests.test_agent_loop -v
python3 -m unittest tests.test_vocab_drip -v
# ...same pattern for test_rain_alert, test_srs_callback, test_telegram_delivery, test_vocab_sinks

# Run everything
python3 -m unittest discover tests -v

# Run the Telegram bot (long-running; needs Vertex AI + Telegram env vars, see README)
python simple_agent/telegram_bot.py

# Run the standalone CLI agent (Vertex AI/Gemini)
python simple_agent/agent.py
python simple_agent/agent.py "What's 12 * 7?"
```

Static site build (repo root):
```bash
bash build.sh   # downloads kanjivg_strokes.json, builds _site/{index,grammar,kanji}.html
```
`vercel.json`'s `buildCommand` must stay short (Vercel has a 256-char limit) — that's why the build logic lives in `build.sh` rather than inline.

Tests use `unittest`, not `pytest`, and mock external calls (Vertex AI's `genai.Client().models.generate_content`, Telegram HTTP, etc.) — they run offline with no credentials needed.

## Architecture

### Model provider split — Gemini vs. Claude

This is the single most important thing to know before touching model-calling code: **`agent.py`, `telegram_bot.py`, and `scheduled/news_digest_agent.py` call Gemini via Vertex AI** (`google-genai`, needs `GOOGLE_APPLICATION_CREDENTIALS` + `GCP_PROJECT_ID`). Everything else that calls an LLM — the `notebooks/`, the standalone `scheduled/news_digest.py` — is **still on the Claude/Anthropic API** (needs `ANTHROPIC_API_KEY`, possibly `ANTHROPIC_WORKSPACE_ID`). Check which file you're in before assuming a provider or credential shape.

### `telegram_bot.py` — the main long-running agent

A manual tool-use loop (calculator, memory, weather, web search via Brave, email, SRS review callbacks) driven by inline Telegram messages via long-polling (`getUpdates`), not a webhook. Key mechanics:
- **Sessions**: `BOT_STATE_PATH` (default `.telegram_bot_state.json`) is an index (active session id, lifetime cost total, Telegram update offset); actual message history per session lives under `BOT_SESSIONS_DIR` (default `.telegram_bot_sessions/`). `/reset`, `/new`, `/sessions`, `/switch <n>` manage these from Telegram itself.
- **Cost cap**: `AGENT_MAX_COST_USD` (default `1.00`) is a running lifetime total across the whole process's life, not per-session — once hit, the bot keeps listening but stops calling the model.
- **Security**: `TELEGRAM_ALLOWED_CHAT_ID` is required — any message from another chat is silently dropped before it reaches the model or any tool.
- **Inline-button callbacks** (Again/Hard/Good/Easy on SRS drip messages) are handled here too, via `srs.record_review`.

### `srs.py` — shared Leitner-style spaced repetition engine

Shared by `kanji_drip.py`, `grammar_drip.py`, and `telegram_bot.py`'s callback handler. One JSON state file per deck (e.g. `.kanji_srs.json`), one entry per item tracking `box`, `reps`, `lapses`, `due`, etc. Due reviews always take priority over introducing new items; new-item introduction is capped per run so the due-queue can't snowball past what the drip's cadence can clear. Each deck has its own `BOX_HOURS_*` table tuned to its push cadence (kanji hourly, grammar/vocab every 2h) — `vocab_drip.py` itself doesn't use `srs.py`'s due-scheduling, just plain round-robin (see `srs.py`'s module docstring for the full rationale).

### `notebooks/` — one self-contained notebook per feature

Each notebook (`01_basics.ipynb` through `08_weather_telegram.ipynb`) is copy-paste independent — no shared imports across notebooks — so each stays runnable top-to-bottom with nothing external to set up. They share a small core that's duplicated rather than factored out: a `getpass` cell for keys (never saved into the notebook file), a hard `MAX_COST_USD` spending cap priced against `claude-haiku-4-5` rates, and turn-collapsing (tool_use/tool_result exchanges get flattened to plain `{user, assistant-text}` after each turn — necessary because Anthropic's built-in `web_search` tool attaches a large opaque per-result blob that would otherwise get resent every future turn). When adding a new notebook, copy this structure from an existing one rather than inventing a new shape.

### `scheduled/` — unattended cron scripts

**These actually run via VPS cron in production, not GitHub Actions** — see `deploy/cron-notifications.md` for the full migration story and the live crontab shape. `rain_alert.py` and `news_digest.py` were originally built for GitHub Actions (workflow files still exist under `.github/workflows/`), then moved to VPS cron so they could reuse credentials already on the box and, for the digest, the real Gemini `Agent`. Check the VPS crontab (`crontab -l`) before assuming a `.github/workflows/*.yml` file reflects what's actually live — both `news-digest.yml` and `rain-alert.yml` have their `schedule:` triggers deliberately disabled (dispatch-only) to avoid duplicate/spammy alerts alongside the VPS cron jobs; `publish-vocab.yml` is unrelated (static-site GitHub Pages deploy, not a VPS-cron duplicate) and still runs on its own schedule.

Two categories, deliberately different in whether they call an LLM:
- **Deterministic, no LLM** (`rain_alert.py`): "is heavy rain coming" is a threshold check on a forecast, not a judgment call — reaching for an LLM here would just add cost/failure surface for nothing. Uses MET Norway's `locationforecast` (hourly precip + severity, no API key) rather than Open-Meteo (used elsewhere for current conditions) because it needs the hourly forecast shape. Keeps a cooldown state file to avoid re-alerting every run; the GitHub Actions path (now dispatch-only) can't persist that file between runs anyway, while the live VPS cron path, via `rain_alert_cron.py`, keeps real state.
- **LLM-assisted** (`news_digest_agent.py`, run via VPS cron — see `deploy/cron-notifications.md`): reuses `telegram_bot.py`'s own `Agent` class for one turn — a `web_search` tool call plus a synthesis reply, on Gemini via Vertex AI — not an open-ended loop, so cost stays small and predictable without the notebooks' budget-cap machinery. The original `news_digest.py` (fixed Tavily search + one Claude summarization call) still exists and works standalone as a simpler/cheaper alternative.
- `kanji_drip.py` / `grammar_drip.py` / `vocab_drip.py` push spaced-repetition items on a schedule (VPS cron only, no GitHub Actions equivalent), backed by `srs.py` (kanji/grammar) and sink modules (`*_sinks.py`) that can also write to Notion or a Google Doc.

GitHub Actions workflows live in `.github/workflows/`; scheduled ones only fire from the default branch (`claude/simple-agent-creation-qys564`), not feature branches — check `on.push.branches` / lack of a schedule trigger before assuming a workflow is live on the current branch, and check the VPS crontab too since that's the actual production schedule for rain alert and news digest.

### `artifact_creation/` — static HTML generation for the vocab/kanji/grammar reference site

`build_vocab_infographic.py`, `build_grammar_reference.py`, `build_kanji_reference.py` each render one of the three pages that `build.sh` assembles into `_site/`. `build_kanjivg_strokes.py` / the `kanjivg_strokes.json` download in `build.sh` supply stroke-order data — fetched at build time (not proxied at request time) specifically to avoid a CORS issue from a prior approach (see commit history). Audio-related scripts (`build_vocab_audio.py`, `upload_audio_to_*.py`, etc.) generate and host TTS pronunciation clips referenced by the generated pages.

### Secrets and local state

Files like `telegram_bot.env`, `deploy/*.env`, `*.json` service-account keys, and the various dotfile JSON state files (`.kanji_srs.json`, `.telegram_bot_state.json`, `.vocab_srs.json`, etc.) are gitignored local/runtime state — never commit them. `.env.example` / `deploy/*.env.example` files show the expected shape without real values.
