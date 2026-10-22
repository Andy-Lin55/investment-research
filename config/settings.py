"""
config/settings.py
统一配置管理，从 .env 文件加载所有环境变量
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env（向上查找到项目根目录）
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=_env_path, override=True)

# ── Notion ──────────────────────────────────────────────────
NOTION_TOKEN: str = os.getenv("NOTION_TOKEN", "")
NOTION_UNIVERSE_DB_ID: str = os.getenv("NOTION_UNIVERSE_DB_ID", "")
NOTION_EVENTS_DB_ID: str = os.getenv("NOTION_EVENTS_DB_ID", "")
NOTION_RESEARCH_DB_ID: str = os.getenv("NOTION_RESEARCH_DB_ID", "")
NOTION_RESEARCH_HISTORY_DB_ID: str = os.getenv("NOTION_RESEARCH_HISTORY_DB_ID", "")

# ── Tushare ─────────────────────────────────────────────────
TUSHARE_TOKEN: str = os.getenv("TUSHARE_TOKEN", "")

# ── Finnhub ──────────────────────────────────────────────────
FINNHUB_KEY: str = os.getenv("FINNHUB_KEY", "")

# ── Optional local ChatGPT/Codex subscription ────────────────
CODEX_SUBSCRIPTION_ENABLED: bool = os.getenv("CODEX_SUBSCRIPTION_ENABLED", "false").lower() in {"true", "1", "yes"}
CODEX_CLI_PATH: str = os.getenv("CODEX_CLI_PATH", "codex")
CODEX_MODEL: str = os.getenv("CODEX_MODEL", "")
CODEX_TIMEOUT_SECONDS: int = int(os.getenv("CODEX_TIMEOUT_SECONDS", "60"))

# ── DMXAPI (OpenAI-compatible) ───────────────────────────────
DMXAPI_KEY: str = os.getenv("DMXAPI_KEY", "")
DMXAPI_BASE_URL: str = "https://www.dmxapi.cn/v1"
DMXAPI_MODEL: str = "gemini-3.8-flash"

# ── Google Gemini (fallback) ──────────────────────────────────
GEMINI_API_KEY: str  = os.getenv("GEMINI_API_KEY", "")
GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_MODEL: str    = "gemini-3.8-flash"

# ── 每日简报通知 ──────────────────────────────────────────────
BRIEFING_TO_EMAIL: str = os.getenv("BRIEFING_TO_EMAIL", "andyl603504@icloud.com")
SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER: str = os.getenv("SMTP_USER", "")
SMTP_PASS: str = os.getenv("SMTP_PASS", "")
BRIEFING_FROM_EMAIL: str = os.getenv("BRIEFING_FROM_EMAIL", SMTP_USER)

# ── 飞书 ────────────────────────────────────────────────────
FEISHU_WEBHOOK: str = os.getenv("FEISHU_WEBHOOK", "")

# ── 路径 ────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
TMP_ANNOUNCEMENTS = Path("/tmp/announcements")
TMP_ANNOUNCEMENTS.mkdir(parents=True, exist_ok=True)

RESEARCH_WATCH_DIR = Path.home() / "Downloads" / "research_reports"
RESEARCH_ARCHIVE_DIR = Path.home() / "research_archive"

# ── 请求限速 ─────────────────────────────────────────────────
REQUEST_INTERVAL_SECONDS: float = 1.0
MAX_RETRIES: int = 3

# ── 公告过滤关键词 ───────────────────────────────────────────
ANNOUNCEMENT_KEYWORDS = [
    "业绩预告", "业绩快报", "重大合同", "股权激励",
    "并购重组", "回购", "管理层变动", "分红实施",
    "股东大会", "股东会",
]

# 定期报告关键词（年报/季报/业绩会）
PERIODIC_REPORT_KEYWORDS = [
    "年度报告", "半年度报告",
    "第一季度报告", "第三季度报告",
    "业绩说明会", "业绩发布会", "投资者说明会",
    "新闻稿", "媒体公告",
]

# ── SEC EDGAR ────────────────────────────────────────────────
SEC_USER_AGENT: str = os.getenv("SEC_USER_AGENT", "InvestmentResearch research@invest.com")

# ── 验证函数 ─────────────────────────────────────────────────
def validate() -> dict:
    """检查所有必填配置是否已设置，返回状态字典"""
    checks = {
        "NOTION_TOKEN": bool(NOTION_TOKEN),
        "NOTION_UNIVERSE_DB_ID": bool(NOTION_UNIVERSE_DB_ID),
        "NOTION_EVENTS_DB_ID": bool(NOTION_EVENTS_DB_ID),
        "NOTION_RESEARCH_DB_ID": bool(NOTION_RESEARCH_DB_ID),
        "TUSHARE_TOKEN": bool(TUSHARE_TOKEN),
        "DMXAPI_KEY": bool(DMXAPI_KEY),
        "FEISHU_WEBHOOK": bool(FEISHU_WEBHOOK),  # 可选
    }
    return checks
