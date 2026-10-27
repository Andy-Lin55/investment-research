import fitz
from data_ingestion.pdf_parser import _extract_text, _trim_text, _parse_json_response


def test_short_document_and_exact_boundary_are_preserved():
    for text in ("a" * 100, "x" * 6000):
        assert _trim_text(text) == text


def test_long_document_preserves_head_and_tail():
    result = _trim_text("H" * 3000 + "M" * 3000 + "T" * 1000)
    assert result.startswith("H" * 3000)
    assert result.endswith("T" * 1000)
    assert "M" not in result
    assert len(result) < 4200


def test_extract_text_reads_all_pdf_pages(tmp_path):
    path = tmp_path / "report.pdf"
    with fitz.open() as document:
        for text in ("Revenue 100", "Cash flow 20"):
            page = document.new_page()
            page.insert_text((50, 50), text)
        document.save(path)
    extracted = _extract_text(path)
    assert "Revenue 100" in extracted
    assert "Cash flow 20" in extracted


def test_missing_pdf_is_unavailable():
    assert _extract_text("/nonexistent/source-report.pdf") == ""


def test_fenced_model_json_is_parsed_and_unstructured_output_is_preserved():
    assert _parse_json_response('```json\n{"profit": 20}\n```') == {"profit": 20}
    assert _parse_json_response("source unavailable") == {"raw_response": "source unavailable"}
