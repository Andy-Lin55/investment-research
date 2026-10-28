from io import BytesIO
import importlib,json,os
from openpyxl import load_workbook
from financials.forecast import project
from tests.test_forecast_model import baseline,scenarios
import pytest


def api():
    try:return importlib.import_module('financials.review_cli')
    except ModuleNotFoundError:pytest.fail('Private model review commands are not implemented')


def test_export_and_import_produces_context_ready_inputs_without_approving_them(tmp_path,capsys):
    m=project(baseline(),scenarios(),as_of='2026-10-27');model=tmp_path/'model.json'
    model.write_text(json.dumps(m));book=tmp_path/'review.xlsx';out=tmp_path/'inputs.json'
    assert api().main(['export','--model',str(model),'--output',str(book)])==0
    assert os.stat(book).st_mode&0o777==0o600
    w=load_workbook(book);w['Assumptions']['D2']=.2;w.save(book)
    assert api().main(['import','--model',str(model),'--workbook',str(book),'--output',str(out)])==0
    value=json.loads(out.read_text())
    rebuilt=project(value['baseline'],value['scenarios'],as_of=value['as_of'])
    assert rebuilt['scenarios']['base'][0]['income']['revenue']==1200
    assert value['human_review']=='pending'
    assert value['original_model_version']==m['model_version']
    assert api().main(['export','--model',str(model),'--output',str(book)])==2
    capsys.readouterr()


def test_private_export_rejects_git_directory(tmp_path,capsys):
    (tmp_path/'.git').mkdir();model=tmp_path/'model.json'
    model.write_text(json.dumps(project(baseline(),scenarios(),as_of='2026-10-27')))
    assert api().main(['export','--model',str(model),'--output',str(tmp_path/'private.xlsx')])==2
    assert not (tmp_path/'private.xlsx').exists()
