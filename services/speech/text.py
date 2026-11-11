"""Transcript text as it is stored: one normal form for Urdu-script, Latin and mixed lines.

Urdu Sukhan writes Urdu in Urdu (Arabic) script, English words in Latin script, and no punctuation, so one segment
can hold both directions. Stored text is therefore kept free of anything that changes how the line is laid out:

* Unicode NFC, so a composed letter (``ۓ``, ``آ``) and its decomposed form (``ے`` + hamza) are the same text.
* Bidirectional controls (embeddings, overrides, isolates, LRM/RLM/ALM) are removed. The display decides the line's
  direction from its letters; a stray or unterminated control from ASR or a paste would reorder the rest of the line.
* Runs of whitespace become one space, and the ends are trimmed.

Zero-width (non-)joiners are kept: they are part of Urdu spelling. Letters are never changed here; folding letter
variants (Arabic ``ي`` / Urdu ``ی``) for search happens only in the search tokenizer.
"""
from __future__ import annotations

import re
import unicodedata

BIDI_CONTROLS = re.compile("[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")
SPACES = re.compile(r"\s+")


def clean(text: str) -> str:
    return SPACES.sub(" ", BIDI_CONTROLS.sub("", unicodedata.normalize("NFC", text))).strip()
