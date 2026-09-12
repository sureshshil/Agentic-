"""Build per-day MP3 study tracks for the N3 vocab deck via edge-tts.

Per word:  word . reading . example1 . example2   (ja-JP-NanamiNeural)
           then: English meaning . example1 EN     (en-US-AriaNeural)
Concatenated into one MP3 per day (25 words) -> <out>/n3_day01.mp3 ...

Resumable: per-word part files are cached under <out>/_parts/ and skipped
on re-run. Network failures retry a few times, then the word is skipped
and logged so the rest of the run still completes.

Usage: build_vocab_audio.py <out_dir> [day_from] [day_to]
"""

import asyncio
import re
import sys
from pathlib import Path

import edge_tts

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("vocab_audio")
DAY_FROM = int(sys.argv[2]) if len(sys.argv) > 2 else 1
DAY_TO = int(sys.argv[3]) if len(sys.argv) > 3 else 16

JP_VOICE = "ja-JP-NanamiNeural"
EN_VOICE = "en-US-AriaNeural"
_BRACKET_RE = re.compile(r"\[[぀-ヿ゠-ヿー]+\]")
_DAY_RE = re.compile(r"day(\d+)")
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
    for i, r in enumerate(out):
        m = _DAY_RE.search(r.get("tags", ""))
        r["day"] = int(m.group(1)) if m else 0
        r["n"] = i + 1
    return out


def strip_furigana(text: str) -> str:
    return _BRACKET_RE.sub("", text).strip()


async def tts(text: str, voice: str, path: Path, rate: str = "+0%") -> None:
    last = None
    for attempt in range(RETRIES):
        try:
            await edge_tts.Communicate(text, voice, rate=rate).save(str(path))
            if path.stat().st_size > 0:
                return
        except Exception as exc:  # noqa: BLE001
            last = exc
            await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"tts failed after {RETRIES}: {last}")


async def word_part(r: dict, parts_dir: Path) -> Path | None:
    dest = parts_dir / f"w{r['n']:03d}.mp3"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    jp = parts_dir / f"w{r['n']:03d}_jp.mp3"
    en = parts_dir / f"w{r['n']:03d}_en.mp3"
    try:
        jp_text = (f"{r['word']}。 {r['reading']}。 "
                   f"{strip_furigana(r['sentence1'])}。 "
                   f"{strip_furigana(r['sentence2'])}。")
        await tts(jp_text, JP_VOICE, jp)
        en_text = f"{r['english'].replace(';', ',')}。 {r['sentence1_en']}"
        await tts(en_text, EN_VOICE, en)
        dest.write_bytes(jp.read_bytes() + en.read_bytes())
        jp.unlink(missing_ok=True)
        en.unlink(missing_ok=True)
        return dest
    except Exception as exc:  # noqa: BLE001
        print(f"  ! word {r['n']} {r['word']}: {exc}", flush=True)
        return None


async def main() -> None:
    data = load()
    OUT.mkdir(parents=True, exist_ok=True)
    parts_dir = OUT / "_parts"
    parts_dir.mkdir(exist_ok=True)

    for day in range(DAY_FROM, DAY_TO + 1):
        items = [r for r in data if r["day"] == day]
        if not items:
            continue
        print(f"day {day:02d}: {len(items)} words", flush=True)
        done = []
        for r in items:
            p = await word_part(r, parts_dir)
            if p:
                done.append(p)
        target = OUT / f"n3_day{day:02d}.mp3"
        with target.open("wb") as fh:
            for p in done:
                fh.write(p.read_bytes())
        print(f"  -> {target.name} ({target.stat().st_size} bytes, "
              f"{len(done)}/{len(items)} words)", flush=True)

    print("done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
