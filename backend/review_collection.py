"""Collect observed Wing review-list DOM through Ziniao CLI, never private endpoints."""
import hashlib,json,subprocess,time,re,uuid
from datetime import datetime
from operation_progress import progress
from pathlib import Path
import openpyxl

URL='https://wing.coupang.com/tenants/cs/product/review'

def cli(profile,command,*args):
    p=subprocess.run(['ziniao-cli','page',command,'--store-id',profile['browser_id'],*args],capture_output=True,text=True,timeout=50)
    if p.returncode:raise RuntimeError('评价页面命令失败：'+p.stderr[:160])
    try:d=json.loads(p.stdout)
    except json.JSONDecodeError:
        if command in ('visit','click') and '✓' in (p.stdout+p.stderr):return {}
        raise RuntimeError('评价页面返回格式改变')
    if not d.get('ok'):raise RuntimeError('评价页面命令失败：'+str(d.get('error')))
    return d['data']['data']

def collect(root,profile,run_date):
    folder=Path(root)/'state/full_exports'/(run_date+'_daily')/'商品评价'/ (datetime.now().strftime('%H%M%S_')+uuid.uuid4().hex[:8])
    folder.mkdir(parents=True,exist_ok=True)
    cli(profile,'visit','--url',URL)
    info=cli(profile,'content','--content-format','structured')
    if info.get('url','').rstrip('#')!=URL or profile['expected_title'] not in info.get('title',''):raise RuntimeError('评价页面身份或地址不匹配')
    target=info['targetId']
    def ev(js):
        result=cli(profile,'exec','--target-id',target,'--script','JSON.stringify('+js+')')
        if result.get('exceptionDetails'):raise RuntimeError('评价DOM脚本执行失败')
        return json.loads(result['result'])
    script="""(()=>({url:location.href,title:document.title,scope:document.body.innerText.split('注册日期')[0],filters:[...document.querySelectorAll('input,select')].filter(e=>e.getClientRects().length).map(e=>({type:e.type,value:e.value})),headers:[...document.querySelectorAll('table thead th')].map(e=>e.innerText.trim()),rows:[...document.querySelectorAll('tr.tbody-tr')].map(e=>({cells:[...e.querySelectorAll('td')].map(c=>c.innerText.trim()),rating:e.querySelector('.scoreNumber')?.innerText,link:e.querySelector('.productId-link')?.href})),paging:[...document.querySelectorAll('.pagination [data-wuic-attrs]')].map(e=>({attrs:e.getAttribute('data-wuic-attrs'),part:e.getAttribute('data-wuic-partial')}))}))()"""
    first=ev(script)
    active=next((x['attrs'] for x in first['paging'] if 'active' in x['attrs']),None)
    if active!='page:1 active':
        one=next((x for x in first['paging'] if x['attrs'].split(' ')[0]=='page:1'),None)
        if not one:raise RuntimeError('请将评价列表返回第一页后重试；停止猜测分页')
        cli(profile,'click','--target-id',target,'--selector',f'.pagination [data-wuic-attrs="{one["attrs"]}"] a')
    count=re.search(r'总计\s*([\d,]+)',first['scope'])
    if not count:raise RuntimeError('评价总数未取得，不能核验完整性')
    expected=int(count.group(1).replace(',',''))
    if expected==0:raise RuntimeError('当前筛选未显示评价，需核验空列表提示；不把加载中状态当作零条')
    pages=[];seen=set()
    for number in range(1,501):
        progress(f"正在读取评价第 {number} 页")
        deadline=time.monotonic()+20
        while True:
            d=ev(script)
            active=next((x['attrs'] for x in d['paging'] if 'active' in x['attrs']),None)
            if active and f'page:{number} active'==active and d['rows'] and hashlib.sha256(json.dumps(d['rows'],ensure_ascii=False).encode()).hexdigest() not in seen:break
            if time.monotonic()>deadline:raise RuntimeError('评价列表/分页未就绪，保留已采页；请检查登录、权限或页面变化')
            time.sleep(.4)
        if d['url'].rstrip('#')!=URL or profile['expected_title'] not in d['title'] or len(d['headers'])!=7 or '评分' not in d['headers']:raise RuntimeError('评价身份或表头发生变化：'+str({k:d[k] for k in ('url','title','headers')}))
        signature=hashlib.sha256(json.dumps(d['rows'],ensure_ascii=False).encode()).hexdigest()
        if signature in seen:raise RuntimeError('评价分页重复，停止避免虚假完整')
        seen.add(signature);pages.append(d)
        (folder/f'page_{number:04d}.json').write_text(json.dumps(d,ensure_ascii=False,indent=2))
        next_page=next((x for x in d['paging'] if x['attrs'].split(' ')[0]==f'page:{number+1}'),None)
        if next_page:selector=f'.pagination [data-wuic-attrs="{next_page["attrs"]}"] a'
        else:
            next_group=next((x for x in d['paging'] if x['part']=='next'),None)
            if not next_group or 'disabled' in next_group['attrs']:break
            selector='.pagination [data-wuic-partial="next"] a'
        cli(profile,'click','--target-id',target,'--selector',selector)
    else:raise RuntimeError('评价列表超过500页安全上限，未声称全部完成')
    rows=[]
    for page_no,page in enumerate(pages,1):
        for r in page['rows']:
            c=r['cells']
            if len(c)!=7:raise RuntimeError('评价列数改变')
            from urllib.parse import urlsplit,parse_qs
            link=r.get('link') or '';u=urlsplit(link)
            option=parse_qs(u.query).get('vendorItemId',[None])[0]
            if not option or r['rating'] not in ('1','2','3','4','5'):raise RuntimeError('评价选项ID或评分未通过校验')
            rows.append({'注册日期':c[1],'商品ID':u.path.split('/')[-1],'选项ID':option,'商品名称':c[3],'评分':int(r['rating']),'评价内容':c[5],'用户名':c[6],'商品链接':link,'页码':page_no})
    if len(rows)!=expected:raise RuntimeError(f'评价总数不符：页面{expected}，采集{len(rows)}；未标记完成')
    result={'store_id':profile['store_id'],'source_url':URL,'captured_at':datetime.now().astimezone().isoformat(),'scope':pages[0]['scope'],'filters':pages[0]['filters'],'page_count':len(pages),'row_count':len(rows),'rows':rows,'status':'complete','definition':'页面当前默认筛选范围内的全部列表页；不代表历史全部评价；不包含未展开详情及图片文件'}
    raw=folder/'reviews.json';raw.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    wb=openpyxl.Workbook();ws=wb.active;ws.title='商品评价'
    headers=list(rows[0]) if rows else []
    ws.append(headers)
    for r in rows:ws.append([r[k] for k in headers])
    for row in ws.iter_rows():
        for cell in row:
            if isinstance(cell.value,str):cell.data_type='s'
    ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
    for col in ws.columns:ws.column_dimensions[col[0].column_letter].width=55 if col[0].value=='评价内容' else 24
    output=folder/'商品评价列表.xlsx';wb.save(output)
    return {'status':'complete','file':str(raw),'sha256':hashlib.sha256(raw.read_bytes()).hexdigest(),'xlsx':str(output),'pages':len(pages),'rows':len(rows),'captured_at':result['captured_at']}
