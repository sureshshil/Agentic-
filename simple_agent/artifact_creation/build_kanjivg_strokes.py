"""Build kanjivg_strokes.json - per-character stroke-order data for the
stroke-order animation in the N3 vocab reference (build_vocab_infographic.py).

For every kanji / kana that appears in the `word` column of the N3 TSVs it
pulls the matching KanjiVG SVG, keeps the stroke paths (in writing order)
and the stroke-number label positions, and writes a compact JSON:

    { "場": { "s": ["<path d>", ...], "n": [[x, y], ...] }, ... }

on KanjiVG's 109x109 viewBox. Raw SVGs are cached under
vocab_artifacts/.kanjivg_cache/ so reruns are offline and fast.

Usage: python scheduled/build_kanjivg_strokes.py [out.json]
"""

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "vocab_artifacts" / "kanjivg_strokes.json"
CACHE = ROOT / "vocab_artifacts" / ".kanjivg_cache"
RAW = "https://raw.githubusercontent.com/KanjiVG/kanjivg/master/kanji/{}.svg"

PATH_RE = re.compile(r'<path id="kvg:[0-9a-f]+-s(\d+)"[^>]*\sd="([^"]+)"')
NUM_RE = re.compile(r'<text transform="matrix\(1 0 0 1 ([-\d.]+) ([-\d.]+)\)">(\d+)</text>')
FLOAT_RE = re.compile(r"-?\d+\.\d+")


def wanted_chars() -> list:
    seen = []
    for tsv in sorted(ROOT.glob("N3_vocab_batch*.tsv")):
        for line in tsv.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or not line.strip():
                continue
            for ch in line.split("\t")[0]:
                # kanji + kana (skip ascii, digits, punctuation)
                if ord(ch) > 0x2E00 and ch not in seen:
                    seen.append(ch)
    return seen


def svg_for(ch: str) -> str | None:
    name = "%05x" % ord(ch)
    fp = CACHE / f"{name}.svg"
    if fp.exists():
        return fp.read_text(encoding="utf-8")
    try:
        data = urllib.request.urlopen(RAW.format(name), timeout=20).read().decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - just skip unknown glyphs
        print(f"  ! {ch} ({name}): {exc}")
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    fp.write_text(data, encoding="utf-8")
    time.sleep(0.05)
    return data


def trim(d: str) -> str:
    return FLOAT_RE.sub(lambda m: f"{float(m.group()):.1f}", d)


def parse(svg: str) -> dict | None:
    strokes = [d for _, d in sorted(PATH_RE.findall(svg), key=lambda t: int(t[0]))]
    if not strokes:
        return None
    nums = [
        (round(float(x), 1), round(float(y), 1))
        for x, y, _ in sorted(NUM_RE.findall(svg), key=lambda t: int(t[2]))
    ]
    return {"s": [trim(s) for s in strokes], "n": nums}


def main() -> None:
    chars = wanted_chars()
    out, missing = {}, []
    for ch in chars:
        svg = svg_for(ch)
        entry = parse(svg) if svg else None
        if entry:
            out[ch] = entry
        else:
            missing.append(ch)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT} - {len(out)}/{len(chars)} chars, {OUT.stat().st_size} bytes")
    if missing:
        print(f"no KanjiVG for: {''.join(missing)}")


if __name__ == "__main__":
    main()
