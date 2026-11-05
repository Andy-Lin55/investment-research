from importlib import import_module,util
from research.runtime import MemoryStore
from tests.test_broker_sources import Drive,pdf
from tests.test_research_profiles import profile
from research.profiles import resolve_profile
from research.discovery import discover_events
import pytest


def api():
    assert util.find_spec('research.v17_live'), 'live discovery/worker wiring is absent'
    return import_module('research.v17_live')


def test_real_discovery_adapter_archives_original_and_queues_revision_without_metadata_becoming_full_text():
    s=MemoryStore();d=Drive();p=resolve_profile(profile(),{})
    rows=[dict(title='2026第三季度报告',document_id='official-id',url='https://static.cninfo.com.cn/doc.pdf',
               published_at='2026-10-30T00:00:00+08:00')]
    src=api().OfficialSource(s,d,collectors={'CN':lambda ticker,start,end:rows},downloader=lambda url:pdf())
    out=discover_events([p],store=s,adapters={'official':src.discover},as_of='2026-11-02')
    assert out['queued']==1 and out['gaps']==[]
    task=next(t for t in s.list() if t.get('record_type')=='research_task')
    assert task['trigger']['kind']=='earnings'
    assert task['trigger']['documents'][0]['verified'] is True
    archived=src.documents(task)
    assert archived[0]['pages'][0]['page']==1 and archived[0]['period_end']=='2026-09-30'
    assert archived[0]['revision_id']!=rows[0]['document_id']


def test_disclosure_prepublication_rescan_failure_prevents_publishing_old_snapshot():
    s=MemoryStore();d=Drive();p=resolve_profile(profile(),{})
    rows=[dict(title='2026第三季度报告',document_id='official-id',url='https://static.cninfo.com.cn/doc.pdf',
               published_at='2026-10-30T00:00:00+08:00')]
    fail=[False]
    def listing(*args):
        if fail[0]:raise OSError('scan failed')
        return rows
    src=api().OfficialSource(s,d,collectors={'CN':listing},downloader=lambda url:pdf())
    discover_events([p],store=s,adapters={'official':src.discover},as_of='2026-11-02')
    task=next(t for t in s.list() if t.get('record_type')=='research_task');docs=src.documents(task)
    fail[0]=True
    from research.workflow import StageWait
    with pytest.raises(StageWait):src.prepublish({'coverage':{'documents':docs}},task)


def test_legacy_research_worker_does_not_consume_new_tasks_or_control_records():
    from research.cli import select_tasks
    from tests.test_research_runtime import event,holding
    from research.runtime import enqueue
    s=MemoryStore();old=enqueue(s,event(),holding())
    new=dict(old,task_id='v17:new',workflow_contract='holdings-v17')
    control=dict(old,task_id='v17:control',record_type='control')
    selected=select_tasks([old,new,control],{'issuer-a'},limit=3,day='2026-11-02')
    assert [t['task_id'] for t in selected]==[old['task_id']]


def test_prior_company_files_are_read_and_tagged_as_prior_analysis_not_primary_facts():
    d=Drive();p=resolve_profile(profile(),{})
    ref=d.put_immutable(p['company_path']+'/04_研究底稿/旧研究.md','旧持有逻辑：关注现金兑现'.encode())
    p['context_files']=[{'path':ref['path'],'role':'prior_research'}]
    out=api().load_prior_context(p,d,None)
    assert out['items'][0]['evidence_kind']=='prior_research'
    assert '现金兑现' in out['items'][0]['source_text'] and out['gaps']==[]
    p['context_files']=[{'path':'投资/另一家公司/private.md'}]
    with pytest.raises(ValueError):api().load_prior_context(p,d,None)


def test_policy_feed_is_actual_full_text_with_explicit_exposure_not_just_a_search_excerpt():
    s=MemoryStore();d=Drive();p=resolve_profile(profile(),{})
    feed={'url':'https://www.gov.cn/feed.xml','issuer_ids':['issuer-a'],'exposure_rationale':'税收影响利润',
          'category':'policy','subtype':'tax_trade','material':True}
    rss=b'<rss><channel><item><title>Tax change</title><link>https://www.gov.cn/a.htm</link><pubDate>Sun, 01 Nov 2026 00:00:00 +0800</pubDate><description>excerpt only</description></item></channel></rss>'
    def fetch(url):return rss if url.endswith('xml') else b'<html><body>Full official policy article.</body></html>'
    src=api().FeedSource(s,d,feed,fetch=fetch)
    out=src.discover(p,'2026-10-30','2026-11-02')
    assert out['complete'] and out['events'][0]['category']=='policy'
    original=api().read_ref(d,out['events'][0]['documents'][0])
    assert original['pages'][0]['text']=='Full official policy article.'
    assert original['exposure_rationale']=='税收影响利润'


def test_env_file_can_enable_explicit_write_but_never_enables_writes_without_write_flag(tmp_path,monkeypatch):
    from research.v17 import main
    path=tmp_path/'private.env';path.write_text('RESEARCH_V17_ENABLED=true\n')
    monkeypatch.setattr(api(),'run',lambda args,env:17)
    assert main(['scan','--write','--env-file',str(path)],env={})==17
    assert main(['scan','--env-file',str(path)],env={})==0


def test_latest_guidance_survives_noise_in_inventory_and_prompt(monkeypatch):
    from research.contract_v17 import compact_material
    p=resolve_profile(profile(),{})
    s=MemoryStore();src=api().OfficialSource(s,Drive())
    items=[dict(document_id='forecast',document_type='业绩预告',published_at='2026-10-20',metadata_revision='g',
                title='2026年度业绩预告',pages=[dict(page=1,text='公司预计本年利润变化')])]
    items.extend(dict(document_id=f'noise-{day}',document_type='公告',published_at=f'2026-10-{day}',metadata_revision=str(day),
                      pages=[]) for day in range(21,29))
    monkeypatch.setattr(src,'metadata',lambda *a:items)
    monkeypatch.setattr(src,'original',lambda p,r:(None,dict(r,revision_id=r['metadata_revision'])))
    task=dict(task_id='test',profile=p,trigger={},created_at='2026-11-03')
    docs=src.documents(task)
    assert any(r['document_id']=='forecast' for r in docs)
    material=compact_material({'coverage':{'documents':docs}},task)
    assert next(r for r in material['coverage']['documents'] if r['document_id']=='forecast')['pages']==items[0]['pages']


def test_cached_annual_extraction_repairs_period_without_downloading_original_again():
    import json
    from financials.forecast import digest
    from research.v17_live import OfficialSource
    from research.workflow import control
    from research.runtime import MemoryStore
    from tests.test_broker_sources import Drive
    from tests.test_research_profiles import profile
    from research.profiles import resolve_profile
    p=resolve_profile(profile(),{});s=MemoryStore();drive=Drive()
    row={'document_id':'old-annual','document_type':'年度报告','title':'2025年年度报告','metadata_revision':'meta'}
    doc=dict(row,issuer_id=p['issuer_id'],revision_id='a'*64,period_end='2025-09-30',
        pages=[{'page':1,'text':'2025年年度报告\n详见第三节管理层讨论'}])
    saved=drive.put_immutable(p['company_path']+'/old.json',json.dumps(doc).encode())
    key='v17:original:'+digest([p['issuer_id'],row['document_id']])
    s.put(control(key,p['issuer_id'],metadata_revision='meta',reference=saved))
    def forbidden(url):raise AssertionError('existing original must not be downloaded again')
    src=OfficialSource(s,drive,downloader=forbidden)
    updated,new=src.original(p,row)
    assert updated['path']!=saved['path'] and s.get(key)['reference']==updated
    assert new['period_end']=='2025-12-31' and new['revision_id']==doc['revision_id']
    assert json.loads(drive.read(saved['path'])[0])['period_end']=='2025-09-30'
