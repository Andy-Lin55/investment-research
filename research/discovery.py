"""Independent source checkpoints: failed scans never mean no new information."""
from datetime import date,timedelta
from financials.forecast import digest
from research.workflow import control,company_state,plan_task


def discover_events(profiles: list[dict], *, store, adapters: dict, as_of: str) -> dict:
    end=date.fromisoformat(as_of);counts=dict(queued=0,scanned=0,gaps=[],status='no_change')
    for profile in profiles:
        issuer=profile['issuer_id']
        for source in profile['sources']:
            key='v17:scan:'+digest([issuer,source]);old=store.get(key) or {}
            last=old.get('covered_until')
            start=(date.fromisoformat(last)-timedelta(days=21) if last else end-timedelta(days=365)).isoformat()
            try:
                if source not in adapters:raise ValueError('configured source unavailable')
                result=adapters[source](profile,start,as_of)
                if not isinstance(result.get('events'),list):raise ValueError('explicit source event list required')
                for event in result['events']:
                    task=plan_task(event,profile,company_state(store,issuer),as_of=as_of)
                    if not store.get(task['task_id']):store.put(task);counts['queued']+=1
                if result.get('complete') is not True:raise ValueError('source coverage incomplete')
                store.put(control(key,issuer,source=source,covered_until=as_of,scanned_at=as_of))
                counts['scanned']+=1
            except Exception as error:
                counts['gaps'].append(dict(issuer_id=issuer,source=source,error_type=type(error).__name__))
                # Keep the last successful watermark, even after a partial ingestion.
                store.put(control(key,issuer,source=source,covered_until=last,last_failed_at=as_of))
        state=company_state(store,issuer)
        for question in state.get('open_questions',[]):
            if question.get('due') and question['due']<=as_of:
                event=dict(issuer_id=issuer,event_id='due:'+question['id'],revision_id=digest(question),
                           kind='due_question',question=question,published_at=as_of)
                task=plan_task(event,profile,state,as_of=as_of)
                if not store.get(task['task_id']):store.put(task);counts['queued']+=1
    counts['status']='partial' if counts['gaps'] else 'ready' if counts['queued'] else 'no_change'
    return counts
