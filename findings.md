# Findings: Hospital Patient Flow & Operations Analytics

Dataset: 54,130 patient visits across 8 departments, 2024-01-01 to 2024-12-31.

## 1. Staffing ratio drives wait time (linear regression)

A naive pooled regression of average wait time on patients-per-nurse (n=7,970 department/date/shift-type observations) gives a slope of 5.055 (R² = 0.5081) — but this pools across departments with very different baseline wait targets, which confounds the estimate (a classic Simpson's-paradox risk). Controlling for department with a fixed-effects (within-department demeaned) OLS fit gives:

`wait_minutes_demeaned = 6.981 x patients_per_nurse_demeaned`

R² = 0.7128, p = 0.00e+00. Holding department constant, each additional patient per nurse is associated with **6.981 additional minutes** of average wait time — this is the coefficient used for the staffing-reallocation projection below.

## 2. Wait time differs significantly across departments (ANOVA)

One-way ANOVA across all 8 departments: F = 1128.07, p = 0.00e+00 — department is a statistically significant driver of wait time.

## 3. Emergency's peak-load shift runs significantly longer waits (t-test)

Rather than assume nights are worst, the empirically slowest Emergency shift was identified from the data: **Afternoon** shift average wait is **33.98 min** vs **30.37 min** for the other two shifts combined (Welch's t-test: t = 36.56, p = 1.21e-284, n_{afternoon}=11,016, n_rest=12,255) — a **3.61 minute** gap. This reflects Emergency's arrival volume peaking in the evening/afternoon window, not overnight.

## 4. Bottleneck department

**Emergency** shows the largest gap to target (32.08 min avg vs 30 min target, +6.9% vs target) across 23,271 visits.

## 5. Bed utilization

**Pediatrics** runs the highest average bed utilization at 104.1% (peak 172.5%) of its 40-bed capacity, computed via an O(n log n) sweep-line occupancy calculation over arrival/discharge timestamps.

## 6. Under-staffed shift flags

1,998 of 7,970 department/shift instances (top quartile of patients-per-nurse within each department) are flagged as under-staffed. These shifts average **31.59 min** wait vs **23.86 min** in normally staffed shifts — a gap directly attributable to staffing ratio (2.0 vs 0.89 patients/nurse). Emergency Afternoon and Morning shifts are the most frequently flagged combination (see data/processed/staffing_flags.csv).

## 7. Staffing reallocation recommendation

Adding 2 nurses to the flagged bottleneck shifts is projected to reduce the patients-per-nurse ratio from 2.0 to 1.67, cutting average wait time by an estimated **2.3 minutes** (31.59 → 29.3 min), based on the fitted regression slope from Finding 1.
