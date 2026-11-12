from urllib.parse import unquote
import pytest
from research.delivery import GraphDrive,VersionConflict
from tests.test_research_delivery import FakeHTTP


class SimpleHTTP(FakeHTTP):
    def __init__(self):super().__init__();self.simple_uploads=[]
    def __call__(self,method,path,**kwargs):
        if method=='PUT' and path.endswith(':/content'):
            assert '/投资/_研究上传暂存/' in unquote(path)
            assert kwargs['headers']['If-None-Match']=='*'
            self.simple_uploads.append(path);key=path[:-9]
            self.files[key]=dict(id='id-'+key,eTag='etag-1',webUrl='https://drive.invalid/file',body=kwargs['body'])
            return self.files[key]
        return super().__call__(method,path,**kwargs)


def test_small_direct_upload_is_only_for_unique_staging_and_still_reads_back_and_moves():
    http=SimpleHTTP();drive=GraphDrive(http,simple_staging=True)
    ref=drive.put_immutable('投资/A/02_财务数据/source.json',b'financial source')
    assert ref['verified'] and len(http.simple_uploads)==1 and not http.uploads
    assert http.files[drive.locator(ref['path'])]['body']==b'financial source'
    drive.put_immutable(ref['path'],b'financial source')
    assert len(http.simple_uploads)==1
    with pytest.raises(VersionConflict):drive.put_immutable(ref['path'],b'changed')


def test_small_direct_upload_still_preserves_a_concurrent_human_destination():
    http=SimpleHTTP();http.rival_before_move=b'human';drive=GraphDrive(http,simple_staging=True)
    with pytest.raises(VersionConflict):drive.put_immutable('投资/A/model.json',b'candidate')
    assert http.files[drive.locator('投资/A/model.json')]['body']==b'human'
