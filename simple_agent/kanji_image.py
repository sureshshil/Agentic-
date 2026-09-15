"""Renders a single kanji character as a large, high-contrast PNG for
Telegram delivery. kanji_drip.py sends this via telegram_bot.send_photo
instead of relying on the Telegram client's own font for the bare
Unicode character embedded in message text - that renders thin and
small (especially on mobile), which is the actual readability problem
this module fixes.

The font (Noto Sans JP, a variable font) isn't committed to the repo -
same "fetch once, cache locally" pattern as
artifact_creation/build_kanjivg_strokes.py's KanjiVG downloads. It's
pulled from the google/fonts repo on first use into FONT_CACHE and
reused after; FONT_CACHE is gitignored like .kanjivg_cache/.
"""

import os
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

_SIMPLE_AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_CACHE = Path(_SIMPLE_AGENT_DIR) / "vocab_artifacts" / ".fonts_cache"
FONT_PATH = FONT_CACHE / "NotoSansJP-Variable.ttf"
FONT_URL = "https://github.com/google/fonts/raw/main/ofl/notosansjp/NotoSansJP%5Bwght%5D.ttf"

CANVAS_SIZE = 640
BG_COLOR = "#fafaf7"
INK_COLOR = "#1a1a1a"
FONT_SIZE = 460
FONT_WEIGHT = 700  # Bold - stays legible at Telegram's small chat-thumbnail size


def _ensure_font() -> Path:
    """Downloads Noto Sans JP (variable, ~9MB) into FONT_CACHE on first
    call; every later call reuses the cached file. Written via a temp
    file + rename so a request interrupted mid-download can't leave a
    truncated file that later calls mistake for a valid cache hit."""
    if FONT_PATH.exists():
        return FONT_PATH
    FONT_CACHE.mkdir(parents=True, exist_ok=True)
    resp = requests.get(FONT_URL, timeout=30)
    resp.raise_for_status()
    tmp = FONT_PATH.with_suffix(".tmp")
    tmp.write_bytes(resp.content)
    tmp.rename(FONT_PATH)
    return FONT_PATH


def _load_font(size: int = FONT_SIZE, weight: int = FONT_WEIGHT):
    font = ImageFont.truetype(str(_ensure_font()), size)
    if hasattr(font, "set_variation_by_axes"):
        try:
            font.set_variation_by_axes([weight])
        except Exception:
            pass  # non-variable fallback font - just use it at its default weight
    return font


def render_kanji_png(kanji: str, font=None) -> bytes:
    """One kanji character -> PNG bytes: a large bold glyph, centered by
    its actual ink bounding box (not font metrics, which leave CJK
    glyphs looking off-center) on a plain card. `font` is normally left
    None (loads/caches Noto Sans JP); tests inject a stand-in font so
    they stay offline."""
    font = font or _load_font()

    img = Image.new("RGB", (CANVAS_SIZE, CANVAS_SIZE), BG_COLOR)
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), kanji, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (CANVAS_SIZE - w) / 2 - bbox[0]
    y = (CANVAS_SIZE - h) / 2 - bbox[1]
    draw.text((x, y), kanji, font=font, fill=INK_COLOR)

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
