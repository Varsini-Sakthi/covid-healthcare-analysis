-- =====================================================================
-- COVID-19 Healthcare Impact Analysis — Analytical Query Layer
-- Run against data/covid_healthcare.db (SQLite dialect; window functions
-- and CTEs used throughout are ANSI-standard and portable to Postgres).
-- =====================================================================

-- ---------------------------------------------------------------
-- Q1. Daily test volume, positives, and 7-day rolling positivity rate
--     (the core "RT-PCR trend analysis" metric public health teams
--      track — the CDC/WHO standard is a 7-day rolling average because
--      raw daily rates are noisy, especially with weekend testing dips)
-- ---------------------------------------------------------------
WITH daily_counts AS (
    SELECT
        test_date,
        COUNT(*) AS total_tests,
        SUM(CASE WHEN result = 'Positive' THEN 1 ELSE 0 END) AS positive_tests
    FROM rtpcr_tests
    GROUP BY test_date
)
SELECT
    test_date,
    total_tests,
    positive_tests,
    ROUND(100.0 * positive_tests / NULLIF(total_tests, 0), 2) AS daily_positivity_pct,
    ROUND(100.0 * AVG(positive_tests) OVER (
        ORDER BY test_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
    ) / NULLIF(AVG(total_tests) OVER (
        ORDER BY test_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
    ), 0), 2) AS rolling_7day_positivity_pct
FROM daily_counts
ORDER BY test_date;


-- ---------------------------------------------------------------
-- Q2. Case doubling time (days) using a 7-day rolling case average
--     Doubling time = ln(2) / growth_rate, growth_rate estimated from
--     the ratio of this week's avg to last week's avg.
-- ---------------------------------------------------------------
WITH daily_pos AS (
    SELECT test_date, SUM(CASE WHEN result='Positive' THEN 1 ELSE 0 END) AS positives
    FROM rtpcr_tests GROUP BY test_date
),
smoothed AS (
    SELECT
        test_date,
        AVG(positives) OVER (ORDER BY test_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS avg_7d
    FROM daily_pos
),
rolling AS (
    SELECT
        test_date,
        avg_7d,
        LAG(avg_7d, 7) OVER (ORDER BY test_date) AS avg_7d_prior_week
    FROM smoothed
)
SELECT
    test_date,
    ROUND(avg_7d, 1) AS rolling_7day_avg_cases,
    ROUND(avg_7d / NULLIF(avg_7d_prior_week, 0), 3) AS week_over_week_ratio,
    CASE
        WHEN avg_7d_prior_week > 0 AND avg_7d > avg_7d_prior_week
        THEN ROUND(7 * LN(2) / LN(avg_7d / avg_7d_prior_week), 1)
        ELSE NULL
    END AS estimated_doubling_time_days
FROM rolling
ORDER BY test_date;


-- ---------------------------------------------------------------
-- Q3. Test-to-hospitalization conversion rate and average lag (days)
--     -- validates the hospitalization pipeline and quantifies the
--     lead time hospitals have to prepare for admission surges after
--     a positivity spike.
-- ---------------------------------------------------------------
SELECT
    r.result,
    COUNT(DISTINCT t.patient_id) AS positive_patients,
    COUNT(DISTINCT h.patient_id) AS hospitalized_patients,
    ROUND(100.0 * COUNT(DISTINCT h.patient_id) / NULLIF(COUNT(DISTINCT t.patient_id), 0), 2)
        AS hospitalization_rate_pct,
    ROUND(AVG(JULIANDAY(h.admission_date) - JULIANDAY(t.test_date)), 1) AS avg_lag_days
FROM rtpcr_tests t
JOIN (SELECT 'Positive' AS result) r ON t.result = r.result
LEFT JOIN hospitalizations h ON h.patient_id = t.patient_id
GROUP BY r.result;


-- ---------------------------------------------------------------
-- Q4. Regional comparison: cumulative positivity, hospitalization rate,
--     and mortality among hospitalized patients (cohort-style comparison)
-- ---------------------------------------------------------------
SELECT
    reg.region_name,
    COUNT(DISTINCT t.patient_id) AS total_patients_tested,
    SUM(CASE WHEN t.result = 'Positive' THEN 1 ELSE 0 END) AS total_positive,
    ROUND(100.0 * SUM(CASE WHEN t.result = 'Positive' THEN 1 ELSE 0 END)
        / COUNT(*), 2) AS positivity_pct,
    COUNT(DISTINCT h.patient_id) AS total_hospitalized,
    SUM(CASE WHEN h.outcome = 'Deceased' THEN 1 ELSE 0 END) AS deaths,
    ROUND(100.0 * SUM(CASE WHEN h.outcome = 'Deceased' THEN 1 ELSE 0 END)
        / NULLIF(COUNT(DISTINCT h.patient_id), 0), 2) AS case_fatality_pct_of_hospitalized
FROM regions reg
JOIN patients p ON p.region_id = reg.region_id
JOIN rtpcr_tests t ON t.patient_id = p.patient_id
LEFT JOIN hospitalizations h ON h.patient_id = p.patient_id
GROUP BY reg.region_name
ORDER BY positivity_pct DESC;


-- ---------------------------------------------------------------
-- Q5. Daily resource strain index per facility
--     strain_index > 0.85 flags a facility approaching capacity —
--     this is the metric that drove "optimized resource allocation"
--     in the resume bullet, feeding directly into the Tableau gauge
--     chart and the LP allocation model in resource_optimization.py
-- ---------------------------------------------------------------
SELECT
    ru.snapshot_date,
    f.facility_name,
    ru.beds_occupied,
    f.bed_capacity,
    ROUND(1.0 * ru.beds_occupied / NULLIF(f.bed_capacity, 0), 3) AS bed_strain_index,
    ru.icu_occupied,
    f.icu_capacity,
    ROUND(1.0 * ru.icu_occupied / NULLIF(f.icu_capacity, 0), 3) AS icu_strain_index,
    CASE WHEN 1.0 * ru.icu_occupied / NULLIF(f.icu_capacity, 0) >= 0.85
         THEN 'SURGE WARNING' ELSE 'Normal' END AS status
FROM resource_utilization ru
JOIN facilities f ON f.facility_id = ru.facility_id
ORDER BY icu_strain_index DESC
LIMIT 50;


-- ---------------------------------------------------------------
-- Q6. Peak ICU strain day per facility (the day each hospital was
--     under the most pressure — used to size the LP optimization model)
-- ---------------------------------------------------------------
WITH strain AS (
    SELECT
        ru.facility_id, f.facility_name, ru.snapshot_date,
        ru.icu_occupied, f.icu_capacity,
        1.0 * ru.icu_occupied / NULLIF(f.icu_capacity, 0) AS icu_strain_index,
        ROW_NUMBER() OVER (
            PARTITION BY ru.facility_id
            ORDER BY 1.0 * ru.icu_occupied / NULLIF(f.icu_capacity, 0) DESC
        ) AS rn
    FROM resource_utilization ru
    JOIN facilities f ON f.facility_id = ru.facility_id
)
SELECT facility_name, snapshot_date, icu_occupied, icu_capacity,
       ROUND(icu_strain_index, 3) AS peak_icu_strain_index
FROM strain WHERE rn = 1
ORDER BY peak_icu_strain_index DESC;


-- ---------------------------------------------------------------
-- Q7. Age-stratified hospitalization and ICU admission risk
--     (supports the risk-adjusted resource forecasting model)
-- ---------------------------------------------------------------
SELECT
    CASE
        WHEN p.age < 18 THEN '0-17'
        WHEN p.age < 40 THEN '18-39'
        WHEN p.age < 60 THEN '40-59'
        WHEN p.age < 75 THEN '60-74'
        ELSE '75+'
    END AS age_band,
    COUNT(DISTINCT t.patient_id) AS positive_patients,
    COUNT(DISTINCT h.patient_id) AS hospitalized,
    ROUND(100.0 * COUNT(DISTINCT h.patient_id) / NULLIF(COUNT(DISTINCT t.patient_id), 0), 2)
        AS hospitalization_rate_pct,
    SUM(CASE WHEN h.icu_flag = 1 THEN 1 ELSE 0 END) AS icu_admissions,
    ROUND(100.0 * SUM(CASE WHEN h.icu_flag = 1 THEN 1 ELSE 0 END)
        / NULLIF(COUNT(DISTINCT h.patient_id), 0), 2) AS icu_rate_pct_of_hospitalized
FROM patients p
JOIN rtpcr_tests t ON t.patient_id = p.patient_id AND t.result = 'Positive'
LEFT JOIN hospitalizations h ON h.patient_id = p.patient_id
GROUP BY age_band
ORDER BY age_band;


-- ---------------------------------------------------------------
-- Q8. Length-of-stay distribution by outcome (recovered vs deceased vs
--     transferred) — informs bed-turnover assumptions in the LP model
-- ---------------------------------------------------------------
SELECT
    outcome,
    COUNT(*) AS n_cases,
    ROUND(AVG(JULIANDAY(discharge_date) - JULIANDAY(admission_date)), 1) AS avg_los_days,
    ROUND(MIN(JULIANDAY(discharge_date) - JULIANDAY(admission_date)), 1) AS min_los_days,
    ROUND(MAX(JULIANDAY(discharge_date) - JULIANDAY(admission_date)), 1) AS max_los_days
FROM hospitalizations
WHERE discharge_date IS NOT NULL
GROUP BY outcome;


-- ---------------------------------------------------------------
-- Q9. Monthly hospitalization and ICU trend (for the executive
--     summary line chart in the Tableau dashboard)
-- ---------------------------------------------------------------
SELECT
    strftime('%Y-%m', admission_date) AS month,
    COUNT(*) AS admissions,
    SUM(icu_flag) AS icu_admissions,
    SUM(ventilator_flag) AS ventilator_admissions,
    ROUND(100.0 * SUM(icu_flag) / COUNT(*), 1) AS icu_rate_pct
FROM hospitalizations
GROUP BY month
ORDER BY month;


-- ---------------------------------------------------------------
-- Q10. Facility-level ranking: which hospitals ran "hottest" on
--      average ICU strain across the whole observation window
--      (used to prioritize where the resource-optimization model
--      should reallocate ventilators/staff from lower-strain sites)
-- ---------------------------------------------------------------
SELECT
    f.facility_name,
    ROUND(AVG(1.0 * ru.icu_occupied / NULLIF(f.icu_capacity, 0)), 3) AS avg_icu_strain,
    ROUND(AVG(1.0 * ru.beds_occupied / NULLIF(f.bed_capacity, 0)), 3) AS avg_bed_strain,
    ROUND(MAX(1.0 * ru.icu_occupied / NULLIF(f.icu_capacity, 0)), 3) AS peak_icu_strain,
    SUM(CASE WHEN ru.icu_occupied >= f.icu_capacity THEN 1 ELSE 0 END) AS days_at_full_icu_capacity
FROM resource_utilization ru
JOIN facilities f ON f.facility_id = ru.facility_id
GROUP BY f.facility_name
ORDER BY avg_icu_strain DESC;


-- ---------------------------------------------------------------
-- Q11. Ct-value trend as a proxy for viral load / transmissibility
--      shifts (lower Ct = higher viral load) — an epidemiologically
--      meaningful signal beyond simple case counts
-- ---------------------------------------------------------------
SELECT
    strftime('%Y-%m', test_date) AS month,
    COUNT(*) AS positive_tests,
    ROUND(AVG(ct_value), 2) AS avg_ct_value,
    ROUND(MIN(ct_value), 1) AS min_ct_value
FROM rtpcr_tests
WHERE result = 'Positive'
GROUP BY month
ORDER BY month;


-- ---------------------------------------------------------------
-- Q12. Weekday vs weekend testing volume bias (data-quality check
--      that should inform how you interpret raw daily figures --
--      a genuinely "PhD-level" touch: acknowledging measurement bias
--      before drawing conclusions from the trend)
-- ---------------------------------------------------------------
SELECT
    CASE CAST(strftime('%w', d.test_date) AS INTEGER)
        WHEN 0 THEN 'Sunday' WHEN 1 THEN 'Monday' WHEN 2 THEN 'Tuesday'
        WHEN 3 THEN 'Wednesday' WHEN 4 THEN 'Thursday' WHEN 5 THEN 'Friday'
        WHEN 6 THEN 'Saturday' END AS day_of_week,
    COUNT(*) AS num_days,
    ROUND(AVG(d.daily_total), 1) AS avg_tests_per_day
FROM (
    SELECT test_date, COUNT(*) AS daily_total FROM rtpcr_tests GROUP BY test_date
) d
GROUP BY day_of_week
ORDER BY avg_tests_per_day DESC;
