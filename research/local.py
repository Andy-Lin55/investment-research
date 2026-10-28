"""Subscription-only optional work; submit immutable candidates, no queue writes."""
import json,os
from pathlib import PurePosixPath


def run_local_queue(store,drive,*,limit,subscription=None):
    if os.getenv('GITHUB_ACTIONS')=='true' or os.getenv('CI','').lower()=='true':
        raise ValueError('subscription credentials are local only')
    if subscription is None:
        from data_ingestion.codex_client import call_codex
        subscription=call_codex
    counts=dict(candidates=0,waiting_subscription=0,waiting_context=0,conflicts=0)
    tasks=[t for t in store.list() if t['kind']=='weekly_deep' and t['status']!='completed']
    for task in tasks[:limit]:
        ref=task['checkpoints'].get('request')
        if not ref:counts['waiting_context']+=1;continue
        request=json.loads(drive.read(ref['path'])[0])
        expected=str(PurePosixPath(ref['path']).parent/'subscription-response.json')
        if (request['task_id']!=task['task_id'] or request['input_fingerprint']!=task['input_fingerprint']
                or request['response_path']!=expected):raise ValueError('request identity or response path differs')
        try:content=subscription(request['prompt'])
        except Exception:content=''
        if not content:counts['waiting_subscription']+=1;continue
        result=dict(task_id=task['task_id'],input_fingerprint=task['input_fingerprint'],content=content,
                    actual_model='not_reported_by_codex_cli',requested_model=os.getenv('CODEX_MODEL') or None,
                    model_provenance='unreported')
        try:
            drive.put_immutable(expected,json.dumps(result,ensure_ascii=False,allow_nan=False).encode())
            counts['candidates']+=1
        except Exception:counts['conflicts']+=1
    return counts
