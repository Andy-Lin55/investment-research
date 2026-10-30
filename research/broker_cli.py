"""Explicit broker search/acquisition; separate from scheduled production jobs."""
import argparse,json,os
from pathlib import Path
import sys
from research.broker_sources import FxBaogao,archive_report


def _output(path):
    if path is None:raise ValueError('private output required')
    root=Path(__file__).resolve().parents[1];target=path.resolve()
    if path.is_symlink() or path.exists() or target==root or root in target.parents or not target.parent.is_dir():
        raise ValueError('new private output outside repository required')
    return target


def _save(path,value):
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['search','archive'])
    p.add_argument('--env-file',type=Path);p.add_argument('--key-file',type=Path)
    p.add_argument('--matrix',type=Path);p.add_argument('--selection',type=Path)
    p.add_argument('--cached-pdf',type=Path);p.add_argument('--output',type=Path)
    p.add_argument('--write',action='store_true');args=p.parse_args(argv)
    if args.mode=='archive' and not args.write:print('{"written":false,"reason":"Explicit --write required"}');return 0
    try:
        env=dict(os.environ)
        from dotenv import dotenv_values
        if args.env_file:env.update({k:v for k,v in dotenv_values(args.env_file).items() if v is not None})
        if args.mode=='archive' and env.get('RESEARCH_BROKER_SOURCES_ENABLED')!='true':
            print('Broker acquisition disabled; no side effects',file=sys.stderr);return 2
        output=_output(args.output)
        key=env.get('FXBAOGAO_API_KEY')
        if not key and args.key_file:key=dotenv_values(args.key_file).get('FXBAOGAO_API_KEY')
        if args.mode=='search':
            if not args.matrix:raise ValueError('question matrix required')
            matrix=json.loads(args.matrix.read_text())
            result=FxBaogao(key).search(matrix)
            _save(output,result);print(json.dumps({'candidates':len(result),'written':True}));return 0
        if not args.selection:raise ValueError('explicit confirmed selection required')
        selected=json.loads(args.selection.read_text())
        if selected.get('identity_confirmed') is not True:raise ValueError('confirm report issuer before acquisition')
        from research.cli import graph_drive
        client=None if args.cached_pdf else FxBaogao(key)
        raw=None
        if args.cached_pdf:
            if args.cached_pdf.stat().st_size>40*1024*1024:raise ValueError('cached original too large')
            raw=args.cached_pdf.read_bytes()
        drive=graph_drive(env)
        result=archive_report(client,drive,company_path=selected['company_path'],issuer_id=selected['issuer_id'],
            report=selected['report'],identity_confirmed=True,cached_pdf=raw)
        _save(output,result);print('{"archived":true,"verified":true}');return 0
    except Exception as error:
        print('Broker operation incomplete: '+type(error).__name__+'; private details omitted',file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
