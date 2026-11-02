"""Readable research updates with program-computed model tables in the body."""
from research.report_view import block,table,value,rich


def render(task,result,files):
    r=result['report'];blocks=[block('heading_1',task['ticker']+'｜AI研究更新'),
        block('quote',r['conclusion']),block('paragraph','核心问题：'+r['research_question']),
        block('paragraph','研究覆盖：'+r['research_status']+'；AI研究候选／未人工采纳。正式模型和人工持有逻辑不自动改写。'),
        block('heading_2','持有逻辑与变化')]
    blocks.append(table(['逻辑','变化与依据','证伪条件'],[[x['claim'],x['change']+'：'+x['reason'],x['falsifier']] for x in r['holding_logic']]))
    blocks.extend([block('heading_2','核心分歧与自己的判断'),block('paragraph',r['key_disagreement']),
        table(['指标／期间','差异解释／缺口','可比状态'],[[x['metric'],x['reason'],x['status']] for x in r['earnings_differences']])])
    model=result.get('model_snapshot')
    if model and model.get('scenarios'):
        blocks.append(block('heading_2','自有预测与情景估值'))
        for case,rows in model['scenarios'].items():
            blocks.append(block('heading_3',case+'｜'+model['currency']+' 原币；研究假设 F'))
            metrics=[('收入',lambda y:y['income']['revenue']),('归母净利',lambda y:y['income']['parent_net_income']),
                ('EPS',lambda y:y['income']['eps']),('经营现金流',lambda y:y['cashflow']['operating']),
                ('期末现金',lambda y:y['balance']['cash']),('债务',lambda y:y['balance']['debt']),
                ('权益',lambda y:y['balance']['equity']),('资金缺口',lambda y:y['funding_shortfall']),
                ('每股估值',lambda y:y['valuation']['price'])]
            blocks.append(table(['指标',*[str(y['year'])+'F' for y in rows]],[[label,*[value(fn(y)) for y in rows]] for label,fn in metrics]))
    else:blocks.append(block('quote','模型或关键输入待补；保留已有研究、具体缺口与下一验证点，不将空缺按零。'))
    blocks.append(block('paragraph',r['valuation_reason']))
    for field,title in [('changes','变化及影响'),('counterevidence','反证与替代解释')]:
        blocks.append(block('heading_2',title))
        for item in r[field]:blocks.append(block('bulleted_list_item',item['text']+'｜证据：'+', '.join(item['evidence'])))
    blocks.extend([block('heading_2','行动条件与下一步'),
        table(['条件','研究行动','复核日期'],[[x['condition'],x['action'],x['due']] for x in r['next_actions']]),
        block('heading_2','未解问题')])
    blocks.extend(block('bulleted_list_item',q) for q in r['open_questions'])
    blocks.append(block('heading_2','原文与底稿'))
    for doc in result.get('source_index',[]):
        blocks.append({'object':'block','type':'paragraph','paragraph':{'rich_text':rich(
            str(doc.get('revision_id'))+'｜'+str(doc.get('locator') or doc.get('document_id')),
            url=doc.get('url'))}})
    for key,ref in files.items():
        if key in {'source','result','model','model_workbook'}:
            blocks.append({'object':'block','type':'paragraph','paragraph':{'rich_text':rich(key+'｜'+ref['path'],url=ref.get('url'))}})
    blocks.append(block('paragraph','实际模型：'+result['actual_model']+'；推理服务：'+result['provider']+'。自动校验不代表人工确认。'))
    if len(blocks)>95:raise ValueError('report exceeds bounded native publication')
    return blocks
