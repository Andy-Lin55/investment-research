"""Research direction and evidence planning; no hidden reasoning or tool autonomy."""
from copy import deepcopy
from collections import Counter
import re
from urllib.parse import urlsplit
from financials.forecast import digest

REQUEST_KINDS={'primary_disclosure','financial_workbook','forecast_assumptions','prior_research',
               'industry','policy','trading_flows'}


def _is_scheduling_metadata(card):
    # cli.weekly uses an ordinary 公告 card with nonempty placeholder content.
    # build_card retains its weekly document ID, but not the enclosing task kind.
    return (card.get('kind')=='weekly_deep'
            or bool(re.fullmatch(r'weekly-\d{4}-\d{2}-\d{2}',str(card.get('source',{}).get('document_id',''))))
            or card.get('evidence_kind')=='scheduling_metadata')


def evidence_is_primary(card):
    """Recognize readable collector disclosure provenance, not verified facts.

    Partial extraction remains primary in origin. Authority does not establish
    completeness, freshness, issuer matching or manual review.
    """
    if _is_scheduling_metadata(card):return False
    if card.get('evidence_kind') not in {None,'primary_disclosure'}:return False
    if card.get('financial_snapshot') or 'source_content' in card.get('missing',[]):return False
    # Match the source URL provenance retained by cli.scan -> build_card.
    # Classification or empty `missing` alone cannot establish authority.
    source=card.get('source',{})
    try:parts=urlsplit(source.get('url') or '')
    except ValueError:return False
    official={'static.cninfo.com.cn','www1.hkexnews.hk','www.hkexnews.hk',
              'www.sec.gov','www.hkex.com.hk'}
    kinds={'公告','重大公告','年度报告','季度报告','中期报告','半年度报告','业绩公告',
           '业绩预告','业绩快报','业绩预告修正','第一季度报告','第三季度报告','业绩会',
           '10-K','10-Q','20-F','40-F','8-K','6-K',
           '10-K/A','10-Q/A','20-F/A','40-F/A','8-K/A','6-K/A'}
    return (parts.scheme=='https' and parts.hostname in official
            and not parts.username and not parts.password
            and card.get('document_type') in kinds
            and bool(source.get('document_id')) and bool(source.get('content_sha256')))


def _source_partial(card):
    return bool(card.get('partial') or card.get('prompt_excerpt_truncated')
                or card.get('extraction',{}).get('partial'))


def evidence_role(card):
    if _is_scheduling_metadata(card):return 'scheduling_metadata'
    if card.get('financial_snapshot'):return 'historical_financial_workbook'
    if evidence_is_primary(card):return 'primary_disclosure'
    role=card.get('evidence_kind')
    return role if role and role!='primary_disclosure' else 'unverified_source'


def build_brief(question,cards,context,model):
    if not isinstance(question,str) or not question.strip() or len(question)>800:
        raise ValueError('explicit bounded core research question required')
    missing=[]
    sources=[*cards,*context]
    if not any(evidence_is_primary(c) for c in sources):missing.append('primary_disclosure')
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
        missing=missing,partial_sources=list(dict.fromkeys(c['revision_id'] for c in sources
            if _source_partial(c) and c.get('revision_id'))),
        source_classifications=[dict(revision_id=c.get('revision_id'),kind=evidence_role(c),
            partial=_source_partial(c),missing=list(c.get('missing',[]))) for c in sources],
        retrieval_policy=dict(max_requests=6,allowed_kinds=sorted(REQUEST_KINDS),
            execution='Only explicitly mapped read-only collectors outside the model; unavailable sources remain missing. No paid fallback or arbitrary file/tool access.'),
        output_policy='回答原问题，依次给证据、计算/推断、反证和改变结论的条件。只输出可审阅的判断依据，不输出隐含思维链。正文用投资研究语言，不暴露内部变量名或原始哈希。')
    brief['contract_id']=digest(brief);return brief


def _merge_ranges(ranges):
    merged=[]
    for start,end in sorted(ranges):
        if merged and start<=merged[-1][1]:merged[-1]=(merged[-1][0],max(merged[-1][1],end))
        else:merged.append((start,end))
    return merged


def _excerpt_ranges(text):
    """Reserve spatial coverage, then round-robin topics across match locations."""
    size=len(text)
    ranges=[(0,1000),(size-1000,size)]
    for i in range(1,6):
        center=size*i//6;ranges.append((center-200,center+200))
    topics=[r'现金流|回款|cash\s*flow|working\s*capital',
            r'债务|借款|应收|存货|debt|receivables|inventory',
            r'净利润|毛利|net\s*profit|margin',
            r'减值|一次性|并购|impairment|one.off|acquisition',
            r'收入|销量|订单|revenue|sales|orders',
            r'风险|反证|risk|counterevidence']
    samples=[]
    for pattern in topics:
        matches=list(re.finditer(pattern,text,re.I))
        # Three match quantiles per topic, rather than the first N keywords.
        indices=sorted({0,len(matches)//2,len(matches)-1}) if matches else []
        samples.append([(max(0,matches[i].start()-180),min(size,matches[i].end()+340))
                        for i in indices])
    for rank in range(3):
        for candidates in samples:
            if rank>=len(candidates):continue
            candidate=_merge_ranges([*ranges,candidates[rank]])
            # Leave space for explicit separators; never truncate a chosen span.
            if sum(b-a for a,b in candidate)+len('\n[资料片段分界]\n')*(len(candidate)-1)<=12000:
                ranges=candidate
    return _merge_ranges(ranges)


def compact_context(context):
    result=[]
    for entry in context or []:
        item=deepcopy(entry)
        if item.get('financial_snapshot'):
            s=item['financial_snapshot']
            gaps=s.get('gaps',[])
            s['gap_summary']=dict(Counter(g.rsplit(':',1)[-1] for g in gaps))
            s['gaps']=[g for g in gaps if not g.endswith((':missing_cached_value',':invalid_cached_value'))]
            s['gap_inventory_note']='Per-cell missing/invalid locations remain in the archived full context; null values are retained in each row.'
            for table in s['tables']:
                for row in table['rows']:
                    for key in ('cells','raw_values','number_formats','display_multipliers'):row.pop(key,None)
            item['source_text']='Structured historical worksheet values below; not an approved forecast.'
        else:
            text=item.get('source_text','')
            ranges=_excerpt_ranges(text) if len(text)>12000 else ([(0,len(text))] if text else [])
            parts=[];excerpts=[];omissions=[];prompt_offset=0;source_offset=0
            separator='\n[资料片段分界]\n'
            for start,end in ranges:
                if start>source_offset:omissions.append(dict(source_start=source_offset,source_end=start))
                if parts:prompt_offset+=len(separator)
                excerpts.append(dict(source_start=start,source_end=end,
                    prompt_start=prompt_offset,prompt_end=prompt_offset+end-start))
                parts.append(text[start:end]);prompt_offset+=end-start;source_offset=end
            if source_offset<len(text):omissions.append(dict(source_start=source_offset,source_end=len(text)))
            item.update(source_text=separator.join(parts),prompt_excerpts=excerpts,
                prompt_omissions=omissions,prompt_source_characters=len(text),
                prompt_excerpt_policy='Offsets are zero-based, end-exclusive Unicode character positions in the supplied source_text, not original PDF/file bytes. Spatial and topic sampling is not exhaustive; unselected evidence and upstream extraction omissions may remain.')
            if omissions:item['partial']=True;item['prompt_excerpt_truncated']=True
        result.append(item)
    return result
