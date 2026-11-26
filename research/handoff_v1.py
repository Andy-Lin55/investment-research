"""Validate immutable Workbench handoffs before admitting historical context."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from research.delivery import scoped_path

SHA256 = re.compile(r'^[0-9a-f]{64}$')
IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
FORECAST_FIELDS = ('metric', 'period_start', 'period_end', 'period_basis', 'value', 'currency',
                   'unit_scale', 'scope', 'ownership_basis', 'origin', 'scenario', 'source_refs')


class HandoffError(ValueError):
    """An explicit rejection of an untrusted cross-project input."""


def _require(condition, code):
    if not condition:
        raise HandoffError(code)


def _time(value, field):
    try:
        if not isinstance(value, str) or not re.fullmatch(
                r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})', value):
            raise ValueError('RFC3339 timestamp required')
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError('timezone missing')
        return stamp
    except (TypeError, ValueError):
        raise HandoffError('invalid_' + field) from None


def _day(value, field):
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError('ISO date required')
        return date.fromisoformat(value)
    except ValueError:
        raise HandoffError('invalid_' + field) from None


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate JSON key')
        value[key] = item
    return value


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _decimal(value, field):
    _require(isinstance(value, str) and bool(re.fullmatch(r'-?(?:0|[1-9]\d*)(?:\.\d+)?', value)), 'invalid_' + field)
    try:
        _require(Decimal(value).is_finite(), 'invalid_' + field)
    except InvalidOperation:
        raise HandoffError('invalid_' + field) from None


def _json_depth(value):
    if isinstance(value, dict):
        return 1 + max((_json_depth(v) for v in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_json_depth(v) for v in value), default=0)
    return 0


def validate_handoff(raw, *, issuer_id, tickers, research_cutoff, expected_sha256):
    """Read original bytes; return only validated JSON, never an adoption decision."""
    _require(isinstance(raw, bytes) and 0 < len(raw) <= 2_000_000, 'handoff_size_invalid')
    _require(isinstance(expected_sha256, str) and SHA256.fullmatch(expected_sha256), 'invalid_expected_sha256')
    _require(sha256(raw).hexdigest() == expected_sha256, 'handoff_sha256_mismatch')
    try:
        data = json.loads(raw, object_pairs_hook=_unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise HandoffError('invalid_handoff_json') from None
    _require(isinstance(data, dict), 'invalid_handoff_json')
    _require(isinstance(issuer_id, str) and bool(issuer_id) and isinstance(tickers, (list, tuple))
             and bool(tickers), 'invalid_expected_identity')
    try:
        _require(_json_depth(data) <= 24, 'handoff_nesting_exceeded')
    except RecursionError:
        raise HandoffError('handoff_nesting_exceeded') from None
    version = data.get('schema_version')
    _require(isinstance(version, str) and version.split('.')[0] == '1', 'unsupported_schema')
    _require(re.fullmatch(r'1\.\d+', version) is not None, 'invalid_schema_version')
    required = {'schema_version', 'handoff_id', 'issuer_id', 'securities', 'producer',
                'knowledge_cutoff', 'parent_handoff_id', 'research_question', 'theses',
                'forecasts', 'valuation', 'disagreements', 'monitoring', 'source_index',
                'artifacts', 'gaps', 'review_status'}
    _require(required <= data.keys(), 'handoff_fields_missing')
    _require(_text(data.get('handoff_id')) and IDENTIFIER.fullmatch(data['handoff_id']), 'invalid_handoff_id')
    _require(data.get('issuer_id') == issuer_id, 'handoff_issuer_mismatch')
    securities = data.get('securities')
    _require(isinstance(securities, list) and bool(securities) and len(securities) <= 20, 'securities_required')
    for security in securities:
        _require(isinstance(security, dict) and all(_text(security.get(k)) for k in ('market', 'ticker', 'currency', 'share_class')),
                 'invalid_security')
        _require(security['ticker'] in tickers, 'handoff_security_mismatch')
    producer = data.get('producer')
    _require(isinstance(producer, dict) and all(_text(producer.get(k)) for k in ('project', 'run_id', 'git_commit', 'created_at')),
             'invalid_producer')
    created = _time(producer['created_at'], 'created_at')
    cutoff = _time(data.get('knowledge_cutoff'), 'knowledge_cutoff')
    research_time = _time(research_cutoff, 'research_cutoff')
    _require(cutoff <= research_time and created <= research_time, 'handoff_after_research_cutoff')
    _require(cutoff <= created, 'handoff_cutoff_after_creation')
    _require(data.get('parent_handoff_id') is None or
             (_text(data['parent_handoff_id']) and IDENTIFIER.fullmatch(data['parent_handoff_id'])), 'invalid_parent_handoff_id')
    _require(data.get('parent_handoff_id') != data['handoff_id'], 'invalid_parent_handoff_id')
    _require(_text(data.get('research_question')), 'research_question_required')
    _require(isinstance(data.get('review_status'), str) and
             data['review_status'] in ('candidate', 'accepted', 'rejected'), 'invalid_review_status')
    sources = data.get('source_index')
    _require(isinstance(sources, list) and bool(sources), 'source_index_required')
    ids = set()
    for source in sources:
        _require(isinstance(source, dict) and all(_text(source.get(k)) for k in
                 ('source_id', 'revision_id', 'category', 'published_at', 'locator', 'storage_uri')), 'invalid_source')
        _require(IDENTIFIER.fullmatch(source['source_id']) is not None, 'invalid_source_id')
        _require(SHA256.fullmatch(source['revision_id']), 'invalid_source_revision')
        _require(source['source_id'] not in ids, 'duplicate_source_id')
        _require(_time(source['published_at'], 'source_published_at') <= cutoff, 'source_after_knowledge_cutoff')
        _require(_text(source.get('version_summary')), 'source_version_summary_required')
        ids.add(source['source_id'])
    theses = data.get('theses')
    _require(isinstance(theses, list) and bool(theses) and len(theses) <= 100, 'theses_required')
    _require(len(sources) <= 300, 'source_index_bounds_exceeded')
    thesis_ids = set()
    for thesis in theses:
        _require(isinstance(thesis, dict) and all(_text(thesis.get(k)) for k in
                 ('thesis_id', 'claim', 'driver', 'falsifier', 'status')) and
                 isinstance(thesis.get('evidence_refs'), list), 'invalid_thesis')
        _require(IDENTIFIER.fullmatch(thesis['thesis_id']) is not None, 'invalid_thesis_id')
        _require(thesis['thesis_id'] not in thesis_ids, 'duplicate_thesis_id')
        thesis_ids.add(thesis['thesis_id'])
        _require(bool(thesis['evidence_refs']) or _text(thesis.get('research_assumption')), 'thesis_unsubstantiated')
        _refs(thesis['evidence_refs'], ids)
    for field in ('forecasts', 'disagreements', 'monitoring', 'artifacts', 'gaps'):
        _require(isinstance(data.get(field), list) and len(data[field]) <= 300, 'invalid_' + field)
    for forecast in data['forecasts']:
        _require(isinstance(forecast, dict) and all(k in forecast for k in FORECAST_FIELDS), 'invalid_forecast')
        _require(all(_text(forecast[k]) for k in FORECAST_FIELDS if k not in ('value', 'source_refs')), 'invalid_forecast')
        try:
            start, end = (_day(forecast[k], 'forecast_period') for k in ('period_start', 'period_end'))
        except HandoffError:
            raise HandoffError('invalid_forecast_period') from None
        _require(start <= end, 'invalid_forecast_period')
        _require(forecast['value'] is None or isinstance(forecast['value'], str), 'invalid_forecast_value')
        if forecast['value'] is not None:
            _decimal(forecast['value'], 'forecast_value')
        _decimal(forecast['unit_scale'], 'unit_scale')
        _require(isinstance(forecast['period_basis'], str) and isinstance(forecast['origin'], str) and
                 forecast['period_basis'] in ('annual', 'year_to_date', 'quarter', 'instant') and
                 forecast['origin'] in ('reported_actual', 'management_guidance', 'broker_forecast', 'research_assumption', 'calculated'), 'invalid_forecast_dimensions')
        _refs(forecast['source_refs'], ids)
        if forecast['origin'] != 'research_assumption':
            _require(bool(forecast['source_refs']), 'forecast_source_required')
    for entry in data['monitoring']:
        _require(isinstance(entry, dict) and any(
                 _text(entry.get(k)) and IDENTIFIER.fullmatch(entry[k]) for k in ('question_id', 'metric_id')) and
                 all(_text(entry.get(k)) for k in ('condition', 'due_date', 'verification', 'action')), 'invalid_monitoring')
        for key in ('question_id', 'metric_id'):
            _require(key not in entry or (_text(entry[key]) and IDENTIFIER.fullmatch(entry[key]) is not None),
                     'invalid_monitoring')
        _day(entry['due_date'], 'monitoring_due_date')
    for entry in data['gaps']:
        _require(isinstance(entry, dict) and all(_text(entry.get(k)) for k in ('missing', 'impact', 'needed_evidence')), 'invalid_gap')
    for entry in data['artifacts']:
        _require(isinstance(entry, dict) and all(_text(entry.get(k)) for k in ('role', 'uri', 'sha256')) and
                 SHA256.fullmatch(entry['sha256']), 'invalid_artifact')
    valuation = data.get('valuation')
    _require(valuation is None or isinstance(valuation, dict), 'invalid_valuation')
    if valuation is not None:
        _refs(valuation.get('source_refs', []), ids)
    for item in data['disagreements']:
        _require(isinstance(item, dict), 'invalid_disagreement')
        _refs(item.get('source_refs', []), ids)
    return data


def _refs(refs, ids):
    _require(isinstance(refs, list) and all(isinstance(ref, str) and ref in ids for ref in refs), 'unresolved_source_ref')


def handoff_path(profile, handoff_id):
    _require(_text(handoff_id) and IDENTIFIER.fullmatch(handoff_id), 'invalid_handoff_id')
    return scoped_path(profile['company_path'] + '/04_研究底稿/Workbench交接/' + handoff_id + '/handoff.json')


def load_handoffs(profile, drive, research_cutoff):
    """Manifest hashes are external to the handoff; self-declared accepted is untrusted."""
    entries = profile.get('handoffs', [])
    _require(isinstance(entries, list) and len(entries) <= 10, 'handoff_bounds_exceeded')
    _time(research_cutoff, 'research_cutoff')
    result = []
    seen = set()
    for entry in entries:
        _require(isinstance(entry, dict), 'invalid_handoff_reference')
        path = handoff_path(profile, entry.get('handoff_id'))
        _require(path not in seen, 'duplicate_handoff_reference')
        seen.add(path)
        _require(entry.get('path') == path, 'handoff_path_mismatch')
        raw, meta = drive.read(path)
        _require(isinstance(meta, dict), 'invalid_handoff_metadata')
        _require('remoteItem' not in meta, 'handoff_shortcut_unmapped')
        handoff = validate_handoff(raw, issuer_id=profile['issuer_id'], tickers=profile['tickers'],
                                   research_cutoff=research_cutoff, expected_sha256=entry.get('sha256'))
        _require(handoff['handoff_id'] == entry['handoff_id'], 'handoff_id_mismatch')
        result.append(dict(revision_id=entry['sha256'], evidence_kind='prior_research',
                           handoff=handoff, source_text=json.dumps(handoff, ensure_ascii=False, sort_keys=True),
                           partial=False, source={'path':path, 'url':meta.get('webUrl'), 'sha256':entry['sha256']},
                           handoff_id=handoff['handoff_id'], producer=handoff['producer'],
                           review_status='candidate', declared_review_status=handoff['review_status'],
                           knowledge_cutoff=handoff['knowledge_cutoff'],
                           source_index=handoff['source_index'],
                           note='Historical research candidate, not current disclosed facts or accepted baseline.'))
    return result
