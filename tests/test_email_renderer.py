from email import policy
from email.parser import BytesParser
from unittest.mock import MagicMock

from bs4 import BeautifulSoup

from scheduler import email_renderer


BODY = """2026-10-23 重点事件 2 条（High 1，Medium 1）

### 今日概览

**示例公司**收入增长，利润下降，需核实原因。[1]

公司第一手披露详解

[1] 示例公司 季度报告（季度报告）

核心事项
- 本季营收100亿元，同比增长5%。
- 本季利润8亿元，同比下降10%。

关键数据
- 单季营业收入：100亿元，同比增长5%
- 归母净利润：8亿元，同比下降10%
- 经营现金流：12亿元

影响解读（分析判断）
收入与利润表现分化，原文未说明利润下降原因。

事件原始摘要
[2] [Medium] 普通新闻
事实摘要：公司将召开股东会。

资料来源
[1] 示例公司 季度报告：https://example.com/report.pdf
[2] 新闻原文：https://example.org/news/article.html
"""


def render(body=BODY):
    return email_renderer.render_briefing_html("投研日报 2026-10-23", body, "2026-10-23")


def test_markdown_becomes_real_headings_and_emphasis():
    soup = BeautifulSoup(render(), "html.parser")
    assert soup.h1.get_text() == "投研日报"
    assert soup.find("h2", string="今日概览")
    assert soup.find("h2", string="公司第一手披露详解")
    assert soup.find("strong", string="示例公司")
    assert "###" not in soup.get_text()
    assert "**" not in soup.get_text()


def test_key_metrics_become_readable_tables_without_losing_units():
    soup = BeautifulSoup(render(), "html.parser")
    header = soup.find("th", string="披露内容")
    table = header.find_parent("table")
    assert len(table.find_all("tr")) == 4
    assert "100亿元，同比增长5%" in table.get_text()
    assert "8亿元，同比下降10%" in table.get_text()
    assert "12亿元" in table.get_text()


def test_citations_link_to_original_sources_and_long_urls_are_compact():
    html = render()
    soup = BeautifulSoup(html, "html.parser")
    citation = soup.find("a", string="[1]")
    assert citation["href"] == "https://example.com/report.pdf"
    assert soup.find("a", string="[2] 新闻原文")["href"] == "https://example.org/news/article.html"
    assert "https://example.org/news/article.html" not in soup.get_text()
    assert "example.org" in soup.get_text()


def test_raw_html_scripts_and_tracking_images_cannot_execute():
    body = BODY.replace('核心事项', '核心事项\n\n<script>alert(1)</script>\n<img src="https://tracker.example/pixel">\n![tracking](https://tracker.example/pixel)\n[unsafe](javascript:alert%281%29)\n')
    soup = BeautifulSoup(render(body), "html.parser")
    assert not soup.find("script")
    assert not soup.find("img")
    assert not soup.find("a", href=lambda href: href and href.startswith("javascript:"))


def test_long_body_and_missing_source_links_are_preserved():
    text = "完整的公司解析。" * 1000
    body = f"2026-10-23 重点事件 1 条\n\n{text}\n\n资料来源\n[1] 未附原文链接"
    soup = BeautifulSoup(render(body), "html.parser")
    assert text in soup.get_text()
    assert "未附原文链接" in soup.get_text()
    assert len(soup.get_text()) > 3000


def test_mime_contains_html_and_complete_plain_text_fallback():
    message = email_renderer.build_briefing_message(
        "投研日报", BODY, "2026-10-23", "sender@example.com", "reader@example.com",
    )
    parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
    assert parsed.get_content_type() == "multipart/alternative"
    assert parsed.get_body(preferencelist=("plain",)).get_content().strip() == BODY.strip()
    html = parsed.get_body(preferencelist=("html",)).get_content()
    assert "关键数据" in html
    assert "@media" in html
    assert 'table-layout:fixed' in html
    assert parsed["To"] == "reader@example.com"


def test_rendering_failure_still_leaves_sendable_plain_email(monkeypatch):
    monkeypatch.setattr(email_renderer, "render_briefing_html", lambda *args: (_ for _ in ()).throw(ValueError("renderer failed")))
    message = email_renderer.build_briefing_message("投研日报", BODY, "2026-10-23", "sender@example.com", "reader@example.com")
    assert message.get_content_type() == "text/plain"
    assert message.get_content().strip() == BODY.strip()


def test_smtp_job_sends_both_email_versions_without_network(monkeypatch):
    import smtplib
    from config import settings
    from scheduler.jobs import _send_briefing

    monkeypatch.setattr(settings, "FEISHU_WEBHOOK", "")
    monkeypatch.setattr(settings, "SMTP_USER", "sender@example.com")
    monkeypatch.setattr(settings, "SMTP_PASS", "test-password")
    monkeypatch.setattr(settings, "BRIEFING_TO_EMAIL", "reader@example.com")
    connection = MagicMock()
    connection.__enter__.return_value = connection
    monkeypatch.setattr(smtplib, "SMTP_SSL", lambda *args, **kwargs: connection)

    _send_briefing("投研日报", BODY, "2026-10-23")

    message = connection.send_message.call_args.args[0]
    assert message.get_content_type() == "multipart/alternative"
    assert "关键数据" in message.get_body(preferencelist=("html",)).get_content()
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == BODY.strip()
