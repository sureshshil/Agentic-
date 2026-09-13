"""Upload per-word MP3s as GitHub Release assets and write a word → URL map.

Creates (or reuses) a release tagged  n3-audio-v1  on the repo.
Resumable: already-uploaded assets are skipped.

Reads:  vocab_artifacts/audio/_parts/w001.mp3 … w400.mp3
Writes: vocab_artifacts/vocab_audio_urls.json  { "word": "https://...", ... }

Usage:
    GITHUB_TOKEN=ghp_... python upload_audio_to_github.py

Get a token at https://github.com/settings/tokens
Required scopes: repo  (or  public_repo  for public repos)
"""

import json
import os
import re
import time
from pathlib import Path

import requests

ROOT   = Path(__file__).resolve().parent.parent
PARTS  = ROOT / "vocab_artifacts" / "audio" / "_parts"
OUT    = ROOT / "vocab_artifacts" / "vocab_audio_urls.json"

REPO   = "sureshshil/Agentic-"
TAG    = "n3-audio-v1"
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
    """Return (release_id, download_base_url)."""
    h = headers(token)
    r = requests.get(f"{API}/repos/{REPO}/releases/tags/{TAG}", headers=h, timeout=30)
    if r.status_code == 200:
        data = r.json()
        print(f"reusing existing release: {data['html_url']}")
        return data["id"], data["html_url"]

    # Create it
    payload = {
        "tag_name": TAG,
        "name": "N3 Vocab Word Audio",
        "body": "Per-word MP3 clips for the N3 vocabulary reference page (word + reading + examples).",
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


def rows(path: Path):
    cols = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#columns:"):
            cols = line[len("#columns:"):].split("\t")
        elif line.startswith("#") or not line.strip():
            continue
        elif cols:
            yield dict(zip(cols, line.split("\t")))


def load_words() -> list[str]:
    out = []
    for f in sorted(ROOT.glob("N3_vocab_batch*.tsv")):
        out += [r["word"] for r in rows(f)]
    return out


def main() -> None:
    token = get_token()
    words = load_words()
    if not words:
        raise SystemExit("No N3_vocab_batch*.tsv files found.")

    release_id, release_url = get_or_create_release(token)
    existing_assets = list_existing_assets(token, release_id)
    print(f"{len(existing_assets)} assets already on release")

    mapping: dict[str, str] = {}
    if OUT.exists():
        mapping = json.loads(OUT.read_text(encoding="utf-8"))

    uploaded = skipped = missing = 0

    for i, word in enumerate(words, 1):
        part = PARTS / f"w{i:03d}.mp3"
        filename = f"w{i:03d}.mp3"

        if not part.exists() or part.stat().st_size == 0:
            missing += 1
            continue

        if filename in existing_assets:
            mapping[word] = existing_assets[filename]
            skipped += 1
            continue

        if word in mapping and mapping[word].startswith("https://"):
            skipped += 1
            continue

        dl_url = upload_asset(token, release_id, filename, part.read_bytes())
        mapping[word] = dl_url
        uploaded += 1
        if uploaded % 10 == 0:
            OUT.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
            print(f"  {uploaded} uploaded…", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    print(
        f"\ndone: {uploaded} uploaded, {skipped} skipped, {missing} missing"
        f"\n{len(mapping)} words mapped  ->  {OUT.name}"
        f"\nRelease: {release_url}"
    )


if __name__ == "__main__":
    main()
