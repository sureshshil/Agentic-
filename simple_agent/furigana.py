"""Inline furigana for Telegram cards.

Telegram has no <ruby>, so every deck shows readings as Anki-style inline
brackets - 部屋[へや]を片付[かたづ]けて. Curated vocab sentences and kanji
examples already carry them; everything else (grammar examples, vocab
headwords, kanji component/hint text, and any LLM practice block text the
model didn't already bracket itself - see llm_enrich.py) doesn't.
`annotate` adds "[reading]" after every kanji run that lacks one, leaving
already-bracketed runs untouched, so it's safe to apply to any text.

Uses fugashi (a MeCab wrapper), a real morphological tagger, rather than a
static kanji-string dictionary like pykakasi: it tags each word's part of
speech and reads it via that word's own dictionary entry, so e.g. 厳しい
(keiyoushi, "strict") correctly reads きびしい instead of a same-spelled but
rarer noun/adjective reading a plain string lookup can't tell apart. Not
perfect - a handful of nouns (方 especially) still have genuinely
context-dependent readings (かた as an honorific "person" vs ほう as "way/
direction") that even POS tagging can't fully resolve - but it needs far
fewer hand-patches than a dictionary-only lookup did.
"""
import re

try:
    import fugashi
    _TAGGER = fugashi.Tagger()
except ImportError:  # degrade to no-op rather than break a cron push
    _TAGGER = None

_KANJI = "一-鿿々ヶ"
_RUN_RE = re.compile(rf"([{_KANJI}]+)(\[[^\]]*\])?")
_KANA_TAIL_RE = re.compile(r"[぀-ゟ]+$")
_KATA_TO_HIRA = {c: chr(c - 0x60) for c in range(0x30a1, 0x30f7)}


def _hira(s: str) -> str:
    return s.translate(_KATA_TO_HIRA)


def _convert(text: str) -> list:
    """[(surface, reading_or_None)] for each token - reading is None for a
    word fugashi's dictionary doesn't know (feature.kana == "*"), so the
    caller can leave it unbracketed rather than bracket it with garbage."""
    out = []
    for word in _TAGGER(text):
        kana = word.feature.kana
        out.append((word.surface, _hira(kana) if kana and kana != "*" else None))
    return out


_HAS_KANJI_RE = re.compile(f"[{_KANJI}]")


def _annotate_chunk(chunk: str) -> str:
    """Annotate a bracket-free stretch of text."""
    out = []
    for orig, hira in _convert(chunk):
        if not _HAS_KANJI_RE.search(orig) or hira is None:
            out.append(orig)
            continue
        lead = re.match(rf"^[^{_KANJI}]*", orig).group(0)
        body = orig[len(lead):]
        tail_m = _KANA_TAIL_RE.search(body)
        tail = tail_m.group(0) if tail_m else ""
        stem = body[: len(body) - len(tail)]
        reading = hira[len(_hira(lead)):]
        if tail and reading.endswith(_hira(tail)):
            reading = reading[: len(reading) - len(tail)]
        elif tail:  # tail doesn't match the reading: re-read the kanji alone
            restem = _convert(stem)
            reading = "".join(h or s for s, h in restem)
        out.append(f"{lead}{stem}[{reading}]{tail}" if stem and reading else orig)
    return "".join(out)


def annotate(text: str) -> str:
    """`text` with "[reading]" added after each bare kanji run."""
    if not text or _TAGGER is None or not _HAS_KANJI_RE.search(text):
        return text
    pieces, pos = [], 0
    for m in _RUN_RE.finditer(text):
        if m.group(2):  # already annotated - keep verbatim
            pieces.append(_annotate_chunk(text[pos:m.start()]))
            pieces.append(m.group(0))
            pos = m.end()
    pieces.append(_annotate_chunk(text[pos:]))
    return "".join(pieces)
