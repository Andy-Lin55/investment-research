"""Lost responses and nested-table readback must verify real report contents."""
from copy import deepcopy
import pytest
from research.delivery import NotionReports,DeliveryError
from tests.test_report_quality import fixture


class HTTP:
    def __init__(self):self.pages={};self.children={};self.serial=0;self.lose=False;self.tamper=False
    def save_blocks(self,blocks):
        result=[]
        for b in deepcopy(blocks):
            self.serial+=1;b['id']='b-'+str(self.serial);kind=b['type'];kids=b[kind].pop('children',[])
            b['has_children']=bool(kids)
            if kids:self.children[b['id']]=self.save_blocks(kids)
            result.append(b)
        return result
    def __call__(self,method,path,*,body=None):
        if path.endswith('/query'):
            return dict(results=list(self.pages.values()),has_more=False)
        if method=='POST' and path=='/pages':
            p={'id':'report-1','url':'https://notion.invalid/v2','properties':deepcopy(body['properties'])}
            self.pages[p['id']]=p;self.children[p['id']]=self.save_blocks(body['children'])
            if self.lose:self.lose=False;raise RuntimeError('created but response lost')
            return deepcopy(p)
        if path.startswith('/pages/'):return deepcopy(self.pages[path.split('/')[2]])
        if path.startswith('/blocks/'):
            rows=deepcopy(self.children[path.split('/')[2]])
            if self.tamper:
                for r in rows:
                    if r['type']=='table_row':r['table_row']['cells'][-1]=[{'type':'text','text':{'content':'changed'}}];break
            return dict(results=rows,has_more=False)
        raise AssertionError((method,path))


def test_native_report_creation_and_lost_response_recovery_preserves_tables():
    t,a=fixture();http=HTTP();http.lose=True;p=NotionReports(http,'reports')
    with pytest.raises(RuntimeError):p.publish(t,a,{})
    ref=p.publish(t,a,{},lookup_only=True)
    assert ref['verified'] is True and len(http.pages)==1
    assert any(b['type']=='table' for b in http.children['report-1'])


def test_changed_nested_financial_cell_prevents_success_receipt():
    t,a=fixture();http=HTTP();p=NotionReports(http,'reports');p.publish(t,a,{})
    http.tamper=True
    with pytest.raises(DeliveryError):p.publish(t,a,{})
