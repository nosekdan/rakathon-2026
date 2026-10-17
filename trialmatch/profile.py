"""Patient profile: all facts about a patient plus a per-concept resolution with conflicts surfaced.

Usage:
  python -m trialmatch.profile --build          # extract facts for all patients into DuckDB (table `facts`)
  python -m trialmatch.profile PAC-XXXXXXXXXX   # print resolved profile of one patient
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

from .config import DB_PATH
from .extract.structured import diagnosis_facts, lab_facts, radiotherapy_doc_facts, treatment_facts
from .extract.text_rules import TextExtractor, diagnoses_from_text
from .schema import Fact

VALUE_CONCEPTS = {"ecog", "gleason", "isup", "tnm", "primary_malignancies", "diagnosis"}
METHOD_RANK = {"structured": 3, "rule": 2, "llm": 1}


@dataclass
class Resolved:
    concept: str
    state: str                      # present | absent | uncertain | value
    value: object = None
    date: str | None = None
    evidence: list[Fact] = field(default_factory=list)
    conflict: bool = False
    note: str | None = None


@dataclass
class PatientProfile:
    pseudonym: str
    sex: str | None = None
    age: int | None = None
    age_band: str | None = None
    icd: list[str] = field(default_factory=list)
    generalization: int = 0
    index_date: str | None = None
    facts: list[Fact] = field(default_factory=list)
    source: str = "dataset"         # dataset | upload
    n_docs: int = 0                 # documents searched (for "not found" explanations)
    doc_range: tuple[str, str] | None = None
    _resolved: dict | None = None

    def age_range(self) -> tuple[int, int] | None:
        if self.age is not None:
            return self.age, self.age
        if self.age_band:
            lo, hi = self.age_band.split("-")
            return int(lo), int(hi)
        return None

    def facts_for(self, concept: str) -> list[Fact]:
        return [f for f in self.facts if f.concept == concept and f.experiencer == "patient"]

    def resolve(self, concept: str) -> Resolved | None:
        if self._resolved is None:
            self._resolved = {}
        if concept not in self._resolved:
            r = _resolve(concept, self.facts_for(concept))
            if concept == "metastasis.any" and (r is None or r.state != "present"):
                # a specific metastasis site implies metastatic disease; a contradicting negation is surfaced
                # read the sites' own facts (not self.resolve: the sites themselves inherit "absent" from here)
                children = [_resolve(k, self.facts_for(k)) for k in ("metastasis.bone", "metastasis.liver",
                                                                      "metastasis.brain", "metastasis.visceral")]
                pos = [c for c in children if c is not None and c.state == "present"]
                if pos:
                    neg = r is not None and r.state == "absent"
                    r = Resolved(concept, "present", True, max(c.date or "" for c in pos) or None,
                                 [f for c in pos for f in c.evidence][:3] + (r.evidence[:1] if neg else []),
                                 conflict=neg, note="odvozeno z lokalizace metastáz" +
                                 ("; jinde výslovně negováno – ověřit" if neg else ""))
            if r is None and concept.startswith("metastasis.") and concept != "metastasis.any":
                parent = self.resolve("metastasis.any")
                if parent is not None and parent.state == "absent":
                    r = Resolved(concept, "absent", False, parent.date, parent.evidence,
                                 note="odvozeno – metastatické onemocnění negováno")
            self._resolved[concept] = r
        return self._resolved[concept]

    def concepts(self) -> list[str]:
        return sorted({f.concept for f in self.facts if f.experiencer == "patient"})


def _rank(f: Fact):
    return (METHOD_RANK.get(f.method, 0), f.confidence, f.date or "")


def _resolve(concept: str, facts: list[Fact]) -> Resolved | None:
    if not facts:
        return None
    if concept.startswith(("lab.", "vitals.")) or concept in VALUE_CONCEPTS:
        affirmed = [f for f in facts if f.polarity == "affirmed"]
        if not affirmed:
            return None
        latest = max(affirmed, key=lambda f: (f.date or "", METHOD_RANK.get(f.method, 0), f.confidence))
        if concept.startswith("lab.") and latest.method != "structured":
            # a measured lab value beats a number read from text unless the text is clearly newer (> 7 days)
            structured = [f for f in affirmed if f.method == "structured"]
            if structured:
                best = max(structured, key=lambda f: f.date or "")
                if not latest.date or not best.date or \
                        (date.fromisoformat(latest.date[:10]) - date.fromisoformat(best.date[:10])).days <= 7:
                    latest = best
        others = sorted([f for f in affirmed if f is not latest], key=lambda f: f.date or "", reverse=True)
        distinct = {json.dumps(f.value, sort_keys=True, default=str) for f in affirmed if f.date == latest.date}
        return Resolved(concept, "value", latest.value, latest.date, [latest] + others[:4], conflict=len(distinct) > 1)
    positive = [f for f in facts if f.polarity == "affirmed" and f.certainty == "certain"]
    uncertain = [f for f in facts if f.polarity == "affirmed" and f.certainty != "certain"]
    negative = [f for f in facts if f.polarity == "negated" and f.certainty == "certain"]
    if positive:
        ev = sorted(positive, key=_rank, reverse=True)[:3]
        latest_pos = max(f.date or "" for f in positive)
        later_neg = [f for f in negative if (f.date or "") > latest_pos]
        return Resolved(concept, "present", True, latest_pos or None, ev + later_neg[:1], conflict=bool(later_neg),
                        note="pozdější zpráva uvádí opak" if later_neg else None)
    if uncertain:
        ev = sorted(uncertain, key=_rank, reverse=True)[:3]
        hyp = all(f.certainty == "hypothetical" for f in uncertain)
        return Resolved(concept, "uncertain", None, max(f.date or "" for f in uncertain) or None, ev,
                        note="pouze plánováno/doporučeno" if hyp else "nejisté (susp.)")
    negative = negative or [f for f in facts if f.polarity == "negated"]  # e.g. negated + uncertain
    if not negative:
        return None
    ev = sorted(negative, key=_rank, reverse=True)[:3]
    return Resolved(concept, "absent", False, max(f.date or "" for f in negative) or None, ev)


# --- building -----------------------------------------------------------------------------------------------------

def extract_patient(con, pseudonym: str, tx: TextExtractor | None = None) -> list[Fact]:
    tx = tx or TextExtractor()
    icd = con.execute("SELECT icd FROM patients WHERE pseudonym = ?", [pseudonym]).fetchone()[0]
    facts = diagnosis_facts(list(icd or []))
    facts += lab_facts(con.execute(
        "SELECT lab_id, ts, name, value, unit, lower, upper FROM labs WHERE pseudonym = ?", [pseudonym]).fetchall())
    facts += treatment_facts(con.execute(
        "SELECT kind, date, code, name, atc FROM treatments WHERE pseudonym = ?", [pseudonym]).fetchall())
    docs = con.execute("""
        SELECT 'document', doc_id, ts, type, type_label, text FROM documents WHERE pseudonym = ?
        UNION ALL SELECT 'imaging', study_id, ts, modality, site, text FROM imaging WHERE pseudonym = ?
        ORDER BY ts""", [pseudonym, pseudonym]).fetchall()
    facts += radiotherapy_doc_facts([(d[1], d[2], d[3], d[4]) for d in docs if d[0] == "document"])
    seen: set[str] = set()
    for kind, sid, ts, _typ, _label, text in docs:
        facts += tx.extract(text or "", date=str(ts)[:10] if ts else None, source_type=kind, source_id=sid, seen=seen)
    return facts


def build_all(db_path=DB_PATH) -> None:
    import duckdb  # only needed for the hackathon dataset build, not for the engine

    con = duckdb.connect(str(db_path))
    tx = TextExtractor()
    con.execute("DROP TABLE IF EXISTS facts")
    con.execute("""CREATE TABLE facts(pseudonym VARCHAR, concept VARCHAR, fact JSON)""")
    pacs = [r[0] for r in con.execute("SELECT pseudonym FROM patients ORDER BY pseudonym").fetchall()]
    t0, total, batch = time.time(), 0, []
    for i, p in enumerate(pacs, 1):
        for f in extract_patient(con, p, tx):
            batch.append((p, f.concept, json.dumps(f.to_dict(), ensure_ascii=False, default=str)))
        if len(batch) > 20000 or i == len(pacs):
            con.executemany("INSERT INTO facts VALUES (?, ?, ?)", batch)
            total += len(batch)
            batch = []
            print(f"{i}/{len(pacs)} patients, {total} facts, {time.time() - t0:.0f}s", flush=True)
    con.execute("CREATE INDEX facts_p ON facts(pseudonym)")
    con.close()


def _fact_from_json(s: str) -> Fact:
    d = json.loads(s)
    if d.get("span"):
        d["span"] = tuple(d["span"])
    return Fact(**d)


def _index_date(con, pseudonym: str) -> str | None:
    r = con.execute("""
        SELECT max(d) FROM (
          SELECT max(ts)::DATE d FROM documents WHERE pseudonym = $p
          UNION ALL SELECT max(ts)::DATE FROM labs WHERE pseudonym = $p
          UNION ALL SELECT max(date) FROM treatments WHERE pseudonym = $p)""", {"p": pseudonym}).fetchone()
    return str(r[0]) if r and r[0] else None


def load_profile(con, pseudonym: str) -> PatientProfile:
    row = con.execute("SELECT sex, age, age_band, icd, generalization FROM patients WHERE pseudonym = ?",
                      [pseudonym]).fetchone()
    if row is None:
        raise KeyError(pseudonym)
    facts = [_fact_from_json(r[0]) for r in con.execute(
        "SELECT fact FROM facts WHERE pseudonym = ?", [pseudonym]).fetchall()]
    prof = PatientProfile(pseudonym, row[0], row[1], row[2], list(row[3] or []), row[4], _index_date(con, pseudonym), facts)
    n, lo, hi = con.execute("""SELECT count(*), min(ts)::DATE, max(ts)::DATE FROM (
        SELECT ts FROM documents WHERE pseudonym = $p UNION ALL SELECT ts FROM imaging WHERE pseudonym = $p)""",
                            {"p": pseudonym}).fetchone()
    prof.n_docs, prof.doc_range = n, (str(lo), str(hi)) if lo else None
    return prof


def load_all_profiles(con) -> dict[str, PatientProfile]:
    profiles = {}
    for p, sex, age, band, icd, gen in con.execute(
            "SELECT pseudonym, sex, age, age_band, icd, generalization FROM patients").fetchall():
        profiles[p] = PatientProfile(p, sex, age, band, list(icd or []), gen)
    for p, d in con.execute("""
        SELECT pseudonym, max(d) FROM (
          SELECT pseudonym, ts::DATE d FROM documents UNION ALL SELECT pseudonym, ts::DATE FROM labs
          UNION ALL SELECT pseudonym, date FROM treatments) GROUP BY pseudonym""").fetchall():
        if p in profiles and d:
            profiles[p].index_date = str(d)
    for p, n, lo, hi in con.execute("""SELECT pseudonym, count(*), min(ts)::DATE, max(ts)::DATE FROM (
            SELECT pseudonym, ts FROM documents UNION ALL SELECT pseudonym, ts FROM imaging) GROUP BY 1""").fetchall():
        if p in profiles:
            profiles[p].n_docs, profiles[p].doc_range = n, (str(lo), str(hi)) if lo else None
    for p, f in con.execute("SELECT pseudonym, fact FROM facts").fetchall():
        profiles[p].facts.append(_fact_from_json(f))
    return profiles


def profile_from_text(text: str, *, sex: str | None = None, age: int | None = None,
                      icd: list[str] | None = None, tx: TextExtractor | None = None) -> PatientProfile:
    """Profile from a single uploaded report (no structured data): text facts + diagnoses detected in text."""
    tx = tx or TextExtractor()
    today = date.today().isoformat()
    facts = tx.extract(text, date=today, source_type="upload", source_id="upload")
    found = diagnoses_from_text(text)
    codes = list(icd or []) or [c for c, _ in found]
    dx = diagnosis_facts(codes)
    for f in dx:
        f.source_type, f.date = "upload", today
        f.method = "structured" if icd else "rule"
        ev = next((s for c, s in found if c == f.source_id), None)
        if ev:
            f.snippet = ev
    return PatientProfile("UPLOAD", sex, age, None, codes, 0, today, dx + facts, source="upload", n_docs=1,
                          doc_range=(today, today))


NOTE_KINDS = ("DocumentReference", "DiagnosticReport", "document", "imaging", "note")


def profile_from_record(sources, *, pseudonym: str, sex: str | None = None, age: int | None = None,
                        age_band: str | None = None, icd: list[str] | tuple = (), labs: list[dict] | tuple = (),
                        snapshot_at: str | None = None, tx: TextExtractor | None = None) -> PatientProfile:
    """Profile from arbitrary sources – the engine entry point for other applications.

    sources: iterable of {"id", "text", "date", "kind"}; free-text kinds in NOTE_KINDS are read by the rules,
             fact.source_id = the source id and fact.span = character offsets in that source's text.
    labs:    iterable of {"name", "value", "unit", "date", "source_id", "lower"?, "upper"?} with Czech lab names
             as in the hospital system (e.g. "HGB", "Neutrofily#", "PSA WHO").
    """
    tx = tx or TextExtractor()
    sources = list(sources.values()) if isinstance(sources, dict) else list(sources)
    notes = sorted((s for s in sources if s.get("kind") in NOTE_KINDS and s.get("text")),
                   key=lambda s: s.get("date") or "")
    codes = [c for c in icd if c]
    facts = diagnosis_facts(codes)
    facts += lab_facts((lab.get("source_id"), lab.get("date"), lab.get("name"), _num(lab.get("value")), lab.get("unit"),
                        _num(lab.get("lower")), _num(lab.get("upper"))) for lab in labs)
    seen: set[str] = set()
    for s in notes:
        facts += tx.extract(s["text"], date=(s.get("date") or "")[:10] or None, source_type="document",
                            source_id=s["id"], seen=seen)
    dates = [d[:10] for d in (*(s.get("date") for s in sources), snapshot_at) if d]
    prof = PatientProfile(pseudonym, sex, age, age_band, codes, 0, max(dates) if dates else None, facts,
                          source="record", n_docs=len(notes))
    note_dates = [s["date"][:10] for s in notes if s.get("date")]
    prof.doc_range = (min(note_dates), max(note_dates)) if note_dates else None
    return prof


def _num(v):
    try:
        return float(str(v).replace(",", ".")) if v not in (None, "") else None
    except ValueError:
        return None


def is_stale(fact_date: str | None, index_date: str | None, days: int) -> bool:
    if not fact_date or not index_date:
        return False
    return date.fromisoformat(fact_date) < date.fromisoformat(index_date) - timedelta(days=days)


if __name__ == "__main__":
    if "--build" in sys.argv:
        build_all()
    elif len(sys.argv) > 1:
        import duckdb

        con = duckdb.connect(str(DB_PATH), read_only=True)
        prof = load_profile(con, sys.argv[1])
        print(prof.pseudonym, prof.sex, prof.age or prof.age_band, prof.icd, "index:", prof.index_date)
        for c in prof.concepts():
            r = prof.resolve(c)
            if r:
                print(f"  {c:32s} {r.state:9s} {str(r.value)[:30]:30s} {r.date or '':10s} "
                      f"{'CONFLICT ' if r.conflict else ''}{r.note or ''}")
