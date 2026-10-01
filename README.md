# UK Mortgage Outlook & Decision Monitor

Automated UK mortgage-rate monitor designed to answer two questions:

1. **What are mortgage rates doing now?**
2. **What are they likely to be in 6 months, 12 months and 2 years, and what does that imply for fixing decisions?**

## What it monitors

### Economic data
- Bank of England Bank Rate
- Headline CPI
- Core CPI
- Services CPI
- Private-sector regular wage growth
- UK unemployment

### Market pricing
- 2-year SONIA swap rate
- 5-year SONIA swap rate
- 2-year UK gilt yield
- 5-year UK gilt yield
- 10-year UK gilt yield

### Actual mortgage pricing
The monitor reads average UK home-buyer fixed mortgage rates from Rightmove/Podium for:

- 60% LTV
- 75% LTV
- 90% LTV
- 95% LTV

for both 2-year and 5-year fixes.

If the live mortgage page cannot be read, configured fallback rates are used and the email flags the source warning.

## How the forecast works

SONIA swaps remain the most important direct input because fixed mortgage pricing is closely linked to the relevant swap tenor:

- 2-year fixes are most sensitive to the 2-year SONIA swap
- 5-year fixes are most sensitive to the 5-year SONIA swap

The model then adjusts for:

- inflation pressure
- wage pressure
- unemployment
- gilt yields / term-premium and fiscal-market stress
- changes in actual mortgage pricing that may reflect lender margins and competition

Gilts deliberately receive a lower weight than SONIA swaps so correlated bond-market moves are not double-counted excessively.

## Forecast output

Each alert contains scenario-model ranges for:

- 6-month typical 2-year fixed mortgage
- 6-month typical 5-year fixed mortgage
- 12-month typical 2-year fixed mortgage
- 12-month typical 5-year fixed mortgage
- 2-year-ahead typical 2-year fixed mortgage
- 2-year-ahead typical 5-year fixed mortgage

These are forecast ranges, not lender quotes.

## Decision guide: 2-year vs 5-year fix

The email now includes a break-even calculation.

Using the assumptions in `config.json`, it calculates the approximate mortgage rate you would need to obtain when refinancing after a 2-year fix for the 2-year route to cost less than taking today's 5-year fix over the same five-year period.

The default illustration is:

- 75% LTV
- £200,000 repayment mortgage
- 25-year remaining term
- £999 2-year product fee
- £999 5-year product fee
- £999 refinancing fee after two years

The comparison uses mortgage interest plus fees rather than treating principal repayments as a cost.

The email then compares that break-even rate with a modelled refinancing-rate proxy derived from the 2-year-ahead forecast.

Change these values under `decision_assumptions` in `config.json` when you want the calculation to reflect a real mortgage decision.

## Trigger levels

Thresholds are held in `config.json` and can be changed without modifying Python.

| Indicator | Lower-rate signal | Higher-rate signal |
|---|---:|---:|
| Headline CPI | <= 2.50% | >= 3.50% |
| Core CPI | <= 2.30% | >= 3.00% |
| Services CPI | <= 3.00% | >= 3.80% |
| Private regular wage growth | <= 3.00% | >= 4.25% |
| Unemployment | >= 5.30% | <= 4.50% |
| 2Y SONIA swap | <= 4.00% | >= 5.00% |
| 5Y SONIA swap | <= 4.10% | >= 5.00% |
| 2Y gilt yield | <= 4.10% | >= 5.00% |
| 5Y gilt yield | <= 4.20% | >= 5.20% |
| 10Y gilt yield | <= 4.70% | >= 5.60% |

## When it emails

The repository checks twice each UK weekday at approximately:

- 08:37 Europe/London
- 18:37 Europe/London

It does not email on every run. An email is sent when one or more material conditions occur, including:

1. A trigger band changes.
2. A 2Y/5Y SONIA swap moves at least 0.15 percentage points cumulatively since the last alert.
3. A monitored gilt yield moves at least 0.20 percentage points cumulatively since the last alert.
4. The configured-LTV average mortgage rate moves at least 0.10 percentage points.
5. A forecast midpoint moves at least 0.15 percentage points.
6. Bank Rate changes by approximately one normal 0.25 percentage-point MPC step.
7. A live source fails and the model falls back to configured values.

Changes are measured from the **last emailed alert anchor**, not merely the previous run, so several small moves accumulate until they become material.

## Email setup

Required GitHub Actions secrets:

- `EMAIL_FROM`
- `EMAIL_TO`
- `EMAIL_APP_PASSWORD`

## Testing

1. Open **Actions**.
2. Select **UK Mortgage Rate Trigger Monitor**.
3. Select **Run workflow**.
4. Leave **Send a test/baseline email** enabled.
5. Run on `main`.

The resulting email should contain:

- current mortgage-rate table by LTV
- 6m / 12m / 2y forecast
- 2Y-vs-5Y break-even calculation
- SONIA swaps
- gilt yields
- inflation/labour-market dashboard
- explanation of what changed

## Files

- `monitor.py` — collection, forecasting, trigger and decision logic
- `config.json` — thresholds, weights, baseline data and decision assumptions
- `state.json` — current observations and last emailed alert anchor
- `.github/workflows/mortgage-monitor.yml` — schedule and manual testing

## Calibration

Major recalibration updated **1 October 2026** to add current mortgage-market pricing and 2Y/5Y/10Y gilt-yield signals.

This is a monitoring and scenario model. Long-horizon interest-rate forecasts are inherently uncertain and should be used as a structured decision aid rather than treated as a guaranteed future mortgage quote.
