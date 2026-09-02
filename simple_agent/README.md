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
