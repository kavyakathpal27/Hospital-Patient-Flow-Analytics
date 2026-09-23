"""
Synthetic hospital operations data generator.

Generates 12 months of patient-visit-level data across 8 departments with
realistic operational patterns baked in (volume seasonality, day-of-week and
hour-of-day effects, and a genuine relationship between staffing ratio and
patient wait time). Intentional messiness (missing values, inconsistent
formats, duplicates) is injected into the raw output to mirror a real-world
source extract that needs cleaning downstream.

Source note: this is a synthetically generated dataset modeled on typical
hospital patient-flow records (arrival -> triage -> bed assignment ->
discharge), not a real patient dataset. See README.md for details.
"""
import numpy as np
import pandas as pd
from pathlib import Path

RNG_SEED = 42
rng = np.random.default_rng(RNG_SEED)

RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

START_DATE = pd.Timestamp("2024-01-01")
END_DATE = pd.Timestamp("2024-12-31")

DEPARTMENTS = [
    # name, total_beds, target_wait_minutes, base_daily_volume, ED-like flag, base_nurses/shift, base_doctors/shift
    # total_beds is sized independently from nurse staffing (~80-85% average occupancy
    # given each department's realized volume x length-of-stay - see reports/findings.md
    # bed-utilization section); nurse staffing follows the smaller historical baseline
    # so the staffing-ratio -> wait-time relationship (the headline regression finding)
    # isn't diluted by scaling nurses up in lockstep with bed capacity.
    ("Emergency",                 90, 30, 55, True,   10, 4),
    ("Cardiology",                46, 45, 14, False,   5, 2),
    ("Orthopedics",                41, 45, 12, False,   5, 2),
    ("General Surgery",            42, 60, 13, False,   6, 3),
    ("Pediatrics",                 40, 40, 16, False,   4, 2),
    ("Internal Medicine",          58, 45, 18, False,   6, 3),
    ("Obstetrics & Gynecology",    38, 40, 11, False,   5, 2),
    ("Neurology",                  33, 50, 9,  False,   4, 2),
]

dim_department = pd.DataFrame(
    DEPARTMENTS,
    columns=["department_name", "total_beds", "target_wait_minutes", "base_daily_volume", "is_emergency",
             "base_nurses_per_shift", "base_doctors_per_shift"],
)
dim_department.insert(0, "department_id", range(1, len(dim_department) + 1))
dim_department.to_csv(RAW_DIR / "dim_department.csv", index=False)

ADMISSION_TYPES = ["Emergency", "Urgent", "Elective", "Referral"]
DIAGNOSIS_BY_DEPT = {
    "Emergency": ["Trauma", "Chest Pain", "Respiratory Distress", "Laceration", "Abdominal Pain", "Fracture"],
    "Cardiology": ["Arrhythmia", "Hypertension", "Heart Failure", "Angina"],
    "Orthopedics": ["Fracture", "Joint Replacement", "Sprain", "Back Pain"],
    "General Surgery": ["Appendicitis", "Hernia", "Gallbladder", "Post-op Care"],
    "Pediatrics": ["Fever", "Respiratory Infection", "Gastroenteritis", "Asthma"],
    "Internal Medicine": ["Diabetes Management", "Pneumonia", "Hypertension", "Infection"],
    "Obstetrics & Gynecology": ["Labor & Delivery", "Prenatal Care", "Gynecologic Surgery"],
    "Neurology": ["Stroke", "Seizure", "Migraine", "Neuropathy"],
}

# ---------------------------------------------------------------------------
# 1) Staffing shifts: 3 shifts/day x 365 days x 8 departments
# ---------------------------------------------------------------------------
SHIFT_TYPES = [("Morning", 7, 8), ("Afternoon", 15, 8), ("Night", 23, 8)]  # name, start_hour, duration_hours

shift_rows = []
shift_id = 1
dates = pd.date_range(START_DATE, END_DATE, freq="D")

for dept in dim_department.itertuples():
    for d in dates:
        dow = d.dayofweek  # 0=Mon
        for shift_name, start_hour, _dur in SHIFT_TYPES:
            # Base staffing scaled to department size, with intentional under-staffing
            # on Emergency night shifts and Monday mornings (system-wide) to create
            # genuine, discoverable bottlenecks.
            base_nurses = dept.base_nurses_per_shift
            base_doctors = dept.base_doctors_per_shift

            nurse_factor = 1.0
            doctor_factor = 1.0

            if shift_name == "Night":
                nurse_factor *= 0.65
                doctor_factor *= 0.6
            if dept.is_emergency and shift_name == "Night":
                nurse_factor *= 0.8  # compounding under-staffing on ED nights
            if dow == 0 and shift_name == "Morning":
                nurse_factor *= 0.85  # Monday morning system-wide dip (holiday backlog effect)
            if dow in (5, 6):
                nurse_factor *= 1.05  # slight weekend bump, not enough to offset ED weekend volume

            nurses = max(2, int(round(base_nurses * nurse_factor + rng.normal(0, 0.6))))
            doctors = max(1, int(round(base_doctors * doctor_factor + rng.normal(0, 0.4))))
            support = max(1, int(round((nurses + doctors) * 0.4 + rng.normal(0, 0.5))))

            shift_rows.append(
                (shift_id, dept.department_id, d.date().isoformat(), shift_name, doctors, nurses, support)
            )
            shift_id += 1

fact_staff_shift = pd.DataFrame(
    shift_rows,
    columns=["shift_id", "department_id", "shift_date", "shift_type", "doctors_on_shift", "nurses_on_shift", "support_staff_on_shift"],
)
fact_staff_shift.to_csv(RAW_DIR / "fact_staff_shift.csv", index=False)

# ---------------------------------------------------------------------------
# 2) Patient visits
# ---------------------------------------------------------------------------
HOUR_WEIGHTS = np.array([
    0.015, 0.010, 0.008, 0.007, 0.008, 0.012, 0.020, 0.032,
    0.045, 0.052, 0.058, 0.060, 0.058, 0.055, 0.052, 0.050,
    0.052, 0.058, 0.065, 0.070, 0.066, 0.058, 0.040, 0.024,
])
HOUR_WEIGHTS = HOUR_WEIGHTS / HOUR_WEIGHTS.sum()

def month_seasonality(month: int, is_emergency: bool) -> float:
    # Flu-season bump for Emergency in Dec/Jan/Feb; mild dip for elective depts in summer.
    if is_emergency and month in (12, 1, 2):
        return 1.28
    if not is_emergency and month in (7, 8):
        return 0.9
    return 1.0

def dow_factor(dow: int, is_emergency: bool) -> float:
    if is_emergency and dow in (4, 5, 6):  # Fri/Sat/Sun
        return 1.22
    if not is_emergency and dow in (5, 6):
        return 0.75  # fewer elective visits on weekends
    return 1.0

def shift_for_hour(hour: int) -> str:
    if 7 <= hour < 15:
        return "Morning"
    if 15 <= hour < 23:
        return "Afternoon"
    return "Night"

shift_lookup = fact_staff_shift.set_index(["department_id", "shift_date", "shift_type"])

# --- Pass 1: arrival timestamps + department/shift assignment (no wait time yet) ---
prelim_rows = []
patient_id_counter = 100000

for dept in dim_department.itertuples():
    for d in dates:
        month = d.month
        dow = d.dayofweek
        expected_volume = dept.base_daily_volume * month_seasonality(month, dept.is_emergency) * dow_factor(dow, dept.is_emergency)
        daily_count = rng.poisson(max(expected_volume, 1))

        hours_today = rng.choice(np.arange(24), size=daily_count, p=HOUR_WEIGHTS)
        for hour in hours_today:
            minute = int(rng.integers(0, 60))
            arrival_dt = pd.Timestamp(d.date()) + pd.Timedelta(hours=int(hour), minutes=minute)
            shift_name = shift_for_hour(hour)
            shift_date_key = d.date().isoformat()

            prelim_rows.append((
                patient_id_counter, dept.department_id, dept.department_name, dept.is_emergency,
                dept.target_wait_minutes, arrival_dt, shift_date_key, shift_name,
            ))
            patient_id_counter += 1

prelim = pd.DataFrame(prelim_rows, columns=[
    "patient_id", "department_id", "department_name", "is_emergency",
    "target_wait_minutes", "arrival_datetime", "shift_date", "shift_type",
])

# --- Pass 2: realized per-shift patient volume drives the staffing ratio & wait time ---
shift_volume = (
    prelim.groupby(["department_id", "shift_date", "shift_type"])
    .size()
    .rename("patient_count")
    .reset_index()
)
shift_volume = shift_volume.merge(
    fact_staff_shift[["department_id", "shift_date", "shift_type", "nurses_on_shift", "doctors_on_shift"]],
    on=["department_id", "shift_date", "shift_type"],
    how="left",
)
shift_volume["patients_per_nurse"] = shift_volume["patient_count"] / shift_volume["nurses_on_shift"].clip(lower=1)

prelim = prelim.merge(
    shift_volume[["department_id", "shift_date", "shift_type", "patients_per_nurse"]],
    on=["department_id", "shift_date", "shift_type"],
    how="left",
)

visit_rows = []
for visit_id, row in enumerate(prelim.itertuples(), start=1):
    base_wait = row.target_wait_minutes * 0.4
    # Continuous (non-threshold) staffing effect: each additional patient per
    # nurse adds ~7 minutes of wait, on average, before noise.
    staffing_penalty = row.patients_per_nurse * 7.0
    noise = rng.normal(0, 5)
    wait_minutes = max(3.0, base_wait + staffing_penalty + noise)

    admission_type = rng.choice(
        ADMISSION_TYPES,
        p=[0.55, 0.2, 0.2, 0.05] if row.is_emergency else [0.1, 0.25, 0.55, 0.1],
    )

    arrival_dt = row.arrival_datetime
    triage_dt = arrival_dt + pd.Timedelta(minutes=max(1, rng.normal(6, 2)))
    bed_assigned_dt = arrival_dt + pd.Timedelta(minutes=wait_minutes)
    los_hours = max(1.0, rng.gamma(shape=3.0, scale=(9.0 if row.is_emergency else 22.0)))
    discharge_dt = bed_assigned_dt + pd.Timedelta(hours=los_hours)

    age = int(np.clip(rng.normal(45, 20), 0, 99))
    gender = rng.choice(["Female", "Male"], p=[0.51, 0.49])
    diagnosis = rng.choice(DIAGNOSIS_BY_DEPT[row.department_name])
    satisfaction = int(np.clip(round(rng.normal(4.2 - staffing_penalty / 30, 0.7)), 1, 5))
    readmitted_30d = bool(rng.random() < (0.09 if row.is_emergency else 0.05))
    billing_amount = round(max(80.0, rng.gamma(shape=2.2, scale=(650 if row.is_emergency else 950))), 2)

    visit_rows.append((
        visit_id, row.patient_id, row.department_id,
        arrival_dt, triage_dt, bed_assigned_dt, discharge_dt,
        admission_type, age, gender, diagnosis,
        round(wait_minutes, 1), round(los_hours, 2),
        readmitted_30d, satisfaction, billing_amount,
    ))

fact_visits = pd.DataFrame(visit_rows, columns=[
    "visit_id", "patient_id", "department_id",
    "arrival_datetime", "triage_datetime", "bed_assigned_datetime", "discharge_datetime",
    "admission_type", "age", "gender", "diagnosis",
    "wait_time_minutes", "length_of_stay_hours",
    "readmitted_30d", "satisfaction_score", "billing_amount",
])

print(f"Generated {len(fact_visits):,} patient visits across {len(dim_department)} departments.")

# ---------------------------------------------------------------------------
# 3) Inject realistic messiness into the RAW extract (cleaned downstream)
# ---------------------------------------------------------------------------
messy = fact_visits.copy()

# 3% missing age
mask = rng.random(len(messy)) < 0.03
messy.loc[mask, "age"] = np.nan

# 2% missing gender
mask = rng.random(len(messy)) < 0.02
messy.loc[mask, "gender"] = np.nan

# 4% missing satisfaction_score
mask = rng.random(len(messy)) < 0.04
messy.loc[mask, "satisfaction_score"] = np.nan

# Inconsistent gender casing/labels on ~5% of remaining rows
mask = (~messy["gender"].isna()) & (rng.random(len(messy)) < 0.05)
messy.loc[mask, "gender"] = messy.loc[mask, "gender"].map({"Female": "F", "Male": "M"})
mask2 = (~messy["gender"].isna()) & (rng.random(len(messy)) < 0.02)
messy.loc[mask2, "gender"] = messy.loc[mask2, "gender"].str.lower()

# Inconsistent admission_type casing on a few rows
mask = rng.random(len(messy)) < 0.015
messy.loc[mask, "admission_type"] = messy.loc[mask, "admission_type"].str.upper()

# Duplicate ~0.5% of rows (re-extract artifact)
dup_idx = rng.choice(messy.index, size=int(len(messy) * 0.005), replace=False)
messy = pd.concat([messy, messy.loc[dup_idx]], ignore_index=True)

# A handful of impossible/negative wait times (sensor/logging glitch)
mask = rng.random(len(messy)) < 0.003
messy.loc[mask, "wait_time_minutes"] = -messy.loc[mask, "wait_time_minutes"]

# Mixed datetime string formats for arrival_datetime (half ISO, half US format)
def mixed_format(ts):
    if pd.isna(ts):
        return ts
    if rng.random() < 0.3:
        return ts.strftime("%m/%d/%Y %H:%M")
    return ts.isoformat(sep=" ")

for col in ["arrival_datetime", "triage_datetime", "bed_assigned_datetime", "discharge_datetime"]:
    messy[col] = messy[col].apply(mixed_format)

# A few billing_amount stored as strings with currency symbols
mask = rng.random(len(messy)) < 0.02
messy["billing_amount"] = messy["billing_amount"].astype(object)
messy.loc[mask, "billing_amount"] = messy.loc[mask, "billing_amount"].apply(lambda x: f"${x:,.2f}")

messy = messy.sample(frac=1.0, random_state=RNG_SEED).reset_index(drop=True)
messy.to_csv(RAW_DIR / "fact_patient_visits.csv", index=False)

print(f"Raw extract written with {len(messy):,} rows (incl. injected duplicates/messiness) to {RAW_DIR}")
