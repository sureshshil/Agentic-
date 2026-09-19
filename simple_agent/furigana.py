"""Inline furigana for Telegram cards.

Telegram has no <ruby>, so every deck shows readings as Anki-style inline
brackets - 部屋[へや]を片付[かたづ]けて. Curated vocab sentences and kanji
examples already carry them; everything else (grammar examples, vocab
headwords, kanji component/hint text, all LLM practice blocks) doesn't.
`annotate` adds "[reading]" after every kanji run that lacks one, leaving
already-bracketed runs untouched, so it's safe to apply to any text.
"""
import re

try:
    import pykakasi
    _KAKASI = pykakasi.kakasi()
except ImportError:  # degrade to no-op rather than break a cron push
    _KAKASI = None

_KANJI = "一-鿿々ヶ"
_RUN_RE = re.compile(rf"([{_KANJI}]+)(\[[^\]]*\])?")
_KANA_TAIL_RE = re.compile(r"[぀-ゟ]+$")
_KATA_TO_HIRA = {c: chr(c - 0x60) for c in range(0x30a1, 0x30f7)}


def _hira(s: str) -> str:
    return s.translate(_KATA_TO_HIRA)


def _convert(text: str) -> list:
    return [(i["orig"], _hira(i["hira"])) for i in _KAKASI.convert(text)]


_HAS_KANJI_RE = re.compile(f"[{_KANJI}]")


def _annotate_chunk(chunk: str) -> str:
    """Annotate a bracket-free stretch of text."""
    out = []
    for orig, hira in _convert(chunk):
        if not _HAS_KANJI_RE.search(orig):
            out.append(orig)
            continue
        lead = re.match(rf"^[^{_KANJI}]*", orig).group(0)
        body = orig[len(lead):]
        tail_m = _KANA_TAIL_RE.search(body)
        tail = tail_m.group(0) if tail_m else ""
        stem = body[: len(body) - len(tail)]
        reading = hira[len(_hira(lead)):]
        if stem in ("今日", "今晩") and tail == "は":  # kakasi reads it こんにちは
            reading = "".join(h for _, h in _convert(stem))
        elif tail and reading.endswith(_hira(tail)):
            reading = reading[: len(reading) - len(tail)]
        elif tail:  # tail doesn't match the reading: re-read the kanji alone
            reading = "".join(h for _, h in _convert(stem))
        out.append(f"{lead}{stem}[{reading}]{tail}" if stem and reading else orig)
    return "".join(out)


def annotate(text: str) -> str:
    """`text` with "[reading]" added after each bare kanji run."""
    if not text or _KAKASI is None or not _HAS_KANJI_RE.search(text):
        return text
    pieces, pos = [], 0
    for m in _RUN_RE.finditer(text):
        if m.group(2):  # already annotated - keep verbatim
            pieces.append(_annotate_chunk(text[pos:m.start()]))
            pieces.append(m.group(0))
            pos = m.end()
    pieces.append(_annotate_chunk(text[pos:]))
    # kakasi always reads a bare 方 as ほう; after a verb stem (働き方, 食べ方)
    # it's かた. の/た/い/な/る before it means "the ~ side" (ほう) - leave those.
    return re.sub(r"(?<=[\u3041-\u3093])(?<![のたいなる])方\[ほう\]", "方[かた]", "".join(pieces))
