"""Turn free-text eligibility criteria into atomic, machine-readable Criterion objects.

Rule-based first (deterministic, explainable); criteria the rules cannot structure are marked `evaluability=llm`
and can be refined with the local LLM (`--llm`). Output is a draft that a clinician must review.

Usage: python -m trialmatch.trials.structure [--llm] [--only LOCAL_ID]
"""
from __future__ import annotations

import json
import re
import sys

from .. import lexicon
from ..config import TRIALS_CRITERIA, TRIALS_RAW
from ..schema import Criterion
from ..text.normalize import fold, parse_number

NOT_ASSESSABLE = re.compile(
    r"informed consent|\bconsent\b|willing|able to (?:comply|swallow|understand)|contracept|pregnan|breast[\s-]?feed|"
    r"lactat|childbearing|\bwocbp\b|sperm|life expectancy|vaccin|investigator(?:'s)? (?:opinion|judg)|"
    r"participat\w* in (?:another|other)|investigational (?:agent|device|drug)|compliance|psychiatric|"
    r"substance abuse|hypersensitivity|allerg|central (?:laboratory|review|confirmation)|tissue (?:sample|block)|"
    r"archival|provide (?:a |an )?(?:tumou?r )?(?:sample|tissue)|sexual|abstinen|major surgery|fistula|"
    r"blood pressure|hypertension|qtc|ejection fraction|lvef|infection requiring|active infection|"
    r"social security|health insurance|deprived of (?:their )?(?:freedom|liberty)|guardianship|legal protection|"
    r"protocol[\s-]defined|other protocol"
)
NEGATED_REQ = re.compile(r"\b(?:no|not|without|never|naive|na[iï]ve|must not|have not|has not|absence of|free of)\b")
POST_EXCLUDED = re.compile(r"\b(?:(?:are|is) not eligible|not allowed|(?:are|is) excluded|ineligible)\b")
PERMISSION = re.compile(r"\b(?:is|are) (?:allowed|permitted)\b|\bmay (?:have )?(?:received|be enrolled)|\bmaximum of\b")
REQUIRES_PRIOR = re.compile(r"\b(?:prior|previous(?:ly)?|received|treated with|progress\w* (?:on|after|following|during)|"
                            r"after|following|refractory|relapsed|failure of|exposure to|history of)\b")
DIAGNOSIS_RX = re.compile(r"(?:histolog\w*|cytolog\w*|patholog\w*)[\w\s/,-]{0,40}(?:confirm|document|proven|diagnos)\w*|"
                          r"diagnosis of|confirmed (?:diagnosis|adenocarcinoma|carcinoma)")
OTHER_MALIGNANCY_RX = re.compile(r"(?:additional|other|another|second|prior|concurrent) (?:primary )?malignan|"
                                 r"other (?:active )?cancer")
WINDOW_RX = re.compile(r"within (?:the )?(?:past |last |prior |previous )?(\d+)\s*(day|week|month|year)s?")
UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}
OP_RX = (r"(?P<op>≥|>=|≤|<=|>|<|=|greater than or equal to|less than or equal to|at least|no (?:more|greater) than|"
         r"not (?:more|greater) than|greater than|less than|more than|above|below|up to|of)")
OP_MAP = {"≥": ">=", ">=": ">=", "≤": "<=", "<=": "<=", ">": ">", "<": "<", "=": "=", "of": "<=",
          "greater than or equal to": ">=", "less than or equal to": "<=", "at least": ">=",
          "no more than": "<=", "no greater than": "<=", "not more than": "<=", "not greater than": "<=",
          "greater than": ">", "less than": "<", "more than": ">", "above": ">", "below": "<", "up to": "<="}
ECOG_RX = re.compile(r"(?:ecog|eastern cooperative oncology group|who performance|performance status)[^.;]{0,80}?"
                     r"(?:(?P<op>≤|<=|<|of|score of|status of|ps)\s*)(?P<a>[0-4])(?:\s*(?:or|-|–|to|,)\s*(?P<b>[0-4]))?"
                     r"(?:\s*(?:or|,)\s*(?P<c>[0-4]))?")
KPS_RX = re.compile(r"(?:karnofsky|\bkps\b)[^.;]{0,40}?(?P<op>≥|>=|>|of|at least)\s*(\d{2,3})")
GENERIC_ORGAN = re.compile(r"adequate (?:organ|bone marrow|hematologic\w*|haematologic\w*|renal|hepatic|liver|"
                           r"kidney|end[\s-]organ)(?:,? (?:and )?[\w-]+){0,3} functions?")

WEIGHTS = {"diagnosis": 3, "sex": 3, "age": 3, "ecog": 2, "metastasis": 2, "prostate": 2, "histology": 2,
           "biomarker": 2, "breast": 2, "therapy": 2, "lab": 1, "adequate_organ_function": 1, "comorbidity": 1,
           "procedure": 1, "status": 1}


def _weight(concept: str | None) -> float:
    if not concept:
        return 1.0
    return float(WEIGHTS.get(concept.split(".")[0], WEIGHTS.get(concept, 1)))


# --- splitting ----------------------------------------------------------------------------------------------------

def split_criteria(text: str) -> list[tuple[str, str]]:
    """Split CT.gov eligibility text into [(kind, criterion_text)] with header bullets prefixed to children."""
    text = text.replace("\\", "")
    kind, out = "inclusion", []
    stack: list[tuple[int, str]] = []  # (indent, header text)
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        low = line.strip().lower()
        if re.match(r"^(key )?inclusion criteria", low):
            kind, stack = "inclusion", []
            continue
        if re.match(r"^(key )?exclusion criteria", low):
            kind, stack = "exclusion", []
            continue
        m = re.match(r"^(\s*)(?:[*\-•]|\d+[.)]|[a-z][.)]|[ivx]+[.)])\s+(.*)$", line)
        if m:
            indent, body = len(m.group(1)), m.group(2).strip()
        else:
            indent, body = len(line) - len(line.lstrip()), line.strip()
            if out and not body.endswith(":") and indent > 0:
                out[-1] = (out[-1][0], out[-1][1] + " " + body)  # wrapped continuation line
                continue
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if body.endswith(":"):
            stack.append((indent, body.rstrip(":")))
            continue
        prefix = " ".join(h for _, h in stack)
        out.append((kind, f"{prefix}: {body}" if prefix else body))
    return out


# --- rules --------------------------------------------------------------------------------------------------------

def _window(f: str) -> int | None:
    m = WINDOW_RX.search(f)
    return int(m.group(1)) * UNIT_DAYS[m.group(2)] if m else None


def _ecog(f: str) -> int | None:
    m = ECOG_RX.search(f)
    if m:
        vals = [int(v) for v in (m.group("a"), m.group("b"), m.group("c")) if v is not None]
        mx = max(vals)
        return mx - 1 if m.group("op") == "<" else mx
    m = KPS_RX.search(f)
    if m:
        kps = int(m.group(2)) + (10 if m.group("op") == ">" else 0)
        return 1 if kps >= 80 else 2 if kps >= 60 else 3
    return None


def _labs(f: str) -> list[tuple[str, str, float, bool, str]]:
    """Return [(lab_key, op, value_in_canonical_unit, is_uln, raw)]."""
    out = []
    for key, lab in lexicon.labs().items():
        for rx in lab.criteria:
            for m in rx.finditer(f):
                tail = f[m.end(): m.end() + 70]
                tm = re.search(OP_RX + r"\s*(?P<num>\d+(?:[.,]\d+)?(?:[ ,]\d{3})*)\s*(?P<rest>[^;,]{0,30})", tail)
                if not tm or tm.start() > 25:
                    continue
                num = parse_number(tm.group("num"))
                if num is None:
                    continue
                rest = tm.group("rest")
                op = OP_MAP.get(tm.group("op"), "<=")
                rest_at = m.end() + tm.start("rest")  # absolute position where the unit starts
                um = re.match(r"\s*(?:x|×|times)?\s*(?:the\s+)?(?:institutional\s+)?(?:upper limit of normal|uln)", rest)
                if um:
                    out.append((key, op, num, True, f[m.start(): rest_at + um.end()]))
                    break
                factor, unit_end = None, 0
                lead = len(rest) - len(rest.lstrip())
                for urx, fac in lab.criteria_units:
                    if (u := urx.match(rest.strip())):
                        factor, unit_end = fac, lead + u.end()
                        break
                if factor is None:
                    if key in ("anc", "platelets") and re.match(r"\s*(?:x|×)\s*10", rest):
                        factor = 1.0
                    elif key == "platelets" and num >= 1000 or key == "anc" and num >= 100:
                        factor = 0.001
                    else:
                        continue
                end = rest_at + unit_end if unit_end else m.end() + tm.end("num")
                out.append((key, op, round(num * factor, 4), False, f[m.start(): end]))
                break
            if out and out[-1][0] == key:
                break
    return out


THERAPY_EN = {
    "therapy.arpi": r"androgen receptor (?:pathway )?inhibitor|\barpi?\b|\bari\b|novel hormonal|next[\s-]generation (?:hormonal|anti[\s-]?androgen)|second[\s-]generation|cyp\s*17",
    "therapy.adt": r"androgen deprivation|\badt\b|lhrh|gnrh",
    "therapy.taxane": r"taxane",
    "therapy.platinum": r"platinum",
    "therapy.chemotherapy": r"chemotherap|cytotoxic",
    "therapy.anti_pd1_pdl1": r"anti[\s-]?pd[\s-]?(?:l)?1|pd[\s-]?(?:l)?1 (?:inhibitor|antibod|blocking)|checkpoint inhibitor|immune checkpoint",
    "therapy.immunotherapy": r"immunotherap",
    "therapy.vegf_tki": r"vegf(?:r)?[\s-]*(?:targeted|tki|tyrosine)|tyrosine kinase inhibitor|\btki\b",
    "therapy.parp": r"\bparp",
    "therapy.cdk46": r"cdk\s*4/6",
    "therapy.her2_directed": r"her2[\s-]*(?:directed|targeted)|anti[\s-]?her2",
    "therapy.topo1_adc": r"topoisomerase i|topo[\s-]?1",
    "therapy.adc": r"antibody[\s-]drug conjugate|\badc\b",
    "therapy.endocrine_breast": r"endocrine therap|aromatase inhibitor|\bai\b",
    "therapy.kras_inhibitor": r"kras\s*g12c inhibitor|kras inhibitor",
    "therapy.psma_radioligand": r"psma[\s-]*(?:targeted )?radio",
    "therapy.radiotherapy": r"radiotherapy|radiation therapy|\bradiation\b",
    "therapy.systemic": r"systemic (?:anti[\s-]?cancer |anti[\s-]?neoplastic )?(?:therapy|treatment)|anti[\s-]?cancer therapy|antineoplastic",
    "therapy.mtor": r"\bmtor",
    "therapy.hif2a": r"hif[\s-]?2",
}


def _therapy_concepts(f: str) -> list[str]:
    found = []
    for concept, rx in THERAPY_EN.items():
        if re.search(rx, f):
            found.append(concept)
    for d in lexicon.drugs():
        if d.name.startswith("generic") or d.name == "CHOP":
            continue
        if any(rx.search(f) for rx in d.patterns):
            found.append(f"drug.{d.name}")
    return found


QUALIFIER_RX = re.compile(
    r"\b(?:with (?:new|active|evidence|symptomatic|untreated|progressive)|for (?:another|other|a different)|"
    r"who (?:are|is|were) (?:eligible|candidates?)|unless|except|other than|that (?:is|are|was|were|has|have|required?)|"
    r"which|requiring|required|in the (?:adjuvant|neoadjuvant|metastatic|advanced) setting|if |"
    r"(?:clinically )?significant|uncontrolled|active|symptomatic|untreated)\b")


def structure_text(trial_id: str, kind: str, text: str, idx: int, diag_icd: list[str]) -> list[Criterion]:
    crits = _structure_text(trial_id, kind, text, idx, diag_icd)
    f = fold(text)
    for c in crits:
        span = find_span(c, f)
        c.text_span = list(span) if span else None
        # Exclusions with extra conditions ("…after RT with new lesions", "…for other malignancy", "active …")
        # can only be confirmed by a human; mere presence of the concept must not hard-exclude the patient.
        if c.kind == "exclusion" and c.concept and not c.concept.startswith("lab.") and c.concept != "ecog":
            c.qualified = bool(QUALIFIER_RX.search(f))
    return crits


def _first(patterns, f: str):
    for rx in patterns:
        m = rx.search(f) if hasattr(rx, "search") else re.search(rx, f)
        if m:
            return m.start(), m.end()
    return None


def find_span(c: Criterion, f: str) -> tuple[int, int] | None:
    """Where in the (folded) criterion text the rule found its evidence – highlighted for the doctor."""
    k = c.concept
    if not k:
        return None
    if k == "ecog":
        return _first([ECOG_RX, KPS_RX], f)
    if k.startswith("lab."):
        if c.note:
            i = f.find(fold(c.note))
            if i >= 0:
                return i, i + len(c.note)
        return _first(lexicon.labs()[k[4:]].criteria, f)
    if k == "any_of":
        for sub in c.value or []:
            span = find_span(Criterion(id=c.id, trial_id=c.trial_id, kind=c.kind, text=c.text, concept=sub), f)
            if span:
                return span
        return None
    if k in lexicon.concepts():
        return _first(lexicon.concepts()[k].criteria, f)
    if k.startswith("therapy."):
        span = _first([THERAPY_EN[k]] if k in THERAPY_EN else [], f)
        if span:
            return span
        cls = k[8:]
        return _first([rx for d in lexicon.drugs() if cls in d.classes for rx in d.patterns], f)
    if k.startswith("drug."):
        return _first([rx for d in lexicon.drugs() if d.name == k[5:] for rx in d.patterns], f)
    if k == "diagnosis":
        return _first([DIAGNOSIS_RX], f)
    if k == "adequate_organ_function":
        return _first([GENERIC_ORGAN], f)
    if k == "other_malignancy":
        return _first([OTHER_MALIGNANCY_RX], f)
    return None


def _structure_text(trial_id: str, kind: str, text: str, idx: int, diag_icd: list[str]) -> list[Criterion]:
    f = fold(text)
    cid = f"{trial_id}:{'I' if kind == 'inclusion' else 'E'}{idx:02d}"
    window = _window(f)
    base = {"trial_id": trial_id, "kind": kind, "text": text, "time_window_days": window}

    def C(suffix="", **kw) -> Criterion:
        concept = kw.get("concept")
        kw.setdefault("weight", _weight(concept))
        return Criterion(id=cid + suffix, **{**base, **kw})

    out: list[Criterion] = []
    if re.match(r"\s*note\b", f):
        return [C(evaluability="not_assessable", note="poznámka k jinému kritériu")]
    # 1) ECOG
    ecog = _ecog(f)
    if ecog is not None:
        out.append(C("e", concept="ecog", operator="<=", value=ecog, time_window_days=None))
    # 2) specific lab thresholds
    for j, (key, op, val, uln, raw) in enumerate(_labs(f)):
        out.append(C(f"l{j}", concept=f"lab.{key}", operator=op, value=val, uln=uln, time_window_days=None,
                     unit="xULN" if uln else lexicon.labs()[key].unit, note=raw))
    if out:
        return out
    # 3) generic "adequate organ function"
    if GENERIC_ORGAN.search(f):
        return [C(concept="adequate_organ_function", operator="present", value=True)]
    # 4) age / sex statements are taken from the structured eligibility module instead
    if re.search(r"\b(?:\d{2}\s*years|years of age|aged?\s*(?:≥|>=)?\s*\d{2})", f) and len(f) < 120:
        return [C(evaluability="not_assessable", note="věk řešen strukturovaně (eligibilityModule)")]
    # 5) not assessable from records (consent, contraception, ...)
    if NOT_ASSESSABLE.search(f) and not re.search(r"metasta|ecog|hiv|hepatitis|pneumonitis|interstitial", f):
        return [C(evaluability="not_assessable")]
    # 6) diagnosis confirmation -> diagnosis gate + histology concept if named
    concepts_hit = [k for k, c in lexicon.concepts().items() if any(rx.search(f) for rx in c.criteria)]
    if DIAGNOSIS_RX.search(f) and diag_icd and kind == "inclusion":
        out.append(C("d", concept="diagnosis", operator="in", value=diag_icd, hard_gate=True))
        for k in concepts_hit:
            if k.startswith(("histology.", "breast.", "prostate.", "biomarker.", "metastasis.")):
                m = next(m for rx in lexicon.concepts()[k].criteria if (m := rx.search(f)))
                neg = bool(NEGATED_REQ.search(f[max(0, m.start() - 45): m.start()])) or \
                    bool(POST_EXCLUDED.search(f[m.end(): m.end() + 60]))
                out.append(C(f"c{len(out)}", concept=k, operator="absent" if neg else "present", value=True))
        return out
    if kind == "inclusion" and PERMISSION.search(f):
        return [C(evaluability="llm", note="povolení / limit, ne požadavek – posoudit ručně")]
    # 7) prior therapy
    therapies = _therapy_concepts(f)
    is_prior = REQUIRES_PRIOR.search(f) or re.search(r"treatment[\s-]na[iï]ve|untreated|no prior", f)
    if therapies and is_prior:
        negated = bool(NEGATED_REQ.search(f)) or "naive" in f or "untreated" in f
        op = "present" if kind == "exclusion" else ("absent" if negated else "present")
        specific = [t for t in therapies if t.startswith("drug.")]
        classes = [t for t in therapies if t.startswith("therapy.")]
        if not classes and len(specific) > 1:
            # several drugs of the same class ("enzalutamide or abiraterone") -> the class itself
            drug_cls = {d.name: set(d.classes) for d in lexicon.drugs()}
            common = set.intersection(*(drug_cls[x[5:]] for x in specific)) - {"chemotherapy", "immunotherapy"}
            if common:
                classes = [f"therapy.{min(common)}"]
        chosen = classes or specific
        if len(chosen) > 1 and "therapy.systemic" in chosen:
            chosen.remove("therapy.systemic")
        if kind == "inclusion" and op == "present" and len(chosen) > 1 and " or " in f:
            return [C(concept="any_of", operator="present", value=chosen[:4], weight=2)]
        for j, t in enumerate(chosen[:4]):
            out.append(C(f"t{j}", concept=t, operator=op, value=True))
        return out
    # 7b) other malignancy
    if OTHER_MALIGNANCY_RX.search(f):
        return [C(concept="other_malignancy", operator="present", value=True)]
    # 8) concept mentions (metastases, histology, comorbidities, biomarkers, procedures)
    if concepts_hit:
        specific_mets = [k for k in concepts_hit if k.startswith("metastasis.") and k != "metastasis.any"]
        if specific_mets and "metastasis.any" in concepts_hit:
            concepts_hit.remove("metastasis.any")
        if "status.progression" in concepts_hit and len(concepts_hit) > 1:
            concepts_hit.remove("status.progression")
        def neg_near(k: str) -> bool:
            # negation cue shortly before the concept mention ("without small cell features", "no brain mets")
            m = next(m for rx in lexicon.concepts()[k].criteria if (m := rx.search(f)))
            return (bool(NEGATED_REQ.search(f[max(0, m.start() - 45): m.start()])) or bool(NEGATED_REQ.search(f[:25]))
                    or bool(POST_EXCLUDED.search(f[m.end(): m.end() + 60])))
        negs = {k: neg_near(k) for k in concepts_hit}
        if kind == "inclusion" and not any(negs.values()) and len(concepts_hit) > 1 and " or " in f:
            return [C(concept="any_of", operator="present", value=concepts_hit[:4], weight=2)]
        for j, k in enumerate(concepts_hit[:3]):
            op = "present" if kind == "exclusion" else ("absent" if negs[k] else "present")
            out.append(C(f"c{j}", concept=k, operator=op, value=True))
        return out
    return [C(evaluability="llm", note="pravidla kritérium nerozpoznala")]


def structure_trial(rec: dict) -> list[Criterion]:
    trial_id = rec["local_id"]
    study = rec.get("study") or {}
    elig = study.get("protocolSection", {}).get("eligibilityModule", {})
    out: list[Criterion] = []
    if rec.get("icd_prefixes"):
        out.append(Criterion(id=f"{trial_id}:G-dx", trial_id=trial_id, kind="inclusion",
                             text=f"Diagnóza: {rec.get('diagnosis_label') or ''}".strip(), concept="diagnosis",
                             operator="in", value=rec["icd_prefixes"], hard_gate=True, weight=3, source="registry"))
    sex = elig.get("sex")
    if sex in ("MALE", "FEMALE"):
        out.append(Criterion(id=f"{trial_id}:G-sex", trial_id=trial_id, kind="inclusion", text=f"Sex: {sex}",
                             concept="sex", operator="=", value=sex.lower(), hard_gate=True, weight=3,
                             source="registry"))
    for key, op in (("minimumAge", ">="), ("maximumAge", "<=")):
        if elig.get(key):
            years = parse_number(elig[key].split()[0])
            out.append(Criterion(id=f"{trial_id}:G-{key}", trial_id=trial_id, kind="inclusion",
                                 text=f"{key}: {elig[key]}", concept="age", operator=op, value=years,
                                 hard_gate=True, weight=3, source="registry"))
    counters = {"inclusion": 0, "exclusion": 0}
    for kind, text in split_criteria(elig.get("eligibilityCriteria", "")):
        counters[kind] += 1
        crits = structure_text(trial_id, kind, text, counters[kind], rec.get("icd_prefixes") or [])
        # the diagnosis gate from the registry already exists; criterion-level dx check is kept only once
        out += [c for c in crits if not (c.concept == "diagnosis" and any(o.concept == "diagnosis" for o in out))]
    return out


def structure_registry_text(trial_id: str, text: str, diagnosis_label: str = "", title: str = "") -> list[Criterion]:
    """Criteria from a registry text with a 'Minimum age / Maximum age / Sex' header followed by the
    ClinicalTrials.gov eligibility text – the engine entry point for other applications."""
    from .registry import diagnosis_to_icd

    header, body = {}, []
    for line in text.splitlines():
        m = re.match(r"^\s*(Minimum age|Maximum age|Sex|Healthy volunteers)\s*:\s*(.*)$", line)
        if m and not body:
            header[m.group(1).lower()] = m.group(2).strip()
        else:
            body.append(line)
    elig = {"eligibilityCriteria": "\n".join(body)}
    sex = header.get("sex", "").upper()
    if sex in ("MALE", "FEMALE"):
        elig["sex"] = sex
    for key, name in (("minimum age", "minimumAge"), ("maximum age", "maximumAge")):
        if re.match(r"\d", header.get(key, "")):
            elig[name] = header[key]
    rec = {"local_id": trial_id, "diagnosis_label": diagnosis_label,
           "icd_prefixes": diagnosis_to_icd(diagnosis_label, title),
           "study": {"protocolSection": {"eligibilityModule": elig}}}
    return structure_trial(rec)


def trial_meta(rec: dict) -> dict:
    ident = (rec.get("study") or {}).get("protocolSection", {}).get("identificationModule", {})
    status = (rec.get("study") or {}).get("protocolSection", {}).get("statusModule", {})
    return {k: rec.get(k) for k in ("local_id", "section", "protocol", "sponsor", "title_cs", "drug", "phase",
                                     "diagnosis_label", "icd_prefixes", "nct_id")} | {
        "brief_title": ident.get("briefTitle"), "overall_status": status.get("overallStatus")}


def build_all(use_llm: bool = False, only: str | None = None) -> None:
    TRIALS_CRITERIA.mkdir(parents=True, exist_ok=True)
    stats = {"total": 0, "auto": 0, "llm": 0, "not_assessable": 0}
    for path in sorted(TRIALS_RAW.glob("*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        if only and rec["local_id"] != only:
            continue
        crits = structure_trial(rec)
        if use_llm:
            from .llm_structure import refine_with_llm
            crits = refine_with_llm(rec, crits)
        out = TRIALS_CRITERIA / path.name
        existing = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        reviewed = {c["id"]: c for c in existing.get("criteria", []) if c.get("status") == "reviewed"}
        merged = [Criterion.from_dict(reviewed[c.id]) if c.id in reviewed else c for c in crits]
        out.write_text(json.dumps({"trial": trial_meta(rec), "criteria": [c.to_dict() for c in merged]},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        for c in merged:
            stats["total"] += 1
            stats[c.evaluability if c.concept or c.evaluability != "auto" else "llm"] += 1
    print(stats)


if __name__ == "__main__":
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    build_all(use_llm="--llm" in sys.argv, only=only)
