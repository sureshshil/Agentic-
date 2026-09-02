# Simple Agent

A minimal Claude-powered agent with a manual tool-use loop. Serves as a
starting point for agent development in this repo.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env  # then fill in ANTHROPIC_API_KEY
export $(cat .env | xargs)
```

## Run

```bash
python agent.py
```

Type messages at the `You:` prompt; type `exit` to quit.

You can also run one message at a time (conversation state is persisted to
a local JSON file between invocations, path configurable via
`AGENT_STATE_PATH`):

```bash
python agent.py "What's 12 * 7?"
```

## Notebook version

`agent.ipynb` has the same `Agent` class split across notebook cells, for
running in JupyterLab, Jupyter Notebook, or Google Colab. Open it, run the
cells top to bottom (the API key cell uses `getpass` so the key isn't saved
into the notebook file), then re-run the "send a message" cell to keep
chatting — `agent.messages` persists in the kernel's memory between runs.

## How it works

- `agent.py` defines an `Agent` class that keeps conversation history and
  runs a loop against the Claude Messages API (`claude-haiku-4-5`).
- Tools wired up:
  - `get_current_time`, `calculator` — basic client-side tools.
  - `web_search` — Anthropic's built-in server-side search tool. Runs on
    Anthropic's infrastructure using your existing `ANTHROPIC_API_KEY`, no
    separate search API key needed.
  - `remember` / `recall` — a simple long-term memory store, persisted to a
    local JSON file (default `.agent_memory.json`, path configurable via
    `AGENT_MEMORY_PATH`). Stored facts are injected into the system prompt
    on every turn, so the agent remembers things about you across separate
    runs, not just within one conversation.
- Add new tools by appending a definition to `TOOLS` and a matching branch
  in `execute_tool`.

## Cost controls

- `max_tokens` (per response) defaults to **1024**, override with
  `AGENT_MAX_TOKENS`. This caps how long a single reply can be, not total
  spend across a conversation — a long back-and-forth still resends the
  full history every turn.
- `MAX_COST_USD` is a hard spending cap, defaulting to **$0.20**, override
  with `AGENT_MAX_COST_USD`. Every response's actual `usage.input_tokens` /
  `usage.output_tokens` is priced against `claude-haiku-4-5` rates and
  accumulated; once the total hits the cap, further calls raise
  `BudgetExceededError` instead of hitting the API again.
  - In the interactive REPL and the notebook, the cap applies for the
    life of that process/kernel.
  - In single-message mode (`python agent.py "..."`), accumulated cost is
    persisted in the state file alongside the conversation, so the cap
    holds across separate invocations too.
- `web_search`'s `max_uses` is capped at 3 per turn — each search adds
  tokens (and the cited source text), so this bounds worst-case spend on
  a single search-heavy question.
- The printed `(session cost so far: ~$X)` after every reply is an
  estimate from local pricing constants, not a billing-accurate figure —
  check the [Anthropic Console](https://console.anthropic.com) for actual
  usage.
