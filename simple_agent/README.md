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

**Why Tavily instead of Anthropic's built-in `web_search`:** the built-in
tool is zero-setup (same API key, no extra account) but its results carry
that large verification blob you can't shrink or opt out of. Tavily is a
plain client-side tool (like the calculator) — we get plain JSON back and
control the size directly: `max_results=3`, `chunks_per_source=1` (≤500
chars/snippet), and a short synthesized `answer` field instead of raw
pages. Trade-off: it needs its own API key and account instead of riding
on your existing Anthropic key.

### Adding a new feature notebook

Copy the "install → keys → tools/Agent class → create agent → try it →
optional chat loop" structure from an existing notebook, swap in just the
new tool(s), and keep the cost-cap + turn-collapsing core intact.
