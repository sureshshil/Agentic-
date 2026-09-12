# N3 vocab study artifacts

Generated from the curated `../N3_vocab_batch*.tsv` files (400 words,
16 days x 25). Regenerate any of these with the scripts in `../scheduled/`.

| File | What | Regenerate |
|---|---|---|
| `n3_vocab_flashcards.html` | Offline flip-deck: filter by day / word type, JP↔EN, shuffle, local "known" marks | `scheduled/build_vocab_flashcards.py <out>` |
| `n3_vocab_anki.txt` | Anki import (File → Import). 14 fields incl. furigana + 3 example sentences + note; tags `N3::day01` | same script |
| `n3_vocab_reference.html` | Print-ready wall-chart: reading, gloss, collocation frame (型), one example; hide-meanings self-quiz; Leitner review; per-word ✎ animated stroke order. Published as an Artifact. | `scheduled/build_vocab_infographic.py <out.html>` |
| `kanjivg_strokes.json` | Per-character stroke paths + stroke-number positions (KanjiVG) for every kanji/kana in the words. Embedded by the reference build for the ✎ stroke-order animation. Raw SVGs cached in `.kanjivg_cache/`. | `scheduled/build_kanjivg_strokes.py` |
| `audio/n3_dayNN.mp3` | Per-day listening track (edge-tts). Per word: JP word → reading → 2 sentences, then EN meaning + first sentence. ~2.5 min each, ~37 MB total | `scheduled/build_vocab_audio.py <out_dir> [day_from] [day_to]` (resumable) |

Deps beyond the repo's usual: `edge-tts` (audio only) — installed in `../myenv`.
`build_kanjivg_strokes.py` fetches from raw.githubusercontent.com on first run,
then works offline from `.kanjivg_cache/` (KanjiVG, CC BY-SA 3.0).

The audio MP3s are large binaries; decide whether to commit or gitignore.
