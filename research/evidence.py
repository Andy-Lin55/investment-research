"""Build reviewable evidence cards without network, AI calls or external writes.

Classification is an explicit metadata hint, never a claim that the source or
investment interpretation has been independently verified.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from urllib.parse import urlsplit


TAXONOMY = {
    "trading_flows": {"block_trade", "price_anomaly", "ownership", "buyback", "fund_flows"},
    "fundamentals": {"earnings", "announcement", "price_valuation", "earnings_call", "industry", "operations"},
    "policy": {"regulatory", "fiscal_monetary", "tax_trade", "macro_external"},
}
_DOCUMENT_TYPES = {
    **{name: ("fundamentals", "earnings") for name in (
        "季度报告", "年度报告", "半年度报告", "第一季度报告", "第三季度报告",
        "业绩预告", "业绩快报", "业绩预告修正", "10-K", "10-Q", "20-F",
    )},
    "行情": ("fundamentals", "price_valuation"),
    "估值": ("fundamentals", "price_valuation"),
    "业绩会": ("fundamentals", "earnings_call"),
    "行业数据": ("fundamentals", "industry"),
    "经营数据": ("fundamentals", "operations"),
    "公告": ("fundamentals", "announcement"),
    "重大公告": ("fundamentals", "announcement"),
    "大宗交易": ("trading_flows", "block_trade"),
    "大额成交": ("trading_flows", "block_trade"),
    "股价异动": ("trading_flows", "price_anomaly"),
    "减持": ("trading_flows", "ownership"),
    "增持": ("trading_flows", "ownership"),
    "股权变动": ("trading_flows", "ownership"),
    "回购": ("trading_flows", "buyback"),
    "资金流": ("trading_flows", "fund_flows"),
    "政策": ("policy", "regulatory"),
    "财政货币": ("policy", "fiscal_monetary"),
    "税收关税": ("policy", "tax_trade"),
    "重大外部事件": ("policy", "macro_external"),
}


def _text(value, field, *, optional=False):
    if not isinstance(value, str) or (not optional and not value.strip()):
        raise ValueError(f"{field} must be a {'possibly empty ' if optional else 'nonempty '}string")
    return value.strip()


def _timestamp(value, field):
    try:
        parsed = datetime.fromisoformat(_text(value, field).replace("Z", "+00:00"))
    except (ValueError, TypeError) as error:
        raise ValueError(f"{field} must be an ISO timestamp with timezone") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc)


def _strings(value, field):
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    return [_text(item, field) for item in value]


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_card(payload: dict) -> dict:
    """Validate supplied metadata and return a candidate card for human review.

    Same issuer/source locator gives the same event identity. A change to source
    text yields a new revision; late ingestion alone never creates a new event.
    No document is fetched, no research is run and no task is persisted here.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    data = deepcopy(payload)
    issuer = _text(data.get("issuer_id"), "issuer_id")
    ticker = _text(data.get("ticker"), "ticker")
    title = _text(data.get("title"), "title")
    doc_type = _text(data.get("document_type"), "document_type")
    source_url = _text(data.get("source_url"), "source_url")
    parts = urlsplit(source_url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("source_url must be an HTTP(S) source without credentials")
    published = _timestamp(data.get("published_at"), "published_at")
    ingested = _timestamp(data.get("ingested_at"), "ingested_at")
    if ingested < published:
        raise ValueError("ingested_at precedes published_at")
    content = _text(data.get("content", ""), "content", optional=True)

    category, subtype = _DOCUMENT_TYPES.get(doc_type, (None, None))
    if "category" in data or "subtype" in data:
        category, subtype = data.get("category"), data.get("subtype")
        if category not in TAXONOMY or subtype not in TAXONOMY[category]:
            raise ValueError("category/subtype is not a supported pair")
    tags = list(dict.fromkeys(_strings(data.get("tags", []), "tags")))
    facts = _strings(data.get("facts", []), "facts")
    interpretations = _strings(data.get("interpretations", []), "interpretations")
    calculations = data.get("calculations", [])
    if not isinstance(calculations, list):
        raise ValueError("calculations must be a list")
    for item in calculations:
        if not isinstance(item, dict):
            raise ValueError("each calculation must be an object")
        _text(item.get("statement"), "calculation.statement")
        _text(item.get("formula"), "calculation.formula")
        inputs = item.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            raise ValueError("each calculation needs explicit inputs")
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in inputs):
            raise ValueError("calculation inputs must be finite numbers")

    stage = data.get("event_stage", "unknown")
    if stage not in {"unknown", "planned", "executed", "draft"}:
        raise ValueError("unsupported event_stage")
    if stage == "unknown" and category == "trading_flows" and subtype in {"ownership", "buyback"}:
        if "计划" in title and not any(word in title for word in ("实施完毕", "完成", "终止")):
            stage = "planned"
        elif any(word in title for word in ("实施完毕", "完成")):
            stage = "executed"
    if stage == "unknown" and category == "policy" and "征求意见" in title:
        stage = "draft"
    material = data.get("is_material", False)
    if not isinstance(material, bool):
        raise ValueError("is_material must be boolean")
    required = _DOCUMENT_TYPES.get(doc_type) == ("fundamentals", "earnings") or doc_type == "重大公告" or material
    missing = []
    if category is None:
        missing.append("classification")
    if not content:
        missing.append("source_content")
    document_id = _text(data.get("document_id", source_url), "document_id")
    event_id = _digest(json.dumps([issuer, document_id], ensure_ascii=False))
    content_hash = _digest(content)
    revision = _digest(event_id + ":" + content_hash)
    return {
        "schema_version": 1, "event_id": event_id, "revision_id": revision,
        "issuer_id": issuer, "ticker": ticker, "title": title, "document_type": doc_type,
        "category": category, "subtype": subtype, "tags": tags,
        "classification_status": "suggested" if category else "needs_review",
        "source": {"url": source_url, "document_id": document_id,
                   "published_at": published.isoformat(), "content_sha256": content_hash,
                   "locator": _text(data.get("source_locator", ""), "source_locator", optional=True)},
        "ingested_at": ingested.isoformat(), "facts": facts, "calculations": calculations,
        "interpretations": interpretations, "event_stage": stage,
        "human_review": "pending", "automatic_validation": "structure_only", "missing": missing,
        "required_research": required,
        "research_status": ("pending_evidence" if not content else "pending") if required else "not_requested",
    }
