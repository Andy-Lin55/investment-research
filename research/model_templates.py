"""Explicit capability routing; do not market an industrial model as universal."""
from financials.forecast import project


def select_model_template(profile: dict) -> dict:
    template=profile.get('sector_template','industrial')
    supported=template=='industrial' and profile.get('fiscal_year_end','12-31')=='12-31'
    return dict(template=template,status='supported' if supported else 'unsupported_template',
                valuation_methods=['earnings_multiple'] if supported else [],
                note='Other sectors/non-calendar fiscal years require a separately validated adapter.')


def calculate(profile,baseline,scenarios,*,as_of,previous=None):
    route=select_model_template(profile)
    if route['status']!='supported':return dict(status='unsupported_template',issuer_id=profile.get('issuer_id'),scenarios={})
    return project(baseline,scenarios,as_of=as_of,previous=previous)
