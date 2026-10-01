"""Small production entrypoint for source-specific hardening around the v3 engine."""
import re

from bs4 import BeautifulSoup

import monitor_v3 as core


def rightmove_average_rates(cfg):
    """Read only the first (average-rate) Rightmove row for each LTV/term.

    Rightmove places lower/lowest-rate tables later on the same page. Without the
    first-row guard those later tables can overwrite the average-rate values.
    """
    fallback = cfg["mortgage_market_fallback"]
    result = {str(k): {"2y": float(v["2y"]), "5y": float(v["5y"])} for k, v in fallback.items()}
    source = "config fallback"
    period = "fallback"
    try:
        url = "https://www.rightmove.co.uk/news/articles/property-news/current-uk-mortgage-rates/"
        soup = BeautifulSoup(core.get(url).text, "html.parser")
        text = soup.get_text(" ", strip=True)
        date_matches = re.findall(r"Updated:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})", text)
        if date_matches:
            period = date_matches[0]
        found = {}
        for tr in soup.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if len(cells) < 4:
                continue
            m_ltv = re.fullmatch(r"(60|75|90|95)\s*%", cells[0])
            if not m_ltv:
                continue
            term_text = cells[1].lower().replace("–", "-")
            term = "2y" if "2-year" in term_text else "5y" if "5-year" in term_text else None
            if not term:
                continue
            key = (m_ltv.group(1), term)
            if key in found:
                continue
            pcts = []
            for cell in cells[2:]:
                pcts.extend(float(x) for x in re.findall(r"(-?\d+(?:\.\d+)?)\s*%", cell))
            if len(pcts) >= 2:
                current = pcts[1]
                if 2.0 <= current <= 10.0:
                    found[key] = current
        if len(found) >= 6:
            for (ltv, term), rate in found.items():
                result.setdefault(ltv, {})[term] = rate
            source = "Rightmove / Podium average home-buyer rates"
    except Exception:
        pass
    return {"rates": result, "period": period, "source": source}


_original_reasons = core.reasons


def reasons(values, bands, mortgage_market, decision_products, flex_tracker, prev, forecast, bank_fc, cfg):
    out = _original_reasons(
        values, bands, mortgage_market, decision_products, flex_tracker, prev, forecast, bank_fc, cfg
    )
    if mortgage_market.get("source") == "config fallback":
        out.append("Data-source warning: Rightmove average mortgage pricing is using configured fallback rates")
    if decision_products.get("source") == "config fallback":
        out.append("Data-source warning: competitive fixed/tracker products are using configured fallback rates")
    if "fallback" in flex_tracker.get("source", "").lower():
        out.append("Data-source warning: flexible tracker pricing is using its configured fallback")
    return list(dict.fromkeys(out))


core.rightmove_average_rates = rightmove_average_rates
core.reasons = reasons


if __name__ == "__main__":
    core.main()
