"""Opt-in holdings research commands. Default command is an offline doctor."""
import argparse
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import hashlib,json,os
from pathlib import Path
import sys,time

from research.delivery import (GraphDrive,NotionTaskStore,NotionReports,ServiceError,rich,plain,backup,restore)
from research.services import (HTTP,doctor,refresh_graph,github_secret_writer,configured_providers,match_roster)
from research.runtime import Budget,enqueue,weekly_plan
from research.engine import process,digest,encoded

STATES=('pending','pending_evidence','retry_delivery','retry_provider','budget_blocked','pending_pricing',
        'pending_model','pending_usage_verification','quality_blocked','completed','baseline_conflict',
        'provider_attempts_exhausted','pending_report_confirmation','waiting_subscription','candidate_ready',
        'superseded_source_resolved','evidence_only','pending_context','pending_model_provenance',
        'pending_budget_reconciliation','pending_attempt_confirmation','pending_model_refresh','retry_release','published')


def pages(http,source,body=None):
    cursor=None;seen=set()
    while True:
        r=http('POST',f'/data_sources/{source}/query',body=dict(body or {},page_size=100,**({'start_cursor':cursor} if cursor else {})))
        yield from r['results']
        if not r.get('has_more'):return
        cursor=r.get('next_cursor')
        if not cursor or cursor in seen:raise ValueError('invalid Notion pagination')
        seen.add(cursor)


def source_id(http,identifier):
    database=http('GET','/databases/'+identifier)
    ids=[s['id'] for s in database['data_sources']]
    if len(ids)!=1:raise ValueError('choose an explicit data source for a multi-source database')
    return ids[0]


def notion_http(env):
    return HTTP('https://api.notion.com/v1',{'Authorization':'Bearer '+env['NOTION_TOKEN'],
                                         'Notion-Version':'2025-09-03','Content-Type':'application/json'})


def ensure_pilot_schema(http,source,schema):
    """Only add missing properties in this helper's dedicated pilot databases."""
    existing=http('GET','/data_sources/'+source)['properties']
    missing={k:v for k,v in schema.items() if k not in existing}
    if missing:http('PATCH','/data_sources/'+source,body={'properties':missing})


def setup_notion(http,parent):
    """Create/reuse a dedicated pilot workspace; no production schema edits."""
    title='持仓研究升级｜隔离试运行'
    children=[];cursor=None
    while True:
        path=f'/blocks/{parent}/children?page_size=100'+('&start_cursor='+cursor if cursor else '')
        r=http('GET',path);children+=r['results']
        if not r.get('has_more'):break
        cursor=r['next_cursor']
    matches=[b for b in children if b.get('type')=='child_page' and b['child_page']['title']==title]
    if len(matches)>1:raise ValueError('duplicate pilot workspace; select one explicitly')
    workspace=http('GET','/pages/'+matches[0]['id']) if matches else http('POST','/pages',body={
        'parent':{'page_id':parent},'properties':{'title':{'title':rich(title)}}})
    tasks={'Name':{'title':{}},'Checkpoint':{'rich_text':{}},'Issuer':{'rich_text':{}},
           'State':{'select':{'options':[{'name':s} for s in STATES]}},
           'Kind':{'select':{'options':[{'name':s} for s in ('mandatory_event','weekly_deep','manual')]}},
           'HumanReview':{'select':{'options':[{'name':s} for s in ('待看','已确认','有疑问','已纠正')]}},
           'Category':{'select':{'options':[{'name':s} for s in ('trading_flows','fundamentals','policy','unclassified')]}},
           'Subtype':{'rich_text':{}},'Source':{'url':{}},'Published':{'date':{}},'Missing':{'rich_text':{}}}
    tasks['RetryRequested']={'checkbox':{}}
    reports={'Name':{'title':{}},'Ticker':{'rich_text':{}},'TriggerEventID':{'rich_text':{}},
             'DocType':{'select':{'options':[{'name':'AI深度分析'}]}},'CoreThesis':{'rich_text':{}},
             'Analyst':{'rich_text':{}},'Broker':{'rich_text':{}}}
    children=http('GET',f'/blocks/{workspace["id"]}/children?page_size=100')['results']
    result={'workspace_url':workspace.get('url'),'workspace_id':workspace['id']}
    for name,schema,key in [('研究任务与信息卡片',tasks,'tasks_source'),('研究报告候选',reports,'reports_source')]:
        existing=[c for c in children if c.get('type')=='child_database' and c['child_database']['title']==name]
        if len(existing)>1:raise ValueError('duplicate pilot database')
        db=http('GET','/databases/'+existing[0]['id']) if existing else http('POST','/databases',body={
            'parent':{'type':'page_id','page_id':workspace['id']},'title':rich(name),
            'is_inline':True,'initial_data_source':{'properties':schema}})
        if len(db['data_sources'])!=1:raise ValueError('ambiguous pilot data source')
        result[key]=db['data_sources'][0]['id']
        if existing:ensure_pilot_schema(http,result[key],schema)
    return result


def oauth_post(path,body):
    import requests
    try:r=requests.post('https://login.microsoftonline.com'+path,data=body,timeout=(15,45))
    except requests.RequestException:raise ServiceError(0) from None
    value=r.json()
    if r.status_code!=200:raise ServiceError(r.status_code)
    return value


def secure_token_file(path,value):
    import tempfile
    path=Path(path)
    if path.name!='.research-graph-token.json' or path.is_symlink() or not path.parent.is_dir():
        raise ValueError('use reserved .research-graph-token.json inside an existing private directory')
    fd,name=tempfile.mkstemp(prefix='.graph-token-',dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w') as stream:json.dump({'refresh_token':value},stream)
        os.replace(name,path)
    finally:
        if Path(name).exists():Path(name).unlink()


def graph_drive(env):
    token=env.get('ONEDRIVE_REFRESH_TOKEN')
    cache=env.get('ONEDRIVE_TOKEN_CACHE')
    if cache and Path(cache).exists():token=json.loads(Path(cache).read_text())['refresh_token']
    if env.get('GITHUB_ACTIONS')=='true':sink=github_secret_writer(env['RESEARCH_GITHUB_TOKEN'])
    elif cache:sink=lambda value:secure_token_file(cache,value)
    else:raise ValueError('safe token rotation destination not configured')
    access=refresh_graph(env['ONEDRIVE_CLIENT_ID'],token,oauth_post,sink)
    return GraphDrive(HTTP('https://graph.microsoft.com/v1.0',{'Authorization':'Bearer '+access}))


def registry(env,drive=None):
    if env.get('RESEARCH_ISSUERS_JSON'):return json.loads(env['RESEARCH_ISSUERS_JSON'])
    if env.get('RESEARCH_REGISTRY_FILE'):return json.loads(Path(env['RESEARCH_REGISTRY_FILE']).read_text())
    if not drive:raise ValueError('private issuer registry required independently of OneDrive authentication')
    return json.loads(drive.read('投资/持仓研究配置/issuers.json')[0])


def active_roster(http,env,reg):
    source=env.get('RESEARCH_UNIVERSE_SOURCE_ID') or source_id(http,env['NOTION_UNIVERSE_DB_ID'])
    tickers=[]
    for page in pages(http,source,{'filter':{'property':'Status','select':{'equals':'Active'}}}):
        ticker=plain(page['properties']['Ticker']['rich_text'])
        if not ticker:raise ValueError('Active listing has no ticker')
        tickers.append(ticker)
    return match_roster(tickers,reg)


def scan(roster,store,drive,*,start,end):
    from research import sources
    counts=dict(scanned=0,queued=0,pending_sources=0,coverage_gaps=len(roster['unmapped']))
    now=datetime.now(timezone.utc).isoformat()
    for h in roster['holdings']:
        for ticker in h['active_tickers']:
            collector=sources.hkex if ticker.endswith('.HK') else sources.cninfo if ticker.endswith(('.SH','.SZ','.BJ')) else sources.sec
            try:rows=collector(ticker,start,end);counts['scanned']+=1
            except Exception:
                counts['coverage_gaps']+=1;continue
            for row in rows:
                dt=sources.document_type(row['title'],row.get('document_type'))
                event=dict(issuer_id=h['issuer_id'],ticker=ticker,title=row['title'],document_type=dt,
                           source_url=row['url'],document_id=row['document_id'],published_at=row['published_at'],
                           publication_precision=row.get('publication_precision','instant'),ingested_at=now,content='',
                           is_material=dt=='重大公告')
                title=row['title']
                if any(k in title for k in ('回购','回購','BUYBACK')):event.update(category='trading_flows',subtype='buyback')
                elif any(k in title for k in ('增持','减持','減持','持股')):event.update(category='trading_flows',subtype='ownership')
                kind='manual' if dt=='公告' else 'mandatory_event'
                task=enqueue(store,event,h,kind=kind)
                if kind=='manual':task['status']='evidence_only';store.put(task);continue
                # A resolved discovery record does not prove that a document
                # served under the same source ID/URL is still unchanged.
                try:
                    raw=sources.download_primary(row['url'])
                    extension='pdf' if raw.startswith(b'%PDF') else 'html'
                    original=drive.put_immutable(h['company_path']+'/01_公司资料/公告归档/'+hashlib.sha256(raw).hexdigest()+'.'+extension,raw)
                    parsed=sources.extract(raw,kind=extension)
                    text_ref=drive.put_immutable(h['company_path']+'/04_研究底稿/原文提取/'+
                             hashlib.sha256(parsed['text'].encode()).hexdigest()+'.json',encoded(parsed))
                    full=dict(event,content=parsed['text'],source_locator='Original file '+original['path'],
                              extraction={k:v for k,v in parsed.items() if k!='text'},original=original,text_ref=text_ref)
                    resolved=enqueue(store,full,h)
                    if resolved['task_id']!=task['task_id']:
                        task['status']='superseded_source_resolved';task['superseded_by']=resolved['task_id'];store.put(task)
                    counts['queued']+=1
                except Exception:
                    counts['pending_sources']+=1
    return counts


def weekly(roster,store,*,day,slots):
    history=store.list();holdings=[]
    for h in roster['holdings']:
        rows=[r for r in history if r['issuer_id']==h['issuer_id']]
        last=max([r.get('week','') or '' for r in rows if r['kind']=='weekly_deep' and r['status']=='completed'],default='')
        holdings.append(dict(h,changed=any(r['kind']=='mandatory_event' and r['status'] not in {'completed','superseded_source_resolved'} for r in rows),
                             due_questions=bool(h.get('next_review_date') and h['next_review_date']<=day),last_review=last))
    plan=weekly_plan(holdings,as_of=day,deep_slots=slots)
    now=datetime.now(timezone.utc).isoformat()
    for h in holdings:
        if h['issuer_id'] not in plan['deep']:continue
        event=dict(issuer_id=h['issuer_id'],ticker=h['active_tickers'][0],title='周度持仓复核 '+day,document_type='公告',
                   source_url=h.get('notion_url','https://www.notion.so/'),document_id='weekly-'+day,
                   published_at=now,ingested_at=now,content='Rule-based weekly review; source corpus must be collected before deep analysis.')
        task=enqueue(store,event,h,kind='weekly_deep',week=day)
        task['needs_context']=True;store.put(task)
    return dict(screened=len(plan['screened']),deep=len(plan['deep']),deferred=len(plan['deferred']),coverage_gaps=len(roster['unmapped']))


def select_tasks(tasks,active_issuers,*,limit,day):
    runnable={'pending','retry_delivery','retry_provider','waiting_subscription'}
    selected=[]
    for task in tasks:
        if task['issuer_id'] not in active_issuers or task['status'] in {'retry_release','published','completed','superseded_source_resolved','evidence_only'}:continue
        eligible=task['status'] in runnable or task.get('retry_requested') is True
        if task['status']=='budget_blocked' and task.get('blocked_on_day')!=day:eligible=True
        if eligible:selected.append(task)
    return sorted(selected,key=lambda t:(t.get('priority',2),t.get('last_attempted_at') or t['created_at'],t['created_at']))[:limit]


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',nargs='?',default='doctor',choices=['doctor','scan','worker','weekly','local','notion-setup','backup','restore'])
    parser.add_argument('--write',action='store_true');parser.add_argument('--env-file',type=Path)
    parser.add_argument('--parent');parser.add_argument('--output',type=Path);parser.add_argument('--source',type=Path)
    parser.add_argument('--limit',type=int,default=3);parser.add_argument('--slots',type=int,default=3)
    args=parser.parse_args(argv);env=dict(os.environ)
    if args.env_file:
        from dotenv import dotenv_values
        env.update({k:v for k,v in dotenv_values(args.env_file).items() if v is not None})
    if args.mode=='doctor':print(json.dumps(doctor(env)));return 0
    if not args.write:print(json.dumps(dict(mode=args.mode,written=False,reason='Explicit --write required')));return 0
    if args.mode not in {'notion-setup','backup','restore'} and env.get('HOLDINGS_RESEARCH_ENABLED')!='true':
        print('Holdings research disabled; no side effects',file=sys.stderr);return 2
    try:
        if args.mode in {'backup','restore'}:
            if not args.source or not args.output:raise ValueError('source and output required')
            (backup if args.mode=='backup' else restore)(args.source,args.output);print('{"verified":true}');return 0
        http=notion_http(env)
        if args.mode=='notion-setup':
            if env.get('GITHUB_ACTIONS')=='true':raise ValueError('setup runs locally only')
            if not args.parent or not args.output:raise ValueError('parent page and private output path required')
            result=setup_notion(http,args.parent)
            with args.output.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
            print(json.dumps({'workspace_url':result['workspace_url'],'configured':True}));return 0
        if args.limit<1 or args.slots<0:raise ValueError('invalid work bounds')
        store=NotionTaskStore(http,env['RESEARCH_NOTION_TASKS_SOURCE_ID'])
        reg=registry(env);roster=active_roster(http,env,reg)
        day=datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        if args.mode=='weekly':result=weekly(roster,store,day=day,slots=args.slots)
        else:
            drive=graph_drive(env)
            store.content_loader=lambda ref:drive.read(ref['path'])[0]
            if args.mode=='scan':
                result=scan(roster,store,drive,start=(datetime.fromisoformat(day)-timedelta(days=21)).date().isoformat(),end=day)
                from research.feeds import poll
                feeds=json.loads(env.get('RESEARCH_OFFICIAL_FEEDS_JSON','[]'))
                feed_result=poll(feeds,roster['holdings'],store,drive,now=datetime.now(timezone.utc).isoformat())
                result['feed_cards']=feed_result['cards'];result['coverage_gaps']+=feed_result['coverage_gaps']
            elif args.mode=='local':
                from research.local import run_local_queue
                result=run_local_queue(store,drive,limit=args.limit,subscription_config={
                    'cli_path':env.get('CODEX_CLI_PATH'),'model':env.get('CODEX_MODEL')})
            else:
                from research.context import prepare_task_context
                reports=NotionReports(http,env['RESEARCH_NOTION_REPORTS_SOURCE_ID']);providers=configured_providers(env)
                result={};mapped={h['issuer_id']:h for h in roster['holdings']}
                tasks=select_tasks(store.list(),set(mapped),limit=args.limit,day=day)
                for task in tasks:
                    task['last_attempted_at']=datetime.now(timezone.utc).isoformat()
                    task['retry_requested']=False;store.put(task)
                    model=prepare_task_context(task,store,drive,http,mapped[task['issuer_id']])
                    outcome=process(task['task_id'],store=store,drive=drive,reports=reports,providers=providers,
                                    budget=Budget(store),day=day,model=model)
                    result[outcome['status']]=result.get(outcome['status'],0)+1
        print(json.dumps(result))
        blocked=any(v for k,v in result.items() if k not in {'completed','waiting_subscription','candidates','reused_candidates','screened','deep','deferred','scanned','queued','feed_cards'})
        return 1 if blocked else 0
    except Exception as error:
        print('Research operation incomplete: '+type(error).__name__+'; private details omitted',file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
