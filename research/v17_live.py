"""Live adapters for the separately gated v17 discovery and cloud publisher."""
from copy import deepcopy
from datetime import date,datetime,timezone,timedelta
import hashlib,json,re
from financials.forecast import digest
from research import sources
from research.delivery import NotionTaskStore,NotionReports,ServiceError
from research.financial_coverage import FULL
from research.workflow import StageWait,control,company_state,plan_task,advance_task
from research.profiles import resolve_profile
from research.discovery import discover_events


def load_prior_context(profile,drive,http):
    from pathlib import PurePosixPath
    from research.context import notion_text
    root=profile['company_path']+'/';items=[];gaps=[]
    files=profile.get('context_files',[])
    if len(files)>30 or len(profile.get('context_pages',[]))>10:raise ValueError('prior-context bounds exceeded')
    for entry in files:
        path=entry['path']
        if not path.startswith(root) or '..' in PurePosixPath(path).parts:raise ValueError('prior context outside company')
        try:
            raw,meta=drive.read(path)
            if 'remoteItem' in meta:raise ValueError('shortcut requires explicit mapping')
            parsed=sources.extract(raw,kind=PurePosixPath(path).suffix.lstrip('.'),max_chars=30000)
            items.append(dict(revision_id=hashlib.sha256(raw).hexdigest(),evidence_kind='prior_research',source_text=parsed['text'],
                partial=parsed['partial'],source={'path':path,'url':meta.get('webUrl')},note='Historical analysis, not current disclosed facts.'))
        except Exception:gaps.append('prior_file_unavailable:'+PurePosixPath(path).name)
    for page in profile.get('context_pages',[]):
        try:
            text=notion_text(http,page)
            items.append(dict(revision_id=digest([page,text]),evidence_kind='prior_research',source_text=text[:30000],
                partial=len(text)>30000,source={'url':'https://www.notion.so/'+page.replace('-','')},note='Prior analysis, not primary facts.'))
        except Exception:gaps.append('prior_notion_unavailable')
    if profile.get('financial_workbook_path'):
        path=profile['financial_workbook_path']
        if not path.startswith(root) or '..' in PurePosixPath(path).parts:raise ValueError('workbook outside company')
        try:
            from research.financial_snapshot import from_workbook
            data,meta=drive.read(path);sha=hashlib.sha256(data).hexdigest()
            snap=from_workbook(data,issuer_id=profile['issuer_id'],source={'path':path,'sha256':sha,'locator':path,'url':meta.get('webUrl')},
                as_of=profile['financial_workbook_as_of'],expected_company=profile['financial_workbook_company'])
            items.append(dict(revision_id=sha,evidence_kind='historical_financial_workbook',financial_snapshot=snap,
                note='Cached actuals with explicit metadata gaps, not accepted forecasts.'))
        except Exception:gaps.append('financial_workbook_unavailable_or_metadata_incomplete')
    return {'items':items,'gaps':gaps}


class FeedSource:
    def __init__(self,store,drive,feed,*,fetch=None):
        from research.feeds import official_feed
        self.store=store;self.drive=drive;self.feed=feed;self.fetch=fetch or official_feed
    def discover(self,profile,start,end):
        from email.utils import parsedate_to_datetime
        import xml.etree.ElementTree as ET
        from research.evidence import TAXONOMY
        f=self.feed
        if (profile['issuer_id'] not in f.get('issuer_ids',[]) or not f.get('exposure_rationale')
                or f.get('category') not in TAXONOMY or f.get('subtype') not in TAXONOMY[f['category']]):
            raise ValueError('explicit source classification and issuer exposure required')
        raw=self.fetch(f['url']);sha=hashlib.sha256(raw).hexdigest()
        self.drive.put_immutable('投资/持仓研究共享/'+f['category']+'/'+sha+'.xml',raw)
        root=ET.fromstring(raw);events=[];complete=True
        for item in root.findall('.//item')[:100]:
            try:
                url=item.findtext('link');title=item.findtext('title');stamp=parsedate_to_datetime(item.findtext('pubDate'))
                if not url or not title or stamp.tzinfo is None:raise ValueError('feed item identity/time incomplete')
                if not start<=stamp.date().isoformat()<=end:continue
                body=self.fetch(url);kind='pdf' if body.startswith(b'%PDF') else 'html';revision=hashlib.sha256(body).hexdigest()
                original=self.drive.put_immutable(profile['company_path']+'/01_原始资料/外部信息/'+revision+'.'+kind,body)
                parsed=sources.extract(body,kind=kind)
                if parsed['partial'] or not parsed['text']:raise ValueError('full feed article unavailable')
                doc=dict(issuer_id=profile['issuer_id'],document_id=url,revision_id=revision,document_type='政策' if f['category']=='policy' else '公告',
                    published_at=stamp.isoformat(),period_end=None,pages=[{'page':1,'text':parsed['text']}],
                    source={'url':url,'original':original},exposure_rationale=f['exposure_rationale'])
                ref=self.drive.put_immutable(profile['company_path']+'/01_原始资料/外部信息/'+revision+'.json',json.dumps(doc,ensure_ascii=False).encode())
                if f.get('material'):
                    events.append(dict(issuer_id=profile['issuer_id'],event_id=url,revision_id=revision,kind='material',
                        published_at=stamp.isoformat(),documents=[ref],category=f['category'],subtype=f['subtype']))
                else:
                    self.store.put(control('v17:observation:'+digest([profile['issuer_id'],url,revision]),profile['issuer_id'],
                        observation={'category':f['category'],'subtype':f['subtype'],'original':ref,'title':title}))
            except Exception:complete=False
        if len(root.findall('.//item'))>100:complete=False
        return {'events':events,'complete':complete}


def read_ref(drive,ref):
    raw=drive.read(ref['path'])[0]
    if ref.get('verified') is not True or hashlib.sha256(raw).hexdigest()!=ref.get('sha256'):
        raise ValueError('original/extraction reference checksum differs')
    return json.loads(raw)


def period_end(title,kind,profile):
    years=re.findall(r'(?<!\d)(20\d{2})(?!\d)',title)
    if not years:return None
    year=int(years[0])
    if re.search(r'第三|三季|Q3',title,re.I):return f'{year}-09-30'
    if re.search(r'第一|一季|Q1',title,re.I):return f'{year}-03-31'
    if re.search(r'半年|中期|INTERIM|Q2',title,re.I):return f'{year}-06-30'
    if kind=='年度报告':return f"{year}-{profile.get('fiscal_year_end','12-31')}"
    return None


class OfficialSource:
    def __init__(self,store,drive,*,collectors=None,downloader=None):
        self.store=store;self.drive=drive
        self.collectors=collectors or {'CN':sources.cninfo,'HK':sources.hkex,'US':sources.sec}
        self.downloader=downloader or sources.download_primary

    def metadata(self,profile,start,end):
        result=[]
        for ticker in profile.get('active_tickers',profile['tickers']):
            market='HK' if ticker.endswith('.HK') else 'CN' if ticker.endswith(('.SZ','.SH','.BJ')) else 'US'
            for raw in self.collectors[market](ticker,start,end):
                row=dict(raw,ticker=ticker,issuer_id=profile['issuer_id'])
                row['document_type']=sources.document_type(row['title'],row.get('document_type'))
                if row['document_type']=='公告':continue
                row['metadata_revision']=digest([ticker,row['document_id'],row['title'],row['url'],row['published_at']])
                result.append(row)
        return list({r['document_id']:r for r in result}.values())

    def original(self,profile,row,*,force=False):
        key='v17:original:'+digest([profile['issuer_id'],row['document_id']]);saved=self.store.get(key)
        if saved and not force and saved['metadata_revision']==row['metadata_revision']:
            return saved['reference'],read_ref(self.drive,saved['reference'])
        raw=self.downloader(row['url']);kind='pdf' if raw.startswith(b'%PDF') else 'html'
        sha=hashlib.sha256(raw).hexdigest();base=profile['company_path']+'/01_原始资料/公司公告/'+sha
        original=self.drive.put_immutable(base+'.'+kind,raw)
        parsed=sources.extract(raw,kind=kind,max_chars=2000000)
        if parsed['partial'] or not parsed['text'].strip():raise ValueError('complete readable source extraction required')
        chunks=re.split(r'\[PDF page (\d+)\]\s*',parsed['text'])
        pages=[{'page':int(chunks[i]),'text':chunks[i+1]} for i in range(1,len(chunks),2)] if kind=='pdf' else [{'page':1,'text':parsed['text']}]
        document=dict(issuer_id=profile['issuer_id'],document_id=row['document_id'],revision_id=sha,
            document_type=row['document_type'],title=row['title'],published_at=row['published_at'],
            metadata_revision=row['metadata_revision'],period_end=period_end(row['title'],row['document_type'],profile),
            pages=pages,total_pages=parsed['total_pages'],source={'url':row['url'],'original':original},ticker=row['ticker'])
        ref=self.drive.put_immutable(base+'.json',json.dumps(document,ensure_ascii=False,allow_nan=False).encode())
        self.store.put(control(key,profile['issuer_id'],metadata_revision=row['metadata_revision'],reference=ref))
        return ref,document

    def discover(self,profile,start,end):
        rows=sorted(self.metadata(profile,start,end),key=lambda r:r['published_at'],reverse=True)
        grouped={};fetched=0;complete=True
        for index,row in enumerate(rows):
            saved=self.store.get('v17:original:'+digest([profile['issuer_id'],row['document_id']]))
            refresh=index<3 or not saved or saved['metadata_revision']!=row['metadata_revision']
            if refresh and fetched>=20:complete=False;continue
            try:
                ref,doc=self.original(profile,row,force=refresh)
                if refresh:fetched+=1
            except Exception:complete=False;continue
            day=row['published_at'][:10];grouped.setdefault(day,[]).append((ref,doc))
        events=[]
        for day,items in grouped.items():
            docs=[doc for _,doc in items];is_financial=any(d['document_type'] in FULL for d in docs)
            events.append(dict(issuer_id=profile['issuer_id'],ticker=docs[0]['ticker'],event_id='official:'+day,
                revision_id=digest(sorted((d['document_id'],d['revision_id']) for d in docs)),
                kind='earnings' if is_financial else 'material',published_at=max(d['published_at'] for d in docs),
                documents=[ref for ref,_ in items],category='fundamentals'))
        return {'events':events,'complete':complete}

    def documents(self,task):
        p=task['profile'];now=task.get('updated_at',task['created_at'])[:10]
        end=date.fromisoformat(now);start=end.replace(year=end.year-5,day=min(end.day,28)).isoformat()
        metadata=self.metadata(p,start,now)
        task['source_snapshot']={r['document_id']:r['metadata_revision'] for r in metadata}
        picked=[]
        groups=[lambda r:r['document_type'] in {'年度报告','10-K','20-F','10-K/A','20-F/A'},
                lambda r:r['document_type'] in FULL and r['document_type'] not in {'年度报告','10-K','20-F','10-K/A','20-F/A'},
                lambda r:r['document_type'] not in FULL]
        for selector,limit in zip(groups,(5,8,3)):
            picked.extend(sorted(filter(selector,metadata),key=lambda r:r['published_at'],reverse=True)[:limit])
        docs={}
        for ref in task['trigger'].get('documents',[]):
            doc=read_ref(self.drive,ref);docs[doc['document_id']]=doc
        for row in picked:
            if row['document_id'] not in docs:
                _,doc=self.original(p,row);docs[doc['document_id']]=doc
        self.store.put(task)
        return list(docs.values())

    def prepublish(self,payload,task):
        now=task.get('updated_at',task['created_at'])[:10]
        try:
            start=(date.fromisoformat(now)-timedelta(days=365*5+2)).isoformat()
            latest=self.metadata(task['profile'],start,now)
            before=task.get('source_snapshot',{})
            if any(before.get(r['document_id'])!=r['metadata_revision'] for r in latest):
                raise ValueError('new or revised official disclosure')
            known={d['document_id']:d for d in payload['coverage']['documents']}
            for row in sorted(latest,key=lambda r:r['published_at'],reverse=True)[:3]:
                if row['document_id'] in known:
                    _,fresh=self.original(task['profile'],row,force=True)
                    if fresh['revision_id']!=known[row['document_id']]['revision_id']:raise ValueError('source bytes revised')
        except Exception:
            raise StageWait('waiting_source',['prepublication_source_check_failed_or_stale']) from None


def broker_adapter(store,drive,env):
    from research.broker_sources import FxBaogao,archive_report,load_report
    from research.retrieval import plan_retrieval,run_retrieval
    def run(payload,task):
        profile=task['profile'];inventory=[]
        def convert(entry):
            parts=re.split(r'\[PDF page (\d+)\]\s*',entry['source_text'])
            return dict(entry,issuer_id=task['issuer_id'],report_id=entry['broker']['report_id'],
                org_name=entry['broker']['org_name'],published_at=entry['broker']['published_at'],
                original_sha256=entry['broker']['original_sha256'],original_verified=True,source_quality='original',
                pages=[{'page':int(parts[i]),'text':parts[i+1]} for i in range(1,len(parts),2)])
        for ref in profile.get('broker_report_refs',[])[:6]:
            try:inventory.append(convert(load_report(drive,ref,company_path=profile['company_path'],issuer_id=task['issuer_id'])))
            except Exception:pass  # explicit limited coverage returned below; never admit a failed original
        enabled=env.get('RESEARCH_BROKER_SOURCES_ENABLED')=='true' and bool(env.get('FXBAOGAO_API_KEY'))
        queries=list(profile.get('broker_queries',[]))
        if not queries and profile.get('company_aliases'):queries=[profile['company_aliases'][0]+' '+profile['tickers'][0].split('.')[0]]
        requests=task.get('retrieval_requests',[])
        if enabled and requests:
            queries.extend(profile['tickers'][0]+' '+r['need'] for r in requests if r['kind']=='sell_side_report')
        adapters={}
        if enabled:
            client=FxBaogao(env['FXBAOGAO_API_KEY'],max_calls=12,max_downloads=6)
            def acquire(row):
                raw=client.download(row['report_id']);parsed=sources.extract(raw,kind='pdf',max_chars=120000)
                cover=parsed['text'].split('[PDF page 2]')[0]
                aliases=profile.get('company_aliases',[])
                symbols=[t for t in profile['tickers']]+[t.split('.')[0] for t in profile['tickers']]
                if (not aliases or not any(a.casefold() in cover.casefold() for a in aliases)
                        or not any(re.search(r'(?<!\d)'+re.escape(t)+r'(?!\d)',cover,re.I) for t in symbols)
                        or re.search(r'AI翻译|机器翻译|機器翻譯|自动翻译|AUTOMATED TRANSLATION',cover,re.I)):
                    path=profile['company_path']+'/01_原始资料/卖方研报/待核验/'+hashlib.sha256(raw).hexdigest()+'.pdf'
                    drive.put_immutable(path,raw)
                    return dict(row,issuer_id=task['issuer_id'],original_verified=False,source_quality='quarantined')
                ref=archive_report(client,drive,company_path=profile['company_path'],issuer_id=task['issuer_id'],
                    report=row,identity_confirmed=True,cached_pdf=raw)
                return convert(load_report(drive,ref,company_path=profile['company_path'],issuer_id=task['issuer_id']))
            adapters={'search':lambda query:client.search([{'question':profile['research_question'],'keywords':query}]),'acquire':acquire}
        plan=plan_retrieval({'issuer_id':task['issuer_id'],'question':profile['research_question'],
            'as_of':task['created_at'],'queries':queries if enabled else []},inventory,
            limits={'max_reports':profile.get('max_broker_reports',5)})
        return run_retrieval(plan,adapters,store)
    return run


def run(args,env):
    from research.cli import notion_http,graph_drive,registry,active_roster
    from research.v17 import build_adapters
    http=notion_http(env);drive=graph_drive(env)
    store=NotionTaskStore(http,env['RESEARCH_NOTION_TASKS_SOURCE_ID'],content_loader=lambda ref:drive.read(ref['path'])[0])
    if args.mode=='local':
        from research.local import run_local_queue
        result=run_local_queue(store,drive,limit=args.limit);print(json.dumps(result));return 0
    roster=active_roster(http,env,registry(env,drive));profiles=[];gaps=list(roster['unmapped'])
    feeds=json.loads(env.get('RESEARCH_OFFICIAL_FEEDS_JSON','[]'))
    for holding in roster['holdings']:
        try:
            p=resolve_profile(holding,holding.get('research_profile',{}))
            p['sources']=list(dict.fromkeys(p['sources']+['feed:'+digest(f['url'])[:16] for f in feeds if p['issuer_id'] in f.get('issuer_ids',[])]))
            profiles.append(p)
        except ValueError:gaps.append(holding['issuer_id']+':profile_incomplete')
    now=datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    if args.limit<1 or args.limit>20:raise ValueError('bounded positive work limit required')
    source=OfficialSource(store,drive)
    if args.mode=='scan':
        source_adapters={'official':source.discover}
        for feed in feeds:source_adapters['feed:'+digest(feed['url'])[:16]]=FeedSource(store,drive,feed).discover
        result=discover_events(profiles,store=store,adapters=source_adapters,as_of=now)
    elif args.mode=='weekly':
        queued=0
        for p in profiles:
            event=dict(issuer_id=p['issuer_id'],event_id='weekly:'+now,revision_id=now,kind='weekly_screen',published_at=now)
            task=plan_task(event,p,company_state(store,p['issuer_id']),as_of=now)
            if not store.get(task['task_id']):store.put(task);queued+=1
        result={'queued':queued,'screened':len(profiles)}
    else:
        reports=NotionReports(http,env['RESEARCH_NOTION_REPORTS_SOURCE_ID'])
        adapters=build_adapters(store=store,drive=drive,reports=reports,env=env,documents=source.documents,
            prepublish=source.prepublish,retrieval=broker_adapter(store,drive,env),
            prior_context=lambda task:load_prior_context(task['profile'],drive,http))
        active={p['issuer_id'] for p in profiles};result={}
        candidates=[t for t in store.list() if t.get('workflow_contract')=='holdings-v17' and t['issuer_id'] in active
            and t.get('record_type')=='research_task' and t['status'] not in {'completed','no_change','quality_blocked','baseline_conflict'}
            and not(t['status']=='partial' and t.get('next_state'))]
        for task in sorted(candidates,key=lambda t:(t['priority'],t.get('last_attempted_at',''),t['created_at']))[:args.limit]:
            task['last_attempted_at']=datetime.now(timezone.utc).isoformat();task['updated_at']=now;store.put(task)
            outcome=advance_task(task['task_id'],store=store,adapters=adapters,now=now)
            result[outcome['status']]=result.get(outcome['status'],0)+1
    if gaps:result['coverage_gaps']=len(gaps)
    print(json.dumps(result,ensure_ascii=False))
    return 1 if gaps or result.get('gaps') else 0
