"""Compare computed scenarios to original Chinese annual guidance ranges."""
from datetime import date
import re

from financials.forecast import number


GUIDANCE_DOCUMENTS = {"业绩预告", "业绩预告修正"}
NUMERIC = r"[-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
RANGE = rf"(?P<low>{NUMERIC})\s*(?:～|~|至|—|–)\s*(?P<high>{NUMERIC})(?![\d,.%％])"
ANNUAL_PERIOD = re.compile(r"(?P<year>20\d{2})年0?1月0?1日(?:至|到|[-—–])(?P=year)年12月31日")
PARENT_PROFIT = re.compile(rf"(?:归属于上市公司股\s*东的净利润|归属于母公司股\s*东的净利润)\s*{RANGE}")
BASIC_EPS = re.compile(rf"基本每股收益\s*[（(]元/\s*股[）)]\s*{RANGE}")
UNITS = {"亿元": 100_000_000, "万元": 10_000, "千元": 1_000, "元": 1}


def _candidate_year(document):
    year = re.search(r"(20\d{2})\s*年", document.get("title", ""))
    if year:
        return int(year[1])
    for page in document.get("pages", []):
        period = ANNUAL_PERIOD.search(re.sub(r"\s+", "", page["text"]))
        if period:
            return int(period["year"])
    return None


def _ranges(document, year):
    """Require full-year dates, labels and units on the same original page."""
    ranges = {}
    for page in document.get("pages", []):
        text = page["text"]
        period = ANNUAL_PERIOD.search(re.sub(r"\s+", "", text))
        if not period or int(period["year"]) != year:
            continue
        for metric, pattern in (("parent_net_income", PARENT_PROFIT), ("eps", BASIC_EPS)):
            match = pattern.search(text)
            if not match:
                continue
            unit = "元/股"
            scale = 1
            if metric == "parent_net_income":
                units = re.findall(r"单位\s*[:：]\s*(?:人民币)?\s*(亿元|万元|千元|元)(?![/／])", text[:match.start()])
                if not units:
                    continue
                unit = units[-1]
                scale = UNITS[unit]
            low, high = [float(match[k].replace(",", "").replace("−", "-")) * scale for k in ("low", "high")]
            if not number(low) or not number(high) or low > high:
                continue
            evidence = dict(low=low, high=high, original_unit=unit,
                            source_ref=document["revision_id"], source_page=page["page"],
                            quote=match[0], published_at=document["published_at"])
            if metric in ranges and ranges[metric] != evidence:
                return {}
            ranges[metric] = evidence
    return ranges


def compare_management_guidance(model, documents, *, issuer_id, as_of):
    """Programmatic gap vs. the nearest boundary. Never use guidance as model input."""
    result = {"rows": [], "gaps": []}
    if (not model or model.get("issuer_id") != issuer_id or model.get("currency") != "CNY"
            or model.get("status") != "ready_for_review"):
        return result

    cutoff = date.fromisoformat(as_of)
    forecast_years = {row["year"] for rows in model.get("scenarios", {}).values() for row in rows}
    by_year = {}
    for doc in documents:
        if (doc.get("document_type") not in GUIDANCE_DOCUMENTS or doc.get("issuer_id") != issuer_id
                or not doc.get("revision_id")):
            continue
        try:
            published = date.fromisoformat(doc.get("published_at", "")[:10])
        except ValueError:
            continue
        year = _candidate_year(doc)
        if year in forecast_years and published <= cutoff:
            by_year.setdefault(year, []).append((published, doc))

    for year, versions in sorted(by_year.items()):
        latest_day = max(published for published, _ in versions)
        latest = {doc["revision_id"]: doc for published, doc in versions if published == latest_day}
        if len(latest) != 1:
            result["gaps"].append({"year": year, "reason": "multiple_latest_guidance_originals"})
            continue
        doc = next(iter(latest.values()))
        original = doc.get("source", {}).get("original", {})
        ranges = _ranges(doc, year) if original.get("verified") is True else {}
        if not ranges:
            result["gaps"].append({"year": year, "reason": "latest_guidance_range_not_verified"})
            continue
        for metric, evidence in ranges.items():
            low, high = evidence["low"], evidence["high"]
            for case, rows in model["scenarios"].items():
                row = next((row for row in rows if row["year"] == year), None)
                if not row or row.get("status") != "ready_for_review":
                    continue
                own = row["income"].get(metric)
                if not number(own):
                    continue
                boundary = low if own < low else high if own > high else own
                position = "below" if own < low else "above" if own > high else "within"
                result["rows"].append(dict(evidence, case=case, metric=metric, year=year, own=own, position=position,
                    difference=own-boundary, unit="CNY/share" if metric == "eps" else "CNY",
                    model_ref=model["model_version"]))
    return result
