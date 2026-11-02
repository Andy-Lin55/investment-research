"""Opt-in v17 entry; no credentials, source access or queue writes on import."""
import argparse
from copy import deepcopy
from datetime import date,datetime,timezone,timedelta
import hashlib,json,os,re
from pathlib import Path
from financials.forecast import digest,number
from research.runtime import Budget,BudgetBlocked
from research.delivery import ServiceError
from research.workflow import StageWait,advance_task,company_state,control,plan_task
from research.contract_v17 import stage_prompt,validate_report,validate_requests,evidence_ids


def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()


def reason(stage,payload,task,*,store,drive,providers,now):
    prompt=stage_prompt(stage,payload,task);round_id=task.get('retrieval_round',0)
    path=task['company_path']+'/04_研究底稿/v17/'+task['task_id'].split(':')[-1]+'/'+stage+'-'+str(round_id)
    input_ref=drive.put_immutable(path+'/request.json',encoded({'contract':'holdings-v17','prompt':prompt,'input_digest':digest(prompt)}))
    if input_ref.get('verified') is not True:raise ValueError('prompt archive not verified')
    if task['kind']=='weekly_deep':
        fingerprint=digest(prompt);request=dict(task_id=task['task_id'],input_fingerprint=fingerprint,
            prompt=prompt,response_path=path+'/subscription-response.json')
        ref=drive.put_immutable(path+'/subscription-request.json',encoded(request))
        task.update(input_fingerprint=fingerprint);task['checkpoints']['request']=ref;store.put(task)
        try:response=json.loads(drive.read(request['response_path'])[0])
        except ServiceError as error:
            if error.status!=404:raise
            raise StageWait('waiting_subscription',['local_subscription_response_required'])
        if response.get('task_id')!=task['task_id'] or response.get('input_fingerprint')!=fingerprint:
            raise StageWait('quality_blocked',['stale_local_candidate'])
        task.setdefault('inference',[]).append({'stage':stage,'actual_model':response.get('actual_model','unreported'),
            'provider':'local_codex_subscription','cost_cny':0,'input_ref':input_ref})
        return json.loads(response['content'])
    if not providers:raise StageWait('waiting_budget',['priced_cloud_provider_not_configured'])
    for provider in providers:
        rates=(provider.input_cny_per_million,provider.output_cny_per_million)
        if any(not number(x) or x<0 for x in rates):continue
        ceiling=((len(prompt.encode())+256)*rates[0]+12000*rates[1])/1e6
        if ceiling<=0:continue
        attempt_id=task['task_id']+':'+stage+':'+str(round_id)
        fresh=store.get(task['task_id'])
        old=next((a for a in fresh['attempts'] if a['attempt_id']==attempt_id),None)
        if old:raise StageWait('waiting_budget',['prior_attempt_needs_confirmation'])
        attempt={'attempt_id':attempt_id,'stage':stage,'status':'inflight','provider':provider.name,'requested_model':provider.model}
        try:fresh=Budget(store).reserve(fresh,ceiling,now,attempt_id,attempt=attempt)
        except BudgetBlocked:raise StageWait('waiting_budget',['model_budget_reached'])
        task.update(fresh)
        try:response=provider(prompt,max_output_tokens=12000)
        except Exception:
            task['attempts'][-1]['status']='unknown_cost';store.put(task)
            raise StageWait('waiting_budget',['unknown_provider_charge_not_retried'])
        usage=(response.get('input_tokens'),response.get('output_tokens'))
        if not response.get('actual_model') or any(type(x) is not int or x<0 for x in usage):
            raise StageWait('waiting_budget',['usage_or_actual_model_unverified'])
        actual=(usage[0]*rates[0]+usage[1]*rates[1])/1e6
        Budget(store).settle(task['task_id'],attempt_id,actual);task.update(store.get(task['task_id']))
        task['attempts'][-1]['status']='charged';task.setdefault('inference',[]).append(dict(stage=stage,
            actual_model=response['actual_model'],provider=provider.name,cost_cny=actual,input_ref=input_ref))
        store.put(task)
        if actual>ceiling+1e-10:raise StageWait('waiting_budget',['provider_charge_exceeds_reservation'])
        try:return json.loads(response['content'])
        except (ValueError,KeyError):raise StageWait('quality_blocked',['invalid_reasoning_json'])
    raise StageWait('waiting_budget',['pricing_unknown'])


def build_adapters(*,store,drive,reports,env,documents,reasoner=None,baseline=None,prepublish=None,retrieval=None,prior_context=None):
    """Connect actual archive, model, report and delivery boundaries to v17 stages."""
    from research.financial_coverage import build_coverage
    from research.forecast_inputs import build_candidate_inputs
    from research.model_templates import select_model_template
    from research.retrieval import plan_retrieval,run_retrieval
    from research.services import configured_providers
    def infer(stage,payload,task):
        return reasoner(stage,payload,task) if reasoner else reason(stage,payload,task,store=store,drive=drive,
            providers=configured_providers(env),now=task.get('updated_at',task['created_at'])[:10])
    def archive(stage,value,task):
        path=task['company_path']+'/04_研究底稿/v17/'+task['task_id'].split(':')[-1]+'/'+stage+'-'+str(task.get('retrieval_round',0))+'-'+digest(value)+'.json'
        return drive.put_immutable(path,encoded(value))
    def coverage(payload,task):
        docs=documents(task)
        if not docs:raise StageWait('waiting_source',['primary_documents_unavailable'])
        prior=prior_context(task) if prior_context else {'items':[],'gaps':[]}
        if task['trigger']['kind']=='weekly_screen':
            versions={d['document_id']:d['revision_id'] for d in docs}
            due=any(q.get('due') and q['due']<=task.get('updated_at',task['created_at'])[:10]
                    for q in task['previous_state'].get('open_questions',[]))
            if (versions==task['previous_state'].get('source_revisions') and not due
                    and not prior['gaps'] and digest(prior['items'])==task['previous_state'].get('context_version',digest([]))):
                return dict(issuer_id=task['issuer_id'],status='no_change',scope='configured_official_sources_only')
            task['kind']='weekly_deep';store.put(task)
        checks=[]
        for doc in docs:
            if doc['issuer_id']!=task['issuer_id']:raise ValueError('document issuer differs')
            checks.append(build_coverage(doc['pages'],profile=task['profile'],document_type=doc['document_type'],
                period_end=doc.get('period_end') or task['created_at'][:10],document_id=doc['document_id']))
        gaps=[doc['document_id']+':'+g for doc,check in zip(docs,checks) for g in check['gaps']]
        if any(not check['input_complete'] for check in checks):
            raise StageWait('waiting_source',gaps)
        return dict(issuer_id=task['issuer_id'],status='partial' if gaps or prior['gaps'] else 'ready',documents=docs,coverage=checks,
            gaps=gaps+prior['gaps'],prior_context=prior['items'],context_version=digest(prior['items']))
    def retrieve(payload,task):
        if retrieval:return retrieval(payload,task)
        p=task['profile'];inventory=p.get('broker_inventory',[])
        return run_retrieval(plan_retrieval({'issuer_id':task['issuer_id'],'question':p['research_question'],
            'as_of':task['created_at'],'queries':[]},inventory,limits={'max_reports':p.get('max_broker_reports',5)}),{},store)
    def model(payload,task):
        if select_model_template(task['profile'])['status']!='supported':
            return dict(issuer_id=task['issuer_id'],status='partial',gaps=['unsupported_template'],model=None)
        raw=infer('model',payload,task);allowed=evidence_ids(payload)
        for fact in raw.get('facts',[]):
            doc=next((d for d in payload['coverage']['documents'] if d['revision_id']==fact.get('source_ref')),None)
            quote=fact.get('quote','')
            if not doc or not quote or not any(quote in p['text'] for p in doc['pages']):
                raise StageWait('quality_blocked',['reported_fact_quote_not_in_original'])
            numeric=[float(x) for x in re.findall(r'[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?',quote.replace(',',''))]
            if not number(fact.get('value')) or not any(abs(x-fact['value'])<=max(1e-8,abs(fact['value'])*1e-10) for x in numeric):
                raise StageWait('quality_blocked',['reported_fact_value_not_in_quote'])
        for a in raw.get('assumptions',[]):
            if not set(a.get('source_refs',[]))<=allowed:raise StageWait('quality_blocked',['assumption_source_not_read'])
        candidate=build_candidate_inputs(raw.get('facts',[]),raw.get('guidance',[]),raw.get('broker_estimates',[]),
            raw.get('assumptions',[]),prior_baseline=task['previous_state'].get('accepted_baseline'),
            as_of=task.get('updated_at',task['created_at'])[:10],profile=task['profile'])
        candidate['candidate_status']=candidate['status'];candidate['status']='ready' if candidate.get('model') and candidate['model']['status']=='ready_for_review' else 'partial'
        candidate['issuer_id']=task['issuer_id'];return candidate
    def judgment(payload,task):
        result=infer('judgment',payload,task);validate_requests(result.get('data_requests',[]))
        return dict(result,issuer_id=task['issuer_id'],status='ready')
    def report(payload,task):
        r=validate_report(infer('report',payload,task),payload,task)
        if task.get('unresolved_data_requests'):r['research_status']='partial'
        return dict(r,issuer_id=task['issuer_id'],status='ready')
    def baseline_check(task):
        if baseline:return baseline(task)
        state=company_state(store,task['issuer_id']);accepted=state.get('accepted_baseline')
        # The accepted model pointer is independent of automatic research publications.
        try:pointer=json.loads(drive.read(task['company_path']+'/research-current.json')[0])
        except ServiceError as error:
            if error.status==404:return accepted
            raise
        expected=(task['previous_state'].get('accepted_baseline') or {}).get('version')
        if pointer.get('model_version')!=expected:raise StageWait('waiting_source',['formal_baseline_needs_reconciliation'])
        return accepted
    def publish(payload,task):
        r=payload['report'];model=payload['model'].get('model');history=task.get('inference') or [{'actual_model':'injected_test_boundary','provider':'injected'}]
        task['publication_key']=task['task_id']+':'+digest(task['stage_refs']);task['report_title']=task['ticker']+' AI研究更新｜未人工采纳'
        doc=payload['coverage']['documents'][0]
        task['card']={'source':{'published_at':doc.get('published_at',task['created_at']),
            'url':doc.get('source',{}).get('url','https://www.notion.so/')},'revision_id':doc['revision_id']}
        task['presentation_version']=2
        analysis=dict(report=r,model_snapshot=model,actual_model=history[-1]['actual_model'],provider=history[-1]['provider'],
            validation={'status':'ready_for_review' if model else 'pending_model'},
            source_index=[dict(revision_id=d['revision_id'],document_id=d['document_id'],url=d.get('source',{}).get('url')) for d in payload['coverage']['documents']])
        files=task.setdefault('publication_files',{})
        for key,value in [('source',payload['coverage']),('result',analysis),('model',model)]:
            if value is not None and key not in files:files[key]=archive('publication-'+key,value,task);store.put(task)
        if model and 'model_workbook' not in files:
            from financials.workbook import export_model
            ref=drive.put_immutable(task['company_path']+'/03_财务模型/候选/'+model['model_version']+'.xlsx',export_model(model))
            if ref.get('verified') is not True:raise ValueError('workbook read-back failed')
            files['model_workbook']=ref;store.put(task)
        attempted=task.get('publication_attempted',False);task['publication_attempted']=True;store.put(task)
        saved=reports.publish(task,analysis,files,candidate=True,lookup_only=attempted)
        if not saved or saved.get('verified') is not True:raise StageWait('waiting_source',['publication_confirmation_required'])
        return dict(saved,input_digest=task['input_digest'],issuer_id=task['issuer_id'])
    return dict(coverage=coverage,retrieval=retrieve,model=model,judgment=judgment,report=report,archive=archive,
        read=lambda ref:json.loads(drive.read(ref['path'])[0]),baseline=baseline_check,publish=publish,
        **({'prepublish':prepublish} if prepublish else {}))


def main(argv=None,*,env=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['doctor','scan','worker','weekly','local'],nargs='?',default='doctor')
    parser.add_argument('--write',action='store_true');parser.add_argument('--env-file',type=Path)
    parser.add_argument('--limit',type=int,default=3);args=parser.parse_args(argv)
    environment=dict(os.environ) if env is None else dict(env)
    if args.write and args.env_file:
        from dotenv import dotenv_values
        environment.update({k:v for k,v in dotenv_values(args.env_file).items() if v is not None})
    if args.mode=='doctor':
        print(json.dumps({'enabled':environment.get('RESEARCH_V17_ENABLED')=='true','contract':'holdings-v17','production_active':False}));return 0
    if not args.write:print('{"written":false,"reason":"Explicit --write required"}');return 0
    if environment.get('RESEARCH_V17_ENABLED')!='true':
        print('{"written":false,"reason":"v17 disabled"}');return 2
    try:
        from research.v17_live import run
        return run(args,environment)
    except Exception as error:
        print(json.dumps({'status':'incomplete','error_type':type(error).__name__}));return 2


if __name__=='__main__':raise SystemExit(main())
