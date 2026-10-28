"""Explicit local forecast review; private exclusive outputs and no cloud writes."""
import argparse,json,os,sys
from pathlib import Path
from financials.workbook import export_model,import_assumptions


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['export','import'])
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--workbook',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        output=args.output.absolute()
        if (not output.parent.is_dir() or output.is_symlink()
                or any(p.is_symlink() or (p/'.git').exists() for p in output.parents)):
            raise ValueError('existing private directory outside Git required')
        suffix='.xlsx' if args.mode=='export' else '.json'
        if output.suffix!=suffix:raise ValueError('output extension differs')
        if args.model.stat().st_size>5_000_000:raise ValueError('bounded model input required')
        model=json.loads(args.model.read_text())
        if args.mode=='export':data=export_model(model)
        else:
            if not args.workbook or args.workbook.stat().st_size>10_000_000:raise ValueError('bounded workbook required')
            new=import_assumptions(args.workbook.read_bytes(),model)
            inputs=dict(baseline=new['baseline'],scenarios=new['assumptions'],as_of=new['as_of'],
                        original_model_version=model['model_version'],human_review='pending')
            data=json.dumps(inputs,ensure_ascii=False,indent=2,allow_nan=False).encode()
        fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(data)
        print(json.dumps({'saved':True,'human_review':'pending'}));return 0
    except Exception as error:
        print('Model review incomplete: '+type(error).__name__+'; private details omitted',file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
