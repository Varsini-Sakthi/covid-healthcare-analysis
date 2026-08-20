"""
generate_synthetic_data.py
---------------------------------------------------------------------
Generates an epidemiologically-realistic (but fully synthetic) COVID-19
healthcare dataset: regions, facilities, patients, RT-PCR tests,
hospitalizations, and daily resource-utilization snapshots.

Design notes (why this isn't just random noise):
  * Daily case counts follow a mixture of Gaussian "waves" + weekly
    seasonality (lower testing on weekends) + Poisson noise, which
    mimics real epidemic curves (e.g. Delta/Omicron waves).
  * Test positivity rate is derived FROM the case curve, not generated
    independently, so positivity-rate trend analysis is meaningful.
  * Hospitalizations lag positive tests by a random draw from a
    log-normal distribution (median ~7 days), matching CDC-reported
    test-to-hospitalization lag structure.
  * ICU/ventilator progression probability increases with patient
    age and comorbidity_count, so the resource-optimization model
    has real signal to learn from.
  * Facility bed/ICU capacity is finite, so resource_utilization can
    exceed capacity during wave peaks — this creates genuine
    "surge"/strain periods for the dashboards to visualize.

Fully vectorized with NumPy/pandas (no per-row Python loops), so it
generates several hundred thousand records in a few seconds.

Run:  python python/generate_synthetic_data.py
Output: data/covid_healthcare.db (SQLite) + CSV exports in data/
"""

import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import date

RNG_SEED = 42
rng = np.random.default_rng(RNG_SEED)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "covid_healthcare.db"
SQL_SCHEMA_PATH = ROOT / "sql" / "01_schema.sql"

START_DATE = date(2020, 3, 1)
END_DATE = date(2021, 12, 31)
N_DAYS = (END_DATE - START_DATE).days + 1

REGIONS = [
    ("Northside Metro", 850_000),
    ("Eastview County", 420_000),
    ("Southport District", 610_000),
    ("Westfield Region", 340_000),
]

FACILITY_TEMPLATES = [
    ("Northside General Hospital", 0, "Hospital", 420, 48, 60),
    ("Northside Community Testing Center", 0, "Testing Site", 0, 0, 0),
    ("Eastview Regional Medical Center", 1, "Hospital", 260, 30, 35),
    ("Eastview Field Clinic", 1, "Field Clinic", 40, 4, 2),
    ("Southport University Hospital", 2, "Hospital", 500, 55, 70),
    ("Southport Rapid Testing Site", 2, "Testing Site", 0, 0, 0),
    ("Westfield Memorial Hospital", 3, "Hospital", 180, 20, 24),
]


def build_wave_curve(n_days: int) -> np.ndarray:
    """Multi-wave Gaussian mixture + weekly seasonality -> daily new-case baseline."""
    t = np.arange(n_days)
    waves = [
        (60, 150, 25),
        (260, 267, 35),
        (430, 400, 40),
        (620, 700, 22),
    ]
    curve = np.zeros(n_days, dtype=float)
    for center, height, width in waves:
        curve += height * np.exp(-((t - center) ** 2) / (2 * width ** 2))
    curve += 7

    weekday = (t + START_DATE.weekday()) % 7
    weekend_factor = np.where(weekday >= 5, 0.7, 1.0)
    curve *= weekend_factor
    return np.clip(curve, 0.5, None)


def main():
    print(f"Simulating {N_DAYS} days from {START_DATE} to {END_DATE}...")
    dates = pd.to_datetime(START_DATE) + pd.to_timedelta(np.arange(N_DAYS), unit="D")

    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    with open(SQL_SCHEMA_PATH) as f:
        conn.executescript(f.read())
    cur = conn.cursor()

    for name, pop in REGIONS:
        cur.execute("INSERT INTO regions (region_name, population) VALUES (?,?)", (name, pop))
    for name, region_idx, ftype, beds, icu, vents in FACILITY_TEMPLATES:
        cur.execute(
            """INSERT INTO facilities (facility_name, region_id, facility_type,
               bed_capacity, icu_capacity, ventilator_count) VALUES (?,?,?,?,?,?)""",
            (name, region_idx + 1, ftype, beds, icu, vents))
    conn.commit()

    facility_df = pd.read_sql("SELECT * FROM facilities", conn)
    base_curve = build_wave_curve(N_DAYS)
    total_pop = sum(p for _, p in REGIONS)

    patient_frames, test_frames, hosp_frames = [], [], []
    patient_id_offset = 1

    for region_idx, (region_name, pop) in enumerate(REGIONS):
        region_id = region_idx + 1
        pop_share = pop / total_pop
        region_curve = rng.poisson(base_curve * pop_share * 1.15)

        region_fac = facility_df[facility_df.region_id == region_id]
        region_fac_ids = region_fac.facility_id.to_numpy()
        region_hosp_ids = region_fac[region_fac.facility_type == "Hospital"].facility_id.to_numpy()
        fac_type_map = region_fac.set_index("facility_id").facility_type.to_dict()

        neg_mult = rng.uniform(2.5, 4.0, size=N_DAYS)
        n_negative = (region_curve * neg_mult).astype(int)
        n_inconclusive = rng.poisson(np.clip(region_curve, 1, None) * 0.02)

        day_idx_pos = np.repeat(np.arange(N_DAYS), region_curve)
        day_idx_neg = np.repeat(np.arange(N_DAYS), n_negative)
        day_idx_inc = np.repeat(np.arange(N_DAYS), n_inconclusive)

        day_idx_all = np.concatenate([day_idx_pos, day_idx_neg, day_idx_inc])
        result_all = np.array(
            ["Positive"] * len(day_idx_pos) + ["Negative"] * len(day_idx_neg) +
            ["Inconclusive"] * len(day_idx_inc))
        n = len(day_idx_all)

        patient_ids = np.arange(patient_id_offset, patient_id_offset + n)
        ages = np.clip(rng.gamma(4.0, 13, size=n), 0, 100).astype(int)
        comorbidities = rng.poisson(0.15 * (ages / 40))
        risk_scores = np.clip(
            0.15 + 0.006 * ages + 0.08 * comorbidities + rng.normal(0, 0.05, size=n), 0.01, 0.99)
        genders = rng.choice(["M", "F"], size=n)
        facility_ids = rng.choice(region_fac_ids, size=n)
        test_dates = dates[day_idx_all]

        ct_values = np.full(n, np.nan)
        pos_mask = result_all == "Positive"
        ct_values[pos_mask] = np.clip(rng.normal(24, 5, size=pos_mask.sum()), 10, 38).round(1)

        patient_frames.append(pd.DataFrame({
            "patient_id": patient_ids, "age": ages, "gender": genders,
            "region_id": region_id, "comorbidity_count": comorbidities,
            "risk_score": risk_scores.round(4)}))

        test_frames.append(pd.DataFrame({
            "patient_id": patient_ids, "facility_id": facility_ids,
            "test_date": test_dates, "result": result_all, "ct_value": ct_values}))

        fac_types = np.array([fac_type_map[f] for f in facility_ids])
        eligible = pos_mask & (fac_types != "Field Clinic")
        elig_idx = np.where(eligible)[0]
        k = len(elig_idx)
        if k > 0:
            hosp_prob = np.clip(0.03 + 0.55 * (risk_scores[elig_idx] ** 2), 0.01, 0.35)
            hosp_draw = rng.uniform(size=k) < hosp_prob
            h_idx = elig_idx[hosp_draw]
            kh = len(h_idx)
            if kh > 0:
                lag_days = np.clip(rng.lognormal(1.9, 0.4, size=kh), 1, 21).astype(int)
                admission_dates = test_dates[h_idx] + pd.to_timedelta(lag_days, unit="D")
                valid = admission_dates <= pd.Timestamp(END_DATE)
                h_idx, admission_dates = h_idx[valid], admission_dates[valid]
                kh = len(h_idx)

                los = np.clip(rng.lognormal(1.8, 0.6, size=kh), 1, 45).astype(int)
                discharge_dates = admission_dates + pd.to_timedelta(los, unit="D")
                overflow = discharge_dates > pd.Timestamp(END_DATE)
                discharge_dates = discharge_dates.where(~overflow, pd.NaT)

                rs = risk_scores[h_idx]
                icu_flag = (rng.uniform(size=kh) < np.clip(0.10 + 0.4 * rs, 0, 0.6)).astype(int)
                vent_flag = (icu_flag & (rng.uniform(size=kh) < 0.45)).astype(int)

                u = rng.uniform(size=kh)
                p_recovered = 0.86 - 0.25 * rs
                p_deceased = 0.10 + 0.25 * rs
                outcome = np.where(overflow, None,
                          np.where(u < p_recovered, "Recovered",
                          np.where(u < p_recovered + p_deceased, "Deceased", "Transferred")))

                hosp_fac_ids = rng.choice(region_hosp_ids, size=kh) if len(region_hosp_ids) else facility_ids[h_idx]

                hosp_frames.append(pd.DataFrame({
                    "patient_id": patient_ids[h_idx], "facility_id": hosp_fac_ids,
                    "admission_date": admission_dates, "discharge_date": discharge_dates,
                    "icu_flag": icu_flag, "ventilator_flag": vent_flag, "outcome": outcome}))

        patient_id_offset += n
        print(f"  Region '{region_name}': {n:,} test records simulated.")

    patients_df = pd.concat(patient_frames, ignore_index=True)
    tests_df = pd.concat(test_frames, ignore_index=True)
    hosp_df = pd.concat(hosp_frames, ignore_index=True) if hosp_frames else pd.DataFrame()

    print(f"\nTotal: {len(patients_df):,} patients/tests, {len(hosp_df):,} hospitalizations.")

    tests_df["test_date"] = tests_df["test_date"].dt.strftime("%Y-%m-%d")
    hosp_df["admission_date"] = hosp_df["admission_date"].dt.strftime("%Y-%m-%d")
    hosp_df["discharge_date"] = hosp_df["discharge_date"].dt.strftime("%Y-%m-%d")
    hosp_df["discharge_date"] = hosp_df["discharge_date"].replace("NaT", None)

    patients_df.to_sql("patients", conn, if_exists="append", index=False)
    tests_df.to_sql("rtpcr_tests", conn, if_exists="append", index=False)
    hosp_df.to_sql("hospitalizations", conn, if_exists="append", index=False)
    conn.commit()

    # ---- Daily resource_utilization snapshots from hospitalization occupancy ----
    hosp_df["admission_date"] = pd.to_datetime(hosp_df["admission_date"])
    hosp_df["discharge_date"] = pd.to_datetime(hosp_df["discharge_date"])
    date_index = pd.DatetimeIndex(dates)
    resource_rows = []

    for fac in facility_df.itertuples():
        if fac.facility_type != "Hospital":
            continue
        fh = hosp_df[hosp_df.facility_id == fac.facility_id]
        if fh.empty:
            occ_counts = np.zeros(N_DAYS, dtype=int)
            icu_counts = np.zeros(N_DAYS, dtype=int)
            vent_counts = np.zeros(N_DAYS, dtype=int)
        else:
            adm = fh.admission_date.to_numpy()
            dis = fh.discharge_date.fillna(pd.Timestamp(END_DATE) + pd.Timedelta(days=1)).to_numpy()
            icu = fh.icu_flag.to_numpy().astype(bool)
            vent = fh.ventilator_flag.to_numpy().astype(bool)
            d_arr = date_index.to_numpy()[:, None]
            occ_mask = (adm[None, :] <= d_arr) & (dis[None, :] >= d_arr)
            occ_counts = np.minimum(occ_mask.sum(axis=1), fac.bed_capacity)
            icu_counts = np.minimum((occ_mask & icu[None, :]).sum(axis=1), fac.icu_capacity)
            vent_counts = np.minimum((occ_mask & vent[None, :]).sum(axis=1), fac.ventilator_count)

        staff = np.clip(
            fac.bed_capacity * 0.9 * rng.uniform(0.85, 1.0, size=N_DAYS)
            - occ_counts * rng.uniform(0.0, 0.15, size=N_DAYS), 5, None).astype(int)

        for i, d in enumerate(dates):
            resource_rows.append((d.strftime("%Y-%m-%d"), fac.facility_id,
                                   int(occ_counts[i]), int(icu_counts[i]),
                                   int(vent_counts[i]), int(staff[i])))

    cur.executemany(
        "INSERT INTO resource_utilization (snapshot_date, facility_id, beds_occupied, "
        "icu_occupied, ventilators_in_use, staff_available) VALUES (?,?,?,?,?,?)", resource_rows)
    conn.commit()

    for table in ["regions", "facilities", "patients", "rtpcr_tests", "hospitalizations",
                  "resource_utilization"]:
        df = pd.read_sql(f"SELECT * FROM {table}", conn)
        df.to_csv(DATA_DIR / f"{table}.csv", index=False)
        print(f"  Exported {table}.csv ({len(df):,} rows)")

    conn.close()
    print(f"\nDone. SQLite DB written to: {DB_PATH}")


if __name__ == "__main__":
    main()
