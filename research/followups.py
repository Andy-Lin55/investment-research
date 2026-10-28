"""Durable human-owned research questions; no investment execution or auto-close."""
from datetime import date
import json,unicodedata
from research.delivery import DeliveryError,rich,plain,text_property
from financials.forecast import digest

STATES=('待看','跟进中','有疑问','已完成','搁置')
SCHEMA={'Key':{'title':{}},'Issuer':{'rich_text':{}},'Payload':{'rich_text':{}},
        'Summary':{'rich_text':{}},'HumanNotes':{'rich_text':{}},'Report':{'url':{}},
        'State':{'select':{'options':[{'name':s} for s in STATES]}},'Due':{'date':{}}}


def identity(item):
    def normalized(value):return ' '.join(unicodedata.normalize('NFKC',value).split())
    return digest([item['issuer_id'],item['kind'],*[normalized(item.get(k,'')) for k in ('condition','action','question')]])


def items_from_report(task,report):
    items=[]
    for action in report['next_actions']:
        items.append(dict(kind='conditional_action',condition=action['condition'],action=action['action'],
                          due=action['due'],evidence=action['evidence']))
    for question in report['open_questions']:
        if not isinstance(question,str) or not question.strip():raise ValueError('nonempty text question required')
        items.append(dict(kind='open_question',question=question,due=None,evidence=[]))
    unique={}
    for item in items:
        item.update(issuer_id=task['issuer_id'],origin_task=task['task_id'],origin_publication=task['publication_key'])
        item['key']=identity(item);unique.setdefault(item['key'],item)
    return list(unique.values())


class NotionFollowups:
    def __init__(self,http,data_source):
        if not data_source:raise ValueError('dedicated follow-up data source required')
        self.http=http;self.data_source=data_source

    def _query(self,filter=None):
        cursor=None;seen=set()
        while True:
            body={'page_size':100}
            if filter:body['filter']=filter
            if cursor:body['start_cursor']=cursor
            result=self.http('POST',f'/data_sources/{self.data_source}/query',body=body)
            yield from result['results']
            if not result.get('has_more'):break
            cursor=result.get('next_cursor')
            if not cursor or cursor in seen:raise DeliveryError('invalid follow-up pagination')
            seen.add(cursor)

    def _decode(self,page):
        p=page['properties'];item=json.loads(plain(p['Payload']['rich_text']))
        if (item.get('key')!=identity(item) or plain(p['Key']['title'])!=item['key']
                or plain(p['Issuer']['rich_text'])!=item['issuer_id']):raise DeliveryError('follow-up identity differs')
        state=(p.get('State',{}).get('select') or {}).get('name')
        if state not in STATES:raise DeliveryError('follow-up state needs human reconciliation')
        due=(p.get('Due',{}).get('date') or {}).get('start')
        if due:date.fromisoformat(due[:10])
        return dict(item,state=state,due=due[:10] if due else None,
                    human_notes=plain(p.get('HumanNotes',{}).get('rich_text',[])),url=page.get('url'))

    def ensure(self,item,*,report_ref,lookup_only=False):
        if item.get('key')!=identity(item):raise ValueError('invalid follow-up identity')
        rows=list(self._query({'property':'Key','title':{'equals':item['key']}}))
        if len(rows)>1:raise DeliveryError('duplicate follow-up keys; reconcile manually')
        if rows:
            saved=self.http('GET','/pages/'+rows[0]['id']);decoded=self._decode(saved)
            if decoded['key']!=item['key']:raise DeliveryError('follow-up readback differs')
            return dict(id=saved['id'],url=saved.get('url'),verified=True,key=item['key'])
        if lookup_only:return None
        payload=json.dumps(item,ensure_ascii=False,sort_keys=True,allow_nan=False)
        if len(payload)>30000:raise ValueError('bounded follow-up payload required')
        summary=item.get('question') or item['condition']+' → '+item['action']
        properties={'Key':{'title':rich(item['key'])},'Issuer':text_property(item['issuer_id']),
                    'Payload':text_property(payload),'Summary':text_property(summary),
                    'State':{'select':{'name':'待看'}},'Due':{'date':{'start':item['due']} if item.get('due') else None},
                    'Report':{'url':report_ref.get('url')}}
        page=self.http('POST','/pages',body={'parent':{'data_source_id':self.data_source},'properties':properties})
        saved=self.http('GET','/pages/'+page['id']);decoded=self._decode(saved)
        if any(decoded.get(k)!=v for k,v in item.items()):raise DeliveryError('follow-up payload readback differs')
        return dict(id=saved['id'],url=saved.get('url'),key=item['key'],verified=True)

    def unresolved(self,issuer):
        rows=[self._decode(p) for p in self._query({'property':'Issuer','rich_text':{'equals':issuer}})]
        if any(r['issuer_id']!=issuer for r in rows):raise DeliveryError('follow-up issuer scope differs')
        return sorted([r for r in rows if r['state'] not in {'已完成','搁置'}],key=lambda r:(r.get('due') or '9999',r['key']))

    def due_issuers(self,day):
        date.fromisoformat(day);rows=[self._decode(p) for p in self._query()]
        return {r['issuer_id'] for r in rows if r['state'] not in {'已完成','搁置'} and r.get('due') and r['due']<=day}
