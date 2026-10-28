"""Single-publisher, checkpointed research; resumes delivery without repaying AI."""
from copy import deepcopy
from datetime import datetime,timezone
import json,time,re

from financials.forecast import digest,number,project
from research.delivery import ServiceError
from research.runtime import (BudgetBlocked,VersionConflict,assert_baseline,research_prompt,validate_report)


def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()


def process(task_id,*,store,drive,reports,providers,budget,day,model=None,followups=None):
    task=store.get(task_id)
    if not task:raise ValueError('unknown durable task')
    if task['status'] in {'retry_release','published'}:return task
    fingerprint=digest([task['card']['revision_id'],task.get('context_digest'),model,'holdings-v1'])
    if task.get('input_fingerprint')!=fingerprint:
        task.update(input_fingerprint=fingerprint,publication_key=task_id+':'+fingerprint,
                    analysis=None,checkpoints={},status='pending')
        store.put(task)
    if task['status'] in {'quality_blocked','completed'}:return task
    def state(name,error=None):
        nonlocal task
        task['status']=name
        if error:task['last_error_type']=type(error).__name__
        store.put(task);return task
    if model is not None:
        try:
            rebuilt=project(model['baseline'],model['assumptions'],as_of=model['as_of'])
            if (model['issuer_id']!=task['issuer_id'] or model['as_of']>day
                    or rebuilt['model_version']!=model['model_version'] or rebuilt['scenarios']!=model['scenarios']):
                raise ValueError('supplied model identity or reproducibility failed')
        except (ValueError,KeyError,TypeError) as error:return state('quality_blocked',error)
        if model['as_of']<task['card']['source']['published_at'][:10]:return state('pending_model_refresh')
    substantive=re.sub(r'\[PDF page \d+\]','',task['evidence'].get('content','')).strip()
    if not substantive or task.get('needs_full_article'):return state('pending_evidence')
    if task.get('needs_context'):return state('pending_context')
    root=task['company_path']
    try:
        try:current=json.loads(drive.read(root+'/research-current.json')[0]).get('model_version')
        except ServiceError as e:
            if e.status!=404:raise
            current=None
        assert_baseline(task['baseline_version'],current)
    except VersionConflict as error:return state('baseline_conflict',error)
    except Exception as error:return state('retry_delivery',error)
    version=root+'/04_研究底稿/'+task_id+'/'+fingerprint
    # A forecast identity excludes comparison history. Preserve each comparison
    # snapshot as a distinct immutable workpaper rather than colliding by model ID.
    model_path=root+'/03_财务模型/候选/'+model['model_version']+'/'+digest(model) if model is not None else None
    if model is not None and 'model' not in task['checkpoints']:
        try:
            ref=drive.put_immutable(model_path+'.json',encoded(model))
            if ref.get('verified') is not True:raise ValueError('model upload not verified')
            task['checkpoints']['model']=ref
            store.put(task)
        except Exception as error:return state('retry_delivery',error)
    if model is not None and 'model_workbook' not in task['checkpoints']:
        try:
            from financials.workbook import export_model
            ref=drive.put_immutable(model_path+'.xlsx',export_model(model))
            if ref.get('verified') is not True:raise ValueError('model workbook not verified')
            task['checkpoints']['model_workbook']=ref;store.put(task)
        except Exception as error:return state('retry_delivery',error)
    if 'source' not in task['checkpoints']:
        try:
            source=drive.put_immutable(version+'/evidence.json',encoded(task['evidence']))
            if source.get('verified') is not True:raise ValueError('source upload not verified')
            task['checkpoints']['source']=source;store.put(task)
        except Exception as error:return state('retry_delivery',error)
    if not task.get('analysis'):
        card=deepcopy(task['card']);card['source_text']=task['evidence']['content']
        card['extraction_coverage']=task['evidence'].get('extraction')
        if task['evidence'].get('exposure_rationale'):
            card['company_exposure_mapping']={'rationale':task['evidence']['exposure_rationale'],
                                              'status':'explicit registry mapping; not independent exposure verification'}
        prompt=research_prompt([card],model,context=task.get('context'),previous=task.get('previous_report'))
        if task['kind']=='weekly_deep':
            try:
                request=dict(task_id=task_id,input_fingerprint=fingerprint,prompt=prompt,
                             response_path=version+'/subscription-response.json')
                if 'request' not in task['checkpoints']:
                    task['checkpoints']['request']=drive.put_immutable(version+'/subscription-request.json',encoded(request))
                    store.put(task)
                try:response=json.loads(drive.read(request['response_path'])[0])
                except ServiceError as e:
                    if e.status!=404:raise
                    return state('waiting_subscription')
                if response['task_id']!=task_id or response['input_fingerprint']!=fingerprint:
                    raise ValueError('stale local candidate')
                report=json.loads(response['content'])
                validation=validate_report(report,[task['card'],*task.get('context',[])],model=model)
                task['analysis']=dict(report=report,validation=validation,actual_model=response['actual_model'],
                    provider='local_codex_subscription',model_provenance=response.get('model_provenance','unreported'),
                    input_tokens=None,output_tokens=None,cost_cny=0,prompt_version='holdings-v1',
                    evidence_revision=task['card']['revision_id'],model_version=model.get('model_version') if model else None,
                    model_snapshot=model)
                store.put(task)
            except (ValueError,KeyError,TypeError) as error:return state('quality_blocked',error)
            except Exception as error:return state('retry_delivery',error)
    if not task.get('analysis'):
        ids={a['attempt_id'] for a in task['attempts']}
        if (any(c['attempt_id'] not in ids for c in task['charges'])
                or any(a.get('status')=='inflight' for a in task['attempts'])):
            return state('pending_attempt_confirmation')
        successful=False
        for provider in providers:
            if len(task['attempts'])>=2:break
            rates=(provider.input_cny_per_million,provider.output_cny_per_million)
            if any(not number(r) or r<0 for r in rates):return state('pending_pricing')
            cap=4000;ceiling=((len(prompt.encode())+256)*rates[0]+cap*rates[1])/1e6
            if ceiling<=0:return state('pending_pricing')
            attempt_id=task_id+':'+str(len(task['attempts']))
            attempt=dict(attempt_id=attempt_id,provider=provider.name,requested_model=provider.model,
                         started_at=datetime.now(timezone.utc).isoformat(),status='inflight')
            try:task=budget.reserve(task,ceiling,day,attempt_id,attempt=attempt)
            except BudgetBlocked:return store.get(task_id)
            start=time.monotonic()
            try:response=provider(prompt,max_output_tokens=cap)
            except Exception as error:
                task['attempts'][-1].update(status='unknown_cost',error_type=type(error).__name__)
                store.put(task);continue
            task['attempts'][-1].update(status='response_received',elapsed_seconds=time.monotonic()-start)
            store.put(task)
            actual=response.get('actual_model')
            usage=(response.get('input_tokens'),response.get('output_tokens'))
            if (not actual or actual!=provider.model or any(isinstance(x,bool) or not isinstance(x,int) or x<0 for x in usage)):
                return state('pending_usage_verification')
            cost=(usage[0]*rates[0]+usage[1]*rates[1])/1e6
            budget.settle(task_id,attempt_id,cost);task=store.get(task_id)
            if cost>ceiling+1e-10:return state('pending_budget_reconciliation')
            try:
                report=json.loads(response['content'])
                validation=validate_report(report,[task['card'],*task.get('context',[])],model=model)
            except (ValueError,KeyError,TypeError) as error:return state('quality_blocked',error)
            task['analysis']=dict(report=report,validation=validation,actual_model=actual,provider=provider.name,
                                  input_tokens=usage[0],output_tokens=usage[1],cost_cny=cost,
                                  elapsed_seconds=time.monotonic()-start,prompt_version='holdings-v1',
                                  model_version=model.get('model_version') if model else None,
                                  model_snapshot=model,
                                  evidence_revision=task['card']['revision_id'],
                                  generated_at=datetime.now(timezone.utc).isoformat())
            store.put(task);successful=True;break
        if not successful:return state('retry_provider' if len(task['attempts'])<2 else 'provider_attempts_exhausted')
    if 'result' not in task['checkpoints']:
        try:
            result=drive.put_immutable(version+'/research.json',encoded(task['analysis']))
            if result.get('verified') is not True:raise ValueError('result upload not verified')
            task['checkpoints']['result']=result;store.put(task)
        except Exception as error:return state('retry_delivery',error)
    if 'report' not in task['checkpoints']:
        uncertain=task['checkpoints'].get('report_attempted',False)
        task['checkpoints']['report_attempted']=True;store.put(task)
        try:
            page=reports.publish(task,task['analysis'],task['checkpoints'],candidate=True,lookup_only=uncertain)
            if not page:return state('pending_report_confirmation')
            task['checkpoints']['report']=page;store.put(task)
        except Exception as error:return state('retry_delivery',error)
    if followups is not None:
        from research.followups import items_from_report
        for item in items_from_report(task,task['analysis']['report']):
            receipts=task['checkpoints'].setdefault('followups',{})
            if item['key'] in receipts:continue
            attempted=task['checkpoints'].setdefault('followups_attempted',[])
            uncertain=item['key'] in attempted
            if not uncertain:attempted.append(item['key']);store.put(task)
            try:
                receipt=followups.ensure(item,report_ref=task['checkpoints']['report'],lookup_only=uncertain)
                if receipt is None:return state('pending_followup_confirmation')
                if receipt.get('verified') is not True:raise ValueError('follow-up not verified')
                receipts[item['key']]=receipt;store.put(task)
            except Exception as error:return state('retry_delivery',error)
    # A delivered facts notice is not completion of mandatory model research.
    if task['analysis'].get('model_provenance')=='unreported':return state('pending_model_provenance')
    if task.get('context_gaps') and task['analysis']['validation']['status']=='ready_for_review':return state('pending_context')
    return state('completed' if task['analysis']['validation']['status']=='ready_for_review' else 'pending_model')
