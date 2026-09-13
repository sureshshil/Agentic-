"""Build the N3 vocab reference wall-chart HTML from TSV source data.

Assembles from separate parts:
  infographic_template.html  - structural HTML with __PLACEHOLDER__ markers
  infographic_style.css      - all CSS
  infographic_script.js      - all JS + client-side card rendering

Also writes vocab_artifacts/vocab_data.json (compact JSON from TSVs) which
is embedded as window.VOCAB and can be shared with other build scripts.

Usage: build_vocab_infographic.py [out.html]
"""

import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "vocab_artifacts" / "n3_vocab_reference.html"
EXAM = dt.date(2026, 12, 3)
TODAY = dt.date.today()

WORD_AUDIO_JSON  = ROOT / "vocab_artifacts" / "vocab_word_audio.json"
AUDIO_URLS_JSON  = ROOT / "vocab_artifacts" / "vocab_audio_urls.json"
KANJIVG_JSON     = ROOT / "vocab_artifacts" / "kanjivg_strokes.json"
VOCAB_DATA_JSON  = ROOT / "vocab_artifacts" / "vocab_data.json"

TYPE_ORDER = ["verb", "noun", "adj", "adv"]
TYPE_LABEL = {"verb": "verbs", "noun": "nouns", "adj": "adjectives", "adv": "adverbs"}

_DAY_RE    = re.compile(r"day(\d+)")
_BRACKET_RE = re.compile(r"\[[぀-ヿ゠-ヿー]+\]")


# ---- data loading -----------------------------------------------------------

def rows(path: Path):
    cols = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#columns:"):
            cols = line[len("#columns:"):].split("\t")
        elif line.startswith("#") or not line.strip():
            continue
        elif cols:
            yield dict(zip(cols, line.split("\t")))


def load_tsv() -> list:
    out = []
    for f in sorted(ROOT.glob("N3_vocab_batch*.tsv")):
        out += list(rows(f))
    for r in out:
        m = _DAY_RE.search(r.get("tags", ""))
        r["day"] = int(m.group(1)) if m else 0
    return out


def to_vocab_json(data: list) -> list:
    """Convert TSV rows to compact JSON records for window.VOCAB."""
    return [
        {
            "w":  r["word"],
            "wf": r["word_furigana"],
            "r":  r["reading"],
            "m":  r["english"],
            "p":  r["pos"],
            "t":  r["type"],
            "d":  r["day"],
            "fr": r["frame"],
            "n":  r["note"],
            "s":  [
                [r["sentence1"],  r["sentence1_en"]],
                [r["sentence2"],  r["sentence2_en"]],
                [r["sentence3"],  r["sentence3_en"]],
            ],
        }
        for r in data
    ]


# ---- header helpers ---------------------------------------------------------

def type_bar(data: list) -> str:
    counts = {t: sum(1 for r in data if r["type"] == t) for t in TYPE_ORDER}
    return "".join(
        f'<span class="tb-seg tb-{t}" style="flex:{counts[t]}">'
        f'<span class="tb-lab">{TYPE_LABEL[t]}</span>'
        f'<span class="tb-n">{counts[t]}</span></span>'
        for t in TYPE_ORDER
    )


def nav_links(data: list) -> str:
    days = sorted({r["day"] for r in data})
    return "".join(f'<a href="#day{d:02d}">{d:02d}</a>' for d in days)


# ---- assembly ---------------------------------------------------------------

def build(data: list) -> str:
    vocab_json = to_vocab_json(data)

    # Prefer streaming URLs (Blob) over embedded base64; fall back to base64 if no URLs file
    if AUDIO_URLS_JSON.exists():
        wa_json = AUDIO_URLS_JSON.read_text(encoding="utf-8").strip()
    elif WORD_AUDIO_JSON.exists():
        wa_json = WORD_AUDIO_JSON.read_text(encoding="utf-8").strip()
    else:
        wa_json = "{}"
    kvg_json = KANJIVG_JSON.read_text(encoding="utf-8").strip() if KANJIVG_JSON.exists() else "{}"

    css      = (HERE / "infographic_style.css").read_text(encoding="utf-8")
    js       = (HERE / "infographic_script.js").read_text(encoding="utf-8")
    template = (HERE / "infographic_template.html").read_text(encoding="utf-8")

    data_script = (
        f"window.VOCAB = {json.dumps(vocab_json, ensure_ascii=False)};\n"
        f"window.WA    = {wa_json};\n"
        f"window.KVG   = {kvg_json};"
    )

    dleft = (EXAM - TODAY).days

    return (
        template
        .replace("__CSS__",      css)
        .replace("__DATA__",     data_script)
        .replace("__JS__",       js)
        .replace("__DLEFT__",    str(dleft))
        .replace("__TYPE_BAR__", type_bar(data))
        .replace("__NAV__",      nav_links(data))
        .replace("__N__",        str(len(data)))
        .replace("__TODAY__",    TODAY.isoformat())
    )


def main():
    data = load_tsv()
    if not data:
        raise SystemExit("No N3_vocab_batch*.tsv files found next to simple_agent/")

    vocab_json = to_vocab_json(data)
    VOCAB_DATA_JSON.parent.mkdir(parents=True, exist_ok=True)
    VOCAB_DATA_JSON.write_text(
        json.dumps(vocab_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"vocab_data.json: {len(vocab_json)} words -> {VOCAB_DATA_JSON}")

    html = build(data)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"n3_vocab_reference.html: {OUT.stat().st_size:,} bytes -> {OUT}")


if __name__ == "__main__":
    main()
