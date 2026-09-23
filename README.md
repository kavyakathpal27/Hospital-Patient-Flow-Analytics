# Power BI Model — Hospital Patient Flow & Operations

This folder documents the Power BI data model built on top of `data/processed/`.
Power BI Desktop is a GUI application, so the `.pbix` itself isn't checked into
source control here — instead this is the exact spec to rebuild it in ~15
minutes: which tables to import, how to relate them, and the DAX measure
library to paste in.

## 1. Get Data

In Power BI Desktop: **Get Data → Text/CSV**, import each file from
`data/processed/`:

| File                                | Role in model      |
|--------------------------------------|---------------------|
| `dim_department.csv`                | Dimension           |
| `dim_date.csv`                      | Dimension (calendar)|
| `fact_patient_visits.csv`           | Fact (grain: 1 row / visit) |
| `fact_staff_shift.csv`              | Fact (grain: 1 row / dept-date-shift) |
| `shift_level_summary.csv`           | Pre-aggregated fact (1 row / dept-date-shift, includes patients_per_nurse) |
| `bed_utilization_summary.csv`       | Pre-aggregated summary (1 row / department) |
| `staffing_flags.csv`                | Pre-aggregated summary (1 row / dept-shift_type, flagged bottlenecks) |
| `monthly_trend.csv`                 | Pre-aggregated summary (1 row / dept-month) |

Load all as **Import** mode (dataset is small; no need for DirectQuery).

## 2. Model relationships

In the Model view, create these relationships (all Many-to-One, single direction
unless noted):

- `fact_patient_visits[department_id]` → `dim_department[department_id]`
- `fact_staff_shift[department_id]` → `dim_department[department_id]`
- `shift_level_summary[department_id]` → `dim_department[department_id]`
- `fact_patient_visits[arrival_date]` → `dim_date[date_key]`
- `fact_staff_shift[shift_date]` → `dim_date[date_key]`
- `shift_level_summary[arrival_date]` → `dim_date[date_key]`

Mark `dim_date` as a **Date Table** (Table tools → Mark as date table →
`date_key` column) so time-intelligence DAX functions work correctly.

## 3. DAX measures

Create a dedicated measures table (New Table → name it `_Measures`, any single
column, hide it) and add these:

```dax
Total Visits = COUNTROWS(fact_patient_visits)

Avg Wait (min) = AVERAGE(fact_patient_visits[wait_time_minutes])

Target Wait (min) =
CALCULATE(
    AVERAGE(dim_department[target_wait_minutes]),
    CROSSFILTER(fact_patient_visits[department_id], dim_department[department_id], BOTH)
)

Pct Above Target =
DIVIDE([Avg Wait (min)] - [Target Wait (min)], [Target Wait (min)])

Avg Length of Stay (hrs) = AVERAGE(fact_patient_visits[length_of_stay_hours])

Avg Satisfaction = AVERAGE(fact_patient_visits[satisfaction_score])

Readmission Rate = AVERAGE(fact_patient_visits[readmitted_30d])

Avg Patients per Nurse = AVERAGE(shift_level_summary[patients_per_nurse])

-- Under-staffed shift count, matching the top-quartile flag used in src/analysis.py
Flagged Shift Count = COUNTROWS(staffing_flags)

Total Shift Count = COUNTROWS(shift_level_summary)

Pct Shifts Flagged = DIVIDE([Flagged Shift Count], [Total Shift Count])

-- Time intelligence (works because dim_date is marked as a date table)
Visits MTD = TOTALMTD([Total Visits], dim_date[date_key])

Avg Wait Prior Month =
CALCULATE([Avg Wait (min)], DATEADD(dim_date[date_key], -1, MONTH))

Wait Trend vs Prior Month = [Avg Wait (min)] - [Avg Wait Prior Month]

-- Bed utilization (from the pre-aggregated summary; visuals should use this
-- table directly rather than trying to recompute occupancy in DAX, since the
-- sweep-line concurrent-patient calculation isn't expressible efficiently in
-- native DAX over a 54K-row fact table)
Avg Bed Utilization % = AVERAGE(bed_utilization_summary[avg_utilization_pct])

Peak Bed Utilization % = MAX(bed_utilization_summary[peak_utilization_pct])
```

## 4. Suggested pages / visuals

1. **Executive Overview** — KPI cards (Total Visits, Avg Wait, Pct Above
   Target, Avg Satisfaction, Avg Bed Utilization %); a bar chart of Avg Wait by
   department with a constant line for Target Wait; a map/table of the 8
   departments ranked by Pct Above Target.
2. **Staffing & Bottlenecks** — scatter plot of `patients_per_nurse` (x) vs
   `avg_wait_minutes` (y) from `shift_level_summary`, colored by department,
   to visually reproduce the regression finding in `reports/findings.md`;
   a matrix of flagged shift counts by department x shift_type from
   `staffing_flags`.
3. **Bed Utilization** — bar chart of `avg_utilization_pct` /
   `peak_utilization_pct` by department from `bed_utilization_summary`, with a
   100% reference line to flag departments running over nominal capacity.
4. **Trends** — line chart of monthly visit volume and avg wait by department
   from `monthly_trend`, to show the flu-season seasonality in Emergency.

Use **drill-through**: right-click a department bar on the Executive Overview
page → set up drill-through to a department detail page filtered by
`dim_department[department_name]`, showing that department's shift-level and
monthly trend visuals.

## 5. Why the source data doesn't include a live Power BI file

Power BI Desktop is a Windows GUI application with no scriptable CLI for
building `.pbix` files from a coding agent, so this repo ships the exact
model spec + pre-aggregated CSVs instead of a binary `.pbix`. If you have
Power BI Desktop installed, following steps 1-4 above reproduces the intended
dashboard in well under 30 minutes. An equivalent live, browser-viewable KPI
dashboard covering the same metrics is also included at
`reports/dashboard.html` for an immediate visual preview.
