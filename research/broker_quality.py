"""Structural broker-analysis gates; investment judgment remains human-reviewed."""


def validate_broker_analysis(report,cards):
    brokers={c['revision_id']:c for c in cards if c.get('evidence_kind')=='sell_side_report' and c.get('broker') and not c.get('missing')}
    allowed={c['revision_id'] for c in cards if not c.get('missing')}
    if report.get('consensus_status')!='sampled_views_only':raise ValueError('sampled brokers are not market consensus')
    h=report.get('independent_hypothesis')
    if (not isinstance(h,dict) or any(not isinstance(h.get(k),str) or not h[k].strip()
            for k in ('claim','mechanism','falsifier')) or not isinstance(h.get('evidence'),list)
            or not h['evidence'] or any(not isinstance(x,str) or x not in allowed for x in h['evidence'])):
        raise ValueError('independent hypothesis and falsifier require evidence')
    comparisons=report.get('broker_comparisons');seen=set()
    if not isinstance(comparisons,list) or not 1<=len(comparisons)<=6:raise ValueError('bounded broker comparisons required')
    for row in comparisons:
        if not isinstance(row,dict):raise ValueError('structured broker comparison required')
        refs=row.get('evidence')
        if not isinstance(refs,list) or len(refs)!=1 or not isinstance(refs[0],str) or refs[0] not in brokers:
            raise ValueError('each broker comparison must cite one retained original')
        source=brokers[refs[0]];pages=row.get('pages')
        if (not isinstance(pages,list) or not 1<=len(pages)<=10
                or any(type(p) is not int or not 1<=p<=source['broker']['total_pages']
                    or '[PDF page '+str(p)+']' not in source.get('source_text','') for p in pages)):
            raise ValueError('broker comparison requires available original pages')
        if any(not isinstance(row.get(k),str) or not row[k].strip()
               for k in ('broker_view','own_view','model_implication','verification_needed')):
            raise ValueError('broker comparison must connect to independent analysis and verification')
        seen.add(refs[0])
    if seen!=set(brokers):raise ValueError('each supplied broker original must be compared')
