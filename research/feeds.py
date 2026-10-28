"""Configured official feeds; exposure mappings are explicit, never inferred."""
from datetime import datetime,timezone,timedelta
from email.utils import parsedate_to_datetime
import hashlib,json
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from research.runtime import enqueue
from research.evidence import TAXONOMY
from research.delivery import ServiceError
from research.sources import extract


def official_feed(url):
    import requests
    p=urlsplit(url);host=p.hostname or ''
    if p.scheme!='https' or p.username or p.password or p.port not in {None,443} or not (
        host in {'www.gov.cn','www.federalreserve.gov','www.sec.gov','www.sfc.hk','www.hkma.gov.hk'}
        or host.endswith(('.gov.cn','.gov','.europa.eu'))):raise ValueError('explicit official HTTPS feed required')
    try:r=requests.get(url,timeout=(15,45),allow_redirects=False)
    except requests.RequestException:raise ServiceError(0) from None
    if r.status_code!=200:raise ServiceError(r.status_code)
    if len(r.content)>10*1024*1024:raise ValueError('feed size bound reached')
    return r.content


def poll(feeds,holdings,store,drive,*,fetch=official_feed,now):
    current=datetime.fromisoformat(now);mapped={h['issuer_id']:h for h in holdings}
    counts=dict(cards=0,coverage_gaps=0)
    if not feeds:counts['coverage_gaps']=1;return counts
    article_cache={}
    for feed in feeds:
        category=feed.get('category');subtype=feed.get('subtype')
        if (category not in TAXONOMY or subtype not in TAXONOMY[category] or not feed.get('issuer_ids')
                or not feed.get('exposure_rationale') or any(i not in mapped for i in feed['issuer_ids'])):
            counts['coverage_gaps']+=1;continue
        try:
            raw=fetch(feed['url']);root=ET.fromstring(raw)
            shared=drive.put_immutable('投资/持仓研究共享/'+category+'/'+hashlib.sha256(raw).hexdigest()+'.xml',raw)
            for item in root.findall('.//item'):
                title=item.findtext('title');url=item.findtext('link');published=item.findtext('pubDate')
                if not title or not url or not published:counts['coverage_gaps']+=1;continue
                stamp=parsedate_to_datetime(published)
                if stamp.tzinfo is None:counts['coverage_gaps']+=1;continue
                if stamp>current or stamp<current-timedelta(days=21):continue
                # Feed text is metadata/excerpt, not a claim of full article ingestion.
                content='RSS title: '+title+'\nRSS excerpt: '+(item.findtext('description') or '')
                for issuer in feed['issuer_ids']:
                    h=mapped[issuer]
                    event=dict(issuer_id=issuer,ticker=h['active_tickers'][0] if h.get('active_tickers') else h['tickers'][0],
                        title=title,source_url=url,document_id=url,document_type='政策' if category=='policy' else '公告',
                        published_at=stamp.isoformat(),ingested_at=now,content=content,category=category,subtype=subtype,
                        is_material=bool(feed.get('material')),source_locator='Feed excerpt; full article not ingested',
                        exposure_rationale=feed['exposure_rationale'],shared_original=shared,
                        content_scope='feed_excerpt_only')
                    task=enqueue(store,event,h,kind='mandatory_event' if feed.get('material') else 'manual')
                    task['status']='pending_evidence' if feed.get('material') else 'evidence_only'
                    task['needs_full_article']=True;store.put(task);counts['cards']+=1
                    if feed.get('material'):
                        try:
                            if url not in article_cache:
                                original=fetch(url);kind='pdf' if original.startswith(b'%PDF') else 'html'
                                parsed=extract(original,kind=kind)
                                archived=drive.put_immutable('投资/持仓研究共享/'+category+'/'+hashlib.sha256(original).hexdigest()+'.'+kind,original)
                                ref=drive.put_immutable('投资/持仓研究共享/'+category+'/'+hashlib.sha256(parsed['text'].encode()).hexdigest()+'.json',
                                    json.dumps(parsed,ensure_ascii=False).encode())
                                article_cache[url]=(parsed,archived,ref)
                            parsed,archived,ref=article_cache[url]
                            resolved=enqueue(store,dict(event,content=parsed['text'],content_scope='full_article_extract',
                                original=archived,text_ref=ref,extraction={k:v for k,v in parsed.items() if k!='text'}),h)
                            if task['task_id']!=resolved['task_id']:
                                task.update(status='superseded_source_resolved',superseded_by=resolved['task_id']);store.put(task)
                        except Exception:counts['coverage_gaps']+=1
        except Exception:counts['coverage_gaps']+=1
    return counts
