import importlib,json
import pytest

def api():
    try:return importlib.import_module('research.cli')
    except ModuleNotFoundError:pytest.fail('Research command entry point is not implemented')

def test_default_doctor_is_offline_and_reports_disabled(capsys,monkeypatch):
    monkeypatch.delenv('HOLDINGS_RESEARCH_ENABLED',raising=False)
    assert api().main([])==0
    assert json.loads(capsys.readouterr().out)['enabled'] is False

def test_cloud_writes_require_both_explicit_write_and_enable_flag(capsys,monkeypatch):
    monkeypatch.setenv('HOLDINGS_RESEARCH_ENABLED','false')
    assert api().main(['worker','--write'])==2
    assert 'disabled' in capsys.readouterr().err

def test_cloud_write_command_without_write_is_offline_summary(capsys):
    assert api().main(['scan'])==0
    r=json.loads(capsys.readouterr().out);assert r['written'] is False

def test_schema_setup_is_explicit_and_separate_from_existing_report_database():
    calls=[]
    def http(method,path,**kw):
        calls.append((method,path,kw))
        if '/children' in path:return dict(results=[],has_more=False)
        if path=='/pages':return dict(id='pilot-page',url='https://notion.invalid/pilot')
        return dict(id='db-'+str(len(calls)),data_sources=[{'id':'source-'+str(len(calls))}])
    r=api().setup_notion(http,'parent')
    assert r['workspace_url']=='https://notion.invalid/pilot'
    assert r['tasks_source']!=r['reports_source']
    assert all(not (c[0]=='PATCH') for c in calls)
