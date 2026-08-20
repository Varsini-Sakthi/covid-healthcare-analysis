"""
statistical_analysis.py
---------------------------------------------------------------------
Graduate-level statistical analysis layer for the COVID-19 healthcare
dataset. Demonstrates four techniques commonly expected of a senior /
PhD-level healthcare data analyst:

  1. STL time-series decomposition of daily positive cases
     (trend / weekly-seasonality / residual)
  2. SARIMA forecasting of daily hospital admissions, with a walk-
     forward validation split and 95% confidence intervals
  3. CUSUM changepoint detection to algorithmically identify the
     onset day of each pandemic "wave" (instead of eyeballing a chart)
  4. Poisson regression modeling daily hospitalization counts as a
     function of lagged case counts — quantifies the lead time and
     magnitude of the case-to-hospitalization relationship, which is
     exactly the kind of evidence a "resource allocation" claim needs
     to be defensible in an interview.

Run:  python python/statistical_analysis.py
Requires: data/covid_healthcare.db (run generate_synthetic_data.py first)
Outputs: PNG charts + printed model summaries in data/analysis_outputs/
"""

import sqlite3
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

from statsmodels.tsa.seasonal import STL
from statsmodels.tsa.statespace.sarimax import SARIMAX
import statsmodels.api as sm
import statsmodels.formula.api as smf

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "covid_healthcare.db"
OUT_DIR = ROOT / "data" / "analysis_outputs"
OUT_DIR.mkdir(exist_ok=True, parents=True)


def load_daily_series():
    conn = sqlite3.connect(DB_PATH)
    cases = pd.read_sql(
        """SELECT test_date, SUM(CASE WHEN result='Positive' THEN 1 ELSE 0 END) AS positives
           FROM rtpcr_tests GROUP BY test_date ORDER BY test_date""", conn,
        parse_dates=["test_date"])
    hosp = pd.read_sql(
        """SELECT admission_date, COUNT(*) AS admissions
           FROM hospitalizations GROUP BY admission_date ORDER BY admission_date""", conn,
        parse_dates=["admission_date"])
    conn.close()

    cases = cases.set_index("test_date").asfreq("D").fillna(0)
    hosp = hosp.set_index("admission_date").asfreq("D").fillna(0)
    df = cases.join(hosp, how="left").fillna(0)
    df.columns = ["positives", "admissions"]
    return df


# ---------------------------------------------------------------
# 1. STL Decomposition
# ---------------------------------------------------------------
def run_stl_decomposition(df: pd.DataFrame):
    print("\n=== 1. STL Time-Series Decomposition (daily positive cases) ===")
    series = df["positives"].clip(lower=0.1)  # STL prefers strictly positive-ish series
    stl = STL(series, period=7, robust=True)  # period=7 -> weekly seasonality
    result = stl.fit()

    fig, axes = plt.subplots(4, 1, figsize=(11, 9), sharex=True)
    axes[0].plot(series.index, series.values, color="#2c3e50"); axes[0].set_title("Observed")
    axes[1].plot(series.index, result.trend, color="#2980b9"); axes[1].set_title("Trend")
    axes[2].plot(series.index, result.seasonal, color="#27ae60"); axes[2].set_title("Weekly Seasonality")
    axes[3].plot(series.index, result.resid, color="#c0392b"); axes[3].set_title("Residual")
    plt.tight_layout()
    fig.savefig(OUT_DIR / "stl_decomposition.png", dpi=120)
    plt.close(fig)

    seasonal_strength = 1 - result.resid.var() / (result.seasonal + result.resid).var()
    print(f"  Weekly seasonal strength (Wang et al. 2006 measure): {seasonal_strength:.3f}")
    print(f"  -> Interpretation: {'strong' if seasonal_strength > 0.5 else 'moderate'} weekly "
          f"periodicity, consistent with reduced weekend testing volume.")
    print(f"  Chart saved: {OUT_DIR / 'stl_decomposition.png'}")
    return result


# ---------------------------------------------------------------
# 2. SARIMA Forecasting of daily hospital admissions
# ---------------------------------------------------------------
def run_sarima_forecast(df: pd.DataFrame, forecast_horizon=21):
    print("\n=== 2. SARIMA Forecast (daily hospital admissions) ===")
    series = df["admissions"]
    train = series.iloc[:-forecast_horizon]
    test = series.iloc[-forecast_horizon:]

    # (p,d,q)x(P,D,Q,7): weekly seasonal order, modest non-seasonal order
    model = SARIMAX(train, order=(2, 1, 2), seasonal_order=(1, 1, 1, 7),
                     enforce_stationarity=False, enforce_invertibility=False)
    fit = model.fit(disp=False)

    forecast_res = fit.get_forecast(steps=forecast_horizon)
    forecast_mean = forecast_res.predicted_mean
    conf_int = forecast_res.conf_int(alpha=0.05)

    mae = np.mean(np.abs(forecast_mean.values - test.values))
    rmse = np.sqrt(np.mean((forecast_mean.values - test.values) ** 2))
    print(f"  Holdout period: last {forecast_horizon} days")
    print(f"  MAE:  {mae:.2f} admissions/day")
    print(f"  RMSE: {rmse:.2f} admissions/day")
    print(f"  AIC:  {fit.aic:.1f}")

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(train.index[-60:], train.values[-60:], label="Training data", color="#2c3e50")
    ax.plot(test.index, test.values, label="Actual (holdout)", color="#27ae60", marker="o", ms=3)
    ax.plot(forecast_mean.index, forecast_mean.values, label="SARIMA forecast", color="#c0392b", ls="--")
    ax.fill_between(forecast_mean.index, conf_int.iloc[:, 0], conf_int.iloc[:, 1],
                     color="#c0392b", alpha=0.15, label="95% CI")
    ax.set_title("SARIMA(2,1,2)x(1,1,1,7) Forecast — Daily Hospital Admissions")
    ax.legend()
    plt.tight_layout()
    fig.savefig(OUT_DIR / "sarima_forecast.png", dpi=120)
    plt.close(fig)
    print(f"  Chart saved: {OUT_DIR / 'sarima_forecast.png'}")
    return fit


# ---------------------------------------------------------------
# 3. CUSUM Changepoint Detection (wave-onset identification)
# ---------------------------------------------------------------
def run_cusum_changepoints(df: pd.DataFrame, threshold_sigma=4.0):
    print("\n=== 3. CUSUM Changepoint Detection (wave onsets) ===")
    series = df["positives"].rolling(7, min_periods=1).mean()
    x = series.values
    mean, std = x.mean(), x.std()

    # Two-sided CUSUM on standardized series
    pos_cusum, neg_cusum = np.zeros(len(x)), np.zeros(len(x))
    changepoints = []
    k = 0.5 * std  # allowance/slack parameter
    h = threshold_sigma * std  # decision threshold

    for i in range(1, len(x)):
        pos_cusum[i] = max(0, pos_cusum[i - 1] + (x[i] - mean) - k)
        neg_cusum[i] = min(0, neg_cusum[i - 1] + (x[i] - mean) + k)
        if pos_cusum[i] > h or neg_cusum[i] < -h:
            changepoints.append(i)
            pos_cusum[i] = 0
            neg_cusum[i] = 0

    cp_dates = series.index[changepoints]
    print(f"  Detected {len(cp_dates)} changepoints (wave onsets/inflections):")
    for d in cp_dates:
        print(f"    -> {d.date()}")

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(series.index, series.values, color="#2c3e50", label="7-day avg positive cases")
    for d in cp_dates:
        ax.axvline(d, color="#e74c3c", alpha=0.6, ls="--")
    ax.set_title("CUSUM-Detected Changepoints in Case Trajectory")
    ax.legend()
    plt.tight_layout()
    fig.savefig(OUT_DIR / "cusum_changepoints.png", dpi=120)
    plt.close(fig)
    print(f"  Chart saved: {OUT_DIR / 'cusum_changepoints.png'}")
    return cp_dates


# ---------------------------------------------------------------
# 4. Poisson Regression: hospitalization demand ~ lagged case counts
# ---------------------------------------------------------------
def run_poisson_regression(df: pd.DataFrame, max_lag=14):
    print("\n=== 4. Poisson Regression: Admissions ~ Lagged Positive Cases ===")
    data = df.copy()
    for lag in [3, 7, 10, 14]:
        data[f"positives_lag{lag}"] = data["positives"].shift(lag)
    data = data.dropna()

    formula = "admissions ~ positives_lag3 + positives_lag7 + positives_lag10 + positives_lag14"
    model = smf.glm(formula=formula, data=data, family=sm.families.Poisson())
    fit = model.fit()

    print(fit.summary().tables[1])
    print(f"\n  Pseudo R-sq (McFadden approx): "
          f"{1 - fit.llf / smf.glm('admissions ~ 1', data=data, family=sm.families.Poisson()).fit().llf:.3f}")

    best_lag_row = fit.params.drop("Intercept").idxmax()
    print(f"  Strongest single predictor: {best_lag_row} "
          f"(coef={fit.params[best_lag_row]:.4f}, p={fit.pvalues[best_lag_row]:.4g})")
    print("  Interpretation: each additional case at this lag is associated with a "
          f"{100*(np.exp(fit.params[best_lag_row])-1):.2f}% change in expected daily admissions, "
          "holding other lags constant — this is the empirical basis for a lead-time-based "
          "resource-allocation policy (see resource_optimization.py).")
    return fit


def main():
    if not DB_PATH.exists():
        raise SystemExit("Database not found. Run generate_synthetic_data.py first.")
    df = load_daily_series()
    print(f"Loaded {len(df)} days of data ({df.index.min().date()} to {df.index.max().date()})")

    run_stl_decomposition(df)
    run_sarima_forecast(df)
    run_cusum_changepoints(df)
    run_poisson_regression(df)

    print(f"\nAll analysis outputs written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
