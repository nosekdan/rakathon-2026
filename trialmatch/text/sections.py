"""Split Czech/English clinical notes into labelled sections (diagnosis, history, family history, conclusion, ...)."""
from __future__ import annotations

import re

# canonical section -> header regex (matched on folded text at line start)
SECTION_HEADERS: dict[str, str] = {
    "diagnosis": r"dg\.?|dx\.?|diagnoz[ay]|diagnosis|onkologicka diagnoza|zakladni dg\.?|hlavni dg\.?|dg\. zaver",
    "family_history": r"ra|rodinna anamneza|family history",
    "personal_history": r"oa|osobni anamneza|past medical history|pmh",
    "medication": r"fa|farmakologicka anamneza|medikace|chronicka medikace|medication",
    "allergy": r"aa|alergicka anamneza|alergie",
    "social": r"sa|socialni anamneza|pa|pracovni anamneza|abusus",
    "current_illness": r"no|nynejsi onemocneni|nynejsi potize|subj\.?|subjektivne|history of present illness",
    "oncology_history": r"onkologicka anamneza|onko anamneza|dosavadni lecba|prubeh onemocneni|onkologicka lecba",
    "exam": r"obj\.?|objektivne|status praesens|fyzikalni vysetreni|physical exam",
    "results": r"lab\.?|laborator|vysledky|zobrazovaci vysetreni|ct|mr|pet",
    "histology": r"histologie|histologicky|hist\.?|biopsie|patologie",
    "conclusion": r"zaver|souhrn|zhodnoceni|assessment|summary|impression|indikacni seminar|onkoboard|mdt",
    "plan": r"doporuceni|plan|terapie|procedura|recommendation|dalsi postup",
}
_HEADER_RE = re.compile(
    r"^[ \t]*(?:" + "|".join(f"(?P<{k}>(?:{v}))" for k, v in SECTION_HEADERS.items()) + r")[ \t]*(?::|-|\n)",
    re.MULTILINE,
)

# Facts found in these sections describe someone other than the patient, or are historic/uncertain.
NON_PATIENT_SECTIONS = {"family_history"}
SECTION_PRIORITY = {"diagnosis": 3, "conclusion": 3, "histology": 3, "oncology_history": 2, "plan": 2,
                    "results": 2, "current_illness": 2, "exam": 2, "personal_history": 1, "medication": 1,
                    "body": 1, "social": 0, "allergy": 0, "family_history": -1}


def split_sections(folded: str) -> list[tuple[str, int, int]]:
    """Return [(section, start, end)] covering the whole text; text before the first header is 'body'."""
    marks = [(m.start(), m.lastgroup) for m in _HEADER_RE.finditer(folded)]
    if not marks or marks[0][0] > 0:
        marks.insert(0, (0, "body"))
    out = []
    for i, (start, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(folded)
        out.append((name, start, end))
    return out


def section_at(sections: list[tuple[str, int, int]], pos: int) -> str:
    for name, s, e in sections:
        if s <= pos < e:
            return name
    return "body"
