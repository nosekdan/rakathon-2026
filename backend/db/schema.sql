-- AI Trial Matching: database schema
-- Facts are stored in long format (patient x concept), so a new trial criterion
-- only adds a row to `concept`, never a new column.

CREATE TABLE IF NOT EXISTS concept (
    id              TEXT PRIMARY KEY,          -- e.g. 'ecog', 'lab.egfr', 'dx.icd10'
    name            TEXT NOT NULL,
    value_type      TEXT NOT NULL CHECK (value_type IN ('number', 'text', 'bool', 'code', 'date')),
    unit            TEXT,
    source          TEXT NOT NULL CHECK (source IN ('structured', 'llm')),
    extraction_hint TEXT,                      -- instructions for the LLM extractor
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS patient (
    id                TEXT PRIMARY KEY,        -- pseudonym PAC-...
    sex               TEXT,
    birth_year        INT,
    birth_year_band   TEXT,                    -- k-anonymity band, e.g. '1950-1954'
    age               INT,
    age_band          TEXT,
    deceased          BOOLEAN,
    generalization    INT,                     -- k-anonymity level 0-7
    last_record_at    TIMESTAMPTZ              -- reference "now" for time windows (dates are shifted)
);

CREATE TABLE IF NOT EXISTS patient_fact (
    id              BIGSERIAL PRIMARY KEY,
    patient_id      TEXT NOT NULL REFERENCES patient(id) ON DELETE CASCADE,
    concept_id      TEXT NOT NULL REFERENCES concept(id),
    value_num       DOUBLE PRECISION,
    value_text      TEXT,
    observed_at     TIMESTAMPTZ,
    source_doc_id   TEXT,                      -- DOC-..., LAB-..., STU-...
    evidence        TEXT,                      -- quote from the source supporting the value
    confidence      REAL,                      -- 1.0 for structured data
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS patient_fact_lookup ON patient_fact (patient_id, concept_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS trial (
    id              TEXT PRIMARY KEY,          -- NCT id
    title           TEXT NOT NULL,
    status          TEXT,
    conditions      TEXT[],
    eligibility_raw TEXT,                      -- original eligibility text from ClinicalTrials.gov
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS criterion (
    id              BIGSERIAL PRIMARY KEY,
    trial_id        TEXT NOT NULL REFERENCES trial(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('inclusion', 'exclusion')),
    raw_text        TEXT NOT NULL,
    concept_id      TEXT REFERENCES concept(id),
    operator        TEXT,                      -- '>=', '<=', '=', 'in', 'not_in', 'exists', ...
    value           JSONB,
    window_days     INT,                       -- value must be observed within N days
    approved_by     TEXT,                      -- doctor who verified the parsed rule
    approved_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS match_result (
    patient_id      TEXT NOT NULL REFERENCES patient(id) ON DELETE CASCADE,
    criterion_id    BIGINT NOT NULL REFERENCES criterion(id) ON DELETE CASCADE,
    status          TEXT NOT NULL CHECK (status IN ('MET', 'NOT_MET', 'UNKNOWN')),
    explanation     TEXT,
    fact_id         BIGINT REFERENCES patient_fact(id) ON DELETE SET NULL,
    evaluated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (patient_id, criterion_id)
);
