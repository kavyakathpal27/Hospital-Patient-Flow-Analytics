-- Star schema for hospital patient-flow analytics.
-- Loaded automatically by src/clean_data.py into data/processed/hospital.db (SQLite).
-- Written in ANSI-compatible SQL; also runs unmodified on PostgreSQL.

CREATE TABLE dim_department (
    department_id       INTEGER PRIMARY KEY,
    department_name     TEXT NOT NULL,
    total_beds           INTEGER NOT NULL,
    target_wait_minutes INTEGER NOT NULL,
    base_daily_volume   INTEGER NOT NULL,
    is_emergency        BOOLEAN NOT NULL
);

CREATE TABLE fact_staff_shift (
    shift_id                INTEGER PRIMARY KEY,
    department_id           INTEGER NOT NULL REFERENCES dim_department(department_id),
    shift_date               DATE NOT NULL,
    shift_type              TEXT NOT NULL CHECK (shift_type IN ('Morning', 'Afternoon', 'Night')),
    doctors_on_shift        INTEGER NOT NULL,
    nurses_on_shift          INTEGER NOT NULL,
    support_staff_on_shift  INTEGER NOT NULL
);

CREATE TABLE fact_patient_visits (
    visit_id                INTEGER PRIMARY KEY,
    patient_id               INTEGER NOT NULL,
    department_id            INTEGER NOT NULL REFERENCES dim_department(department_id),
    arrival_datetime         TIMESTAMP NOT NULL,
    triage_datetime          TIMESTAMP NOT NULL,
    bed_assigned_datetime    TIMESTAMP NOT NULL,
    discharge_datetime       TIMESTAMP NOT NULL,
    arrival_date              DATE NOT NULL,
    arrival_hour              INTEGER NOT NULL,
    arrival_dow               TEXT NOT NULL,
    arrival_month             INTEGER NOT NULL,
    shift_type                TEXT NOT NULL,
    admission_type            TEXT NOT NULL,
    age                       INTEGER NOT NULL,
    gender                    TEXT NOT NULL,
    diagnosis                 TEXT NOT NULL,
    wait_time_minutes         REAL NOT NULL,
    length_of_stay_hours     REAL NOT NULL,
    readmitted_30d            BOOLEAN NOT NULL,
    satisfaction_score        INTEGER NOT NULL,
    billing_amount            REAL NOT NULL
);

CREATE INDEX idx_visits_dept ON fact_patient_visits(department_id);
CREATE INDEX idx_visits_date ON fact_patient_visits(arrival_date);
CREATE INDEX idx_shift_dept_date ON fact_staff_shift(department_id, shift_date);
