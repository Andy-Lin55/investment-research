"""Company configuration; identity is never inferred from report prose."""
from copy import deepcopy
from datetime import date
from financials.forecast import digest
from research.delivery import scoped_path


def resolve_profile(holding: dict, config: dict) -> dict:
    p=deepcopy(holding)
    for key in ('issuer_id','tickers','identity_confirmed','company_path'):
        if key in config and config[key]!=p.get(key):
            raise ValueError('configuration cannot change confirmed issuer identity')
    p.update(deepcopy(config))
    if (p.get('identity_confirmed') is not True or p.get('status')!='Active'
            or not isinstance(p.get('issuer_id'),str) or not p['issuer_id'].strip()
            or not isinstance(p.get('tickers'),list) or not p['tickers']
            or any(not isinstance(t,str) or not t.strip() for t in p['tickers'])
            or len(set(p['tickers']))!=len(p['tickers'])):
        raise ValueError('confirmed Active issuer and unique listings required')
    p['company_path']=scoped_path(p['company_path'])
    if len(p['company_path'].split('/'))<2:raise ValueError('company directory required')
    if not isinstance(p.get('research_question'),str) or not p['research_question'].strip():
        raise ValueError('company-specific research question required')
    end=p.setdefault('fiscal_year_end','12-31')
    date.fromisoformat('2000-'+end)
    p.setdefault('sector_template','industrial')
    p.setdefault('market','CN' if p['tickers'][0].endswith(('.SZ','.SH','.BJ')) else
                 'HK' if p['tickers'][0].endswith('.HK') else 'US')
    if p['market'] not in {'CN','HK','US'}:raise ValueError('unsupported disclosure market')
    p.setdefault('sources',['official'])
    if not isinstance(p['sources'],list) or not p['sources'] or any(not isinstance(s,str) or not s for s in p['sources']):
        raise ValueError('explicit source routes required')
    p['model_support']='supported' if p['sector_template']=='industrial' and end=='12-31' else 'unsupported_template'
    p['profile_version']=digest({k:v for k,v in p.items() if k!='profile_version'})
    return p


def forecast_years(profile: dict, as_of: str) -> list[int]:
    now=date.fromisoformat(as_of);month,day=map(int,profile['fiscal_year_end'].split('-'))
    year=now.year+((now.month,now.day)>(month,day))
    return [year,year+1,year+2]
