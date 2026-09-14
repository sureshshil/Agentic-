#!/usr/bin/env python3
"""
Build n3_kanji_reference.html from 6 N3 kanji CSV batches.
"""

import csv
import json
import re
import os
from pathlib import Path

# ── paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
ROOT = SCRIPT_DIR.parent          # simple_agent/
OUT = ROOT / "vocab_artifacts" / "n3_kanji_reference.html"
KVG_URL_FILE = ROOT / "vocab_artifacts" / "vocab_kvg_url.json"
CSV_FILES = sorted(ROOT.glob("N3_kanji_batch*.csv"))

# ── helpers ────────────────────────────────────────────────────────────────

def parse_furigana_ruby(text: str) -> str:
    """Convert 漢字[reading] → <ruby>漢字<rt>reading</rt></ruby>."""
    def replacer(m):
        kanji = m.group(1)
        reading = m.group(2)
        return f"<ruby>{_esc(kanji)}<rt>{_esc(reading)}</rt></ruby>"
    # Split on spaces first to avoid matching across word boundaries incorrectly
    return re.sub(r'([^\s\[]+?)\[([^\]]+)\]', replacer, text)


def _esc(s: str) -> str:
    """HTML-escape a string."""
    return (s
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _plain(text: str) -> str:
    """Strip furigana brackets to get plain text."""
    return re.sub(r'\[[^\]]*\]', '', text)


def parse_words(words_field: str) -> list:
    """
    Parse the words field split by <br>.
    Each entry: "word[reading] — meaning_en — meaning_ne"
    Returns list of {w, r, m, mn}.
    """
    result = []
    if not words_field.strip():
        return result
    for entry in words_field.split("<br>"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(" — ")
        if len(parts) < 2:
            continue
        word_part = parts[0].strip()
        meaning_en = parts[1].strip() if len(parts) > 1 else ""
        meaning_ne = parts[2].strip() if len(parts) > 2 else ""
        # Extract reading from brackets: 決[き]める → surface=決める, reading=き
        # Build readable surface (strip brackets) and collect readings
        surface = _plain(word_part)
        # Extract readings in order
        readings = re.findall(r'\[([^\]]+)\]', word_part)
        reading_str = "·".join(readings) if readings else ""
        result.append({"w": surface, "r": reading_str, "m": meaning_en, "mn": meaning_ne})
    return result


def extract_day(tags: str) -> int:
    """Extract day number from tags like 'kanji_day1' → 1."""
    m = re.search(r'kanji_day(\d+)', tags)
    return int(m.group(1)) if m else 0


def read_kanji_rows() -> list:
    """Read all CSV files and return list of row dicts."""
    rows = []
    for csv_path in CSV_FILES:
        with open(csv_path, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
    return rows


def build_kanji_data(rows: list) -> list:
    """Transform CSV rows into compact JSON-friendly objects."""
    data = []
    for row in rows:
        kanji = row["kanji"].strip()
        day = extract_day(row.get("tags", ""))
        words = parse_words(row.get("words", ""))
        disc = {}
        if row.get("disc_front", "").strip():
            opts = [o.strip() for o in row.get("disc_options", "").split("/") if o.strip()]
            disc = {
                "f": row["disc_front"].strip(),
                "o": opts,
                "a": row.get("disc_answer", "").strip(),
                "n": row.get("disc_note", "").strip(),
            }
        data.append({
            "k": kanji,
            "m": row.get("meaning_en", "").strip(),
            "mn": row.get("meaning_ne", "").strip(),
            "jlpt": row.get("jlpt", "N3").strip(),
            "on": row.get("onyomi", "").strip(),
            "kun": row.get("kunyomi", "").strip(),
            "w": words,
            "ex": row.get("example", "").strip(),
            "ex_en": row.get("example_en", "").strip(),
            "comp": row.get("component", "").strip(),
            "cf": row.get("confusable", "").strip(),
            "dq": disc,
            "day": day,
            "tags": row.get("tags", "").strip(),
        })
    # Sort by day then original order
    data.sort(key=lambda x: x["day"])
    return data


def render_words_html(words: list) -> str:
    if not words:
        return ""
    parts = []
    for w in words:
        surface = _esc(w["w"])
        reading = _esc(w["r"])
        m_en = _esc(w["m"])
        m_ne = _esc(w["mn"])
        parts.append(
            f'<span class="cw-surface">{surface}</span>'
            f'<span class="cw-rd">({reading})</span>'
            f'<span class="cw-sep"> — </span>'
            f'<span class="cw-m">{m_en}</span>'
            + (f'<span class="cw-mn"> — {m_ne}</span>' if m_ne else "")
        )
    items = "".join(f'<li class="cw-item">{p}</li>' for p in parts)
    return f'<ul class="cw-list">{items}</ul>'


def render_example_html(ex: str, ex_en: str) -> str:
    if not ex:
        return ""
    ruby_ex = parse_furigana_ruby(ex)
    en_part = f'<span class="ex-en">{_esc(ex_en)}</span>' if ex_en else ""
    return (
        f'<div class="kex">'
        f'<span class="ex-jp">{ruby_ex}</span>'
        f'{en_part}'
        f'</div>'
    )


def render_disc_html(dq: dict) -> str:
    if not dq or not dq.get("f"):
        return ""
    opts_html = "".join(
        f'<button class="dq-opt" type="button" data-ans="{_esc(dq["a"])}">{_esc(o)}</button>'
        for o in dq.get("o", [])
    )
    note = f'<p class="dq-note" hidden>{_esc(dq["n"])}</p>' if dq.get("n") else ""
    return (
        f'<div class="dq">'
        f'<p class="dq-front">{_esc(dq["f"])}</p>'
        f'<div class="dq-opts">{opts_html}</div>'
        f'{note}'
        f'</div>'
    )


def render_card(r: dict) -> str:
    kanji = r["k"]
    kanji_esc = _esc(kanji)
    on_label = f'<span class="rd-label">オン</span><span class="rd-on">{_esc(r["on"])}</span>' if r["on"] else ""
    kun_label = f'<span class="rd-label kun-label">くん</span><span class="rd-kun">{_esc(r["kun"])}</span>' if r["kun"] else ""
    reading_row = f'<div class="k-rd">{on_label}{kun_label}</div>' if (r["on"] or r["kun"]) else ""

    jlpt_badge = f'<span class="jlpt-badge">{_esc(r["jlpt"])}</span>'
    meaning_en = f'<p class="mn">{_esc(r["m"])}</p>' if r["m"] else ""
    meaning_ne = f'<p class="mn-ne">{_esc(r["mn"])}</p>' if r["mn"] else ""

    words_html = render_words_html(r["w"])
    example_html = render_example_html(r["ex"], r["ex_en"])

    more_parts = []
    if r["comp"]:
        more_parts.append(f'<p class="comp"><span class="nt-label">component</span> {_esc(r["comp"])}</p>')
    if r["cf"]:
        more_parts.append(f'<p class="conf"><span class="nt-label">confusable</span> {_esc(r["cf"])}</p>')
    disc_html = render_disc_html(r["dq"])
    if disc_html:
        more_parts.append(disc_html)
    has_more = bool(more_parts)
    more_html = f'<div class="more">{"".join(more_parts)}</div>' if has_more else ""
    cue = '<span class="e-cue">+</span>' if has_more else ""

    # search index: kanji + on + kun + meaning_en + words surface text
    words_search = " ".join(w["w"] for w in r["w"])
    search_str = " ".join([
        kanji,
        r["on"].lower(),
        _plain(r["kun"]).lower(),
        r["m"].lower(),
        words_search.lower(),
    ])

    return (
        f'<article class="e{" has-note" if has_more else ""}" '
        f'data-w="{kanji_esc}" '
        f'data-on="{_esc(r["on"])}" '
        f'data-kun="{_esc(_plain(r["kun"]))}" '
        f'data-s="{_esc(search_str)}">'
        f'<button class="pip" type="button" aria-label="advance" title="advance in SRS"></button>'
        f'<div class="e-body">'
        f'<div class="e-head">'
        f'<span class="w k-char">{kanji_esc}</span>'
        f'<button class="draw" type="button" data-w="{kanji_esc}" '
        f'  aria-label="stroke order for {kanji_esc}" title="stroke order">&#9998;</button>'
        f'{jlpt_badge}'
        f'</div>'
        f'{reading_row}'
        f'{meaning_en}'
        f'{meaning_ne}'
        f'{words_html}'
        f'{example_html}'
        f'{more_html}'
        f'</div>'
        f'<span class="e-tags" aria-hidden="true">'
        f'<span class="e-due">due</span>{cue}'
        f'</span>'
        f'</article>'
    )



def build_html(kanji_data: list, kvg_url: str) -> str:
    # Group by day
    by_day: dict = {}
    day_order = []
    for r in kanji_data:
        d = r["day"]
        if d not in by_day:
            by_day[d] = []
            day_order.append(d)
        by_day[d].append(r)

    all_days = sorted(day_order)

    # Nav links
    nav_links = "".join(
        f'<a href="#day{str(d).zfill(2)}">{str(d).zfill(2)}</a>'
        for d in all_days
    )

    # Day sections
    day_sections = []
    for d in all_days:
        items = by_day[d]
        dd = str(d).zfill(2)
        cards = "".join(render_card(r) for r in items)
        day_sections.append(
            f'<section class="day" id="day{dd}" data-day="{d}">'
            f'<div class="day-head">'
            f'<span class="day-no">DAY {dd}</span>'
            f'<span class="day-meta">{len(items)} kanji</span>'
            f'<span class="day-tally"></span>'
            f'</div>'
            f'<div class="grid">{cards}</div>'
            f'</section>'
        )

    main_content = "\n".join(day_sections)
    kanji_json = json.dumps(kanji_data, ensure_ascii=False, separators=(",", ":"))
    total = len(kanji_data)

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>N3 漢字 {total}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Shippori+Mincho:wght@500;600;700&family=Newsreader:ital,wght@0,400;0,500;1,400&family=JetBrains+Mono:wght@400;500;700&display=swap">
<style>:root {{
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
  html {{ font-size:18px; }}
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
  h1 {{ font-weight:700; font-size:clamp(26px,5vw,42px); margin:0;
    letter-spacing:.01em; text-wrap:balance; }}
  h1 .lat {{ font-family:var(--mono); font-weight:500; font-size:.42em;
    letter-spacing:.16em; color:var(--muted); display:block; margin-top:.5em; }}
  .sub {{ margin:14px 0 0; color:var(--muted); font-family:var(--serif);
    font-size:16px; max-width:70ch; }}
  .k-furi {{ color:var(--furi); font-weight:500; }}
  .k-seal {{ color:var(--seal); }}
  .dcount {{ display:inline-flex; align-items:baseline; gap:.5em; margin-top:18px;
    font-family:var(--mono); font-size:13px; letter-spacing:.06em;
    color:var(--seal); border:1px solid var(--seal); border-radius:2px; padding:5px 10px; }}
  .dcount b {{ font-weight:700; font-size:15px; }}

  /* sticky controls */
  .controls {{
    position:sticky; top:0; z-index:20; background:var(--paper);
    border-bottom:1px solid var(--hair); padding:10px 0 8px;
    display:flex; gap:10px 14px; align-items:center; flex-wrap:wrap;
    font-family:var(--mono); font-size:15px;
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

  .stat {{ display:flex; align-items:center; gap:8px; color:var(--muted); font-size:13px; }}
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
    border-radius:2px; letter-spacing:.04em; font-size:13px; }}
  .navdays a:hover {{ color:var(--seal); background:var(--surface); }}

  /* day */
  .day {{ padding-top:34px; scroll-margin-top:100px; }}
  .day.empty {{ display:none; }}
  .day-head {{ display:flex; align-items:baseline; gap:14px;
    border-bottom:1px solid var(--reed); padding-bottom:6px; margin-bottom:16px; }}
  .day-no {{ font-family:var(--mono); font-weight:700; font-size:22px;
    letter-spacing:.1em; color:var(--reed); }}
  .day-meta {{ font-family:var(--mono); font-size:13px; color:var(--faint);
    letter-spacing:.04em; }}
  .day-tally {{ font-family:var(--mono); font-size:13px; color:var(--reed);
    letter-spacing:.04em; margin-left:auto; }}
  .day-tally:empty {{ display:none; }}

  .grid {{ display:flex; flex-direction:column; }}
  .e {{ position:relative; padding:0.7rem 1.1rem 0.8rem 0.9rem;
    border-bottom:1px solid var(--hair); break-inside:avoid; }}
  .e.has-note {{ cursor:pointer; }}
  .e.hit {{ display:none; }}

  /* furigana */
  ruby rt {{ color:var(--furi); font-family:var(--mono); font-weight:500;
    letter-spacing:.02em; }}
  .ex-jp rt {{ font-size:.7em; }}

  /* kanji character — large */
  .k-char {{ font-size:3.5rem; font-weight:700; color:var(--ink); line-height:1.1; }}

  .e-head {{ display:flex; align-items:center; gap:12px; flex-wrap:wrap; }}

  /* on/kun reading row */
  .k-rd {{ display:flex; align-items:baseline; gap:10px; margin-top:4px; font-family:var(--mono); font-size:.85rem; flex-wrap:wrap; }}
  .rd-label {{ font-size:.65rem; letter-spacing:.08em; color:var(--faint); margin-right:2px; }}
  .rd-on {{ color:var(--seal); font-weight:500; }}
  .rd-kun {{ color:var(--furi); font-weight:500; }}
  .kun-label {{ margin-left:8px; }}

  .jlpt-badge {{ font-family:var(--mono); font-size:.62rem; color:var(--muted);
    border:1px solid var(--hair); border-radius:2px; padding:1px 5px;
    letter-spacing:.02em; margin-left:auto; align-self:flex-start; margin-top:.3rem; }}

  .draw {{ font:inherit; font-family:var(--mono); font-size:.7rem; line-height:1;
    cursor:pointer; color:var(--muted); background:var(--surface);
    border:1px solid var(--hair); border-radius:50%; width:1.4rem; height:1.4rem;
    padding:0; display:inline-flex; align-items:center; justify-content:center; flex:none; }}
  .draw:hover {{ color:var(--reed); border-color:var(--reed); }}
  .draw:focus-visible {{ outline:2px solid var(--reed); outline-offset:2px; }}
  .draw[disabled] {{ opacity:.3; cursor:default; }}

  .mn {{ margin:6px 0 0; font-family:var(--serif); font-size:1.1rem; font-weight:500; }}
  .mn-ne {{ margin:2px 0 0; font-size:.85rem; color:var(--muted); }}

  /* compound words list */
  .cw-list {{ margin:.6rem 0 0; padding:0; list-style:none;
    display:flex; flex-direction:column; gap:.3rem; }}
  .cw-item {{ font-size:.88rem; display:flex; align-items:baseline; flex-wrap:wrap; gap:3px; }}
  .cw-surface {{ font-family:var(--sans); font-weight:600; color:var(--ink); }}
  .cw-rd {{ font-family:var(--mono); font-size:.76rem; color:var(--furi); }}
  .cw-sep {{ color:var(--faint); }}
  .cw-m {{ font-family:var(--serif); color:var(--ink); }}
  .cw-mn {{ font-size:.8rem; color:var(--muted); font-family:var(--serif); }}

  /* example */
  .kex {{ margin-top:.6rem; padding-left:1rem; border-left:2px solid var(--hair); }}
  .kex .ex-jp {{ display:block; font-size:.95rem; }}
  .kex .ex-en {{ display:block; font-family:var(--serif); font-style:italic;
    font-size:.85rem; color:var(--muted); margin-top:2px; }}

  /* expandable more */
  .more {{ display:none; margin-top:10px; padding-top:10px; border-top:1px dotted var(--hair); }}
  .e.open .more {{ display:block; }}

  .nt-label {{ font-family:var(--mono); font-size:.62rem; color:var(--seal);
    border:1px solid var(--seal); border-radius:2px; padding:0 3px;
    margin-right:6px; letter-spacing:.04em; }}
  .comp, .conf {{ margin:6px 0 0; font-size:.82rem; color:var(--faint); line-height:1.5; }}

  /* discrimination question */
  .dq {{ margin-top:10px; padding:8px 10px; background:var(--surface);
    border:1px solid var(--hair); border-radius:2px; }}
  .dq-front {{ margin:0 0 8px; font-size:.85rem; color:var(--ink); font-family:var(--serif); }}
  .dq-opts {{ display:flex; gap:6px; flex-wrap:wrap; }}
  .dq-opt {{ font:inherit; font-family:var(--sans); font-size:1rem; font-weight:600;
    cursor:pointer; color:var(--ink); background:var(--surface);
    border:1px solid var(--hair); border-radius:2px; padding:4px 12px; }}
  .dq-opt:hover {{ border-color:var(--reed); }}
  .dq-opt.correct {{ color:var(--reed); border-color:var(--reed);
    background:color-mix(in srgb,var(--reed) 8%,var(--surface)); }}
  .dq-opt.wrong {{ color:var(--faint); border-color:var(--hair); text-decoration:line-through; }}
  .dq-note {{ margin:8px 0 0; font-size:.78rem; color:var(--faint); line-height:1.5;
    font-family:var(--serif); }}

  /* SRS pip */
  .pip {{ position:absolute; top:.6rem; left:0; width:.5rem; height:.5rem; padding:0;
    border:1.5px solid var(--faint); border-radius:50%; background:var(--surface);
    cursor:pointer; }}
  .pip:hover {{ border-color:var(--reed); }}
  .pip:focus-visible {{ outline:2px solid var(--reed); outline-offset:2px; }}
  .e.st-learning .pip {{ border-color:var(--reed);
    background:linear-gradient(90deg,var(--reed) 50%,var(--surface) 50%); }}
  .e.st-mature .pip {{ background:var(--reed); border-color:var(--reed); }}
  .e.st-mature .k-char {{ color:var(--muted); }}

  .e-tags {{ position:absolute; top:.5rem; right:.35rem; display:flex; flex-direction:column;
    align-items:flex-end; gap:3px; font-family:var(--mono); }}
  .e-cue {{ font-size:.65rem; color:var(--faint); user-select:none; }}
  .e.open .e-cue {{ color:var(--seal); }}
  .e-due {{ font-size:.6rem; letter-spacing:.08em; text-transform:uppercase;
    color:var(--seal); border:1px solid var(--seal); border-radius:2px;
    padding:0 3px; display:none; }}
  .e.is-due .e-due {{ display:block; }}

  body[data-filter="due"] .e:not(.is-due),
  body[data-filter="new"] .e:not(.st-new),
  body[data-filter="learning"] .e:not(.st-learning),
  body[data-filter="mature"] .e:not(.st-mature) {{ display:none; }}

  /* visibility toggles */
  body.no-furi ruby rt {{ visibility:hidden; }}
  body.no-mn .mn, body.no-mn .mn-ne, body.no-mn .ex-en {{
    background:var(--hair); color:transparent;
    border-radius:2px; user-select:none; }}

  .nores {{ display:none; padding:40px 0; color:var(--muted); font-family:var(--serif);
    font-size:1.05rem; }}
  .nores.show {{ display:block; }}

  footer {{ margin-top:52px; padding-top:16px; border-top:2px solid var(--ink);
    font-family:var(--mono); font-size:12px; color:var(--faint); letter-spacing:.03em; }}
  footer details {{ margin-top:10px; }}
  footer summary {{ cursor:pointer; color:var(--muted); }}
  footer a {{ color:var(--reed); }}
  footer textarea {{ width:100%; max-width:560px; height:80px; margin-top:8px;
    font:inherit; font-size:12px; background:var(--surface); color:var(--ink);
    border:1px solid var(--hair); border-radius:2px; padding:6px; }}
  footer .io-btns {{ display:flex; gap:6px; margin-top:6px; }}
  footer .io-btns button {{ font:inherit; font-size:12px; cursor:pointer;
    color:var(--muted); background:var(--surface); border:1px solid var(--hair);
    border-radius:2px; padding:3px 8px; }}

  /* stroke-order overlay */
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

  /* review overlay */
  .rv[hidden] {{ display:none; }}
  .rv {{ position:fixed; inset:0; z-index:50; background:color-mix(in srgb,var(--paper) 88%,transparent);
    backdrop-filter:blur(3px); display:flex; align-items:center; justify-content:center; padding:20px; }}
  .rv-card {{ width:min(760px,100%); background:var(--surface); border:1px solid var(--hair);
    border-top:3px solid var(--seal); border-radius:3px; padding:22px 24px 20px;
    box-shadow:0 20px 60px color-mix(in srgb,var(--ink) 22%,transparent); }}
  .rv-top {{ display:flex; justify-content:space-between; align-items:center; gap:10px;
    font-family:var(--mono); font-size:12px; color:var(--muted); letter-spacing:.06em; }}
  .rv-top button {{ font:inherit; cursor:pointer; color:var(--muted);
    background:none; border:1px solid var(--hair); border-radius:2px; padding:3px 7px; }}
  .rv-front {{ text-align:center; padding:34px 0 26px; font-size:4rem; font-weight:700; }}
  .rv-hint {{ text-align:center; font-family:var(--mono); font-size:13px;
    color:var(--faint); letter-spacing:.06em; padding-bottom:22px; }}
  .rv-back {{ padding:6px 0 18px; border-top:1px solid var(--hair); margin-top:4px; }}
  .rv-back[hidden] {{ display:none; }}
  .rv-back .pip, .rv-back .e-tags {{ display:none; }}
  .rv-back .more {{ display:block; margin-top:8px; padding-top:8px; border-top:1px dotted var(--hair); }}
  .rv-actions {{ display:flex; gap:8px; }}
  .rv-actions button {{ flex:1; font:inherit; font-family:var(--mono); font-size:15px;
    cursor:pointer; padding:10px 4px; border-radius:2px; border:1px solid var(--hair);
    background:var(--surface); color:var(--ink); letter-spacing:.03em; }}
  .rv-actions button:focus-visible {{ outline:2px solid var(--reed); outline-offset:2px; }}
  .rv-actions .g-again {{ border-color:var(--seal); color:var(--seal); }}
  .rv-actions .g-good {{ border-color:var(--reed); color:var(--reed); }}
  .rv-actions .g-easy {{ color:var(--muted); }}
  .rv-actions .reveal {{ flex:1; border-color:var(--reed); color:var(--reed); font-weight:700; }}
  .rv-done {{ text-align:center; padding:30px 0; }}
  .rv-done p {{ font-family:var(--serif); font-size:17px; margin:0 0 4px; }}
  .rv-done span {{ font-family:var(--mono); font-size:13px; color:var(--muted); }}

  @media (prefers-reduced-motion:no-preference) {{ html {{ scroll-behavior:smooth; }} }}
  @media print {{
    .controls, .pip, .e-cue, .e-due, .rv, .draw, .so {{ display:none; }}
    header {{ background-image:none; }}
    body {{ background:#fff; color:#000; }}
    .day, .e {{ break-inside:avoid; }}
    .e.st-mature .k-char {{ color:#000; }}
  }}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="inner">
    <h1>N3 漢字 <span class="lat">{total} KANJI &middot; {len(all_days)} DAYS &middot; JLPT N3</span></h1>
  </div>
  <p class="sub">Every kanji from the N3 deck. On&rsquo;yomi in <span class="k-seal">vermilion</span>,
    kun&rsquo;yomi in <span class="k-furi">blue</span>. Click <b>+</b> cards for component analysis,
    confusable notes, and discrimination quizzes. Hit <b>&#9998;</b> for animated stroke order.
    Each kanji has a spaced-repetition ring; <b>Review</b> walks what is due.</p>
  <div class="dcount">EXAM 2026&middot;12&middot;03 &nbsp;<b>D&minus;80</b></div>
</header>

<nav class="controls">
  <div class="search">
    <input id="q" type="search" placeholder="search kanji &middot; reading &middot; meaning" autocomplete="off" spellcheck="false">
    <button class="x" id="q-x" type="button" aria-label="clear search" hidden>&times;</button>
  </div>
  <button class="review-btn" id="review-open">Review</button>
  <div class="stat" title="mature = box 5+ &middot; due = ready to review">
    <span class="bar"><i id="stat-fill"></i></span>
    <span><b class="s-mat" id="s-mat">0</b> mature &middot; <b id="s-lrn">0</b> learning &middot;
      <b id="s-new">{total}</b> new &middot; <b class="s-due" id="s-due">0</b> due</span>
  </div>
  <select class="filter" id="filter" aria-label="filter kanji">
    <option value="all">all kanji</option>
    <option value="due">due now</option>
    <option value="new">new</option>
    <option value="learning">learning</option>
    <option value="mature">mature</option>
  </select>
  <div class="toggles">
    <button id="t-furi" aria-pressed="false">hide furigana</button>
    <button id="t-mn" aria-pressed="false">hide meanings</button>
  </div>
  <div class="navdays">{nav_links}</div>
</nav>

<main>
<p class="nores" id="nores">No kanji matches that.</p>
{main_content}
</main>

<footer>
  N3 漢字 &nbsp;&middot;&nbsp; {total} kanji &nbsp;&middot;&nbsp; generated 2026-09-14
  &nbsp;&middot;&nbsp; <b>/</b> search &nbsp; <b>R</b> review &nbsp; <b>W</b> stroke order &nbsp;
  click a card with <b>+</b> for component &amp; confusion notes, click the ring to promote,
  hit <b>&#9998;</b> for animated stroke order &nbsp;&middot;&nbsp; SRS boxes 0&ndash;6
  at 1&middot;2&middot;4&middot;8&middot;16&middot;35-day steps, saved in this browser &nbsp;&middot;&nbsp;
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

<script>
window.KVG_URL = "/kanjivg_strokes.json";

(function () {{
  var body = document.body;
  var LS = {{
    get: function (k, d) {{ try {{ var v = JSON.parse(localStorage.getItem(k)); return v == null ? d : v; }} catch (e) {{ return d; }} }},
    set: function (k, v) {{ try {{ localStorage.setItem(k, JSON.stringify(v)); }} catch (e) {{}} }}
  }};

  /* ---- search --------------------------------------------------------- */
  function _esc(s) {{
    return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  }}
  function _plain(s) {{ return s.replace(/\\[[^\\]]*\\]/g,""); }}

  var q = document.getElementById("q");
  var qx = document.getElementById("q-x");
  var nores = document.getElementById("nores");
  var entries = Array.prototype.slice.call(document.querySelectorAll(".e"));
  var days = Array.prototype.slice.call(document.querySelectorAll(".day"));
  var byKanji = {{}};
  entries.forEach(function (e) {{ byKanji[e.dataset.w] = e; }});
  var TOTAL = entries.length;

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

  /* ---- card expand ---------------------------------------------------- */
  document.addEventListener("click", function (ev) {{
    var e = ev.target.closest(".e.has-note");
    if (!e) return;
    if (ev.target.closest(".pip") || ev.target.closest(".draw") || ev.target.closest(".dq-opt")) return;
    e.classList.toggle("open");
  }});

  /* ---- discrimination quiz ------------------------------------------- */
  document.addEventListener("click", function (ev) {{
    var btn = ev.target.closest(".dq-opt");
    if (!btn) return;
    ev.stopPropagation();
    var dq = btn.closest(".dq");
    if (!dq || dq.dataset.answered) return;
    dq.dataset.answered = "1";
    var ans = btn.dataset.ans;
    Array.prototype.forEach.call(dq.querySelectorAll(".dq-opt"), function (b) {{
      b.classList.add(b.textContent.trim() === ans ? "correct" : "wrong");
    }});
    var note = dq.querySelector(".dq-note");
    if (note) note.hidden = false;
  }});

  /* ---- filter + visibility toggles ------------------------------------ */
  var pref = LS.get("n3kref", {{}});
  var filter = document.getElementById("filter");
  filter.value = pref.filter || "all";
  body.dataset.filter = filter.value;
  filter.addEventListener("change", function () {{
    body.dataset.filter = filter.value; pref.filter = filter.value; LS.set("n3kref", pref);
    refreshDays();
  }});

  var defs = {{ "t-furi": "no-furi", "t-mn": "no-mn" }};
  Object.keys(defs).forEach(function (id) {{
    var btn = document.getElementById(id);
    setToggle(btn, !!pref[id]);
    btn.addEventListener("click", function () {{
      setToggle(btn, btn.getAttribute("aria-pressed") !== "true");
      Object.keys(defs).forEach(function (i) {{
        pref[i] = document.getElementById(i).getAttribute("aria-pressed") === "true";
      }});
      LS.set("n3kref", pref);
    }});
  }});
  function setToggle(btn, on) {{
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    body.classList.toggle(defs[btn.id], on);
  }}

  /* ---- SRS core ------------------------------------------------------- */
  var BOX_DAYS = [0, 1, 2, 4, 8, 16, 35];
  var MATURE = 5;
  var NEW_PER_SESSION = 15;
  var srs = LS.get("n3krs", {{}});

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
  function grade(w, g) {{
    var s = srs[w] || {{ b: 0, reps: 0, lapses: 0 }};
    if (g === "again") {{ if (s.b > 0) s.lapses++; s.b = 0; s.due = TODAY; }}
    else {{
      s.b = Math.min(6, s.b + (g === "easy" ? 2 : 1));
      if (g === "easy" && s.b < 3) s.b = 3;
      s.due = todayStr(BOX_DAYS[s.b]);
    }}
    s.reps++;
    srs[w] = s;
    LS.set("n3krs", srs);
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
        ? ("box " + s.b + (d ? " \xb7 due" : " \xb7 due " + s.due)) : "new - click to mark seen";
    }});
    set("s-mat", mat); set("s-lrn", lrn); set("s-new", TOTAL - mat - lrn); set("s-due", due);
    document.getElementById("stat-fill").style.width = (mat / TOTAL * 100) + "%";
    var qsz = queueSize();
    var btn = document.getElementById("review-open");
    btn.disabled = qsz === 0;
    btn.textContent = qsz ? "Review " + qsz : "Review — clear";
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
    var searching = !!q.value.trim();
    if (!anyVisible && !searching && f !== "all") {{
      nores.textContent = f === "due" ? "Nothing due right now — come back later."
        : "No " + f + " kanji yet.";
      nores.classList.add("show");
    }} else if (!searching) {{
      nores.classList.remove("show");
    }}
  }}

  /* ---- pip click: advance one box ------------------------------------ */
  document.addEventListener("click", function (ev) {{
    var pip = ev.target.closest(".pip");
    if (!pip) return;
    ev.stopPropagation();
    var e = pip.closest(".e");
    if (!e) return;
    var w = e.dataset.w;
    var s = srs[w] || {{ b: 0, reps: 0, lapses: 0 }};
    s.b = Math.min(6, s.b + 1);
    s.due = todayStr(BOX_DAYS[s.b]);
    s.reps++;
    srs[w] = s;
    LS.set("n3krs", srs);
    paint();
  }});

  /* ---- review overlay ------------------------------------------------ */
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
      stage.innerHTML = '<div class="rv-done"><p>きりがいい。 Session done.</p>' +
        '<span>' + done + ' reviewed' + (left ? ' \xb7 ' + left + ' still in queue' : ' \xb7 queue clear') +
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
    var e = byKanji[w];
    rvCount.textContent = (qi + 1) + " / " + queue.length;
    stage.innerHTML = "";
    var front = document.createElement("div");
    front.className = "rv-front";
    front.textContent = w;
    var hint = document.createElement("div");
    hint.className = "rv-hint";
    hint.textContent = srs[w] ? ("box " + srs[w].b) : "new kanji";
    var back = document.createElement("div");
    back.className = "rv-back"; back.hidden = true;
    back.appendChild(e.querySelector(".e-body").cloneNode(true));
    var actions = document.createElement("div");
    actions.className = "rv-actions";
    actions.innerHTML = '<button class="reveal" data-a="reveal">reveal \xa0\xb7\xa0 space</button>';
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
    if (rv.hidden && so.hidden) {{
      if (ev.key === "/" && ev.target !== q) {{ ev.preventDefault(); q.focus(); q.select(); return; }}
      if (ev.key === "r" || ev.key === "R") {{ if (ev.target !== q) {{ document.getElementById("review-open").click(); return; }} }}
    }}
    if (!rv.hidden) {{
      if (ev.key === " " || ev.key === "Spacebar") {{ ev.preventDefault(); reveal(); return; }}
      if (ev.key === "1") {{ var b1 = stage.querySelector("[data-a=again]"); if (b1) b1.click(); return; }}
      if (ev.key === "2") {{ var b2 = stage.querySelector("[data-a=good]"); if (b2) b2.click(); return; }}
      if (ev.key === "3") {{ var b3 = stage.querySelector("[data-a=easy]"); if (b3) b3.click(); return; }}
      if (ev.key === "Escape") {{ closeReview(); return; }}
    }}
    if (!so.hidden && ev.key === "Escape") {{ soClose(); return; }}
  }});

  /* ---- backup / restore ---------------------------------------------- */
  var io = document.getElementById("io");
  document.getElementById("io-export").onclick = function () {{ io.value = JSON.stringify(srs); }};
  document.getElementById("io-import").onclick = function () {{
    try {{
      var obj = JSON.parse(io.value);
      if (obj && typeof obj === "object") {{
        srs = obj; LS.set("n3krs", srs); paint();
        io.value = "restored " + Object.keys(srs).length + " kanji";
      }}
    }} catch (e) {{ io.value = "could not parse that JSON"; }}
  }};
  document.getElementById("io-reset").onclick = function () {{
    if (confirm("Clear all spaced-repetition progress on this device?")) {{
      srs = {{}}; LS.set("n3krs", srs); paint(); io.value = "";
    }}
  }};

  /* ---- stroke-order animation ---------------------------------------- */
  var KVG = window.KVG || {{}};
  var haveKvg = !!Object.keys(KVG).length;

  if (!haveKvg && window.KVG_URL) {{
    fetch(window.KVG_URL)
      .then(function (r) {{ return r.json(); }})
      .then(function (data) {{
        KVG = data;
        haveKvg = !!Object.keys(KVG).length;
        if (haveKvg) {{
          Array.prototype.forEach.call(document.querySelectorAll(".draw"), function (b) {{
            b.disabled = false; b.title = "stroke order";
          }});
        }}
      }})
      .catch(function (err) {{ console.warn("KVG fetch failed:", err); }});
  }}
  var SVGNS = "http://www.w3.org/2000/svg";
  var so = document.getElementById("so");
  var soChars = document.getElementById("so-chars");
  var soCard = document.querySelector(".so-card");
  var soHint = document.getElementById("so-hint");
  var soPlayBtn = document.getElementById("so-play");
  var soAllBtn = document.getElementById("so-all");
  var soNumBtn = document.getElementById("so-num");

  var soRet = null;
  var soTracks = [];
  var soK = 0;
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

  function computeDur(idx) {{
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
      p.getBoundingClientRect();
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
    soHint.innerHTML = "<b>" + soK + "</b> / " + soMax + (soK >= soMax && soMax ? " ✓" : "");
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
    var e = byKanji[w];
    document.getElementById("so-rd").textContent = e ? (e.dataset.kun || e.dataset.on || "") : "";
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
    soApplyNums(on); pref.soNums = on; LS.set("n3kref", pref);
  }});
  so.addEventListener("click", function (ev) {{ if (ev.target === so) soClose(); }});

  document.addEventListener("click", function (ev) {{
    var d = ev.target.closest(".draw");
    if (!d) return;
    ev.stopPropagation();
    soOpen(d.dataset.w, d);
  }});

  if (!haveKvg) {{
    var kvgMsg = window.KVG_URL ? "loading stroke data…" : "stroke data not built";
    Array.prototype.forEach.call(document.querySelectorAll(".draw"), function (b) {{
      b.disabled = true; b.title = kvgMsg;
    }});
  }}

  /* ---- init ----------------------------------------------------------- */
  paint();
}})();
</script>
</body>
</html>"""


def main():
    # Read KVG URL
    kvg_url = "/kanjivg_strokes.json"
    if KVG_URL_FILE.exists():
        with open(KVG_URL_FILE, encoding="utf-8") as f:
            obj = json.load(f)
            kvg_url = obj.get("url", kvg_url)

    print(f"Reading {len(CSV_FILES)} CSV files...")
    rows = read_kanji_rows()
    print(f"  Total rows: {len(rows)}")

    kanji_data = build_kanji_data(rows)
    print(f"  Parsed kanji: {len(kanji_data)}")

    days_found = sorted(set(r["day"] for r in kanji_data))
    print(f"  Days found: {days_found[0]}–{days_found[-1]} ({len(days_found)} days)")

    html = build_html(kanji_data, kvg_url)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)

    size_kb = OUT.stat().st_size / 1024
    print(f"\nOutput: {OUT}")
    print(f"  Size: {size_kb:.1f} KB")
    print(f"  Kanji count: {len(kanji_data)}")
    print(f"  Days: {len(days_found)}")
    print("Done.")


if __name__ == "__main__":
    main()
