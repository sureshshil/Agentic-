"""Render the N3 vocab reference wall-chart (Artifact HTML) from the TSVs.

Static-renders all 400 entries so the page reads at rest and prints. JS adds:
  - live search (word / reading / meaning / 型 frame)
  - click a card to expand (sentences 2-3 + usage note)
  - a Leitner spaced-repetition layer: every word has a box (0-6) and a due
    date in localStorage; a Review mode walks the due + new queue with
    Again / Good / Easy grading; the browse list shows each word's state and
    a status filter (all / due / new / learning / mature)
  - furigana / meanings / 型 visibility toggles
  - a per-word stroke-order animation (✎ on each card / press W): each
    kanji/kana is drawn one stroke at a time from embedded KanjiVG paths,
    with the stroke number shown as it goes; replay / play-pause /
    step / show-all-strokes / number toggle. Needs kanjivg_strokes.json
    (built by build_kanjivg_strokes.py); the ✎ button is disabled if it
    is missing.
  - export / import of the SRS progress as JSON

Output: n3_vocab_reference.html (no <html>/<head>/<body> wrapper - the
Artifact tool adds it).
"""

import datetime as dt
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("n3_vocab_reference.html")
EXAM = dt.date(2026, 12, 3)
TODAY = dt.date.today()

# Per-word "<word>。 <reading>。" clips, base64, from build_vocab_word_audio.py.
# Embedded as data: URIs so playback works inside the Artifact sandbox
# (which blocks all external media). Optional - {} if not generated yet.
WORD_AUDIO_JSON = ROOT / "vocab_artifacts" / "vocab_word_audio.json"

# Per-character KanjiVG stroke data: { "場": {"s": ["<path d>", ...],
# "n": [[x, y], ...] } } on a 109x109 viewBox, strokes in writing order and
# "n" the KanjiVG stroke-number label positions. Built by
# build_kanjivg_strokes.py from the words' kanji + kana. Optional - the
# stroke-order button is disabled when it is absent.
KANJIVG_JSON = ROOT / "vocab_artifacts" / "kanjivg_strokes.json"

# The full per-day tracks (word + reading + two example sentences) that the
# user uploaded to Google Drive. Too large to embed (~37 MB); fetched at
# runtime through the viewer's Google Drive connector via the `mcp`
# capability, with an "open in Drive" link as the fallback.
DRIVE_CONNECTOR = "Google Drive"  # connector display name matched by callTool()
DRIVE_AUDIO = {
    1: "12hZEPhZIj7YwZojl8TvyuROhdbJqsH9F", 2: "1vACRAolRPOYlO2210NFS9tl5HMZmiXzs",
    3: "18GFKmqLiN7xuiptQ3hTFV1kSb6L8fOoM", 4: "156Mr6VUE_f6RFjRwgg_p79vwMms1BW5_",
    5: "1zThfEWXZIDfU103DNJypLBTY9jmRaZsE", 6: "1EdZDAbko8pghisnoQ9-FaEa3x3MMPm86",
    7: "1oVQ9xZF_oj_iZFmvRm1v395cMCBxuj4r", 8: "1B00nbuBHHf7t-krx8rY5bGE83FncMMf8",
    9: "1S1_kbx7itiCP5FPY0lRIRlwphCGxlZUo", 10: "1OMs9p8Wm-8efRd6z6owzC8Iy3RX4MGak",
    11: "1EnctlT2m2q3zpsRPaOcAB4IMHJxTzweh", 12: "12AiPOG6lPhcjEJUkSANoo5rW4VKgmT4s",
    13: "13IFsaWr8fEbpsVc5PL5IGhPDcyqIgvsT", 14: "1j-dPvBJbCpXPTnLq7t9DMU5amu_ZcqVG",
    15: "1aWKBQsVPifcAbb_CMGkte_6nNRAnzXDL", 16: "1EOg_uUHR4CyYxew5y2fk2JLykV-NJHkz",
}

_RUBY_RE = re.compile(r"([㐀-鿿豈-﫿々〆〇ヶ]+)\[([぀-ヿ゠-ヿー]+)\]")
_BRACKET_RE = re.compile(r"\[[぀-ヿ゠-ヿー]+\]")
_DAY_RE = re.compile(r"day(\d+)")
TYPE_LABEL = {"verb": "verbs", "noun": "nouns", "adj": "adjectives", "adv": "adverbs"}
TYPE_ORDER = ["verb", "noun", "adj", "adv"]


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
    for r in out:
        m = _DAY_RE.search(r.get("tags", ""))
        r["day"] = int(m.group(1)) if m else 0
    return out


def ruby(text: str) -> str:
    parts, last = [], 0
    for m in _RUBY_RE.finditer(text):
        parts.append(html.escape(text[last:m.start()]))
        parts.append(f"<ruby>{html.escape(m.group(1))}<rt>{html.escape(m.group(2))}</rt></ruby>")
        last = m.end()
    parts.append(html.escape(text[last:]))
    return "".join(parts)


def plain(text: str) -> str:
    return _BRACKET_RE.sub("", text)


def esc(s: str) -> str:
    return html.escape(s or "")


def sentence_block(n: int, jp: str, en: str) -> str:
    return f"""<div class="ex">
        <span class="ex-no">{n}</span>
        <span class="ex-jp">{ruby(jp)}</span>
        <span class="ex-en">{esc(en)}</span>
      </div>"""


def entry_html(r: dict) -> str:
    search = " ".join([
        plain(r["word"]), r["reading"], r["english"].lower(),
        r["frame"], plain(r["sentence1"]),
    ]).lower()
    note = esc(r["note"])
    more = f'\n    <div class="more"><p class="nt">{note}</p></div>' if r["note"].strip() else ""
    cue = '<span class="e-cue">+</span>' if r["note"].strip() else ""
    sentences = "\n      ".join([
        sentence_block(1, r["sentence1"], r["sentence1_en"]),
        sentence_block(2, r["sentence2"], r["sentence2_en"]),
        sentence_block(3, r["sentence3"], r["sentence3_en"]),
    ])
    return f"""<article class="e{' has-note' if r['note'].strip() else ''}" data-w="{esc(r['word'])}" data-s="{esc(search)}">
  <button class="pip" type="button" aria-label="I know this - advance" title="I know this - advance"></button>
  <div class="e-body">
    <div class="e-head">
      <span class="w">{ruby(r['word_furigana'])}</span>
      <span class="rd">{esc(r['reading'])}</span>
      <button class="say" type="button" data-w="{esc(r['word'])}" aria-label="pronounce {esc(r['word'])}" title="say it">&#9654;</button>
      <button class="draw" type="button" data-w="{esc(r['word'])}" aria-label="stroke order for {esc(r['word'])}" title="stroke order">&#9998;</button>
      <span class="pos">{esc(r['pos'])}</span>
    </div>
    <p class="mn">{esc(r['english'])}</p>
    <p class="pt"><span class="pt-k">型</span>{esc(r['frame'])}</p>
    <div class="ex-grid">
      {sentences}
    </div>{more}
  </div>
  <span class="e-tags" aria-hidden="true"><span class="e-due">due</span>{cue}</span>
</article>"""


def day_html(day: int, items: list) -> str:
    counts = {}
    for r in items:
        counts[r["type"]] = counts.get(r["type"], 0) + 1
    mix = " · ".join(f"{counts[t]} {t}" for t in TYPE_ORDER if counts.get(t))
    entries = "\n".join(entry_html(r) for r in items)
    fid = DRIVE_AUDIO.get(day, "")
    return f"""<section class="day" id="day{day:02d}" data-day="{day}">
  <div class="day-head">
    <span class="day-no">DAY {day:02d}</span>
    <span class="day-meta">{len(items)} words &nbsp;/&nbsp; {mix}</span>
    <span class="day-tally" data-day-tally="{day}"></span>
    <button class="day-audio" type="button" data-day="{day}" data-fid="{fid}"
      aria-expanded="false" aria-controls="dp{day}">&#9654; full track</button>
  </div>
  <div class="day-player" id="dp{day}" hidden></div>
  <div class="grid">
{entries}
  </div>
</section>"""


def build(data: list) -> str:
    days = sorted({r["day"] for r in data})
    by_day = {d: [r for r in data if r["day"] == d] for d in days}
    total_types = {t: sum(1 for r in data if r["type"] == t) for t in TYPE_ORDER}
    n = len(data)
    dleft = (EXAM - TODAY).days
    wa_json = "{}"
    if WORD_AUDIO_JSON.exists():
        wa_json = WORD_AUDIO_JSON.read_text(encoding="utf-8").strip() or "{}"
    has_word_audio = wa_json != "{}"

    kvg_json = "{}"
    if KANJIVG_JSON.exists():
        kvg_json = KANJIVG_JSON.read_text(encoding="utf-8").strip() or "{}"
    has_kvg = kvg_json != "{}"

    type_bar = "".join(
        f'<span class="tb-seg tb-{t}" style="flex:{total_types[t]}">'
        f'<span class="tb-lab">{TYPE_LABEL[t]}</span>'
        f'<span class="tb-n">{total_types[t]}</span></span>'
        for t in TYPE_ORDER
    )
    nav = "".join(f'<a href="#day{d:02d}">{d:02d}</a>' for d in days)
    sections = "\n".join(day_html(d, by_day[d]) for d in days)

    return f"""<title>N3 語彙 400</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Shippori+Mincho:wght@500;600;700&family=Newsreader:ital,wght@0,400;0,500;1,400&family=JetBrains+Mono:wght@400;500;700&display=swap">
<style>
  :root {{
    --paper:#faf7f0; --ink:#23241f; --muted:#74766c; --faint:#9a9b90;
    --seal:#b23a48; --reed:#3f6f4e; --furi:#2f52c4;
    --surface:#fffef9; --hair:#e4e0d5; --grid:#ece7d8;
    --sans:"Shippori Mincho",'Hiragino Mincho ProN','Yu Mincho',serif;
    --serif:"Newsreader",Georgia,'Times New Roman',serif;
    --mono:"JetBrains Mono",ui-monospace,'SFMono-Regular',Menlo,monospace;
  }}
  @media (prefers-color-scheme:dark) {{
    :root:not([data-theme="light"]) {{
      --paper:#1a1b17; --ink:#e9e7dd; --muted:#9a9c8f; --faint:#787a6e;
      --seal:#e0808f; --reed:#7cbf90; --furi:#93aaff;
      --surface:#22231e; --hair:#33342d; --grid:#2b2c25;
    }}
  }}
  :root[data-theme="dark"] {{
    --paper:#1a1b17; --ink:#e9e7dd; --muted:#9a9c8f; --faint:#787a6e;
    --seal:#e0808f; --reed:#7cbf90; --furi:#93aaff;
    --surface:#22231e; --hair:#33342d; --grid:#2b2c25;
  }}
  * {{ box-sizing:border-box; }}
  /* base size drives every rem below - bumped for tablet reading (iPad Air 11") */
  html {{ font-size:40px; }}
  body {{
    margin:0; background:var(--paper); color:var(--ink);
    font-family:var(--sans); font-size:1rem; line-height:1.6; -webkit-font-smoothing:antialiased;
  }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:0 20px 90px; }}

  /* masthead */
  header {{
    padding:44px 0 22px; border-bottom:2px solid var(--ink);
    background-image:
      linear-gradient(var(--grid) 1px,transparent 1px),
      linear-gradient(90deg,var(--grid) 1px,transparent 1px);
    background-size:26px 26px; background-position:-1px -1px;
  }}
  header .inner {{ background:var(--paper); display:inline-block; padding-right:18px; }}
  h1 {{ font-weight:700; font-size:clamp(2.1rem,5vw,3rem); margin:0;
    letter-spacing:.01em; text-wrap:balance; }}
  h1 .lat {{ font-family:var(--mono); font-weight:500; font-size:.42em;
    letter-spacing:.16em; color:var(--muted); display:block; margin-top:.5em; }}
  .sub {{ margin:14px 0 0; color:var(--muted); font-family:var(--serif);
    font-size:1.02rem; max-width:62ch; }}
  .k-furi {{ color:var(--furi); font-weight:500; }}
  .k-seal {{ color:var(--seal); }}
  .dcount {{ display:inline-flex; align-items:baseline; gap:.5em; margin-top:18px;
    font-family:var(--mono); font-size:.82rem; letter-spacing:.06em;
    color:var(--seal); border:1px solid var(--seal); border-radius:2px; padding:5px 10px; }}
  .dcount b {{ font-weight:700; font-size:1.05rem; }}

  .tbar {{ display:flex; margin-top:24px; height:44px; border:1px solid var(--hair);
    border-radius:2px; overflow:hidden; font-family:var(--mono); }}
  .tb-seg {{ display:flex; flex-direction:column; justify-content:center;
    padding:0 12px; border-right:1px solid var(--paper); min-width:0; }}
  .tb-seg:last-child {{ border-right:0; }}
  .tb-lab {{ font-size:.66rem; letter-spacing:.08em; text-transform:uppercase;
    color:var(--ink); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .tb-n {{ font-size:.9rem; font-weight:700; }}
  .tb-verb {{ background:color-mix(in srgb,var(--reed) 20%,var(--surface)); }}
  .tb-noun {{ background:color-mix(in srgb,var(--seal) 15%,var(--surface)); }}
  .tb-adj  {{ background:color-mix(in srgb,var(--reed) 10%,var(--surface)); }}
  .tb-adv  {{ background:var(--surface); }}

  /* sticky controls */
  .controls {{
    position:sticky; top:0; z-index:20; background:var(--paper);
    border-bottom:1px solid var(--hair); padding:10px 0 8px;
    display:flex; gap:10px 14px; align-items:center; flex-wrap:wrap;
    font-family:var(--mono); font-size:15px;  /* pinned - stays compact while the cards scale up */
  }}
  .search {{ position:relative; flex:1 1 180px; max-width:300px; }}
  .search input {{ width:100%; font:inherit; color:var(--ink); background:var(--surface);
    border:1px solid var(--hair); border-radius:2px; padding:6px 24px 6px 9px; }}
  .search input:focus-visible {{ outline:2px solid var(--reed); outline-offset:1px; }}
  .search .x {{ position:absolute; right:5px; top:50%; transform:translateY(-50%);
    border:0; background:none; color:var(--muted); cursor:pointer; font-size:1rem;
    line-height:1; padding:2px; }}
  .search .x[hidden] {{ display:none; }}

  .review-btn {{ font:inherit; font-weight:700; letter-spacing:.04em; cursor:pointer;
    color:var(--surface); background:var(--seal); border:1px solid var(--seal);
    border-radius:2px; padding:6px 12px; }}
  .review-btn:disabled {{ background:var(--surface); color:var(--faint);
    border-color:var(--hair); cursor:default; font-weight:500; }}
  .review-btn:focus-visible {{ outline:2px solid var(--reed); outline-offset:2px; }}

  .stat {{ display:flex; align-items:center; gap:8px; color:var(--muted); }}
  .stat .bar {{ width:90px; height:6px; background:var(--hair); border-radius:3px; overflow:hidden; }}
  .stat .bar i {{ display:block; height:100%; width:0; background:var(--reed); transition:width .3s ease; }}
  .stat b {{ font-weight:700; }}
  .stat .s-mat {{ color:var(--reed); }} .stat .s-due {{ color:var(--seal); }}

  .filter {{ font:inherit; color:var(--muted); background:var(--surface);
    border:1px solid var(--hair); border-radius:2px; padding:5px 6px; cursor:pointer; }}

  .toggles {{ display:flex; gap:4px; margin-left:auto; flex-wrap:wrap; }}
  .toggles button {{ font:inherit; color:var(--muted); background:var(--surface);
    border:1px solid var(--hair); border-radius:2px; padding:4px 9px; cursor:pointer;
    letter-spacing:.03em; }}
  .toggles button[aria-pressed="true"] {{ color:var(--seal); border-color:var(--seal); }}
  .toggles button:focus-visible {{ outline:2px solid var(--reed); outline-offset:1px; }}

  .navdays {{ display:flex; gap:2px; flex-wrap:wrap; flex-basis:100%; order:5; }}
  .navdays a {{ color:var(--muted); text-decoration:none; padding:3px 6px;
    border-radius:2px; letter-spacing:.04em; }}
  .navdays a:hover {{ color:var(--seal); background:var(--surface); }}

  /* day */
  .day {{ padding-top:34px; scroll-margin-top:100px; }}
  .day.empty {{ display:none; }}
  .day-head {{ display:flex; align-items:baseline; gap:14px;
    border-bottom:1px solid var(--reed); padding-bottom:6px; margin-bottom:16px; }}
  .day-no {{ font-family:var(--mono); font-weight:700; font-size:.95rem;
    letter-spacing:.1em; color:var(--reed); }}
  .day-meta {{ font-family:var(--mono); font-size:.7rem; color:var(--faint);
    letter-spacing:.04em; }}
  .day-tally {{ font-family:var(--mono); font-size:.7rem; color:var(--reed);
    letter-spacing:.04em; margin-left:auto; }}
  .day-tally:empty {{ display:none; }}

  .grid {{ display:flex; flex-direction:column; }}
  .e {{ position:relative; padding:0.55rem 1.1rem 0.6rem 0.75rem;
    border-bottom:1px solid var(--hair); break-inside:avoid; }}
  .e.has-note {{ cursor:pointer; }}
  .e.hit {{ display:none; }}
  .w {{ font-size:1.36rem; font-weight:600; }}

  /* furigana - saturated blue, its own weight, never the ground */
  ruby rt {{ color:var(--furi); font-family:var(--mono); font-weight:500;
    letter-spacing:.02em; }}
  .w rt {{ font-size:.46em; }}
  .ex-jp rt {{ font-size:.56em; }}

  .e-head {{ display:flex; align-items:baseline; gap:9px; flex-wrap:wrap; }}
  .rd {{ font-family:var(--mono); font-size:.78rem; color:var(--seal); letter-spacing:.02em; }}
  .pos {{ font-family:var(--mono); font-size:.62rem; color:var(--muted);
    border:1px solid var(--hair); border-radius:2px; padding:1px 5px;
    letter-spacing:.02em; margin-left:auto; }}
  .say {{ font:inherit; font-family:var(--mono); font-size:.28rem; line-height:1;
    cursor:pointer; color:var(--muted); background:var(--surface);
    border:1px solid var(--hair); border-radius:50%; width:.55rem; height:.55rem;
    padding:0; display:inline-flex; align-items:center; justify-content:center;
    flex:none; }}
  .say:hover {{ color:var(--seal); border-color:var(--seal); }}
  .say:focus-visible {{ outline:2px solid var(--reed); outline-offset:2px; }}
  .say.playing {{ color:var(--seal); border-color:var(--seal);
    background:color-mix(in srgb,var(--seal) 14%,var(--surface)); }}
  .draw {{ font:inherit; font-family:var(--mono); font-size:.3rem; line-height:1;
    cursor:pointer; color:var(--muted); background:var(--surface);
    border:1px solid var(--hair); border-radius:50%; width:.55rem; height:.55rem;
    padding:0; display:inline-flex; align-items:center; justify-content:center; flex:none; }}
  .draw:hover {{ color:var(--reed); border-color:var(--reed); }}
  .draw:focus-visible {{ outline:2px solid var(--reed); outline-offset:2px; }}
  .draw[disabled] {{ opacity:.3; cursor:default; }}

  /* ---- stroke-order overlay ----------------------------------------- */
  .so[hidden] {{ display:none; }}
  .so {{ position:fixed; inset:0; z-index:55;
    background:color-mix(in srgb,var(--paper) 88%,transparent);
    backdrop-filter:blur(3px); display:flex; align-items:center; justify-content:center; padding:20px;
    -webkit-tap-highlight-color:transparent; }}
  html.so-lock, body.so-lock {{ overflow:hidden !important; }}
  .so-card {{ width:min(720px,100%); max-height:calc(100vh - 40px); overflow:auto;
    background:var(--surface); border:1px solid var(--hair); border-top:3px solid var(--reed);
    border-radius:3px; padding:18px 20px 16px;
    box-shadow:0 20px 60px color-mix(in srgb,var(--ink) 22%,transparent); }}
  .so-top {{ display:flex; align-items:baseline; gap:12px; margin-bottom:14px; font-family:var(--mono); }}
  .so-top .sw {{ font-family:var(--sans); font-size:26px; font-weight:700; }}
  .so-top .srd {{ font-size:13px; color:var(--seal); }}
  .so-top .so-x {{ margin-left:auto; font:inherit; font-size:12px; cursor:pointer;
    color:var(--muted); background:none; border:1px solid var(--hair); border-radius:2px; padding:3px 7px; }}

  .so-chars {{ display:flex; flex-wrap:wrap; gap:12px; justify-content:center; }}
  .so-char {{ flex:none; width:clamp(150px,40vw,220px); }}
  .so-char svg {{ display:block; width:100%; height:auto; border:1px solid var(--hair);
    border-radius:2px; background:
      linear-gradient(var(--grid) 1px,transparent 1px) center / 100% 50% no-repeat,
      linear-gradient(90deg,var(--grid) 1px,transparent 1px) center / 50% 100% no-repeat,
      var(--surface); }}
  .so-char .ghost {{ stroke:var(--hair); stroke-width:4; fill:none;
    stroke-linecap:round; stroke-linejoin:round; }}
  .so-char .ink path {{ stroke:var(--ink); stroke-width:4.5; fill:none;
    stroke-linecap:round; stroke-linejoin:round; }}
  .so-char .ink path.lead {{ stroke:var(--seal); stroke-width:5.5; }}
  .so-char .nums text {{ font-family:var(--mono); font-size:8px; font-weight:700;
    fill:var(--seal); paint-order:stroke; stroke:var(--surface); stroke-width:2.4px;
    stroke-linejoin:round; transition:opacity .2s ease; }}
  .so-card.no-nums .nums {{ display:none; }}
  .so-char .cap {{ margin-top:4px; font-family:var(--mono); font-size:11px;
    color:var(--faint); text-align:center; letter-spacing:.04em; }}
  .so-char.nodata {{ display:flex; flex-direction:column; align-items:center;
    justify-content:center; height:clamp(150px,40vw,220px); border:1px dashed var(--hair);
    border-radius:2px; font-family:var(--sans); font-size:64px; color:var(--faint); }}
  .so-char.nodata span {{ font-family:var(--mono); font-size:10px; margin-top:8px; }}

  .so-actions {{ display:flex; gap:6px; margin-top:14px; flex-wrap:wrap; font-family:var(--mono); }}
  .so-actions button {{ font:inherit; font-size:13px; cursor:pointer; color:var(--ink);
    background:var(--surface); border:1px solid var(--hair); border-radius:2px; padding:5px 10px;
    letter-spacing:.03em; }}
  .so-actions button:hover {{ border-color:var(--reed); }}
  .so-actions button[aria-pressed="true"] {{ color:var(--reed); border-color:var(--reed); }}
  .so-actions button:focus-visible {{ outline:2px solid var(--reed); outline-offset:2px; }}
  .so-actions .so-hint {{ margin-left:auto; align-self:center; font-size:12px;
    color:var(--faint); letter-spacing:.04em; }}
  .so-actions .so-hint b {{ color:var(--seal); font-weight:700; }}

  .day-audio {{ font:inherit; font-family:var(--mono); font-size:.66rem;
    letter-spacing:.04em; cursor:pointer; color:var(--reed);
    background:var(--surface); border:1px solid var(--hair); border-radius:2px;
    padding:3px 8px; }}
  .day-audio:hover {{ border-color:var(--reed); }}
  .day-audio:focus-visible {{ outline:2px solid var(--reed); outline-offset:2px; }}
  .day-audio[aria-expanded="true"] {{ color:var(--seal); border-color:var(--seal); }}
  .day-player {{ margin:4px 0 14px; }}
  .day-player[hidden] {{ display:none; }}
  .day-player audio {{ width:100%; max-width:520px; height:38px; display:block; }}
  .dp-load {{ font-family:var(--mono); font-size:.72rem; color:var(--muted); }}
  .dp-link {{ font-family:var(--mono); font-size:.72rem; color:var(--reed); }}
  .dp-err {{ font-family:var(--mono); font-size:.66rem; color:var(--faint); }}

  .mn {{ margin:5px 0 0; font-family:var(--serif); font-size:.97rem; }}
  .pt {{ margin:5px 0 0; font-size:.82rem; color:var(--muted); line-height:1.5; }}
  .pt-k {{ font-size:.62rem; color:var(--seal); border:1px solid var(--seal);
    border-radius:2px; padding:0 3px; margin-right:6px; position:relative; top:-1px; }}
  .ex-grid {{ margin-top:.5rem; display:flex; flex-direction:column; gap:.6rem; }}
  .ex {{ position:relative; padding-left:1.15rem; }}
  .ex-no {{ position:absolute; left:0; top:.15rem; font-family:var(--mono);
    font-size:.66rem; font-weight:700; color:var(--seal); }}
  .ex-jp {{ display:block; font-size:.92rem; }}
  .ex-en {{ display:block; font-family:var(--serif); font-style:italic;
    font-size:.85rem; color:var(--muted); margin-top:2px; }}

  .e-tags {{ position:absolute; top:.45rem; right:.35rem; display:flex; flex-direction:column;
    align-items:flex-end; gap:3px; font-family:var(--mono); }}
  .e-cue {{ font-size:.45rem; color:var(--faint); user-select:none; }}
  .e.open .e-cue {{ color:var(--seal); }}
  .e-due {{ font-size:.3rem; letter-spacing:.08em; text-transform:uppercase;
    color:var(--seal); border:1px solid var(--seal); border-radius:2px;
    padding:0 3px; display:none; }}
  .e.is-due .e-due {{ display:block; }}

  .more {{ display:none; margin-top:8px; padding-top:8px; border-top:1px dotted var(--hair); }}
  .e.open .more {{ display:block; }}
  .nt {{ margin:8px 0 0; font-size:.78rem; color:var(--faint); line-height:1.5; }}

  /* SRS status pip: hollow=new, half=learning, filled=mature */
  .pip {{ position:absolute; top:.5rem; left:0; width:.42rem; height:.42rem; padding:0;
    border:1.5px solid var(--faint); border-radius:50%; background:var(--surface);
    cursor:pointer; }}
  .pip:hover {{ border-color:var(--reed); }}
  .pip:focus-visible {{ outline:2px solid var(--reed); outline-offset:2px; }}
  .e.st-learning .pip {{ border-color:var(--reed);
    background:linear-gradient(90deg,var(--reed) 50%,var(--surface) 50%); }}
  .e.st-mature .pip {{ background:var(--reed); border-color:var(--reed); }}
  .e.st-mature .w {{ color:var(--muted); }}

  body[data-filter="due"] .e:not(.is-due),
  body[data-filter="new"] .e:not(.st-new),
  body[data-filter="learning"] .e:not(.st-learning),
  body[data-filter="mature"] .e:not(.st-mature) {{ display:none; }}

  /* visibility toggles */
  body.no-furi ruby rt {{ visibility:hidden; }}
  body.no-mn .mn, body.no-mn .ex-en {{ background:var(--hair); color:transparent;
    border-radius:2px; user-select:none; }}
  body.no-mn .mn::selection, body.no-mn .ex-en::selection {{ background:transparent; }}
  body.no-pt .pt {{ display:none; }}

  .nores {{ display:none; padding:40px 0; color:var(--muted); font-family:var(--serif);
    font-size:1.05rem; }}
  .nores.show {{ display:block; }}

  footer {{ margin-top:52px; padding-top:16px; border-top:2px solid var(--ink);
    font-family:var(--mono); font-size:.7rem; color:var(--faint); letter-spacing:.03em; }}
  footer details {{ margin-top:10px; }}
  footer summary {{ cursor:pointer; color:var(--muted); }}
  footer a {{ color:var(--reed); }}
  footer textarea {{ width:100%; max-width:560px; height:80px; margin-top:8px;
    font:inherit; font-size:.66rem; background:var(--surface); color:var(--ink);
    border:1px solid var(--hair); border-radius:2px; padding:6px; }}
  footer .io-btns {{ display:flex; gap:6px; margin-top:6px; }}
  footer .io-btns button {{ font:inherit; font-size:.66rem; cursor:pointer;
    color:var(--muted); background:var(--surface); border:1px solid var(--hair);
    border-radius:2px; padding:3px 8px; }}

  /* review overlay */
  .rv[hidden] {{ display:none; }}
  .rv {{ position:fixed; inset:0; z-index:50; background:color-mix(in srgb,var(--paper) 88%,transparent);
    backdrop-filter:blur(3px); display:flex; align-items:center; justify-content:center; padding:20px; }}
  .rv-card {{ width:min(520px,100%); background:var(--surface); border:1px solid var(--hair);
    border-top:3px solid var(--seal); border-radius:3px; padding:22px 24px 20px;
    box-shadow:0 20px 60px color-mix(in srgb,var(--ink) 22%,transparent); }}
  .rv-top {{ display:flex; justify-content:space-between; align-items:center; gap:10px;
    font-family:var(--mono); font-size:.7rem; color:var(--muted); letter-spacing:.06em; }}
  .rv-top button {{ font:inherit; cursor:pointer; color:var(--muted);
    background:none; border:1px solid var(--hair); border-radius:2px; padding:3px 7px; }}
  .rv-top .rv-auto {{ display:flex; align-items:center; gap:4px; cursor:pointer;
    margin-left:auto; }}
  .rv-front {{ text-align:center; padding:34px 0 26px; font-size:2.6rem; font-weight:700; }}
  .rv-front .say {{ font-size:.5rem; width:26px; height:26px; vertical-align:middle;
    margin-left:12px; }}
  .rv-hint {{ text-align:center; font-family:var(--mono); font-size:.68rem;
    color:var(--faint); letter-spacing:.06em; padding-bottom:22px; }}
  .rv-back {{ padding:6px 0 18px; border-top:1px solid var(--hair); margin-top:4px; }}
  .rv-back[hidden] {{ display:none; }}
  .rv-back .kn, .rv-back .pip, .rv-back .e-tags {{ display:none; }}
  .rv-back .e-body {{ cursor:default; }}
  .rv-back .ex-grid {{ gap:9px; }}
  .rv-back .more {{ display:block; margin-top:8px; padding-top:8px; border-top:1px dotted var(--hair); }}
  .rv-actions {{ display:flex; gap:8px; }}
  .rv-actions button {{ flex:1; font:inherit; font-family:var(--mono); font-size:.8rem;
    cursor:pointer; padding:10px 4px; border-radius:2px; border:1px solid var(--hair);
    background:var(--surface); color:var(--ink); letter-spacing:.03em; }}
  .rv-actions button:focus-visible {{ outline:2px solid var(--reed); outline-offset:2px; }}
  .rv-actions .g-again {{ border-color:var(--seal); color:var(--seal); }}
  .rv-actions .g-good {{ border-color:var(--reed); color:var(--reed); }}
  .rv-actions .g-easy {{ color:var(--muted); }}
  .rv-actions .reveal {{ flex:1; border-color:var(--reed); color:var(--reed); font-weight:700; }}
  .rv-done {{ text-align:center; padding:30px 0; }}
  .rv-done p {{ font-family:var(--serif); font-size:1.1rem; margin:0 0 4px; }}
  .rv-done span {{ font-family:var(--mono); font-size:.72rem; color:var(--muted); }}

  /* ---- chrome stays at a fixed, readable size; the 4x bump is for the
     study cards (word / reading / meaning / 型 / the three sentences / note) --- */
  header h1 {{ font-size:clamp(26px,5vw,42px); }}
  .sub {{ font-size:16px; max-width:70ch; }}
  .dcount {{ font-size:13px; }}  .dcount b {{ font-size:15px; }}
  .tbar {{ font-size:13px; }}
  .tb-lab {{ font-size:1em; }}  .tb-n {{ font-size:1.15em; }}
  .day-no {{ font-size:22px; }}
  .day-meta, .day-tally, .day-audio {{ font-size:13px; }}
  footer {{ font-size:12px; }}
  footer textarea, footer .io-btns button {{ font-size:12px; }}
  .rv-card {{ width:min(760px,100%); }}
  .rv-top, .rv-top button {{ font-size:12px; }}
  .rv-hint {{ font-size:13px; }}
  .rv-actions button {{ font-size:15px; }}
  .rv-done p {{ font-size:17px; }}  .rv-done span {{ font-size:13px; }}

  @media (prefers-reduced-motion:no-preference) {{ html {{ scroll-behavior:smooth; }} }}
  @media print {{
    .controls, .pip, .e-cue, .e-due, .rv, .say, .draw, .so, .day-audio, .day-player {{ display:none; }}
    header {{ background-image:none; }}
    body {{ background:#fff; color:#000; }}
    .day, .e {{ break-inside:avoid; }}
    .e {{ padding-right:22px; }}
    .e.st-mature .w {{ color:#000; }}
  }}
</style>

<div class="wrap">
<header>
  <div class="inner">
    <h1>N3 語彙 <span class="lat">400 WORDS · 16 DAYS · JLPT N3 VOCABULARY</span></h1>
  </div>
  <p class="sub">Every word from the drip deck. Furigana in <span class="k-furi">blue</span>,
    the dictionary reading in <span class="k-seal">vermilion</span>, the English gloss, the
    collocation frame (型), and three example sentences &mdash; click a card with a
    <b>+</b> for its usage note. Each word carries a spaced-repetition box; <b>Review</b> walks what is due.
    Hit <b>&#9654;</b> on any card to hear the word, <b>&#9998;</b> for the animated stroke order;
    <b>&#9654; full track</b> in a day header streams that day's example-sentence recording from Drive.</p>
  <div class="dcount">EXAM 2026·12·03 &nbsp;<b>D&minus;{dleft}</b></div>
  <div class="tbar">{type_bar}</div>
</header>

<nav class="controls">
  <div class="search">
    <input id="q" type="search" placeholder="search word · reading · meaning · 型" autocomplete="off" spellcheck="false">
    <button class="x" id="q-x" type="button" aria-label="clear search" hidden>&times;</button>
  </div>
  <button class="review-btn" id="review-open">Review</button>
  <div class="stat" title="mature = box 5+ · due = ready to review">
    <span class="bar"><i id="stat-fill"></i></span>
    <span><b class="s-mat" id="s-mat">0</b> mature · <b id="s-lrn">0</b> learning ·
      <b id="s-new">{n}</b> new · <b class="s-due" id="s-due">0</b> due</span>
  </div>
  <select class="filter" id="filter" aria-label="filter words">
    <option value="all">all words</option>
    <option value="due">due now</option>
    <option value="new">new</option>
    <option value="learning">learning</option>
    <option value="mature">mature</option>
  </select>
  <div class="toggles">
    <button id="t-furi" aria-pressed="false">hide furigana</button>
    <button id="t-mn" aria-pressed="false">hide meanings</button>
    <button id="t-pt" aria-pressed="false">hide 型</button>
  </div>
  <div class="navdays">{nav}</div>
</nav>

<main>
<p class="nores" id="nores">No word matches that.</p>
{sections}
</main>

<footer>
  N3 語彙 400 &nbsp;·&nbsp; {n} entries &nbsp;·&nbsp; generated {TODAY.isoformat()}
  &nbsp;·&nbsp; <b>/</b> search &nbsp; <b>R</b> review &nbsp; <b>P</b> replay audio &nbsp; <b>W</b> stroke order &nbsp;
  click a card for its note, click the ring to promote a word, hit <b>&#9998;</b> for the animated
  stroke order &nbsp;·&nbsp; SRS boxes 0-6
  at 1·2·4·8·16·35-day steps, saved in this browser &nbsp;·&nbsp; word audio embedded;
  day tracks fetched live through your Google&nbsp;Drive connector &nbsp;·&nbsp;
  stroke order from <a href="https://kanjivg.tagaini.net" target="_blank" rel="noopener">KanjiVG</a>
  (CC&nbsp;BY-SA&nbsp;3.0).
  <details>
    <summary>back up / restore progress</summary>
    <textarea id="io" spellcheck="false" placeholder="your SRS progress as JSON"></textarea>
    <div class="io-btns">
      <button id="io-export">show current</button>
      <button id="io-import">restore from box</button>
      <button id="io-reset">reset all progress</button>
    </div>
  </details>
</footer>
</div>

<div class="rv" id="rv" hidden>
  <div class="rv-card" role="dialog" aria-modal="true" aria-label="review">
    <div class="rv-top"><span id="rv-count">0 / 0</span>
      <label class="rv-auto"><input type="checkbox" id="rv-auto"> &#9654; auto</label>
      <button id="rv-close" type="button">esc</button></div>
    <div id="rv-stage"></div>
  </div>
</div>

<div class="so" id="so" hidden>
  <div class="so-card" role="dialog" aria-modal="true" aria-label="stroke order">
    <div class="so-top">
      <span class="sw" id="so-w"></span><span class="srd" id="so-rd"></span>
      <button class="so-x" id="so-close" type="button">esc</button>
    </div>
    <div class="so-chars" id="so-chars"></div>
    <div class="so-actions">
      <button id="so-replay" type="button">&#8635; replay</button>
      <button id="so-play" type="button" aria-pressed="true">&#10073;&#10073; pause</button>
      <button id="so-back" type="button">&#8249; step</button>
      <button id="so-fwd" type="button">step &#8250;</button>
      <button id="so-speed" type="button">speed &times;1</button>
      <button id="so-all" type="button" aria-pressed="false">show all</button>
      <button id="so-num" type="button" aria-pressed="true">&#9839; numbers</button>
      <span class="so-hint" id="so-hint"></span>
    </div>
  </div>
</div>

<script>window.WA = {wa_json}; window.KVG = {kvg_json};</script>
<script>
(function () {{
  var body = document.body;
  var LS = {{
    get: function (k, d) {{ try {{ var v = JSON.parse(localStorage.getItem(k)); return v == null ? d : v; }} catch (e) {{ return d; }} }},
    set: function (k, v) {{ try {{ localStorage.setItem(k, JSON.stringify(v)); }} catch (e) {{}} }}
  }};

  /* ---- audio: per-word clips (embedded) + per-day tracks (Drive/mcp) --- */
  var WA = window.WA || {{}};
  var _wa = new Audio();
  var _wasrc = null;
  function playWord(w, btn) {{
    var b64 = WA[w];
    if (!b64) return false;
    try {{ _wa.pause(); }} catch (e) {{}}
    if (_wasrc) _wa.currentTime = 0;
    _wa.src = "data:audio/mpeg;base64," + b64;
    _wasrc = w;
    document.querySelectorAll(".say.playing").forEach(function (s) {{ s.classList.remove("playing"); }});
    if (btn) {{
      btn.classList.add("playing");
      _wa.onended = _wa.onpause = function () {{ btn.classList.remove("playing"); }};
    }}
    _wa.play().catch(function () {{ if (btn) btn.classList.remove("playing"); }});
    return true;
  }}
  document.addEventListener("click", function (ev) {{
    var s = ev.target.closest(".say");
    if (!s) return;
    ev.stopPropagation();           // don't trigger card-expand
    playWord(s.dataset.w, s);
  }});

  var _dayData = {{}};               // day -> "data:audio/mpeg;base64,..." (session cache)
  var _mcpP = null;
  function getMcp() {{
    if (_mcpP) return _mcpP;
    _mcpP = (window.claude && window.claude.use)
      ? window.claude.use("mcp").catch(function () {{ return null; }})
      : Promise.resolve(null);
    return _mcpP;
  }}
  function driveLink(fid) {{
    return "https://drive.google.com/file/d/" + fid + "/view";
  }}
  function mountAudio(box, url) {{
    box.innerHTML = "";
    var a = document.createElement("audio");
    a.controls = true; a.preload = "metadata"; a.src = url; a.autoplay = true;
    box.appendChild(a);
  }}
  function fallback(box, fid, code) {{
    box.innerHTML = '<a class="dp-link" href="' + driveLink(fid) +
      '" target="_blank" rel="noopener">open this track in Google Drive &#8599;</a>' +
      (code ? ' <span class="dp-err">(' + code + ')</span>' : '');
  }}
  function pickB64(res) {{
    var p = res && res.payload;
    if (p && typeof p === "object" && (p.content || p.data)) return p.content || p.data;
    var blocks = res && res.content;
    if (Array.isArray(blocks)) {{
      for (var i = 0; i < blocks.length; i++) {{
        var t = blocks[i] && blocks[i].text;
        if (typeof t === "string") {{
          try {{ var o = JSON.parse(t); if (o && (o.content || o.data)) return o.content || o.data; }}
          catch (e) {{}}
        }}
      }}
    }}
    return null;
  }}
  async function loadDayTrack(day, fid, box) {{
    if (_dayData[day]) {{ mountAudio(box, _dayData[day]); return; }}
    box.innerHTML = '<span class="dp-load">fetching track from Drive&hellip;</span>';
    var mcp = await getMcp();
    if (!mcp) {{ fallback(box, fid); return; }}
    try {{
      var res = await mcp.callTool("{DRIVE_CONNECTOR}", "download_file_content",
        {{ fileId: fid }}, {{ cache: {{ staleTime: 300000, gcTime: 86400000 }} }});
      var b64 = pickB64(res);
      if (!b64) throw {{ code: "empty_response" }};
      _dayData[day] = "data:audio/mpeg;base64," + b64;
      mountAudio(box, _dayData[day]);
    }} catch (err) {{
      fallback(box, fid, err && err.code);
    }}
  }}
  document.addEventListener("click", function (ev) {{
    var b = ev.target.closest(".day-audio");
    if (!b) return;
    var box = document.getElementById("dp" + b.dataset.day);
    var open = b.getAttribute("aria-expanded") === "true";
    if (open) {{ box.hidden = true; b.setAttribute("aria-expanded", "false"); return; }}
    b.setAttribute("aria-expanded", "true"); box.hidden = false;
    loadDayTrack(b.dataset.day, b.dataset.fid, box);
  }});

  var entries = Array.prototype.slice.call(document.querySelectorAll(".e"));
  var days = Array.prototype.slice.call(document.querySelectorAll(".day"));
  var byWord = {{}};
  entries.forEach(function (e) {{ byWord[e.dataset.w] = e; }});
  var TOTAL = entries.length;

  /* ---- SRS core ----------------------------------------------------- */
  var BOX_DAYS = [0, 1, 2, 4, 8, 16, 35];      // days until next due, per box
  var MATURE = 5;
  var NEW_PER_SESSION = 15;
  var srs = LS.get("n3srs", {{}});             // word -> box b, due date, reps, lapses

  function todayStr(offset) {{
    var d = new Date(); d.setHours(0, 0, 0, 0);
    if (offset) d.setDate(d.getDate() + offset);
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }}
  var TODAY = todayStr(0);

  function isDue(w) {{
    var s = srs[w];
    return !!s && (s.b === 0 || s.due <= TODAY);
  }}
  function grade(w, g) {{                       // g: "again" | "good" | "easy"
    var s = srs[w] || {{ b: 0, reps: 0, lapses: 0 }};
    if (g === "again") {{ if (s.b > 0) s.lapses++; s.b = 0; s.due = TODAY; }}
    else {{
      s.b = Math.min(6, s.b + (g === "easy" ? 2 : 1));
      if (g === "easy" && s.b < 3) s.b = 3;
      s.due = todayStr(BOX_DAYS[s.b]);
    }}
    s.reps++;
    srs[w] = s;
    LS.set("n3srs", srs);
    paint();
  }}

  function paint() {{
    var mat = 0, lrn = 0, due = 0;
    entries.forEach(function (e) {{
      var s = srs[e.dataset.w];
      var st = !s ? "new" : (s.b >= MATURE ? "mature" : "learning");
      e.classList.toggle("st-new", st === "new");
      e.classList.toggle("st-learning", st === "learning");
      e.classList.toggle("st-mature", st === "mature");
      var d = isDue(e.dataset.w);
      e.classList.toggle("is-due", d);
      if (st === "mature") mat++; else if (st === "learning") lrn++;
      if (d) due++;
      e.querySelector(".pip").title = s
        ? ("box " + s.b + (d ? " · due" : " · due " + s.due)) : "new - click to mark seen";
    }});
    set("s-mat", mat); set("s-lrn", lrn); set("s-new", TOTAL - mat - lrn); set("s-due", due);
    document.getElementById("stat-fill").style.width = (mat / TOTAL * 100) + "%";
    var q = queueSize();
    var btn = document.getElementById("review-open");
    btn.disabled = q === 0;
    btn.textContent = q ? "Review " + q : "Review — clear";
    days.forEach(function (dy) {{
      var es = dy.querySelectorAll(".e");
      var m = dy.querySelectorAll(".e.st-mature").length;
      dy.querySelector(".day-tally").textContent = m ? m + "/" + es.length : "";
    }});
    refreshDays();
  }}
  function set(id, v) {{ document.getElementById(id).textContent = v; }}
  function refreshDays() {{
    var f = document.getElementById("filter").value;
    var anyVisible = false;
    days.forEach(function (d) {{
      var vis = Array.prototype.some.call(d.querySelectorAll(".e"), function (e) {{
        if (e.classList.contains("hit")) return false;
        if (f === "all") return true;
        return e.classList.contains(f === "due" ? "is-due" : "st-" + f);
      }});
      d.classList.toggle("empty", !vis);
      if (vis) anyVisible = true;
    }});
    var searching = !!document.getElementById("q").value.trim();
    if (!anyVisible && !searching && f !== "all") {{
      nores.textContent = f === "due" ? "Nothing due right now — come back later or learn new words in Review."
        : "No " + f + " words yet.";
      nores.classList.add("show");
    }} else if (!searching) {{
      nores.classList.remove("show");
    }}
  }}

  /* ---- review session --------------------------------------------- */
  function buildQueue() {{
    var dueList = [], newList = [];
    entries.forEach(function (e) {{
      var w = e.dataset.w;
      if (isDue(w)) dueList.push(w);
      else if (!srs[w]) newList.push(w);
    }});
    shuffle(dueList);
    return dueList.concat(newList.slice(0, NEW_PER_SESSION));
  }}
  function queueSize() {{ return buildQueue().length; }}
  function shuffle(a) {{
    for (var i = a.length - 1; i > 0; i--) {{
      var j = Math.floor(Math.random() * (i + 1)); var t = a[i]; a[i] = a[j]; a[j] = t;
    }}
    return a;
  }}

  var rv = document.getElementById("rv");
  var stage = document.getElementById("rv-stage");
  var rvCount = document.getElementById("rv-count");
  var rvAuto = document.getElementById("rv-auto");
  var queue = [], qi = 0, done = 0, revealed = false;

  function openReview() {{
    queue = buildQueue(); qi = 0; done = 0;
    if (!queue.length) return;
    rv.hidden = false;
    document.getElementById("rv-close").focus();
    showCard();
  }}
  function closeReview() {{ rv.hidden = true; paint(); }}

  function showCard() {{
    revealed = false;
    if (qi >= queue.length) {{
      rvCount.textContent = done + " reviewed";
      var left = buildQueue().length;
      stage.innerHTML = '<div class="rv-done"><p>キリがいい。 Session done.</p>' +
        '<span>' + done + ' reviewed' + (left ? ' · ' + left + ' still in the queue' : ' · queue clear') +
        '</span></div>' +
        '<div class="rv-actions">' +
        (left ? '<button class="g-good" id="rv-more">keep going</button>' : '') +
        '<button class="reveal" id="rv-again-close">done</button></div>';
      var more = document.getElementById("rv-more");
      if (more) more.onclick = function () {{ openReview(); }};
      document.getElementById("rv-again-close").onclick = closeReview;
      document.getElementById("rv-again-close").focus();
      return;
    }}
    var w = queue[qi];
    var e = byWord[w];
    rvCount.textContent = (qi + 1) + " / " + queue.length;
    stage.innerHTML = "";
    var front = document.createElement("div");
    front.className = "rv-front";
    front.textContent = w;
    if (WA[w]) {{
      var sb = document.createElement("button");
      sb.className = "say"; sb.type = "button"; sb.dataset.w = w;
      sb.setAttribute("aria-label", "pronounce " + w); sb.title = "say it";
      sb.innerHTML = "&#9654;";
      front.appendChild(sb);
      if (rvAuto && rvAuto.checked) setTimeout(function () {{ playWord(w, sb); }}, 120);
    }}
    var hint = document.createElement("div");
    hint.className = "rv-hint";
    hint.textContent = srs[w] ? ("box " + srs[w].b) : "new word";
    var back = document.createElement("div");
    back.className = "rv-back"; back.hidden = true;
    back.appendChild(e.querySelector(".e-body").cloneNode(true));
    var actions = document.createElement("div");
    actions.className = "rv-actions";
    actions.innerHTML = '<button class="reveal" data-a="reveal">reveal &nbsp;·&nbsp; space</button>';
    stage.append(front, hint, back, actions);
    actions.querySelector("[data-a=reveal]").focus();
    actions.addEventListener("click", onAction);
  }}
  function reveal() {{
    if (revealed) return;
    var bk = stage.querySelector(".rv-back");
    if (!bk) return;
    revealed = true;
    bk.hidden = false;
    var h = stage.querySelector(".rv-hint"); if (h) h.remove();
    var a = stage.querySelector(".rv-actions");
    a.innerHTML =
      '<button class="g-again" data-a="again">Again <small>1</small></button>' +
      '<button class="g-good" data-a="good">Good <small>2</small></button>' +
      '<button class="g-easy" data-a="easy">Easy <small>3</small></button>';
    a.querySelector("[data-a=good]").focus();
  }}
  function onAction(ev) {{
    var b = ev.target.closest("button"); if (!b) return;
    var a = b.dataset.a;
    if (a === "reveal") {{ reveal(); return; }}
    var w = queue[qi];
    grade(w, a);
    done++;
    if (a === "again") queue.splice(Math.min(queue.length, qi + 3), 0, w);
    qi++;
    showCard();
  }}

  document.getElementById("review-open").addEventListener("click", openReview);
  document.getElementById("rv-close").addEventListener("click", closeReview);
  rv.addEventListener("click", function (ev) {{ if (ev.target === rv) closeReview(); }});
  document.addEventListener("keydown", function (ev) {{
    if (!so.hidden) {{
      if (ev.key === "Escape") soClose();
      else if (ev.key === " ") {{ ev.preventDefault(); soTogglePlay(); }}
      else if (ev.key === "r" || ev.key === "R") soReplay();
      else if (ev.key === "ArrowRight") {{ ev.preventDefault(); soStep(1); }}
      else if (ev.key === "ArrowLeft") {{ ev.preventDefault(); soStep(-1); }}
      else if (ev.key === "a" || ev.key === "A") document.getElementById("so-all").click();
      else if (ev.key === "n" || ev.key === "N") document.getElementById("so-num").click();
      return;
    }}
    if (!rv.hidden) {{
      if (ev.key === "Escape") closeReview();
      else if (ev.key === " ") {{ ev.preventDefault(); reveal(); }}
      else if (ev.key === "p" || ev.key === "P") {{ if (queue[qi]) playWord(queue[qi]); }}
      else if (revealed && "123".indexOf(ev.key) !== -1) {{
        ["again", "good", "easy"][+ev.key - 1] && grade0(["again", "good", "easy"][+ev.key - 1]);
      }}
      return;
    }}
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {{
      if (ev.key === "Escape" && document.activeElement === q) {{ q.value = ""; runSearch(); q.blur(); }}
      return;
    }}
    if (ev.key === "/") {{ ev.preventDefault(); q.focus(); }}
    else if (ev.key === "r" || ev.key === "R") {{ openReview(); }}
    else if (ev.key === "p" || ev.key === "P") {{
      var oc = document.querySelector(".e.open");
      if (oc) playWord(oc.dataset.w, oc.querySelector(".say"));
    }}
    else if (ev.key === "w" || ev.key === "W") {{
      var dc = document.querySelector(".e.open") || entries.find(function (e) {{ return !e.classList.contains("hit"); }});
      if (dc) soOpen(dc.dataset.w, dc.querySelector(".draw"));
    }}
  }});
  function grade0(a) {{ var w = queue[qi]; grade(w, a); done++;
    if (a === "again") queue.splice(Math.min(queue.length, qi + 3), 0, w); qi++; showCard(); }}

  /* ---- browse: pip = quick promote, click card = expand ------------ */
  entries.forEach(function (e) {{
    e.querySelector(".pip").addEventListener("click", function (ev) {{
      ev.stopPropagation();
      grade(e.dataset.w, "good");
    }});
    e.addEventListener("click", function (ev) {{
      if (ev.target.closest(".pip") || ev.target.closest(".say") || ev.target.closest(".draw")) return;
      if (!e.classList.contains("has-note")) return;
      if (!window.getSelection().isCollapsed) return;
      e.classList.toggle("open");
    }});
  }});

  /* ---- search ----------------------------------------------------- */
  var q = document.getElementById("q");
  var qx = document.getElementById("q-x");
  var nores = document.getElementById("nores");
  function runSearch() {{
    var t = q.value.trim().toLowerCase();
    qx.hidden = !t;
    var shown = 0;
    entries.forEach(function (e) {{
      var hit = !t || e.dataset.s.indexOf(t) !== -1;
      e.classList.toggle("hit", !hit);
      if (hit) shown++;
    }});
    refreshDays();
    nores.classList.toggle("show", !!t && shown === 0);
  }}
  q.addEventListener("input", runSearch);
  qx.addEventListener("click", function () {{ q.value = ""; runSearch(); q.focus(); }});

  /* ---- filter + visibility toggles ------------------------------------ */
  var pref = LS.get("n3ref", {{}});
  if (rvAuto) {{
    rvAuto.checked = pref.rvAuto !== false && !!Object.keys(WA).length;
    rvAuto.disabled = !Object.keys(WA).length;
    rvAuto.addEventListener("change", function () {{
      pref.rvAuto = rvAuto.checked; LS.set("n3ref", pref);
    }});
  }}
  var filter = document.getElementById("filter");
  filter.value = pref.filter || "all";
  body.dataset.filter = filter.value;
  filter.addEventListener("change", function () {{
    body.dataset.filter = filter.value; pref.filter = filter.value; LS.set("n3ref", pref);
    refreshDays();
  }});

  var defs = {{ "t-furi": "no-furi", "t-mn": "no-mn", "t-pt": "no-pt" }};
  Object.keys(defs).forEach(function (id) {{
    var btn = document.getElementById(id);
    setToggle(btn, !!pref[id]);
    btn.addEventListener("click", function () {{
      setToggle(btn, btn.getAttribute("aria-pressed") !== "true");
      Object.keys(defs).forEach(function (i) {{
        pref[i] = document.getElementById(i).getAttribute("aria-pressed") === "true";
      }});
      LS.set("n3ref", pref);
    }});
  }});
  function setToggle(btn, on) {{
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    body.classList.toggle(defs[btn.id], on);
  }}

  /* ---- backup / restore --------------------------------------------- */
  var io = document.getElementById("io");
  document.getElementById("io-export").onclick = function () {{ io.value = JSON.stringify(srs); }};
  document.getElementById("io-import").onclick = function () {{
    try {{
      var obj = JSON.parse(io.value);
      if (obj && typeof obj === "object") {{
        srs = obj; LS.set("n3srs", srs); paint();
        io.value = "restored " + Object.keys(srs).length + " words";
      }}
    }} catch (e) {{ io.value = "could not parse that JSON"; }}
  }};
  document.getElementById("io-reset").onclick = function () {{
    if (confirm("Clear all spaced-repetition progress on this device?")) {{
      srs = {{}}; LS.set("n3srs", srs); paint(); io.value = "";
    }}
  }};

  /* ---- stroke-order animation (embedded KanjiVG paths) ------------- */
  var KVG = window.KVG || {{}};
  var haveKvg = !!Object.keys(KVG).length;
  var SVGNS = "http://www.w3.org/2000/svg";
  var so = document.getElementById("so");
  var soChars = document.getElementById("so-chars");
  var soCard = document.querySelector(".so-card");
  var soHint = document.getElementById("so-hint");
  var soPlayBtn = document.getElementById("so-play");
  var soAllBtn = document.getElementById("so-all");
  var soNumBtn = document.getElementById("so-num");

  var soRet = null;
  var soTracks = [];        // per char: {{ paths:[el], nums:[el], lens:[num], count }}
  var soK = 0;              // strokes revealed so far (0..soMax)
  var soMax = 0;
  var soResumeK = 0;
  var soTimer = null;
  var soPlaying = false;
  var curDur = 500;
  var SPEEDS = [0.5, 1, 1.5, 2];
  var soSpeed = 1;

  function svgEl(name, attrs) {{
    var el = document.createElementNS(SVGNS, name);
    for (var k in attrs) el.setAttribute(k, attrs[k]);
    return el;
  }}

  function buildTrack(ch) {{
    var wrap = document.createElement("div");
    wrap.className = "so-char";
    var d = KVG[ch];
    if (!d || !d.s || !d.s.length) {{
      wrap.className = "so-char nodata";
      wrap.textContent = ch;
      var note = document.createElement("span");
      note.textContent = "no stroke data";
      wrap.appendChild(note);
      soChars.appendChild(wrap);
      return null;
    }}
    var svg = svgEl("svg", {{ viewBox: "0 0 109 109" }});
    var ghost = svgEl("g", {{ "class": "ghost" }});
    var ink = svgEl("g", {{ "class": "ink" }});
    var nums = svgEl("g", {{ "class": "nums" }});
    var paths = [];
    d.s.forEach(function (dd) {{
      ghost.appendChild(svgEl("path", {{ d: dd }}));
      var p = svgEl("path", {{ d: dd }});
      ink.appendChild(p);
      paths.push(p);
    }});
    (d.n || []).forEach(function (xy, i) {{
      var t = svgEl("text", {{ x: xy[0], y: xy[1] }});
      t.textContent = String(i + 1);
      t.style.opacity = "0";
      nums.appendChild(t);
    }});
    svg.appendChild(ghost); svg.appendChild(ink); svg.appendChild(nums);
    wrap.appendChild(svg);
    var cap = document.createElement("div");
    cap.className = "cap";
    cap.textContent = d.s.length + (d.s.length === 1 ? " stroke" : " strokes");
    wrap.appendChild(cap);
    soChars.appendChild(wrap);
    return {{ paths: paths, nums: nums.children, count: d.s.length, lens: [] }};
  }}

  function soMeasure() {{
    soTracks.forEach(function (tr) {{
      tr.lens = tr.paths.map(function (p) {{
        var L = 100;
        try {{ L = p.getTotalLength() || 100; }} catch (e) {{}}
        p.style.strokeDasharray = L + " " + (L + 1);
        p.style.strokeDashoffset = String(L);
        return L;
      }});
    }});
  }}

  function computeDur(idx) {{             // idx = 0-based stroke about to draw
    var L = 0;
    soTracks.forEach(function (tr) {{ if (idx < tr.count) L = Math.max(L, tr.lens[idx] || 0); }});
    return Math.max(240, Math.min(1300, L * 7 / soSpeed));
  }}

  function showStroke(i, tr, animate) {{
    var p = tr.paths[i];
    if (!p) return;
    if (animate) {{
      p.style.transition = "none";
      p.style.strokeDashoffset = String(tr.lens[i]);
      p.getBoundingClientRect();          // reflow so the transition runs from full
      p.style.transition = "stroke-dashoffset " + curDur + "ms linear";
      p.style.strokeDashoffset = "0";
      p.classList.add("lead");
      setTimeout(function () {{ p.classList.remove("lead"); }}, curDur + 40);
    }} else {{
      p.style.transition = "none";
      p.style.strokeDashoffset = "0";
      p.classList.remove("lead");
    }}
    if (tr.nums[i]) tr.nums[i].style.opacity = "1";
  }}
  function hideStroke(i, tr) {{
    var p = tr.paths[i];
    if (!p) return;
    p.style.transition = "none";
    p.style.strokeDashoffset = String(tr.lens[i]);
    p.classList.remove("lead");
    if (tr.nums[i]) tr.nums[i].style.opacity = "0";
  }}

  function updateHint() {{
    soHint.innerHTML = "<b>" + soK + "</b> / " + soMax + (soK >= soMax && soMax ? " &#10003;" : "");
  }}
  function seek(k, animate) {{
    k = Math.max(0, Math.min(soMax, k));
    var prev = soK;
    soK = k;
    soTracks.forEach(function (tr) {{
      for (var i = 0; i < tr.count; i++) {{
        if (i < k) showStroke(i, tr, animate && i >= prev && i === k - 1);
        else hideStroke(i, tr);
      }}
    }});
    updateHint();
  }}

  function soTick() {{
    if (soK >= soMax) {{ soSetPlaying(false); return; }}
    curDur = computeDur(soK);
    seek(soK + 1, true);
    soTimer = setTimeout(soTick, curDur + 180 / soSpeed);
  }}
  function soSetPlaying(on) {{
    soPlaying = on;
    clearTimeout(soTimer); soTimer = null;
    soPlayBtn.setAttribute("aria-pressed", on ? "true" : "false");
    soPlayBtn.innerHTML = on ? "&#10073;&#10073; pause" : "&#9654; play";
    if (on) {{
      if (soAllBtn.getAttribute("aria-pressed") === "true") soAllBtn.click();
      if (soK >= soMax) seek(0, false);
      soTick();
    }}
  }}
  function soTogglePlay() {{ soSetPlaying(!soPlaying); }}
  function soReplay() {{ clearTimeout(soTimer); seek(0, false); soSetPlaying(true); }}
  function soStep(dir) {{
    soSetPlaying(false);
    if (soAllBtn.getAttribute("aria-pressed") === "true") return;
    if (dir > 0) {{ curDur = Math.min(computeDur(soK), 480); seek(soK + 1, true); }}
    else seek(soK - 1, false);
  }}

  function soOpen(w, ret) {{
    if (!haveKvg) return;
    soRet = ret || null;
    clearTimeout(soTimer); soTimer = null; soPlaying = false;
    document.getElementById("so-w").textContent = w;
    var e = byWord[w];
    document.getElementById("so-rd").textContent = e ? e.querySelector(".rd").textContent : "";
    soChars.innerHTML = "";
    soTracks = [];
    Array.from(w).forEach(function (ch) {{
      var tr = buildTrack(ch);
      if (tr) soTracks.push(tr);
    }});
    soMax = soTracks.reduce(function (m, tr) {{ return Math.max(m, tr.count); }}, 0);
    soResumeK = 0;
    soAllBtn.setAttribute("aria-pressed", "false");
    so.hidden = false;
    document.documentElement.classList.add("so-lock");
    document.body.classList.add("so-lock");
    requestAnimationFrame(function () {{
      soMeasure();
      seek(0, false);
      if (soMax) soSetPlaying(true); else updateHint();
    }});
    document.getElementById("so-close").focus();
  }}
  function soClose() {{
    so.hidden = true;
    clearTimeout(soTimer); soTimer = null; soPlaying = false;
    document.documentElement.classList.remove("so-lock");
    document.body.classList.remove("so-lock");
    if (soRet && soRet.focus) soRet.focus();
  }}

  document.getElementById("so-close").addEventListener("click", soClose);
  document.getElementById("so-replay").addEventListener("click", soReplay);
  document.getElementById("so-play").addEventListener("click", soTogglePlay);
  document.getElementById("so-fwd").addEventListener("click", function () {{ soStep(1); }});
  document.getElementById("so-back").addEventListener("click", function () {{ soStep(-1); }});
  document.getElementById("so-speed").addEventListener("click", function () {{
    soSpeed = SPEEDS[(SPEEDS.indexOf(soSpeed) + 1) % SPEEDS.length];
    this.innerHTML = "speed &times;" + soSpeed;
  }});
  soAllBtn.addEventListener("click", function () {{
    var on = this.getAttribute("aria-pressed") !== "true";
    this.setAttribute("aria-pressed", on ? "true" : "false");
    if (on) {{ soResumeK = soK; soSetPlaying(false); seek(soMax, false); }}
    else {{ seek(soResumeK, false); }}
  }});
  function soApplyNums(on) {{
    soNumBtn.setAttribute("aria-pressed", on ? "true" : "false");
    soCard.classList.toggle("no-nums", !on);
  }}
  soApplyNums(pref.soNums !== false);
  soNumBtn.addEventListener("click", function () {{
    var on = soCard.classList.contains("no-nums");
    soApplyNums(on); pref.soNums = on; LS.set("n3ref", pref);
  }});
  so.addEventListener("click", function (ev) {{ if (ev.target === so) soClose(); }});

  document.addEventListener("click", function (ev) {{
    var d = ev.target.closest(".draw");
    if (!d) return;
    ev.stopPropagation();
    soOpen(d.dataset.w, d);
  }});

  if (!haveKvg) {{
    Array.prototype.forEach.call(document.querySelectorAll(".draw"), function (b) {{
      b.disabled = true; b.title = "stroke data not built";
    }});
  }}

  paint();
}})();
</script>
"""


if __name__ == "__main__":
    OUT.write_text(build(load()), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
