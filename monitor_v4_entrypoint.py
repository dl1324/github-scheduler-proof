import monitor_v4 as model


def robust_hoa_product(heading_phrase, ltv, fallback):
    """More tolerant parser for HOA/MAB long fixed-rate tables."""
    out = dict(fallback)
    out.setdefault("source", "config fallback")
    try:
        soup = model.BeautifulSoup(
            model.base.get("https://hoa.org.uk/best-mortgage-rates/").text,
            "html.parser",
        )
        heading = None
        for tag in soup.find_all(["h2", "h3", "h4"]):
            text = tag.get_text(" ", strip=True).lower()
            if all(word in text for word in ("10", "year", "fixed", "remortgage")):
                heading = tag
                break

        table = heading.find_next("table") if heading else None
        if table:
            for tr in table.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
                if len(cells) < 3:
                    continue
                rate = model.base.percent_number(cells[1])
                fee = model.base.money_number(cells[2])
                pct_vals = [model.base.percent_number(c) for c in cells]
                ltv_vals = [v for v in pct_vals if v is not None and 50 <= v <= 100]
                if rate is not None and 2 <= rate <= 10 and ltv_vals and max(ltv_vals) >= ltv:
                    return {
                        "lender": cells[0],
                        "rate": float(rate),
                        "fee": float(fee if fee is not None else fallback.get("fee", 0.0)),
                        "source": "HomeOwners Alliance / MAB current remortgage table",
                    }

        flat = soup.get_text(" | ", strip=True)
        start = flat.lower().find("best 10 year")
        if start >= 0:
            tokens = [t.strip() for t in flat[start:start + 6000].split("|") if t.strip()]
            for i in range(1, len(tokens) - 9):
                rate = model.base.percent_number(tokens[i])
                if rate is None or not (2 <= rate <= 10):
                    continue
                lender = tokens[i - 1]
                if not any(ch.isalpha() for ch in lender) or "£" in lender:
                    continue
                nearby = [model.base.percent_number(t) for t in tokens[i + 1:i + 10]]
                ltv_vals = [v for v in nearby if v is not None and 50 <= v <= 100]
                if not ltv_vals or max(ltv_vals) < ltv:
                    continue
                fee = next(
                    (model.base.money_number(t) for t in tokens[i + 1:i + 5] if model.base.money_number(t) is not None),
                    None,
                )
                return {
                    "lender": lender,
                    "rate": float(rate),
                    "fee": float(fee if fee is not None else fallback.get("fee", 0.0)),
                    "source": "HomeOwners Alliance / MAB current remortgage table",
                }
    except Exception:
        pass
    return out


model.hoa_product = robust_hoa_product

if __name__ == "__main__":
    model.main()
