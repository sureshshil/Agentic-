"""Build flashcard artifacts from the curated N3 TSVs:

  1. n3_vocab_anki.txt  - tab-separated, Anki "File > Import" ready, with a
     furigana field usable by Anki's {{furigana:...}} and {{kana:...}}.
  2. n3_vocab_flashcards.html - one self-contained page: flip cards,
     shuffle, keyboard nav, filter by day / word type, mark known.

Usage: build_vocab_flashcards.py [out_dir]
"""

import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")

_RUBY_RE = re.compile(r"([㐀-鿿豈-﫿々〆〇ヶ]+)\[([぀-ヿ゠-ヿー]+)\]")
_BRACKET_RE = re.compile(r"\[[぀-ヿ゠-ヿー]+\]")
_DAY_RE = re.compile(r"day(\d+)")


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
        r["idx"] = i
    return out


def strip_furigana(text: str) -> str:
    return _BRACKET_RE.sub("", text).strip()


def ruby_html(text: str) -> str:
    def repl(m):
        return f"<ruby>{html.escape(m.group(1))}<rt>{html.escape(m.group(2))}</rt></ruby>"
    # escape non-ruby parts, then splice ruby tags
    parts, last = [], 0
    for m in _RUBY_RE.finditer(text):
        parts.append(html.escape(text[last:m.start()]))
        parts.append(repl(m))
        last = m.end()
    parts.append(html.escape(text[last:]))
    return "".join(parts)


# ---- 1. Anki -------------------------------------------------------------

ANKI_FIELDS = ["Word", "WordFurigana", "Reading", "Meaning", "PartOfSpeech",
               "Type", "Sentence1", "Sentence1EN", "Sentence2", "Sentence2EN",
               "Sentence3", "Sentence3EN", "Note", "Tags"]


def write_anki(data, path: Path):
    lines = [
        "#separator:tab",
        "#html:false",
        "#notetype:N3 Vocab (drip)",
        f"#columns:{chr(9).join(ANKI_FIELDS)}",
        "#tags column:14",
    ]
    for r in data:
        row = [
            r["word"], r["word_furigana"], r["reading"], r["english"],
            r["pos"], r["type"],
            r["sentence1"], r["sentence1_en"],
            r["sentence2"], r["sentence2_en"],
            r["sentence3"], r["sentence3_en"],
            r["note"].replace("\t", " "),
            f"N3::day{r['day']:02d} {r['type']}",
        ]
        lines.append("\t".join(c.replace("\n", " ").replace("\t", " ") for c in row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(data)


# ---- 2. HTML deck ------------------------------------------------------------

def card_json(data):
    import json
    out = []
    for r in data:
        out.append({
            "w": r["word"],
            "wf": ruby_html(r["word_furigana"]),
            "r": r["reading"],
            "m": r["english"],
            "p": r["pos"],
            "t": r["type"],
            "d": r["day"],
            "ex": [
                [ruby_html(r["sentence1"]), r["sentence1_en"]],
                [ruby_html(r["sentence2"]), r["sentence2_en"]],
                [ruby_html(r["sentence3"]), r["sentence3_en"]],
            ],
            "n": r["note"],
        })
    return json.dumps(out, ensure_ascii=False)


HTML_TMPL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>N3 Vocab Flashcards</title>
<style>
  :root {
    --bg:#f4f4f2; --fg:#1c1c1e; --muted:#6b6b70; --card:#ffffff;
    --line:#e2e2df; --accent:#3a6ea5; --good:#2e7d52; --shadow:0 6px 24px rgba(0,0,0,.10);
  }
  @media (prefers-color-scheme:dark){
    :root{--bg:#161618;--fg:#ececef;--muted:#9a9aa2;--card:#232327;
      --line:#34343a;--accent:#6fa8dc;--good:#67c295;--shadow:0 6px 24px rgba(0,0,0,.4);}
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
    font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;}
  header{padding:14px 16px;border-bottom:1px solid var(--line);
    display:flex;gap:10px;flex-wrap:wrap;align-items:center;position:sticky;top:0;
    background:var(--bg);z-index:5}
  header h1{font-size:15px;margin:0;font-weight:700;letter-spacing:.02em}
  select,button{font:inherit;color:var(--fg);background:var(--card);
    border:1px solid var(--line);border-radius:8px;padding:6px 10px;cursor:pointer}
  button:active{transform:translateY(1px)}
  .spacer{flex:1}
  .count{color:var(--muted);font-variant-numeric:tabular-nums}
  main{display:flex;flex-direction:column;align-items:center;padding:24px 16px 40px}
  .card{width:min(560px,100%);background:var(--card);border:1px solid var(--line);
    border-radius:18px;box-shadow:var(--shadow);padding:32px 28px;min-height:340px;
    display:flex;flex-direction:column;justify-content:center;text-align:center;
    cursor:pointer;user-select:none}
  .word{font-size:52px;font-weight:700;line-height:1.15}
  .word rt{font-size:.34em;font-weight:500;color:var(--muted)}
  .pos{margin-top:10px;color:var(--muted);font-size:13px}
  .hint{margin-top:22px;color:var(--muted);font-size:12px;letter-spacing:.04em;text-transform:uppercase}
  .reading{font-size:26px;color:var(--accent);font-weight:600}
  .meaning{font-size:21px;margin-top:6px}
  .ex{margin-top:20px;text-align:left;border-top:1px solid var(--line);padding-top:16px}
  .ex p{margin:.5em 0}
  .ex .jp{font-size:17px}
  .ex .jp rt{font-size:.5em;color:var(--muted)}
  .ex .en{color:var(--muted);font-size:13px;margin-top:2px}
  .note{margin-top:16px;text-align:left;font-size:13px;color:var(--muted);
    background:var(--bg);border-radius:10px;padding:10px 12px}
  .controls{display:flex;gap:10px;margin-top:22px;align-items:center}
  .big{padding:10px 18px;border-radius:10px;font-weight:600}
  .known{border-color:var(--good);color:var(--good)}
  kbd{background:var(--bg);border:1px solid var(--line);border-radius:5px;
    padding:1px 6px;font-size:11px}
  footer{text-align:center;color:var(--muted);font-size:12px;padding:0 16px 30px}
</style>
</head>
<body>
<header>
  <h1>N3 Vocab</h1>
  <select id="day"></select>
  <select id="type">
    <option value="">all types</option>
    <option value="verb">verbs</option>
    <option value="noun">nouns</option>
    <option value="adj">adjectives</option>
    <option value="adv">adverbs</option>
  </select>
  <select id="mode">
    <option value="jp">JP &rarr; EN</option>
    <option value="en">EN &rarr; JP</option>
  </select>
  <button id="shuffle">shuffle</button>
  <button id="reset" title="clear known marks">reset</button>
  <span class="spacer"></span>
  <span class="count" id="count"></span>
</header>
<main>
  <div class="card" id="card"></div>
  <div class="controls">
    <button class="big" id="prev">&larr;</button>
    <button class="big" id="flip">flip</button>
    <button class="big" id="next">&rarr;</button>
    <button class="big known" id="mark">known</button>
  </div>
  <footer>
    <kbd>Space</kbd> flip &nbsp; <kbd>&larr;</kbd><kbd>&rarr;</kbd> move &nbsp;
    <kbd>K</kbd> known &nbsp; <kbd>S</kbd> shuffle &middot;
    known marks are saved in this browser
  </footer>
</main>
<script>
const CARDS = __CARDS__;
const $ = s => document.querySelector(s);
let deck = [], pos = 0, flipped = false;
let known = new Set(JSON.parse(localStorage.getItem("n3known") || "[]"));

const daySel = $("#day");
daySel.innerHTML = '<option value="">all 16 days</option>' +
  [...new Set(CARDS.map(c => c.d))].sort((a,b)=>a-b)
    .map(d => `<option value="${d}">day ${d}</option>`).join("");

function saveKnown(){ localStorage.setItem("n3known", JSON.stringify([...known])); }
function key(c){ return c.d + "|" + c.w; }

function build(){
  const d = daySel.value, t = $("#type").value;
  deck = CARDS.filter(c => (!d || c.d == d) && (!t || c.t === t));
  pos = 0; flipped = false;
  render();
}
function render(){
  const c = deck[pos];
  $("#count").textContent = deck.length ? `${pos+1} / ${deck.length}  ·  ${known.size} known` : "0";
  if(!c){ $("#card").innerHTML = "<div class='meaning'>No cards match.</div>"; return; }
  const mode = $("#mode").value;
  const front = mode === "en"
    ? `<div class="meaning">${c.m}</div><div class="pos">${c.p}</div>`
    : `<div class="word">${c.wf}</div><div class="pos">${c.p}</div>`;
  const exs = c.ex.filter(e => e[0]).map(e =>
    `<p class="jp">${e[0]}</p><p class="en">${e[1]}</p>`).join("");
  const back = `
    <div class="reading">${c.r}</div>
    <div class="word" style="font-size:34px">${mode==="en"?c.wf:""}</div>
    <div class="meaning">${mode==="en"?"":c.m}</div>
    <div class="ex">${exs}</div>
    ${c.n ? `<div class="note">${c.n}</div>` : ""}`;
  $("#card").innerHTML = (flipped ? back : front) +
    `<div class="hint">${flipped ? "" : "tap / space to flip"}</div>`;
  $("#card").style.outline = known.has(key(c)) ? "2px solid var(--good)" : "none";
}
function flip(){ flipped = !flipped; render(); }
function go(n){ if(!deck.length) return; pos = (pos + n + deck.length) % deck.length; flipped = false; render(); }
function mark(){
  const c = deck[pos]; if(!c) return;
  known.has(key(c)) ? known.delete(key(c)) : known.add(key(c));
  saveKnown(); go(1);
}
function shuffle(){
  for(let i = deck.length - 1; i > 0; i--){
    const j = Math.floor(Math.random() * (i + 1));
    [deck[i], deck[j]] = [deck[j], deck[i]];
  }
  pos = 0; flipped = false; render();
}
$("#card").onclick = flip;
$("#flip").onclick = flip;
$("#prev").onclick = () => go(-1);
$("#next").onclick = () => go(1);
$("#mark").onclick = mark;
$("#shuffle").onclick = shuffle;
$("#reset").onclick = () => { known.clear(); saveKnown(); render(); };
daySel.onchange = build; $("#type").onchange = build; $("#mode").onchange = build;
document.onkeydown = e => {
  if(e.key === " "){ e.preventDefault(); flip(); }
  else if(e.key === "ArrowRight") go(1);
  else if(e.key === "ArrowLeft") go(-1);
  else if(e.key.toLowerCase() === "k") mark();
  else if(e.key.toLowerCase() === "s") shuffle();
};
build();
</script>
</body>
</html>
"""


def write_html(data, path: Path):
    path.write_text(HTML_TMPL.replace("__CARDS__", card_json(data)), encoding="utf-8")
    return len(data)


def main():
    data = load()
    OUT.mkdir(parents=True, exist_ok=True)
    n1 = write_anki(data, OUT / "n3_vocab_anki.txt")
    n2 = write_html(data, OUT / "n3_vocab_flashcards.html")
    print(f"anki: {n1} notes -> {OUT/'n3_vocab_anki.txt'}")
    print(f"html: {n2} cards -> {OUT/'n3_vocab_flashcards.html'}")


if __name__ == "__main__":
    main()
