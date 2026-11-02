"""v17 staged workflow, reusing the existing single-publisher task store."""
from copy import deepcopy
from datetime import date
from financials.forecast import digest

STAGES=('coverage','retrieval','model','judgment','report')
KINDS={'initial','earnings','guidance','correction','material','weekly_screen','weekly_deep','manual','due_question'}


class StageWait(RuntimeError):
    def __init__(self,status,gaps):
        if status not in {'partial','waiting_source','waiting_budget','waiting_subscription','quality_blocked'}:
            raise ValueError('explicit blocking state required')
        self.status=status;self.gaps=list(gaps);super().__init__(status)


def control(key,issuer,**values):
    return dict(task_id=key,issuer_id=issuer,kind='manual',record_type='control',status='pending',
                charges=[],attempts=[],checkpoints={},**values)


def company_state(store,issuer):
    return deepcopy((store.get('v17:company:'+issuer) or {}).get('research_state',{}))


def plan_task(event: dict, profile: dict, state: dict, *, as_of: str) -> dict:
    date.fromisoformat(as_of)
    if event.get('issuer_id')!=profile['issuer_id'] or event.get('kind') not in KINDS:
        raise ValueError('mapped issuer and supported event kind required')
    if not event.get('event_id') or not event.get('revision_id'):raise ValueError('versioned source event required')
    identity=digest([profile['issuer_id'],event['event_id'],event['revision_id'],event['kind']])
    return dict(schema_version=2,workflow_contract='holdings-v17',record_type='research_task',task_id='v17:'+identity,
        issuer_id=profile['issuer_id'],ticker=event.get('ticker',profile['tickers'][0]),
        company_path=profile['company_path'],kind='weekly_deep' if event['kind']=='weekly_deep' else 'manual',
        trigger=deepcopy(event),profile=deepcopy(profile),previous_state=deepcopy(state),
        input_digest=digest([event,profile,state]),status='pending',priority=0 if event['kind'] in
            {'earnings','guidance','correction','material'} else 2,created_at=as_of,
        baseline_version=(state.get('accepted_baseline') or {}).get('version'),
        charges=[],attempts=[],checkpoints={},stage_refs={},human_review='pending',research_question=profile['research_question'])


def build_next_state(previous: dict, result: dict, *, manifest: dict) -> dict:
    if manifest.get('verified') is not True or not manifest.get('input_digest') or not manifest.get('id'):
        raise ValueError('verified publication read-back required')
    if manifest.get('issuer_id') and manifest['issuer_id']!=result.get('issuer_id'):
        raise ValueError('research state issuer mismatch')
    state=deepcopy(previous);questions={q['id']:q for q in state.get('open_questions',[])}
    for q in result.get('questions',[]):
        if not isinstance(q,dict) or not q.get('id') or not q.get('need'):raise ValueError('identified follow-up required')
        if q.get('resolved') is not True:questions[q['id']]=deepcopy(q)
    state['open_questions']=list(questions.values())
    if 'source_revisions' in result:state['source_revisions']=deepcopy(result['source_revisions'])
    for key in ('holding_logic','context_version'):
        if key in result:state[key]=deepcopy(result[key])
    state['last_published_research']=dict(manifest,review_status='unadopted')
    return state


def advance_task(task_id: str, *, store, adapters: dict, now: str) -> dict:
    date.fromisoformat(now);task=store.get(task_id)
    if not task or task.get('workflow_contract')!='holdings-v17':raise ValueError('v17 durable task required')
    if task['status'] in {'completed','no_change'} or task.get('next_state') and task['status']=='partial':return task
    payload={'event':deepcopy(task['trigger']),'profile':task['profile'],'previous':task['previous_state']}
    def save(status,**fields):
        task.update(status=status,updated_at=now,**fields);store.put(task);return task
    try:
        for stage in STAGES:
            task['running_stage']=stage
            if stage in task['stage_refs']:
                ref=task['stage_refs'][stage]
                output=adapters['read'](ref) if 'path' in ref else deepcopy(ref['value'])
                if digest(output)!=ref['output_digest']:raise ValueError('checkpoint digest mismatch')
            else:
                store.put(task)
                if stage not in adapters:raise StageWait('waiting_source',['adapter_missing:'+stage])
                output=adapters[stage](payload,task)
                if not isinstance(output,dict) or output.get('issuer_id')!=task['issuer_id']:
                    raise ValueError('stage output identity required')
                if output.get('status')=='no_change':
                    if task['trigger']['kind']!='weekly_screen':raise ValueError('mandatory event cannot be skipped as no change')
                    return save('no_change',screened_at=now)
                if output.get('status') not in {'ready','partial'}:raise ValueError('stage readiness required')
                ref=adapters['archive'](stage,output,task) if 'archive' in adapters else {'value':deepcopy(output)}
                if 'archive' in adapters and ref.get('verified') is not True:raise ValueError('unverified stage archive')
                task['stage_refs'][stage]=dict(ref,output_digest=digest(output));store.put(task)
            payload[stage]=output
            if stage=='judgment' and output.get('data_requests'):
                from research.contract_v17 import validate_requests
                requests=validate_requests(output['data_requests'])
                if task.get('retrieval_round',0)<2:
                    task['retrieval_round']=task.get('retrieval_round',0)+1
                    task['retrieval_requests']=requests
                    task.setdefault('stage_history',[]).append(deepcopy(task['stage_refs']))
                    task['stage_refs']={k:v for k,v in task['stage_refs'].items() if k=='coverage'}
                    store.put(task)
                    return advance_task(task_id,store=store,adapters=adapters,now=now)
                task['unresolved_data_requests']=requests;task['research_status']='partial';store.put(task)
            if stage=='report' and (task.get('research_status')=='partial' or output.get('research_status')=='partial'):
                task['research_status']='partial'
        expected=task['previous_state'].get('accepted_baseline')
        if 'baseline' not in adapters:raise StageWait('waiting_source',['baseline_check_unavailable'])
        if adapters['baseline'](task)!=expected:return save('baseline_conflict')
        if 'prepublish' in adapters:adapters['prepublish'](payload,task)
        if 'publication' not in task['checkpoints']:
            if 'publish' not in adapters:raise StageWait('waiting_source',['publication_adapter_missing'])
            manifest=adapters['publish'](payload,task)
            if manifest.get('verified') is not True or manifest.get('input_digest')!=task['input_digest']:
                raise ValueError('publication not verified against frozen inputs')
            task['checkpoints']['publication']=manifest;store.put(task)
        result=dict(payload['report'],issuer_id=task['issuer_id'])
        result['source_revisions']={d['document_id']:d['revision_id'] for d in payload['coverage'].get('documents',[])}
        result['context_version']=payload['coverage'].get('context_version')
        new=build_next_state(task['previous_state'],result,manifest=task['checkpoints']['publication'])
        current=company_state(store,task['issuer_id'])
        if current and current.get('last_published_research') not in (task['previous_state'].get('last_published_research'),new.get('last_published_research')):
            return save('baseline_conflict')
        store.put(control('v17:company:'+task['issuer_id'],task['issuer_id'],research_state=new))
        return save('partial' if task.get('research_status')=='partial' else 'completed',next_state=new)
    except StageWait as error:return save(error.status,gaps=error.gaps)
    except Exception as error:return save('retryable_failure',last_error_type=type(error).__name__)
