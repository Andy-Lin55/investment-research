"""Compare annual candidate forecasts with the most recent disclosed YTD actuals."""
from datetime import date

from financials.forecast import number


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
