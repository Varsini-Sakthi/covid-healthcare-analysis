-- =====================================================================
-- COVID-19 Healthcare Impact Analysis — Relational Schema
-- Portable ANSI SQL (tested against SQLite; trivial edits for Postgres)
-- =====================================================================

DROP TABLE IF EXISTS resource_utilization;
DROP TABLE IF EXISTS hospitalizations;
DROP TABLE IF EXISTS rtpcr_tests;
DROP TABLE IF EXISTS patients;
DROP TABLE IF EXISTS facilities;
DROP TABLE IF EXISTS regions;

-- ------------------------------------------------------------
-- Dimension: regions (catchment areas served by facilities)
-- ------------------------------------------------------------
CREATE TABLE regions (
    region_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    region_name     TEXT NOT NULL,
    population      INTEGER NOT NULL
);

-- ------------------------------------------------------------
-- Dimension: facilities (hospitals / testing sites)
-- ------------------------------------------------------------
CREATE TABLE facilities (
    facility_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    facility_name   TEXT NOT NULL,
    region_id       INTEGER NOT NULL REFERENCES regions(region_id),
    facility_type   TEXT NOT NULL CHECK (facility_type IN ('Hospital','Testing Site','Field Clinic')),
    bed_capacity    INTEGER NOT NULL,
    icu_capacity    INTEGER NOT NULL,
    ventilator_count INTEGER NOT NULL
);

-- ------------------------------------------------------------
-- Dimension: patients (de-identified, synthetic)
-- ------------------------------------------------------------
CREATE TABLE patients (
    patient_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    age             INTEGER NOT NULL,
    gender          TEXT NOT NULL CHECK (gender IN ('M','F')),
    region_id       INTEGER NOT NULL REFERENCES regions(region_id),
    comorbidity_count INTEGER NOT NULL DEFAULT 0,
    risk_score      REAL NOT NULL  -- 0-1 composite risk index used for outcome modeling
);

-- ------------------------------------------------------------
-- Fact: RT-PCR test results
-- ------------------------------------------------------------
CREATE TABLE rtpcr_tests (
    test_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id      INTEGER NOT NULL REFERENCES patients(patient_id),
    facility_id     INTEGER NOT NULL REFERENCES facilities(facility_id),
    test_date       DATE NOT NULL,
    result          TEXT NOT NULL CHECK (result IN ('Positive','Negative','Inconclusive')),
    ct_value        REAL  -- cycle threshold; lower = higher viral load (NULL if negative)
);

-- ------------------------------------------------------------
-- Fact: hospitalizations (linked to a positive test where applicable)
-- ------------------------------------------------------------
CREATE TABLE hospitalizations (
    hosp_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id      INTEGER NOT NULL REFERENCES patients(patient_id),
    facility_id     INTEGER NOT NULL REFERENCES facilities(facility_id),
    admission_date  DATE NOT NULL,
    discharge_date  DATE,          -- NULL = still admitted at end of observation window
    icu_flag        INTEGER NOT NULL DEFAULT 0,  -- 1 = required ICU
    ventilator_flag INTEGER NOT NULL DEFAULT 0,  -- 1 = required mechanical ventilation
    outcome         TEXT CHECK (outcome IN ('Recovered','Deceased','Transferred', NULL))
);

-- ------------------------------------------------------------
-- Fact: daily resource utilization snapshots (for strain/capacity analysis)
-- ------------------------------------------------------------
CREATE TABLE resource_utilization (
    snapshot_date   DATE NOT NULL,
    facility_id     INTEGER NOT NULL REFERENCES facilities(facility_id),
    beds_occupied   INTEGER NOT NULL,
    icu_occupied    INTEGER NOT NULL,
    ventilators_in_use INTEGER NOT NULL,
    staff_available INTEGER NOT NULL,
    PRIMARY KEY (snapshot_date, facility_id)
);

-- ------------------------------------------------------------
-- Indexes to support the analytical queries in 02_analysis_queries.sql
-- ------------------------------------------------------------
CREATE INDEX idx_tests_date        ON rtpcr_tests(test_date);
CREATE INDEX idx_tests_result      ON rtpcr_tests(result);
CREATE INDEX idx_tests_facility    ON rtpcr_tests(facility_id);
CREATE INDEX idx_hosp_admission    ON hospitalizations(admission_date);
CREATE INDEX idx_hosp_facility     ON hospitalizations(facility_id);
CREATE INDEX idx_resource_date     ON resource_utilization(snapshot_date);
CREATE INDEX idx_patients_region   ON patients(region_id);
