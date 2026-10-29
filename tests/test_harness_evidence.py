"""Evidence authority and bounded sampling must not hide research gaps."""
from copy import deepcopy

import pytest

from research import harness
from research.cli import weekly
from research.evidence import build_card
from research.runtime import MemoryStore
from tests.test_evidence_cards import payload
from tests.test_research_runtime import holding


def weekly_card():
    store = MemoryStore()
    weekly({'holdings': [dict(holding(), active_tickers=['000001.SZ'])], 'unmapped': []},
           store, day='2026-10-29', slots=1)
    return store.list()[0]['card']


def disclosure_card(**updates):
    return build_card(payload(source_url='https://static.cninfo.com.cn/finalpage/report.pdf',
                              document_id='123456', **updates))


def test_real_weekly_scheduler_card_never_satisfies_primary_disclosure_gap():
    card = weekly_card()
    assert card['document_type'] == '公告' and card['missing'] == []
    brief = harness.build_brief('现金回款能否支持增长？', [card], [], None)
    assert 'primary_disclosure' in brief['missing']
    assert brief['source_classifications'][0]['kind'] == 'scheduling_metadata'


def test_public_primary_classifier_uses_provenance_and_explicit_source_roles():
    classify = getattr(harness, 'evidence_is_primary', None)
    assert callable(classify), 'report_view needs a shared public classifier'
    actual = disclosure_card()
    prior = dict(actual, evidence_kind='prior_research')
    weekly_with_official_link = weekly_card()
    weekly_with_official_link['source']['url'] = actual['source']['url']
    unknown = dict(actual, source=dict(actual['source'], url='https://research.invalid/report'))
    cases = [(actual, True), (prior, False), (weekly_card(), False),
             (weekly_with_official_link, False), (unknown, False),
             (disclosure_card(content='', facts=[]), False),
             ({'missing': []}, False),
             (dict(actual, kind='weekly_deep'), False)]
    for card, expected in cases:
        before = deepcopy(card)
        assert classify(card) is expected
        assert card == before


@pytest.mark.parametrize('host', ['static.cninfo.com.cn', 'www1.hkexnews.hk', 'www.sec.gov'])
def test_actual_collector_disclosures_are_primary_even_when_partial(host):
    card = disclosure_card()
    card['source']['url'] = 'https://' + host + '/filing.pdf'
    card['partial'] = True
    context = [{'revision_id': 'old-research', 'evidence_kind': 'prior_research',
                'source_text': '旧观点待核验', 'partial': True, 'missing': []}]
    brief = harness.build_brief('现金回款能否支持增长？', [weekly_card(), card], context, None)
    assert 'primary_disclosure' not in brief['missing']
    assert brief['partial_sources'] == [card['revision_id'], 'old-research']
    assert brief['source_classifications'][1]['kind'] == 'primary_disclosure'
    assert brief['source_classifications'][1]['partial'] is True


@pytest.mark.parametrize('fraction', [0.31, 0.63, 0.84])
def test_repeated_early_risk_does_not_hide_late_cashflow_counterevidence(fraction):
    text = ('风险提示：一般市场风险需要关注。\n' * 1600)
    text += '常规背景材料。\n' * 9000
    offset = int(len(text) * fraction)
    counter = '经营现金流由正转负，应收回款延迟，短期债务集中到期。'
    text = text[:offset] + counter + text[offset:]
    entry = {'revision_id': 'long-source', 'source_text': text,
             'evidence_kind': 'prior_research', 'partial': True}
    before = deepcopy(entry)
    out = harness.compact_context([entry])[0]
    assert counter in out['source_text']
    assert len(out['source_text']) <= 12000
    assert out['partial'] is True and out['prompt_excerpt_truncated'] is True
    assert entry == before
    excerpts = out['prompt_excerpts']
    assert any(e['source_start'] <= offset and e['source_end'] >= offset + len(counter)
               for e in excerpts)
    for e in excerpts:
        assert out['source_text'][e['prompt_start']:e['prompt_end']] == text[e['source_start']:e['source_end']]
    intervals = sorted([(e['source_start'], e['source_end']) for e in excerpts] +
                       [(e['source_start'], e['source_end']) for e in out['prompt_omissions']])
    assert intervals[0][0] == 0 and intervals[-1][1] == len(text)
    assert all(a[1] == b[0] for a, b in zip(intervals, intervals[1:]))
    assert 'not exhaustive' in out['prompt_excerpt_policy']


def test_long_document_without_keywords_still_samples_interior_locations():
    text = ''.join(f'第{i:05d}条：普通背景说明。\n' for i in range(9000))
    out = harness.compact_context([{'source_text': text}])[0]
    assert '第00000条' in out['source_text'] and '第08999条' in out['source_text']
    assert len(out['source_text']) <= 12000
    assert any(len(text) * .4 < e['source_start'] < len(text) * .6 for e in out['prompt_excerpts'])


def test_short_upstream_partial_source_remains_partial_without_invented_omissions():
    entry = {'revision_id': 'partial-extract', 'source_text': '仅提取第一页现金流说明', 'partial': True}
    out = harness.compact_context([entry])[0]
    assert out['partial'] is True and out['source_text'] == entry['source_text']
    assert out['prompt_omissions'] == []
    assert 'not exhaustive' in out['prompt_excerpt_policy']
