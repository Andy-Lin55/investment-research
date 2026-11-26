"""Compare annual candidate forecasts with the most recent disclosed YTD actuals."""
from datetime import date

from financials.forecast import number

# Handoff metrics -> the keys `compare_with_reported_progress` reads.
_PROGRESS_METRICS = (
    ('revenue', 'income', 'revenue'),
    ('parent_net_income', 'income', 'parent_net_income'),
    ('operating_cashflow', 'cashflow', 'operating'),
)


def _scaled(value, unit_scale):
    """A forecast amount in the tracker's base unit, or None when unreadable.

    `unit_scale` is the multiplier from the stated value to the base unit. The
    tracker works in the `baseline` scale declared by the handoff, so every
    scenario row is brought to that same scale before comparison; mixing two
    scales would make a beat look like a miss by three orders of magnitude.

    The handoff carries amounts as decimal strings by contract, so they are
    parsed here rather than assumed to be floats.
    """
    try:
        amount = value if number(value) else float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not number(amount):
        return None
    try:
        scale = float(unit_scale)
    except (TypeError, ValueError):
        return None
    if not number(scale) or scale <= 0:
        return None
    return amount * scale


def handoff_forecasts_to_progress_model(handoff, *, profile):
    """Build the `{baseline, scenarios}` model the progress bridge consumes.

    Returns None when the handoff cannot support a same-period comparison — no
    reported-actual baseline, no annual base scenario, or inconsistent units.
    Guessing a baseline is worse than declining: the tracker would then grade a
    filing against a number nobody published.
    """
    if not isinstance(handoff, dict):
        return None
    issuer_id = profile.get('issuer_id') if isinstance(profile, dict) else None
    if not issuer_id or handoff.get('issuer_id') != issuer_id:
        return None
    currency = None
    forecasts = handoff.get('forecasts') or []
    annual = [row for row in forecasts
              if row.get('period_basis') == 'annual'
              and str(row.get('period_end') or '').endswith('-12-31')]
    if not annual:
        return None

    def rows_for(origin, scenario):
        return [row for row in annual
                if row.get('origin') == origin and row.get('scenario') == scenario]

    actuals = rows_for('reported_actual', 'actual')
    case_rows_by_case = {
        case: (rows_for('calculated', case) or rows_for('broker_forecast', case)
               or rows_for('research_assumption', case))
        for case in ('base', 'bull', 'bear')
    }
    base_rows = case_rows_by_case['base']
    if not base_rows:
        return None

    # Unit consistency: the baseline and every scenario row must share one
    # scale. Two scales in one handoff is a producer bug, not something to
    # reconcile silently here.
    all_case_rows = [row for rows in case_rows_by_case.values() for row in rows]
    scales = {str(row.get('unit_scale')) for row in actuals + all_case_rows
              if row.get('unit_scale') is not None}
    if len(scales) != 1:
        return None
    scale = next(iter(scales))
    currencies = {row.get('currency') for row in actuals + all_case_rows if row.get('currency')}
    if len(currencies) > 1:
        return None
    currency = next(iter(currencies), None) or 'CNY'

    def year_of(row):
        try:
            return int(str(row['period_end'])[:4])
        except (KeyError, ValueError):
            return None

    baseline_row = None
    for row in actuals:
        if row.get('metric') != 'revenue':
            continue
        year = year_of(row)
        if year is None:
            continue
        if baseline_row is None or year > year_of(baseline_row):
            baseline_row = row
    if baseline_row is None or year_of(baseline_row) is None:
        return None
    baseline_year = year_of(baseline_row)
    baseline = {'period_end': f'{baseline_year}-12-31', 'revenue': None,
                'parent_net_income': None, 'operating_cashflow': None}
    for row in actuals:
        if year_of(row) != baseline_year:
            continue
        metric = row.get('metric')
        if metric not in baseline:
            continue
        amount = _scaled(row.get('value'), row.get('unit_scale'))
        if amount is not None:
            baseline[metric] = amount
    if baseline.get('revenue') is None:
        return None

    scenarios = {}
    for case in ('base', 'bull', 'bear'):
        case_rows = case_rows_by_case[case]
        if not case_rows and case != 'base':
            continue
        by_year = {}
        for row in case_rows:
            year = year_of(row)
            if year is None or year <= baseline_year:
                continue
            metric = row.get('metric')
            target = next((entry for entry in _PROGRESS_METRICS if entry[0] == metric), None)
            if not target:
                continue
            amount = _scaled(row.get('value'), row.get('unit_scale'))
            if amount is None:
                continue
            _, table, key = target
            entry = by_year.setdefault(year, {
                'year': year, 'status': 'ready_for_review',
                'income': {'revenue': None, 'parent_net_income': None},
                'cashflow': {'operating': None},
            })
            entry[table][key] = amount
        ready = []
        for year in sorted(by_year):
            entry = by_year[year]
            if not any(value is not None for value in entry['income'].values()):
                continue
            ready.append(entry)
        if ready:
            scenarios[case] = ready
    if 'base' not in scenarios:
        return None
    return {
        'issuer_id': issuer_id, 'currency': currency, 'baseline': baseline,
        'scenarios': scenarios,
        # A producer-declared "accepted" is not an adoption decision; the
        # operator owns that, so the bridge always carries a candidate marker.
        'review_status': 'candidate',
        'source_handoff_id': handoff.get('handoff_id'),
    }


def compare_with_reported_progress(model, data, *, tickers, as_of):
    if not model or model.get('issuer_id')!=data.get('issuer_id'):
        return {}
    if not model.get('baseline',{}).get('period_end','').endswith('-12-31'):
        return {}
    cutoff=date.fromisoformat(as_of)
    year=cutoff.year
    statements={}
    for kind in ('income','cashflow'):
        records={}
        for record in data.get('statements',{}).get(kind,[]):
            period=record['period_end']
            if (record.get('ticker') in tickers and record.get('period_basis')=='year_to_date'
                    and record.get('scope')=='consolidated' and record.get('currency')==model['currency']
                    and record.get('period_start')==period[:4]+'-01-01'
                    and record.get('source_provider')=='Tushare' and record.get('value_kind')=='reported_provider'
                    and record.get('revision_id') and date.fromisoformat(record['published_at'])<=cutoff
                    and date.fromisoformat(period)<=cutoff):
                key=record['ticker'],period
                # An unexpected duplicate is not resolved by input order.
                records[key]=record if key not in records else None
        statements[kind]=records
    periods=[(period,ticker) for (ticker,period),record in statements['income'].items() if record
             and period[:4]==str(year) and period[5:] in {'03-31','06-30','09-30'}]
    if not periods:
        return {}
    period,ticker=max(periods)
    if sum(end==period for end,_ in periods)!=1:
        return {}
    prior_ytd=str(year-1)+period[4:]
    prior_annual=f'{year-1}-12-31'
    definitions=(('revenue','income','revenue','income','revenue'),
                 ('parent_net_income','income','n_income_attr_p','income','parent_net_income'),
                 ('operating_cashflow','cashflow','n_cashflow_act','cashflow','operating'))
    rows=[]
    for metric,kind,field,table,key in definitions:
        actual=statements[kind].get((ticker,period))
        if not actual or not number(actual['values'].get(field)):
            continue
        ytd=actual['values'][field]
        last_q=statements[kind].get((ticker,prior_ytd)) or {}
        last_fy=statements[kind].get((ticker,prior_annual)) or {}
        prev_values=[x.get('values',{}).get(field) for x in (last_fy,last_q)]
        prior=prev_values[0]-prev_values[1] if all(number(v) for v in prev_values) else None
        for case in ('base','bull','bear'):
            projected=next((r for r in model['scenarios'].get(case,[]) if r['year']==year),None)
            if not projected or projected['status'] not in {'ready_for_review','loss_valuation_pending'}:
                continue
            full_year=projected[table][key]
            if not number(full_year):
                continue
            rows.append(dict(metric=metric,case=case,reported_ytd=ytd,full_year=full_year,
                implied_remainder=full_year-ytd,prior_remainder=prior,source_ref=actual['revision_id'],
                prior_source_refs=[last_fy['revision_id'],last_q['revision_id']] if prior is not None else []))
    return dict(actual_period=period,currency=model['currency'],ticker=ticker,rows=rows,
                prior_actual_period=prior_ytd) if rows else {}
