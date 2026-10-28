"""Read explicitly scoped source corpus; old analysis stays old analysis."""
from copy import deepcopy
import hashlib,json,re
from pathlib import PurePosixPath

from financials.forecast import digest,project
from research.delivery import ServiceError
from research.sources import extract


def notion_text(http,page_id,*,max_blocks=400):
    if not re.fullmatch(r'[0-9a-fA-F-]{32,36}',page_id):raise ValueError('Notion page ID required')
    parts=[];count=0
    def visit(block,depth=0):
        nonlocal count
        if depth>6:raise ValueError('Notion context depth exceeded')
        cursor=None;seen=set()
        while True:
            path=f'/blocks/{block}/children?page_size=100'+('&start_cursor='+cursor if cursor else '')
            r=http('GET',path)
            for b in r['results']:
                count+=1
                if count>max_blocks:raise ValueError('Notion context block bound reached')
                kind=b['type'];value=b.get(kind,{})
                text=''.join(t.get('plain_text',t.get('text',{}).get('content','')) for t in value.get('rich_text',[]))
                if 'cells' in value:text=' | '.join(''.join(t.get('plain_text',t.get('text',{}).get('content','')) for t in cell) for cell in value['cells'])
                if text:parts.append('[Notion block '+b['id']+'] '+text)
                if b.get('has_children'):visit(b['id'],depth+1)
            if not r.get('has_more'):break
            cursor=r.get('next_cursor')
            if not cursor or cursor in seen:raise ValueError('Notion context cursor invalid')
            seen.add(cursor)
    visit(page_id);return '\n'.join(parts)


def prepare_task_context(task,store,drive,http,holding):
    task=store.get(task['task_id']);context=[];gaps=[];root=holding['company_path'].rstrip('/')
    entries=holding.get('context_files',[])
    if len(entries)>30:raise ValueError('explicitly prioritize at most 30 context files')
    for item in entries:
        path=PurePosixPath(item['path'])
        if path.is_absolute() or '..' in path.parts or not path.as_posix().startswith(root+'/'):
            raise ValueError('context file outside company scope')
        try:
            data,meta=drive.read(path.as_posix())
            if 'remoteItem' in meta:raise ValueError('cross-drive shortcut requires explicit source mapping')
            kind=path.suffix.lstrip('.').lower();parsed=extract(data,kind=kind,max_chars=30000)
            if not parsed['text'].strip():raise ValueError('no readable/cached text')
            context.append(dict(revision_id=hashlib.sha256(data).hexdigest(),evidence_kind=item.get('role','prior_research'),
                                source={'url':meta.get('webUrl'),'locator':path.as_posix(),
                                        'file_modified_at':meta.get('lastModifiedDateTime'),'published_at':None},
                                source_text=parsed['text'],missing=[],partial=parsed['partial'],
                                note='Existing research or cached model values; not current primary financial facts.'))
        except Exception as error:gaps.append('context_file_unreadable:'+path.name+':'+type(error).__name__)
    for page in holding.get('context_pages',[]):
        try:
            text=notion_text(http,page)
            if not text.strip():raise ValueError('empty Notion context')
            context.append(dict(revision_id=digest([page,text]),evidence_kind='prior_research',
                                source={'url':'https://www.notion.so/'+page.replace('-',''),'locator':page,'published_at':None},
                                source_text=text[:30000],missing=[],partial=len(text)>30000,
                                note='Prior Notion analysis; dates and claims still require original-source verification.'))
        except Exception as error:gaps.append('notion_context_unreadable:'+type(error).__name__)
    model=None
    if holding.get('forecast_inputs_path'):
        path=holding['forecast_inputs_path']
        if not path.startswith(root+'/') or '..' in PurePosixPath(path).parts:raise ValueError('forecast input outside company')
        try:
            inputs=json.loads(drive.read(path)[0])
            previous=None;expected=task.get('baseline_version')
            if expected is not None:
                pointer=json.loads(drive.read(root+'/research-current.json')[0])
                if pointer.get('model_version')!=expected:raise ValueError('prior baseline version changed')
                ref=pointer.get('model') or pointer.get('workpaper')
                if not ref or not ref['path'].startswith(root+'/') or '..' in PurePosixPath(ref['path']).parts:
                    raise ValueError('prior model reference missing or outside company')
                prior=json.loads(drive.read(ref['path'])[0])
                previous=prior.get('model_snapshot',prior)
                if not previous or previous.get('model_version')!=expected:raise ValueError('prior model version differs')
                if prior.get('report'):task['previous_report']=prior['report']
            model=project(inputs['baseline'],inputs['scenarios'],as_of=inputs['as_of'],previous=previous)
            if model['issuer_id']!=holding['issuer_id']:raise ValueError('forecast issuer mismatch')
        except Exception as error:gaps.append('forecast_inputs_invalid:'+type(error).__name__)
    else:gaps.append('forecast_inputs_not_configured')
    task['context']=context;task['context_gaps']=gaps;task['context_digest']=digest([context,gaps])
    task['needs_context']=task.get('kind')=='weekly_deep' and not context
    if context:
        pack={'context':context,'context_gaps':gaps,'context_digest':task['context_digest']}
        path=root+'/04_研究底稿/资料索引/'+task['context_digest']+'.json'
        task['context_ref']=drive.put_immutable(path,json.dumps(pack,ensure_ascii=False,allow_nan=False).encode())
    store.put(task);return model
