# UK Mortgage Rate Trigger Monitor

Automated UK mortgage-rate monitor that checks the indicators most likely to change the 6-month, 12-month and 2-year outlook for fixed mortgage rates.

## What it monitors

The workflow currently tracks:

- Bank of England Bank Rate
- Headline CPI
- Core CPI
- Services CPI
- Private-sector regular wage growth
- UK unemployment
- 2-year SONIA swap rate
- 5-year SONIA swap rate

ONS indicators are read from official ONS time-series data. Bank Rate is read from the Bank of England. Public SONIA swap closes are read from BlueGamma's public table; these are normally the previous UK business-day close rather than live intraday prices.

## Trigger levels

Trigger thresholds are kept in `config.json` so they can be changed without editing Python.

Initial thresholds:

| Indicator | Lower-rate signal | Higher-rate signal |
|---|---:|---:|
| Headline CPI | <= 2.50% | >= 3.50% |
| Core CPI | <= 2.30% | >= 3.00% |
| Services CPI | <= 3.00% | >= 3.80% |
| Private regular wage growth | <= 3.00% | >= 4.25% |
| Unemployment | >= 5.30% | <= 4.50% |
| 2Y SONIA swap | <= 4.00% | >= 5.00% |
| 5Y SONIA swap | <= 4.10% | >= 5.00% |

The swap series have the greatest weight in the forecast model, followed by services inflation and wages.

## Forecast output

When a material trigger fires, the email contains updated ranges for:

- 6-month typical 2-year fixed mortgage
- 6-month typical 5-year fixed mortgage
- 12-month typical 2-year fixed mortgage
- 12-month typical 5-year fixed mortgage
- 2-year-ahead typical 2-year fixed mortgage
- 2-year-ahead typical 5-year fixed mortgage

The forecast is calibrated to a mainstream borrower around 75% LTV. It is a scenario-monitoring model rather than a lender quote or a claim of precise future rates.

The forecast anchors and sensitivities are all visible in `config.json`.

## When it emails

The repository checks twice each UK weekday at approximately:

- 08:37 Europe/London
- 18:37 Europe/London

It does **not** email on every run. An email is sent when one or more of these occurs:

1. An indicator crosses from neutral into a lower-rate or higher-rate trigger band, or moves back out of one.
2. The 2Y or 5Y SONIA swap has moved by at least **0.15 percentage points cumulatively since the last alert**.
3. A forecast midpoint moves by at least **0.15 percentage points since the last alert**.
4. Bank Rate changes by roughly one standard 0.25 percentage-point MPC step.
5. A monitored source fails and the model has to use its configured fallback value.

The important detail is that swap changes are compared with the **last emailed alert anchor**, not merely the previous run. Several small daily moves therefore accumulate until they become material.

## Email setup

This repository uses the same Gmail/App Password approach as the Soak & Sleep tracker.

Go to:

`Repository > Settings > Secrets and variables > Actions > New repository secret`

Create these three repository secrets:

### `EMAIL_FROM`
The Gmail address that sends the alert.

### `EMAIL_TO`
The address that receives the alert. It can be the same address.

### `EMAIL_APP_PASSWORD`
The Google App Password for the Gmail account. Do not use the normal Gmail password.

GitHub does not allow secrets from one repository to be copied automatically into another, so the same three values used by the duvet tracker need to be added here once.

## Test it

After adding the secrets:

1. Open **Actions**.
2. Select **UK Mortgage Rate Trigger Monitor**.
3. Select **Run workflow**.
4. Leave **Send a test/baseline email** enabled.
5. Run the workflow on `main`.

The first successful run writes the current observations and forecast to `state.json` and emails the baseline dashboard.

## Files

- `monitor.py` — data collection, trigger logic, forecast calculation and email generation.
- `config.json` — trigger thresholds, weights, baseline market levels and forecast sensitivities.
- `state.json` — latest observations plus the last emailed alert anchor.
- `.github/workflows/mortgage-monitor.yml` — automated schedule and manual test control.

## Current calibration date

Initial calibration: **27 September 2026**.

The baseline was set around Bank Rate 3.75%, headline CPI 3.1%, core CPI 2.6%, services CPI 3.4%, unemployment 4.9%, 2Y SONIA swap 4.63%, and 5Y SONIA swap 4.79%.

As the macro regime changes materially, the thresholds and long-horizon anchors should occasionally be reviewed rather than treated as permanent economic constants.
