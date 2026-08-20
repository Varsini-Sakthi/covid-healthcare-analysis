# Tableau Calculated Fields

Paste these directly into Tableau's calculated field editor (Analysis → Create Calculated Field).
Field names below are also the recommended Tableau field name.

---

**Is Positive**
```
IIF([result] = "Positive", 1, 0)
```

**7-Day Rolling Positivity %**
*(Table calc — set "Compute Using" to `test_date`, window: 6 previous + current)*
```
WINDOW_SUM(SUM([Is Positive])) / WINDOW_SUM(COUNTD([test_id])) * 100
```
Set the window via Analysis → Table Calculation → "Moving Calculation" → Previous 6 values.

**Positive Tests**
```
SUM([Is Positive])
```

**Hospitalizations**
*(Requires a relationship/blend to the hospitalizations table)*
```
COUNTD(IIF(ISNULL([hosp_id]), NULL, [patient_id]))
```

**ICU Admissions**
```
SUM([icu_flag])
```

**Ventilator Cases**
```
SUM([ventilator_flag])
```

**Hospitalization Rate %**
```
[Hospitalizations] / [Positive Tests] * 100
```

**ICU Strain Index**
*(on the resource_utilization + facilities data source)*
```
[icu_occupied] / [icu_capacity]
```

**Bed Strain Index**
```
[beds_occupied] / [bed_capacity]
```

**Strain Status**
```
IF [ICU Strain Index] >= 0.85 THEN "Surge Warning"
ELSEIF [ICU Strain Index] >= 0.7 THEN "Elevated"
ELSE "Normal"
END
```

**Length of Stay (days)**
```
DATEDIFF('day', [admission_date], [discharge_date])
```

**Case Fatality Rate % (of hospitalized)**
```
SUM(IIF([outcome] = "Deceased", 1, 0)) / COUNTD([patient_id]) * 100
```

**Doubling Time Proxy (week-over-week ratio)**
*(table calc, compute using test_date, window: 7 back)*
```
SUM([Is Positive]) / LOOKUP(SUM([Is Positive]), -7)
```
