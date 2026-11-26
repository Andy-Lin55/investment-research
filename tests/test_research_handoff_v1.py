"""Synthetic cross-project contract tests; no private research or source PDFs."""
from hashlib import sha256
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from research.handoff_v1 import HandoffError, handoff_path, load_handoffs, validate_handoff
from research.contract_v17 import compact_material
from research.profiles import resolve_profile
from research.workflow import advance_task, plan_task
from research.runtime import MemoryStore
from research.v17 import build_adapters
from research.v17_live import load_prior_context
from tests.test_broker_sources import Drive
from tests.test_research_profiles import profile

FIXTURE = Path(__file__).parent / 'fixtures/handoff-v1/candidate.json'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode()


def candidate():
    return json.loads(FIXTURE.read_text())


def validate(value):
    raw = encoded(value)
    return validate_handoff(raw, issuer_id='issuer-a', tickers=['000001.SZ'],
                            research_cutoff='2026-11-02T23:59:59+00:00',
                            expected_sha256=sha256(raw).hexdigest())


def test_v1_schema_and_candidate_fixture_are_valid():
    schema = json.loads((FIXTURE.parents[3] / 'research/schemas/workbench-handoff-v1.schema.json').read_text())
    assert schema['$schema'].endswith('2020-12/schema')
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for name in ('candidate.json', 'no-baseline.json', 'dual-listed.json'):
        validator.validate(json.loads((FIXTURE.parent / name).read_text()))
    assert validate(candidate())['forecasts'][0]['value'] == '195.05'


@pytest.mark.parametrize(('mutation', 'code'), [
    (lambda d: d.update(schema_version='2.0'), 'unsupported_schema'),
    (lambda d: d.update(issuer_id='issuer-b'), 'handoff_issuer_mismatch'),
    (lambda d: d.update(knowledge_cutoff='2026-11-04T00:00:00+08:00'), 'handoff_after_research_cutoff'),
    (lambda d: d['source_index'][0].update(published_at='2026-11-01T00:00:00+08:00'), 'source_after_knowledge_cutoff'),
    (lambda d: d['theses'][0].update(evidence_refs=['missing']), 'unresolved_source_ref'),
    (lambda d: d['forecasts'][0].update(value=0), 'invalid_forecast_value'),
    (lambda d: d['forecasts'][0].update(period_start='2026-02-31'), 'invalid_forecast_period'),
    (lambda d: d.update(theses=[]), 'theses_required'),
    (lambda d: d['producer'].update(created_at='2026-10-29T22:00:00+08:00'), 'handoff_cutoff_after_creation'),
    (lambda d: d['monitoring'][0].update(due_date='2027-02-30'), 'invalid_monitoring_due_date'),
    (lambda d: d['source_index'][0].update(source_id='bad source'), 'invalid_source_id'),
    (lambda d: d['theses'][0].update(thesis_id='bad thesis'), 'invalid_thesis_id'),
    (lambda d: d['producer'].update(created_at='2026-10-31'), 'invalid_created_at'),
])
def test_rejects_invalid_cross_project_contract(mutation, code):
    value = candidate(); mutation(value)
    with pytest.raises(HandoffError, match='^' + code + '$'):
        validate(value)


def test_sha256_verifies_original_bytes_and_preserves_unknown_minor_fields():
    value = candidate(); value['schema_version'] = '1.1'; value['extra_future_field'] = {'a': 1}
    raw = encoded(value); expected = sha256(raw).hexdigest()
    assert validate_handoff(raw, issuer_id='issuer-a', tickers=['000001.SZ'],
                            research_cutoff='2026-11-02T23:59:59+00:00', expected_sha256=expected)['extra_future_field'] == {'a': 1}
    with pytest.raises(HandoffError, match='handoff_sha256_mismatch'):
        validate_handoff(raw + b' ', issuer_id='issuer-a', tickers=['000001.SZ'],
                         research_cutoff='2026-11-02T23:59:59+00:00', expected_sha256=expected)


def test_ambiguous_duplicate_json_keys_fail_closed():
    raw = FIXTURE.read_bytes().replace(b'"issuer_id": "issuer-a",',
                                      b'"issuer_id": "issuer-a", "issuer_id": "issuer-b",')
    with pytest.raises(HandoffError, match='invalid_handoff_json'):
        validate_handoff(raw, issuer_id='issuer-a', tickers=['000001.SZ'],
                         research_cutoff='2026-11-02T23:59:59+00:00', expected_sha256=sha256(raw).hexdigest())


def test_declared_accepted_does_not_enter_prompt_as_acceptance():
    p = resolve_profile(profile(), {}); d = Drive(); value = candidate(); value['review_status'] = 'accepted'
    raw = encoded(value); path = handoff_path(p, value['handoff_id'])
    d.put_immutable(path, raw)
    p['handoffs'] = [{'handoff_id': value['handoff_id'], 'path': path, 'sha256': sha256(raw).hexdigest()}]
    compact = compact_material({'coverage': {'prior_context': load_handoffs(p, d, '2026-11-02T23:59:59+00:00')}},
                               {'profile': p, 'trigger': {}})['coverage']['prior_context'][0]
    assert compact['review_status'] == compact['handoff_summary']['review_status'] == 'candidate'
    assert compact['handoff_summary']['declared_review_status'] == 'accepted'
    assert compact.get('declared_review_status') is None


def test_missing_legacy_fields_reject_without_guessing_history():
    legacy = json.loads((FIXTURE.parent / 'legacy-missing-fields.json').read_text())
    with pytest.raises(HandoffError, match='unsupported_schema'):
        validate(legacy)
    legacy['schema_version'] = '1.0'
    with pytest.raises(HandoffError, match='handoff_fields_missing'):
        validate(legacy)


def test_no_baseline_and_dual_listed_synthetic_fixtures():
    no_baseline = json.loads((FIXTURE.parent / 'no-baseline.json').read_text())
    assert validate(no_baseline)['theses'][0]['research_assumption']
    dual = json.loads((FIXTURE.parent / 'dual-listed.json').read_text())
    raw = encoded(dual)
    assert len(validate_handoff(raw, issuer_id='issuer-hk', tickers=['600001.SH', '0001.HK'],
                                research_cutoff='2026-11-02T23:59:59+00:00',
                                expected_sha256=sha256(raw).hexdigest())['securities']) == 2
    with pytest.raises(HandoffError, match='handoff_security_mismatch'):
        validate_handoff(raw, issuer_id='issuer-hk', tickers=['0001.HK'],
                         research_cutoff='2026-11-02T23:59:59+00:00',
                         expected_sha256=sha256(raw).hexdigest())


def test_company_scoped_import_is_candidate_even_if_producer_claims_accepted():
    p = resolve_profile(profile(), {}); d = Drive(); value = candidate(); value['review_status'] = 'accepted'
    raw = encoded(value); path = handoff_path(p, value['handoff_id'])
    d.put_immutable(path, raw)
    p['handoffs'] = [{'handoff_id':value['handoff_id'], 'path':path, 'sha256':sha256(raw).hexdigest()}]
    result = load_prior_context(p, d, None, research_cutoff='2026-11-02T23:59:59+00:00')
    item = result['items'][0]
    assert item['evidence_kind'] == 'prior_research' and item['review_status'] == 'candidate'
    assert item['declared_review_status'] == 'accepted' and item['handoff']['theses'][0]['thesis_id'] == 'cash-conversion'
    assert result['gaps'] == []
    compact = compact_material({'coverage': {'prior_context':result['items']}},
                               {'profile':p, 'trigger':{}})['coverage']['prior_context'][0]
    assert compact['handoff_summary']['theses'][0]['thesis_id'] == 'cash-conversion'
    assert 'forecasts' not in compact['handoff_summary'] and 'handoff' not in compact
    assert load_prior_context(resolve_profile(profile(), {}), Drive(), None) == {'items': [], 'gaps': []}


def test_no_handoff_requires_no_cutoff_but_handoff_requires_one():
    p = resolve_profile(profile(), {})
    assert load_prior_context(p, Drive(), None) == {'items': [], 'gaps': []}
    p['handoffs'] = [{'handoff_id': 'demo-issuer-a-v1'}]
    with pytest.raises(ValueError, match='handoff research cutoff required'):
        load_prior_context(p, Drive(), None)
    with pytest.raises(HandoffError, match='invalid_research_cutoff'):
        load_handoffs(p, Drive(), '2026-11-02')


def test_rejected_handoff_sets_visible_error_code_on_task():
    store = MemoryStore(); drive = Drive(); p = resolve_profile(profile(), {})
    value = candidate(); value['issuer_id'] = 'issuer-b'; raw = encoded(value)
    path = handoff_path(p, value['handoff_id'])
    drive.put_immutable(path, raw)
    p['handoffs'] = [{'handoff_id': value['handoff_id'], 'path': path, 'sha256': sha256(raw).hexdigest()}]
    trigger = {'issuer_id': 'issuer-a', 'event_id': 'synthetic-event', 'revision_id': 'synthetic-revision',
               'kind': 'earnings', 'published_at': '2026-11-02'}
    task = plan_task(trigger, p, {}, as_of='2026-11-02'); store.put(task)
    docs = [{'issuer_id': 'issuer-a', 'document_id': 'synthetic-official', 'revision_id': 'synthetic-sha',
             'document_type': '公告', 'published_at': '2026-11-01', 'pages': [{'page': 1, 'text': 'synthetic'}]}]
    adapters = build_adapters(store=store, drive=drive, reports=None, env={}, documents=lambda t: docs,
                              prior_context=lambda t: load_prior_context(t['profile'], drive, None,
                                  t['created_at'] + 'T00:00:00+00:00'))
    result = advance_task(task['task_id'], store=store, adapters=adapters, now='2026-11-02')
    assert result['status'] == 'retryable_failure'
    assert result['last_error_type'] == 'HandoffError'
    assert result['last_error_code'] == 'handoff_issuer_mismatch'
    assert result['stage_refs'] == {}


def test_wrong_company_path_and_shortcut_fail_closed():
    p = resolve_profile(profile(), {}); d = Drive(); raw = encoded(candidate())
    ref = {'handoff_id':'demo-issuer-a-v1', 'sha256':sha256(raw).hexdigest(), 'path':'投资/测试/issuer-b/handoff.json'}
    p['handoffs'] = [ref]
    with pytest.raises(HandoffError, match='handoff_path_mismatch'):
        load_handoffs(p, d, '2026-11-02T23:59:59+00:00')
    ref['path'] = handoff_path(p, ref['handoff_id']); d.put_immutable(ref['path'], raw)
    original = d.read
    d.read = lambda path: (original(path)[0], {'remoteItem':{}})
    with pytest.raises(HandoffError, match='handoff_shortcut_unmapped'):
        load_handoffs(p, d, '2026-11-02T23:59:59+00:00')
