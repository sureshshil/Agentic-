"""Generate one tiny MP3 per N3 word ("<word>。 <reading>。") via edge-tts
and write them base64-encoded into vocab_word_audio.json:

    { "場合": "<base64 mp3>", ... }

build_vocab_infographic.py embeds this map as data: URIs so each card /
review prompt has an inline play button - no network, works in the
Artifact sandbox. Resumable: per-word parts cached under _wparts/.

Usage: build_vocab_word_audio.py [out.json]
"""

import asyncio
import base64
import json
import re
import sys
from pathlib import Path

import edge_tts

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "vocab_artifacts" / "vocab_word_audio.json"
PARTS = OUT.parent / "_wparts"
VOICE = "ja-JP-NanamiNeural"
RETRIES = 3


def rows(path: Path):
    cols = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#columns:"):
            cols = line[len("#columns:"):].split("\t")
        elif line.startswith("#") or not line.strip():
            continue
        elif cols:
            yield dict(zip(cols, line.split("\t")))


def load():
    out = []
    for f in sorted(ROOT.glob("N3_vocab_batch*.tsv")):
        out += list(rows(f))
    return out


async def synth(text: str, path: Path) -> None:
    last = None
    for attempt in range(RETRIES):
        try:
            await edge_tts.Communicate(text, VOICE).save(str(path))
            if path.stat().st_size > 0:
                return
        except Exception as exc:  # noqa: BLE001
            last = exc
            await asyncio.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"tts failed: {last}")


async def main() -> None:
    data = load()
    PARTS.mkdir(parents=True, exist_ok=True)
    existing = {}
    if OUT.exists():
        existing = json.loads(OUT.read_text(encoding="utf-8"))

    out = dict(existing)
    total = 0
    for i, r in enumerate(data, 1):
        w = r["word"]
        part = PARTS / f"{i:03d}.mp3"
        if not part.exists() or part.stat().st_size == 0:
            await synth(f"{w}。 {r['reading']}。", part)
            if i % 25 == 0:
                print(f"  {i}/{len(data)}", flush=True)
        b = part.read_bytes()
        total += len(b)
        out[w] = base64.b64encode(b).decode("ascii")

    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT.name}: {len(out)} clips, {total/1e6:.1f} MB raw, "
          f"{OUT.stat().st_size/1e6:.1f} MB json", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
