"""One-off: append the exam-prep prompt section (JLPT N3, exam day
December 3) to the 'vocabn3prompts' Google Doc.

Same mechanism as write_vocab_prompts_doc.py - service-account token +
Docs REST batchUpdate, appended after the existing content.

Run once:  myenv/bin/python scheduled/write_vocab_prompts_doc_exam.py
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

PARAS = [
    ("", P),
    ("Exam-prep prompts — JLPT N3, exam day December 3", H1),
    ("These sharpen the same source doc toward the 文字・語彙 (vocab), "
     "文法 (grammar/particles) and 聴解 (listening) sections. Replace the "
     "bracketed date with today’s date when you paste. Scope line as before: "
     "“the most recent 'Added … UTC' marker” for a fresh batch, "
     "“ALL entries in the document” for cumulative work.", P),
    ("", P),

    ("Study phases (≈12 weeks out on Sept 8)", H2),
    ("• Now → early Nov: learn each drip’s new words daily; one cumulative "
     "review a week (prompt E2).", P),
    ("• Nov 3 → Nov 23: exam-format drills 3× a week (E1, E5), cumulative "
     "review twice a week, start a confusables pass (E6).", P),
    ("• Nov 24 → Dec 1: timed full 語彙 sets, weak-spot drills only (E3), "
     "listening practice (E4).", P),
    ("• Dec 1 → Dec 2: light review only (E7). No new material. Sleep.", P),
    ("", P),

    ("E1. Exam-format vocab set (文字・語彙, all five question types)", H2),
    ("NotebookLM Studio → Quiz → Customize, or paste in chat.", P),
    ("From [scope], build a JLPT N3 文字・語彙 practice set using the five "
     "official question types:", P),
    ("1. 漢字読み — a sentence with the target word written in kanji; "
     "4 hiragana options for its reading.", P),
    ("2. 表記 — a sentence with the target word written in hiragana; "
     "4 kanji options.", P),
    ("3. 文脈規定 — a sentence with the target word replaced by ___; "
     "4 word options that all fit grammatically, one correct by meaning.", P),
    ("4. 言い換え類義 — a sentence with the target word underlined; "
     "4 options, pick the closest in meaning.", P),
    ("5. 用法 — the target word, then 4 sentences; pick the one that uses "
     "it correctly.", P),
    ("Take target words and sentences from the source. Build distractors from "
     "other words in the document where possible, otherwise plausible N3-level "
     "words. 3–4 questions per type. Put the answer key with one-line "
     "explanations at the very end, not inline.", P),
    ("", P),

    ("E2. Weekly cumulative review", H2),
    ("Scope: use ALL entries in the document. Today is [date]; the exam is "
     "December 3.", P),
    ("Produce a prioritized list for a 30-minute review session. Rank highest: "
     "words added three or more weeks ago, and words that carry a particle "
     "pattern (助詞). Rank lowest: the most recent batch (still fresh). "
     "Give at most 15 words. For each: reading, meaning, particle pattern, and "
     "one cloze sentence (an example from the doc with the word blanked). "
     "Finish with 5 self-check questions mixing reading, meaning and particle.", P),
    ("", P),

    ("E3. Weak-spot drill", H2),
    ("Words I got wrong: [paste the words].", P),
    ("For each, pull from the source: reading, meaning, every particle pattern, "
     "and every example sentence (Japanese / kana / English). Then add: 3 new "
     "N3-level cloze sentences per word, a one-line note on why it is easy to "
     "confuse (similar kanji, reading, or meaning — name the word it clashes "
     "with), and a short mnemonic. End with a 12-question mixed quiz covering "
     "only these words, answer key at the end.", P),
    ("", P),

    ("E4. Listening practice (聴解)", H2),
    ("NotebookLM Studio → Audio Overview → Deep Dive → Customize.", P),
    ("Make a listening episode from [scope]. For each word: read example "
     "sentence 1 once at natural native speed — no slow repeat — then ask a "
     "short comprehension question in Japanese about it, leave 5 seconds of "
     "silence, then give the answer and read the sentence again slowly with the "
     "English. After every 5 words, do a 3-sentence dictation: read each "
     "sentence twice at natural speed with 8 seconds of silence after. End with "
     "every sentence read once, natural speed, as a shadowing track.", P),
    ("", P),

    ("E5. 文脈規定 speed sprint", H2),
    ("From [scope], make a rapid fill-in-the-blank set: take each example "
     "sentence, blank the target word (___), and give 4 options — the answer "
     "plus 3 other words from the document. 20 items. Answers as a plain list "
     "at the end only, no explanations. This one is for speed, so keep the "
     "sentences short.", P),
    ("", P),

    ("E6. Confusable-word pass", H2),
    ("Scan ALL entries. Find pairs or small groups of words that are easy to "
     "mix up — similar kanji, similar reading, overlapping meaning, or the "
     "same particle. For each group: list the words with readings and "
     "meanings, explain the distinction in one or two lines, and give one "
     "contrasting example sentence per word (from the source where available, "
     "otherwise your own at N3 level). Aim for 8–12 groups.", P),
    ("", P),

    ("E7. Final-week light review", H2),
    ("Scope: use ALL entries. This is my last week before the exam — no new "
     "material. Give a calm 15-minute review: 20 high-value words (common ones, "
     "or ones likely to be half-forgotten by now), each as "
     "word → reading → meaning on a single line, then a 10-item recognition "
     "quiz (word → meaning, multiple choice) with answers at the end. Keep the "
     "tone encouraging.", P),
    ("", P),

    ("E8. Build my study plan (run once)", H2),
    ("Today is [date]. My JLPT N3 exam is December 3. Using this vocabulary "
     "document as my word list, build a week-by-week plan to exam day. Each "
     "week: which words to focus on (by batch date range), how many review "
     "sessions, which of the prompts above (E1–E7) to run that week, and one "
     "measurable checkpoint. Assume about 30 minutes a day. Front-load "
     "new-word acquisition, shift to cumulative review and exam-format drills "
     "from mid-November, and make the last week light review only.", P),
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
