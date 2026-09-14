import json
from pathlib import Path

base = Path(__file__).resolve().parent.parent / "vocab_artifacts"
urls = json.loads((base / "vocab_audio_urls.json").read_text(encoding="utf-8"))
b64  = json.loads((base / "vocab_word_audio.json").read_text(encoding="utf-8"))

added = 0
for word, val in b64.items():
    if word not in urls:
        urls[word] = val
        added += 1

(base / "vocab_audio_urls.json").write_text(json.dumps(urls, ensure_ascii=False), encoding="utf-8")
print(f"merged: {len(urls)} total ({added} base64 entries added to existing URL entries)")
