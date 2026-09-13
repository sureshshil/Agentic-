"""Upload per-day MP3 tracks as GitHub Release assets and write a day → URL map.

Creates (or reuses) a release tagged  n3-day-audio-v1  on the repo.
Resumable: already-uploaded assets are skipped.

Reads:  vocab_artifacts/audio/n3_day01.mp3 … n3_day16.mp3
Writes: vocab_artifacts/vocab_day_audio_urls.json  { "1": "https://...", "2": "https://...", ... }

Usage:
    GITHUB_TOKEN=ghp_... python upload_day_audio_to_github.py

Get a token at https://github.com/settings/tokens
Required scopes: repo  (or  public_repo  for public repos)
"""

import json
import os
import re
import time
from pathlib import Path

import requests

ROOT  = Path(__file__).resolve().parent.parent
AUDIO = ROOT / "vocab_artifacts" / "audio"
OUT   = ROOT / "vocab_artifacts" / "vocab_day_audio_urls.json"

REPO   = "sureshshil/Agentic-"
TAG    = "n3-day-audio-v1"
API    = "https://api.github.com"
UPLOAD = "https://uploads.github.com"


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
        "name": "N3 Vocab Day Audio Tracks",
        "body": "Full per-day MP3 listening tracks for the N3 vocabulary reference page.",
        "draft": False,
        "prerelease": False,
    }
    r = requests.post(f"{API}/repos/{REPO}/releases", headers=h, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    print(f"created release: {data['html_url']}")
    return data["id"], data["html_url"]


def list_existing_assets(token: str, release_id: int) -> dict[str, str]:
    """Return {filename: download_url} for assets already on the release."""
    h = headers(token)
    assets = {}
    page = 1
    while True:
        r = requests.get(
            f"{API}/repos/{REPO}/releases/{release_id}/assets",
            headers=h, params={"per_page": 100, "page": page}, timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        for a in data:
            assets[a["name"]] = a["browser_download_url"]
        page += 1
    return assets


def upload_asset(token: str, release_id: int, filename: str, data: bytes,
                 retries: int = 3) -> str:
    h = headers(token)
    h["Content-Type"] = "audio/mpeg"
    url = f"{UPLOAD}/repos/{REPO}/releases/{release_id}/assets?name={filename}"
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
    token = get_token()

    # Find all n3_dayNN.mp3 files sorted by day number
    day_files = sorted(AUDIO.glob("n3_day*.mp3"))
    if not day_files:
        raise SystemExit(f"No n3_day*.mp3 files found in {AUDIO}")

    release_id, release_url = get_or_create_release(token)
    existing_assets = list_existing_assets(token, release_id)
    print(f"{len(existing_assets)} assets already on release")

    mapping: dict[str, str] = {}
    if OUT.exists():
        mapping = json.loads(OUT.read_text(encoding="utf-8"))

    uploaded = skipped = 0

    for path in day_files:
        m = re.search(r"n3_day(\d+)\.mp3", path.name)
        if not m:
            continue
        day = str(int(m.group(1)))   # "01" -> "1"
        filename = path.name         # "n3_day01.mp3"

        if filename in existing_assets:
            mapping[day] = existing_assets[filename]
            skipped += 1
            print(f"  skip  {filename} (already uploaded)")
            continue

        if day in mapping and mapping[day].startswith("https://"):
            skipped += 1
            continue

        print(f"  upload {filename} ({path.stat().st_size:,} bytes)…", flush=True)
        dl_url = upload_asset(token, release_id, filename, path.read_bytes())
        mapping[day] = dl_url
        uploaded += 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(mapping, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(
        f"\ndone: {uploaded} uploaded, {skipped} skipped"
        f"\n{len(mapping)} days mapped  ->  {OUT.name}"
        f"\nRelease: {release_url}"
    )


if __name__ == "__main__":
    main()
