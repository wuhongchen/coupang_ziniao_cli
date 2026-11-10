"""Read observed Wing product-row images; map options only through local verified batches."""
import runtime_clock as clock
import re,time,urllib.parse,urllib.request
from pathlib import Path
from datetime import datetime
from storage import write,sha
from image_cache import image_type,NoRedirect,LIMIT
URL='https://wing.coupang.com/vendor-inventory/list'
SCRIPT=r'''(()=>{const parents=[...document.querySelectorAll('.cell-product .ip-title[href]')].filter(a=>new URL(a.href).searchParams.get('vendorInventoryId'));const rows=parents.map(a=>{const parent=a.closest('tr'),i=parent.querySelector('.cell-product img');let e=parent.nextElementSibling,options=[];while(e&&!e.querySelector('.cell-product .ip-title[href]')){const m=e.innerText.match(/(?:选项ID|옵션ID)\s*(\d+)/);if(m){const im=e.querySelector('.cell-product img');options.push({option_id:m[1],name:e.querySelector('.ip-title')?.innerText.trim(),image_url:im?(im.currentSrc||im.src):null,text:e.innerText})}e=e.nextElementSibling}return {product_id:new URL(a.href).searchParams.get('vendorInventoryId'),name:a.innerText.trim(),image_url:i?(i.currentSrc||i.src):null,options}});const m=document.body.innerText.match(/共\s*([\d,]+)个/);return {title:document.title,url:location.href,total:m?Number(m[1].replaceAll(',','')):null,loading:!!document.querySelector('.item-list-expanding'),rows}})()'''


def verify_coverage(rows,total):
 ids=[r.get('product_id') for r in rows]
 if total is None or len(ids)!=total or len(set(ids))!=len(ids) or any(not str(i).isdigit() for i in ids):raise ValueError('商品列表覆盖不完整或商品ID重复')
 return {'expected_products':total,'observed_products':len(ids),'complete':True,'definition':'页面全部筛选下的未删除商品，含停售；不包含详情页附图'}

def image_url(url):
 if not isinstance(url,str):raise ValueError('图片地址缺失')
 if url.startswith('//'):url='https:'+url
 p=urllib.parse.urlsplit(url)
 if p.scheme!='https' or not re.fullmatch(r'image\d*\.coupangcdn\.com',p.netloc) or not p.path.startswith('/image/vendor_inventory/') or '..' in urllib.parse.unquote(p.path).split('/') or p.query or p.fragment or '\\' in url:raise ValueError('非受支持的已观察商品图片地址')
 return url

def map_options(rows,mapping):
 return [{**r,'option_ids':sorted(oid for oid,pids in mapping.items() if pids=={r['product_id']}),'image_scope':'product_shared','option_specific_verified':False} for r in rows]

def collect(root,profile,target_id,product_id=None,option_id=None,all_products=False):
 import ziniao_exports as browser
 from report_pipeline import state
 for value in (product_id,option_id):
  if value and not re.fullmatch(r'\d+',value):raise ValueError('商品ID或选项ID必须为数字')
 if all_products and (product_id or option_id):raise ValueError('--all不能同时指定商品或选项')
 if not target_id:raise ValueError('头图采集必须明确 --target-id，避免操作错误标签页')
 mapping={};sources=[]
 for kind in ('inventory','sales_window'):
  batch=max((b for b in state(root,profile['store_id']) if b['kind']==kind),key=lambda b:(b['end'],b['captured']),default=None)
  if batch:
   source=root/batch['file']
   if not source.is_file() or sha(source)!=batch['digest']:raise ValueError('SKU映射来源原文件校验失败')
   sources.append({'batch':batch['id'],'file':str(source),'sha256':batch['digest'],'period':[batch['start'],batch['end']]})
   for r in batch['payload']:
    if r.get('id') and r.get('pid'):mapping.setdefault(str(r['id']),set()).add(str(r['pid']))
 if option_id:
  pids=mapping.get(option_id,set())
  if len(pids)!=1:raise ValueError('选项缺少唯一、已核验的商品映射')
  pid=next(iter(pids))
  if product_id and pid!=product_id:raise ValueError('商品与选项ID不匹配')
  product_id=pid
 browser.configure_runtime(root,{**profile,'target_id':target_id})
 current=browser.evaluate('({title:document.title,url:location.href})')
 if urllib.parse.urlsplit(current['url']).netloc!='wing.coupang.com' or profile['expected_title'] not in current['title']:raise ValueError('请先将指定标签页正常登录到本店Wing，再运行头图采集')
 if urllib.parse.urlsplit(current['url']).path!='/vendor-inventory/list':browser.call('navigate',{'url':URL})
 deadline=time.monotonic()+25
 while True:
  page=browser.evaluate(SCRIPT)
  if profile['expected_title'] not in page['title']:raise ValueError('页面身份变化或登录失效')
  if page['rows']:break
  if time.monotonic()>=deadline:raise RuntimeError('商品列表未就绪或没有可识别的商品行')
  time.sleep(.5)
 stamp=datetime.now(clock.timezone());dest=root/'state/product_images'/stamp.strftime('%Y-%m-%d/%H%M%S_%f');dest.mkdir(parents=True,exist_ok=False)
 write(dest/'page.json',page)
 if all_products:
  query=urllib.parse.parse_qs(urllib.parse.urlsplit(page['url']).query,keep_blank_values=True)
  for key in ('salesMethod','productStatus','stockSearchType','shippingFeeSearchType','saleEndDateSearchType','bundledShippingSearchType','upBundling','shippingMethod','exposureStatus'):
   if query.get(key,['ALL'])!=['ALL']:raise ValueError('全量头图需要全部商品筛选，当前受限：'+key)
  if query.get('searchKeywords',[''])!=[''] or query.get('displayCategoryCodes',[''])!=['']:raise ValueError('全量头图不接受商品搜索或类目筛选')
  total=page['total']
  if total is None or total>5000:raise ValueError('商品总数不可确认或超过安全分页上限')
  parsed=urllib.parse.urlsplit(page['url']);query.update(page=['1'],countPerPage=['50'])
  page_count=max(1,(total+49)//50);all_rows=[]
  for number in range(1,page_count+1):
   query['page']=[str(number)];url=urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query,doseq=True)))
   browser.call('navigate',{'url':url});deadline=time.monotonic()+25
   while True:
    page=browser.evaluate(SCRIPT)
    if profile['expected_title'] not in page['title']:raise ValueError('翻页后店铺身份变化')
    if page['rows'] and page['total']==total:break
    if time.monotonic()>=deadline:raise RuntimeError('商品分页未就绪')
    time.sleep(.5)
   write(dest/('page_'+str(number)+'.json'),page)
   for item in page['rows']:
    pid=item['product_id']
    js="""(()=>{document.querySelectorAll('[data-product-images-expand]').forEach(e=>e.removeAttribute('data-product-images-expand'));const a=[...document.querySelectorAll('.cell-product .ip-title[href]')].find(a=>new URL(a.href).searchParams.get('vendorInventoryId')===%s);const e=a?.closest('tr')?.querySelector('.cell-selector .list-v3-arrow');if(!e)return null;e.setAttribute('data-product-images-expand','1');return e.className})()"""%__import__('json').dumps(pid)
    arrow=browser.evaluate(js)
    if arrow and 'down' in arrow:browser.click('[data-product-images-expand="1"]')
    deadline=time.monotonic()+15
    while True:
     expanded=browser.evaluate(SCRIPT);row=next((r for r in expanded['rows'] if r['product_id']==pid),None)
     if row and row['options']:break
     if time.monotonic()>=deadline:raise RuntimeError('商品选项未完整加载：'+pid)
     time.sleep(.5)
    all_rows.append(row)
   write(dest/('page_'+str(number)+'_expanded.json'),browser.evaluate(SCRIPT))
  coverage=verify_coverage(all_rows,total)
  page['rows']=all_rows
 else:coverage={'expected_products':None,'observed_products':len(page['rows']),'complete':False}
 rows=map_options(page['rows'],mapping)
 for row in rows:
  observed={o['option_id'] for o in row.get('options',[])}
  row['option_ids']=sorted(set(row['option_ids'])|observed)

 if product_id:rows=[r for r in rows if r['product_id']==product_id]
 if not rows:raise ValueError('当前可见列表没有该商品；不猜测图片或自动改筛选')
 result={'store_id':profile['store_id'],'captured_at':stamp.isoformat(),'source_url':page['url'],'scope':'all_listed_products' if all_products else 'current_visible_product_rows','coverage':coverage,'option_mapping_sources':sources,'rows':[],'option_images':[],'errors':[]}
 for row in rows:
  try:
   row['image_url']=image_url(row['image_url'])
   with urllib.request.build_opener(NoRedirect()).open(row['image_url'],timeout=20) as response:data=response.read(LIMIT+1)
   if len(data)>LIMIT:raise ValueError('图片超过12MB上限')
   kind=image_type(data);ext={'image/png':'.png','image/jpeg':'.jpg','image/gif':'.gif','image/webp':'.webp'}[kind]
   path=dest/'products'/row['product_id']/('main'+ext);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
   row.update(file=str(path),sha256=sha(path),media_type=kind,status='complete')
   for oid in row['option_ids']:
    observed=next((o for o in row.get('options',[]) if o['option_id']==oid),{})
    entry={'option_id':oid,'product_id':row['product_id'],'name':observed.get('name'),'image_scope':'product_shared','option_specific_verified':False,'file':str(path),'sha256':row['sha256'],'status':'complete','mapping_basis':'wing_option_row' if observed else 'verified_report_batch'}
    if observed.get('image_url'):
     source=image_url(observed['image_url'])
     if source==row['image_url']:data0=data;kind0=kind
     else:
      with urllib.request.build_opener(NoRedirect()).open(source,timeout=20) as response:data0=response.read(LIMIT+1)
      if len(data0)>LIMIT:raise ValueError('选项图片超过12MB上限')
      kind0=image_type(data0)
     ext0={'image/png':'.png','image/jpeg':'.jpg','image/gif':'.gif','image/webp':'.webp'}[kind0]
     opath=dest/'options'/oid/('main'+ext0);opath.parent.mkdir(parents=True,exist_ok=True);opath.write_bytes(data0)
     entry.update(file=str(opath),sha256=sha(opath),image_url=source,image_scope='option_row',option_specific_verified=True)
    write(dest/'options'/oid/'image.json',entry);result['option_images'].append(entry)
  except Exception as exc:row.update(status='failed',error=str(exc));result['errors'].append({'product_id':row['product_id'],'error':str(exc)})
  result['rows'].append(row);write(dest/'manifest.json',result)
 result['status']='incomplete' if result['errors'] else 'complete';result['downloaded']=sum(r['status']=='complete' for r in result['rows']);result['manifest']=str(dest/'manifest.json');write(dest/'manifest.json',result)
 return result
