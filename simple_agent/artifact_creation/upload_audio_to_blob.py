"""Upload per-word MP3s to Vercel Blob and write a word → URL mapping.

Reads:  vocab_artifacts/audio/_parts/w001.mp3 … w400.mp3
Writes: vocab_artifacts/vocab_audio_urls.json  { "word": "https://...", ... }

Resumable: skips words already present in the output JSON.

Usage:
    BLOB_READ_WRITE_TOKEN=vercel_blob_... python upload_audio_to_blob.py
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT  = Path(__file__).resolve().parent.parent
PARTS = ROOT / "vocab_artifacts" / "audio" / "_parts"
OUT   = ROOT / "vocab_artifacts" / "vocab_audio_urls.json"

BLOB_API   = "https://blob.vercel-storage.com"
API_VERSION = "7"
_DAY_RE    = re.compile(r"day(\d+)")


def get_token() -> str:
    tok = os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip()
    if not tok:
        raise SystemExit(
            "BLOB_READ_WRITE_TOKEN not set.\n"
            "Set it with:  $env:BLOB_READ_WRITE_TOKEN='vercel_blob_...'"
        )
    return tok


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


def upload(token: str, pathname: str, data: bytes, retries: int = 3) -> str:
    """Upload bytes to Vercel Blob, return public URL."""
    url = f"{BLOB_API}/{pathname}"
    headers = {
        "Authorization": f"Bearer {token}",
        "x-api-version": API_VERSION,
        "content-type": "audio/mpeg",
        "x-cache-control-max-age": "31536000",  # 1 year
    }
    for attempt in range(retries):
        try:
            r = requests.put(url, data=data, headers=headers, timeout=60)
            r.raise_for_status()
            return r.json()["url"]
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt
            print(f"  retry {attempt+1} after {wait}s ({exc})", flush=True)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def main() -> None:
    token = get_token()
    words = load_words()
    if not words:
        raise SystemExit("No N3_vocab_batch*.tsv files found.")

    existing: dict[str, str] = {}
    if OUT.exists():
        existing = json.loads(OUT.read_text(encoding="utf-8"))

    mapping = dict(existing)
    uploaded = skipped = missing = 0

    for i, word in enumerate(words, 1):
        part = PARTS / f"w{i:03d}.mp3"
        if not part.exists() or part.stat().st_size == 0:
            missing += 1
            continue
        if word in mapping:
            skipped += 1
            continue
        blob_url = upload(token, f"n3-audio/w{i:03d}.mp3", part.read_bytes())
        mapping[word] = blob_url
        uploaded += 1
        if uploaded % 25 == 0:
            OUT.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
            print(f"  {uploaded} uploaded so far…", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    print(
        f"done: {uploaded} uploaded, {skipped} already existed, {missing} missing"
        f"  ->  {OUT.name}  ({len(mapping)} total words)"
    )


if __name__ == "__main__":
    main()
