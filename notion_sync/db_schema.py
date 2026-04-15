"""
notion_sync/db_schema.py
定义三个 Notion 数据库的完整字段 schema
用于创建/验证数据库结构
"""

# ── DB1: Universe 标的宇宙 ───────────────────────────────────
UNIVERSE_SCHEMA = {
    "Name": {"type": "title"},
    "Ticker": {"type": "rich_text"},
    "Market": {
        "type": "select",
        "options": ["A股", "港股", "美股"],
    },
    "Industry": {
        "type": "select",
        "options": [
            "消费", "科技", "医药", "金融", "能源",
            "地产", "工业", "材料", "公用事业", "通信",
        ],
    },
    "MarketCap": {
        "type": "select",
        "options": ["大盘", "中盘", "小盘"],
    },
    "Status": {
        "type": "select",
        "options": ["Coverage", "Active", "Watch", "Archived"],
    },
    "LatestSignal": {
        "type": "select",
        "options": ["Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"],
    },
    "AIScore": {"type": "number"},
    "LastUpdated": {"type": "date"},
    # 扩展A：财务快照字段
    "PE_TTM": {"type": "number"},
    "PB": {"type": "number"},
    "ROE": {"type": "number"},
    "RevenueGrowth": {"type": "number"},
    "NetProfitGrowth": {"type": "number"},
    "DividendYield": {"type": "number"},
}

# ── DB2: Events 事件流 ───────────────────────────────────────
EVENTS_SCHEMA = {
    "Name": {"type": "title"},
    "Ticker": {"type": "relation"},          # → Universe DB
    "EventType": {
        "type": "select",
        "options": [
            "公告", "业绩会", "研报", "专家纪要", "并购", "8-K",
            "年度报告", "中期报告", "季度报告", "业绩会材料", "新闻稿",
            "20-F", "6-K", "10-K", "10-Q", "新闻资讯",
        ],
    },
    "FiscalPeriod": {"type": "rich_text"},
    "Importance": {
        "type": "select",
        "options": ["High", "Medium", "Low"],
    },
    "Signal": {
        "type": "select",
        "options": ["Strong Buy", "Buy", "Hold", "Sell", "Strong Sell", "中性"],
    },
    "EventDate": {"type": "date"},
    "ProcessedAt": {"type": "date"},
    "AI Summary": {"type": "rich_text"},
    "KeyNumbers": {"type": "rich_text"},
    "SourceURL": {"type": "url"},
    "Status": {
        "type": "select",
        "options": ["New", "Reviewed", "Actioned"],
    },
}

# ── DB3: Research 研究库 ─────────────────────────────────────
RESEARCH_SCHEMA = {
    "Name": {"type": "title"},
    "Ticker": {"type": "relation"},          # → Universe DB
    "DocType": {
        "type": "select",
        "options": ["卖方研报", "电话会纪要", "专家纪要", "自研"],
    },
    "Broker": {"type": "rich_text"},
    "Analyst": {"type": "rich_text"},
    "Rating": {
        "type": "select",
        "options": ["买入", "增持", "中性", "减持", "卖出"],
    },
    "TargetPrice": {"type": "number"},
    "Upside": {"type": "number"},
    "CoreThesis": {"type": "rich_text"},
    "Catalysts": {"type": "rich_text"},
    "ReportDate": {"type": "date"},
    "ParsedAt": {"type": "date"},
    "RiskFactors":    {"type": "rich_text"},
    "ValuationView":  {"type": "select", "options": ["低估", "合理", "高估"]},
    "Confidence":     {"type": "select", "options": ["High", "Medium", "Low"]},
    "DebateSummary":  {"type": "rich_text"},
    "TriggerEventID": {"type": "rich_text"},
    "DataSources":    {"type": "rich_text"},
}


def build_notion_property(prop_name: str, prop_def: dict, value) -> dict:
    """
    将 Python 值转换为 Notion API 的属性格式

    用法：
        build_notion_property("Ticker", {"type": "rich_text"}, "600519")
    """
    prop_type = prop_def["type"]

    if prop_type == "title":
        return {"title": [{"text": {"content": str(value)}}]}

    elif prop_type == "rich_text":
        return {"rich_text": [{"text": {"content": str(value)}}]}

    elif prop_type == "select":
        return {"select": {"name": str(value)}}

    elif prop_type == "number":
        return {"number": float(value) if value is not None else None}

    elif prop_type == "date":
        if value is None:
            return {"date": None}
        if hasattr(value, "isoformat"):
            iso = value.isoformat()
        else:
            iso = str(value)
        return {"date": {"start": iso}}

    elif prop_type == "url":
        return {"url": str(value) if value else None}

    elif prop_type == "relation":
        # value 应为 Notion page_id 字符串或列表
        if isinstance(value, list):
            return {"relation": [{"id": v} for v in value]}
        return {"relation": [{"id": str(value)}]}

    else:
        raise ValueError(f"Unsupported property type: {prop_type}")
