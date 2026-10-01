# UK Mortgage Outlook & Decision Monitor

Automated UK mortgage decision-support monitor for a **60% LTV** borrower.

## Purpose

The monitor combines current mortgage pricing, macro data, SONIA swaps and official Bank of England gilt yields to estimate the direction of UK mortgage pricing and compare realistic mortgage strategies.

It is designed to answer:

1. What are mortgage rates doing now?
2. What are 2Y/5Y fixes and trackers likely to cost in 6 months, 12 months and 2 years?
3. Is it better, under the model, to fix now or use a flexible tracker and wait?
4. If waiting, is a 2Y, 3Y or 5Y fix the better destination?
5. What future fixed rate is actually required for waiting to beat fixing today?
6. How does a 10Y fix compare with fixing for 5 years and refinancing later?
7. How much does each strategy cost if rates turn out lower or higher than the central case?

## Current data inputs

### Economy
- Bank Rate
- headline CPI
- core CPI
- services CPI
- private regular wage growth
- unemployment

### Wholesale / bond markets
- 2Y SONIA swap
- 5Y SONIA swap
- 2Y gilt
- 5Y gilt
- 10Y gilt

Gilts are read from the **Bank of England nominal gilt spot curve**. SONIA swaps remain the main fixed-mortgage pricing signal.

### Mortgage products
The model tracks:
- competitive 2Y fix
- competitive 3Y fix
- competitive 5Y fix
- competitive 10Y fix
- cheapest tracker
- flexible/no-ERC tracker proxy

Market-average 2Y and 5Y rates are also tracked separately to calibrate the wider lender-pricing environment.

## 5-year tactical strategies

The v4 engine compares these over the same 60-month horizon using mortgage interest plus modelled product/refinance fees:

- 5Y fix now
- 3Y fix now → refinance
- 2Y fix now → refinance
- tracker 3m → 2Y fix
- tracker 3m → 3Y fix
- tracker 3m → 5Y fix
- tracker 6m → 2Y fix
- tracker 6m → 3Y fix
- tracker 6m → 5Y fix
- tracker 12m → 2Y fix
- tracker 12m → 3Y fix
- tracker 12m → 5Y fix
- tracker throughout 5 years

A configurable **tracker-until-trigger** strategy is also added when the central model reaches the selected target fixed rate. The default trigger is a competitive 5Y fix of **4.50%**, with a maximum wait of 24 months.

## Scenario stress test

Each tactical strategy is recalculated under:

- **Lower-rate:** future fixed and Bank Rate paths -0.75 percentage points
- **Base:** central model
- **Higher-rate:** future fixed and Bank Rate paths +1.00 percentage point

A mortgage fixed today is not repriced by the scenario. Only future refinancing and tracker legs change.

This exposes both expected cost and the cost of being wrong.

## Tracker break-even analysis

For every 3m/6m/12m waiting period and for each 2Y/3Y/5Y destination, the monitor calculates:

- modelled future fix rate
- break-even future fix rate
- headroom between them

Positive headroom means the central model expects the future fix to be sufficiently cheap for waiting to beat a 5Y fix today.

## 10-year certainty analysis

This is deliberately separate from the 5-year tactical ranking.

It compares:

- 10Y fix now
- 5Y fix now → another 5Y fix in year five

The model also calculates the second 5Y rate required for the 5+5 route to break even with the 10Y fix.

Because a year-five mortgage rate cannot be forecast precisely from today's curve, the central year-five 5Y rate is an explicit planning assumption in `config.json` and is stress-tested +/-1.00 percentage point.

## Assumptions

Default decision case:

- 60% LTV
- £200,000 repayment mortgage
- 25 years remaining
- tracker waiting windows: 3, 6 and 12 months
- future refinance/fix fee: £1,495
- flexible tracker exit cost: £0

Edit `decision_assumptions`, `scenario_shifts` and `long_horizon_assumptions` in `config.json` when a real mortgage decision approaches.

## Schedule

Runs approximately:

- 08:37 UK time, Monday-Friday
- 18:37 UK time, Monday-Friday

Emails are triggered by material changes in swaps, gilts, mortgage products, forecasts, Bank Rate or macro trigger bands. A manual test can always be sent from GitHub Actions.

## Files

- `monitor_v4.py` — active strategy and decision engine
- `monitor_v3.py` — previous engine retained as a base module and reference
- `config.json` — model weights, scenarios, product fallbacks and decision assumptions
- `state.json` — latest readings and alert anchor
- `.github/workflows/mortgage-monitor.yml` — schedule and execution

This is a structured scenario model, not a guarantee of future rates or regulated mortgage advice. Product eligibility, ERCs, legal/valuation costs, cashback, broker costs and lender criteria can materially change the real-world result.
