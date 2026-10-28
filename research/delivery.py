"""Scoped Graph/Notion delivery and independent, checksummed local snapshots."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path,PurePosixPath
from urllib.parse import quote
import zipfile

from research.runtime import VersionConflict

class DeliveryError(RuntimeError):pass
class ServiceError(DeliveryError):
    def __init__(self,status):
        self.status=status;super().__init__(f'Service request failed ({status}); response omitted')


def scoped_path(path):
    p=PurePosixPath(path)
    if (p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0]!='投资'
            or any(c in path for c in ('\\','\x00'))):raise ValueError('path must stay inside 投资')
    return p.as_posix()


class GraphDrive:
    def __init__(self,http):self.http=http
    def locator(self,path):return '/me/drive/root:/'+quote(scoped_path(path),safe='/')
    def read(self,path):
        loc=self.locator(path)
        meta=self.http('GET',loc)
        data=self.http('GET',loc+':/content',raw=True)
        return data,meta
    def _parents(self,path):
        parts=PurePosixPath(scoped_path(path)).parent.parts
        for i,name in enumerate(parts):
            loc=self.locator('/'.join(parts[:i+1]))
            try:
                meta=self.http('GET',loc)
                if 'folder' not in meta:raise DeliveryError('destination parent is not a folder')
            except ServiceError as e:
                if e.status!=404:raise
                if i==0:raise DeliveryError('existing 投资 root required')
                parent=self.locator('/'.join(parts[:i]))
                self.http('POST',parent+':/children',body={'name':name,'folder':{},'@microsoft.graph.conflictBehavior':'fail'})
    def put_immutable(self,path,data):
        if not isinstance(data,bytes) or not data:raise ValueError('nonempty bytes required')
        if len(data)>40*1024*1024:raise ValueError('bounded upload supports files up to 40 MiB')
        loc=self.locator(path);digest=hashlib.sha256(data).hexdigest()
        try:
            old,meta=self.read(path)
            if hashlib.sha256(old).hexdigest()!=digest:raise VersionConflict('existing file differs; never overwrite')
        except ServiceError as error:
            if error.status!=404:raise
            self._parents(path)
            session=self.http('POST',loc+':/createUploadSession',body={'item':{'@microsoft.graph.conflictBehavior':'fail'}})
            url=session['uploadUrl'];chunk_size=320*1024*10
            for offset in range(0,len(data),chunk_size):
                chunk=data[offset:offset+chunk_size]
                self.http('PUT',url,body=chunk,headers={'Content-Length':str(len(chunk)),
                    'Content-Range':f'bytes {offset}-{offset+len(chunk)-1}/{len(data)}'},authenticated=False)
            old,meta=self.read(path)
            if hashlib.sha256(old).hexdigest()!=digest:raise DeliveryError('upload read-back checksum differs')
        return dict(path=path,sha256=digest,id=meta['id'],etag=meta['eTag'],url=meta.get('webUrl'),verified=True)
    def update_pointer(self,path,value,*,expected_version,expected_etag=None):
        """Reserved system pointer only; human files are never writable here."""
        if PurePosixPath(path).name!='research-current.json':raise ValueError('reserved pointer only')
        data,meta=self.read(path);old=json.loads(data)
        if expected_etag is not None and meta['eTag']!=expected_etag:raise VersionConflict('pointer ETag changed')
        if old.get('model_version')!=expected_version:raise VersionConflict('formal baseline changed')
        body=json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
        self.http('PUT','/me/drive/items/'+quote(meta['id'],safe='')+'/content',body=body,
                  headers={'If-Match':meta['eTag'],'Content-Type':'application/json'})
        actual,_=self.read(path)
        if actual!=body:raise DeliveryError('pointer read-back differs')


def rich(text):return [{'type':'text','text':{'content':text[i:i+1900]}} for i in range(0,len(text),1900)]
def text_property(value):return {'rich_text':rich(value)}
def plain(items):return ''.join(x.get('plain_text',x.get('text',{}).get('content','')) for x in items)


class NotionTaskStore:
    def __init__(self,http,data_source,content_loader=None):
        self.http=http;self.data_source=data_source;self.ids={};self.cache={};self.content_loader=content_loader
    def _query(self,body):
        cursor=None;seen=set()
        while True:
            response=self.http('POST',f'/data_sources/{self.data_source}/query',body=dict(body,**({'start_cursor':cursor} if cursor else {})))
            yield from response['results']
            if not response.get('has_more'):return
            cursor=response.get('next_cursor')
            if not cursor or cursor in seen:raise DeliveryError('invalid/repeated Notion pagination cursor')
            seen.add(cursor)
    def _decode(self,page,*,hydrate=True):
        task=json.loads(plain(page['properties']['Checkpoint']['rich_text']))
        self.ids[task['task_id']]=page['id']
        review=page['properties'].get('HumanReview',{}).get('select')
        if review:task['human_review']=review['name']
        if 'RetryRequested' in page['properties']:
            task['retry_requested']=page['properties']['RetryRequested']['checkbox']
        if hydrate:
            for kind,ref in [('content',task.get('evidence',{}).get('text_ref')),('context',task.get('context_ref'))]:
                if not ref:continue
                identity=(task['task_id'],kind,ref['path'])
                if identity not in self.cache and self.content_loader:
                    raw=self.content_loader(ref)
                    if ref.get('sha256') and hashlib.sha256(raw).hexdigest()!=ref['sha256']:
                        raise DeliveryError('referenced text/context differs from archived checksum')
                    pack=json.loads(raw)
                    self.cache[identity]=pack['text'] if kind=='content' else pack['context']
                if identity in self.cache:
                    if kind=='content':
                        text=self.cache[identity].strip()
                        if hashlib.sha256(text.encode()).hexdigest()!=task['card']['source']['content_sha256']:
                            raise DeliveryError('source text revision mismatch')
                        task['evidence']['content']=text
                    else:task['context']=deepcopy(self.cache[identity])
        return task
    def get(self,key):
        rows=list(self._query({'filter':{'property':'Name','title':{'equals':key}},'page_size':100}))
        if len(rows)>1:raise DeliveryError('duplicate task keys; resolve instead of selecting arbitrarily')
        return self._decode(rows[0]) if rows else None
    def list(self):return [self._decode(p,hydrate=False) for p in self._query({'page_size':100})]
    def put(self,task):
        checkpoint=deepcopy(task)
        ref=checkpoint.get('evidence',{}).get('text_ref')
        if ref:
            if ref.get('verified') is not True:raise ValueError('verified external text reference required')
            if task['evidence'].get('content'):
                self.cache[(task['task_id'],'content',ref['path'])]=task['evidence']['content']
            checkpoint['evidence']['content']='';checkpoint['content_external']=True
        if checkpoint.get('context_ref'):
            ref=checkpoint['context_ref']
            if ref.get('verified') is not True:raise ValueError('verified context reference required')
            self.cache[(task['task_id'],'context',ref['path'])]=deepcopy(task.get('context',[]))
            checkpoint['context']=[]
        encoded=json.dumps(checkpoint,ensure_ascii=False,allow_nan=False,separators=(',',':'))
        if len(encoded)>150000 or len(encoded.encode())>350000:raise ValueError('checkpoint too large; use a source-file reference')
        key=task['task_id'];old=self.get(key)
        props={'Checkpoint':text_property(encoded),'State':{'select':{'name':task['status']}}}
        if 'retry_requested' in task:props['RetryRequested']={'checkbox':bool(task['retry_requested'])}
        if old:
            self.http('PATCH',f'/pages/{self.ids[key]}',body={'properties':props})
        else:
            props.update(Name={'title':rich(key)},Issuer=text_property(task['issuer_id']),
                         Kind={'select':{'name':task['kind']}},HumanReview={'select':{'name':'待看'}})
            card=task.get('card')
            if card:
                props.update(Category={'select':{'name':card['category'] or 'unclassified'}},
                             Subtype=text_property(card['subtype'] or ''),Source={'url':card['source']['url']},
                             Published={'date':{'start':card['source']['published_at']}},
                             Missing=text_property(', '.join(card['missing'])))
            page=self.http('POST','/pages',body={'parent':{'data_source_id':self.data_source},'properties':props})
            self.ids[key]=page['id']


class NotionReports:
    def __init__(self,http,data_source):self.http=http;self.data_source=data_source
    def publish(self,task,result,files,*,candidate=True,lookup_only=False):
        """Add immutable candidate report rows; do not update Universe signals."""
        key=task['publication_key'];query={'filter':{'property':'TriggerEventID','rich_text':{'equals':key}},'page_size':100}
        body=render_report(task,result,files)
        rows=self.http('POST',f'/data_sources/{self.data_source}/query',body=query)['results']
        if len(rows)>1:raise DeliveryError('duplicate report keys')
        if rows:return self._verify(rows[0],key,body)
        if lookup_only:return None
        report=result['report']
        blocks=[{'object':'block','type':'paragraph','paragraph':{'rich_text':rich(body[i:i+1800])}}
                for i in range(0,len(body),1800)]
        if len(blocks)>95:raise ValueError('bounded report too long; write full workpaper and shorten report')
        props={'Name':{'title':rich(task['ticker']+' 持仓研究 '+('候选版' if candidate else '正式版'))},
               'Ticker':text_property(task['ticker']),'TriggerEventID':text_property(key),
               'DocType':{'select':{'name':'AI深度分析'}},'CoreThesis':text_property(report['thesis'][:1800]),
               'Analyst':text_property(result['actual_model']),'Broker':text_property(result['provider'])}
        page=self.http('POST','/pages',body={'parent':{'data_source_id':self.data_source},'properties':props,'children':blocks})
        return self._verify(page,key,body)
    def _verify(self,page,key,expected_body=None):
        saved=self.http('GET','/pages/'+page['id'])
        if plain(saved['properties']['TriggerEventID']['rich_text'])!=key:raise DeliveryError('report key read-back differs')
        children=self.http('GET','/blocks/'+page['id']+'/children?page_size=100')
        if children.get('has_more'):raise DeliveryError('unexpected report content pagination')
        text=''.join(plain(b.get('paragraph',{}).get('rich_text',[])) for b in children['results'])
        if not text.strip() or expected_body is not None and text!=expected_body:
            raise DeliveryError('report content read-back differs or is missing')
        return {'id':page['id'],'url':saved.get('url',page.get('url')),'verified':True}


def render_report(task,result,files):
    r=result['report'];lines=[task['ticker']+' 持仓研究｜待人工核验',r['conclusion'],'持仓逻辑：'+r['thesis'],
        '实际模型：'+result['actual_model']+'；推理服务：'+result['provider'],
        '模型状态：'+('待补充或核验模型' if result['validation']['status']=='pending_model' else '可供人工审阅'),
        '资料截止：'+task['card']['source']['published_at']]
    for key,label in [('changes','变化及模型影响'),('counterevidence','反证'),('next_actions','下一步与条件')]:
        lines.append(label)
        for item in r[key]:
            lines.append((item['condition']+' → '+item['action']+'；验证日期：'+item['due']) if key=='next_actions' else item['text'])
            lines.append('引用证据：'+', '.join(item['evidence']))
    model=result.get('model_snapshot')
    if model:
        lines.append('三表预测与情景估值（'+model['currency']+'，金额保留原始单位）')
        for name,years in model.get('scenarios',{}).items():
            for row in years:
                lines.append(f'{name} / {row["year"]}：收入 {row["income"]["revenue"]:,.2f}；归母净利 {row["income"]["parent_net_income"]:,.2f}；'
                    f'期末现金 {row["balance"]["cash"]:,.2f}；债务 {row["balance"]["debt"]:,.2f}；估值 {row["valuation"]["price"]}；资金缺口 {row["funding_shortfall"]:,.2f}')
        if model.get('assumption_changes'):lines.append('本次变化的假设：'+', '.join(x['field'] for x in model['assumption_changes']))
    stable_files={k:v for k,v in files.items() if k in {'source','result','model'}}
    lines.extend(['未解决问题：'+'；'.join(r['open_questions']),'证据与底稿：',
                  '\n'.join(k+'：'+str(v.get('url') or v.get('path')) for k,v in stable_files.items()),
                  '事实来源：'+task['card']['source']['url'],'人工状态：'+task['human_review'],
                  '自动检查仅检查结构、引用与模型计算；不代表事实已获人工确认。'])
    lines.append('资料缺口：'+json.dumps(task.get('context_gaps',[]),ensure_ascii=False))
    extraction=task.get('evidence',{}).get('extraction')
    if extraction:
        lines.append('原文提取覆盖：'+('部分章节；请结合完整原件复核' if extraction.get('partial') else '提取文本已覆盖可读原文')+
                     '；纳入字符 '+str(extraction.get('included_characters'))+'／可提取字符 '+str(extraction.get('extracted_characters')))
    return '\n\n'.join(lines)


def backup(source,destination):
    source,destination=Path(source),Path(destination)
    if source.is_symlink() or not source.is_dir():raise ValueError('existing source directory required')
    if destination.is_relative_to(source):raise ValueError('backup must be independent of source tree')
    files=[p for p in source.rglob('*') if p.is_file() and not any(x.is_symlink() for x in [p,*p.parents])]
    hashes={p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    with zipfile.ZipFile(destination,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for p in files:archive.write(p,p.relative_to(source).as_posix())
        archive.writestr('_snapshot_manifest.json',json.dumps(hashes))


def restore(snapshot,destination):
    root=Path(destination)
    if root.exists():raise FileExistsError('restore requires a new destination; existing files stay unchanged')
    with zipfile.ZipFile(snapshot) as archive:
        hashes=json.loads(archive.read('_snapshot_manifest.json'))
        for name,digest in hashes.items():
            p=PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts:raise ValueError('unsafe snapshot path')
            if hashlib.sha256(archive.read(name)).hexdigest()!=digest:raise DeliveryError('snapshot checksum differs')
        root.mkdir(parents=True,exist_ok=False)
        for name in hashes:
            p=root/name;p.parent.mkdir(parents=True,exist_ok=True)
            with p.open('xb') as stream:stream.write(archive.read(name))
