import importlib,json
import pytest


def api():
    try:return importlib.import_module('research.broker_cli')
    except ModuleNotFoundError:pytest.fail('Broker CLI not implemented')


def test_archive_without_write_never_reads_private_configuration(tmp_path,capsys):
    missing=tmp_path/'private-secret.env'
    assert api().main(['archive','--env-file',str(missing)])==0
    assert 'written' in capsys.readouterr().out


def test_archive_disabled_never_starts_cloud_authorization(tmp_path,monkeypatch,capsys):
    f=tmp_path/'config.env';f.write_text('RESEARCH_BROKER_SOURCES_ENABLED=false\n')
    def forbidden(*a):pytest.fail('disabled acquisition must not authorize cloud access')
    monkeypatch.setattr('research.cli.graph_drive',forbidden)
    assert api().main(['archive','--write','--env-file',str(f)])==2
    assert 'disabled' in capsys.readouterr().err.lower()


def test_search_cli_writes_private_candidates_without_exposing_provider_errors(tmp_path,monkeypatch,capsys):
    m=api();matrix=tmp_path/'matrix.json';matrix.write_text(json.dumps([{'question':'cash?','keywords':'cash'}]))
    key=tmp_path/'key.env';key.write_text('FXBAOGAO_API_KEY=SECRET\n')
    class Client:
        def __init__(self,key):assert key=='SECRET'
        def search(self,matrix):return [{'report_id':1,'identity_status':'candidate'}]
    monkeypatch.setattr(m,'FxBaogao',Client)
    out=tmp_path/'candidates.json'
    assert m.main(['search','--matrix',str(matrix),'--key-file',str(key),'--output',str(out)])==0
    assert out.stat().st_mode&0o777==0o600
    assert json.loads(out.read_text())[0]['identity_status']=='candidate'
    assert 'SECRET' not in capsys.readouterr().out
    assert m.main(['search','--matrix',str(matrix),'--key-file',str(key),'--output',str(out)])==2


def test_search_without_private_output_does_not_call_provider(tmp_path,monkeypatch):
    m=api()
    def forbidden(*a,**k):pytest.fail('Invalid output must be rejected before source calls')
    monkeypatch.setattr(m,'FxBaogao',forbidden)
    assert m.main(['search'])==2
