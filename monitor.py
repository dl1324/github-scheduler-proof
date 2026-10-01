import json
import math
import os
import re
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import requests
from bs4 import BeautifulSoup

CONFIG_PATH = Path("config.json")
STATE_PATH = Path("state.json")
TIMEOUT = 25
UA = "uk-mortgage-rate-monitor/2.0 (+GitHub Actions)"


@dataclass
class Obs:
    value: float
    period: str
    source: str


def get(url):
    r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
    r.raise_for_status()
    return r


def pct_numbers(text):
    return [float(x) for x in re.findall(r"(-?\d+(?:\.\d+)?)\s*%", text)]


def latest_ons(path):
    data = get("https://www.ons.gov.uk" + path + "/data").json()
    for bucket in ("months", "quarters", "years"):
        rows = data.get(bucket) or []
        for row in reversed(rows):
            try:
                return Obs(float(str(row["value"]).replace(",", "")), row.get("date") or row.get("label") or "", "ONS")
            except Exception:
                pass
    raise RuntimeError(f"No numeric observation found for {path}")


def bank_rate(fallback):
    try:
        html = get("https://www.bankofengland.co.uk/boeapps/database/Bank-Rate.asp").text
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        m = re.search(r"(?:current\s+)?Bank\s+Rate.{0,80}?(\d+(?:\.\d+)?)\s*%", text, re.I)
        if not m:
            m = re.search(r"(\d+(?:\.\d+)?)\s*%[^%]{0,80}Bank\s+Rate", text, re.I)
        if m:
            return Obs(float(m.group(1)), datetime.now(timezone.utc).date().isoformat(), "Bank of England")
    except Exception:
        pass
    return Obs(float(fallback), "fallback", "config fallback")


def swaps(fallback2, fallback5):
    html = get("https://www.bluegamma.io/sonia-swap-rates-uk").text
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    out = {}
    for tenor, fallback in [("2 Year", fallback2), ("5 Year", fallback5)]:
        patterns = [
            rf"{re.escape(tenor)}\s+(?:Live\s+)?(?:\|\s*)?(\d+(?:\.\d+)?)\s*%",
            rf"{re.escape(tenor)}.{{0,90}}?(\d+(?:\.\d+)?)\s*%",
        ]
        m = None
        for p in patterns:
            m = re.search(p, text, re.I)
            if m:
                break
        out[tenor] = Obs(
            float(m.group(1)) if m else float(fallback),
            "previous UK business-day close",
            "BlueGamma public SONIA swap table" if m else "config fallback",
        )
    return out


def gilt_yield(years, fallback):
    url = f"https://uk.investing.com/rates-bonds/uk-{years}-year-bond-yield"
    try:
        text = BeautifulSoup(get(url).text, "html.parser").get_text(" ", strip=True)
        patterns = [
            rf"Current United Kingdom {years}-Year bond yield.*?price today is\s+(\d+(?:\.\d+)?)",
            rf"United Kingdom {years}-Year Bond Yield.{{0,600}}?(\d+(?:\.\d{{3,4}})?)",
        ]
        for p in patterns:
            m = re.search(p, text, re.I | re.S)
            if m:
                return Obs(float(m.group(1)), "latest market reading", "Investing.com UK gilt yield")
    except Exception:
        pass
    return Obs(float(fallback), "fallback", "config fallback")


def rightmove_mortgage_rates(cfg):
    fallback = cfg["mortgage_market_fallback"]
    result = {str(k): {"2y": float(v["2y"]), "5y": float(v["5y"])} for k, v in fallback.items()}
    source = "config fallback"
    period = "fallback"
    try:
        url = "https://www.rightmove.co.uk/news/articles/property-news/current-uk-mortgage-rates/"
        soup = BeautifulSoup(get(url).text, "html.parser")
        text = soup.get_text(" ", strip=True)
        mdate = re.search(r"Updated:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})", text)
        if mdate:
            period = mdate.group(1)
        found = {}
        for tr in soup.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if len(cells) < 3:
                continue
            ltv_match = re.fullmatch(r"(60|75|90|95)\s*%", cells[0])
            if not ltv_match:
                continue
            term_text = cells[1].lower().replace("–", "-")
            term = "2y" if "2-year" in term_text else "5y" if "5-year" in term_text else None
            if term is None:
                continue
            key = (ltv_match.group(1), term)
            if key in found:
                continue
            vals = []
            for cell in cells[2:]:
                vals.extend(pct_numbers(cell))
            if vals:
                current = vals[1] if len(vals) >= 2 else vals[0]
                if 2.0 <= current <= 10.0:
                    found[key] = current
        if len(found) >= 6:
            for (ltv, term), rate in found.items():
                if ltv in result:
                    result[ltv][term] = rate
            source = "Rightmove / Podium average home-buyer rates"
    except Exception:
        pass
    return {"rates": result, "period": period, "source": source}


def band(name, value, cfg):
    t = cfg["triggers"][name]
    if t.get("direction") == "inverse":
        if value >= t["lower_rate"]:
            return "LOWER"
        if value <= t["higher_rate"]:
            return "HIGHER"
    else:
        if value <= t["lower_rate"]:
            return "LOWER"
        if value >= t["higher_rate"]:
            return "HIGHER"
    return "NEUTRAL"


def pressure_index(values, cfg):
    total = 0.0
    denom = 0.0
    bands = {}
    for key, weight in cfg["weights"].items():
        if key not in values:
            continue
        b = band(key, values[key]["value"], cfg)
        bands[key] = b
        total += weight * (-1 if b == "LOWER" else 1 if b == "HIGHER" else 0)
        denom += weight
    return (total / denom if denom else 0.0), bands


def chosen_market_rates(mortgage_market, cfg):
    ltv = str(cfg["decision_assumptions"]["ltv"])
    row = mortgage_market["rates"].get(ltv)
    if row:
        return float(row["2y"]), float(row["5y"])
    fallback = cfg["mortgage_market_fallback"][ltv]
    return float(fallback["2y"]), float(fallback["5y"])


def make_forecast(values, mortgage_market, pressure, cfg):
    b = cfg["baseline"]
    d2 = values["swap_2y"]["value"] - b["swap_2y"]
    d5 = values["swap_5y"]["value"] - b["swap_5y"]
    gilt_delta = (
        0.25 * (values["gilt_2y"]["value"] - b["gilt_2y"])
        + 0.35 * (values["gilt_5y"]["value"] - b["gilt_5y"])
        + 0.40 * (values["gilt_10y"]["value"] - b["gilt_10y"])
    )
    current2, current5 = chosen_market_rates(mortgage_market, cfg)
    ltv = str(cfg["decision_assumptions"]["ltv"])
    base_mort = cfg["mortgage_market_fallback"][ltv]
    market_delta2 = current2 - float(base_mort["2y"])
    market_delta5 = current5 - float(base_mort["5y"])
    result = {}
    for horizon, hcfg in cfg["forecast_anchors"].items():
        shift2 = pressure * hcfg["pressure_sensitivity"] + d2 * hcfg["swap_sensitivity"] + gilt_delta * hcfg["gilt_sensitivity"] + market_delta2 * hcfg["current_market_sensitivity"]
        shift5 = pressure * hcfg["pressure_sensitivity"] + d5 * hcfg["swap_sensitivity"] + gilt_delta * hcfg["gilt_sensitivity"] + market_delta5 * hcfg["current_market_sensitivity"]
        mid2 = round(hcfg["mortgage_2y"] + shift2, 2)
        mid5 = round(hcfg["mortgage_5y"] + shift5, 2)
        width = hcfg["range_half_width"]
        result[horizon] = {
            "mortgage_2y_mid": mid2,
            "mortgage_2y_range": [round(mid2 - width, 2), round(mid2 + width, 2)],
            "mortgage_5y_mid": mid5,
            "mortgage_5y_range": [round(mid5 - width, 2), round(mid5 + width, 2)],
        }
    return result


def monthly_interest_and_balance(balance, annual_rate, months, remaining_term_months):
    monthly = annual_rate / 100.0 / 12.0
    payment = balance / remaining_term_months if monthly == 0 else balance * monthly / (1 - (1 + monthly) ** (-remaining_term_months))
    interest_total = 0.0
    bal = balance
    for _ in range(months):
        interest = bal * monthly
        principal = payment - interest
        bal = max(0.0, bal - principal)
        interest_total += interest
    return interest_total, bal


def break_even_refi_rate(current2, current5, cfg):
    a = cfg["decision_assumptions"]
    balance = float(a["loan_amount"])
    term_months = int(a["term_years"]) * 12
    interest5, _ = monthly_interest_and_balance(balance, current5, 60, term_months)
    cost5 = interest5 + float(a["five_year_fee"])
    interest2, balance2 = monthly_interest_and_balance(balance, current2, 24, term_months)
    def cost2_then(rate):
        interest3, _ = monthly_interest_and_balance(balance2, rate, 36, term_months - 24)
        return interest2 + interest3 + float(a["two_year_fee"]) + float(a["refinance_fee"])
    lo, hi = 0.0, 15.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if cost2_then(mid) < cost5:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 2)


def decision_view(mortgage_market, forecast, cfg):
    current2, current5 = chosen_market_rates(mortgage_market, cfg)
    be = break_even_refi_rate(current2, current5, cfg)
    f2 = forecast["2 years"]
    refi_proxy = round((f2["mortgage_2y_mid"] + f2["mortgage_5y_mid"]) / 2, 2)
    margin = round(be - refi_proxy, 2)
    if margin >= 0.25:
        signal = "Modelled future refinancing rates are below the 2Y-vs-5Y break-even."
    elif margin <= -0.25:
        signal = "Modelled future refinancing rates are above the 2Y-vs-5Y break-even."
    else:
        signal = "The model is close to the 2Y-vs-5Y break-even; certainty, fees and flexibility matter more."
    return {"ltv": int(cfg["decision_assumptions"]["ltv"]), "current_2y": round(current2, 2), "current_5y": round(current5, 2), "break_even_refi_rate": be, "model_refi_proxy": refi_proxy, "margin": margin, "signal": signal}


def fmt(v):
    return f"{v:.2f}%"


def changed_enough(a, b, threshold):
    try:
        return abs(float(a) - float(b)) >= threshold
    except Exception:
        return True


def reasons(values, bands, mortgage_market, prev, forecast, cfg):
    out = []
    anchor = (prev or {}).get("alert_anchor", {}) or {}
    old_bands = anchor.get("bands", {})
    old_vals = anchor.get("values", {})
    old_fc = anchor.get("forecast", {})
    old_market = anchor.get("mortgage_market", {})
    for key, b in bands.items():
        if old_bands and key not in old_bands:
            out.append(f"New trigger added: {cfg['labels'].get(key, key)} = {b}")
        elif old_bands and old_bands.get(key) != b:
            out.append(f"{cfg['labels'].get(key, key)} trigger moved {old_bands.get(key, '?')} → {b}")
    for key in ("swap_2y", "swap_5y"):
        if key in old_vals and changed_enough(values[key]["value"], old_vals[key]["value"], cfg["alert_rules"]["swap_move_pp"]):
            out.append(f"{cfg['labels'][key]} moved {values[key]['value'] - old_vals[key]['value']:+.2f}pp since last alert")
    for key in ("gilt_2y", "gilt_5y", "gilt_10y"):
        if key in old_vals and changed_enough(values[key]["value"], old_vals[key]["value"], cfg["alert_rules"]["gilt_move_pp"]):
            out.append(f"{cfg['labels'][key]} moved {values[key]['value'] - old_vals[key]['value']:+.2f}pp since last alert")
    if "bank_rate" in old_vals and changed_enough(values["bank_rate"]["value"], old_vals["bank_rate"]["value"], 0.24):
        out.append(f"Bank Rate changed from {old_vals['bank_rate']['value']:.2f}% to {values['bank_rate']['value']:.2f}%")
    ltv = str(cfg["decision_assumptions"]["ltv"])
    try:
        old_rates = old_market["rates"][ltv]
        new_rates = mortgage_market["rates"][ltv]
        for term in ("2y", "5y"):
            if changed_enough(new_rates[term], old_rates[term], cfg["alert_rules"]["mortgage_move_pp"]):
                out.append(f"{ltv}% LTV average {term.upper()} mortgage moved {new_rates[term] - old_rates[term]:+.2f}pp since last alert")
    except Exception:
        if mortgage_market.get("source") != "config fallback":
            out.append("Live mortgage pricing is now included in the model")
    for key, value in values.items():
        if "fallback" in value.get("source", "").lower():
            old_source = old_vals.get(key, {}).get("source", "")
            if "fallback" not in old_source.lower():
                out.append(f"Data-source warning: {cfg['labels'].get(key, key)} is using its configured fallback")
    if mortgage_market.get("source") == "config fallback":
        old_source = old_market.get("source", "")
        if "fallback" not in old_source.lower():
            out.append("Data-source warning: current mortgage pricing is using configured fallback rates")
    for horizon, row in forecast.items():
        if horizon in old_fc:
            if changed_enough(row["mortgage_2y_mid"], old_fc[horizon]["mortgage_2y_mid"], cfg["alert_rules"]["forecast_move_pp"]):
                out.append(f"{horizon} 2-year mortgage forecast moved materially")
            if changed_enough(row["mortgage_5y_mid"], old_fc[horizon]["mortgage_5y_mid"], cfg["alert_rules"]["forecast_move_pp"]):
                out.append(f"{horizon} 5-year mortgage forecast moved materially")
    return list(dict.fromkeys(out))


def outlook_direction(current2, current5, forecast):
    current = (current2 + current5) / 2
    six = (forecast["6 months"]["mortgage_2y_mid"] + forecast["6 months"]["mortgage_5y_mid"]) / 2
    delta = six - current
    if delta <= -0.50:
        return "Falling rapidly"
    if delta <= -0.10:
        return "Falling gradually"
    if delta < 0.10:
        return "Broadly stable"
    if delta < 0.50:
        return "Rising gradually"
    return "Rising rapidly"


def email_html(values, bands, mortgage_market, forecast, decision, why, pressure, cfg):
    ordered = [("bank_rate", "Bank Rate"), ("headline_cpi", "Headline CPI"), ("core_cpi", "Core CPI"), ("services_cpi", "Services CPI"), ("wage_growth", "Private regular wage growth"), ("unemployment", "Unemployment"), ("swap_2y", "2Y SONIA swap"), ("swap_5y", "5Y SONIA swap"), ("gilt_2y", "2Y gilt yield"), ("gilt_5y", "5Y gilt yield"), ("gilt_10y", "10Y gilt yield")]
    rows = ""
    for key, label in ordered:
        value = values.get(key)
        if value:
            rows += f"<tr><td>{label}</td><td>{fmt(value['value'])}</td><td>{bands.get(key, 'TRACK')}</td><td>{value['period']}</td><td>{value['source']}</td></tr>"
    market_rows = ""
    for ltv in ("60", "75", "90"):
        if ltv in mortgage_market["rates"]:
            x = mortgage_market["rates"][ltv]
            market_rows += f"<tr><td>{ltv}%</td><td>{fmt(x['2y'])}</td><td>{fmt(x['5y'])}</td></tr>"
    frows = "".join(f"<tr><td>{h}</td><td>{fmt(x['mortgage_2y_mid'])} ({fmt(x['mortgage_2y_range'][0])}–{fmt(x['mortgage_2y_range'][1])})</td><td>{fmt(x['mortgage_5y_mid'])} ({fmt(x['mortgage_5y_range'][0])}–{fmt(x['mortgage_5y_range'][1])})</td></tr>" for h, x in forecast.items())
    why_html = "".join(f"<li>{r}</li>" for r in why) or "<li>Manual/test report</li>"
    signal = "higher-rate pressure" if pressure > 0.15 else "lower-rate pressure" if pressure < -0.15 else "broadly neutral"
    direction = outlook_direction(decision["current_2y"], decision["current_5y"], forecast)
    a = cfg["decision_assumptions"]
    return f"""<html><body>
    <h2>UK Mortgage Outlook & Trigger Alert</h2>
    <p><b>Current direction:</b> {direction}<br><b>Macro/market pressure:</b> {signal} ({pressure:+.2f})</p>
    <h3>Current mortgage market</h3>
    <table border="1" cellpadding="6" cellspacing="0"><tr><th>LTV</th><th>Average 2Y fix</th><th>Average 5Y fix</th></tr>{market_rows}</table>
    <p><small>{mortgage_market['source']} — {mortgage_market['period']}. These are market averages, not personalised quotes.</small></p>
    <h3>Forecast</h3>
    <table border="1" cellpadding="6" cellspacing="0"><tr><th>Horizon</th><th>Typical 2Y fix</th><th>Typical 5Y fix</th></tr>{frows}</table>
    <h3>2-year vs 5-year decision guide</h3>
    <p>Using the configured {decision['ltv']}% LTV example:</p>
    <ul><li>Current 2Y fix: <b>{fmt(decision['current_2y'])}</b></li><li>Current 5Y fix: <b>{fmt(decision['current_5y'])}</b></li><li>Approximate refinance rate after two years needed for the 2Y route to beat the 5Y route over five years: <b>{fmt(decision['break_even_refi_rate'])}</b></li><li>Modelled three-year refinancing proxy in two years: <b>{fmt(decision['model_refi_proxy'])}</b></li></ul>
    <p><b>Interpretation:</b> {decision['signal']}</p>
    <p><small>Break-even assumptions: £{a['loan_amount']:,} repayment mortgage, {a['term_years']}-year term, 2Y fee £{a['two_year_fee']:,}, 5Y fee £{a['five_year_fee']:,}, refinance fee £{a['refinance_fee']:,}. Edit these in config.json to match a real decision.</small></p>
    <h3>What changed</h3><ul>{why_html}</ul>
    <h3>Market & economic dashboard</h3>
    <table border="1" cellpadding="6" cellspacing="0"><tr><th>Indicator</th><th>Latest</th><th>Band</th><th>Period</th><th>Source</th></tr>{rows}</table>
    <p><small>LOWER = signal consistent with lower mortgage rates; HIGHER = signal consistent with higher rates. SONIA swaps remain the main direct fixed-mortgage pricing input. Gilts are a supplementary term-premium/fiscal/global-bond signal. Forecasts are scenario-model ranges, not lender quotes.</small></p>
    </body></html>"""


def send_email(subject, html):
    email_from = os.environ.get("EMAIL_FROM")
    email_to = os.environ.get("EMAIL_TO")
    password = os.environ.get("EMAIL_APP_PASSWORD")
    if not all((email_from, email_to, password)):
        raise RuntimeError("Missing EMAIL_FROM, EMAIL_TO or EMAIL_APP_PASSWORD GitHub secret")
    msg = EmailMessage()
    msg["From"] = email_from
    msg["To"] = email_to
    msg["Subject"] = subject
    msg.set_content("UK Mortgage Outlook & Trigger Alert - view this email in HTML.")
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as smtp:
        smtp.login(email_from, password)
        smtp.send_message(msg)


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
            obs = latest_ons(path)
        except Exception:
            obs = Obs(cfg["baseline"][key], "fallback", "config fallback")
        values[key] = obs.__dict__
    values["bank_rate"] = bank_rate(cfg["baseline"]["bank_rate"]).__dict__
    try:
        swap_obs = swaps(cfg["baseline"]["swap_2y"], cfg["baseline"]["swap_5y"])
        values["swap_2y"] = swap_obs["2 Year"].__dict__
        values["swap_5y"] = swap_obs["5 Year"].__dict__
    except Exception:
        values["swap_2y"] = Obs(cfg["baseline"]["swap_2y"], "fallback", "config fallback").__dict__
        values["swap_5y"] = Obs(cfg["baseline"]["swap_5y"], "fallback", "config fallback").__dict__
    for key, years in (("gilt_2y", 2), ("gilt_5y", 5), ("gilt_10y", 10)):
        values[key] = gilt_yield(years, cfg["baseline"][key]).__dict__
    mortgage_market = rightmove_mortgage_rates(cfg)
    pressure, bands = pressure_index(values, cfg)
    forecast = make_forecast(values, mortgage_market, pressure, cfg)
    decision = decision_view(mortgage_market, forecast, cfg)
    why = reasons(values, bands, mortgage_market, prev, forecast, cfg)
    test = os.environ.get("TEST_EMAIL", "").lower() in {"1", "true", "yes"}
    first = not prev.get("alert_anchor")
    should_send = test or first or bool(why)
    now = datetime.now(timezone.utc).isoformat()
    state = {"last_checked": now, "latest_values": values, "mortgage_market": mortgage_market, "latest_bands": bands, "latest_pressure_index": round(pressure, 4), "latest_forecast": forecast, "latest_decision_view": decision, "alert_anchor": prev.get("alert_anchor")}
    if should_send:
        prefix = "TEST — " if test else ""
        subject = f"{prefix}UK Mortgage Outlook: {outlook_direction(decision['current_2y'], decision['current_5y'], forecast)}"
        send_email(subject, email_html(values, bands, mortgage_market, forecast, decision, why, pressure, cfg))
        state["alert_anchor"] = {"values": values, "mortgage_market": mortgage_market, "bands": bands, "forecast": forecast, "decision_view": decision, "sent_at": now}
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


if __name__ == "__main__":
    main()
