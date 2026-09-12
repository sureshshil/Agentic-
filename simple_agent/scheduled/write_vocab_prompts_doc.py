"""One-off: write the flashcard / audio / infographic prompt library into
the 'vocabn3prompts' Google Doc.

Same mechanism as vocab_sinks.gdoc_append - service-account token + the
Docs REST batchUpdate - only the target doc id differs. The doc must be
shared with the service account's client_email as Editor (already done).

Run:  myenv/bin/python scheduled/write_vocab_prompts_doc.py
Idempotent-ish: it appends, so run once. Re-running adds a second copy.
"""

import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from vocab_sinks import _gdoc_access_token, _paragraphs_to_requests  # noqa: E402

DOC_ID = "1LFX_wreziJXMfXhdKohpHAEY5LSmxT9D3hRrDJ2e380"
SA_JSON = os.environ.get(
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    os.path.join(os.path.dirname(__file__), "..", "deploy",
                 "quixotic-sunset-471304-g9-31388d371f18.json"),
)
_TIMEOUT = 15

H1, H2, H3, P = "HEADING_1", "HEADING_2", "HEADING_3", None

# (text, named_style_or_None). Prompt bodies are one paragraph per line so
# they read cleanly in the doc; select the block and copy to use one.
PARAS = [
    ("N3 Vocab — Study-Material Prompts", H1),
    ("Copy-paste prompts for turning the “JLPT Japanese Vocabulary Log” "
     "Google Doc (the one the vocab drip appends to every couple of hours, used "
     "as a NotebookLM source) into flashcards, audio, and infographics.", P),
    ("", P),

    ("Setup (once)", H2),
    ("1. NotebookLM → your notebook → Sources → Add source → "
     "Google Drive → pick “JLPT Japanese Vocabulary Log”.", P),
    ("2. After the drip adds new words, open the source’s ⋮ menu → "
     "Sync with Drive so NotebookLM re-reads the latest batch.", P),
    ("3. In Studio, click the format (Flashcards / Audio Overview / Video "
     "Overview / Quiz / Reports), hit Customize, and paste a prompt below.", P),
    ("", P),

    ("Doc structure the prompts assume", H2),
    ("• H1 “JLPT Japanese Vocabulary Log”, then a preamble paragraph.", P),
    ("• Repeated batch markers: “Added YYYY-MM-DD HH:MM UTC · N3”.", P),
    ("• One H2 per word: “<word> — <kana reading>”.", P),
    ("• Under each word: “Meaning:”, “Part of speech:”, "
     "“Particle patterns (助詞):”, then “Examples:” "
     "with numbered items, each in normal Japanese / kana / English.", P),
    ("Scope line: every prompt starts with one. Keep “Use ONLY the entries "
     "under the most recent 'Added … UTC' marker” for the daily drip; "
     "swap in “Use ALL entries in the document” for a full review.", P),
    ("", P),

    ("1. Flashcards", H2),
    ("NotebookLM Studio → Flashcards → Customize. Or paste 1b in chat "
     "for an Anki / Quizlet CSV.", P),
    ("Make one flashcard per vocabulary entry.", P),
    ("Scope: use ONLY the entries under the most recent \"Added ... UTC\" marker.", P),
    ("Front: the word in normal Japanese (with kanji if it has any), nothing else.", P),
    ("Back, on separate lines:", P),
    ("  - reading in kana", P),
    ("  - English meaning", P),
    ("  - part of speech", P),
    ("  - particle pattern (助詞), or \"-\" if none is listed", P),
    ("  - one example sentence: Japanese, then its English on the next line", P),
    ("Keep the back to five short lines. Do not invent words, readings, or "
     "sentences that are not in the source. Order the cards as they appear in "
     "the document.", P),
    ("", P),

    ("1b. Flashcards as CSV (paste in chat)", H3),
    ("From the entries under the most recent \"Added ... UTC\" marker, output a "
     "plain CSV with a header row and one row per word. No prose before or after.", P),
    ("Columns: word (normal Japanese), reading (kana), meaning (English), pos "
     "(part of speech), particle (助詞 pattern or empty), example_jp "
     "(first example in normal Japanese), example_kana (that sentence in kana), "
     "example_en (that sentence in English).", P),
    ("Quote every field with double quotes; escape internal quotes by doubling "
     "them. Use only content present in the source.", P),
    ("", P),

    ("2. Audio", H2),
    ("NotebookLM Studio → Audio Overview → Deep Dive → Customize.", P),
    ("Audience: an intermediate learner (around JLPT N3) reviewing today’s "
     "new words.", P),
    ("Scope: cover ONLY the entries under the most recent \"Added ... UTC\" marker.", P),
    ("For each word, in document order:", P),
    ("  - say the word, then its kana reading slowly, then the English meaning", P),
    ("  - name the part of speech and the particle pattern (助詞) it takes", P),
    ("  - read example sentence 1 in Japanese at natural speed, then again "
     "slowly, then give the English", P),
    ("  - add one quick usage tip or a contrast with a similar word", P),
    ("Keep it to about 45-60 seconds per word. Speak the Japanese clearly and "
     "pause between words. Do not add vocabulary that is not in the source.", P),
    ("Close with a 20-second rapid recap: word → meaning only.", P),
    ("", P),

    ("2b. Pronunciation drill", H3),
    ("Make a short call-and-response pronunciation drill from the entries under "
     "the most recent \"Added ... UTC\" marker. For each word:", P),
    ("  - read the word in Japanese, pause 2 seconds for the listener to repeat, "
     "then read the kana reading", P),
    ("  - read example sentence 1 in Japanese, pause 3 seconds, then read it again", P),
    ("No English except a one-line meaning per word. Keep the whole thing under "
     "5 minutes. Stay strictly within the words in the source.", P),
    ("", P),

    ("3. Infographic", H2),
    ("NotebookLM Studio → Video Overview → Customize. Or paste 3b in "
     "chat for a spec you feed to a slides / design tool.", P),
    ("Make an infographic-style visual summary of the words under the most "
     "recent \"Added ... UTC\" marker - one slide per word.", P),
    ("Each slide:", P),
    ("  - the word large in Japanese, with the kana reading beneath it", P),
    ("  - the English meaning as a short headline", P),
    ("  - a labelled chip for the part of speech and one for the particle "
     "pattern (助詞)", P),
    ("  - example sentence 1 in Japanese with the English underneath, smaller", P),
    ("  - if two words in the batch are related, note the link in one short line", P),
    ("Open with a title slide \"N3 Vocabulary - <date of the batch>\" and a word "
     "count. Close with a grid slide listing every word and its meaning. Keep "
     "text minimal and readable; use only content from the source.", P),
    ("", P),

    ("3b. Infographic as text spec (paste in chat)", H3),
    ("For the entries under the most recent \"Added ... UTC\" marker, output a "
     "structured spec for a one-page infographic. Use this exact layout:", P),
    ("TITLE: N3 Vocabulary - <batch date> (<n> words)", P),
    ("For each word:", P),
    ("  ## <word> (<reading>)", P),
    ("  MEANING: <english>", P),
    ("  POS: <part of speech>   PARTICLE: <助詞 pattern or \"-\">", P),
    ("  EXAMPLE: <japanese>", P),
    ("           <english>", P),
    ("  MNEMONIC: <one-line memory hook you derive from the meaning>", P),
    ("FOOTER: one row \"word - meaning\" for every word, comma-separated.", P),
    ("Plain text only, no markdown tables, no commentary. Only use words, "
     "readings, and sentences found in the source; the MNEMONIC line is the "
     "only thing you may compose.", P),
    ("", P),

    ("4. Bonus — quiz & study guide", H2),
    ("4a. Quiz — NotebookLM Studio → Quiz → Customize (or chat).", H3),
    ("Write a 10-question quiz on the entries under the most recent \"Added ... "
     "UTC\" marker. Mix: 4 multiple-choice (Japanese word → English "
     "meaning), 3 fill-in-the-blank (an example sentence with the target word "
     "removed, kana hint given), 3 particle questions (choose the correct "
     "助詞 for the word). Put all answers in an \"Answer key\" section "
     "at the end. Draw only on the source.", P),
    ("", P),
    ("4b. Study guide — NotebookLM Studio → Reports → Study guide "
     "→ Customize.", H3),
    ("Build a study guide for the entries under the most recent \"Added ... "
     "UTC\" marker. Group the words by part of speech. For each word give "
     "reading, meaning, particle pattern, and one example (Japanese + English). "
     "Add a short \"Watch out for\" section noting any words in the batch that "
     "look or sound similar. End with a plain list of the words for self-testing.", P),
]


def main() -> None:
    token = _gdoc_access_token(SA_JSON)
    headers = {"Authorization": f"Bearer {token}"}

    doc = requests.get(
        f"https://docs.googleapis.com/v1/documents/{DOC_ID}",
        headers=headers, timeout=_TIMEOUT,
    )
    doc.raise_for_status()
    content = doc.json().get("body", {}).get("content", [])
    end_index = content[-1]["endIndex"] if content else 2
    insert_at = max(1, end_index - 1)

    text, style_requests = _paragraphs_to_requests(PARAS, insert_at)
    resp = requests.post(
        f"https://docs.googleapis.com/v1/documents/{DOC_ID}:batchUpdate",
        headers=headers,
        json={"requests": [
            {"insertText": {"location": {"index": insert_at}, "text": text}},
            *style_requests,
        ]},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    print(f"ok: inserted {len(text)} chars at index {insert_at}, "
          f"{len(style_requests)} heading styles")


if __name__ == "__main__":
    main()
