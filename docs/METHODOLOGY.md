# Methodology

## 1. Problem framing

The project reconstructs the analytical pipeline a healthcare data analyst would run during a
pandemic response: (a) monitor RT-PCR testing trends to detect emerging waves early, (b) model
the downstream hospitalization/ICU burden those waves produce, and (c) turn that burden forecast
into a concrete resource-allocation decision. Each stage is implemented with a method chosen to
match what that stage actually requires — not the fanciest available technique.

## 2. Data

Real patient-level COVID-19 records are PHI and cannot be redistributed, so this project uses a
synthetic dataset generated to match known epidemiological structure rather than raw randomness:

- **Multi-wave case curves**: a Gaussian-mixture model with four wave components (spring 2020,
  winter 2020–21, a Delta-like wave, an Omicron-like sharp wave), matched to the general shape
  and relative timing of the actual U.S. pandemic curve (without reproducing any real dataset).
- **Weekly testing seasonality**: a ~30% weekend reduction in test volume, matching the
  well-documented reporting-lag artifact in real public health surveillance data.
- **Test-to-hospitalization lag**: log-normal distributed (median ≈ 7 days), consistent with
  CDC-reported lag distributions between diagnosis and hospital admission.
- **Risk-stratified severity**: hospitalization, ICU, and mortality probabilities scale with a
  composite `risk_score` driven by age and comorbidity count, so age-stratified analysis (Q7 in
  the SQL layer) recovers a realistic risk gradient rather than flat probabilities.
- **Finite facility capacity**: bed/ICU/ventilator counts are fixed per facility, so occupancy can
  genuinely exceed capacity during wave peaks — this is what makes the "surge" and resource-strain
  analysis meaningful rather than cosmetic.

**Limitation**: this is a single synthetic realization with one random seed. A real deployment
would need to validate that the generative assumptions (wave shape, lag distributions, risk
gradients) match the target population before treating any of the downstream numbers as
operationally actionable. Every technique below is chosen so it would transfer directly to a real
data warehouse with only a data-source swap.

## 3. Trend analysis (SQL layer)

Twelve queries (`sql/02_analysis_queries.sql`) implement rolling averages, week-over-week growth
ratios, cohort/regional comparisons, and a doubling-time estimate. Two choices are worth calling
out explicitly because they're the kind of detail an interviewer will probe:

- **7-day rolling windows, not raw daily figures.** Given the weekend testing artifact described
  above, a raw daily positivity rate is a biased, noisy signal. All positivity/case trend metrics
  use a trailing 7-day window, matching CDC/WHO surveillance convention.
- **Doubling time is derived from the ratio of rolling averages one week apart**, not from a
  naive two-point growth rate, for the same reason — using un-smoothed daily counts would make
  the estimate extremely sensitive to which two days you happen to compare.

## 4. Statistical modeling (`python/statistical_analysis.py`)

| Technique | Why this technique, specifically |
|---|---|
| **STL decomposition** (period=7) | Separates trend from weekly seasonality and residual noise. Chosen over a simple moving average because it explicitly quantifies seasonal strength (Wang et al. 2006 measure), giving a defensible number for "how much of the day-to-day wiggle is just the weekend testing artifact vs. real signal." |
| **SARIMA(2,1,2)×(1,1,1,7)** | A seasonal ARIMA is the standard baseline for count-like time series with weekly periodicity. Validated with a walk-forward holdout (last 21 days), reporting MAE/RMSE/AIC rather than in-sample fit only — in-sample R² on a time series is not a valid measure of forecast quality. |
| **CUSUM changepoint detection** | Identifies wave onset dates algorithmically instead of by eye. A two-sided CUSUM with a `k=0.5σ` slack and `h=4σ` threshold is a standard, interpretable choice (as opposed to a black-box Bayesian changepoint model) — appropriate given the audience (operational stakeholders) needs to trust *why* a date was flagged. |
| **Poisson GLM** (admissions ~ lagged cases) | Admissions are count data, so Poisson (not OLS) is the correct link function. Multiple lags (3/7/10/14 days) are included simultaneously so the model can separate "fast" severe cases from slower-progressing ones, rather than assuming a single fixed lag. |

**Statistical caveats stated up front** (the kind of thing a rigorous analyst flags before anyone
asks): the Poisson model assumes lagged case counts are exogenous, which ignores the feedback loop
where hospital strain itself can suppress testing capacity; the SARIMA model does not incorporate
exogenous regressors (like new variant emergence) that would improve real forecasts; and the CUSUM
threshold is a tunable hyperparameter, not a ground truth — different `h` values produce different
changepoint counts, so the algorithm's output should be read as a hypothesis to validate against
domain knowledge, not a final answer.

## 5. Resource allocation optimization (`python/resource_optimization.py`)

This is the part of the original bullet point ("optimized resource allocation") that most
portfolios leave as an unsupported claim. Here it's a small, transparent linear program:

- **Decision variables**: integer bed transfers `x[f]` per facility.
- **Objective**: minimize total projected unmet ICU demand across the network.
- **Constraints**: the reallocation is zero-sum (beds are moved, not conjured), a facility can
  only donate beds up to a fraction of its own projected *headroom* (capacity minus its own
  projected demand — it can't be left short to help a neighbor), and no facility's effective
  capacity can go negative.
- **Demand projection** uses a transparent linear extrapolation of each facility's most recent
  14-day ICU trend (not a black-box forecast), specifically so that every number feeding the
  optimizer can be explained without re-deriving a whole time series model in the interview room.

The model is deliberately simple (a static single-period LP, not a multi-period stochastic
program) — appropriate for a portfolio piece that needs to be fully explainable, while still
producing a real, solved, non-trivial reallocation recommendation with a quantified improvement
over the no-reallocation baseline.

## 6. What would change for a production/real-data version

1. Swap the synthetic generator for a real (de-identified, IRB-approved) data extract; re-validate
   every distributional assumption listed in §2.
2. Replace the static LP with a rolling multi-period stochastic program that re-solves daily as
   new forecasts arrive, and add a penalty term for the *cost* of transfer (travel time, staff
   disruption), not just bed count.
3. Add exogenous regressors to the SARIMA model (variant prevalence, mobility data, vaccination
   rate) — a plain seasonal ARIMA is a reasonable baseline, not a ceiling.
4. Move the SQL layer from SQLite to a warehouse (Postgres/Snowflake/BigQuery) with materialized
   views refreshed on a schedule, so the Tableau dashboards are genuinely real-time rather than
   reading a static extract.
