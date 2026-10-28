"""Explicit acceptance, version-aware release/rollback and trial evidence gate."""
from datetime import date
import json
from research.delivery import ServiceError,DeliveryError,VersionConflict


def trial_readiness(rows,*,as_of):
    cutoff=date.fromisoformat(as_of)
    real=[r for r in rows if r.get('environment')=='pilot_live' and date.fromisoformat(r['day'])<=cutoff]
    verified=[r for r in real if r.get('delivery_verified') is True]
    days=[date.fromisoformat(r['day']) for r in verified]
    issuers={r['issuer_id'] for r in verified};kinds={r['kind'] for r in verified}
    span=(max(days)-min(days)).days if days else 0
    ready=span>=14 and len(issuers)>=3 and {'earnings','incremental','no_change'}<=kinds and len(real)==len(verified)
    return dict(ready=ready,real_issuers=len(issuers),elapsed_observed_days=span,
                unverified_runs=len(real)-len(verified),missing_classes=sorted({'earnings','incremental','no_change'}-kinds),
                note='Synthetic tests, calendar waiting without runs, and unverified deliveries cannot complete the pilot.')


def release_candidate(task,store,drive,reports,*,enabled=False,accepted_model_version):
    if not enabled:raise ValueError('formal publication disabled')
    if (task.get('status') not in {'completed','retry_release'} or task.get('human_review')!='已确认'
            or task.get('context_gaps') or task.get('analysis',{}).get('validation',{}).get('status')!='ready_for_review'
            or not accepted_model_version or task['analysis'].get('model_version')!=accepted_model_version
            or task['analysis'].get('model_provenance')=='unreported'):
        raise ValueError('exact model acceptance, completed checks and human confirmation required')
    if any(not task['checkpoints'].get(k,{}).get('verified') for k in ('source','result')):
        raise ValueError('verified private workpapers required')
    path=task['company_path']+'/research-current.json';key=task['publication_key']+':formal'
    value=dict(model_version=accepted_model_version,publication_key=key,
               workpaper=task['checkpoints']['result'],publication_status='pending_notion')
    try:
        try:
            raw,meta=drive.read(path);old=json.loads(raw)
            if old.get('model_version')==accepted_model_version:
                if old.get('publication_key')!=key:raise VersionConflict('candidate identity differs')
            else:
                if old.get('model_version')!=task['baseline_version']:raise VersionConflict('baseline changed')
                drive.put_immutable(task['company_path']+'/05_历史版本/'+str(old['model_version'])+'/pointer.json',raw)
                drive.update_pointer(path,value,expected_version=old['model_version'],expected_etag=meta['eTag'])
        except ServiceError as error:
            if error.status!=404 or task['baseline_version'] is not None:raise
            drive.put_immutable(path,json.dumps(value,ensure_ascii=False,sort_keys=True).encode())
        formal=dict(task,publication_key=key)
        attempted=task['checkpoints'].get('formal_report_attempted',False)
        task['checkpoints']['formal_report_attempted']=True;store.put(task)
        page=reports.publish(formal,task['analysis'],task['checkpoints'],candidate=False,lookup_only=attempted)
        if not page:raise DeliveryError('formal report outcome still uncertain')
        task['checkpoints']['formal_report']=page;store.put(task)
        raw,meta=drive.read(path);current=json.loads(raw)
        if current.get('model_version')!=accepted_model_version or current.get('publication_key')!=key:
            raise VersionConflict('formal pointer changed during delivery')
        value.update(publication_status='complete',report=page)
        drive.update_pointer(path,value,expected_version=accepted_model_version,expected_etag=meta['eTag'])
        task['status']='published';store.put(task);return task
    except Exception:
        task['status']='retry_release';store.put(task);raise


def rollback_pointer(drive,company_path,snapshot,*,expected_current):
    """Explicit pointer restore only. Existing reports/models remain archived."""
    path=company_path+'/research-current.json';raw,meta=drive.read(path)
    if json.loads(raw).get('model_version')!=expected_current:raise VersionConflict('newer baseline present')
    drive.put_immutable(company_path+'/05_历史版本/'+expected_current+'/pre-rollback-pointer.json',raw)
    drive.update_pointer(path,snapshot,expected_version=expected_current,expected_etag=meta['eTag'])
