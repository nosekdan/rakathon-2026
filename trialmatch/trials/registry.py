"""Parse the FNMH list of recruiting trials (xlsx) into a list of TrialEntry records."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

import openpyxl

from ..config import TRIALS_XLSX

# Czech diagnosis label (lowercase substring) -> ICD-10 prefixes. Used as the default diagnosis gate.
DIAGNOSIS_ICD = [
    ("hepatocelul", ["C22"]), ("kolorekt", ["C18", "C19", "C20"]), ("tlustého střeva", ["C18"]),
    ("pankrea", ["C25"]), ("žaludk", ["C16"]), ("žlučovod", ["C22", "C23", "C24"]), ("jícn", ["C15"]),
    ("prsu", ["C50"]), ("prostat", ["C61"]), ("ledvin", ["C64"]), ("renáln", ["C64"]),
    ("močového měchýře", ["C67"]), ("urotel", ["C65", "C66", "C67", "C68"]),
    ("endometri", ["C54"]), ("děložního hrdla", ["C53"]), ("cervix", ["C53"]),
    ("vaječník", ["C56", "C57", "C48"]), ("ovarian", ["C56", "C57", "C48"]),
    ("hlav", ["C00", "C01", "C02", "C03", "C04", "C05", "C06", "C09", "C10", "C11", "C12", "C13", "C14", "C30",
              "C31", "C32"]),
    ("nosohltan", ["C11"]), ("štítné", ["C73"]),
    ("dlbcl", ["C83"]), ("velkobuněčný b", ["C83"]), ("folikulární lymfom", ["C82"]),
    ("non-hodgkin", ["C82", "C83", "C84", "C85", "C86", "C88"]), ("lymfom", ["C81", "C82", "C83", "C84", "C85"]),
    ("plic", ["C34"]), ("nsclc", ["C34"]), ("melanom", ["C43"]), ("kachexie", ["R64"]),
]


@dataclass
class TrialEntry:
    local_id: str
    section: str
    protocol: str
    sponsor: str
    title_cs: str
    drug: str
    phase: str
    diagnosis_label: str
    year_opened: int | None
    icd_prefixes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def diagnosis_to_icd(label: str, title: str = "") -> list[str]:
    explicit = re.findall(r"\b([CDR]\d{2})\d?\b", label)
    text = f"{label} {title}".lower()
    found = [p for key, prefixes in DIAGNOSIS_ICD if key in text for p in prefixes]
    return sorted(set(explicit) | set(found))


def _year(serial) -> int | None:
    try:  # Excel serial date
        return 1899 + int((float(serial) - 60) // 365.2425) + 1 if serial else None
    except (TypeError, ValueError):
        return None


def load_registry(path=TRIALS_XLSX) -> list[TrialEntry]:
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0]
    trials, section = [], ""
    for row in ws.iter_rows(values_only=True):
        cells = [str(c).strip() if c is not None else "" for c in row[:10]] + [""] * 10
        num, year, protocol, sponsor, title, drug, phase, diag = cells[:8]
        if not any(cells):
            continue
        if num == "Číslo":
            continue
        if num and not title:
            section = num
            continue
        if not title:
            continue
        trials.append(TrialEntry(
            local_id=num or f"ROW-{len(trials) + 1}", section=section, protocol=protocol, sponsor=sponsor,
            title_cs=title, drug=drug, phase=phase, diagnosis_label=diag, year_opened=_year(year),
            icd_prefixes=diagnosis_to_icd(diag, title),
        ))
    seen: dict[str, int] = {}
    for t in trials:  # the xlsx reuses some local numbers for different protocols
        seen[t.local_id] = seen.get(t.local_id, 0) + 1
        if seen[t.local_id] > 1:
            t.local_id = f"{t.local_id}-{seen[t.local_id]}"
    return trials


if __name__ == "__main__":
    for t in load_registry():
        print(f"{t.local_id:15s} {t.section:12s} {t.protocol[:28]:28s} {','.join(t.icd_prefixes):20s} {t.title_cs[:60]}")
