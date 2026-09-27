import json, os, re, smtplib, ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import requests
from bs4 import BeautifulSoup

CONFIG_PATH = Path("config.json")
STATE_PATH = Path("state.json")
TIMEOUT = 25
UA = "uk-mortgage-rate-monitor/1.0 (+GitHub Actions)"


@dataclass
class Obs:
    value: float
    period: str
    source: str


def get(url):
    r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
    r.raise_for_status()
    return r


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
        m = re.search(rf"{re.escape(tenor)}\s+(?:Live\s+)?(?:\|\s*)?(\d+(?:\.\d+)?)\s*%", text, re.I)
        if not m:
            pos = text.lower().find(tenor.lower())
            sample = text[pos:pos + 220] if pos >= 0 else ""
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", sample)
        out[tenor] = Obs(
            float(m.group(1)) if m else float(fallback),
            "previous UK business-day close",
            "BlueGamma public SONIA swap table" if m else "config fallback",
        )
    return out


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


def make_forecast(values, pressure, cfg):
    d2 = values["swap_2y"]["value"] - cfg["baseline"]["swap_2y"]
    d5 = values["swap_5y"]["value"] - cfg["baseline"]["swap_5y"]
    result = {}
    for horizon, hcfg in cfg["forecast_anchors"].items():
        shift2 = pressure * hcfg["pressure_sensitivity"] + d2 * hcfg["swap_sensitivity"]
        shift5 = pressure * hcfg["pressure_sensitivity"] + d5 * hcfg["swap_sensitivity"]
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


def fmt(v):
    return f"{v:.2f}%"


def changed_enough(a, b, threshold):
    try:
        return abs(float(a) - float(b)) >= threshold
    except Exception:
        return True


def reasons(values, bands, prev, forecast, cfg):
    reasons_out = []
    anchor = (prev or {}).get("alert_anchor", {})
    old_bands = anchor.get("bands", {})
    old_vals = anchor.get("values", {})
    old_fc = anchor.get("forecast", {})

    for key, b in bands.items():
        if old_bands and old_bands.get(key) != b:
            reasons_out.append(f"{cfg['labels'].get(key, key)} trigger moved {old_bands.get(key, '?')} → {b}")

    for key in ("swap_2y", "swap_5y"):
        if key in old_vals and changed_enough(values[key]["value"], old_vals[key]["value"], cfg["alert_rules"]["swap_move_pp"]):
            reasons_out.append(f"{cfg['labels'][key]} moved {values[key]['value'] - old_vals[key]['value']:+.2f}pp since last alert")

    if "bank_rate" in old_vals and changed_enough(values["bank_rate"]["value"], old_vals["bank_rate"]["value"], 0.24):
        reasons_out.append(f"Bank Rate changed from {old_vals['bank_rate']['value']:.2f}% to {values['bank_rate']['value']:.2f}%")

    for key, value in values.items():
        if "fallback" in value.get("source", "").lower():
            old_source = old_vals.get(key, {}).get("source", "")
            if "fallback" not in old_source.lower():
                reasons_out.append(f"Data-source warning: {cfg['labels'].get(key, key)} is using its configured fallback")

    for horizon, row in forecast.items():
        if horizon in old_fc:
            if changed_enough(row["mortgage_2y_mid"], old_fc[horizon]["mortgage_2y_mid"], cfg["alert_rules"]["forecast_move_pp"]):
                reasons_out.append(f"{horizon} 2-year mortgage forecast moved materially")
            if changed_enough(row["mortgage_5y_mid"], old_fc[horizon]["mortgage_5y_mid"], cfg["alert_rules"]["forecast_move_pp"]):
                reasons_out.append(f"{horizon} 5-year mortgage forecast moved materially")

    return list(dict.fromkeys(reasons_out))


def email_html(values, bands, forecast, why, pressure):
    ordered = [
        ("bank_rate", "Bank Rate"),
        ("headline_cpi", "Headline CPI"),
        ("core_cpi", "Core CPI"),
        ("services_cpi", "Services CPI"),
        ("wage_growth", "Private regular wage growth"),
        ("unemployment", "Unemployment"),
        ("swap_2y", "2Y SONIA swap"),
        ("swap_5y", "5Y SONIA swap"),
    ]
    rows = ""
    for key, label in ordered:
        value = values.get(key)
        if value:
            rows += f"<tr><td>{label}</td><td>{fmt(value['value'])}</td><td>{bands.get(key, 'TRACK')}</td><td>{value['period']}</td><td>{value['source']}</td></tr>"

    frows = "".join(
        f"<tr><td>{h}</td><td>{fmt(x['mortgage_2y_range'][0])}–{fmt(x['mortgage_2y_range'][1])}</td>"
        f"<td>{fmt(x['mortgage_5y_range'][0])}–{fmt(x['mortgage_5y_range'][1])}</td></tr>"
        for h, x in forecast.items()
    )
    why_html = "".join(f"<li>{r}</li>" for r in why) or "<li>Manual/test report</li>"
    direction = "higher-rate pressure" if pressure > 0.15 else "lower-rate pressure" if pressure < -0.15 else "broadly neutral"
    return f"""<html><body>
    <h2>UK Mortgage Rate Trigger Alert</h2>
    <p><b>Signal:</b> {direction} ({pressure:+.2f})</p>
    <h3>What changed</h3><ul>{why_html}</ul>
    <h3>Updated forecast</h3>
    <table border="1" cellpadding="6" cellspacing="0"><tr><th>Horizon</th><th>Typical 2Y fix</th><th>Typical 5Y fix</th></tr>{frows}</table>
    <p>Forecasts are scenario-model ranges for a mainstream ~75% LTV borrower, not product quotes.</p>
    <h3>Trigger dashboard</h3>
    <table border="1" cellpadding="6" cellspacing="0"><tr><th>Indicator</th><th>Latest</th><th>Band</th><th>Period</th><th>Source</th></tr>{rows}</table>
    <p><small>LOWER = signal consistent with lower mortgage rates; HIGHER = signal consistent with higher rates. This is a monitoring model, not financial advice.</small></p>
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
    msg.set_content("UK Mortgage Rate Trigger Alert - view this email in HTML.")
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

    pressure, bands = pressure_index(values, cfg)
    forecast = make_forecast(values, pressure, cfg)
    why = reasons(values, bands, prev, forecast, cfg)

    test = os.environ.get("TEST_EMAIL", "").lower() in {"1", "true", "yes"}
    first = not prev.get("alert_anchor")
    should_send = test or first or bool(why)
    now = datetime.now(timezone.utc).isoformat()

    if should_send:
        subject = "UK Mortgage Forecast — " + ("TEST / baseline" if test or first else why[0])
        send_email(subject, email_html(values, bands, forecast, why, pressure))
        anchor = {"values": values, "bands": bands, "forecast": forecast, "sent_at": now}
    else:
        anchor = prev.get("alert_anchor", {})

    state = {
        "last_checked": now,
        "latest_values": values,
        "latest_bands": bands,
        "latest_pressure_index": round(pressure, 4),
        "latest_forecast": forecast,
        "alert_anchor": anchor,
    }
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


if __name__ == "__main__":
    main()
