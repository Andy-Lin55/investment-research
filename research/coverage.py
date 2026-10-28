"""Private, metadata-only coverage review; candidates never authorize research."""
from collections import Counter
import csv,io,json,os
from datetime import date
from pathlib import Path,PurePosixPath

from research.inventory import scan_company
from research.services import validate_registry


def build_inventory(records,registry,root,*,observed_at,max_directories=5000,max_depth=6,feeds=()):
    date.fromisoformat(observed_at);validate_registry(registry);root=Path(root)
    if root.name!='投资' or root.is_symlink() or not root.is_dir():raise ValueError('existing 投资 directory required')
    if any(isinstance(n,bool) or not isinstance(n,int) or n<1 for n in (max_directories,max_depth)):
        raise ValueError('positive inventory bounds required')
    root=root.resolve();directories=[];truncated=False;scan_errors=[]
    def onerror(error):scan_errors.append(type(error).__name__)
    for directory,children,_ in os.walk(root,followlinks=False,onerror=onerror):
        current=Path(directory)
        children[:]=sorted(n for n in children if not n.startswith('.') and not (current/n).is_symlink())
        depth=len(current.relative_to(root).parts)
        if depth>=max_depth:
            if children:truncated=True
            children[:]=[]
        if current!=root:
            if len(directories)>=max_directories:truncated=True;break
            directories.append(current)
    counts=Counter(r['ticker'] for r in records)
    mapped={ticker:h for h in registry for ticker in h['tickers']};rows=[]
    for record in records:
        ticker=record['ticker'];h=mapped.get(ticker);gaps=[];files=[]
        name=record.get('name','').strip()
        candidates=[p.relative_to(root.parent).as_posix() for p in directories if name and p.name==name]
        path=h['company_path'] if h else None
        if not h:gaps.append('issuer_mapping_not_confirmed')
        if counts[ticker]>1:gaps.append('duplicate_active_ticker')
        if h:
            parts=PurePosixPath(path).parts[1:];company=root.joinpath(*parts)
            unsafe=any(root.joinpath(*parts[:i+1]).is_symlink() for i in range(len(parts)))
            if unsafe:gaps.append('company_directory_unsafe')
            elif not company.is_dir():gaps.append('company_directory_missing')
            else:
                inventory=scan_company(company);files=inventory['files']
                if inventory['truncated']:gaps.append('company_inventory_truncated')
                if any('error' in f for f in files):gaps.append('company_metadata_unreadable')
        if not h or not h.get('forecast_inputs_path'):gaps.append('forecast_inputs_not_configured')
        information={'trading_flows':{'status':'not_configured'},'policy':{'status':'not_configured'},
                     'fundamentals':{'disclosures':'collector_configured' if h else 'unmapped',
                                     'financial_model':'configured' if h and h.get('forecast_inputs_path') else 'not_configured',
                                     'existing_materials':'metadata_only' if files else 'not_inspected'}}
        for category in ('policy','trading_flows'):
            if h and any(f.get('category')==category and h['issuer_id'] in f.get('issuer_ids',[])
                         and f.get('exposure_rationale') and f.get('url') for f in feeds):
                information[category]['status']='configured_unverified'
        rows.append(dict(ticker=ticker,name=name,notion_url=record.get('notion_url'),
            mapping_status='confirmed' if h else 'pending',issuer_id=h['issuer_id'] if h else None,
            company_path=path,folder_candidates=sorted(candidates),files=files,gaps=gaps,
            information=information))
    return dict(schema_version=1,observed_at=observed_at,active_listings=len(rows),
                confirmed_listings=sum(r['mapping_status']=='confirmed' for r in rows),listings=rows,
                directory_scan_truncated=truncated,directory_scan_errors=scan_errors,
                note='Folder matches are candidates only. Metadata does not prove readability, source authority, cloud sync or coverage completeness.')


def export_inventory(report,output):
    output=Path(output);csvpath=output.with_suffix('.csv')
    if output.suffix!='.json':raise ValueError('private JSON output required')
    if any((p/'.git').exists() for p in output.absolute().parents):
        raise ValueError('holdings exports must stay outside Git working trees')
    for p in (output,csvpath):
        if p.exists() or p.is_symlink():raise FileExistsError('existing export is never overwritten')
        if not p.parent.is_dir() or any(x.is_symlink() for x in p.parents):raise ValueError('existing non-symlink output directory required')
    def cell(value):
        value=str(value)
        return "'"+value if value.lstrip().startswith(('=','+','-','@','\t','\r','\n')) else value
    stream=io.StringIO();writer=csv.writer(stream)
    writer.writerow(['持仓代码','公司名称','映射状态','已确认目录','候选目录（待确认）','已有资料数','缺口'])
    for row in report['listings']:
        writer.writerow([cell(v) for v in (row['ticker'],row['name'],row['mapping_status'],row['company_path'] or '',
            '；'.join(row['folder_candidates']),len(row['files']),'；'.join(row['gaps']))])
    for p,body in ((output,json.dumps(report,ensure_ascii=False,indent=2).encode()),
                   (csvpath,stream.getvalue().encode('utf-8-sig'))):
        fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as f:f.write(body)
