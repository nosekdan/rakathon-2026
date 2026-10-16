from screening import Criterion, Evidence, Logic, TrialExtraction, badge, locate_evidence, summarize


def demo_data():
    sources = {
        "demo-note": {
            "id": "demo-note",
            "title": "Syntetická ambulantní zpráva",
            "date": "2026-10-10",
            "kind": "DocumentReference",
            "text": "SYNTETICKÝ ZÁZNAM, NEJDE O SKUTEČNÉHO PACIENTA.\nPacient: Demo A. Věk: 64 let. Pohlaví: muž.\nDiagnóza: karcinom prostaty C61.\nVýška: 180 cm. Váha: 78 kg. ECOG: 1.\nPři současné terapii je onemocnění stabilní, bez progrese.\nBRCA2: varianta nejasného klinického významu.\nAktuální krevní obraz není v dokumentaci k dispozici.",
        },
        "demo-protocol": {
            "id": "demo-protocol",
            "title": "Syntetické protokoly / ukázka",
            "date": "2026-10-10",
            "kind": "protocol",
            "text": "SYNTETICKÉ STUDIE, NEJDE O SKUTEČNÝ NÁBOR.\nDEMO-01: Věk alespoň 18 let. Karcinom prostaty. ECOG 0 nebo 1.\nDEMO-02: Věk alespoň 18 let. Karcinom prostaty. ECOG 0 nebo 1. Dokumentovaná progrese při současné terapii.\nDEMO-03: Věk alespoň 18 let. Karcinom prostaty. Patogenní mutace BRCA2. Hemoglobin alespoň 100 g/l v posledních 14 dnech.",
        },
    }
    patient = {
        "id": "DEMO-A",
        "name": "Demo pacient A",
        "sex": "male",
        "age": "64",
        "birth_year": None,
        "diagnoses": "C61",
        "target": True,
        "deceased": False,
        "notes": 1,
        "labs": 0,
        "snapshot_at": "2026-10-10",
        "version": "synthetic-v1",
        "synthetic": True,
        "sources": list(sources.values()),
        "latest_labs": [],
        "treatments": [],
        "recent_notes": [{key: value for key, value in sources["demo-note"].items() if key != "text"}],
        "vitals": {},
    }
    for name, value, quote in [
        ("height", "180", "Výška: 180 cm"),
        ("weight", "78", "Váha: 78 kg"),
        ("ecog", "1", "ECOG: 1"),
    ]:
        patient["vitals"][name] = {
            "value": value,
            "date": "2026-10-10",
            **locate_evidence(Evidence(source_id="demo-note", quote=quote), sources),
        }
    definitions = [
        (
            "age",
            "Věk alespoň 18 let.",
            "Věk: 64 let",
            "met",
            "Věk 64 let splňuje dolní hranici 18 let.",
            None,
            "none",
        ),
        (
            "diagnosis",
            "Karcinom prostaty.",
            "Diagnóza: karcinom prostaty C61.",
            "met",
            "V dokumentaci je výslovně uveden karcinom prostaty.",
            None,
            "none",
        ),
        ("ecog", "ECOG 0 nebo 1.", "ECOG: 1", "met", "ECOG 1 je v požadovaném rozsahu.", None, "none"),
        (
            "progression",
            "Dokumentovaná progrese při současné terapii.",
            "Při současné terapii je onemocnění stabilní, bez progrese.",
            "not_met",
            "Poslední zpráva popisuje stabilní onemocnění bez progrese.",
            "Znovu posoudit při dokumentované progresi.",
            "progression",
        ),
        (
            "brca",
            "Patogenní mutace BRCA2.",
            "BRCA2: varianta nejasného klinického významu.",
            "unknown",
            "Varianta nejasného významu není potvrzením patogenní mutace.",
            "Vyžádat interpretaci molekulárního nálezu.",
            "none",
        ),
        (
            "hb",
            "Hemoglobin alespoň 100 g/l v posledních 14 dnech.",
            None,
            "missing",
            "V posouzeném záznamu není aktuální hodnota hemoglobinu.",
            "Doplnit aktuální krevní obraz a posoudit výsledek.",
            "none",
        ),
    ]
    studies = []
    results = []
    for index, (title, included) in enumerate(
        [
            ("Základní screening", [0, 1, 2]),
            ("Po progresi onemocnění", [0, 1, 2, 3]),
            ("Biomarkerová studie", [0, 1, 4, 5]),
        ],
        1,
    ):
        trial_id = f"DEMO-0{index}"
        criteria = []
        decisions = []
        for position in included:
            identifier, requirement, quote, status, reason, action, trigger = definitions[position]
            criteria.append(
                Criterion(
                    id=identifier,
                    requirement=requirement,
                    role="inclusion",
                    source=Evidence(source_id="demo-protocol", quote=requirement),
                    trigger=trigger,
                )
            )
            decisions.append(
                {
                    "criterion_id": identifier,
                    "status": status,
                    "reason": reason,
                    "action": action,
                    "evidence": [locate_evidence(Evidence(source_id="demo-note", quote=quote), sources)]
                    if quote
                    else [],
                }
            )
        extraction = TrialExtraction(
            criteria=criteria,
            logic=Logic(op="all", children=[Logic(op="ref", criterion_id=item.id) for item in criteria]),
            complete=True,
        )
        registry = {"status": "RECRUITING", "site_recruiting": True, "state": "synthetic", "url": None}
        summary = summarize(extraction, decisions)
        summary["badge"] = badge(
            summary["status"],
            criteria_complete=True,
            facts_complete=True,
            recruiting=True,
            site_recruiting=True,
            deceased=False,
        )
        studies.append(
            {
                "id": trial_id,
                "protocol": trial_id,
                "title": title,
                "diagnosis": "Karcinom prostaty",
                "sponsor": "Syntetická ukázka",
                "phase": "Demo",
                "registry": registry,
            }
        )
        results.append(
            {
                "patient_id": patient["id"],
                "trial_id": trial_id,
                "summary": summary,
                "criteria": extraction.model_dump(),
                "decisions": decisions,
                "registry": registry,
                "model": "Syntetická ukázka, bez inference",
                "patient_facts": {"facts": [], "source_ids": ["demo-note"], "complete": True},
                "assessed_at": "2026-10-10",
                "snapshot_at": "2026-10-10",
                "limitations": ["Syntetický scénář, nikoli skutečná klinická studie."],
                "future_watch": [
                    {
                        "criterion_id": "progression",
                        "trigger": "progression",
                        "text": "Znovu posoudit po dokumentované progresi.",
                        "caveat": "Budoucí způsobilost není zaručena. Všechny podmínky je nutné ověřit znovu.",
                    }
                ]
                if index == 2
                else [],
            }
        )
    patient["results"] = results
    return {"patient": patient, "trials": studies}
