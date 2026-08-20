# COVID-19 Healthcare Impact Analysis
### RT-PCR Testing Trends, Hospitalization Dynamics & Resource Allocation Optimization

A graduate-level (PhD-caliber) healthcare data analytics project demonstrating the full pipeline
referenced in the resume bullet:

> *"Conducted comprehensive trend analysis on RT-PCR test results and hospitalization patterns
> using SQL and Tableau, building real-time dashboards that optimized resource allocation during
> pandemic response."*

This repo gives you everything needed to reproduce that project end-to-end on your own machine:
a relational database (SQLite, zero-config), a synthetic-but-epidemiologically-realistic dataset
generator, an analytical SQL layer, a statistical/forecasting layer in Python (time series
decomposition, SARIMA forecasting, changepoint detection, Poisson regression, linear-programming
resource optimization), and a Tableau dashboard build guide.

**Why synthetic data?** Real patient-level COVID data is protected health information (PHI) under
HIPAA and isn't publicly redistributable at the individual level. This project generates data with
realistic epidemic dynamics (multi-wave case curves, testing lag, hospitalization lag, ICU
progression rates, capacity constraints) so every downstream technique, the SQL, the stats, the
dashboards, is something you can describe and defend in an interview using your own numbers.

---

## 1. Project Structure

```
covid_project/
├── README.md                          ← you are here
├── requirements.txt
├── sql/
│   ├── 01_schema.sql                  ← relational schema (6 tables)
│   └── 02_analysis_queries.sql        ← 12 analytical queries (window fns, CTEs, rolling avgs)
├── python/
│   ├── generate_synthetic_data.py     ← builds the SQLite DB + CSVs
│   ├── statistical_analysis.py        ← decomposition, SARIMA forecast, changepoint detection
│   └── resource_optimization.py       ← linear programming ICU/ventilator allocation model
├── tableau/
│   ├── TABLEAU_DASHBOARD_GUIDE.md     ← step-by-step dashboard build instructions
│   └── calculated_fields.md           ← every calculated field, ready to paste into Tableau
├── data/                              ← generated CSVs + covid_healthcare.db land here
└── docs/
    └── METHODOLOGY.md                 ← the "PhD-level" writeup: assumptions, limitations, stats
```

## 2. Setup on your Mac

```bash
# 1. Clone/unzip this folder, then cd into it
cd covid_project

# 2. Create a virtual environment (Python 3.10+ recommended)
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Generate the dataset (creates data/covid_healthcare.db + CSVs)
python python/generate_synthetic_data.py

# 5. Run the SQL analysis layer against the generated SQLite DB
sqlite3 data/covid_healthcare.db < sql/01_schema.sql   # (no-op if generator already ran it)
sqlite3 data/covid_healthcare.db
sqlite> .read sql/02_analysis_queries.sql

# 6. Run the statistical / forecasting layer
python python/statistical_analysis.py

# 7. Run the resource-allocation optimization model
python python/resource_optimization.py

# 8. Open Tableau Desktop / Tableau Public and follow tableau/TABLEAU_DASHBOARD_GUIDE.md,
#    connecting to the CSVs in data/ (or directly to covid_healthcare.db via the
#    "Other Databases (ODBC)" / SQLite connector).
```

No PostgreSQL/MySQL server required, everything runs against a local SQLite file, which Tableau
Desktop (Mac) connects to natively via its "SQLite" or generic ODBC connector. If you'd rather use
Postgres (closer to a real hospital data warehouse), `sql/01_schema.sql` is written in
portable ANSI SQL and will run with only trivial syntax tweaks (`AUTOINCREMENT` → `SERIAL`).

## 3. What each layer demonstrates

| Layer | Skill demonstrated |
|---|---|
| `sql/01_schema.sql` | Relational modeling of a healthcare data warehouse (facts + dimensions) |
| `sql/02_analysis_queries.sql` | Window functions, rolling averages, positivity-rate calc, doubling time, ICU strain index, cohort/regional comparisons |
| `generate_synthetic_data.py` | Epidemic curve simulation (multi-wave Gaussian mixture), realistic test→hospitalization→ICU lag structure |
| `statistical_analysis.py` | STL time-series decomposition, SARIMA forecasting w/ confidence intervals, CUSUM changepoint detection for wave onset, Poisson regression for hospitalization demand |
| `resource_optimization.py` | Linear programming (PuLP) to optimally allocate a constrained ICU-bed/ventilator pool across facilities under demand forecasts, this is the "optimized resource allocation" claim, made rigorous and defensible |
| Tableau dashboards | Real-time-style KPI dashboard: positivity rate trend, hospitalization funnel, regional heatmap, resource strain gauge |

## 4. Using this for your resume / interview

`docs/METHODOLOGY.md` contains a written methodology section (assumptions, model choices,
limitations) in the style expected for a portfolio piece or take-home case study, use it to
answer "walk me through your project" questions with specifics instead of generalities.
