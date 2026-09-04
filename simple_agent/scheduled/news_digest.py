"""Daily news digest. Searches via Tavily, summarizes with Claude, and
pushes the result via ntfy.sh. Meant to run once a day via GitHub Actions
(see ../../.github/workflows/news-digest.yml) - unlike rain_alert.py this
does call Claude, but it's a single fixed request (~$0.001-0.003/run at
claude-haiku-4-5 rates), not an open-ended agent loop, so cost is small
and predictable without needing a runtime budget cap.

Env vars:
  ANTHROPIC_API_KEY        required
  ANTHROPIC_WORKSPACE_ID   optional - see the notebooks' README section
  TAVILY_API_KEY           required
  NTFY_TOPIC               required - your ntfy.sh topic (see notebook 07)
  NEWS_QUERY               optional - default "top world news today"
"""

import os

import anthropic
import requests

MODEL = "claude-haiku-4-5"
NEWS_QUERY = os.environ.get("NEWS_QUERY") or "top world news today"
TAVILY_MAX_RESULTS = 5


def _request_with_retry(method, url, attempts=3, **kwargs):
    last_exc = None
    for _ in range(attempts):
        try:
            resp = requests.request(method, url, timeout=30, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
    raise last_exc


def search_news(query: str) -> dict:
    resp = _request_with_retry(
        "POST",
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}"},
        json={
            "query": query,
            "max_results": TAVILY_MAX_RESULTS,
            "chunks_per_source": 1,
            "topic": "news",
            "include_answer": "basic",
        },
    )
    return resp.json()


def build_client() -> anthropic.Anthropic:
    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    if workspace_id:
        return anthropic.Anthropic(
            default_headers={"anthropic-workspace-id": workspace_id}
        )
    return anthropic.Anthropic()


def summarize(search_data: dict) -> str:
    lines = []
    if search_data.get("answer"):
        lines.append("Overview: " + search_data["answer"])
    for r in search_data.get("results", []):
        lines.append("- " + r["title"] + ": " + r["content"])
    raw_news = "\n".join(lines) if lines else "No search results found."

    client = build_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        system=(
            "Summarize the following news search results into a short, "
            "readable digest (5-8 points max), suitable for a push "
            "notification. Be concise and skip filler.\n\n"
            "Plain text only - this is rendered as-is on a phone lock "
            "screen with no markdown support. Do not use **bold**, "
            "# headings, _italics_, or markdown bullet syntax. Start each "
            "point with a plain dash (-) and a line break between points."
        ),
        messages=[{"role": "user", "content": raw_news}],
    )
    return "".join(b.text for b in response.content if b.type == "text")


def send_notification(title: str, message: str) -> None:
    topic = os.environ["NTFY_TOPIC"]
    _request_with_retry(
        "POST",
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Tags": "newspaper"},
    )


def main() -> None:
    missing = [
        name
        for name in ("ANTHROPIC_API_KEY", "TAVILY_API_KEY", "NTFY_TOPIC")
        if not os.environ.get(name)
    ]
    if missing:
        raise SystemExit(
            f"Missing required secret(s): {', '.join(missing)}. Add them: "
            "Settings -> Secrets and variables -> Actions -> New repository secret."
        )

    search_data = search_news(NEWS_QUERY)
    digest = summarize(search_data)
    print(digest)
    send_notification("Daily News Digest", digest)


if __name__ == "__main__":
    main()
