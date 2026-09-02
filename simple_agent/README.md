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

## How it works

- `agent.py` defines an `Agent` class that keeps conversation history and
  runs a loop against the Claude Messages API (`claude-opus-5`).
- Two example tools are wired up (`get_current_time`, `calculator`) to show
  how Claude can call tools and receive results back mid-conversation.
- Add new tools by appending a definition to `TOOLS` and a matching branch
  in `execute_tool`.
