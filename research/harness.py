"""Research direction and evidence planning; no hidden reasoning or tool autonomy."""
from copy import deepcopy
import re
from financials.forecast import digest

REQUEST_KINDS={'primary_disclosure','financial_workbook','forecast_assumptions','prior_research',
               'industry','policy','trading_flows'}


def build_brief(question,cards,context,model):
    if not isinstance(question,str) or not question.strip() or len(question)>800:
        raise ValueError('explicit bounded core research question required')
    missing=[]
    if not any(not c.get('missing') for c in cards):missing.append('primary_disclosure')
    snapshots=[c['financial_snapshot'] for c in context if c.get('financial_snapshot')]
    if not snapshots:missing.append('financial_workbook')
    if not model or model.get('status')!='ready_for_review':missing.append('approved_forecast')
    brief=dict(version='holdings-question-v2',question=question.strip(),
        required_analysis=['经营变化与增长来源','经常性利润与一次性项目','现金转换及营运资金','资产负债与融资依赖',
                           '预测假设/估值的影响及缺口','反证、替代解释、何种事实会改变判断','具体跟进条件与时间'],
        source_priority=['current_primary_original','structured_financial_workbook','approved_forecast',
                         'prior_research_as_hypothesis','mapped_external_sources'],
        source_boundaries=['年初至今累计不可伪装单季；期末时点不与期间流量混用',
                           '旧研究、缓存模型和管理层预期不是已验证的当前事实',
                           '缺失不按零、未知不按利空；公告计划不等于实施',
                           '先核验同一主体、期间、币种、合并范围和现金口径，再做比较'],
        missing=missing,partial_sources=[c['revision_id'] for c in context if c.get('partial')],
        retrieval_policy=dict(max_requests=6,allowed_kinds=sorted(REQUEST_KINDS),
            execution='Only explicitly mapped read-only collectors outside the model; unavailable sources remain missing. No paid fallback or arbitrary file/tool access.'),
        output_policy='回答原问题，依次给证据、计算/推断、反证和改变结论的条件。只输出可审阅的判断依据，不输出隐含思维链。正文用投资研究语言，不暴露内部变量名或原始哈希。')
    brief['contract_id']=digest(brief);return brief


def compact_context(context):
    result=[]
    for entry in context or []:
        item=deepcopy(entry)
        if item.get('financial_snapshot'):
            s=item['financial_snapshot']
            for table in s['tables']:
                for row in table['rows']:row.pop('cells',None)
            item['source_text']='Structured historical worksheet values below; not an approved forecast.'
        else:
            text=item.get('source_text','')
            if len(text)<=12000:item['source_text']=text
            else:
                # Preserve provenance-bearing opening, late counterevidence and
                # ending instead of silently selecting only the first pages.
                ranges=[(0,1600),(len(text)-1600,len(text))]
                terms=r'现金流|回款|债务|净利润|毛利|减值|并购|风险|反证|一次性|cash flow|working capital|debt|impairment'
                for match in list(re.finditer(terms,text,re.I))[:60]:
                    ranges.append((max(0,match.start()-300),min(len(text),match.end()+500)))
                merged=[]
                for a,b in sorted(ranges):
                    if merged and a<=merged[-1][1]:merged[-1]=(merged[-1][0],max(merged[-1][1],b))
                    else:merged.append((a,b))
                parts=[];remaining=11800
                # Ensure the final excerpt remains in the bounded pack.
                if merged and merged[-1][1]==len(text):
                    ending=merged.pop();tail=text[max(ending[0],len(text)-1600):];remaining-=len(tail)
                else:tail=''
                for a,b in merged:
                    if remaining<=0:break
                    chunk=text[a:min(b,a+remaining)];parts.append(chunk);remaining-=len(chunk)+20
                if tail:parts.append(tail)
                item['source_text']='\n[资料片段分界]\n'.join(parts)[:12000]
            if len(text)>12000:item['partial']=True;item['prompt_excerpt_truncated']=True
        result.append(item)
    return result
