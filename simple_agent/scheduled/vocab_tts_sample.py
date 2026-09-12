"""Sample: turn the first few N3 words into one MP3 via edge-tts.

Per word: word -> kana reading -> English -> example 1 (natural speed),
then example 1 again slowed down. Japanese voice ja-JP-NanamiNeural.
Outputs scratchpad/vocab_sample.mp3 (plain MP3 concatenation).
"""

import asyncio
import re
import sys
from pathlib import Path

import edge_tts

TSV = Path(__file__).resolve().parent.parent / "N3_vocab_batch1_words1-100.tsv"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("vocab_sample.mp3")
N = int(sys.argv[2]) if len(sys.argv) > 2 else 3

VOICE = "ja-JP-NanamiNeural"
EN_VOICE = "en-US-AriaNeural"
_BRACKET_RE = re.compile(r"\[[぀-ヿ]+\]")


def rows(path: Path):
    cols = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#columns:"):
            cols = line[len("#columns:"):].split("\t")
        elif line.startswith("#") or not line.strip():
            continue
        elif cols:
            yield dict(zip(cols, line.split("\t")))


def strip_furigana(text: str) -> str:
    return _BRACKET_RE.sub("", text).strip()


async def synth(text: str, path: Path, voice: str, rate: str = "+0%") -> None:
    await edge_tts.Communicate(text, voice, rate=rate).save(str(path))


async def main() -> None:
    entries = list(rows(TSV))[:N]
    tmp = OUT.parent / "_tts_parts"
    tmp.mkdir(exist_ok=True)
    parts = []

    for i, e in enumerate(entries):
        word, reading, eng = e["word"], e["reading"], e["english"]
        s1 = strip_furigana(e["sentence1"])
        s1_en = e["sentence1_en"].strip()

        jp_block = f"{word}。 {reading}。 {s1}。"
        p1 = tmp / f"{i}_jp.mp3"
        await synth(jp_block, p1, VOICE)
        parts.append(p1)

        p2 = tmp / f"{i}_en.mp3"
        await synth(eng, p2, EN_VOICE)
        parts.append(p2)

        p3 = tmp / f"{i}_slow.mp3"
        await synth(f"{s1}。", p3, VOICE, rate="-30%")
        parts.append(p3)

        p4 = tmp / f"{i}_s1en.mp3"
        await synth(s1_en, p4, EN_VOICE)
        parts.append(p4)

    with OUT.open("wb") as out:
        for p in parts:
            out.write(p.read_bytes())
        for p in parts:
            p.unlink()
    tmp.rmdir()
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {len(entries)} words)")


if __name__ == "__main__":
    asyncio.run(main())
