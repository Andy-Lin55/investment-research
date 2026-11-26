from importlib import import_module,util
from research.runtime import MemoryStore


def api():
    assert util.find_spec('research.retrieval'), 'bounded multi-source retrieval is not implemented'
    return import_module('research.retrieval')


def paper(key,issuer='issuer-a',org='broker-a'):
    return dict(report_id=key,issuer_id=issuer,org_name=org,published_at='2026-09-20',
        original_date='2026-09-18',index_date='2026-09-20',original_verified=True,
        source_quality='original',original_sha256='sha-'+str(key),pages=[{'page':1,'text':'original'}])


def test_wrong_issuer_duplicates_and_quarantined_copy_do_not_count_as_originals():
    inventory=[paper('1'),paper('1'),paper('2','wrong'),dict(paper('3'),source_quality='quarantined')]
    plan=api().plan_retrieval({'issuer_id':'issuer-a','question':'现金？','queries':[]},inventory,
        limits={'max_reports':5,'max_queries':6,'max_rounds':2})
    out=api().run_retrieval(plan,{},MemoryStore())
    assert [x['report_id'] for x in out['reports']]==['1']
    assert out['reports'][0]['original_date']=='2026-09-18'
    assert out['coverage_status']=='limited_institution_sample'
    assert len(out['rejected'])==2


def test_more_than_two_originals_can_be_used_without_downloading_cached_documents_again():
    plan=api().plan_retrieval({'issuer_id':'issuer-a','question':'回报？','queries':['issuer-a']},[],
        limits={'max_reports':4,'max_queries':6,'max_rounds':2})
    acquisitions=[]
    def acquire(row):acquisitions.append(row['report_id']);return paper(row['report_id'],org='broker-'+row['report_id'])
    adapters={'search':lambda query:[{'report_id':str(i)} for i in range(5)],'acquire':acquire}
    journal=MemoryStore();out=api().run_retrieval(plan,adapters,journal)
    assert len(out['reports'])==4 and len(acquisitions)==4
    second=api().run_retrieval(plan,adapters,journal)
    assert len(second['reports'])==4 and len(acquisitions)==4


def test_unknown_acquisition_result_remains_reserved_and_is_not_retried():
    plan=api().plan_retrieval({'issuer_id':'issuer-a','question':'利润？','queries':['q']},[],
        limits={'max_reports':3,'max_queries':1,'max_rounds':1})
    count=[]
    def lost(row):count.append(1);raise OSError('unknown acquisition cost')
    adapters={'search':lambda q:[{'report_id':'1'}],'acquire':lost};j=MemoryStore()
    first=api().run_retrieval(plan,adapters,j);second=api().run_retrieval(plan,adapters,j)
    assert count==[1] and first['gaps'] and second['gaps']


def test_query_and_round_caps_are_enforced_and_search_metadata_is_not_admitted():
    plan=api().plan_retrieval({'issuer_id':'issuer-a','question':'增长？','queries':['a','b','c','d']},[],
        limits={'max_reports':5,'max_queries':2,'max_rounds':1})
    called=[]
    out=api().run_retrieval(plan,{'search':lambda q:called.append(q) or [paper(q)],
        'acquire':lambda row:dict(row,original_verified=False)},MemoryStore())
    assert len(called)==2 and not out['reports']
    assert out['consensus_status']=='not_established'
