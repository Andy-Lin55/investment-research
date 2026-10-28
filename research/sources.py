"""First-party disclosure collection, bounded parsing and explicit coverage gaps."""
from datetime import date,datetime,timedelta,timezone
import io,json,os,re,zipfile
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from research.delivery import ServiceError

class CoverageError(RuntimeError):pass


def public_json(method,url,**kwargs):
    import requests
    headers={'User-Agent':os.getenv('SEC_USER_AGENT','InvestmentResearch contact-required@example.invalid'),
             'Accept':'application/json','Referer':'https://www.cninfo.com.cn/'}
    try:
        r=requests.request(method,url,headers=headers,timeout=(15,45),**kwargs)
        if not 200<=r.status_code<300:raise ServiceError(r.status_code)
        return r.json()
    except (requests.RequestException,ValueError):raise CoverageError('Source request failed; not evidence of no disclosures') from None


def cninfo(ticker,start,end,request=public_json):
    code=ticker.split('.')[0];rows=[];seen=set()
    for page in range(1,101):
        data=request('POST','https://www.cninfo.com.cn/new/hisAnnouncement/query',data={
            'pageNum':page,'pageSize':100,'tabName':'fulltext','searchkey':code,'seDate':start+'~'+end})
        if not isinstance(data.get('announcements'),list) or not isinstance(data.get('hasMore'),bool):
            raise CoverageError('CNINFO response does not prove page coverage')
        for item in data['announcements']:
            if str(item.get('secCode'))!=code:continue
            identity=item['announcementId']
            if identity in seen:continue
            seen.add(identity)
            rows.append(dict(title=re.sub('<[^>]+>','',item['announcementTitle']),
                             url='https://static.cninfo.com.cn/'+item['adjunctUrl'],document_id=str(identity),
                             published_at=datetime.fromtimestamp(item['announcementTime']/1000,timezone.utc).isoformat()))
        if not data['hasMore']:return rows
    raise CoverageError('CNINFO pagination exceeded bound; disclosures remain pending')


def hkex(ticker,start,end,request=public_json):
    code=ticker.split('.')[0].zfill(5)
    entries=request('GET','https://www1.hkexnews.hk/ncms/script/eds/activestock_sehk_e.json')
    indexes=[e['i'] for e in entries if str(e['c']).zfill(5)==code]
    if len(indexes)!=1:raise CoverageError('HK issuer code not resolved uniquely')
    def window(a,b):
        data=request('GET','https://www1.hkexnews.hk/search/titleSearchServlet.do',params={
            'lang':'en','market':'SEHK','stockId':indexes[0],'category':0,'documentType':-1,
            'fromDate':a.strftime('%Y%m%d'),'toDate':b.strftime('%Y%m%d'),'title':'','searchType':0,
            't1code':-2,'t2Gcode':-2,'t2code':-2,'rowRange':1000,'sortByOptions':'DateTime','sortDir':0})
        if not isinstance(data.get('hasNextRow'),bool) or 'recordCnt' not in data:
            raise CoverageError('HKEX pagination metadata absent')
        rows=json.loads(data['result'])
        if data['hasNextRow'] or int(data['recordCnt'])>len(rows):
            if a==b:raise CoverageError('HKEX one-day result capped; do not mark source scanned')
            mid=a+(b-a)//2
            return window(a,mid)+window(mid+timedelta(days=1),b)
        return [dict(title=r['TITLE'],url='https://www1.hkexnews.hk'+r['FILE_LINK'],document_id=r['FILE_LINK'],
                     published_at=datetime.strptime(r['DATE_TIME'],'%d/%m/%Y %H:%M').replace(tzinfo=timezone(timedelta(hours=8))).isoformat()) for r in rows]
    rows=window(date.fromisoformat(start),date.fromisoformat(end))
    return list({r['document_id']:r for r in rows}.values())


def sec(ticker,start,end,request=public_json):
    if 'example.invalid' in os.getenv('SEC_USER_AGENT','example.invalid'):
        raise CoverageError('Configure a real SEC contact User-Agent before collection')
    mapping=request('GET','https://www.sec.gov/files/company_tickers.json')
    matches=[str(v['cik_str']).zfill(10) for v in mapping.values() if v['ticker'].upper()==ticker.upper()]
    if len(matches)!=1:raise CoverageError('SEC issuer not resolved uniquely')
    cik=matches[0];data=request('GET',f'https://data.sec.gov/submissions/CIK{cik}.json')
    filings=data['filings'];batches=[filings['recent']]
    for old in filings.get('files',[]):
        if old['filingTo']>=start and old['filingFrom']<=end:
            batches.append(request('GET','https://data.sec.gov/submissions/'+old['name']))
    out=[]
    accepted={'10-K','10-Q','20-F','40-F','8-K','6-K','10-K/A','10-Q/A','20-F/A','40-F/A','8-K/A','6-K/A'}
    for batch in batches:
        for i,day in enumerate(batch['filingDate']):
            if not start<=day<=end or batch['form'][i] not in accepted:continue
            accession=batch['accessionNumber'][i];document=batch['primaryDocument'][i]
            out.append(dict(title=ticker+' '+batch['form'][i]+' '+day,document_type=batch['form'][i],
                url=f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace("-","")}/{document}',
                document_id=accession,published_at=day+'T00:00:00+00:00',publication_precision='day'))
    return list({r['document_id']:r for r in out}.values())


def document_type(title,form=None):
    if form in {'10-K','10-Q','20-F','10-K/A','10-Q/A','20-F/A'}:return form
    if re.search(r'年[度報报]|ANNUAL REPORT',title,re.I):return '年度报告'
    if re.search(r'季度|QUARTER',title,re.I):return '季度报告'
    if re.search(r'半年|中期報告|中期报告|INTERIM REPORT',title,re.I):return '中期报告'
    if re.search(r'业绩|業績|RESULTS|EARNINGS',title,re.I):return '业绩公告'
    if form in {'8-K','6-K','40-F','40-F/A','8-K/A','6-K/A'} or re.search(
            r'收购|收購|并购|合并|融资|融資|配售|诉讼|訴訟|监管|監管|股东大会|股東大會|回购|回購|减持|增持|重大|'
            r'ACQUISITION|MERGER|FINANCING|LITIGATION|REGULATOR|PLACEMENT|BUYBACK|DISPOSAL|PROFIT WARNING',title,re.I):return '重大公告'
    return '公告'


def download_primary(url):
    import requests
    def check(target):
        p=urlsplit(target)
        if p.scheme!='https' or p.username or p.password or p.hostname not in {
            'static.cninfo.com.cn','www1.hkexnews.hk','www.hkexnews.hk','www.sec.gov','www.hkex.com.hk'}:
            raise ValueError('first-party source URL required')
    for _ in range(5):
        check(url)
        try:r=requests.get(url,headers={'User-Agent':os.getenv('SEC_USER_AGENT','InvestmentResearch contact-required@example.invalid')},
                           timeout=(15,60),allow_redirects=False,stream=True)
        except requests.RequestException:raise CoverageError('Original download failed') from None
        if r.status_code in {301,302,303,307,308}:
            from urllib.parse import urljoin
            url=urljoin(url,r.headers['Location']);continue
        if r.status_code!=200:raise ServiceError(r.status_code)
        chunks=[];size=0
        for chunk in r.iter_content(1024*1024):
            size+=len(chunk)
            if size>40*1024*1024:raise CoverageError('Source exceeds bounded download')
            chunks.append(chunk)
        return b''.join(chunks)
    raise CoverageError('Too many original-source redirects')


def extract(data,*,kind,max_chars=100000):
    pages=[];total_pages=1;substantive=0
    if kind=='pdf':
        import fitz
        with fitz.open(stream=data,filetype='pdf') as doc:
            total_pages=len(doc)
            for i,page in enumerate(doc):
                text=page.get_text().strip();substantive+=len(text)
                if text:pages.append(f'[PDF page {i+1}]\n'+text)
    elif kind=='docx':
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            pages=['[DOCX body]\n'+''.join(ET.fromstring(z.read('word/document.xml')).itertext())]
    elif kind=='xlsx':
        import openpyxl
        wb=openpyxl.load_workbook(io.BytesIO(data),read_only=True,data_only=True)
        for sheet in wb:
            pages.append('[XLSX sheet '+sheet.title+']\n'+'\n'.join(str(row) for row in sheet.iter_rows(values_only=True)))
        wb.close()
    else:
        from bs4 import BeautifulSoup
        text=data.decode('utf-8',errors='replace')
        if kind=='html':
            soup=BeautifulSoup(text,'html.parser')
            for node in soup(['script','style','nav','header','footer']):node.decompose()
            text=soup.get_text('\n',strip=True)
        pages=[text]
    full='\n\n'.join(pages).strip()
    if kind!='pdf':substantive=len(full)
    return dict(text=full[:max_chars].strip(),partial=len(full)>max_chars,total_pages=total_pages if kind=='pdf' else len(pages),
                substantive_characters=substantive,
                extracted_characters=len(full),included_characters=min(len(full),max_chars),
                note='Full original retained separately. Extracted/cached text is not manual verification.')
