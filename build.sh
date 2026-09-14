#!/bin/bash
set -e
mkdir -p _site
curl -fsSL "https://github.com/sureshshil/Agentic-/releases/download/n3-kvg-v1/kanjivg_strokes.json" -o _site/kanjivg_strokes.json
python3 simple_agent/artifact_creation/build_vocab_infographic.py _site/index.html
python3 simple_agent/artifact_creation/build_grammar_reference.py _site/grammar.html
python3 simple_agent/artifact_creation/build_kanji_reference.py _site/kanji.html
