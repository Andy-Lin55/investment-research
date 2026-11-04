"""Readable research updates with program-computed model tables in the body."""
from research.report_view import block,table,value,rich


def standard_forecast_tables(rows,model,price):
    scale=1e8 if model['baseline']['revenue']>=1e8 else 1
    unit=('亿元' if scale==1e8 else '原币')+' '+model['currency']
    def cells(items):
        return table(['指标／单位',*[str(y['year'])+'F' for y in rows]],[[label,*[value(fn(y)/s if s is not None else fn(y)) if fn(y) is not None else '—' for y in rows]] for label,fn,s in items])
    result=[]
    groups=[('预测利润表',[
        ('收入',lambda y:y['income']['revenue'],scale),('营业成本（含折旧）',lambda y:y['income']['cogs'],scale),
        ('经营费用（含折旧）',lambda y:y['income']['opex'],scale),('净财务费用代理',lambda y:y['income']['interest'],scale),
        ('其他税前净损益',lambda y:y['income']['other_pretax_income'],scale),('税前利润',lambda y:y['income']['pretax'],scale),
        ('所得税',lambda y:y['income']['tax'],scale),('净利润',lambda y:y['income']['net_income'],scale),
        ('归母净利',lambda y:y['income']['parent_net_income'],scale),('基本EPS / '+model['currency']+'/股',lambda y:y['income']['eps'],1)]),
        ('预测资产负债表',[
        ('现金等价物',lambda y:y['balance']['cash'],scale),('应收账款',lambda y:y['balance']['receivables'],scale),
        ('存货',lambda y:y['balance']['inventory'],scale),('固定资产',lambda y:y['balance']['ppe'],scale),
        ('其他资产',lambda y:y['balance']['other_assets'],scale),('总资产',lambda y:sum(y['balance'][k] for k in ('cash','receivables','inventory','ppe','other_assets')),scale),
        ('应付账款',lambda y:y['balance']['payables'],scale),('所定义债务',lambda y:y['balance']['debt'],scale),
        ('其他负债',lambda y:y['balance']['other_liabilities'],scale),('含少数权益',lambda y:y['balance']['equity'],scale),
        ('资产负债差额 / 原币',lambda y:y['checks']['balance_difference'],1)]),
        ('预测现金流量表',[
        ('经营现金流',lambda y:y['cashflow']['operating'],scale),('投资现金流',lambda y:y['cashflow']['investing'],scale),
        ('融资现金流',lambda y:y['cashflow']['financing'],scale),('汇率影响',lambda y:y['cashflow']['fx'],scale),
        ('现金变动',lambda y:y['cashflow']['change'],scale),('期末现金等价物',lambda y:y['balance']['cash'],scale),
        ('资本开支',lambda y:y['cashflow']['capex'],scale),('股息假设',lambda y:y['cashflow']['dividends'],scale),
        ('资金缺口',lambda y:y['funding_shortfall'],scale)])]
    for title,items in groups:result.extend([block('paragraph',title+'｜'+unit),cells(items)])
    def approved_price(row):
        return row['valuation']['price'] if row['status']=='ready_for_review' else None
    labels={'ready_for_review':'待人工复核','funding_required':'有资金缺口','unbalanced':'三表未勾稽',
            'invalid_asset_balance':'资产余额异常','prior_year_unresolved':'前期缺口待解决','loss_valuation_pending':'亏损，PE估值不适用'}
    items=[('校验状态',lambda y:labels.get(y['status'],y['status']),None),('PE / 倍',lambda y:y['valuation']['multiple'],1),('每股情景估值 / '+model['currency'],approved_price,1)]
    if price and price.get('currency')==model['currency'] and price.get('price',0)>0:
        items.append(('相对所列收盘价空间 / %（非年化，不含股息）',lambda y:(approved_price(y)/price['price']-1)*100 if approved_price(y) is not None else None,1))
    result.append(cells(items));return result


def render(task,result,files):
    r=result['report'];blocks=[block('heading_1',task['ticker']+'｜AI研究更新'),
        block('quote',r['conclusion']),block('paragraph','核心问题：'+r['research_question']),
        block('paragraph','持有判断：'+r['thesis']),
        block('paragraph','研究覆盖：'+r['research_status']+'；AI研究候选／未人工采纳。正式模型和人工持有逻辑不自动改写。'),
        block('heading_2','持有逻辑与变化')]
    blocks.append(table(['逻辑','变化与依据','证伪条件'],[[x['claim'],x['change']+'：'+x['reason'],x['falsifier']] for x in r['holding_logic']]))
    blocks.extend([block('heading_2','核心分歧与自己的判断'),block('paragraph',r['key_disagreement']),
        table(['指标／期间','差异解释／缺口','可比状态'],[[x['metric'],x['reason'],x['status']] for x in r['earnings_differences']])])
    price=result.get('market_snapshot')
    if price:
        blocks.append(block('paragraph','价格基准：'+price['ticker']+' '+value(price['price'])+' '+price['currency']+
            '｜交易日 '+price['trade_date']+'；Tushare 未复权收盘价，非盘中实时报价。'))
    history=result.get('historical_tables',[])
    if history:
        blocks.append(block('heading_2','历史三张表｜实际值 A'))
        for item in history:
            blocks.append(block('heading_3',item['title']+'｜'+item['currency']+' 原币／'+item['period_basis']))
            if item['origin']=='broker_reported_history':blocks.append(block('paragraph','卖方原文转载历史数，尚未经公司公告交叉核验；非卖方预测。'))
            periods=item['periods']
            for offset in range(0,len(periods),6):
                selected=periods[offset:offset+6]
                blocks.append(table(['指标／单位',*selected],[[row['label']+' / '+row['unit'],
                    *[value(v) for v in row['values'][offset:offset+6]]] for row in item['rows']]))
        blocks.append(block('paragraph','利润表和现金流量表非年度期间为年初至今累计；资产负债表为期末数。货币资金与现金等价物口径不同，缺项留空。'))
    if result.get('financial_gaps'):blocks.append(block('paragraph','财务数据待核验：'+'；'.join(result['financial_gaps'])))
    guidance = result.get('management_guidance_comparison', {})
    if guidance.get('rows'):
        blocks.append(block('heading_3', '自有预测与公司指引对照'))
        headings = ['指标／期间', '情景', '公司指引区间', '自有预测', '距区间差额', '原文']
        rows = []
        for row in guidance['rows']:
            is_eps = row['metric'] == 'eps'
            scale = 1 if is_eps else 1e8
            metric = '基本EPS（元/股）' if is_eps else '归母净利（亿元）'
            position = {'above': '高于上限', 'below': '低于下限', 'within': '区间内'}[row['position']]
            rows.append([metric+'／'+str(row['year']), row['case'],
                         value(row['low']/scale)+' 至 '+value(row['high']/scale), value(row['own']/scale),
                         position+' '+value(row['difference']/scale),
                         row['published_at'][:10]+' p'+str(row['source_page'])])
        blocks.append(table(headings, rows))
    if guidance.get('gaps'):
        blocks.append(block('paragraph', '公司指引待核验：'+ '；'.join(
            str(gap['year'])+' 年最新披露的区间或版本无法唯一确认' for gap in guidance['gaps'])))
    comparison=result.get('forecast_comparison',{})
    if comparison.get('comparable'):
        blocks.append(block('heading_3','自有预测与机构预期对照'))
        rows=comparison['comparable']
        for offset in range(0,len(rows),30):
            blocks.append(table(['指标／期间／币种','预测所有者','机构值','自有值','差异','未解释差异'],
                [[x['metric']+'/'+x['period_end']+'/'+x['currency'],x['estimate_owner'],value(x['value']),
                  value(x['own_value']),value(x['absolute_difference']),value(x['unexplained_difference'])] for x in rows[offset:offset+30]]))
    if comparison.get('incomparable'):
        blocks.append(block('paragraph','不可比／未准入预测：'+'；'.join(x.get('reason','dimensions not aligned') for x in comparison['incomparable'])))
    if not comparison.get('comparable'):
        blocks.append(block('paragraph','尚无通过主体、期间、币种及会计口径核对的机构数值；不据此声称全市场预期。'))
    model=result.get('model_snapshot')
    if model and model.get('scenarios'):
        blocks.append(block('heading_2','自有预测与情景估值'))
        if r.get('valuation_status')=='pending':
            blocks.append(block('quote','以下为研究假设下的情景敏感性。盈利与估值依据仍待核验，不作为正式目标价。'))
        mapping=result.get('baseline_mapping')
        if mapping:
            bridge=mapping['cash_bridge']
            blocks.append(block('paragraph','预测基期：'+model['baseline']['period_end']+'；现金口径为现金等价物 '+value(bridge.get('cash_equivalents'))+
                '，货币资金 '+value(bridge.get('monetary_funds'))+'；差额 '+value(bridge.get('monetary_funds_less_cash_equivalents'))+' 保留在其他资产。'))
            for note in mapping.get('limitations',[]):blocks.append(block('paragraph',note))
        for case in ('base','bull','bear'):
            rows=model['scenarios'][case]
            blocks.append(block('heading_3',case+'｜'+model['currency']+' 原币；研究假设 F'))
            if model['baseline'].get('standard_model_version')==1:
                blocks.extend(standard_forecast_tables(rows,model,price));continue
            metrics=[('收入',lambda y:y['income']['revenue']),('归母净利',lambda y:y['income']['parent_net_income']),
                ('EPS',lambda y:y['income']['eps']),('经营现金流',lambda y:y['cashflow']['operating']),
                ('期末现金',lambda y:y['balance']['cash']),('债务',lambda y:y['balance']['debt']),
                ('权益',lambda y:y['balance']['equity']),('资金缺口',lambda y:y['funding_shortfall']),
                ('每股估值',lambda y:y['valuation']['price'])]
            blocks.append(table(['指标',*[str(y['year'])+'F' for y in rows]],[[label,*[value(fn(y)) for y in rows]] for label,fn in metrics]))
    else:blocks.append(block('quote','模型或关键输入待补；保留已有研究、具体缺口与下一验证点，不将空缺按零。'))
    blocks.append(block('paragraph',r['valuation_reason']))
    if not result.get('market_snapshot'):
        blocks.append(block('paragraph','未取得可核验的同币种、同股类当前价格快照；情景估值不等于当前上涨／下跌空间。'))
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
        if key in {'source','result','model','model_workbook','financial_workbook','financial_data'}:
            blocks.append({'object':'block','type':'paragraph','paragraph':{'rich_text':rich(key+'｜'+ref['path'],url=ref.get('url'))}})
    blocks.append(block('paragraph','实际模型：'+result['actual_model']+'；推理服务：'+result['provider']+'。自动校验不代表人工确认。'))
    if len(blocks)>95:raise ValueError('report exceeds bounded native publication')
    return blocks
