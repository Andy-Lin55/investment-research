import pytest
from unittest.mock import patch, MagicMock
from investment_research.data_ingestion.pdf_parser import (
    extract_text_from_pdf,
    truncate_text,
)


def test_truncate_text_short():
    text = "a" * 100
    assert truncate_text(text) == text


def test_truncate_text_long():
    text = "a" * 7000
    result = truncate_text(text)
    assert len(result) <= 4200   # 3000 + separator (~100) + 1000


def test_truncate_text_exact_boundary():
    text = "x" * 6000
    assert truncate_text(text) == text


def test_truncate_text_over_boundary():
    text = "x" * 6001
    result = truncate_text(text)
    assert "中间省略" in result
    assert len(result) < 6001


def test_extract_text_missing_file():
    result = extract_text_from_pdf("/nonexistent/path/file.pdf")
    assert result == ""
