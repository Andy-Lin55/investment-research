"""Render briefing text as a readable, self-contained HTML email."""
from __future__ import annotations

from datetime import date
from email.message import EmailMessage
from html import escape
import re
from urllib.parse import urlparse

from loguru import logger


SECTION_HEADINGS = {"公司第一手披露详解", "事件原始摘要"}
DETAIL_HEADINGS = {"核心事项", "关键数据", "影响解读", "影响解读（分析判断）", "后续关注", "资料缺口", "已解析关键数据", "已有解析记录（补充）"}
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Arial,sans-serif"
TAG_STYLES = {
    "p": "margin:0 0 14px;font-size:15px;line-height:1.85;",
    "h1": "margin:30px 0 16px;font-size:22px;line-height:1.5;color:#162c25;",
    "h2": "margin:30px 0 16px;padding-top:20px;border-top:2px solid #dce7e1;font-size:21px;line-height:1.5;color:#162c25;",
    "h3": "margin:24px 0 12px;font-size:18px;line-height:1.6;color:#273842;",
    "h4": "margin:20px 0 10px;font-size:15px;line-height:1.7;color:#17664c;",
    "ul": "margin:8px 0 18px;padding-left:22px;",
    "ol": "margin:8px 0 18px;padding-left:24px;",
    "li": "margin:0 0 9px;font-size:15px;line-height:1.85;",
    "strong": "font-weight:650;color:#202a31;",
    "a": "color:#17664c;text-decoration:underline;overflow-wrap:anywhere;word-break:break-word;",
    "hr": "border:0;border-top:1px solid #e1e7e3;margin:26px 0;",
    "blockquote": "margin:16px 0;padding:10px 16px;border-left:3px solid #9fb8ac;background:#f3f7f4;color:#4d6257;",
    "pre": "padding:14px;background:#f3f6f4;white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;",
    "code": "font-family:Consolas,monospace;font-size:13px;overflow-wrap:anywhere;",
}


def _safe_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not any(ord(char) < 32 for char in value)


def _sources(source_notes: str) -> tuple[dict[str, str], str]:
    references = {}
    rows = []
    for line in source_notes.splitlines():
        if not line.strip():
            continue
        match = re.search(r"https?://\S+\s*$", line)
        url = match.group(0).strip() if match else ""
        label = line[:match.start()].rstrip(" ：:") if match else line
        number = re.match(r"^\[(\d+)\]", label)
        if url and _safe_url(url):
            if number:
                references.setdefault(number.group(1), url)
            domain = urlparse(url).hostname or ""
            content = (f'<a href="{escape(url, quote=True)}" style="color:#17664c;text-decoration:underline;">{escape(label)}</a>'
                       f'<br><span style="font-size:12px;color:#75837b;">{escape(domain)}</span>')
        else:
            content = escape(line)
        rows.append(f'<tr><td style="padding:10px 0;border-bottom:1px solid #e4eae6;font-size:13px;line-height:1.7;overflow-wrap:anywhere;word-break:break-word;">{content}</td></tr>')
    table = '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="width:100%;table-layout:fixed;">' + "".join(rows) + "</table>"
    return references, table


def _prepare_markdown(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in SECTION_HEADINGS:
            lines.append("## " + stripped)
        elif stripped in DETAIL_HEADINGS:
            lines.append("#### " + stripped)
        elif re.match(r"^\[\d+\]\s+", stripped) and not re.search(r"https?://", stripped):
            lines.append("### " + stripped)
        else:
            lines.append(line)
    return "\n".join(lines)


def _metric_tables(soup, markdown) -> None:
    from bs4 import BeautifulSoup

    for heading in list(soup.find_all(["h2", "h3", "h4"])):
        if heading.get_text(strip=True) not in {"关键数据", "已解析关键数据"}:
            continue
        listing = heading.find_next_sibling()
        if listing is None or listing.name != "ul":
            continue
        table = soup.new_tag("table", attrs={"width": "100%", "cellspacing": "0", "cellpadding": "0", "style": "width:100%;table-layout:fixed;border-collapse:collapse;margin:10px 0 20px;"})
        caption_row = soup.new_tag("tr")
        for label in ("指标 / 事项", "披露内容"):
            cell = soup.new_tag("th", attrs={"scope": "col", "style": "width:50%;padding:10px 12px;text-align:left;background:#eaf1ec;color:#365448;font-size:12px;line-height:1.6;border-bottom:1px solid #dbe5de;"})
            cell.string = label
            caption_row.append(cell)
        table.append(caption_row)
        for index, item in enumerate(listing.find_all("li", recursive=False)):
            text = item.get_text(" ", strip=True)
            label, separator, value = text.partition("：")
            if not separator:
                label, separator, value = text.partition(":")
            row = soup.new_tag("tr")
            cells = (label, value) if separator else (text,)
            for column, content in enumerate(cells):
                background = "#f5f7f7" if index % 2 == 0 else "#ffffff"
                color = "#59656b" if column == 0 else "#202c31"
                cell = soup.new_tag("td", attrs={"style": f"padding:10px 12px;vertical-align:top;background:{background};color:{color};border-bottom:1px solid #e2e9e4;font-size:14px;line-height:1.75;overflow-wrap:anywhere;word-break:break-word;"})
                if not separator:
                    cell["colspan"] = "2"
                for node in list(BeautifulSoup(markdown.renderInline(content), "html.parser").contents):
                    cell.append(node)
                row.append(cell)
            table.append(row)
        listing.replace_with(table)


def _link_citations(soup, references: dict[str, str]) -> None:
    for node in list(soup.find_all(string=True)):
        if node.parent.name in {"a", "code", "pre"} or not re.search(r"\[\d+\]", str(node)):
            continue
        cursor = 0
        text = str(node)
        for match in re.finditer(r"\[(\d+)\]", text):
            url = references.get(match.group(1))
            if not url:
                continue
            node.insert_before(text[cursor:match.start()])
            anchor = soup.new_tag("a", href=url)
            anchor.string = match.group(0)
            node.insert_before(anchor)
            cursor = match.end()
        if cursor:
            node.insert_before(text[cursor:])
            node.extract()


def render_briefing_html(subject: str, body: str, today: str) -> str:
    from bs4 import BeautifulSoup
    from markdown_it import MarkdownIt

    main, separator, source_notes = body.rpartition("\n\n资料来源\n")
    if not separator:
        main, source_notes = body, ""
    metadata, divider, remainder = main.partition("\n\n")
    if metadata.startswith(today) and divider:
        main = remainder
        metadata = metadata[len(today):].strip()
    else:
        metadata = ""
    references, source_html = _sources(source_notes)
    markdown = MarkdownIt("commonmark", {"html": False, "breaks": True})
    markdown.validateLink = lambda value: _safe_url(value)
    markdown.renderer.rules["image"] = lambda tokens, index, options, env: escape(tokens[index].content)
    soup = BeautifulSoup(markdown.render(_prepare_markdown(main)), "html.parser")
    for heading in soup.find_all(["h1", "h2", "h3"]):
        if heading.get_text(strip=True) in {"今日概览", "重点事件", "后续关注"}:
            heading.name = "h2"
    for divider in list(soup.find_all("hr")):
        following = divider.find_next_sibling()
        if following and following.name == "h2":
            divider.decompose()
    _metric_tables(soup, markdown)
    _link_citations(soup, references)
    for tag in soup.find_all(True):
        if tag.name in TAG_STYLES:
            tag["style"] = TAG_STYLES[tag.name] + tag.get("style", "")
    if soup.find("h2"):
        first = soup.find("h2")
        if first is soup.find(True):
            first["style"] += "border-top:0;padding-top:0;margin-top:20px;"
    date_label = date.fromisoformat(today).strftime("%Y.%m.%d")
    sources_section = (
        '<h2 style="margin:32px 0 8px;padding-top:22px;border-top:2px solid #dce7e1;font-size:20px;color:#162c25;">资料来源</h2>' + source_html
        if source_notes else ""
    )
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>{escape(subject)}</title>
<style>@media only screen and (max-width:600px){{.email-pad{{padding-left:18px!important;padding-right:18px!important;}}.report-title{{font-size:28px!important;}}}}body{{margin:0;letter-spacing:0;}}table{{border-spacing:0;}}a{{overflow-wrap:anywhere;}}</style></head>
<body style="margin:0;padding:0;background:#f2f4f5;font-family:{FONT};color:#29323a;letter-spacing:0;">
<div style="display:none;max-height:0;overflow:hidden;">{escape(subject)} · {escape(metadata)}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="width:100%;background:#f2f4f5;"><tr><td align="center" style="padding:24px 10px;">
<!--[if mso]><table role="presentation" width="720"><tr><td><![endif]-->
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="width:100%;max-width:720px;background:#ffffff;border-top:5px solid #17664c;">
<tr><td class="email-pad" style="padding:28px 32px 24px;border-bottom:1px solid #dce5de;">
<p style="margin:0 0 12px;font-size:11px;font-weight:700;color:#17664c;">INVESTMENT RESEARCH</p>
<h1 class="report-title" style="margin:0 0 12px;font-size:32px;line-height:1.3;font-weight:700;color:#172d23;">投研日报</h1>
<p style="margin:0 0 6px;font-size:14px;color:#5a6d60;">{escape(date_label)}</p>
<p style="margin:0;font-size:13px;line-height:1.7;color:#5a6d60;">{escape(metadata)}</p>
</td></tr>
<tr><td class="email-pad" style="padding:4px 32px 28px;overflow-wrap:anywhere;word-break:break-word;">{soup}{sources_section}</td></tr>
<tr><td class="email-pad" style="padding:18px 32px;border-top:1px solid #e2e8e3;font-size:11px;line-height:1.6;color:#7a897f;">INVESTMENT RESEARCH · {escape(date_label)}</td></tr>
</table><!--[if mso]></td></tr></table><![endif]-->
</td></tr></table></body></html>'''


def build_briefing_message(subject: str, body: str, today: str, sender: str, recipient: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)
    try:
        message.add_alternative(render_briefing_html(subject, body, today), subtype="html")
    except Exception as error:
        logger.warning(f"HTML 邮件排版失败，保留完整纯文本邮件（{type(error).__name__}）")
    return message
