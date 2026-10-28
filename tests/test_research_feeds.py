import importlib
import pytest
from research.runtime import MemoryStore
from tests.test_research_runtime import holding

def api():
    try:return importlib.import_module('research.feeds')
    except ModuleNotFoundError:pytest.fail('Classified policy/trading feed polling is not implemented')

def test_no_feed_configuration_is_a_visible_coverage_gap():
    assert api().poll([],[],MemoryStore(),None,fetch=lambda _:b'',now='2026-10-27T09:00:00+00:00')['coverage_gaps']==1

def test_policy_mapping_requires_explicit_exposure_and_retains_one_shared_original():
    rss=b'<rss><channel><item><title>Tax rule</title><link>https://www.gov.cn/policy/test</link><pubDate>Tue, 27 Oct 2026 00:00:00 GMT</pubDate><description>New tax rules</description></item></channel></rss>'
    class Drive:
        def __init__(self):self.paths=[]
        def put_immutable(self,path,data):self.paths.append(path);return dict(path=path,verified=True)
    d=Drive();store=MemoryStore();h=holding()
    feed=dict(url='https://www.gov.cn/rss',category='policy',subtype='tax_trade',issuer_ids=['issuer-a'],
              exposure_rationale='Explicit tax exposure mapping',material=False)
    r=api().poll([feed],[h],store,d,fetch=lambda _:rss,now='2026-10-27T09:00:00+00:00')
    assert r['cards']==1;assert len(d.paths)==1
    t=store.list()[0];assert t['card']['category']=='policy';assert t['status']=='evidence_only'

def test_unmapped_policy_never_becomes_a_claim_about_every_holding():
    feed=dict(url='https://www.gov.cn/rss',category='policy',subtype='regulatory',issuer_ids=[],material=True)
    r=api().poll([feed],[holding()],MemoryStore(),None,fetch=lambda _:b'',now='2026-10-27T09:00:00+00:00')
    assert r['coverage_gaps']==1
