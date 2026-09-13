"""Upload kanjivg_strokes.json as a GitHub Release asset.

Creates (or reuses) a release tagged  n3-kvg-v1  on the repo.
Idempotent: if the asset already exists it prints the URL and exits.

Reads:  vocab_artifacts/kanjivg_strokes.json
Writes: vocab_artifacts/vocab_kvg_url.json  { "url": "https://..." }

Usage:
    GITHUB_TOKEN=ghp_... python upload_kvg_to_github.py

Get a token at https://github.com/settings/tokens
Required scopes: repo  (or  public_repo  for public repos)
"""

import json
import os
import time
from pathlib import Path

import requests

ROOT   = Path(__file__).resolve().parent.parent
SRC    = ROOT / "vocab_artifacts" / "kanjivg_strokes.json"
OUT    = ROOT / "vocab_artifacts" / "vocab_kvg_url.json"

REPO     = "sureshshil/Agentic-"
TAG      = "n3-kvg-v1"
FILENAME = "kanjivg_strokes.json"
API      = "https://api.github.com"
UPLOAD   = "https://uploads.github.com"


def get_token() -> str:
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if not tok:
        raise SystemExit(
            "GITHUB_TOKEN not set.\n"
            "Create one at https://github.com/settings/tokens (scope: repo)\n"
            "Then run:  $env:GITHUB_TOKEN='ghp_...'"
        )
    return tok


def headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_or_create_release(token: str) -> tuple[int, str]:
    h = headers(token)
    r = requests.get(f"{API}/repos/{REPO}/releases/tags/{TAG}", headers=h, timeout=30)
    if r.status_code == 200:
        data = r.json()
        print(f"reusing existing release: {data['html_url']}")
        return data["id"], data["html_url"]

    payload = {
        "tag_name": TAG,
        "name": "N3 KanjiVG Stroke Data",
        "body": "kanjivg_strokes.json — stroke-path data for the N3 vocab reference stroke-order animation.",
        "draft": False,
        "prerelease": False,
    }
    r = requests.post(f"{API}/repos/{REPO}/releases", headers=h, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    print(f"created release: {data['html_url']}")
    return data["id"], data["html_url"]


def find_existing_asset(token: str, release_id: int) -> str | None:
    """Return download URL if FILENAME is already on the release, else None."""
    h = headers(token)
    page = 1
    while True:
        r = requests.get(
            f"{API}/repos/{REPO}/releases/{release_id}/assets",
            headers=h, params={"per_page": 100, "page": page}, timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        for a in data:
            if a["name"] == FILENAME:
                return a["browser_download_url"]
        page += 1


def upload_asset(token: str, release_id: int, data: bytes,
                 retries: int = 3) -> str:
    h = headers(token)
    h["Content-Type"] = "application/json"
    url = f"{UPLOAD}/repos/{REPO}/releases/{release_id}/assets?name={FILENAME}"
    for attempt in range(retries):
        try:
            r = requests.post(url, headers=h, data=data, timeout=120)
            r.raise_for_status()
            return r.json()["browser_download_url"]
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 3 * (attempt + 1)
            print(f"  retry {attempt+1} after {wait}s ({exc})", flush=True)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"Source not found: {SRC}\nRun build_kanjivg_strokes.py first.")

    token = get_token()
    release_id, release_url = get_or_create_release(token)

    existing_url = find_existing_asset(token, release_id)
    if existing_url:
        print(f"asset already uploaded: {existing_url}")
    else:
        print(f"uploading {SRC.name} ({SRC.stat().st_size:,} bytes)…", flush=True)
        existing_url = upload_asset(token, release_id, SRC.read_bytes())
        print(f"uploaded: {existing_url}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"url": existing_url}, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {OUT.name}  ->  {OUT}")
    print(f"Release: {release_url}")


if __name__ == "__main__":
    main()
