"""Explicit, sourced industrial forecast; no financing plugs or invented inputs."""
from copy import deepcopy
from datetime import date
import hashlib
import json
import math

BASE_FIELDS = ('revenue','cash','receivables','inventory','ppe','other_assets','payables','debt',
               'other_liabilities','equity','shares')
DRIVERS = ('growth','cogs_ratio','opex_ratio','tax_rate','dep_rate','capex_ratio','receivable_days',
           'inventory_days','payable_days','interest_rate','dividend_payout','debt_change',
           'equity_issuance','share_change','fx_cash','other_equity_change','minority_ratio','pe_multiple')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def number(value):
    return not isinstance(value,bool) and isinstance(value,(int,float)) and math.isfinite(value)


def project(baseline, scenarios, *, as_of, previous=None):
    baseline, scenarios = deepcopy(baseline), deepcopy(scenarios)
    if (baseline.get('amount_unit')!=baseline.get('currency') or baseline.get('share_unit')!='shares'
            or baseline.get('amount_scale',1)!=1 or baseline.get('share_scale',1)!=1):
        raise ValueError('explicit raw currency amount units and unscaled share counts required')
    cutoff, end = date.fromisoformat(as_of), date.fromisoformat(baseline['period_end'])
    if (baseline.get('sector_template','industrial') != 'industrial' or end > cutoff
            or (end.month,end.day)!=(12,31) or baseline.get('cash_scope_confirmed') is not True
            or any(not baseline.get(k) for k in ('issuer_id','currency','revision_id','source_locator'))
            or any(not number(baseline.get(k)) for k in BASE_FIELDS)):
        raise ValueError('industrial annual baseline, source and confirmed cash scope required')
    assets=sum(baseline[k] for k in ('cash','receivables','inventory','ppe','other_assets'))
    liabilities=sum(baseline[k] for k in ('payables','debt','other_liabilities','equity'))
    if abs(assets-liabilities)>1e-6 or baseline['shares']<=0 or baseline['revenue']<=0:
        raise ValueError('baseline balance, revenue or shares invalid')
    if not isinstance(scenarios,dict) or set(scenarios)!={'base','bull','bear'}:
        raise ValueError('base/bull/bear scenarios required')
    missing=[]
    horizons=[]
    for name,years in scenarios.items():
        if not isinstance(years,list) or not years: raise ValueError('forecast years required')
        horizons.append([r['year'] for r in years])
        for i,row in enumerate(years):
            if isinstance(row['year'],bool) or row['year']!=end.year+i+1:
                raise ValueError('consecutive annual forecast years required')
            for field in DRIVERS:
                value=row.get('values',{}).get(field)
                source=row.get('sources',{}).get(field)
                if not number(value) or not isinstance(source,str) or not source.strip():
                    missing.append(f'{name}.{row["year"]}.{field}')
                    continue
                if (field in {'cogs_ratio','opex_ratio','tax_rate','dep_rate','dividend_payout','minority_ratio'} and not 0<=value<=1
                        or field=='growth' and value<=-1
                        or field in {'capex_ratio','receivable_days','inventory_days','payable_days','interest_rate','pe_multiple'} and value<0):
                    raise ValueError(f'invalid forecast driver {field}')
    if any(x!=horizons[0] for x in horizons):raise ValueError('scenario horizons must match')
    result=dict(schema_version=1,issuer_id=baseline['issuer_id'],currency=baseline['currency'],
                as_of=as_of,baseline=baseline,baseline_revision=baseline['revision_id'],assumptions=scenarios,
                model_version=digest([baseline,scenarios,as_of]),human_review='pending',
                status='pending_assumptions' if missing else 'ready_for_review',missing=missing,
                scenarios={},assumption_changes=[])
    if previous:
        if previous['issuer_id']!=baseline['issuer_id'] or previous['currency']!=baseline['currency']:
            raise ValueError('previous model identity/currency mismatch')
        for name,years in scenarios.items():
            old={r['year']:r for r in previous['assumptions'].get(name,[])}
            for row in years:
                for field,value in row.get('values',{}).items():
                    before=old.get(row['year'],{}).get('values',{}).get(field)
                    if value!=before:
                        result['assumption_changes'].append(dict(scenario=name,year=row['year'],field=field,
                                                                 previous=before,current=value,source=row['sources'].get(field)))
    if missing:return result
    for name,years in scenarios.items():
        b=deepcopy(baseline);projected=[]
        for row in years:
            a=row['values']; revenue=b['revenue']*(1+a['growth'])
            cogs=revenue*a['cogs_ratio'];opex=revenue*a['opex_ratio'];dep=b['ppe']*a['dep_rate']
            debt=b['debt']+a['debt_change'];shares=b['shares']+a['share_change']
            if debt<0 or shares<=0:raise ValueError('negative debt or nonpositive projected shares')
            if bool(a['equity_issuance']) != bool(a['share_change']):
                raise ValueError('equity issuance and share changes must both be explicit')
            interest=(b['debt']+debt)/2*a['interest_rate'];pretax=revenue-cogs-opex-dep-interest
            tax=max(pretax,0)*a['tax_rate'];profit=pretax-tax
            ar=revenue*a['receivable_days']/365;inventory=cogs*a['inventory_days']/365;ap=cogs*a['payable_days']/365
            wc=(ar-b['receivables'])+(inventory-b['inventory'])-(ap-b['payables'])
            cfo=profit+dep-wc;capex=revenue*a['capex_ratio'];dividends=max(profit,0)*a['dividend_payout']
            cfi=-capex;cff=a['debt_change']+a['equity_issuance']-dividends
            cash=b['cash']+cfo+cfi+cff+a['fx_cash']
            # Noncash FX/OCI effects require explicit other_equity_change; they
            # are never silently solved as a balancing item.
            equity=b['equity']+profit-dividends+a['equity_issuance']+a['other_equity_change']
            balance=dict(cash=cash,receivables=ar,inventory=inventory,ppe=b['ppe']+capex-dep,
                         other_assets=b['other_assets'],payables=ap,debt=debt,
                         other_liabilities=b['other_liabilities'],equity=equity,shares=shares)
            if balance['ppe']<0:raise ValueError('negative projected fixed assets')
            assets=sum(balance[k] for k in ('cash','receivables','inventory','ppe','other_assets'))
            liabilities=sum(balance[k] for k in ('payables','debt','other_liabilities','equity'))
            difference=assets-liabilities
            parent_profit=profit*(1-a['minority_ratio']);eps=parent_profit/((b['shares']+shares)/2)
            projected.append(dict(year=row['year'],value_kind='forecast_assumption',
                                  income=dict(revenue=revenue,cogs=cogs,opex=opex,depreciation=dep,interest=interest,
                                              pretax=pretax,tax=tax,net_income=profit,parent_net_income=parent_profit,eps=eps),
                                  balance=balance,cashflow=dict(operating=cfo,investing=cfi,financing=cff,
                                      fx=a['fx_cash'],capex=capex,dividends=dividends,change=cash-b['cash']),
                                  checks=dict(balance_difference=difference,cash_difference=cash-b['cash']-cfo-cfi-cff-a['fx_cash']),
                                  valuation=dict(method='earnings_multiple',price=eps*a['pe_multiple'] if eps>0 else None,
                                                 currency=baseline['currency'],multiple=a['pe_multiple']),
                                  funding_shortfall=max(0,-cash),
                                  status='unbalanced' if abs(difference)>1e-6 else 'funding_required' if cash<0
                                         else 'loss_valuation_pending' if eps<=0 else 'ready_for_review'))
            b=dict(balance,revenue=revenue)
        result['scenarios'][name]=projected
    if any(r['status']!='ready_for_review' for rows in result['scenarios'].values() for r in rows):
        result['status']='needs_model_review'
    return result
