"""Length-preserving text folding (lowercase, no diacritics) so regex offsets map 1:1 back to the original text."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache


@lru_cache(maxsize=4096)
def _fold_char(ch: str) -> str:
    base = unicodedata.normalize("NFD", ch)[0].lower()
    return base[0] if base else ch


def fold(text: str) -> str:
    """'Výkonnostní stav ECOG 1' -> 'vykonnostni stav ecog 1' with identical length."""
    out = "".join(_fold_char(c) for c in text)
    assert len(out) == len(text)
    return out


def parse_number(s: str) -> float | None:
    """Czech/English number: '2,5' / '2.5' / '100 000' / '100,000' -> float."""
    s = s.strip().replace(" ", " ")
    if re.fullmatch(r"\d{1,3}([ ,]\d{3})+", s):
        s = re.sub(r"[ ,]", "", s)
    s = s.replace(",", ".").replace(" ", "")
    try:
        return float(s)
    except ValueError:
        return None


_SENT_RE = re.compile(r"(?<=[.!?;])\s+(?=[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ0-9])|\n\s*\n|\n(?=\s*[-•*]\s)|\n(?=[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]{2,}\s*:)")


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Rough sentence/line segmentation good enough for scoping negation in clinical notes."""
    spans, start = [], 0
    for m in _SENT_RE.finditer(text):
        if m.start() > start:
            spans.append((start, m.start()))
        start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    # split overly long lines on single newlines as well
    out = []
    for s, e in spans:
        if e - s > 400:
            pos = s
            for m in re.finditer(r"\n", text[s:e]):
                out.append((pos, s + m.start()))
                pos = s + m.end()
            out.append((pos, e))
        else:
            out.append((s, e))
    return [(s, e) for s, e in out if text[s:e].strip()]
