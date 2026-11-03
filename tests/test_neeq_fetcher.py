import json
from unittest.mock import Mock

import pytest

from data_ingestion import neeq_fetcher
from data_ingestion.disclosure_health import SourceFetchError, SourceHealth


def page(number, rows, *, last, total):
    payload = {"status": 0, "listInfo": {"number": number, "content": rows, "lastPage": last, "totalElements": total}}
    return Mock(text="null(" + json.dumps([payload]) + ")")


def filing(identifier, title="[定期报告]亚锦科技:2026年三季度报告", code="830806"):
    return {"companyCd": code, "publishDate": "2026-10-28", "disclosureTitle": title,
            "destFilePath": f"/disclosure/2026/2026-10-28/{identifier}.pdf"}


def test_collects_official_neeq_originals_across_pages(monkeypatch):
    post = Mock(side_effect=[page(0, [filing("quarter")], last=False, total=2),
                             page(1, [filing("dividend", "[临时公告]2026年半年度权益分派实施公告")], last=True, total=2)])
    monkeypatch.setattr(neeq_fetcher.requests, "post", post)
    rows = neeq_fetcher.fetch_neeq_filings("830806.NQ", since_date="2026-10-26", until_date="2026-11-03", strict=True)
    assert [call.kwargs["data"]["page"] for call in post.call_args_list] == [0, 1]
    assert [row["doc_type"] for row in rows] == ["季度报告", "公告"]
    assert rows[0]["url"] == "https://www.neeq.com.cn/disclosure/2026/2026-10-28/quarter.pdf"


@pytest.mark.parametrize("invalid", [filing("other", code="000001"), filing("repeat")])
def test_cross_issuer_or_repeated_neeq_page_is_a_visible_failure(monkeypatch, invalid):
    post = Mock(side_effect=[page(0, [filing("repeat")], last=False, total=2),
                             page(1, [invalid], last=True, total=2)])
    monkeypatch.setattr(neeq_fetcher.requests, "post", post)
    health = SourceHealth("NEEQ", "830806.NQ")
    with pytest.raises(SourceFetchError):
        neeq_fetcher.fetch_neeq_filings("830806.NQ", since_date="2026-10-26", until_date="2026-11-03", health=health, strict=True)
    assert health.status == "partial"
