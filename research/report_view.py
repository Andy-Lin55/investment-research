"""Native Notion report blocks, readable citations and inline financial tables."""
import json,re
from collections import Counter
from urllib.parse import urlsplit
from financials.forecast import number
from research.harness import evidence_role


def rich(text,*,url=None,bold=False):
    text=str(text)
    if url:
        p=urlsplit(url)
        if p.scheme not in {'http','https'} or not p.hostname or p.username or p.password:url=None
    return [dict(type='text',text=dict(content=text[i:i+1800],**({'link':{'url':url}} if url else {})),
                 annotations={'bold':bold}) for i in range(0,len(text),1800)]


def readable(text):
    return str(text).replace('model为null','尚未提供已审核预测模型').replace(
        '材料human_review为pending，automatic_validation为structure_only','资料尚待人工核验，自动检查仅覆盖结构与引用')


def block(kind,text):return {'object':'block','type':kind,kind:{'rich_text':rich(readable(text))}}
def value(v):return '—' if v is None else f'{v:,.2f}' if number(v) else str(v)


def visible_periods(periods):
    def end(i):return periods[i][:4]+'-12-31' if '年度A' in periods[i] else periods[i][:10]
    ordered=sorted(range(len(periods)),key=end)
    if len(periods)<=5:return ordered
    annual=[i for i in ordered if '年度A' in periods[i] or '-12-31期末A' in periods[i]]
    interim=[i for i in ordered if i not in annual]
    selected=annual[-2:]+interim[-2:]
    if interim:
        latest=periods[interim[-1]];previous=str(int(latest[:4])-1)+latest[4:]
        if previous in periods:selected.append(periods.index(previous))
    return sorted(set(selected),key=end)


def visible_rows(title,rows):
    if len(rows)<=20:return rows
    patterns={'利润表':'营业收入|营业成本|毛利润|毛利率|营业利润|利润总额|所得税|净利润|每股收益|研发|财务费用|投资收益',
              '资产负债表':'货币资金|应收账款|存货|固定资产|商誉|资产总计|资产合计|负债合计|负债总计|权益合计|股东权益|借款',
              '现金流量表':'现金流量净额|现金及现金等价物|购建固定|分配股利|汇率变动|经营性应收|经营性应付|存货的减少|偿还债务|取得借款|销售商品|购买商品'}
    return [r for r in rows if re.search(patterns[title],r['label'])]


def table(headers,rows,*,url=None):
    cells=[headers,*rows]
    if len(headers)>10 or len(cells)>100:raise ValueError('split wide/long tables explicitly')
    if any(len(row)!=len(headers) for row in cells):raise ValueError('table row width differs')
    children=[{'object':'block','type':'table_row','table_row':{'cells':[rich(v,url=url,bold=i==0) for v in row]}}
              for i,row in enumerate(cells)]
    return {'object':'block','type':'table','table':{'table_width':len(headers),'has_column_header':True,
                                                  'has_row_header':False,'children':children}}


def render(task,result,files):
    r=result['report'];sources=[];by_id={}
    for c in [task['card'],*task.get('context',[])]:
        rid=c.get('revision_id')
        if not rid or rid in by_id:continue
        source=c.get('source',{});index=len(sources)+1
        role=evidence_role(c)
        label={'primary_disclosure':'第一手原件','scheduling_metadata':'任务排程记录',
               'historical_financial_workbook':'历史模型数据','sell_side_report':'卖方观点／原文待人工核验'}.get(role,'既有研究／待核验资料')
        item=dict(index=index,url=source.get('url'),locator=source.get('locator') or source.get('url') or '资料引用',kind=label)
        by_id[rid]=item;sources.append(item)
    def cites(refs):
        tokens=[]
        for rid in refs:
            s=by_id.get(rid)
            if not s:raise ValueError('unresolved presentation evidence reference')
            tokens+=rich('['+str(s['index'])+'] ',url=s['url'])
        return {'object':'block','type':'paragraph','paragraph':{'rich_text':tokens}}
    blocks=[block('heading_1',task.get('report_title') or task['ticker']+'｜持仓研究候选'),
            block('quote','待人工核验｜资料截止 '+task['card']['source']['published_at'][:10]+
                  '；仅覆盖所列资料，不代表最新公司全貌'),block('heading_2','核心问题')]
    blocks.append(block('paragraph',task.get('research_question') or '经营变化是否改变既有持仓逻辑、财务预期与后续关注条件？'))
    blocks += [block('heading_2','结论与持仓逻辑'),block('paragraph',r['conclusion']),block('paragraph',r['thesis'])]
    broker_contract=task.get('research_brief',{}).get('version')=='holdings-broker-v1'
    if broker_contract and r.get('independent_hypothesis'):
        h=r['independent_hypothesis'];blocks.append(block('heading_2','独立假设与可推翻条件'))
        for key,label in [('claim','判断'),('mechanism','作用机制'),('falsifier','何种事实会改变判断')]:
            blocks.append(block('paragraph',label+'：'+h[key]))
        blocks.append(cites(h['evidence']))
    if broker_contract and r.get('broker_comparisons'):
        blocks.append(block('heading_2','卖方观点与我们的分歧'))
        blocks.append(block('quote','仅比较所列机构与报告日期，不代表市场共识；卖方预测不等于公司已披露事实或已审核的自有模型。'))
        broker_map={c['revision_id']:c for c in task.get('context',[]) if c.get('broker')}
        for row in r['broker_comparisons']:
            b=broker_map[row['evidence'][0]]['broker']
            blocks.append(block('heading_3',b['org_name']+'｜'+b['published_at']))
            blocks.append(block('paragraph',b['title']+'；第 '+ '、'.join(map(str,row['pages']))+' 页'))
            for key,label in [('broker_view','机构观点／预测'),('own_view','我们的判断'),('model_implication','模型影响'),('verification_needed','待核验')]:
                blocks.append(block('paragraph',label+'：'+row[key]))
            blocks.append(cites(row['evidence']))
    blocks.append(block('heading_2','财务模型与三表数据'))
    snapshots=[c['financial_snapshot'] for c in task.get('context',[]) if c.get('financial_snapshot')]
    if not snapshots:blocks.append(block('paragraph','尚未接入可展示的历史三表；原有 Excel 附件不等于已提取模型数据。'))
    for s in snapshots:
        if s['issuer_id']!=task['issuer_id']:raise ValueError('financial snapshot issuer differs')
        blocks.append(block('paragraph','历史实际／缓存值 A｜数据截点 '+s['as_of']+'；按 Excel 展示单位，空缺用“—”，尚待人工核验。'))
        for t in s['tables']:
            blocks.append(block('heading_3',t['title']))
            blocks.append(block('paragraph','币种：'+(t.get('currency') or '待核验')+'；合并口径：'+
                {'consolidated':'合并','parent':'母公司'}.get(t.get('scope'),'待核验')+'。单位按各科目列示；未确认原始缩放时，不据此跨口径合并。'))
            retained=visible_rows(t['title'],t['rows']);indices=visible_periods(t['periods'])
            if not retained:
                blocks.append(block('paragraph','该表的关键科目尚未完成映射，请核对原始 Excel。'));continue
            for start in range(0,len(indices),5):
                subset=indices[start:start+5]
                rows=[[row['label'],row['unit'],*[value(row['values'][i]) for i in subset]] for row in retained]
                for offset in range(0,len(rows),40):
                    blocks.append(table(['科目','单位',*[t['periods'][i] for i in subset]],rows[offset:offset+40],url=s['source'].get('url')))
            omitted=len(t['rows'])-len(retained)
            if omitted or len(indices)<len(t['periods']):
                omitted_periods=[p for i,p in enumerate(t['periods']) if i not in indices]
                blocks.append(block('paragraph','正文按收入／利润、资产负债、现金转换／偿债科目名称筛选；期间取最近两年年度、最近两期及最新中期的上年同期。未展示 '+str(omitted)+' 科目；未展示期间：'+('、'.join(omitted_periods) or '无')+'。完整 '+str(len(t['rows']))+' 科目／'+str(len(t['periods']))+' 期、逐格地址保留在原始 Excel 和底稿中，未将未展示项目按零处理。'))
        blocks.append(block('paragraph','数据为历史 Excel 缓存值，不能替代已审核预测；货币资金与现金等价物分别保留。'))
        if s.get('gaps'):
            gaps=Counter(g.rsplit(':',1)[-1] for g in s['gaps'])
            labels={'missing_cached_value':'空白缓存格','invalid_cached_value':'无效缓存格',
                    'missing_currency':'币种标签待核验','missing_scope':'合并口径待核验',
                    'missing_raw_amount_unit':'原始金额单位待核验','missing_raw_share_unit':'原始股数单位待核验',
                    'missing_raw_per_share_unit':'原始每股单位待核验','missing_historical_source':'原始财务来源标签待核验'}
            summary='；'.join(labels.get(k,'其他口径／期间缺口')+' '+str(v)+' 项' for k,v in gaps.items())
            blocks.append(block('paragraph','模型数据缺口：'+summary+'。空白不等于零，逐项位置留在完整底稿中。'))
    model=result.get('model_snapshot')
    if not model or not model.get('scenarios'):
        blocks.append(block('quote','预测与估值待补：尚无已核验的预测假设；历史 Excel 数据不自动转为当前预测或目标价。'))
    else:
        for case,title in [('base','基准'),('bull','乐观'),('bear','谨慎')]:
            years=model['scenarios'][case];blocks.append(block('heading_3',title+'情景预测 F｜'+model['currency']+'；待人工核验'))
            metrics=[('收入／亿原币',lambda y:y['income']['revenue']/1e8),('归母净利／亿原币',lambda y:y['income']['parent_net_income']/1e8),
                     ('每股盈利／原币',lambda y:y['income']['eps']),('期末现金／亿原币',lambda y:y['balance']['cash']/1e8),
                     ('债务／亿原币',lambda y:y['balance']['debt']/1e8),('权益／亿原币',lambda y:y['balance']['equity']/1e8),
                     ('经营现金流／亿原币',lambda y:y['cashflow']['operating']/1e8),('投资现金流／亿原币',lambda y:y['cashflow']['investing']/1e8),
                     ('融资现金流／亿原币',lambda y:y['cashflow']['financing']/1e8),('资金缺口／亿原币',lambda y:y['funding_shortfall']/1e8),
                     ('每股估值／原币',lambda y:y['valuation']['price']),('资产负债差额／原币',lambda y:y['checks']['balance_difference']),
                     ('现金勾稽差额／原币',lambda y:y['checks']['cash_difference'])]
            for start in range(0,len(years),4):
                subset=years[start:start+4]
                blocks.append(table(['指标',*[str(y['year'])+'F' for y in subset]],[[label,*[value(fn(y)) for y in subset]] for label,fn in metrics]))
            assumptions=model['assumptions'][case]
            keys=[('growth','收入增速',True),('cogs_ratio','营业成本率',True),('opex_ratio','费用率',True),
                  ('tax_rate','所得税率',True),('capex_ratio','资本开支／收入',True),
                  ('receivable_days','应收周转天数',False),('inventory_days','存货周转天数',False),
                  ('payable_days','应付周转天数',False),('pe_multiple','估值市盈率／倍',False)]
            for start in range(0,len(assumptions),4):
                subset=assumptions[start:start+4];rows=[]
                for field,label,percent in keys:
                    values=[value(y['values'][field]*100)+'%' if percent else value(y['values'][field]) for y in subset]
                    source_text='；'.join(str(y['year'])+'：'+y['sources'][field] for y in subset)
                    rows.append([label,*values,source_text])
                blocks.append(table(['关键假设',*[str(y['year'])+'F' for y in subset],'依据（各期）'],rows))
        if model.get('assumption_changes'):
            blocks.append(block('heading_3','假设变化'))
            changes=model['assumption_changes']
            for start in range(0,len(changes),40):
                blocks.append(table(['情景／年度','假设','原值','新值','依据'],[[x['scenario']+'/'+str(x['year']),x['field'],value(x['previous']),value(x['current']),x.get('source') or '待补'] for x in changes[start:start+40]]))
    for key,title in [('changes','变化、证据与模型影响'),('counterevidence','反证与替代解释')]:
        blocks.append(block('heading_2',title))
        for i,item in enumerate(r[key],1):
            blocks.append(block('paragraph',str(i)+'. '+item['text']));blocks.append(cites(item['evidence']))
    blocks.append(block('heading_2','下一步关注与条件'))
    blocks.append(table(['触发条件','研究行动','复核日期','引用'],[[x['condition'],x['action'],x['due'],' '.join('['+str(by_id[e]['index'])+']' for e in x['evidence'])] for x in r['next_actions']]))
    blocks.append(block('heading_2','尚未解决的问题'))
    blocks.extend(block('bulleted_list_item',q) for q in r['open_questions'])
    if r.get('data_requests'):
        blocks.append(block('heading_3','下一轮需补充的数据'))
        blocks.extend(block('bulleted_list_item',x['need']+'；用途：'+x['why']) for x in r['data_requests'])
    blocks.append(block('heading_2','资料与模型口径'))
    if broker_contract and sources:
        appendix=table(['编号','资料类型','来源／位置'],
            [['['+str(s['index'])+']',s['kind'],s['locator']] for s in sources])
        for row,s in zip(appendix['table']['children'][1:],sources):
            row['table_row']['cells'][2]=rich(s['locator'],url=s['url'])
        blocks.append(appendix)
    else:
        for s in sources:
            blocks.append({'object':'block','type':'paragraph','paragraph':{'rich_text':rich('['+str(s['index'])+'] '+s['kind']+'｜'+s['locator'],url=s['url'])}})
    for key in ('source','result','model','model_workbook'):
        if key in files:
            ref=files[key];blocks.append({'object':'block','type':'paragraph','paragraph':{'rich_text':rich({'source':'原文底稿','result':'研究底稿','model':'模型数据','model_workbook':'Excel 模型'}[key],url=ref.get('url'))}})
    gaps=task.get('context_gaps',[])
    if gaps:blocks.append(block('paragraph','待补资料：'+'；'.join('预测假设尚未配置' if g=='forecast_inputs_not_configured' else g for g in gaps)))
    extraction=task.get('evidence',{}).get('extraction')
    if extraction and extraction.get('partial'):blocks.append(block('paragraph','原件提取范围为部分页面／章节，请结合完整原件复核。'))
    blocks.append(block('paragraph','人工状态：'+str(task.get('human_review','待看'))+'；自动检查不代表事实和投资判断已获人工确认。'))
    if result.get('model_provenance')=='unreported' or result.get('actual_model')=='not_reported_by_codex_cli':
        blocks.append(block('paragraph','生成渠道：本机订阅；实际模型身份尚未由服务回报，保留待核验状态。'))
    if len(blocks)>95 or len(json.dumps(blocks,ensure_ascii=False).encode())>450000:
        raise ValueError('report exceeds a single bounded Notion publication; explicit split required')
    return blocks
