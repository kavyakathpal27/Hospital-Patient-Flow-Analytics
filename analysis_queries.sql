-- Analysis queries for hospital patient-flow star schema (data/processed/hospital.db).
-- Uses CTEs and window functions. Verified against SQLite; portable to Postgres/T-SQL
-- with trivial date-function substitutions.

-- ---------------------------------------------------------------------------
-- 1) Average wait time vs target, by department, ranked worst-first
-- ---------------------------------------------------------------------------
WITH dept_wait AS (
    SELECT
        d.department_name,
        d.target_wait_minutes,
        COUNT(*)                                   AS visit_count,
        ROUND(AVG(v.wait_time_minutes), 1)         AS avg_wait_minutes
    FROM fact_patient_visits v
    JOIN dim_department d ON d.department_id = v.department_id
    GROUP BY d.department_name, d.target_wait_minutes
)
SELECT
    department_name,
    target_wait_minutes,
    avg_wait_minutes,
    ROUND(100.0 * (avg_wait_minutes - target_wait_minutes) / target_wait_minutes, 1) AS pct_above_target,
    visit_count
FROM dept_wait
ORDER BY pct_above_target DESC;

-- ---------------------------------------------------------------------------
-- 2) Wait time by department x shift_type, with rank of worst shift per dept
--    (window function: RANK() OVER PARTITION BY department)
-- ---------------------------------------------------------------------------
WITH shift_wait AS (
    SELECT
        d.department_name,
        v.shift_type,
        COUNT(*)                             AS visit_count,
        ROUND(AVG(v.wait_time_minutes), 1)  AS avg_wait_minutes
    FROM fact_patient_visits v
    JOIN dim_department d ON d.department_id = v.department_id
    GROUP BY d.department_name, v.shift_type
)
SELECT
    department_name,
    shift_type,
    avg_wait_minutes,
    visit_count,
    RANK() OVER (PARTITION BY department_name ORDER BY avg_wait_minutes DESC) AS wait_rank_within_dept
FROM shift_wait
ORDER BY department_name, wait_rank_within_dept;

-- ---------------------------------------------------------------------------
-- 3) Staffing ratio vs wait time: patients-per-nurse per shift, joined to
--    average wait time realized in that same department/date/shift_type
--    (bottleneck detection input for Python correlation/regression step)
-- ---------------------------------------------------------------------------
WITH shift_volume AS (
    SELECT
        department_id,
        arrival_date,
        shift_type,
        COUNT(*)                            AS patient_count,
        ROUND(AVG(wait_time_minutes), 1)   AS avg_wait_minutes
    FROM fact_patient_visits
    GROUP BY department_id, arrival_date, shift_type
),
staffing AS (
    SELECT department_id, shift_date, shift_type, nurses_on_shift, doctors_on_shift
    FROM fact_staff_shift
)
SELECT
    d.department_name,
    sv.arrival_date,
    sv.shift_type,
    sv.patient_count,
    s.nurses_on_shift,
    ROUND(1.0 * sv.patient_count / NULLIF(s.nurses_on_shift, 0), 2) AS patients_per_nurse,
    sv.avg_wait_minutes
FROM shift_volume sv
JOIN staffing s
  ON s.department_id = sv.department_id
 AND s.shift_date = sv.arrival_date
 AND s.shift_type = sv.shift_type
JOIN dim_department d ON d.department_id = sv.department_id
ORDER BY patients_per_nurse DESC
LIMIT 20;

-- ---------------------------------------------------------------------------
-- 4) Bed utilization proxy: concurrent patients (arrived, not yet discharged)
--    sampled at each visit's arrival timestamp, vs department bed capacity
--    (self-join / correlated subquery)
--
--    NOTE ON SCALE: this correlated subquery is O(n^2) per department and is
--    fine for ad-hoc/sampled exploration (LIMIT applied) but takes ~15+
--    minutes over the full 54K-row fact table even with covering indexes on
--    (department_id, arrival_datetime) / (department_id, discharge_datetime),
--    since SQLite can't satisfy both the arrival and discharge range
--    predicates from a single index. The production figure used in
--    reports/findings.md and the Power BI model is computed in
--    src/analysis.py with an O(n log n) sweep-line algorithm instead.
-- ---------------------------------------------------------------------------
WITH occupancy AS (
    SELECT
        v.visit_id,
        v.department_id,
        v.arrival_datetime,
        (
            SELECT COUNT(*)
            FROM fact_patient_visits v2
            WHERE v2.department_id = v.department_id
              AND v2.arrival_datetime <= v.arrival_datetime
              AND v2.discharge_datetime > v.arrival_datetime
        ) AS concurrent_patients
    FROM fact_patient_visits v
)
SELECT
    d.department_name,
    d.total_beds,
    ROUND(AVG(o.concurrent_patients), 1)                                   AS avg_concurrent_patients,
    ROUND(100.0 * AVG(o.concurrent_patients) / d.total_beds, 1)           AS avg_utilization_pct,
    ROUND(100.0 * MAX(o.concurrent_patients) / d.total_beds, 1)          AS peak_utilization_pct
FROM occupancy o
JOIN dim_department d ON d.department_id = o.department_id
GROUP BY d.department_name, d.total_beds
ORDER BY avg_utilization_pct DESC;

-- ---------------------------------------------------------------------------
-- 5) Under/overstaffed shift flags: patients-per-nurse ratio vs the
--    department's own 75th-percentile ratio (window function: PERCENT_RANK)
-- ---------------------------------------------------------------------------
WITH shift_volume AS (
    SELECT department_id, arrival_date, shift_type, COUNT(*) AS patient_count
    FROM fact_patient_visits
    GROUP BY department_id, arrival_date, shift_type
),
joined AS (
    SELECT
        sv.department_id,
        sv.arrival_date,
        sv.shift_type,
        sv.patient_count,
        s.nurses_on_shift,
        1.0 * sv.patient_count / NULLIF(s.nurses_on_shift, 0) AS patients_per_nurse
    FROM shift_volume sv
    JOIN fact_staff_shift s
      ON s.department_id = sv.department_id
     AND s.shift_date = sv.arrival_date
     AND s.shift_type = sv.shift_type
),
ranked AS (
    SELECT *,
        PERCENT_RANK() OVER (PARTITION BY department_id ORDER BY patients_per_nurse) AS pct_rank
    FROM joined
)
SELECT
    d.department_name,
    r.shift_type,
    COUNT(*)                                            AS flagged_shift_count,
    ROUND(AVG(r.patients_per_nurse), 2)                AS avg_patients_per_nurse_when_flagged
FROM ranked r
JOIN dim_department d ON d.department_id = r.department_id
WHERE r.pct_rank >= 0.75  -- top quartile of patient-per-nurse load for that department
GROUP BY d.department_name, r.shift_type
ORDER BY flagged_shift_count DESC;

-- ---------------------------------------------------------------------------
-- 6) Monthly volume trend by department (seasonality check)
-- ---------------------------------------------------------------------------
SELECT
    d.department_name,
    v.arrival_month,
    COUNT(*) AS visit_count,
    ROUND(AVG(v.wait_time_minutes), 1) AS avg_wait_minutes
FROM fact_patient_visits v
JOIN dim_department d ON d.department_id = v.department_id
GROUP BY d.department_name, v.arrival_month
ORDER BY d.department_name, v.arrival_month;
