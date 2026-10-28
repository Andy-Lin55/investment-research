import importlib,json
import pytest


def api():return importlib.import_module('research.coverage')


def registry():return [dict(issuer_id='issuer-a',tickers=['000001.SZ'],company_path='投资/甲公司',identity_confirmed=True)]


def test_inventory_keeps_all_active_rows_and_never_confirms_folder_candidates(tmp_path):
    root=tmp_path/'投资';root.mkdir();(root/'甲公司').mkdir();(root/'乙公司').mkdir()
    (root/'甲公司'/'old.pdf').write_bytes(b'not hydrated')
    records=[dict(ticker='000001.SZ',name='甲公司'),dict(ticker='0700.HK',name='乙公司')]
    r=api().build_inventory(records,registry(),root,observed_at='2026-10-28')
    assert r['active_listings']==2 and r['confirmed_listings']==1
    a,b=r['listings'];assert a['mapping_status']=='confirmed'
    assert a['files'][0]['content_inspected'] is False
    assert b['mapping_status']=='pending' and b['folder_candidates']==['投资/乙公司']
    assert b['company_path'] is None and b['files']==[]
    assert b['information']['fundamentals']['disclosures']=='unmapped'
    assert a['information']['policy']['status']=='not_configured'
    assert a['information']['trading_flows']['status']=='not_configured'


def test_inventory_does_not_follow_directory_symlinks_or_read_file_contents(tmp_path,monkeypatch):
    root=tmp_path/'投资';root.mkdir();outside=tmp_path/'private';outside.mkdir();(root/'乙公司').symlink_to(outside)
    monkeypatch.setattr(type(root),'read_bytes',lambda *a:pytest.fail('inventory must not hydrate files'))
    r=api().build_inventory([dict(ticker='B',name='乙公司')],[],root,observed_at='2026-10-28')
    assert r['listings'][0]['folder_candidates']==[]


def test_inventory_rejects_confirmed_company_paths_through_symlink(tmp_path):
    root=tmp_path/'投资';root.mkdir();outside=tmp_path/'private';outside.mkdir();(root/'甲公司').symlink_to(outside)
    r=api().build_inventory([dict(ticker='000001.SZ',name='甲公司')],registry(),root,observed_at='2026-10-28')
    assert r['listings'][0]['files']==[]
    assert 'company_directory_unsafe' in r['listings'][0]['gaps']


def test_inventory_bounds_and_duplicates_are_visible(tmp_path):
    root=tmp_path/'投资';root.mkdir();(root/'行业').mkdir();(root/'行业'/'乙公司').mkdir()
    r=api().build_inventory([dict(ticker='B',name='乙公司'),dict(ticker='B',name='乙公司')],[],root,
                            observed_at='2026-10-28',max_directories=1)
    assert r['directory_scan_truncated']
    assert len(r['listings'])==2 and 'duplicate_active_ticker' in r['listings'][0]['gaps']


def test_private_export_does_not_overwrite_or_expose_spreadsheet_formulas(tmp_path):
    root=tmp_path/'投资';root.mkdir()
    r=api().build_inventory([dict(ticker='B',name='=HYPERLINK("evil")')],[],root,observed_at='2026-10-28')
    out=tmp_path/'coverage.json';api().export_inventory(r,out)
    assert out.stat().st_mode & 0o777==0o600
    assert out.with_suffix('.csv').stat().st_mode & 0o777==0o600
    assert "'=HYPERLINK" in out.with_suffix('.csv').read_text(encoding='utf-8-sig')
    with pytest.raises(FileExistsError):api().export_inventory(r,out)


def test_read_only_inventory_cli_does_not_require_production_or_print_holdings(tmp_path,monkeypatch,capsys):
    from research import cli
    root=tmp_path/'投资';root.mkdir();out=tmp_path/'review.json'
    monkeypatch.setenv('HOLDINGS_RESEARCH_ENABLED','false')
    monkeypatch.setattr(cli,'active_records',lambda *a:[dict(ticker='PRIVATE',name='私有持仓')],raising=False)
    monkeypatch.setattr(cli,'notion_http',lambda env:None)
    monkeypatch.setattr(cli,'registry',lambda env:[])
    assert cli.main(['inventory','--source',str(root),'--output',str(out)])==0
    text=capsys.readouterr().out
    assert 'PRIVATE' not in text and '私有持仓' not in text
    assert json.loads(out.read_text())['active_listings']==1


def test_private_export_refuses_git_working_tree(tmp_path):
    root=tmp_path/'repo';root.mkdir();(root/'.git').mkdir()
    with pytest.raises(ValueError):api().export_inventory({'listings':[]},root/'private.json')


def test_mapped_feeds_are_configuration_only_and_not_complete_coverage(tmp_path):
    root=tmp_path/'投资';root.mkdir()
    feeds=[dict(category='policy',issuer_ids=['issuer-a'],exposure_rationale='Explicit mapping',url='https://www.gov.cn/feed')]
    r=api().build_inventory([dict(ticker='000001.SZ',name='甲公司')],registry(),root,observed_at='2026-10-28',feeds=feeds)
    assert r['listings'][0]['information']['policy']['status']=='configured_unverified'
