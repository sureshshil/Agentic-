"""
build_grammar_reference.py
Generates simple_agent/vocab_artifacts/n3_grammar_reference.html from
the 6 n3_grammar_batch*.csv files in simple_agent/.
"""

import csv
import json
import os
import re
import sys
from pathlib import Path
from datetime import date

# ── paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT       = SCRIPT_DIR.parent            # simple_agent/
OUT_DIR    = ROOT / "vocab_artifacts"
OUT_FILE   = OUT_DIR / "n3_grammar_reference.html"

CSV_GLOB   = sorted(ROOT.glob("n3_grammar_batch*.csv"))
if not CSV_GLOB:
    sys.exit("ERROR: no n3_grammar_batch*.csv files found in " + str(ROOT))

OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── read CSVs ─────────────────────────────────────────────────────────────────
def batch_num(path: Path) -> int:
    m = re.search(r'batch(\d+)', path.name)
    return int(m.group(1)) if m else 0

records = []
for csv_path in sorted(CSV_GLOB, key=batch_num):
    bn = batch_num(csv_path)
    with open(csv_path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            g = row.get("grammar", "").strip()
            if not g:
                continue
            tags_raw = row.get("tags", "")
            # extract category: last word that is not N3/grammar/jlpt
            cats = [t.strip() for t in tags_raw.split()
                    if t.strip().lower() not in ("n3", "grammar", "jlpt", "")]
            cat = cats[0] if cats else "other"

            # examples
            exs = []
            for n in ("1", "2", "3"):
                j = row.get(f"ex{n}", "").strip()
                r = row.get(f"ex{n}_reading", "").strip()
                e = row.get(f"ex{n}_en", "").strip()
                if j:
                    exs.append({"j": j, "r": r, "e": e})

            # disc options list
            disc_opts_raw = row.get("disc_options", "").strip()
            disc_opts = [o.strip() for o in disc_opts_raw.split(" / ")] if disc_opts_raw else []

            rec = {
                "g":  g,
                "f":  row.get("formation", "").strip(),
                "m":  row.get("meaning_en", "").strip(),
                "mn": row.get("meaning_ne", "").strip(),
                "ex": exs,
                "fr": row.get("frame", "").strip(),
                "nu": row.get("nuance", "").strip(),
                "sp": row.get("spoken", "").strip(),
                "co": row.get("contrast", "").strip(),
                "tr": row.get("trap", "").strip(),
                "cl": [row.get("cloze_front", "").strip(),
                       row.get("cloze_answer", "").strip()],
                "dq": {
                    "f":  row.get("disc_front", "").strip(),
                    "o":  disc_opts,
                    "a":  row.get("disc_answer", "").strip(),
                    "w":  row.get("disc_why", "").strip(),
                    "wr": row.get("disc_wrong", "").strip(),
                },
                "cq": {
                    "f": row.get("contrast_front", "").strip(),
                    "a": row.get("contrast_answer", "").strip(),
                    "n": row.get("contrast_note", "").strip(),
                },
                "batch": bn,
                "cat":   cat,
            }
            records.append(rec)

total = len(records)
cats_all = sorted({r["cat"] for r in records})
today = date.today().isoformat()

grammar_json = json.dumps(records, ensure_ascii=False, separators=(",", ":"))

# ── group by batch ────────────────────────────────────────────────────────────
from collections import defaultdict
by_batch: dict[int, list] = defaultdict(list)
for r in records:
    by_batch[r["batch"]].append(r)

# ── HTML ──────────────────────────────────────────────────────────────────────
def esc(s: str) -> str:
    return (s.replace("&","&amp;").replace("<","&lt;")
             .replace(">","&gt;").replace('"',"&quot;"))

# nav links for groups
nav_links = "".join(
    f'<a href="#grp{b:02d}">{b:02d}</a>'
    for b in sorted(by_batch)
)

# category options
cat_opts = '<option value="">all categories</option>\n'
for c in cats_all:
    cat_opts += f'<option value="{esc(c)}">{esc(c)}</option>\n'

# cards HTML
cards_html_parts = []
for bn in sorted(by_batch):
    items = by_batch[bn]
    cards_html_parts.append(
        f'<section class="day" id="grp{bn:02d}" data-batch="{bn}">\n'
        f'<div class="day-head">'
        f'<span class="day-no">GROUP {bn:02d}</span>'
        f'<span class="day-meta">{esc(items[0]["g"])} → {esc(items[-1]["g"])}</span>'
        f'<span class="day-tally">{len(items)} patterns</span>'
        f'</div>\n'
        f'<div class="grid">\n'
    )
    for idx, r in enumerate(items):
        g_id = f"g{bn:02d}_{idx:03d}"
        has_more = bool(r["nu"] or r["sp"] or r["co"] or r["tr"])
        cls = "e" + (" has-note" if has_more else "")
        ex_html = ""
        for i, ex in enumerate(r["ex"]):
            ex_html += (
                f'<div class="ex">'
                f'<span class="ex-no">{i+1}</span>'
                f'<span class="ex-jp">{esc(ex["j"])}</span>'
                f'<span class="ex-rd">{esc(ex["r"])}</span>'
                f'<span class="ex-en">{esc(ex["e"])}</span>'
                f'</div>'
            )
        more_html = ""
        if has_more:
            if r["nu"]:
                more_html += f'<p class="nt nu-note"><span class="nt-k">nuance</span> {esc(r["nu"])}</p>'
            if r["sp"]:
                more_html += f'<p class="nt sp-note"><span class="nt-k">spoken</span> {esc(r["sp"])}</p>'
            if r["co"]:
                more_html += f'<p class="nt co-note"><span class="nt-k">contrast</span> {esc(r["co"])}</p>'
            if r["tr"]:
                more_html += f'<p class="nt tr-note"><span class="nt-k">trap</span> {esc(r["tr"])}</p>'

        cards_html_parts.append(
            f'<div class="{cls}" id="{g_id}" data-gi="{idx}" data-batch="{bn}" data-cat="{esc(r["cat"])}">'
            f'<button class="pip" aria-label="SRS status" data-id="{esc(r["g"])}"></button>'
            f'<div class="e-tags">'
            f'<span class="e-cue">{"+" if has_more else ""}</span>'
            f'<span class="e-due">DUE</span>'
            f'</div>'
            f'<div class="e-body">'
            f'<div class="gw">{esc(r["g"])}</div>'
            f'<div class="gf">{esc(r["f"])}</div>'
            f'<div class="mn">{esc(r["m"])}'
            f'{"<span class=mnne>" + esc(r["mn"]) + "</span>" if r["mn"] else ""}'
            f'</div>'
            f'{"<div class=pt><span class=pt-k>型</span>" + esc(r["fr"]) + "</div>" if r["fr"] else ""}'
            f'<div class="ex-grid">{ex_html}</div>'
            f'{"<div class=more>" + more_html + "</div>" if more_html else ""}'
            f'</div>'
            f'</div>\n'
        )
    cards_html_parts.append('</div>\n</section>\n')

cards_html = "".join(cards_html_parts)

# ── full HTML document ────────────────────────────────────────────────────────
html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>N3 文法 120</title>
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
html {{ font-size:18px; }}
body {{
  margin:0; background:var(--paper); color:var(--ink);
  font-family:var(--sans); font-size:1rem; line-height:1.6;
  -webkit-font-smoothing:antialiased;
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
h1 {{ font-weight:700; font-size:clamp(24px,5vw,40px); margin:0;
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

.drill-btn {{ font:inherit; font-weight:700; letter-spacing:.04em; cursor:pointer;
  color:var(--surface); background:var(--reed); border:1px solid var(--reed);
  border-radius:2px; padding:6px 12px; }}
.drill-btn:disabled {{ background:var(--surface); color:var(--faint);
  border-color:var(--hair); cursor:default; font-weight:500; }}
.drill-btn:focus-visible {{ outline:2px solid var(--seal); outline-offset:2px; }}

.stat {{ display:flex; align-items:center; gap:8px; color:var(--muted); }}
.stat .bar {{ width:90px; height:6px; background:var(--hair); border-radius:3px; overflow:hidden; }}
.stat .bar i {{ display:block; height:100%; width:0; background:var(--reed); transition:width .3s ease; }}
.stat b {{ font-weight:700; }}
.stat .s-mat {{ color:var(--reed); }} .stat .s-due {{ color:var(--seal); }}

.filter {{ font:inherit; color:var(--muted); background:var(--surface);
  border:1px solid var(--hair); border-radius:2px; padding:5px 6px; cursor:pointer; }}

.toggles {{ display:flex; gap:4px; flex-wrap:wrap; }}
.toggles button {{ font:inherit; color:var(--muted); background:var(--surface);
  border:1px solid var(--hair); border-radius:2px; padding:4px 9px; cursor:pointer;
  letter-spacing:.03em; }}
.toggles button[aria-pressed="true"] {{ color:var(--seal); border-color:var(--seal); }}
.toggles button:focus-visible {{ outline:2px solid var(--reed); outline-offset:1px; }}
.ml-auto {{ margin-left:auto; }}

.navdays {{ display:flex; gap:2px; flex-wrap:wrap; flex-basis:100%; order:5; }}
.navdays a {{ color:var(--muted); text-decoration:none; padding:3px 6px;
  border-radius:2px; letter-spacing:.04em; font-size:13px; }}
.navdays a:hover {{ color:var(--seal); background:var(--surface); }}

/* group sections */
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
.e {{ position:relative; padding:0.55rem 1.1rem 0.6rem 0.75rem;
  border-bottom:1px solid var(--hair); break-inside:avoid; }}
.e.has-note {{ cursor:pointer; }}
.e.hit {{ display:none; }}

/* grammar word (large) */
.gw {{ font-size:1.3rem; font-weight:700; color:var(--seal); line-height:1.3; }}
/* formation (mono, muted) */
.gf {{ font-family:var(--mono); font-size:.72rem; color:var(--muted);
  margin-top:2px; letter-spacing:.01em; white-space:pre-wrap; word-break:break-all; }}

.e-head {{ display:flex; align-items:baseline; gap:9px; flex-wrap:wrap; }}

.mn {{ margin:5px 0 0; font-family:var(--serif); font-size:.97rem; }}
.mnne {{ display:block; font-size:.82rem; color:var(--muted); font-style:normal; margin-top:1px; }}
.pt {{ margin:5px 0 0; font-size:.82rem; color:var(--muted); line-height:1.5; }}
.pt-k {{ font-size:.62rem; color:var(--seal); border:1px solid var(--seal);
  border-radius:2px; padding:0 3px; margin-right:6px; position:relative; top:-1px; }}

.ex-grid {{ margin-top:.5rem; display:flex; flex-direction:column; gap:.5rem; }}
.ex {{ position:relative; padding-left:1.15rem; }}
.ex-no {{ position:absolute; left:0; top:.15rem; font-family:var(--mono);
  font-size:.66rem; font-weight:700; color:var(--seal); }}
.ex-jp {{ display:block; font-size:.92rem; }}
.ex-rd {{ display:block; font-family:var(--mono); font-size:.72rem;
  color:var(--furi); margin-top:1px; letter-spacing:.01em; }}
.ex-en {{ display:block; font-family:var(--serif); font-style:italic;
  font-size:.85rem; color:var(--muted); margin-top:2px; }}

.e-tags {{ position:absolute; top:.45rem; right:.35rem; display:flex; flex-direction:column;
  align-items:flex-end; gap:3px; font-family:var(--mono); }}
.e-cue {{ font-size:.8rem; color:var(--faint); user-select:none; }}
.e.open .e-cue {{ color:var(--seal); }}
.e-due {{ font-size:.62rem; letter-spacing:.08em; text-transform:uppercase;
  color:var(--seal); border:1px solid var(--seal); border-radius:2px;
  padding:0 3px; display:none; }}
.e.is-due .e-due {{ display:block; }}

.more {{ display:none; margin-top:8px; padding-top:8px; border-top:1px dotted var(--hair); }}
.e.open .more {{ display:block; }}
.nt {{ margin:6px 0 0; font-size:.78rem; color:var(--faint); line-height:1.5; }}
.nt-k {{ font-size:.62rem; color:var(--seal); border:1px solid var(--seal);
  border-radius:2px; padding:0 3px; margin-right:5px; position:relative; top:-1px; }}

/* SRS pip */
.pip {{ position:absolute; top:.5rem; left:0; width:.42rem; height:.42rem; padding:0;
  border:1.5px solid var(--faint); border-radius:50%; background:var(--surface);
  cursor:pointer; }}
.pip:hover {{ border-color:var(--reed); }}
.pip:focus-visible {{ outline:2px solid var(--reed); outline-offset:2px; }}
.e.st-learning .pip {{ border-color:var(--reed);
  background:linear-gradient(90deg,var(--reed) 50%,var(--surface) 50%); }}
.e.st-mature .pip {{ background:var(--reed); border-color:var(--reed); }}
.e.st-mature .gw {{ color:var(--muted); }}

/* filter states */
body[data-filter="due"]      .e:not(.is-due)      {{ display:none; }}
body[data-filter="new"]      .e:not(.st-new)       {{ display:none; }}
body[data-filter="learning"] .e:not(.st-learning)  {{ display:none; }}
body[data-filter="mature"]   .e:not(.st-mature)    {{ display:none; }}

/* visibility toggles */
body.no-mn .mn,
body.no-mn .ex-en  {{ background:var(--hair); color:transparent; border-radius:2px; user-select:none; }}
body.no-pt .pt {{ display:none; }}
body.no-rd .ex-rd {{ visibility:hidden; }}

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

/* ── review overlay ────────────────────────────────────────────────────────── */
.rv[hidden] {{ display:none; }}
.rv {{ position:fixed; inset:0; z-index:50;
  background:color-mix(in srgb,var(--paper) 88%,transparent);
  backdrop-filter:blur(3px); display:flex; align-items:center; justify-content:center; padding:20px; }}
.rv-card {{ width:min(760px,100%); background:var(--surface); border:1px solid var(--hair);
  border-top:3px solid var(--seal); border-radius:3px; padding:22px 24px 20px;
  box-shadow:0 20px 60px color-mix(in srgb,var(--ink) 22%,transparent); }}
.rv-top {{ display:flex; justify-content:space-between; align-items:center; gap:10px;
  font-family:var(--mono); font-size:12px; color:var(--muted); letter-spacing:.06em; }}
.rv-top button {{ font:inherit; cursor:pointer; color:var(--muted);
  background:none; border:1px solid var(--hair); border-radius:2px; padding:3px 7px; }}
.rv-front {{ text-align:center; padding:34px 0 26px; font-size:2rem; font-weight:700; color:var(--seal); }}
.rv-hint {{ text-align:center; font-family:var(--mono); font-size:13px;
  color:var(--faint); letter-spacing:.06em; padding-bottom:22px; }}
.rv-back {{ padding:6px 0 18px; border-top:1px solid var(--hair); margin-top:4px; }}
.rv-back[hidden] {{ display:none; }}
.rv-actions {{ display:flex; gap:8px; }}
.rv-actions button {{ flex:1; font:inherit; font-family:var(--mono); font-size:15px;
  cursor:pointer; padding:10px 4px; border-radius:2px; border:1px solid var(--hair);
  background:var(--surface); color:var(--ink); letter-spacing:.03em; }}
.rv-actions button:focus-visible {{ outline:2px solid var(--reed); outline-offset:2px; }}
.rv-actions .g-again {{ border-color:var(--seal); color:var(--seal); }}
.rv-actions .g-good  {{ border-color:var(--reed); color:var(--reed); }}
.rv-actions .g-easy  {{ color:var(--muted); }}
.rv-actions .reveal  {{ flex:1; border-color:var(--reed); color:var(--reed); font-weight:700; }}
.rv-done {{ text-align:center; padding:30px 0; }}
.rv-done p {{ font-family:var(--serif); font-size:17px; margin:0 0 4px; }}
.rv-done span {{ font-family:var(--mono); font-size:13px; color:var(--muted); }}

/* ── drill overlay ─────────────────────────────────────────────────────────── */
.dr[hidden] {{ display:none; }}
.dr {{ position:fixed; inset:0; z-index:51;
  background:color-mix(in srgb,var(--paper) 88%,transparent);
  backdrop-filter:blur(3px); display:flex; align-items:center; justify-content:center; padding:20px; }}
.dr-card {{ width:min(760px,100%); background:var(--surface); border:1px solid var(--hair);
  border-top:3px solid var(--reed); border-radius:3px; padding:22px 24px 20px;
  box-shadow:0 20px 60px color-mix(in srgb,var(--ink) 22%,transparent);
  max-height:90vh; overflow-y:auto; }}
.dr-top {{ display:flex; justify-content:space-between; align-items:center;
  font-family:var(--mono); font-size:12px; color:var(--muted); letter-spacing:.06em; margin-bottom:16px; }}
.dr-top button {{ font:inherit; cursor:pointer; color:var(--muted);
  background:none; border:1px solid var(--hair); border-radius:2px; padding:3px 7px; }}
.dr-step-badge {{ font-family:var(--mono); font-size:11px; letter-spacing:.08em;
  color:var(--seal); border:1px solid var(--seal); border-radius:2px; padding:1px 6px; }}
.dr-grammar {{ font-size:1.5rem; font-weight:700; color:var(--seal); margin-bottom:6px; }}
.dr-question {{ font-size:1.1rem; margin:14px 0; line-height:1.7; }}
.dr-blank {{ color:var(--seal); font-weight:700; }}
.dr-answer {{ margin:12px 0; padding:10px 14px;
  background:color-mix(in srgb,var(--seal) 10%,var(--surface));
  border-left:3px solid var(--seal); border-radius:2px;
  font-family:var(--mono); font-size:1rem; display:none; }}
.dr-answer.show {{ display:block; }}
.dr-answer .ans-word {{ color:var(--seal); font-weight:700; font-size:1.1rem; }}
.dr-note {{ font-family:var(--serif); font-size:.88rem; color:var(--muted);
  margin-top:8px; line-height:1.5; }}
.dr-opts {{ display:flex; flex-direction:column; gap:8px; margin:14px 0; }}
.dr-opts button {{ font:inherit; font-family:var(--mono); font-size:.9rem;
  text-align:left; padding:8px 12px; border:1px solid var(--hair);
  background:var(--surface); color:var(--ink); border-radius:2px; cursor:pointer; }}
.dr-opts button:hover:not(:disabled) {{ border-color:var(--reed); }}
.dr-opts button.correct {{ border-color:var(--reed) !important; background:color-mix(in srgb,var(--reed) 15%,var(--surface)) !important; color:var(--reed) !important; }}
.dr-opts button.wrong   {{ border-color:var(--seal) !important; background:color-mix(in srgb,var(--seal) 10%,var(--surface)) !important; color:var(--seal) !important; }}
.dr-opts button:disabled {{ cursor:default; opacity:.7; }}
.dr-why {{ margin-top:10px; font-family:var(--serif); font-size:.88rem;
  color:var(--muted); line-height:1.5; display:none; }}
.dr-why.show {{ display:block; }}
.dr-actions {{ display:flex; gap:8px; margin-top:18px; flex-wrap:wrap; }}
.dr-actions button {{ font:inherit; font-family:var(--mono); font-size:.88rem;
  padding:8px 14px; border:1px solid var(--hair); background:var(--surface);
  color:var(--ink); border-radius:2px; cursor:pointer; letter-spacing:.03em; }}
.dr-actions .dr-next {{ border-color:var(--reed); color:var(--reed); font-weight:700; }}
.dr-actions .dr-reveal {{ border-color:var(--seal); color:var(--seal); font-weight:700; }}
.dr-actions .g-again {{ border-color:var(--seal); color:var(--seal); }}
.dr-actions .g-good  {{ border-color:var(--reed); color:var(--reed); }}
.dr-actions .g-easy  {{ color:var(--muted); }}
.dr-done {{ text-align:center; padding:30px 0; }}
.dr-done p {{ font-family:var(--serif); font-size:17px; margin:0 0 4px; }}
.dr-done span {{ font-family:var(--mono); font-size:13px; color:var(--muted); }}

@media (prefers-reduced-motion:no-preference) {{ html {{ scroll-behavior:smooth; }} }}
@media print {{
  .controls, .pip, .e-cue, .e-due, .rv, .dr {{ display:none; }}
  header {{ background-image:none; }}
  body {{ background:#fff; color:#000; }}
  .day, .e {{ break-inside:avoid; }}
  .e {{ padding-right:22px; }}
  .e.st-mature .gw {{ color:#000; }}
}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="inner">
    <h1>N3 文法 <span class="lat">120 PATTERNS · 6 GROUPS · JLPT N3 GRAMMAR</span></h1>
  </div>
  <p class="sub">Every grammar point from the drip deck. Formation rule in
    <span class="k-seal">vermilion</span>, English &amp; Nepali meanings, the 型 frame,
    three example sentences, nuance notes &mdash; click a card with a
    <b>+</b> for its usage note. <b>Review</b> walks SRS due items;
    <b>Drill</b> cycles cloze → discrimination → contrast for each pattern.</p>
  <div class="dcount">EXAM 2026·12·03 &nbsp;<b id="dcount-days">D&minus;?</b></div>
</header>

<nav class="controls">
  <div class="search">
    <input id="q" type="search" placeholder="search grammar · meaning · formation" autocomplete="off" spellcheck="false">
    <button class="x" id="q-x" type="button" aria-label="clear search" hidden>&times;</button>
  </div>
  <button class="review-btn" id="review-open">Review</button>
  <button class="drill-btn"  id="drill-open">Drill</button>
  <div class="stat" title="mature = box 5+ · due = ready to review">
    <span class="bar"><i id="stat-fill"></i></span>
    <span><b class="s-mat" id="s-mat">0</b> mature &middot; <b id="s-lrn">0</b> learning &middot;
      <b id="s-new">{total}</b> new &middot; <b class="s-due" id="s-due">0</b> due</span>
  </div>
  <select class="filter" id="filter" aria-label="filter">
    <option value="all">all patterns</option>
    <option value="due">due now</option>
    <option value="new">new</option>
    <option value="learning">learning</option>
    <option value="mature">mature</option>
  </select>
  <select class="filter" id="cat-filter" aria-label="filter by category">
    {cat_opts}
  </select>
  <div class="toggles ml-auto">
    <button id="t-mn"  aria-pressed="false">hide meanings</button>
    <button id="t-pt"  aria-pressed="false">hide 型</button>
    <button id="t-rd"  aria-pressed="false">hide readings</button>
    <button id="t-dark" aria-pressed="false">dark</button>
  </div>
  <div class="navdays">{nav_links}</div>
</nav>

<main>
<p class="nores" id="nores">No pattern matches that.</p>
{cards_html}
</main>

<footer>
  N3 文法 120 &nbsp;&middot;&nbsp; {total} patterns &nbsp;&middot;&nbsp; generated {today}
  &nbsp;&middot;&nbsp; <b>/</b> search &nbsp; <b>R</b> review &nbsp; <b>D</b> drill &nbsp;
  click a card for its note, click the ring to promote a pattern &nbsp;&middot;&nbsp;
  SRS boxes 0-6 at 1&middot;2&middot;4&middot;8&middot;16&middot;35-day steps, saved in localStorage.
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

<!-- review overlay -->
<div class="rv" id="rv" hidden>
  <div class="rv-card" role="dialog" aria-modal="true" aria-label="review">
    <div class="rv-top">
      <span id="rv-count">0 / 0</span>
      <button id="rv-close" type="button">esc</button>
    </div>
    <div id="rv-stage"></div>
  </div>
</div>

<!-- drill overlay -->
<div class="dr" id="dr" hidden>
  <div class="dr-card" role="dialog" aria-modal="true" aria-label="drill">
    <div class="dr-top">
      <span id="dr-count">0 / 0</span>
      <span id="dr-step-badge" class="dr-step-badge">CLOZE</span>
      <button id="dr-close" type="button">esc</button>
    </div>
    <div id="dr-stage"></div>
  </div>
</div>

<script>
(function(){{
'use strict';

// ── data ────────────────────────────────────────────────────────────────────
const GRAMMAR = {grammar_json};
const SRS_KEY = 'n3grs';
const STEPS   = [1,2,4,8,16,35]; // days per box

// ── SRS helpers ─────────────────────────────────────────────────────────────
function loadSRS() {{
  try {{ return JSON.parse(localStorage.getItem(SRS_KEY)||'{{}}'); }}
  catch(e) {{ return {{}}; }}
}}
function saveSRS(d) {{ localStorage.setItem(SRS_KEY, JSON.stringify(d)); }}

let SRS = loadSRS();

function getCard(gid) {{
  return SRS[gid] || {{box:0, next:0}};
}}
function isDue(gid) {{
  const c = getCard(gid);
  return c.box === 0 || Date.now() >= c.next;
}}
function promote(gid, rating) {{
  const c = getCard(gid);
  let box = c.box;
  if (rating === 'again') {{ box = Math.max(0, box-1); }}
  else if (rating === 'good') {{ box = Math.min(6, box+1); }}
  else {{ box = Math.min(6, box+2); }}
  const days = STEPS[Math.min(box, STEPS.length-1)];
  SRS[gid] = {{box, next: Date.now() + days*864e5}};
  saveSRS(SRS);
  return box;
}}

// ── SRS state to DOM ─────────────────────────────────────────────────────────
function stateClass(box) {{
  if (box === 0) return 'st-new';
  if (box < 5)  return 'st-learning';
  return 'st-mature';
}}
function applyStates() {{
  let mat=0, lrn=0, nw=0, due=0;
  document.querySelectorAll('.e[data-cat]').forEach(el => {{
    const gid = el.querySelector('.pip')?.dataset.id || '';
    const c = getCard(gid);
    el.classList.remove('st-new','st-learning','st-mature','is-due');
    el.classList.add(stateClass(c.box));
    if (isDue(gid) && c.box > 0) el.classList.add('is-due');
    if (c.box === 0) nw++;
    else if (c.box < 5) lrn++;
    else mat++;
    if (isDue(gid) && c.box > 0) due++;
  }});
  document.getElementById('s-mat').textContent = mat;
  document.getElementById('s-lrn').textContent = lrn;
  document.getElementById('s-new').textContent = nw;
  document.getElementById('s-due').textContent = due;
  const total = mat+lrn+nw;
  const fill  = total ? Math.round(mat/total*100) : 0;
  document.getElementById('stat-fill').style.width = fill+'%';
}}

// ── pip click ────────────────────────────────────────────────────────────────
document.querySelectorAll('.pip').forEach(btn => {{
  btn.addEventListener('click', e => {{
    e.stopPropagation();
    const gid = btn.dataset.id;
    promote(gid, 'good');
    applyStates();
  }});
}});

// ── card toggle ──────────────────────────────────────────────────────────────
document.querySelectorAll('.e.has-note').forEach(el => {{
  el.querySelector('.e-body')?.addEventListener('click', ()=> el.classList.toggle('open'));
}});

// ── search ───────────────────────────────────────────────────────────────────
const qEl = document.getElementById('q');
const qx  = document.getElementById('q-x');
const noresEl = document.getElementById('nores');

function doSearch() {{
  const v = qEl.value.trim().toLowerCase();
  qx.hidden = !v;
  let any = false;
  document.querySelectorAll('.e').forEach(el => {{
    if (!v) {{ el.classList.remove('hit'); any = true; return; }}
    const text = el.textContent.toLowerCase();
    const show = text.includes(v);
    el.classList.toggle('hit', !show);
    if (show) any = true;
  }});
  noresEl.classList.toggle('show', !any);
  // hide empty sections
  document.querySelectorAll('.day').forEach(sec => {{
    const vis = [...sec.querySelectorAll('.e')].some(e => !e.classList.contains('hit'));
    sec.classList.toggle('empty', !vis);
  }});
}}
qEl.addEventListener('input', doSearch);
qx.addEventListener('click', ()=>{{ qEl.value=''; doSearch(); qEl.focus(); }});

// ── filter selects ────────────────────────────────────────────────────────────
document.getElementById('filter').addEventListener('change', e => {{
  document.body.dataset.filter = e.target.value;
}});
document.getElementById('cat-filter').addEventListener('change', e => {{
  const cat = e.target.value;
  document.querySelectorAll('.e').forEach(el => {{
    if (!cat) {{ el.classList.remove('hit'); }}
    else {{ el.classList.toggle('hit', el.dataset.cat !== cat); }}
  }});
  noresEl.classList.toggle('show',
    !![...document.querySelectorAll('.e')].every(e => e.classList.contains('hit')));
  document.querySelectorAll('.day').forEach(sec => {{
    const vis = [...sec.querySelectorAll('.e')].some(e => !e.classList.contains('hit'));
    sec.classList.toggle('empty', !vis);
  }});
}});

// ── toggles ──────────────────────────────────────────────────────────────────
function mkToggle(btnId, bodyClass) {{
  document.getElementById(btnId)?.addEventListener('click', function() {{
    const on = this.getAttribute('aria-pressed') === 'true';
    this.setAttribute('aria-pressed', String(!on));
    document.body.classList.toggle(bodyClass, !on);
  }});
}}
mkToggle('t-mn',  'no-mn');
mkToggle('t-pt',  'no-pt');
mkToggle('t-rd',  'no-rd');
document.getElementById('t-dark')?.addEventListener('click', function() {{
  const on = this.getAttribute('aria-pressed') === 'true';
  this.setAttribute('aria-pressed', String(!on));
  document.documentElement.dataset.theme = on ? '' : 'dark';
}});

// ── keyboard shortcuts ────────────────────────────────────────────────────────
document.addEventListener('keydown', e => {{
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === '/' || e.key === 'f') {{ e.preventDefault(); qEl.focus(); }}
  if (e.key === 'r' || e.key === 'R') document.getElementById('review-open')?.click();
  if (e.key === 'd' || e.key === 'D') document.getElementById('drill-open')?.click();
  if (e.key === 'Escape') {{
    document.getElementById('rv-close')?.click();
    document.getElementById('dr-close')?.click();
  }}
}});

// ── exam countdown ────────────────────────────────────────────────────────────
(function() {{
  const exam = new Date('2026-12-03');
  const diff = Math.ceil((exam - Date.now()) / 864e5);
  document.getElementById('dcount-days').textContent = 'D−' + diff;
}})();

// ── review ───────────────────────────────────────────────────────────────────
const rvEl     = document.getElementById('rv');
const rvCount  = document.getElementById('rv-count');
const rvStage  = document.getElementById('rv-stage');
const rvOpen   = document.getElementById('review-open');
const rvClose  = document.getElementById('rv-close');

let rvQueue = [], rvIdx = 0, rvRevealed = false;

function buildRvCard(gr) {{
  const gid = gr.g;
  rvRevealed = false;
  const exHtml = (gr.ex||[]).map((ex,i)=>
    `<div class="ex"><span class="ex-no">${{i+1}}</span>
     <span class="ex-jp">${{esc(ex.j)}}</span>
     <span class="ex-rd">${{esc(ex.r)}}</span>
     <span class="ex-en">${{esc(ex.e)}}</span></div>`).join('');
  return `
    <div class="rv-front">${{esc(gr.g)}}</div>
    <div class="rv-hint">${{esc(gr.f)}}</div>
    <div class="rv-back" hidden id="rv-back">
      <div class="mn">${{esc(gr.m)}}${{gr.mn?`<span class="mnne">${{esc(gr.mn)}}</span>`:''}}
      </div>
      ${{gr.fr?`<div class="pt"><span class="pt-k">型</span>${{esc(gr.fr)}}</div>`:''}}
      <div class="ex-grid">${{exHtml}}</div>
      ${{gr.nu||gr.sp||gr.co||gr.tr?`<div class="more" style="display:block">
        ${{gr.nu?`<p class="nt"><span class="nt-k">nuance</span> ${{esc(gr.nu)}}</p>`:''}}
        ${{gr.sp?`<p class="nt"><span class="nt-k">spoken</span> ${{esc(gr.sp)}}</p>`:''}}
        ${{gr.co?`<p class="nt"><span class="nt-k">contrast</span> ${{esc(gr.co)}}</p>`:''}}
        ${{gr.tr?`<p class="nt"><span class="nt-k">trap</span> ${{esc(gr.tr)}}</p>`:''}}
      </div>`:''}}
    </div>
    <div class="rv-actions" id="rv-actions">
      <button class="reveal" id="rv-reveal">reveal</button>
    </div>`;
}}

function showRv(idx) {{
  if (idx >= rvQueue.length) {{
    rvStage.innerHTML = `<div class="rv-done"><p>Session done ✓</p>
      <span>${{rvQueue.length}} patterns reviewed</span></div>`;
    rvCount.textContent = '';
    return;
  }}
  rvCount.textContent = `${{idx+1}} / ${{rvQueue.length}}`;
  const gr = rvQueue[idx];
  rvStage.innerHTML = buildRvCard(gr);
  document.getElementById('rv-reveal').addEventListener('click', revealRv);
}}

function revealRv() {{
  document.getElementById('rv-back').hidden = false;
  const acts = document.getElementById('rv-actions');
  acts.innerHTML = `
    <button class="g-again">again</button>
    <button class="g-good">good</button>
    <button class="g-easy">easy</button>`;
  acts.querySelectorAll('button').forEach(btn => {{
    btn.addEventListener('click', () => {{
      promote(rvQueue[rvIdx].g, btn.textContent.trim());
      applyStates();
      rvIdx++;
      showRv(rvIdx);
    }});
  }});
}}

rvOpen.addEventListener('click', () => {{
  SRS = loadSRS();
  rvQueue = GRAMMAR.filter(g => isDue(g.g));
  if (!rvQueue.length) return;
  rvIdx = 0;
  rvEl.hidden = false;
  showRv(0);
}});
rvClose.addEventListener('click', () => {{ rvEl.hidden = true; }});
rvEl.addEventListener('click', e => {{ if (e.target === rvEl) rvEl.hidden = true; }});

// ── drill ─────────────────────────────────────────────────────────────────────
const drEl     = document.getElementById('dr');
const drCount  = document.getElementById('dr-count');
const drBadge  = document.getElementById('dr-step-badge');
const drStage  = document.getElementById('dr-stage');
const drOpen   = document.getElementById('drill-open');
const drClose  = document.getElementById('dr-close');

// Drill state
let drQueue = []; // array of grammar records with cloze_front set
let drIdx   = 0;
let drStep  = 0;  // 0=cloze, 1=disc, 2=contrast
let drStepsDone = 0; // how many steps done for current pattern (to decide when to show SRS)

const STEP_LABELS = ['CLOZE','DISCRIMINATE','CONTRAST'];

function nextDrillPattern() {{
  drIdx++;
  drStep = 0;
  drStepsDone = 0;
  showDrill();
}}

function showDrill() {{
  if (drIdx >= drQueue.length) {{
    drStage.innerHTML = `<div class="dr-done"><p>Drill complete ✓</p>
      <span>${{drQueue.length}} patterns drilled</span></div>`;
    drCount.textContent = '';
    drBadge.textContent = '';
    return;
  }}
  const gr = drQueue[drIdx];
  drCount.textContent = `${{drIdx+1}} / ${{drQueue.length}}`;

  // Determine which steps are available for this pattern
  const hasCloze    = !!(gr.cl && gr.cl[0]);
  const hasDisc     = !!(gr.dq && gr.dq.f);
  const hasContrast = !!(gr.cq && gr.cq.f);
  const stepsAvail  = [hasCloze, hasDisc, hasContrast];

  // Find the next available step from drStep
  while (drStep < 3 && !stepsAvail[drStep]) drStep++;
  if (drStep >= 3) {{
    // show SRS buttons
    showDrSRS(gr);
    return;
  }}

  drBadge.textContent = STEP_LABELS[drStep];

  if (drStep === 0) showDrCloze(gr);
  else if (drStep === 1) showDrDisc(gr);
  else showDrContrast(gr);
}}

function showDrCloze(gr) {{
  drStage.innerHTML = `
    <div class="dr-grammar">${{esc(gr.g)}}</div>
    <div style="font-family:var(--mono);font-size:.78rem;color:var(--muted);margin-bottom:8px">${{esc(gr.f)}}</div>
    <p style="font-size:.88rem;color:var(--muted);margin:0 0 4px">Fill in the blank:</p>
    <div class="dr-question">${{esc(gr.cl[0]).replace(/＿+/g,'<span class="dr-blank">＿＿＿</span>')}}</div>
    <div class="dr-answer" id="dr-ans">
      <span class="ans-word">${{esc(gr.cl[1])}}</span>
    </div>
    <div class="dr-actions">
      <button class="dr-reveal" id="dr-reveal-btn">reveal</button>
    </div>`;
  document.getElementById('dr-reveal-btn').addEventListener('click', () => {{
    document.getElementById('dr-ans').classList.add('show');
    document.getElementById('dr-reveal-btn').remove();
    document.querySelector('.dr-actions').innerHTML +=
      `<button class="dr-next" id="dr-next-btn">next →</button>`;
    document.getElementById('dr-next-btn').addEventListener('click', () => {{
      drStep++; drStepsDone++;
      // skip unavailable
      while (drStep < 3 && ![!!(drQueue[drIdx].cl&&drQueue[drIdx].cl[0]),
             !!(drQueue[drIdx].dq&&drQueue[drIdx].dq.f),
             !!(drQueue[drIdx].cq&&drQueue[drIdx].cq.f)][drStep]) drStep++;
      if (drStep >= 3) {{ showDrSRS(drQueue[drIdx]); }}
      else {{ showDrill(); }}
    }});
  }});
}}

function showDrDisc(gr) {{
  const opts = gr.dq.o || [];
  const corr = gr.dq.a || '';
  drStage.innerHTML = `
    <div class="dr-grammar">${{esc(gr.g)}}</div>
    <p style="font-size:.88rem;color:var(--muted);margin:0 0 4px">Choose the correct option:</p>
    <div class="dr-question">${{esc(gr.dq.f)}}</div>
    <div class="dr-opts">${{opts.map(o =>
      `<button data-opt="${{esc(o)}}">${{esc(o)}}</button>`).join('')}}</div>
    <div class="dr-why" id="dr-why"></div>
    <div class="dr-actions" id="dr-disc-acts"></div>`;

  document.querySelectorAll('.dr-opts button').forEach(btn => {{
    btn.addEventListener('click', function() {{
      document.querySelectorAll('.dr-opts button').forEach(b => b.disabled = true);
      const chosen = this.dataset.opt;
      // match by letter prefix (e.g. "B ようになった")
      const corrLetter = corr.trim().split(' ')[0];
      const chosenLetter = chosen.trim().split(' ')[0];
      const ok = chosenLetter === corrLetter;
      this.classList.add(ok ? 'correct' : 'wrong');
      if (!ok) {{
        // highlight correct
        document.querySelectorAll('.dr-opts button').forEach(b => {{
          if (b.dataset.opt.trim().split(' ')[0] === corrLetter) b.classList.add('correct');
        }});
      }}
      const whyEl = document.getElementById('dr-why');
      whyEl.textContent = gr.dq.w + (gr.dq.wr ? ' · ' + gr.dq.wr : '');
      whyEl.classList.add('show');
      document.getElementById('dr-disc-acts').innerHTML =
        `<button class="dr-next" id="dr-next-btn">next →</button>`;
      document.getElementById('dr-next-btn').addEventListener('click', () => {{
        drStep++; drStepsDone++;
        while (drStep < 3 && ![!!(drQueue[drIdx].cl&&drQueue[drIdx].cl[0]),
               !!(drQueue[drIdx].dq&&drQueue[drIdx].dq.f),
               !!(drQueue[drIdx].cq&&drQueue[drIdx].cq.f)][drStep]) drStep++;
        if (drStep >= 3) {{ showDrSRS(drQueue[drIdx]); }}
        else {{ showDrill(); }}
      }});
    }});
  }});
}}

function showDrContrast(gr) {{
  drStage.innerHTML = `
    <div class="dr-grammar">${{esc(gr.g)}}</div>
    <p style="font-size:.88rem;color:var(--muted);margin:0 0 4px">Contrast fill-in:</p>
    <div class="dr-question">${{esc(gr.cq.f)}}</div>
    <div class="dr-answer" id="dr-ans">
      <span class="ans-word">${{esc(gr.cq.a)}}</span>
      ${{gr.cq.n?`<div class="dr-note">${{esc(gr.cq.n)}}</div>`:''}}
    </div>
    <div class="dr-actions">
      <button class="dr-reveal" id="dr-reveal-btn">reveal</button>
    </div>`;
  document.getElementById('dr-reveal-btn').addEventListener('click', () => {{
    document.getElementById('dr-ans').classList.add('show');
    document.getElementById('dr-reveal-btn').remove();
    document.querySelector('.dr-actions').innerHTML +=
      `<button class="dr-next" id="dr-next-btn">SRS →</button>`;
    document.getElementById('dr-next-btn').addEventListener('click', () => {{
      drStep++; drStepsDone++;
      showDrSRS(drQueue[drIdx]);
    }});
  }});
}}

function showDrSRS(gr) {{
  drBadge.textContent = 'SRS';
  drStage.innerHTML = `
    <div class="dr-grammar">${{esc(gr.g)}}</div>
    <p style="font-family:var(--serif);font-size:.95rem;color:var(--muted);margin:12px 0">
      How well did you know this pattern?</p>
    <div class="dr-actions">
      <button class="g-again">again</button>
      <button class="g-good">good</button>
      <button class="g-easy">easy</button>
    </div>`;
  document.querySelectorAll('.dr-actions button').forEach(btn => {{
    btn.addEventListener('click', () => {{
      promote(gr.g, btn.textContent.trim());
      applyStates();
      drIdx++; drStep = 0; drStepsDone = 0;
      showDrill();
    }});
  }});
}}

drOpen.addEventListener('click', () => {{
  // All patterns that have at least a cloze_front
  drQueue = GRAMMAR.filter(g => g.cl && g.cl[0]);
  if (!drQueue.length) return;
  drIdx = 0; drStep = 0; drStepsDone = 0;
  drEl.hidden = false;
  showDrill();
}});
drClose.addEventListener('click', () => {{ drEl.hidden = true; }});
drEl.addEventListener('click', e => {{ if (e.target === drEl) drEl.hidden = true; }});

// ── escape helper ────────────────────────────────────────────────────────────
function esc(s) {{
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

// ── footer IO ────────────────────────────────────────────────────────────────
document.getElementById('io-export').addEventListener('click', () => {{
  document.getElementById('io').value = JSON.stringify(loadSRS(), null, 2);
}});
document.getElementById('io-import').addEventListener('click', () => {{
  try {{
    const d = JSON.parse(document.getElementById('io').value);
    saveSRS(d); SRS = d; applyStates();
  }} catch(e) {{ alert('Invalid JSON'); }}
}});
document.getElementById('io-reset').addEventListener('click', () => {{
  if (!confirm('Reset all SRS progress?')) return;
  saveSRS({{}}); SRS = {{}}; applyStates();
}});

// ── init ──────────────────────────────────────────────────────────────────────
applyStates();
}})();
</script>
</body>
</html>
"""

OUT_FILE.write_text(html, encoding="utf-8")
size = OUT_FILE.stat().st_size
words = len(html.split())
print(f"Written: {OUT_FILE}")
print(f"Size:    {size:,} bytes ({size/1024:.1f} KB)")
print(f"Words:   {words:,}")
print(f"Records: {total}")
