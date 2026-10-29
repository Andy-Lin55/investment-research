"""Read back native report trees; do not accept a correct title with missing data."""
from research.delivery import DeliveryError
from urllib.parse import urlsplit,urlunsplit


def canonical_url(url):
    if not url:return url
    p=urlsplit(url)
    # Notion inserts '/' for an empty HTTP root path. Query/fragment remain
    # exact so a different target cannot pass as mere URL normalization.
    if p.scheme in {'http','https'} and p.netloc and not p.path:
        return urlunsplit((p.scheme,p.netloc,'/',p.query,p.fragment))
    return url


def canonical_rich(items):
    output=[]
    for r in items:
        a=r.get('annotations',{});text=r.get('text',{})
        output.append((text.get('content',r.get('plain_text','')),canonical_url((text.get('link') or {}).get('url') or r.get('href')),
                       bool(a.get('bold',False))))
    return output


def canonical(blocks):
    output=[]
    for b in blocks:
        kind=b['type'];v=b.get(kind,{})
        if kind=='table':
            value=(v['table_width'],bool(v.get('has_column_header')),bool(v.get('has_row_header')),
                   canonical(v.get('children',[])))
        elif kind=='table_row':value=[canonical_rich(c) for c in v['cells']]
        elif kind in {'heading_1','heading_2','heading_3','paragraph','quote','bulleted_list_item','numbered_list_item'}:
            value=canonical_rich(v.get('rich_text',[]))
        else:raise DeliveryError('unexpected report block type')
        output.append((kind,value))
    return output


def read_tree(http,page_id,*,max_blocks=900):
    count=0
    def read(parent,depth=0):
        nonlocal count
        if depth>2:raise DeliveryError('unexpected report nesting')
        cursor=None;seen=set();blocks=[]
        while True:
            path='/blocks/'+parent+'/children?page_size=100'+('&start_cursor='+cursor if cursor else '')
            result=http('GET',path)
            for b in result['results']:
                count+=1
                if count>max_blocks:raise DeliveryError('report readback bound exceeded')
                if b['type']=='table':
                    b['table']['children']=read(b['id'],depth+1)
                elif b.get('has_children') or b.get(b['type'],{}).get('children'):
                    raise DeliveryError('unexpected descendants under report text/row')
                blocks.append(b)
            if not result.get('has_more'):break
            cursor=result.get('next_cursor')
            if not cursor or cursor in seen:raise DeliveryError('invalid report pagination')
            seen.add(cursor)
        return blocks
    return read(page_id)
