"""Tier-2 extraction: lexicon + regex extractors over free text (Czech, English, abbreviations).

Every match is put in context (section, negation, uncertainty, experiencer) and returned as a Fact with the source
sentence as evidence.
"""
from __future__ import annotations

import hashlib
import re

from .. import lexicon
from ..schema import Fact
from ..text.negation import classify
from ..text.normalize import fold, parse_number, sentence_spans
from ..text.sections import NON_PATIENT_SECTIONS, SECTION_PRIORITY, section_at, split_sections

NUM = r"(\d+(?:[.,]\d+)?)"

# ECOG / WHO PS / "výkonnostní stav" with a value or range ("PS 0-1" -> worst = 1)
_ECOG = re.compile(
    r"(?<![a-z])(?:ecog(?:\s*ps)?|who\s*ps|ps(?:\s*\(?(?:ecog|who)\)?)?|who|performance status|"
    r"vykonnostni stav(?:\s*(?:ecog|who))?|celkovy stav\s*ps)\s*(?:dle\s+(?:ecog|who)\s*)?[:=]?\s*(?:je\s*)?([0-4])(?:\s*(?:-|–|/|az|to|or)\s*([0-4]))?(?![0-9]|[.,]\d|\s*%)"
)
_WHO_GRADE = re.compile(r"gliom|astrocyt|oligodendro|meningeom|ependym|grade|stupe|klasifik|tumor|nador|lymfom|"
                        r"karcinom|\bca\b|histolog")
_KPS = re.compile(r"(?:karnofsk\w*(?:\s*(?:index|score|skore))?|\bkps\b|\bki\b)\s*[:=]?\s*(\d{2,3})\s*%?")
# TNM: optional prefix (c, p, yp, r), T, N, M parts; at least T+N or an explicit M
_TNM = re.compile(
    r"(?<![a-z0-9])(?P<prefix>y?[cpr]|yp)?t(?P<t>[0-4x]|is)(?P<tsub>[a-d]?\d?)\s*,?\s*"
    r"(?:(?:y?[cp])?n(?P<n>[0-3x])(?P<nsub>[a-c]?)\s*,?\s*)"
    r"(?:(?:y?[cp])?m(?P<m>[01x])(?P<msub>[a-c]?))?(?![a-z0-9])"
)
_M_ONLY = re.compile(r"(?<![a-z0-9])(?:[cp])?m(?P<m>[01])(?P<msub>[a-c]?)(?![a-z0-9])")
_GLEASON = re.compile(
    r"(?:gleason\w*|\bgs\b)\s*(?:skore|score|sk\.?|grade)?\s*[:=]?\s*(?:(\d{1,2})\s*\(\s*)?(\d)\s*\+\s*(\d)"
    r"|(?:gleason\w*|\bgs\b)\s*(?:skore|score|sk\.?)?\s*[:=]?\s*(\d{1,2})(?!\s*\+)"
)
_ISUP = re.compile(r"\bisup\s*(?:grade|gg|skupina|g\.?)?\s*[:=]?\s*([1-5])\b|\bgg\s*([1-5])\b")
_PSA = re.compile(r"(?<![a-z])psa\b(?:\s*(?:hodnota|level|who))?\s*(?:[:=]|je|by|cca|<|>|of)?\s*" + NUM +
                  r"\s*(ng\s*/\s*ml|ug\s*/\s*l|µg\s*/\s*l)?")


def _sha(s: str) -> str:
    return hashlib.sha1(re.sub(r"\s+", " ", s).strip().encode()).hexdigest()[:16]


class TextExtractor:
    """Extract facts from one text. Pass a shared `seen` set to skip copy-forward sentences across documents."""

    def __init__(self):
        self.concepts = lexicon.concepts()
        self.drugs = lexicon.drugs()

    def extract(self, text: str, *, date: str | None = None, source_type: str = "document",
                source_id: str | None = None, seen: set[str] | None = None) -> list[Fact]:
        if not text:
            return []
        folded = fold(text)
        sections = split_sections(folded)
        facts: list[Fact] = []
        for s_start, s_end in sentence_spans(text):
            sent = folded[s_start:s_end]
            if seen is not None:
                h = _sha(sent)
                if h in seen:
                    continue  # copied from an earlier report; the earliest occurrence already produced facts
                seen.add(h)
            section = section_at(sections, s_start)
            base = {"date": date, "source_type": source_type, "source_id": source_id, "section": section,
                    "snippet": text[s_start:s_end].strip()[:500]}
            facts += self._concepts(folded, s_start, s_end, section, base)
            facts += self._drugs(folded, s_start, s_end, section, base)
            facts += self._ecog(sent, s_start, base)
            facts += self._tnm(sent, s_start, base)
            facts += self._gleason(sent, s_start, base)
            facts += self._psa(sent, s_start, base)
            facts += self._labs_in_text(sent, s_start, base)
            facts += self._vitals(sent, s_start, base)
        return facts

    # --- helpers -------------------------------------------------------------------------------------------------
    def _fact(self, concept, value, folded, start, end, s_start, s_end, section, base, **kw) -> Fact:
        ctx = classify(folded, start, end, s_start, s_end)
        experiencer = "other" if section in NON_PATIENT_SECTIONS else ctx.experiencer
        conf = 0.6 + 0.1 * max(SECTION_PRIORITY.get(section, 1), 0)
        return Fact(concept=concept, value=value, polarity=ctx.polarity, certainty=ctx.certainty,
                    experiencer=experiencer, method="rule", span=(start, end), confidence=min(conf, 0.95),
                    **base, **kw)

    def _concepts(self, folded, s, e, section, base) -> list[Fact]:
        out = []
        sent = folded[s:e]
        for key, c in self.concepts.items():
            taken: list[tuple[int, int]] = []
            for rx in c.patient:
                for m in rx.finditer(sent):
                    if any(m.start() < te and ts < m.end() for ts, te in taken):
                        continue  # overlapping match of another pattern of the same concept
                    taken.append((m.start(), m.end()))
                    out.append(self._fact(key, True, folded, s + m.start(), s + m.end(), s, e, section, base))
        return out

    def _drugs(self, folded, s, e, section, base) -> list[Fact]:
        out = []
        sent = folded[s:e]
        for d in self.drugs:
            for rx in d.patterns:
                m = rx.search(sent)
                if m:
                    f = self._fact(f"drug.{d.name}", True, folded, s + m.start(), s + m.end(), s, e, section, base,
                                   extra={"atc": d.atc, "classes": d.classes})
                    out.append(f)
                    for cls in d.classes + ([] if "adt" in d.classes else ["systemic"]):
                        out.append(Fact(**{**f.to_dict(), "concept": f"therapy.{cls}", "extra": {"drug": d.name}}))
                    break
        return out

    def _ecog(self, sent, s, base) -> list[Fact]:
        out = []
        for m in _ECOG.finditer(sent):
            # bare "WHO 2" is also the WHO tumour grade ("astrocytom WHO 2") – only take it outside that context
            if m.group(0).startswith("who") and _WHO_GRADE.search(sent[max(0, m.start() - 40): m.start()]):
                continue
            a, b = int(m.group(1)), m.group(2)
            value = max(a, int(b)) if b is not None else a
            out.append(Fact(concept="ecog", value=value, method="rule", span=(s + m.start(), s + m.end()),
                            confidence=0.9, **base))
        for m in _KPS.finditer(sent):
            kps = int(m.group(1))
            if 10 <= kps <= 100 and kps % 10 == 0:
                ecog = 0 if kps == 100 else 1 if kps >= 80 else 2 if kps >= 60 else 3 if kps >= 40 else 4
                out.append(Fact(concept="ecog", value=ecog, method="rule", span=(s + m.start(), s + m.end()),
                                confidence=0.7, extra={"kps": kps, "converted": True}, **base))
        return out

    def _tnm(self, sent, s, base) -> list[Fact]:
        out = []
        for m in _TNM.finditer(sent):
            t = m.group("t") + (m.group("tsub") or "")
            n = m.group("n") + (m.group("nsub") or "")
            mm = m.group("m")
            tnm = f"{m.group('prefix') or ''}T{t}N{n}" + (f"M{mm}{m.group('msub') or ''}" if mm else "")
            out.append(Fact(concept="tnm", value=tnm.upper().replace("IS", "is"), method="rule",
                            span=(s + m.start(), s + m.end()), confidence=0.85, **base))
            if mm in ("0", "1"):
                out.append(Fact(concept="metastasis.any", value=True,
                                polarity="affirmed" if mm == "1" else "negated", method="rule",
                                span=(s + m.start(), s + m.end()), confidence=0.85, extra={"from": "tnm"}, **base))
        if not out:
            for m in _M_ONLY.finditer(sent):
                # bare "M0"/"M1b" only near staging words to avoid matching e.g. drug names
                window = sent[max(0, m.start() - 30): m.end() + 5]
                if re.search(r"stadi|stage|tnm|klasifik|\bc?t\d|\bn\d", window):
                    out.append(Fact(concept="metastasis.any", value=True,
                                    polarity="affirmed" if m.group("m") == "1" else "negated", method="rule",
                                    span=(s + m.start(), s + m.end()), confidence=0.75, extra={"from": "tnm"}, **base))
        return out

    def _gleason(self, sent, s, base) -> list[Fact]:
        out = []
        for m in _GLEASON.finditer(sent):
            if m.group(2):
                a, b = int(m.group(2)), int(m.group(3))
                if 1 <= a <= 5 and 1 <= b <= 5:
                    out.append(Fact(concept="gleason", value=a + b, method="rule", span=(s + m.start(), s + m.end()),
                                    confidence=0.9, extra={"pattern": f"{a}+{b}"}, **base))
            elif m.group(4) and 2 <= int(m.group(4)) <= 10:
                out.append(Fact(concept="gleason", value=int(m.group(4)), method="rule",
                                span=(s + m.start(), s + m.end()), confidence=0.8, **base))
        for m in _ISUP.finditer(sent):
            out.append(Fact(concept="isup", value=int(m.group(1) or m.group(2)), method="rule",
                            span=(s + m.start(), s + m.end()), confidence=0.85, **base))
        return out

    def _psa(self, sent, s, base) -> list[Fact]:
        out = []
        for m in _PSA.finditer(sent):
            v = parse_number(m.group(1))
            if v is not None and v < 100000:
                out.append(Fact(concept="lab.psa", value=v, unit="ug/L", method="rule",
                                span=(s + m.start(), s + m.end()), confidence=0.75, **base))
        return out


    def _vitals(self, sent, s, base) -> list[Fact]:
        """Weight / height / BMI ("Hmotnost: 71 kg", "výška 177 cm", "BMI 24,3"); BMI computed when both given."""
        out, vals = [], {}
        for key, rx, lo, hi, unit in _VITALS:
            m = rx.search(sent)
            if not m:
                continue
            v = parse_number(m.group(1))
            if v is None or not lo <= v <= hi:
                continue
            vals[key] = v
            concept = "vitals.height" if key == "height" else f"lab.{key}"
            out.append(Fact(concept=concept, value=v, unit=unit, method="rule", span=(s + m.start(), s + m.end()),
                            confidence=0.85, **base))
        if "bmi" not in vals and "weight" in vals and "height" in vals:
            bmi = round(vals["weight"] / (vals["height"] / 100) ** 2, 1)
            out.append(Fact(concept="lab.bmi", value=bmi, unit="kg/m2", method="rule", confidence=0.85,
                            extra={"computed": True}, **base))
        return out

    def _labs_in_text(self, sent, s, base) -> list[Fact]:
        out = []
        for key, rx, lo, hi in _TEXT_LABS:
            for m in rx.finditer(sent):
                v = parse_number(m.group(1))
                unit = (m.group(2) or "").replace(" ", "")
                if v is None:
                    continue
                if key == "hemoglobin" and unit.endswith("dl"):
                    v *= 10
                if not lo <= v <= hi:
                    continue
                lab = lexicon.labs()[key]
                upper = _DEFAULT_UPPER.get(key)
                out.append(Fact(concept=f"lab.{key}", value=v, unit=lab.unit, method="rule",
                                span=(s + m.start(), s + m.end()), confidence=0.7,
                                extra={"uln_ratio": round(v / upper, 3) if upper else None, "raw_value": v,
                                       "raw_unit": unit or lab.unit, "upper": upper, "default_upper": bool(upper)},
                                **base))
        return out


# Lab values written in free text: (lab, regex with value + optional unit, plausible range in canonical unit)
_V = r"\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*(g\s*/\s*[dl]l?|umol/l|µmol/l|nmol/l|ukat/l|µkat/l|x?\s*10\^?9/l|ml/s)?"
_TEXT_LABS = [
    ("hemoglobin", re.compile(r"(?<![a-z])(?:hb|hgb|hemoglobin\w*)" + _V), 30, 250),
    ("platelets", re.compile(r"(?<![a-z])(?:plt|trombocyt\w*|platelets?)" + _V), 1, 2000),
    # absolute count only ("Neutrofily# 4.9", "neutrofily abs."); plain "Neutrofily 0.669" is the relative fraction
    ("anc", re.compile(r"(?<![a-z])(?:neutrofil\w*\s*(?:#|abs\.?)|anc|neu\s*#)" + _V), 0, 60),
    # plain "neutrofily X": absolute only when X > 1 (the relative fraction is ≤ 1, e.g. "Neutrofily 0.669")
    ("anc", re.compile(r"(?<![a-z])neutrofil\w*" + _V), 1.01, 60),
    ("creatinine", re.compile(r"(?<![a-z])(?:kreatinin\w*|creatinine|krea)" + _V), 10, 2000),
    ("testosterone", re.compile(r"(?<![a-z])(?:testosteron\w*|testosterone)" + _V), 0, 60),
    ("bilirubin", re.compile(r"(?<![a-z])(?:bilirubin\w*|bili)" + _V), 1, 800),
    ("alt", re.compile(r"(?<![a-z])alt" + _V), 0.01, 50),
    ("ast", re.compile(r"(?<![a-z])ast" + _V), 0.01, 50),
]
_VITALS = [
    ("weight", re.compile(r"(?<![a-z])(?:hmotnost|vaha|hm\.|weight)\s*[:=]?\s*(\d{2,3}(?:[.,]\d)?)\s*kg"), 25, 300, "kg"),
    ("height", re.compile(r"(?<![a-z])(?:vyska|height)\s*[:=]?\s*(\d{3}(?:[.,]\d)?)\s*cm"), 100, 230, "cm"),
    ("bmi", re.compile(r"(?<![a-z])bmi\s*[:=]?\s*(\d{2}(?:[.,]\d+)?)"), 10, 70, "kg/m2"),
]
# Typical adult upper reference limits, used only for text-derived values without a lab-specific norm.
_DEFAULT_UPPER = {"bilirubin": 17.0, "alt": 0.8, "ast": 0.8, "creatinine": 104.0}


# Free-text diagnosis detection (for uploaded reports without structured ICD codes)
_ICD = re.compile(r"(?<![a-z0-9])([cd]\d{2})(?:\.?(\d))?(?![0-9])")
_CANCER_WORD = re.compile(r"karcinom|\bca\b|nador|tumor|novotvar|malign|adenokarcinom|carcinoma|cancer|lymfom|lymphoma|"
                          r"melanom|sarkom|\brcc\b|\bnsclc\b|\bhcc\b|neoplasm")


def diagnoses_from_text(text: str) -> list[tuple[str, str]]:
    """Return [(icd3, evidence sentence)] from explicit ICD codes and cancer-type words."""
    from ..trials.registry import DIAGNOSIS_ICD
    folded = fold(text)
    found: dict[str, str] = {}
    sections = split_sections(folded)
    for s, e in sentence_spans(text):
        sent = folded[s:e]
        if section_at(sections, s) in NON_PATIENT_SECTIONS or re.search(r"(?<![a-z])(?:ra|rodinna anamneza)\s*:", sent):
            continue
        for m in _ICD.finditer(sent):
            if m.group(1).startswith("c") or m.group(1)[1:3] < "49":
                found.setdefault(m.group(1).upper(), text[s:e].strip())
        if _CANCER_WORD.search(sent):
            for key, prefixes in DIAGNOSIS_ICD:
                if key != "hlav" and re.search(rf"(?<![a-z]){re.escape(fold(key))}", sent):
                    found.setdefault(prefixes[0], text[s:e].strip())
    return list(found.items())
