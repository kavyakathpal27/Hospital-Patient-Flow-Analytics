"""
ETL: clean the raw hospital extract and load a star schema into SQLite.

Cleaning steps (with before/after counts logged to reports/cleaning_log.json):
  1. Drop exact duplicate visit_id rows introduced by the (simulated) re-extract.
  2. Parse mixed-format datetime strings (ISO + US MM/DD/YYYY) into a single dtype.
  3. Strip currency symbols/commas from billing_amount and cast to float.
  4. Normalize gender labels (F/f/Female -> Female, M/m/Male -> Male), impute
     unresolvable missing values as "Unknown" rather than dropping rows.
  5. Normalize admission_type casing.
  6. Impute missing age with the department-level median age.
  7. Impute missing satisfaction_score with the department-level median score.
  8. Drop rows with impossible (negative) wait_time_minutes.
"""
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
REPORTS_DIR = ROOT / "reports"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = PROCESSED_DIR / "hospital.db"

log = {"steps": []}


def record(step, detail):
    log["steps"].append({"step": step, "detail": detail})
    print(f"[{step}] {detail}")


def parse_mixed_datetime(series: pd.Series) -> pd.Series:
    # format="mixed" infers the format per-element, which correctly handles a
    # column containing both ISO ("2024-01-01 07:23:00.123456") and US
    # ("01/15/2024 07:23") style strings.
    return pd.to_datetime(series, format="mixed", errors="coerce")


def clean_billing(series: pd.Series) -> pd.Series:
    cleaned = series.astype(str).str.replace(r"[$,]", "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce")


def normalize_gender(series: pd.Series) -> pd.Series:
    mapping = {
        "female": "Female", "f": "Female",
        "male": "Male", "m": "Male",
    }
    normalized = series.str.strip().str.lower().map(mapping)
    return normalized.fillna("Unknown")


def normalize_admission_type(series: pd.Series) -> pd.Series:
    return series.str.strip().str.title()


def main():
    dim_department = pd.read_csv(RAW_DIR / "dim_department.csv")
    # Keep shift_date as a plain "YYYY-MM-DD" string (not parsed to Timestamp) so it
    # joins cleanly against fact_patient_visits.arrival_date, which is stored the same way.
    fact_staff_shift = pd.read_csv(RAW_DIR / "fact_staff_shift.csv", dtype={"shift_date": str})
    raw = pd.read_csv(RAW_DIR / "fact_patient_visits.csv")

    n_raw = len(raw)
    record("load_raw", f"{n_raw:,} raw visit rows loaded")

    # 1) Duplicates
    before = len(raw)
    raw = raw.drop_duplicates(subset=["visit_id"], keep="first")
    record("drop_duplicates", f"removed {before - len(raw):,} duplicate visit_id rows "
                               f"({(before - len(raw)) / before:.2%} of raw extract)")

    # 2) Datetimes
    dt_cols = ["arrival_datetime", "triage_datetime", "bed_assigned_datetime", "discharge_datetime"]
    for col in dt_cols:
        raw[col] = parse_mixed_datetime(raw[col])
    unparsed = raw[dt_cols].isna().sum().sum()
    record("parse_datetimes", f"parsed mixed ISO/US datetime formats across {len(dt_cols)} columns "
                               f"({unparsed} unparseable values found)")

    # 3) Billing amount
    before_missing = raw["billing_amount"].isna().sum()
    raw["billing_amount"] = clean_billing(raw["billing_amount"])
    record("clean_billing", f"stripped currency symbols/commas from billing_amount "
                             f"({raw['billing_amount'].isna().sum() - before_missing} coercion failures)")

    # 4) Gender
    missing_gender_before = raw["gender"].isna().sum()
    raw["gender"] = normalize_gender(raw["gender"])
    record("normalize_gender", f"standardized gender labels; imputed {missing_gender_before:,} missing "
                                f"values ({missing_gender_before / n_raw:.2%} of raw extract) as 'Unknown'")

    # 5) Admission type
    raw["admission_type"] = normalize_admission_type(raw["admission_type"])
    record("normalize_admission_type", "standardized admission_type casing (title case)")

    # 6) Age imputation (department-level median)
    missing_age_before = raw["age"].isna().sum()
    raw["age"] = raw.groupby("department_id")["age"].transform(lambda s: s.fillna(s.median()))
    record("impute_age", f"imputed {missing_age_before:,} missing age values "
                          f"({missing_age_before / n_raw:.2%} of raw extract) with department median")

    # 7) Satisfaction score imputation
    missing_sat_before = raw["satisfaction_score"].isna().sum()
    raw["satisfaction_score"] = raw.groupby("department_id")["satisfaction_score"].transform(
        lambda s: s.fillna(round(s.median()))
    )
    record("impute_satisfaction", f"imputed {missing_sat_before:,} missing satisfaction_score values "
                                   f"({missing_sat_before / n_raw:.2%} of raw extract) with department median")

    # 8) Impossible wait times
    before = len(raw)
    raw = raw[raw["wait_time_minutes"] >= 0]
    record("drop_invalid_wait_times", f"removed {before - len(raw):,} rows with negative wait_time_minutes")

    # Derived fields for analysis
    raw["arrival_hour"] = raw["arrival_datetime"].dt.hour
    raw["arrival_dow"] = raw["arrival_datetime"].dt.day_name()
    raw["arrival_date"] = raw["arrival_datetime"].dt.date.astype(str)
    raw["arrival_month"] = raw["arrival_datetime"].dt.month
    raw["shift_type"] = pd.cut(
        raw["arrival_hour"], bins=[-1, 6, 14, 22, 23], labels=["Night", "Morning", "Afternoon", "Night"], ordered=False
    )
    # cut() with duplicate label edges collapses correctly for 24h wrap; fix the 23:00 hour bucket
    raw.loc[raw["arrival_hour"] == 23, "shift_type"] = "Night"

    n_clean = len(raw)
    total_removed_or_flagged = n_raw - n_clean
    record("summary", f"{n_raw:,} raw rows -> {n_clean:,} clean rows "
                       f"({total_removed_or_flagged / n_raw:.2%} removed as invalid/duplicate; "
                       f"missing values imputed rather than dropped)")

    fact_visits = raw[[
        "visit_id", "patient_id", "department_id",
        "arrival_datetime", "triage_datetime", "bed_assigned_datetime", "discharge_datetime",
        "arrival_date", "arrival_hour", "arrival_dow", "arrival_month", "shift_type",
        "admission_type", "age", "gender", "diagnosis",
        "wait_time_minutes", "length_of_stay_hours",
        "readmitted_30d", "satisfaction_score", "billing_amount",
    ]].reset_index(drop=True)

    # Date dimension for Power BI time-intelligence DAX (CALCULATE + date-table functions
    # require a proper marked calendar table rather than filtering the fact table directly).
    all_dates = pd.date_range(fact_visits["arrival_datetime"].min().normalize(),
                               fact_visits["arrival_datetime"].max().normalize(), freq="D")
    dim_date = pd.DataFrame({"date": all_dates})
    dim_date["date_key"] = dim_date["date"].dt.strftime("%Y-%m-%d")
    dim_date["year"] = dim_date["date"].dt.year
    dim_date["month_number"] = dim_date["date"].dt.month
    dim_date["month_name"] = dim_date["date"].dt.month_name()
    dim_date["day_name"] = dim_date["date"].dt.day_name()
    dim_date["week_of_year"] = dim_date["date"].dt.isocalendar().week.astype(int)
    dim_date["is_weekend"] = dim_date["date"].dt.dayofweek.isin([5, 6])
    dim_date["quarter"] = dim_date["date"].dt.quarter
    dim_date = dim_date.drop(columns=["date"])

    # Persist processed CSVs (Power BI / BI-tool ready star schema)
    dim_department.to_csv(PROCESSED_DIR / "dim_department.csv", index=False)
    dim_date.to_csv(PROCESSED_DIR / "dim_date.csv", index=False)
    fact_staff_shift.to_csv(PROCESSED_DIR / "fact_staff_shift.csv", index=False)
    fact_visits.to_csv(PROCESSED_DIR / "fact_patient_visits.csv", index=False)

    # Load into SQLite
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    dim_department.to_sql("dim_department", conn, index=False)
    dim_date.to_sql("dim_date", conn, index=False)
    fact_staff_shift.to_sql("fact_staff_shift", conn, index=False)
    fact_visits.to_sql("fact_patient_visits", conn, index=False)
    conn.execute("CREATE INDEX idx_visits_dept ON fact_patient_visits(department_id)")
    conn.execute("CREATE INDEX idx_visits_date ON fact_patient_visits(arrival_date)")
    conn.execute("CREATE INDEX idx_shift_dept_date ON fact_staff_shift(department_id, shift_date)")
    conn.commit()
    conn.close()
    record("load_sqlite", f"loaded star schema into {DB_PATH.relative_to(ROOT)}")

    with open(REPORTS_DIR / "cleaning_log.json", "w") as f:
        json.dump(log, f, indent=2)

    print("\nDone. Cleaning log written to reports/cleaning_log.json")


if __name__ == "__main__":
    main()
