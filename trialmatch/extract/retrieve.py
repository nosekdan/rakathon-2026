"""Retrieve the few patient sentences most relevant to a criterion (BM25 over folded text, CZ query expansion)."""
from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

from .. import lexicon
from ..text.normalize import fold, sentence_spans

# English criterion words -> Czech clinical stems (folded). Lets an English criterion find Czech evidence.
EN_CS = {
    "bleeding": ["krvacen", "hemoragi"], "hemorrhage": ["krvacen", "hemoragi"], "effusion": ["vypotek", "vypotk"],
    "pleural": ["pleural", "fluidothorax"], "oxygen": ["kyslik", "o2", "oxygenoterap"], "hypoxia": ["hypoxi", "saturac"],
    "fistula": ["pistel"], "immunodeficiency": ["imunodefic", "imunosupres"], "steroid": ["kortikoid", "prednison",
    "dexametazon", "medrol"], "steroids": ["kortikoid", "prednison", "dexametazon"], "transfusion": ["transfuz", "erymasa"],
    "measurable": ["meritel", "recist", "loziska"], "lesion": ["lozisk", "lezi"], "lesions": ["lozisk", "lezi"],
    "surgery": ["operac", "resekc", "ektomi"], "seizure": ["epilep", "kreci"],
    "thrombosis": ["tromboz"], "embolism": ["embol"], "cardiac": ["srdec", "kardi"], "heart": ["srdec", "kardi"],
    "liver": ["jater", "jatra", "hepat"], "kidney": ["ledvin", "renal"], "bone": ["kost", "skelet"],
    "brain": ["mozk", "cns"], "lung": ["plic", "pulmo"], "progression": ["progres"], "metastatic": ["metast", "mts"],
    "metastases": ["metast", "mts"], "radiotherapy": ["radioterap", "ozar", "rt"], "chemotherapy": ["chemoterap", "cht"],
    "psma": ["psma"], "pet": ["pet"], "hypertension": ["hypertenz"],
    "infection": ["infekc"], "tuberculosis": ["tuberkul", "tbc"], "pregnant": ["tehot"], "dementia": ["demenc"],
    "weight": ["hmotnost", "vaha", "kg"], "loss": ["ubytek", "pokles"], "cachexia": ["kachexi"], "appetite": ["chut"],
    "symptomatic": ["symptomat", "potize"], "pain": ["bolest"], "transplant": ["transplant"], "stent": ["stent"],
    "biliary": ["zlucov", "bilia"], "obstruction": ["obstrukc", "uzaver"], "ascites": ["ascit"],
    "cavitation": ["kavit", "rozpad"], "vessel": ["cev"], "invasion": ["invaz", "prorust"], "nephrectomy": ["nefrektom"],
    "resorptive": ["denosumab", "xgeva", "zoledron", "bisfosfon"], "bisphosphonate": ["zoledron", "bisfosfon"],
    "denosumab": ["denosumab", "xgeva", "prolia"], "testosterone": ["testosteron"], "castrate": ["kastrac"],
    "stroke": ["cmp", "iktus", "mozkova", "cevni"], "cerebrovascular": ["cmp", "iktus"],
    "anticoagulation": ["warfarin", "antikoag", "heparin", "fraxiparin", "apixaban", "eliquis", "rivaroxaban",
                        "xarelto", "dabigatran"], "anticoagulant": ["warfarin", "antikoag", "heparin"],
    "vitamin": ["warfarin"], "diabetes": ["diabet", "dm", "metformin", "inzulin"], "hepatitis": ["hepatitid", "hbv", "hcv"],
    "cirrhosis": ["cirhoz", "jater"], "neuropathy": ["neuropati", "parestez"], "smoking": ["kurak", "koureni"],
}
_WORD = re.compile(r"[a-z0-9]{3,}")
_STOP = {"has", "have", "with", "the", "and", "for", "any", "who", "are", "must", "within", "prior", "than", "not",
         "that", "this", "from", "been", "per", "other", "participants", "participant", "patients", "patient",
         "study", "treatment", "days", "weeks", "months", "first", "dose", "known", "history", "least"}


def _tokens(text: str) -> list[str]:
    return _WORD.findall(fold(text))


def expand_query(criterion_text: str) -> list[str]:
    toks = [t for t in _tokens(criterion_text) if t not in _STOP]
    out = list(toks)
    for t in toks:
        out += EN_CS.get(t, [])
    for d in lexicon.drugs():
        if any(rx.search(fold(criterion_text)) for rx in d.patterns):
            out += [d.name.lower()]
    return out


class SentenceIndex:
    """BM25 index over sentences of all texts of one patient. Prefix matching on Czech stems via token truncation."""

    def __init__(self, docs: list[tuple[str, str | None, str]]):
        """docs: [(source_id, date, text)]"""
        self.sentences: list[tuple[str, str | None, str]] = []
        seen = set()
        for sid, date, text in docs:
            for s, e in sentence_spans(text or ""):
                sent = text[s:e].strip()
                key = fold(sent)
                if len(sent) < 8 or key in seen:
                    continue
                seen.add(key)
                self.sentences.append((sid, date, sent))
        corpus = [[t[:6] for t in _tokens(s)] or ["_"] for _, _, s in self.sentences]
        self.bm25 = BM25Okapi(corpus) if corpus else None

    def search(self, criterion_text: str, k: int = 6) -> list[tuple[str, str | None, str]]:
        if not self.bm25:
            return []
        q = [t[:6] for t in expand_query(criterion_text)]
        scores = self.bm25.get_scores(q)
        best = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        return [self.sentences[i] for i in best if scores[i] > 0]


def patient_docs(con, pseudonym: str) -> list[tuple[str, str | None, str]]:
    rows = con.execute("""
        SELECT doc_id, ts, text FROM documents WHERE pseudonym = $p AND text IS NOT NULL
        UNION ALL SELECT study_id, ts, text FROM imaging WHERE pseudonym = $p AND text IS NOT NULL
        ORDER BY 2 DESC""", {"p": pseudonym}).fetchall()
    return [(r[0], str(r[1])[:10] if r[1] else None, r[2]) for r in rows]
