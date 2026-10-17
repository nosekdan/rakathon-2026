"""Fill a separate demo database: synthetic studies, rule-based patient facts and comparisons. No model calls.

The studies are invented. Patient facts are real values picked by rules from the real records, each with an
exact quote, so every fact opens in its source. Comparisons apply simple threshold rules to those facts.
Results land in their own file, so the real cache is never touched and the real pipeline never mistakes
them for model output:

    uv run python seed_demo.py
    TRIALSCREEN_DB=.cache/demo-seed.sqlite3 uv run uvicorn main:app --host 127.0.0.1 --port 8765
"""

import os

os.environ.setdefault("TRIALSCREEN_DB", ".cache/demo-seed.sqlite3")

import re
from datetime import UTC, datetime

import storage
from pipeline import MODEL, VERSION, patient_input, trial_input
from repository import NOTE_KINDS, patient_record, patients
from screening import (
    Decision,
    Evidence,
    Logic,
    TrialExtraction,
    future_watch,
    locate_evidence,
    summarize,
    verify_decisions,
)

FACTS_LABEL = "Ukázková extrakce pravidly (bez LLM)"
MATCH_LABEL = "Ukázkové porovnání pravidly (bez LLM)"
LABS = {
    "HGB": ("hgb", "Hemoglobin"),
    "PLT": ("plt", "Trombocyty"),
    "Neutrofily#": ("neut", "Neutrofily (absolutní počet)"),
    "Kreatinin": ("creat", "Kreatinin"),
    "eGFR-krea-(CKD-EPI)": ("egfr", "eGFR (CKD-EPI)"),
    "ALT": ("alt", "ALT"),
    "AST": ("ast", "AST"),
    "Bilirubin celkový": ("bili", "Bilirubin celkový"),
    "Albumin": ("alb", "Albumin"),
    "PSA WHO": ("psa", "PSA"),
    "Testosteron": ("testo", "Testosteron"),
}


# Patients -----------------------------------------------------------------------------------------------


def located(source: dict, quote: str) -> dict | None:
    return locate_evidence(Evidence(source_id=source["id"], quote=quote), {source["id"]: source})


def extract(record: dict) -> list[dict]:
    """Facts as (code, category, name, value, date, evidence); evidence is always an exact source span."""
    facts = []

    def add(code, category, name, value, date, source, quote):
        evidence = located(source, quote) if quote else None
        if evidence:
            facts.append(
                {
                    "code": code,
                    "category": category,
                    "name": name,
                    "value": str(value),
                    "date": date,
                    "evidence": evidence,
                }
            )

    cohort = record["sources"]["cohort"]
    raw = record["raw"]
    age_column = "vek" if raw.get("vek") else "vek_pasmo"
    if raw.get(age_column):
        add("age", "zaklad", "Věk", raw[age_column], None, cohort, f"{age_column}: {raw[age_column]}")
    sex = {"male": "Muž", "female": "Žena"}.get(raw["pohlavi"], raw["pohlavi"])
    add("sex", "zaklad", "Pohlaví", sex, None, cohort, f"pohlavi: {raw['pohlavi']}")
    if raw.get("diagnozy"):
        add("dx", "nador", "Diagnózy (MKN-10)", raw["diagnozy"], None, cohort, f"diagnozy: {raw['diagnozy']}")
    for code, name, unit in (("height", "Výška", "cm"), ("weight", "Hmotnost", "kg"), ("ecog", "ECOG", "")):
        vital = record["vitals"].get(code)
        if vital:
            value = f"{vital['value']} {unit}".strip()
            add(
                code,
                "zaklad",
                name,
                value,
                vital["date"],
                record["sources"][vital["source_id"]],
                vital["quote"],
            )

    notes = sorted(
        (source for source in record["sources"].values() if source["kind"] in NOTE_KINDS),
        key=lambda source: source["date"] or "",
        reverse=True,
    )
    patterns = (
        ("tnm", "Klasifikace TNM", r"\b[cpy]?T[0-4][a-c]?\s*N[0-3xX]\s*M[01xX]\b"),
        ("gleason", "Gleason skóre", r"Gleason\w*\s*(?:sk[oó]re)?\s*:?\s*\d\s*\+\s*\d(?:\s*=\s*\d+)?"),
        ("metast", "Metastázy (zmínka v textu)", r"[^.\n]{0,120}metast[^.\n]{0,120}"),
    )
    for code, name, pattern in patterns:
        for note in notes:
            match = re.search(pattern, note["text"], re.IGNORECASE)
            if match:
                quote = match.group().strip()
                add(code, "nador", name, re.sub(r"\s+", " ", quote), note["date"], note, quote)
                break

    for lab in record["latest_labs"]:
        if lab["name"] in LABS and lab["value"] not in ("", None):
            code, name = LABS[lab["name"]]
            source = record["sources"][lab["source_id"]]
            add(
                code,
                "laborator",
                name,
                f"{lab['value']} {lab['unit']}".strip(),
                lab["date"],
                source,
                source["text"].split("\n")[0].strip(),
            )
    seen = set()
    for treatment in sorted(record["treatments"], key=lambda item: item["date"] or "", reverse=True):
        name = treatment["name"]
        if name and not name.isdigit() and name not in seen:
            seen.add(name)
            add(
                "drug",
                "lecba",
                "Léčivo",
                name,
                treatment["date"],
                record["sources"][treatment["source_id"]],
                name,
            )
    return [{"id": f"F{index}", **fact} for index, fact in enumerate(facts, 1)]


def chosen_patients() -> list[dict]:
    candidates = [
        patient
        for patient in patients()
        if patient["target"]
        and not patient["deceased"]
        and 5 <= patient["notes"] <= 40
        and patient["labs"] > 20
    ]
    prostate = [patient for patient in candidates if patient["diagnoses"] == "C61"][:10]
    kidney = [patient for patient in candidates if patient["diagnoses"] == "C64"][:4]
    return prostate + kidney


# Studies ------------------------------------------------------------------------------------------------


def number(fact: dict | None) -> float | None:
    match = re.search(r"-?\d+(?:[.,]\d+)?", fact["value"]) if fact else None
    return float(match.group().replace(",", ".")) if match else None


def missing(action):
    return lambda facts: ("missing", "Údaj v dokumentaci chybí.", [], action)


def at_least(code, threshold, label):
    def rule(facts):
        value = number(facts.get(code))
        if value is None:
            return "missing", f"{label}: hodnota není doložena.", [], f"Doplnit {label.lower()}."
        status = "met" if value >= threshold else "not_met"
        return status, f"{label} {facts[code]['value']} (požadováno ≥ {threshold}).", [code], None

    return rule


def at_most(code, threshold, label):
    def rule(facts):
        value = number(facts.get(code))
        if value is None:
            return "missing", f"{label}: hodnota není doložena.", [], f"Doplnit {label.lower()}."
        status = "met" if value <= threshold else "not_met"
        return status, f"{label} {facts[code]['value']} (požadováno ≤ {threshold}).", [code], None

    return rule


def adult(facts):
    value = number(facts.get("age"))
    if value is None:
        return "missing", "Věk není doložen.", [], "Ověřit věk."
    return ("met" if value >= 18 else "not_met"), f"Věk {facts['age']['value']}.", ["age"], None


def diagnosis(code):
    def rule(facts):
        fact = facts.get("dx")
        if not fact:
            return "missing", "Diagnóza není v kohortě uvedena.", [], "Ověřit diagnózu."
        status = "met" if code in fact["value"] else "not_met"
        return status, f"Diagnóza v záznamu: {fact['value']}. Histologii ověřit.", ["dx"], None

    return rule


def metastatic(required):
    def rule(facts):
        tnm = facts.get("tnm")
        if tnm and re.search(r"M[01]", tnm["value"], re.IGNORECASE):
            present = bool(re.search(r"M1", tnm["value"], re.IGNORECASE))
            return ("met" if present == required else "not_met"), f"TNM {tnm['value']}.", ["tnm"], None
        mention = facts.get("metast")
        if mention:
            return (
                "unknown",
                "Text zmiňuje metastázy, rozsah ověřit.",
                ["metast"],
                "Ověřit staging (CT, scintigrafie).",
            )
        return "missing", "Staging není v dokumentaci doložen.", [], "Doplnit staging."

    return rule


PROSTATE_BASE = [
    ("age", "inclusion", "Věk alespoň 18 let.", "Male participants aged 18 years or older.", adult),
    (
        "dx",
        "inclusion",
        "Histologicky potvrzený adenokarcinom prostaty.",
        "Histologically confirmed adenocarcinoma of the prostate.",
        diagnosis("C61"),
    ),
]
STUDIES = [
    {
        "id": "UKAZKA-PROSTATA-01",
        "title": "Ukázka: metastatický kastračně rezistentní karcinom prostaty po léčbě ARPI",
        "diagnosis": "Karcinom prostaty (mCRPC)",
        "phase": "Fáze 3",
        "status": "RECRUITING",
        "site": True,
        "sex": "MALE",
        "criteria": PROSTATE_BASE
        + [
            ("ecog", "inclusion", "ECOG 0–1.", "ECOG performance status 0 or 1.", at_most("ecog", 1, "ECOG")),
            (
                "metastatic",
                "inclusion",
                "Dokumentované metastatické onemocnění.",
                "Documented metastatic disease on imaging.",
                metastatic(True),
            ),
            (
                "castrate",
                "inclusion",
                "Kastrační hladina testosteronu (≤ 1,7 nmol/l).",
                "Serum testosterone at castrate level (<= 1.7 nmol/L).",
                at_most("testo", 1.7, "Testosteron"),
            ),
            (
                "arpi",
                "inclusion",
                "Progrese po předchozí léčbě ARPI (enzalutamid, abirateron).",
                "Progression after prior androgen receptor pathway inhibitor.",
                missing("Doložit předchozí léčbu ARPI a progresi."),
            ),
            (
                "hgb",
                "inclusion",
                "Hemoglobin ≥ 90 g/l.",
                "Hemoglobin >= 90 g/L.",
                at_least("hgb", 90, "Hemoglobin"),
            ),
            (
                "neut",
                "inclusion",
                "Neutrofily ≥ 1,5 × 10⁹/l.",
                "Absolute neutrophil count >= 1.5 x 10^9/L.",
                at_least("neut", 1.5, "Neutrofily"),
            ),
            (
                "plt",
                "inclusion",
                "Trombocyty ≥ 100 × 10⁹/l.",
                "Platelets >= 100 x 10^9/L.",
                at_least("plt", 100, "Trombocyty"),
            ),
            (
                "brain",
                "exclusion",
                "Bez aktivních mozkových metastáz.",
                "Active brain metastases.",
                missing("Ověřit nepřítomnost mozkových metastáz."),
            ),
        ],
    },
    {
        "id": "UKAZKA-PROSTATA-02",
        "title": "Ukázka: neoadjuvantní léčba lokalizovaného karcinomu prostaty s vysokým rizikem",
        "diagnosis": "Karcinom prostaty (lokalizovaný)",
        "phase": "Fáze 2",
        "status": "RECRUITING",
        "site": True,
        "sex": "MALE",
        "criteria": PROSTATE_BASE
        + [
            ("ecog", "inclusion", "ECOG 0–1.", "ECOG performance status 0 or 1.", at_most("ecog", 1, "ECOG")),
            (
                "m0",
                "inclusion",
                "Bez vzdálených metastáz (M0).",
                "No evidence of distant metastases (M0).",
                metastatic(False),
            ),
            ("psa", "inclusion", "PSA ≥ 4 µg/l.", "PSA >= 4 ng/mL.", at_least("psa", 4, "PSA")),
            (
                "naive",
                "inclusion",
                "Nekastrační hladina testosteronu (> 1,7 nmol/l).",
                "Non-castrate testosterone (> 1.7 nmol/L).",
                at_least("testo", 1.7, "Testosteron"),
            ),
            (
                "egfr",
                "inclusion",
                "eGFR ≥ 0,5 ml/s/1,73 m² (≈ 30 ml/min).",
                "eGFR >= 30 mL/min/1.73 m2.",
                at_least("egfr", 0.5, "eGFR"),
            ),
        ],
    },
    {
        "id": "UKAZKA-PROSTATA-03",
        "title": "Ukázka: PARP inhibitor u karcinomu prostaty s mutací v genech HRR",
        "diagnosis": "Karcinom prostaty (HRR mutace)",
        "phase": "Fáze 3",
        "status": "NOT_YET_RECRUITING",
        "site": False,
        "sex": "MALE",
        "criteria": PROSTATE_BASE
        + [
            (
                "hrr",
                "inclusion",
                "Patogenní mutace BRCA1/2 nebo ATM.",
                "Deleterious BRCA1, BRCA2 or ATM alteration.",
                missing("Doplnit výsledek genetického vyšetření."),
            ),
            ("ecog", "inclusion", "ECOG 0–2.", "ECOG performance status 0 to 2.", at_most("ecog", 2, "ECOG")),
            (
                "hgb",
                "inclusion",
                "Hemoglobin ≥ 90 g/l.",
                "Hemoglobin >= 90 g/L.",
                at_least("hgb", 90, "Hemoglobin"),
            ),
            ("alt", "inclusion", "ALT ≤ 1,5 µkat/l.", "ALT <= 2.5 x ULN.", at_most("alt", 1.5, "ALT")),
        ],
    },
    {
        "id": "UKAZKA-LEDVINA-01",
        "title": "Ukázka: první linie léčby pokročilého světlobuněčného karcinomu ledviny",
        "diagnosis": "Karcinom ledviny (pokročilý)",
        "phase": "Fáze 3",
        "status": "RECRUITING",
        "site": True,
        "sex": "ALL",
        "criteria": [
            ("age", "inclusion", "Věk alespoň 18 let.", "Participants aged 18 years or older.", adult),
            (
                "dx",
                "inclusion",
                "Histologicky potvrzený karcinom ledviny.",
                "Histologically confirmed renal cell carcinoma.",
                diagnosis("C64"),
            ),
            ("ecog", "inclusion", "ECOG 0–1.", "ECOG performance status 0 or 1.", at_most("ecog", 1, "ECOG")),
            (
                "egfr",
                "inclusion",
                "eGFR ≥ 0,5 ml/s/1,73 m² (≈ 30 ml/min).",
                "eGFR >= 30 mL/min/1.73 m2.",
                at_least("egfr", 0.5, "eGFR"),
            ),
            (
                "hgb",
                "inclusion",
                "Hemoglobin ≥ 90 g/l.",
                "Hemoglobin >= 90 g/L.",
                at_least("hgb", 90, "Hemoglobin"),
            ),
            (
                "plt",
                "inclusion",
                "Trombocyty ≥ 100 × 10⁹/l.",
                "Platelets >= 100 x 10^9/L.",
                at_least("plt", 100, "Trombocyty"),
            ),
            (
                "prior",
                "exclusion",
                "Bez předchozí systémové léčby pokročilého onemocnění.",
                "Prior systemic therapy for advanced disease.",
                missing("Ověřit předchozí systémovou léčbu."),
            ),
        ],
    },
    {
        "id": "UKAZKA-LEDVINA-02",
        "title": "Ukázka: adjuvantní imunoterapie po nefrektomii",
        "diagnosis": "Karcinom ledviny (po nefrektomii)",
        "phase": "Fáze 3",
        "status": "COMPLETED",
        "site": False,
        "sex": "ALL",
        "criteria": [
            ("age", "inclusion", "Věk alespoň 18 let.", "Participants aged 18 years or older.", adult),
            (
                "dx",
                "inclusion",
                "Karcinom ledviny po radikální nebo parciální nefrektomii.",
                "Renal cell carcinoma after nephrectomy.",
                diagnosis("C64"),
            ),
            (
                "m0",
                "inclusion",
                "Bez vzdálených metastáz (M0).",
                "No evidence of distant metastases (M0).",
                metastatic(False),
            ),
            ("ecog", "inclusion", "ECOG 0–1.", "ECOG performance status 0 or 1.", at_most("ecog", 1, "ECOG")),
        ],
    },
]


def registry_for(study: dict, now: str) -> dict:
    lines = [f"* {line}" for _, _, _, line, _ in study["criteria"]]
    text = "\n".join(
        [
            "Minimum age: 18 Years",
            "Maximum age: not specified",
            f"Sex: {study['sex']}",
            "Healthy volunteers: False",
            "SYNTHETIC DEMO PROTOCOL, NOT A REAL STUDY.",
            "Eligibility Criteria:",
            *lines,
        ]
    )
    return {
        "state": "resolved",
        "synthetic": True,
        "nct_id": None,
        "title": study["title"],
        "status": study["status"],
        "last_update": None,
        "url": None,
        "site_recruiting": study["site"],
        "sites": [
            {"facility": "Fakultní nemocnice v Motole", "status": "RECRUITING" if study["site"] else None}
        ],
        "checked_at": now,
        "sources": {
            "registry": {
                "id": "registry",
                "title": "Ukázkový protokol / kritéria",
                "date": None,
                "kind": "protocol",
                "text": text,
                "url": None,
            }
        },
    }


def main() -> None:
    if storage.PATH.exists():
        storage.PATH.unlink()
    now = datetime.now(UTC).isoformat()
    studies = []
    for study in STUDIES:
        registry = registry_for(study, now)
        source = registry["sources"]["registry"]
        criteria = [
            {
                "id": identifier,
                "requirement": requirement,
                "role": role,
                "source": located(source, line),
                "trigger": "none",
            }
            for identifier, role, requirement, line, _ in study["criteria"]
        ]
        extraction = {
            "key": trial_input(registry)["key"],
            "trial_id": study["id"],
            "nct_id": None,
            "model": "Ukázková data",
            "criteria": criteria,
            "logic": {
                "op": "all",
                "children": [{"op": "ref", "criterion_id": item["id"]} for item in criteria],
            },
            "complete": True,
            "limitations": ["Smyšlená ukázková studie, nikoli skutečný protokol."],
        }
        storage.put(
            "studies",
            study["id"],
            study["id"],
            {
                "id": study["id"],
                "hospital_id": "",
                "protocol": study["id"],
                "title": study["title"],
                "diagnosis": study["diagnosis"],
                "sponsor": "Ukázková data",
                "phase": study["phase"],
                "synthetic": True,
            },
        )
        storage.put("registry", study["id"], study["id"], registry)
        storage.put("criteria", extraction["key"], study["id"], extraction)
        studies.append((study, extraction))

    for patient in chosen_patients():
        record = patient_record(patient["id"])
        task = patient_input(record)
        facts = extract(record)
        storage.put(
            "facts",
            task["key"],
            patient["id"],
            {
                "key": task["key"],
                "patient_id": patient["id"],
                "model": FACTS_LABEL,
                "synthetic": True,
                "snapshot_at": record["snapshot_at"],
                "as_of": None,
                "source_ids": [source["id"] for source in task["payload"]["sources"]],
                "complete": task["complete"],
                "dropped_citations": 0,
                "limitations": ["Údaje vybrány pravidly ze skutečné dokumentace, nikoli jazykovým modelem."],
                "facts": [{key: value for key, value in fact.items() if key != "code"} for fact in facts],
            },
        )
        by_code = {}
        for fact in facts:
            by_code.setdefault(fact["code"], fact)
        for study, extraction in studies:
            if study["criteria"][1][4](by_code)[0] == "not_met":
                continue  # Only compare studies for the patient's own diagnosis.
            decisions = []
            for identifier, _, _, _, rule in study["criteria"]:
                status, reason, codes, action = rule(by_code)
                fact_ids = [by_code[code]["id"] for code in codes]
                decisions.append(
                    Decision(
                        criterion_id=identifier,
                        status=status,
                        reason=reason,
                        fact_ids=fact_ids,
                        action=action,
                    )
                )
            parsed = TrialExtraction.model_validate(
                {**extraction, "logic": Logic.model_validate(extraction["logic"])}
            )
            verified = verify_decisions(parsed.criteria, decisions, {fact["id"]: fact for fact in facts})
            storage.put(
                "matches",
                f"{patient['id']}:{study['id']}",
                patient["id"],
                {
                    "patient_id": patient["id"],
                    "trial_id": study["id"],
                    "facts_key": task["key"],
                    "criteria_key": extraction["key"],
                    "model": MODEL,
                    "label": MATCH_LABEL,
                    "pipeline_version": VERSION,
                    "assessed_at": now,
                    "summary": summarize(parsed, verified),
                    "decisions": verified,
                    "future_watch": future_watch(parsed, verified),
                },
            )
        print(f"{patient['id']}: {len(facts)} údajů")
    print(f"Hotovo: {storage.PATH}")


if __name__ == "__main__":
    main()
