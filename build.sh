#!/bin/bash
set -e
mkdir -p _site
python3 simple_agent/artifact_creation/build_vocab_infographic.py _site/index.html
python3 simple_agent/artifact_creation/build_grammar_reference.py _site/grammar.html
python3 simple_agent/artifact_creation/build_kanji_reference.py _site/kanji.html
