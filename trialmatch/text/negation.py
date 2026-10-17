"""NegEx-style context detection for Czech + English clinical text (operates on folded text).

For a concept match inside a sentence, decide:
  polarity:  affirmed | negated
  certainty: certain | uncertain | hypothetical (planned / recommended / considered)
  experiencer: patient | other (family members)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Triggers are written in folded form (lowercase, no diacritics).
PRE_NEG = [
    r"bez", r"bez znamek", r"bez prokazu", r"bez susp(?:\.|ektnich)?", r"neprokaz\w*", r"nepotvrz\w*", r"neni",
    r"nebyl[aoy]?", r"nema", r"nemel[a]?", r"neguje", r"vyloucen[aoy]?", r"zadn[aeyi]\w*",
    r"nenalezen\w*", r"nezjisten\w*", r"absence", r"chybi", r"nelecen\w*", r"neuzival\w*", r"neuziva\w*",
    r"nebyl[aoy]? lecen\w*", r"neindikovan\w*", r"ani",
    r"no", r"not", r"without", r"negative for", r"denies", r"free of", r"absence of", r"no evidence of", r"ruled out",
]
POST_NEG = [
    r"neprokazan\w*", r"nepotvrzen\w*", r"nebyl[aoy]? prokazan\w*", r"nejsou", r"neni", r"negativni", r"neg\.?",
    r"vyloucen\w*", r"nezjisten\w*", r"nelecen\w*", r"nepodan\w*", r"neuzival\w*", r"0\s*%?\s*neg", r"not (?:seen|detected|found)", r"negative", r"absent",
    r"\(-\)", r"-\s*neg",
]
PRE_UNCERTAIN = [r"susp\.?", r"suspektni", r"suspekce", r"podezreni na", r"nelze vyloucit", r"pravdepodobne?",
                 r"mozn[aey]", r"nejist[aey]", r"dif\.?\s*dg\.?", r"possible", r"probable", r"suspected",
                 r"cannot be excluded", r"questionable", r"v\.?s\.?"]
POST_UNCERTAIN = [r"\?", r"nelze vyloucit", r"suspektni", r"susp\.?", r"neurcit\w*", r"k dif\w*"]
PRE_HYPOTHETICAL = [r"planuj\w*", r"planovan[aeyi]?", r"doporuc\w*", r"zvazit", r"zvazujeme", r"indikov\w*",
                    r"zahajit", r"zahajime", r"navrh\w*", r"v pripade", r"pokud", r"k zahajeni",
                    r"consider", r"recommend\w*", r"planned", r"if"]
FAMILY = [r"matk[aiy]", r"otec", r"otce", r"otci", r"bratr\w*", r"syn(?:a|em|ovi)?", r"dcer[aiy]?", r"babick\w*",
          r"dedec\w*", r"strycek", r"teta", r"tety", r"rodic\w*", r"\[osoba\]", r"mother", r"father",
          r"sister", r"brother", r"family history"]


def _compile(words: list[str], suffix: bool = False) -> re.Pattern:
    body = "|".join(f"(?:{w})" for w in sorted(words, key=len, reverse=True))
    # word-ish boundaries, folded text only has ascii letters for words
    return re.compile(rf"(?<![a-z0-9])(?:{body})(?![a-z0-9])")


_PRE_NEG = _compile(PRE_NEG)
_POST_NEG = _compile(POST_NEG)
_PRE_UNC = _compile(PRE_UNCERTAIN)
_POST_UNC = _compile(POST_UNCERTAIN)
_PRE_HYP = _compile(PRE_HYPOTHETICAL)
_FAMILY = _compile(FAMILY)
# Scope terminators: a trigger before one of these does not reach the concept.
_TERMINATOR = re.compile(r"(?<![a-z0-9])(?:ale|avsak|vsak|but|however|although|jinak|krome|s vyjimkou|except)(?![a-z0-9])|[;:]")

PRE_WINDOW = 60   # characters before the match
POST_WINDOW = 35  # characters after the match


@dataclass
class Context:
    polarity: str = "affirmed"
    certainty: str = "certain"
    experiencer: str = "patient"


def _last_scope(segment: str) -> str:
    cut = list(_TERMINATOR.finditer(segment))
    return segment[cut[-1].end():] if cut else segment


def _first_scope(segment: str) -> str:
    m = _TERMINATOR.search(segment)
    seg = segment[: m.start()] if m else segment
    # a post-trigger must not jump over a comma-separated list item that has its own finding
    return seg.split(",")[0]


def classify(folded: str, start: int, end: int, sent_start: int, sent_end: int) -> Context:
    pre = _last_scope(folded[max(sent_start, start - PRE_WINDOW): start])
    post = _first_scope(folded[end: min(sent_end, end + POST_WINDOW)])
    ctx = Context()
    inner = folded[start:end]  # composite patterns ("mr mozku bez meta") may contain the trigger themselves
    if _PRE_NEG.search(pre) or _POST_NEG.search(post) or _PRE_NEG.search(inner):
        ctx.polarity = "negated"
    if _PRE_HYP.search(pre):
        ctx.certainty = "hypothetical"
    elif _PRE_UNC.search(pre) or _POST_UNC.search(post):
        ctx.certainty = "uncertain"
    if _FAMILY.search(folded[sent_start:start]):
        ctx.experiencer = "other"
    return ctx
