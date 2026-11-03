"""Source-backed category mapping, not forecasts or hidden balancing plugs."""
from copy import deepcopy
from datetime import date
import math
from financials.forecast import BASE_FIELDS,digest,number
from financials.statements import reconcile

DEBT_FIELDS=('st_borr','lt_borr','bond_payable','non_cur_liab_due_1y')


def map_standard_baseline(data,profile,*,as_of):
    out=dict(status='partial',baseline=None,lineage={},observations={},cash_bridge={},gaps=[],
        mapping_version=1,limitations=[
            '债务定义为借款、应付债券及一年内到期非流动负债；未单列租赁等负债保留在其他负债，不代表完整净债务。',
            '其他资产和其他负债是已披露总额扣除明确分类的历史余额，不能用来平衡未来预测。',
            '标准模型采用简化经营分类；收购、处置及其他重大投资融资事件需要另行拆解，不据此声称已完整建模。'])
    if not data.get('statements'):out['gaps']=['normalized_three_statements_unavailable'];return out
    if data.get('issuer_id')!=profile['issuer_id']:raise ValueError('financial issuer differs from confirmed profile')
    if profile.get('sector_template','industrial')!='industrial' or profile.get('fiscal_year_end','12-31')!='12-31':
        out['gaps']=['unsupported_forecast_template'];return out
    cutoff=date.fromisoformat(as_of);statements=data['statements']
    annual=sorted({r['period_end'] for rows in statements.values() for r in rows
        if r['period_end'].endswith('-12-31') and date.fromisoformat(r['period_end'])<=cutoff})
    if not annual:out['gaps']=['annual_baseline_unavailable'];return out
    period=annual[-1];records={}
    for kind in ('income','balancesheet','cashflow'):
        matches=[r for r in statements.get(kind,[]) if r['period_end']==period]
        if not matches:out['gaps'].append(kind+':'+period);continue
        if len(matches)!=1:raise ValueError('ambiguous normalized annual record')
        r=matches[0]
        if (r.get('ticker') not in profile['tickers'] or r.get('scope')!='consolidated'
                or r.get('currency')!='CNY' or r.get('company_type')!='1'
                or r.get('value_kind')!='reported_provider' or r.get('source_provider')!='Tushare'
                or date.fromisoformat(r['published_at'])>cutoff
                or r['revision_id']!=digest({k:v for k,v in r.items() if k!='revision_id'})):
            raise ValueError('normalized annual identity, scope, date or checksum differs')
        if kind!='balancesheet' and (r.get('period_basis')!='year_to_date' or r.get('period_start')!=period[:4]+'-01-01'):
            raise ValueError('annual flow basis differs')
        if kind=='balancesheet' and r.get('period_basis')!='instant':raise ValueError('balance date basis differs')
        records[kind]=r
    if out['gaps']:return out
    checks=reconcile({'statements':{k:[r] for k,r in records.items()},'annual_periods':[period],'recent_periods':[]})
    out['annual_reconciliation']=checks
    out['gaps']=['annual_reconciliation:'+check['check'] for check in checks
        if check['status']=='fail' or (check['check']=='cash_rollforward' and check['status']!='pass')]
    def term(kind,field,coefficient=1):
        r=records[kind];value=r['values'].get(field)
        unit='shares' if field=='total_share' else 'CNY'
        if not number(value):
            out['gaps'].append(kind+'.'+field);return None
        if r['units'].get(field)!=unit:raise ValueError('raw financial unit differs')
        return dict(statement=kind,field=field,value=value,coefficient=coefficient,
            source_ref=r['revision_id'],source_locator=r['source_locator']+'/'+field,unit=unit,period_end=period)
    def group(name,terms,formula):
        if any(t is None for t in terms):return None
        value=math.fsum(t['value']*t['coefficient'] for t in terms)
        out['lineage'][name]=dict(formula=formula,terms=terms,value=value)
        return value
    revenue_field='revenue' if number(records['income']['values'].get('revenue')) else 'total_revenue'
    sources={'revenue':[('income',revenue_field)],'cash':[('cashflow','c_cash_equ_end_period')],
        'receivables':[('balancesheet','accounts_receiv')],'inventory':[('balancesheet','inventories')],
        'ppe':[('balancesheet','fix_assets')],'payables':[('balancesheet','accounts_pay')],
        'equity':[('balancesheet','total_hldr_eqy_inc_min_int')],'shares':[('balancesheet','total_share')],
        'debt':[('balancesheet',f) for f in DEBT_FIELDS]}
    values={name:group(name,[term(k,f) for k,f in fields],'+'.join(k+'.'+f for k,f in fields)) for name,fields in sources.items()}
    total_assets=term('balancesheet','total_assets');total_liab=term('balancesheet','total_liab')
    asset_terms=[total_assets,term('cashflow','c_cash_equ_end_period',-1),
        *[term('balancesheet',f,-1) for f in ('accounts_receiv','inventories','fix_assets')]]
    liability_terms=[total_liab,term('balancesheet','accounts_pay',-1),*[term('balancesheet',f,-1) for f in DEBT_FIELDS]]
    values['other_assets']=group('other_assets',asset_terms,'total_assets - cash_equivalents - receivables - inventory - fixed_assets')
    values['other_liabilities']=group('other_liabilities',liability_terms,'total_liabilities - accounts_payable - defined_debt')
    out['gaps']=sorted(set(out['gaps']))
    if out['gaps']:return out
    if abs(total_assets['value']-total_liab['value']-values['equity'])>.01:
        out['gaps']=['reported_balance_does_not_reconcile'];return out
    if any(values[k]<-.01 for k in BASE_FIELDS) or values['shares']<=0 or values['revenue']<=0:
        out['gaps']=['invalid_baseline_category'];return out
    monetary=records['balancesheet']['values'].get('money_cap')
    out['cash_bridge']=dict(cash_equivalents=values['cash'],monetary_funds=monetary,
        monetary_funds_less_cash_equivalents=monetary-values['cash'] if number(monetary) else None,
        note='只作口径桥接；差额已包含在其他资产，不能当作可用现金。')
    baseline=dict(values,issuer_id=profile['issuer_id'],currency='CNY',period_end=period,
        amount_unit='CNY',share_unit='shares',amount_scale=1,share_scale=1,sector_template='industrial',
        cash_scope_confirmed=True,costs_include_depreciation=True,standard_model_version=1,
        finance_cost_scope='net_finance_expense_proxy',
        revision_id=digest([r['revision_id'] for r in records.values()]+[out['mapping_version']]),
        source_locator='; '.join(r['source_locator'] for r in records.values()))
    inc=records['income']['values'];cf=records['cashflow']['values']
    def observation(name,value,fields,note='历史观察值，不是未来假设。'):
        if not number(value):return
        out['observations'][name]=dict(value=value,period_end=period,kind='historical_observation',note=note,
            source_refs=sorted({records[k]['revision_id'] for k,_ in fields}),fields=[k+'.'+f for k,f in fields])
    rev=values['revenue'];cost=inc.get('oper_cost');expense=[inc.get(f) for f in ('sell_exp','admin_exp','rd_exp')]
    if number(cost):observation('cogs_ratio',cost/rev,[('income','oper_cost')],'已包含报表成本内折旧；不能再从利润重复扣减。')
    if all(number(x) for x in expense):observation('opex_ratio',sum(expense)/rev,[('income',f) for f in ('sell_exp','admin_exp','rd_exp')])
    if number(cost) and all(number(x) for x in [*expense,inc.get('fin_exp'),inc.get('total_profit')]):
        other=inc['total_profit']-(rev-cost-sum(expense)-inc['fin_exp'])
        observation('other_pretax_income',other,[('income',f) for f in (revenue_field,'oper_cost','sell_exp','admin_exp','rd_exp','fin_exp','total_profit')],
            '历史其他税前净损益的算术桥接，含投资收益、税金、减值等；未来金额及现金归属需要独立研究假设。')
    if number(inc.get('fin_exp')) and values['debt']>0:
        observation('interest_rate',inc['fin_exp']/values['debt'],[('income','fin_exp'),*sources['debt']],
            '净财务费用／期末所定义债务的代理比率，含汇兑等影响，不是借款合同利率。')
    ratios={'tax_rate':(inc.get('income_tax'),inc.get('total_profit'),'income','income_tax'),
        'minority_ratio':(inc.get('minority_gain'),inc.get('n_income'),'income','minority_gain'),
        'dep_rate':(cf.get('depr_fa_coga_dpba'),values['ppe'],'cashflow','depr_fa_coga_dpba'),
        'capex_ratio':(cf.get('c_pay_acq_const_fiolta'),rev,'cashflow','c_pay_acq_const_fiolta')}
    for name,(numerator,denominator,kind,field) in ratios.items():
        if number(numerator) and number(denominator) and denominator>0:observation(name,numerator/denominator,[(kind,field)])
    for name,key,denom in [('receivable_days','receivables',rev),('inventory_days','inventory',cost),('payable_days','payables',cost)]:
        if number(denom) and denom>0:observation(name,values[key]/denom*365,sources[key])
    parents=inc.get('n_income_attr_p');eps=inc.get('basic_eps')
    if number(parents) and number(eps) and eps>0:
        observation('inferred_weighted_average_shares',parents/eps,[('income','n_income_attr_p'),('income','basic_eps')],
            '仅由归母净利／已四舍五入EPS推算的股数观察值，不替代经核验的加权股数及发行时点。')
    out.update(status='ready',baseline=baseline,baseline_digest=digest(baseline));return out


def expand_driver_cases(cases):
    if not isinstance(cases,dict) or len(cases)>32:raise ValueError('bounded driver cases required')
    out=[]
    for field,entry in cases.items():
        if not isinstance(entry,dict) or not entry.get('rationale') or not entry.get('source_refs'):
            raise ValueError('driver rationale and source references required')
        rows=entry.get('cases',{})
        if set(rows)!={'base','bull','bear'}:raise ValueError('three explicit driver cases required')
        for scenario,years in rows.items():
            if not isinstance(years,dict) or not 1<=len(years)<=5:raise ValueError('bounded annual cases required')
            for year,row in years.items():
                if not str(year).isdigit() or not isinstance(row,dict):raise ValueError('explicit annual driver row required')
                out.append(dict(field=field,scenario=scenario,year=int(year),value=row.get('value'),range=row.get('range'),
                    rationale=entry['rationale'],source_refs=deepcopy(entry['source_refs']),kind='research_assumption'))
    return out
