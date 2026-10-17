from screening import (
    Criterion,
    Evidence,
    Logic,
    TrialExtraction,
    badge,
    future_watch,
    locate_evidence,
    summarize,
)

DATE = "2026-10-10"
PROTOCOL = {
    "id": "demo-protocol",
    "title": "Syntetické protokoly / ukázka",
    "date": DATE,
    "kind": "protocol",
    "text": "SYNTETICKÉ STUDIE, NEJDE O SKUTEČNÝ NÁBOR.\n"
    "DEMO-01: Věk alespoň 18 let. Karcinom prostaty. ECOG 0 nebo 1.\n"
    "DEMO-02: Věk alespoň 18 let. Karcinom prostaty. ECOG 0 nebo 1. "
    "Dokumentovaná progrese při současné terapii.\n"
    "DEMO-03: Věk alespoň 18 let. Karcinom prostaty. Patogenní mutace BRCA2. "
    "Hemoglobin alespoň 100 g/l v posledních 14 dnech.\n"
    "DEMO-04: Věk alespoň 18 let. Karcinom ledviny. ECOG 0 nebo 1. "
    "Hemoglobin alespoň 100 g/l v posledních 14 dnech.\n"
    "DEMO-05 (ukončena): Věk alespoň 18 let. Karcinom prostaty. ECOG 0 nebo 1.\n"
    "DEMO-06 (zatím nenabírá): Věk alespoň 18 let. Karcinom ledviny. ECOG 0 nebo 1.",
}
DIAGNOSES = {"C61": "karcinom prostaty", "C64": "karcinom ledviny"}
REQUIREMENTS = {
    "age": ("Věk alespoň 18 let.", "none"),
    "prostate": ("Karcinom prostaty.", "none"),
    "kidney": ("Karcinom ledviny.", "none"),
    "ecog": ("ECOG 0 nebo 1.", "none"),
    "progression": ("Dokumentovaná progrese při současné terapii.", "progression"),
    "brca": ("Patogenní mutace BRCA2.", "none"),
    "hb": ("Hemoglobin alespoň 100 g/l v posledních 14 dnech.", "none"),
}
# The last two are not open, so the "Otevřené" filter and the recruiting condition of the alert are visible.
STUDIES = [
    ("DEMO-01", "Základní screening", "C61", ["age", "prostate", "ecog"], "RECRUITING"),
    ("DEMO-02", "Po progresi onemocnění", "C61", ["age", "prostate", "ecog", "progression"], "RECRUITING"),
    ("DEMO-03", "Biomarkerová studie", "C61", ["age", "prostate", "brca", "hb"], "RECRUITING"),
    ("DEMO-04", "Karcinom ledviny, první linie", "C64", ["age", "kidney", "ecog", "hb"], "RECRUITING"),
    ("DEMO-05", "Ukončená studie hormonální léčby", "C61", ["age", "prostate", "ecog"], "COMPLETED"),
    (
        "DEMO-06",
        "Připravovaná studie, adjuvance ledvin",
        "C64",
        ["age", "kidney", "ecog"],
        "NOT_YET_RECRUITING",
    ),
]
# Synthetic patients. A is the original walkthrough: one study met, one blocked by progression, one unclear.
PROFILES = [
    {
        "letter": "A",
        "age": 64,
        "sex": "male",
        "dx": "C61",
        "height": 180,
        "weight": 78,
        "ecog": 1,
        "progression": False,
        "brca": "vus",
        "hb": None,
    },
    {
        "letter": "B",
        "age": 71,
        "sex": "male",
        "dx": "C61",
        "height": 176,
        "weight": 84,
        "ecog": 0,
        "progression": True,
        "brca": "pathogenic",
        "hb": 132,
        "psa": "18,4",
        "tnm": "cT3a N1 M1b",
        "gleason": "4 + 4 = 8",
        "therapy": "ADT a enzalutamid od 03/2025",
        "comorbidity": "arteriální hypertenze",
    },
    {
        "letter": "C",
        "age": 58,
        "sex": "male",
        "dx": "C61",
        "height": 182,
        "weight": 91,
        "ecog": 2,
        "progression": True,
        "brca": None,
        "hb": 118,
        "psa": "42,0",
        "tnm": "cT4 N1 M1c",
        "therapy": "docetaxel, 4. cyklus",
        "comorbidity": "diabetes mellitus 2. typu",
    },
    {
        "letter": "D",
        "age": 77,
        "sex": "male",
        "dx": "C61",
        "height": 171,
        "weight": 69,
        "ecog": 1,
        "progression": True,
        "brca": "negative",
        "hb": 96,
        "psa": "9,7",
        "gleason": "4 + 3 = 7",
        "therapy": "abirateron s prednisonem",
        "comorbidity": "chronická renální insuficience G3a",
    },
    {
        "letter": "E",
        "age": 66,
        "sex": "male",
        "dx": "C61",
        "height": 178,
        "weight": 80,
        "ecog": None,
        "progression": None,
        "brca": None,
        "hb": None,
        "psa": "5,2",
        "gleason": "3 + 4 = 7",
    },
    {
        "letter": "F",
        "age": 69,
        "sex": "male",
        "dx": "C61",
        "height": 174,
        "weight": 88,
        "ecog": 1,
        "progression": True,
        "brca": "vus",
        "hb": 104,
        "psa": "27,9",
        "tnm": "pT3b N0 M1a",
        "therapy": "ADT, po radikální prostatektomii",
        "comorbidity": "fibrilace síní",
    },
    {
        "letter": "G",
        "age": 82,
        "sex": "male",
        "dx": "C61",
        "height": 168,
        "weight": 62,
        "ecog": 3,
        "progression": False,
        "brca": None,
        "hb": 98,
        "psa": "3,1",
        "therapy": "ADT",
        "comorbidity": "CHOPN",
    },
    {
        "letter": "H",
        "age": 55,
        "sex": "male",
        "dx": "C61",
        "height": 185,
        "weight": 86,
        "ecog": 0,
        "progression": False,
        "brca": "pathogenic",
        "hb": 141,
        "psa": "1,8",
        "tnm": "cT2c N0 M1b",
        "gleason": "4 + 5 = 9",
        "therapy": "ADT a apalutamid",
    },
    {
        "letter": "I",
        "age": 62,
        "sex": "female",
        "dx": "C64",
        "height": 166,
        "weight": 64,
        "ecog": 0,
        "hb": 125,
        "tnm": "pT3a N0 M1",
        "therapy": "po radikální nefrektomii, bez systémové léčby",
    },
    {
        "letter": "J",
        "age": 70,
        "sex": "male",
        "dx": "C64",
        "height": 177,
        "weight": 95,
        "ecog": 1,
        "hb": 92,
        "tnm": "cT2b N1 M1",
        "comorbidity": "ischemická choroba srdeční",
    },
    {
        "letter": "K",
        "age": 48,
        "sex": "female",
        "dx": "C64",
        "height": 170,
        "weight": 58,
        "ecog": None,
        "hb": 130,
        "therapy": "po parciální nefrektomii",
    },
    {
        "letter": "L",
        "age": 74,
        "sex": "male",
        "dx": "C64",
        "height": 172,
        "weight": 79,
        "ecog": 2,
        "hb": 109,
        "tnm": "cT3a N0 M1",
        "therapy": "sunitinib, 2. linie",
        "comorbidity": "arteriální hypertenze",
    },
]


def note_lines(profile: dict) -> dict[str, str]:
    """The note's sentences by topic; facts and decisions quote them exactly."""
    sex = "muž" if profile["sex"] == "male" else "žena"
    lines = {
        "header": "SYNTETICKÝ ZÁZNAM, NEJDE O SKUTEČNÉHO PACIENTA.",
        "age": f"Věk: {profile['age']} let",
        "sex": f"Pohlaví: {sex}",
        "dx": f"Diagnóza: {DIAGNOSES[profile['dx']]} {profile['dx']}.",
        "height": f"Výška: {profile['height']} cm",
        "weight": f"Váha: {profile['weight']} kg",
    }
    if profile.get("ecog") is not None:
        lines["ecog"] = f"ECOG: {profile['ecog']}"
    for key, template in (
        ("tnm", "Staging: {}."),
        ("gleason", "Gleason skóre: {}."),
        ("psa", "PSA: {} µg/l."),
        ("therapy", "Léčba: {}."),
        ("comorbidity", "Komorbidity: {}."),
    ):
        if profile.get(key):
            lines[key] = template.format(profile[key])
    progression = profile.get("progression")
    if progression is not None:
        lines["progression"] = (
            "Na současné terapii dokumentována progrese onemocnění."
            if progression
            else "Při současné terapii je onemocnění stabilní, bez progrese."
        )
    brca = {
        "pathogenic": "BRCA2: patogenní mutace.",
        "vus": "BRCA2: varianta nejasného klinického významu.",
        "negative": "BRCA1/2: bez patogenní mutace.",
    }.get(profile.get("brca"))
    if brca:
        lines["brca"] = brca
    lines["hb"] = (
        f"Hemoglobin: {profile['hb']} g/l."
        if profile.get("hb")
        else "Aktuální krevní obraz není v dokumentaci k dispozici."
    )
    return lines


def note_text(profile: dict, lines: dict[str, str]) -> str:
    vitals = f"{lines['height']}. {lines['weight']}." + (f" {lines['ecog']}." if "ecog" in lines else "")
    rest = ("tnm", "gleason", "psa", "therapy", "comorbidity", "progression", "brca", "hb")
    return "\n".join(
        [
            lines["header"],
            f"Pacient: Demo {profile['letter']}. {lines['age']}. {lines['sex']}.",
            lines["dx"],
            vitals,
            *[lines[key] for key in rest if key in lines],
        ]
    )


def decide(identifier: str, profile: dict, lines: dict[str, str]) -> tuple[str, str, str | None, str | None]:
    """Status, reason, quoted line and suggested action for one criterion."""
    if identifier == "age":
        return "met", f"Věk {profile['age']} let splňuje dolní hranici 18 let.", lines["age"], None
    if identifier in ("prostate", "kidney"):
        label = DIAGNOSES[profile["dx"]]
        return "met", f"V dokumentaci je výslovně uveden {label}.", lines["dx"], None
    if identifier == "ecog":
        if profile.get("ecog") is None:
            return "missing", "Výkonnostní stav (ECOG) není v dokumentaci uveden.", None, "Doplnit ECOG."
        if profile["ecog"] <= 1:
            return "met", f"ECOG {profile['ecog']} je v požadovaném rozsahu.", lines["ecog"], None
        return "not_met", f"ECOG {profile['ecog']} je mimo požadovaný rozsah 0–1.", lines["ecog"], None
    if identifier == "progression":
        progression = profile.get("progression")
        if progression is None:
            return "missing", "Vývoj onemocnění na současné terapii není popsán.", None, "Doplnit restaging."
        if progression:
            return "met", "Zpráva dokumentuje progresi při současné terapii.", lines["progression"], None
        return (
            "not_met",
            "Poslední zpráva popisuje stabilní onemocnění bez progrese.",
            lines["progression"],
            "Znovu posoudit při dokumentované progresi.",
        )
    if identifier == "brca":
        return {
            "pathogenic": ("met", "Dokumentována patogenní mutace BRCA2.", lines.get("brca"), None),
            "vus": (
                "unknown",
                "Varianta nejasného významu není potvrzením patogenní mutace.",
                lines.get("brca"),
                "Vyžádat interpretaci molekulárního nálezu.",
            ),
            "negative": (
                "not_met",
                "Genetické vyšetření patogenní mutaci neprokázalo.",
                lines.get("brca"),
                None,
            ),
        }.get(
            profile.get("brca"),
            (
                "missing",
                "Výsledek genetického vyšetření není k dispozici.",
                None,
                "Zvážit vyšetření BRCA1/2.",
            ),
        )
    if identifier == "hb":
        if not profile.get("hb"):
            return (
                "missing",
                "V posouzeném záznamu není aktuální hodnota hemoglobinu.",
                None,
                "Doplnit aktuální krevní obraz a posoudit výsledek.",
            )
        status = "met" if profile["hb"] >= 100 else "not_met"
        return status, f"Hemoglobin {profile['hb']} g/l (požadováno alespoň 100 g/l).", lines["hb"], None
    raise ValueError(identifier)


FACTS = [
    ("age", "zaklad", "Věk", lambda p: f"{p['age']} let"),
    ("sex", "zaklad", "Pohlaví", lambda p: "Muž" if p["sex"] == "male" else "Žena"),
    ("height", "zaklad", "Výška", lambda p: f"{p['height']} cm"),
    ("weight", "zaklad", "Hmotnost", lambda p: f"{p['weight']} kg"),
    ("ecog", "zaklad", "ECOG", lambda p: str(p["ecog"])),
    ("dx", "nador", "Diagnóza", lambda p: f"{DIAGNOSES[p['dx']].capitalize()} ({p['dx']})"),
    ("tnm", "nador", "Klasifikace TNM", lambda p: p["tnm"]),
    ("gleason", "nador", "Gleason skóre", lambda p: p["gleason"]),
    (
        "brca",
        "nador",
        "BRCA",
        lambda p: {
            "pathogenic": "Patogenní mutace BRCA2",
            "vus": "VUS v BRCA2",
            "negative": "Bez patogenní mutace",
        }[p["brca"]],
    ),
    (
        "progression",
        "nador",
        "Vývoj onemocnění",
        lambda p: "Progrese na současné terapii" if p["progression"] else "Stabilní, bez progrese",
    ),
    ("psa", "laborator", "PSA", lambda p: f"{p['psa']} µg/l"),
    ("hb", "laborator", "Hemoglobin", lambda p: f"{p['hb']} g/l"),
    ("therapy", "lecba", "Současná léčba", lambda p: p["therapy"]),
    ("comorbidity", "ostatni", "Komorbidity", lambda p: p["comorbidity"]),
]


def demo_patient(profile: dict, studies: list[dict], extractions: dict[str, TrialExtraction]) -> dict:
    lines = note_lines(profile)
    note = {
        "id": "demo-note",
        "title": "Syntetická ambulantní zpráva",
        "date": DATE,
        "kind": "DocumentReference",
        "text": note_text(profile, lines),
    }
    sources = {"demo-note": note, "demo-protocol": PROTOCOL}

    def evidence(quote: str) -> dict:
        located = locate_evidence(Evidence(source_id="demo-note", quote=quote), sources)
        assert located, quote
        return located

    patient = {
        "id": f"DEMO-{profile['letter']}",
        "name": f"Demo pacient {profile['letter']}",
        "sex": profile["sex"],
        "age": str(profile["age"]),
        "birth_year": None,
        "diagnoses": profile["dx"],
        "target": True,
        "deceased": False,
        "notes": 1,
        "labs": 0,
        "snapshot_at": DATE,
        "version": "synthetic-v2",
        "synthetic": True,
        "sources": list(sources.values()),
        "latest_labs": [],
        "treatments": [],
        "recent_notes": [{key: value for key, value in note.items() if key != "text"}],
        # Where the card's demographics come from, so the source viewer highlights the right sentence.
        "evidence": {key: evidence(lines[key]) for key in ("age", "sex", "dx")},
        "vitals": {},
    }
    for name, key in (("height", "height"), ("weight", "weight"), ("ecog", "ecog")):
        if key in lines:
            patient["vitals"][name] = {"value": str(profile[key]), "date": DATE, **evidence(lines[key])}
    facts = []
    for key, category, name, value in FACTS:
        if key in lines and profile.get(key) is not None:
            facts.append(
                {
                    "id": f"F{len(facts) + 1}",
                    "name": name,
                    "value": value(profile),
                    "date": DATE,
                    "category": category,
                    "evidence": evidence(lines[key]),
                }
            )
    patient["facts"] = facts
    results = []
    for study in studies:
        if study["dx"] != profile["dx"]:
            continue
        extraction = extractions[study["id"]]
        decisions = []
        for criterion in extraction.criteria:
            status, reason, quote, action = decide(criterion.id, profile, lines)
            decisions.append(
                {
                    "criterion_id": criterion.id,
                    "status": status,
                    "reason": reason,
                    "action": action,
                    "evidence": [evidence(quote)] if quote else [],
                }
            )
        summary = summarize(extraction, decisions)
        summary["badge"] = badge(
            summary["status"],
            criteria_complete=True,
            facts_complete=True,
            recruiting=study["registry"]["status"] == "RECRUITING",
            site_recruiting=study["registry"]["site_recruiting"],
            deceased=False,
        )
        results.append(
            {
                "patient_id": patient["id"],
                "trial_id": study["id"],
                "summary": summary,
                "criteria": extraction.model_dump(),
                "decisions": decisions,
                "registry": study["registry"],
                "model": "Syntetická ukázka, bez inference",
                "patient_facts": {"facts": [], "source_ids": ["demo-note"], "complete": True},
                "assessed_at": DATE,
                "snapshot_at": DATE,
                "limitations": ["Syntetický scénář, nikoli skutečná klinická studie."],
                "future_watch": future_watch(extraction, decisions),
            }
        )
    patient["results"] = results
    return patient


def demo_data():
    studies = []
    extractions = {}
    for trial_id, title, dx, criteria, status in STUDIES:
        recruiting = status == "RECRUITING"
        registry = {"status": status, "site_recruiting": recruiting, "state": "synthetic", "url": None}
        extractions[trial_id] = TrialExtraction(
            criteria=[
                Criterion(
                    id=identifier,
                    requirement=REQUIREMENTS[identifier][0],
                    role="inclusion",
                    source=Evidence(source_id="demo-protocol", quote=REQUIREMENTS[identifier][0]),
                    trigger=REQUIREMENTS[identifier][1],
                )
                for identifier in criteria
            ],
            logic=Logic(
                op="all", children=[Logic(op="ref", criterion_id=identifier) for identifier in criteria]
            ),
            complete=True,
        )
        studies.append(
            {
                "id": trial_id,
                "protocol": trial_id,
                "title": title,
                "diagnosis": DIAGNOSES[dx].capitalize(),
                "sponsor": "Syntetická ukázka",
                "phase": "Demo",
                "registry": registry,
                "dx": dx,
            }
        )
    patients = [demo_patient(profile, studies, extractions) for profile in PROFILES]
    return {"patient": patients[0], "patients": patients, "trials": studies}
