import json
import os
from datetime import datetime, timezone

import monitor_v3 as base
from bs4 import BeautifulSoup

CONFIG_PATH = base.CONFIG_PATH
STATE_PATH = base.STATE_PATH

SCENARIOS = ("lower", "base", "higher")


def hoa_product(heading_phrase, ltv, fallback):
    """Read a remortgage product from the HomeOwners Alliance/MAB tables."""
    out = dict(fallback)
    out.setdefault("source", "config fallback")
    try:
        soup = BeautifulSoup(base.get("https://hoa.org.uk/best-mortgage-rates/").text, "html.parser")
        heading = None
        for tag in soup.find_all(["h2", "h3", "h4"]):
            if heading_phrase.lower() in tag.get_text(" ", strip=True).lower():
                heading = tag
                break
        table = heading.find_next("table") if heading else None
        if table:
            for tr in table.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
                if len(cells) < 3:
                    continue
                pct_vals = [base.percent_number(c) for c in cells]
                ltv_vals = [v for v in pct_vals if v is not None and 50 <= v <= 100]
                if not ltv_vals or max(ltv_vals) < ltv:
                    continue
                rate = base.percent_number(cells[1])
                fee = base.money_number(cells[2])
                if rate is None:
                    continue
                return {
                    "lender": cells[0],
                    "rate": float(rate),
                    "fee": float(fee or 0.0),
                    "source": "HomeOwners Alliance / MAB current remortgage table",
                }
    except Exception:
        pass
    return out


def extended_decision_products(cfg):
    """2Y, 3Y, 5Y, 10Y fixes plus the cheapest tracker."""
    ltv = int(cfg["decision_assumptions"]["ltv"])
    out = base.which_decision_products(cfg)
    fb = cfg["decision_product_fallback"]

    p3 = None
    try:
        soup = BeautifulSoup(
            base.get("https://www.which.co.uk/money/mortgages-and-property/mortgages/best-mortgage-rates-and-deals-aLbQB2O2lDAz").text,
            "html.parser",
        )
        p3 = base._which_table_product(
            soup,
            "best three-year fixed rate mortgages for remortgaging",
            ltv,
        )
    except Exception:
        pass
    out["fixed_3y"] = p3 or dict(fb["fixed_3y"])

    out["fixed_10y"] = hoa_product(
        "best 10 year fixed rate mortgage",
        ltv,
        fb["fixed_10y"],
    )
    return out


def future_fixed_rate(months, term, forecast, mortgage_market, products, cfg, scenario_shift=0.0):
    """Competitive future fixed-rate proxy for 2Y/3Y/5Y products."""
    avg2, avg5 = base.chosen_average_rates(mortgage_market, cfg)
    p2 = float(products["fixed_2y"]["rate"])
    p3 = float(products["fixed_3y"]["rate"])
    p5 = float(products["fixed_5y"]["rate"])

    if term == "2y":
        rate = base.fixed_forecast_for_month(months, "2y", forecast, avg2, p2)
    elif term == "5y":
        rate = base.fixed_forecast_for_month(months, "5y", forecast, avg5, p5)
    elif term == "3y":
        f2 = base.fixed_forecast_for_month(months, "2y", forecast, avg2, p2)
        f5 = base.fixed_forecast_for_month(months, "5y", forecast, avg5, p5)
        current_proxy = (avg2 + avg5) / 2.0
        spread3 = p3 - current_proxy
        rate = (f2 + f5) / 2.0 + spread3
    else:
        raise ValueError(f"Unsupported fixed term {term}")
    return round(max(0.0, rate + scenario_shift), 2)


def shifted_bank_forecast(bank_fc, shift):
    return {k: max(0.0, float(v) + shift) for k, v in bank_fc.items()}


def tracker_path(months, bank_now, bank_fc, margin, scenario_bank_shift=0.0):
    shifted = shifted_bank_forecast(bank_fc, scenario_bank_shift)
    return base.tracker_monthly_path(months, bank_now, shifted, margin)


def add_fixed_segment(balance, term_months, elapsed, months, rate):
    interest, new_balance = base.monthly_interest_and_balance(
        balance, rate, months, max(1, term_months - elapsed)
    )
    return interest, new_balance, elapsed + months


def tail_term_for_months(months):
    return "2y" if months <= 24 else "3y"


def strategy_fix_now(term, scenario, balance, term_months, mortgage_market, products, forecast, cfg):
    months = {"2y": 24, "3y": 36, "5y": 60}[term]
    p = products[f"fixed_{term}"]
    shift = float(cfg["scenario_shifts"][scenario]["fixed_pp"])
    interest1, bal, elapsed = add_fixed_segment(balance, term_months, 0, min(months, 60), float(p["rate"]))
    fees = float(p["fee"])
    if elapsed < 60:
        remaining = 60 - elapsed
        tail_term = tail_term_for_months(remaining)
        tail_rate = future_fixed_rate(elapsed, tail_term, forecast, mortgage_market, products, cfg, shift)
        interest2, bal, elapsed = add_fixed_segment(bal, term_months, elapsed, remaining, tail_rate)
        fees += float(cfg["decision_assumptions"]["refinance_fee"])
        return interest1 + interest2 + fees
    return interest1 + fees


def strategy_tracker_then_fix(wait, term, scenario, balance, term_months, bank_now, bank_fc,
                              flex, mortgage_market, products, forecast, cfg):
    s = cfg["scenario_shifts"][scenario]
    trates = tracker_path(
        wait, bank_now, bank_fc, float(flex["margin"]), float(s["bank_pp"])
    )
    tracker_interest, bal = base.simulate_variable(balance, term_months, trates)
    elapsed = wait
    cost = tracker_interest + float(flex["fee"]) + float(cfg["decision_assumptions"].get("tracker_exit_cost", 0.0))

    remaining = 60 - elapsed
    if remaining <= 0:
        return cost

    fixed_months = min({"2y": 24, "3y": 36, "5y": 60}[term], remaining)
    first_rate = future_fixed_rate(wait, term, forecast, mortgage_market, products, cfg, float(s["fixed_pp"]))
    interest, bal, elapsed = add_fixed_segment(bal, term_months, elapsed, fixed_months, first_rate)
    cost += interest + float(cfg["decision_assumptions"]["future_fix_fee"])

    if elapsed < 60:
        remaining = 60 - elapsed
        tail_term = tail_term_for_months(remaining)
        tail_rate = future_fixed_rate(elapsed, tail_term, forecast, mortgage_market, products, cfg, float(s["fixed_pp"]))
        interest, bal, elapsed = add_fixed_segment(bal, term_months, elapsed, remaining, tail_rate)
        cost += interest + float(cfg["decision_assumptions"]["refinance_fee"])
    return cost


def strategy_tracker_full(scenario, balance, term_months, bank_now, bank_fc, flex, cfg):
    s = cfg["scenario_shifts"][scenario]
    rates = tracker_path(60, bank_now, bank_fc, float(flex["margin"]), float(s["bank_pp"]))
    interest, _ = base.simulate_variable(balance, term_months, rates)
    return interest + float(flex["fee"])


def threshold_wait_month(target, max_wait, mortgage_market, products, forecast, cfg, scenario="base"):
    shift = float(cfg["scenario_shifts"][scenario]["fixed_pp"])
    for month in range(1, max_wait + 1):
        if future_fixed_rate(month, "5y", forecast, mortgage_market, products, cfg, shift) <= target:
            return month
    return None


def tactical_decision_view(mortgage_market, products, flex, forecast, bank_fc, bank_now, cfg):
    a = cfg["decision_assumptions"]
    balance = float(a["loan_amount"])
    term_months = int(a["term_years"]) * 12
    waits = [int(x) for x in a["tracker_wait_months"]]

    specs = [
        ("5Y fix now", lambda sc: strategy_fix_now("5y", sc, balance, term_months, mortgage_market, products, forecast, cfg)),
        ("3Y fix now → refinance", lambda sc: strategy_fix_now("3y", sc, balance, term_months, mortgage_market, products, forecast, cfg)),
        ("2Y fix now → refinance", lambda sc: strategy_fix_now("2y", sc, balance, term_months, mortgage_market, products, forecast, cfg)),
    ]
    for wait in waits:
        for term in ("2y", "3y", "5y"):
            label = f"Tracker {wait}m → {term.upper()} fix"
            specs.append((
                label,
                lambda sc, w=wait, t=term: strategy_tracker_then_fix(
                    w, t, sc, balance, term_months, bank_now, bank_fc,
                    flex, mortgage_market, products, forecast, cfg
                ),
            ))
    specs.append((
        "Tracker throughout 5Y",
        lambda sc: strategy_tracker_full(sc, balance, term_months, bank_now, bank_fc, flex, cfg),
    ))

    target = float(a.get("fix_trigger_5y_rate", 4.50))
    max_wait = int(a.get("fix_trigger_max_wait_months", 24))
    trigger_month = threshold_wait_month(target, max_wait, mortgage_market, products, forecast, cfg, "base")
    if trigger_month:
        specs.append((
            f"Tracker until 5Y ≤ {target:.2f}% (model month {trigger_month})",
            lambda sc, w=trigger_month: strategy_tracker_then_fix(
                w, "5y", sc, balance, term_months, bank_now, bank_fc,
                flex, mortgage_market, products, forecast, cfg
            ),
        ))

    rows = []
    for name, fn in specs:
        costs = {sc: round(fn(sc), 0) for sc in SCENARIOS}
        rows.append({
            "name": name,
            "lower_cost": costs["lower"],
            "base_cost": costs["base"],
            "higher_cost": costs["higher"],
            "stress_range": round(costs["higher"] - costs["lower"], 0),
        })

    best = min(x["base_cost"] for x in rows)
    for x in rows:
        x["difference_vs_base_lowest"] = round(x["base_cost"] - best, 0)

    p2, p5 = products["fixed_2y"], products["fixed_5y"]
    cost5 = strategy_fix_now("5y", "base", balance, term_months, mortgage_market, products, forecast, cfg)
    i2, bal2 = base.monthly_interest_and_balance(balance, float(p2["rate"]), 24, term_months)
    def two_then(rate):
        i3, _ = base.monthly_interest_and_balance(bal2, rate, 36, term_months - 24)
        return i2 + i3 + float(p2["fee"]) + float(a["refinance_fee"])
    lo, hi = 0.0, 15.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if two_then(mid) < cost5:
            lo = mid
        else:
            hi = mid
    break_even_2v5 = round((lo + hi) / 2.0, 2)

    model_refi = future_fixed_rate(24, "3y", forecast, mortgage_market, products, cfg, 0.0)

    wait_break_evens = []
    for wait in waits:
        trates = tracker_path(wait, bank_now, bank_fc, float(flex["margin"]), 0.0)
        tracker_interest, bal = base.simulate_variable(balance, term_months, trates)
        for term in ("2y", "3y", "5y"):
            remaining = 60 - wait
            product_months = min({"2y": 24, "3y": 36, "5y": 60}[term], remaining)
            future_fee = float(a["future_fix_fee"])
            exit_cost = float(a.get("tracker_exit_cost", 0.0))

            def total_at_rate(rate):
                first_i, bal_after = base.monthly_interest_and_balance(
                    bal, rate, product_months, term_months - wait
                )
                total = tracker_interest + first_i + float(flex["fee"]) + future_fee + exit_cost
                elapsed = wait + product_months
                if elapsed < 60:
                    rem = 60 - elapsed
                    tail = future_fixed_rate(elapsed, tail_term_for_months(rem), forecast, mortgage_market, products, cfg, 0.0)
                    tail_i, _ = base.monthly_interest_and_balance(
                        bal_after, tail, rem, term_months - elapsed
                    )
                    total += tail_i + float(a["refinance_fee"])
                return total

            lo, hi = 0.0, 15.0
            for _ in range(60):
                mid = (lo + hi) / 2.0
                if total_at_rate(mid) < cost5:
                    lo = mid
                else:
                    hi = mid
            be = round((lo + hi) / 2.0, 2)
            model_rate = future_fixed_rate(wait, term, forecast, mortgage_market, products, cfg, 0.0)
            wait_break_evens.append({
                "wait_months": wait,
                "fix_term": term,
                "model_fix_rate": model_rate,
                "break_even_fix_rate": be,
                "headroom_pp": round(be - model_rate, 2),
            })

    return {
        "strategies": rows,
        "break_even_refi_rate_2v5": break_even_2v5,
        "model_refi_proxy_2y": model_refi,
        "wait_break_evens": wait_break_evens,
        "trigger_target": target,
        "trigger_month": trigger_month,
    }


def ten_year_view(products, mortgage_market, forecast, cfg):
    """10Y certainty analysis kept separate from the 5Y tactical ranking."""
    a = cfg["decision_assumptions"]
    balance = float(a["loan_amount"])
    term_months = int(a["term_years"]) * 12
    p5 = products["fixed_5y"]
    p10 = products["fixed_10y"]

    i10, _ = base.monthly_interest_and_balance(balance, float(p10["rate"]), 120, term_months)
    cost10 = i10 + float(p10["fee"])

    i5, bal5 = base.monthly_interest_and_balance(balance, float(p5["rate"]), 60, term_months)
    first_cost = i5 + float(p5["fee"])

    long_cfg = cfg["long_horizon_assumptions"]
    central_future5 = float(long_cfg["year5_5y_fix"])
    lower_future5 = max(0.0, central_future5 + float(long_cfg["lower_shift_pp"]))
    higher_future5 = max(0.0, central_future5 + float(long_cfg["higher_shift_pp"]))

    def cost_5_plus_5(rate):
        i2, _ = base.monthly_interest_and_balance(bal5, rate, 60, term_months - 60)
        return first_cost + i2 + float(a["future_fix_fee"])

    lo, hi = 0.0, 15.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if cost_5_plus_5(mid) < cost10:
            lo = mid
        else:
            hi = mid
    be = round((lo + hi) / 2.0, 2)

    return {
        "fixed_10y": p10,
        "fixed_5y": p5,
        "cost_10y": round(cost10, 0),
        "cost_5plus5_lower": round(cost_5_plus_5(lower_future5), 0),
        "cost_5plus5_base": round(cost_5_plus_5(central_future5), 0),
        "cost_5plus5_higher": round(cost_5_plus_5(higher_future5), 0),
        "future_5y_proxy": central_future5,
        "break_even_second_5y_rate": be,
        "note": "The year-5 mortgage rate is a long-horizon planning assumption, not a precise forecast.",
    }


def extended_email_html(values, bands, mortgage_market, forecast, bank_fc, products, flex,
                        tactical, long10, why, pressure, cfg):
    market_rows = ""
    for ltv in ("60", "75", "90"):
        if ltv in mortgage_market["rates"]:
            x = mortgage_market["rates"][ltv]
            market_rows += f"<tr><td>{ltv}%</td><td>{base.fmt(x['2y'])}</td><td>{base.fmt(x['5y'])}</td></tr>"

    product_rows = ""
    for key, label in (
        ("fixed_2y", "2Y fix"),
        ("fixed_3y", "3Y fix"),
        ("fixed_5y", "5Y fix"),
        ("fixed_10y", "10Y fix"),
        ("tracker_cheapest", "Cheapest tracker"),
    ):
        p = products[key]
        product_rows += (
            f"<tr><td>{label}</td><td>{base.fmt(p['rate'])}</td>"
            f"<td>{base.fmt_money(p['fee'])}</td><td>{p['lender']}</td></tr>"
        )
    product_rows += (
        f"<tr><td>Flexible tracker proxy</td><td>{base.fmt(flex['rate'])}</td>"
        f"<td>{base.fmt_money(flex['fee'])}</td><td>{flex['lender']} — no-ERC model route</td></tr>"
    )

    fc_rows = ""
    for h, x in forecast.items():
        tracker_mid = bank_fc[h] + float(flex["margin"])
        fc_rows += (
            f"<tr><td>{h}</td><td>{base.fmt(bank_fc[h])}</td>"
            f"<td>{base.fmt(x['mortgage_2y_mid'])}</td>"
            f"<td>{base.fmt(x['mortgage_5y_mid'])}</td>"
            f"<td>{base.fmt(tracker_mid)}</td></tr>"
        )

    tactical_rows = "".join(
        f"<tr><td>{x['name']}</td><td>{base.fmt_money(x['lower_cost'])}</td>"
        f"<td><b>{base.fmt_money(x['base_cost'])}</b></td>"
        f"<td>{base.fmt_money(x['higher_cost'])}</td>"
        f"<td>{base.fmt_money(x['difference_vs_base_lowest'])}</td></tr>"
        for x in tactical["strategies"]
    )

    be_rows = "".join(
        f"<tr><td>{x['wait_months']}m</td><td>{x['fix_term'].upper()}</td>"
        f"<td>{base.fmt(x['model_fix_rate'])}</td>"
        f"<td>{base.fmt(x['break_even_fix_rate'])}</td>"
        f"<td>{x['headroom_pp']:+.2f}pp</td></tr>"
        for x in tactical["wait_break_evens"]
    )

    ordered = [
        ("bank_rate", "Bank Rate"), ("headline_cpi", "Headline CPI"),
        ("core_cpi", "Core CPI"), ("services_cpi", "Services CPI"),
        ("wage_growth", "Private regular wage growth"), ("unemployment", "Unemployment"),
        ("swap_2y", "2Y SONIA swap"), ("swap_5y", "5Y SONIA swap"),
        ("gilt_2y", "2Y gilt yield"), ("gilt_5y", "5Y gilt yield"),
        ("gilt_10y", "10Y gilt yield"),
    ]
    econ_rows = "".join(
        f"<tr><td>{label}</td><td>{base.fmt(values[key]['value'])}</td>"
        f"<td>{bands.get(key, 'TRACK')}</td><td>{values[key]['period']}</td>"
        f"<td>{values[key]['source']}</td></tr>"
        for key, label in ordered if key in values
    )
    why_html = "".join(f"<li>{r}</li>" for r in why) or "<li>Manual/test report</li>"
    signal = "higher-rate pressure" if pressure > 0.15 else "lower-rate pressure" if pressure < -0.15 else "broadly neutral"

    trigger_text = (
        f"Model reaches the configured {tactical['trigger_target']:.2f}% 5Y-fix trigger around month {tactical['trigger_month']}."
        if tactical["trigger_month"]
        else f"Model does not reach the configured {tactical['trigger_target']:.2f}% 5Y-fix trigger within 24 months."
    )

    return f"""<html><body>
    <h2>UK Mortgage Outlook & Decision Monitor v4</h2>
    <p><b>Macro/market pressure:</b> {signal} ({pressure:+.2f})</p>

    <h3>Current market averages</h3>
    <table border="1" cellpadding="6" cellspacing="0"><tr><th>LTV</th><th>Average 2Y</th><th>Average 5Y</th></tr>{market_rows}</table>

    <h3>Competitive {cfg['decision_assumptions']['ltv']}% LTV products</h3>
    <table border="1" cellpadding="6" cellspacing="0"><tr><th>Product</th><th>Rate</th><th>Fee</th><th>Lender / note</th></tr>{product_rows}</table>

    <h3>Forward outlook</h3>
    <table border="1" cellpadding="6" cellspacing="0"><tr><th>Horizon</th><th>Bank Rate model</th><th>Typical 2Y fix</th><th>Typical 5Y fix</th><th>Flexible tracker</th></tr>{fc_rows}</table>

    <h3>5-year tactical strategies — scenario stress test</h3>
    <p>All routes are compared over the same 60 months using mortgage interest + modelled fees. Lower/base/higher columns stress future rates; a fix already taken today is not repriced retrospectively.</p>
    <table border="1" cellpadding="6" cellspacing="0"><tr><th>Strategy</th><th>Lower-rate scenario</th><th>Base</th><th>Higher-rate scenario</th><th>Above lowest base</th></tr>{tactical_rows}</table>

    <h3>Tracker → fix break-even table</h3>
    <p>Positive headroom means the modelled future fix is below the rate required for waiting to beat a 5Y fix today.</p>
    <table border="1" cellpadding="6" cellspacing="0"><tr><th>Wait</th><th>Then</th><th>Modelled fix</th><th>Break-even fix</th><th>Headroom</th></tr>{be_rows}</table>
    <p><b>Trigger strategy:</b> {trigger_text}</p>

    <h3>2Y vs 5Y now</h3>
    <p>Approximate refinance break-even after the 2Y fix: <b>{base.fmt(tactical['break_even_refi_rate_2v5'])}</b>. Modelled 3Y-equivalent refinance proxy in two years: <b>{base.fmt(tactical['model_refi_proxy_2y'])}</b>.</p>

    <h3>10-year certainty analysis</h3>
    <p>Current 10Y fix: <b>{base.fmt(long10['fixed_10y']['rate'])}</b> ({long10['fixed_10y']['lender']}); current 5Y fix: <b>{base.fmt(long10['fixed_5y']['rate'])}</b>.</p>
    <table border="1" cellpadding="6" cellspacing="0">
      <tr><th>10-year route</th><th>Lower future-rate case</th><th>Base</th><th>Higher future-rate case</th></tr>
      <tr><td>10Y fix now</td><td>{base.fmt_money(long10['cost_10y'])}</td><td>{base.fmt_money(long10['cost_10y'])}</td><td>{base.fmt_money(long10['cost_10y'])}</td></tr>
      <tr><td>5Y fix now → another 5Y</td><td>{base.fmt_money(long10['cost_5plus5_lower'])}</td><td>{base.fmt_money(long10['cost_5plus5_base'])}</td><td>{base.fmt_money(long10['cost_5plus5_higher'])}</td></tr>
    </table>
    <p>The second 5Y rate in year five would need to be about <b>{base.fmt(long10['break_even_second_5y_rate'])}</b> for the two routes to cost the same. Central year-5 planning proxy: <b>{base.fmt(long10['future_5y_proxy'])}</b>. {long10['note']}</p>

    <h3>What changed</h3><ul>{why_html}</ul>
    <h3>Market & economic dashboard</h3>
    <table border="1" cellpadding="6" cellspacing="0"><tr><th>Indicator</th><th>Latest</th><th>Band</th><th>Period</th><th>Source</th></tr>{econ_rows}</table>
    <p><small>This is a structured scenario model, not a guarantee of future rates or regulated mortgage advice. Product eligibility, ERCs, legal/valuation costs, cashback and lender criteria can materially change the real-world outcome.</small></p>
    </body></html>"""


def main():
    cfg = json.loads(CONFIG_PATH.read_text())
    prev = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    values = {}

    series = {
        "headline_cpi": "/economy/inflationandpriceindices/timeseries/d7g7/mm23",
        "core_cpi": "/economy/inflationandpriceindices/timeseries/dko8/mm23",
        "services_cpi": "/economy/inflationandpriceindices/timeseries/d7nn/mm23",
        "wage_growth": "/employmentandlabourmarket/peopleinwork/earningsandworkinghours/timeseries/kaj4/lms",
        "unemployment": "/employmentandlabourmarket/peoplenotinwork/unemployment/timeseries/mgsx/lms",
    }
    for key, path in series.items():
        try:
            obs = base.latest_ons(path)
        except Exception:
            obs = base.Obs(cfg["baseline"][key], "fallback", "config fallback")
        values[key] = obs.__dict__

    values["bank_rate"] = base.bank_rate(cfg["baseline"]["bank_rate"]).__dict__

    try:
        swap_obs = base.swaps(cfg["baseline"]["swap_2y"], cfg["baseline"]["swap_5y"])
        values["swap_2y"] = swap_obs["2 Year"].__dict__
        values["swap_5y"] = swap_obs["5 Year"].__dict__
    except Exception:
        values["swap_2y"] = base.Obs(cfg["baseline"]["swap_2y"], "fallback", "config fallback").__dict__
        values["swap_5y"] = base.Obs(cfg["baseline"]["swap_5y"], "fallback", "config fallback").__dict__

    for key, obs in base.boe_gilt_yields(cfg).items():
        values[key] = obs.__dict__

    mortgage_market = base.rightmove_average_rates(cfg)
    products = extended_decision_products(cfg)
    flex = base.flexible_tracker(cfg, values["bank_rate"]["value"])

    pressure, bands = base.pressure_index(values, cfg)
    forecast = base.make_forecast(values, mortgage_market, pressure, cfg)
    bank_fc = base.bank_rate_forecast(values, pressure, cfg)

    tactical = tactical_decision_view(
        mortgage_market, products, flex, forecast, bank_fc,
        values["bank_rate"]["value"], cfg
    )
    long10 = ten_year_view(products, mortgage_market, forecast, cfg)

    why = base.reasons(
        values, bands, mortgage_market, products, flex, prev, forecast, bank_fc, cfg
    )
    old_products = ((prev or {}).get("alert_anchor") or {}).get("decision_products", {})
    for key, label in (("fixed_3y", "competitive 3Y fix"), ("fixed_10y", "competitive 10Y fix")):
        try:
            if base.changed_enough(
                products[key]["rate"], old_products[key]["rate"],
                cfg["alert_rules"]["mortgage_move_pp"]
            ):
                why.append(f"{label} moved {products[key]['rate'] - old_products[key]['rate']:+.2f}pp since last alert")
        except Exception:
            if key not in old_products:
                why.append(f"New strategy input added: {label}")

    why = list(dict.fromkeys(why))
    test = os.environ.get("TEST_EMAIL", "").lower() in {"1", "true", "yes"}
    first = not prev.get("alert_anchor")
    should_send = test or first or bool(why)
    now = datetime.now(timezone.utc).isoformat()

    state = {
        "last_checked": now,
        "model_version": 4,
        "latest_values": values,
        "mortgage_market": mortgage_market,
        "decision_products": products,
        "flexible_tracker": flex,
        "latest_bands": bands,
        "latest_pressure_index": round(pressure, 4),
        "latest_forecast": forecast,
        "latest_bank_rate_forecast": bank_fc,
        "latest_tactical_view": tactical,
        "latest_ten_year_view": long10,
        "alert_anchor": prev.get("alert_anchor"),
    }

    if should_send:
        prefix = "TEST — " if test else ""
        subject = f"{prefix}UK Mortgage Outlook v4"
        base.send_email(
            subject,
            extended_email_html(
                values, bands, mortgage_market, forecast, bank_fc, products,
                flex, tactical, long10, why, pressure, cfg
            ),
        )
        state["alert_anchor"] = {
            "values": values,
            "mortgage_market": mortgage_market,
            "decision_products": products,
            "flexible_tracker": flex,
            "bands": bands,
            "forecast": forecast,
            "bank_rate_forecast": bank_fc,
            "tactical_view": tactical,
            "ten_year_view": long10,
            "sent_at": now,
        }

    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


if __name__ == "__main__":
    main()
