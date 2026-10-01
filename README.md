# UK Mortgage Outlook & Decision Monitor

Automated UK mortgage decision-support monitor for a 60% LTV borrower. It is designed to answer:

1. What are UK mortgage rates doing now?
2. What are 2-year and 5-year fixes likely to cost in 6 months, 12 months and 2 years?
3. Is fixing for 2 years or 5 years currently more attractive on the model?
4. Could using a flexible tracker for 3, 6 or 12 months before fixing improve the expected outcome?

## Data monitored

### Economy
- Bank of England Bank Rate
- Headline CPI
- Core CPI
- Services CPI
- Private-sector regular wage growth
- UK unemployment

### Wholesale and bond markets
- 2-year SONIA swap
- 5-year SONIA swap
- 2-year gilt yield
- 5-year gilt yield
- 10-year gilt yield

SONIA swaps remain the primary fixed-mortgage pricing signal. Gilt yields are deliberately lower-weight supplementary signals for term premium, fiscal risk and global bond-market stress.

The gilt feed now uses the Bank of England's official daily nominal gilt spot-curve workbook. Configured fallback values are only used if that source cannot be read.

### Mortgage market
Two different mortgage datasets are used for different jobs:

**Market averages**
- Rightmove / Podium 2-year and 5-year averages by LTV
- used to calibrate the overall mortgage market and lender-margin environment

**Competitive products used in strategy comparisons**
- competitive 60% LTV 2-year remortgage fix
- competitive 60% LTV 5-year remortgage fix
- cheapest tracked 60% LTV tracker
- separate flexible/no-ERC tracker proxy for the wait-before-fixing model

The model keeps the cheapest tracker and the flexible tracker separate because the absolute cheapest tracker may have an early repayment charge and therefore may be unsuitable for switching into a fix after only a few months.

## Forecast

The email shows model ranges for:

- Bank Rate
- typical 2-year fixes
- typical 5-year fixes
- a flexible tracker proxy

at:

- 6 months
- 12 months
- 2 years

The fixed-rate forecast combines inflation/labour data, SONIA swaps, gilt yields and changes in actual mortgage pricing. The tracker projection is based mainly on the modelled Bank Rate path plus the current tracker margin.

## Decision engine

The model compares strategies over a common five-year period using **mortgage interest plus relevant product/refinance fees**. Principal repayments are not treated as a cost.

Current default assumptions in `config.json`:

- 60% LTV
- £200,000 repayment mortgage
- 25-year remaining term
- future refinance/fix fee: £1,495
- tracker exit cost: £0 for the flexible tracker proxy
- tracker waiting periods: 3, 6 and 12 months

### Strategies shown

1. **5-year fix now**
2. **2-year fix now, then modelled refinance after two years**
3. **Flexible tracker for 3 months, then modelled 5-year fix**
4. **Flexible tracker for 6 months, then modelled 5-year fix**
5. **Flexible tracker for 12 months, then modelled 5-year fix**

For every tracker-wait period the email shows:

- projected tracker rate at the point of switching
- modelled 5-year fixed rate at that point
- the **break-even future 5-year fixed rate** that would be required for waiting to beat fixing for five years today
- expected five-year interest + fees
- a plain-English model signal

This makes the key question explicit. For example, if waiting six months only beats today's 5-year fix when a 5-year deal falls below 4.5%, but the model expects 4.9%, the monitor will show that the wait strategy does not currently clear its break-even hurdle.

## 2-year vs 5-year break-even

The email also calculates the refinancing rate needed in two years for the 2-year-fix strategy to beat taking today's 5-year fix over the same five-year horizon, including fees.

## Alerts

The workflow checks at approximately:

- 08:37 Europe/London, Monday-Friday
- 18:37 Europe/London, Monday-Friday

It emails when a material change occurs, including:

- an economic/market trigger changes band
- 2Y/5Y SONIA swaps move >= 0.15 percentage points cumulatively since the last alert
- gilt yields move >= 0.20 percentage points
- a monitored mortgage/product rate moves >= 0.10 percentage points
- a forecast midpoint moves >= 0.15 percentage points
- Bank Rate changes materially
- a data source fails and the model has to use a fallback

Movements are compared with the **last emailed alert anchor**, so a series of smaller changes can accumulate into a material alert.

## Testing

1. Open **Actions**.
2. Select **UK Mortgage Outlook & Decision Monitor**.
3. Select **Run workflow**.
4. Leave **Send a test/baseline email** enabled.
5. Run on `main`.

The email should contain current mortgage pricing, fixed/tracker product inputs, the 6m/12m/2y outlook, five-year strategy-cost comparisons, tracker-wait break-even tables and the economic/market dashboard.

## Files

- `monitor_v3.py` — live data, forecasting, tracker/fixed strategy simulation and email generation
- `monitor.py` — previous v2 engine retained for reference; not used by the scheduled workflow
- `config.json` — thresholds, weights, fallbacks and decision assumptions
- `state.json` — latest observations and last emailed alert anchor
- `.github/workflows/mortgage-monitor.yml` — schedule and manual testing

## Important limitations

This is a structured scenario model, not regulated mortgage advice and not a guarantee of future mortgage rates. Product eligibility, ERCs, valuation/legal costs, broker fees, cashback and lender-specific criteria can change the actual best decision. The flexible-tracker waiting strategy is only valid where the selected tracker can genuinely be exited at the intended time without a material ERC.

Major v3 upgrade: **1 October 2026**.
