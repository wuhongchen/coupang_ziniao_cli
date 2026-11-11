"""Non-destructive, store/date/category archive of existing local exports."""
import runtime_clock as clock
from operation_progress import progress
from pathlib import Path
from datetime import date,datetime
import csv,hashlib,json,re,shutil,sqlite3

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def write(path,data):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)
def category(path):
 text=str(path)
 if 'product_images' in path.parts:return 'product_images'
 for marker,name in [('sales_daily','sales_daily'),('sales_window','sales_window'),('inventory_','inventory'),('returns_','returns'),('movement_','movement'),('广告每日结算','ads_bills'),('dailySettlement','ads_bills'),('广告活动汇总','ads_campaigns'),('pa_total_campaign','ads_campaigns'),('PPIR','products'),('全量商品','products'),('评价活动费用','settlement_fees'),('review_scans','review_reconciliation'),('评价核对','review_reconciliation'),('加载未完成','incomplete_pages'),('order_report_recheck','order_audit'),('daily_sales_recheck','sales_audit')]:
  if marker in text:return name
 for marker,name in [('选项流量关键词','keywords_traffic'),('类目','category'),('商品目录','products'),('商品评价','reviews'),('广告逐日','ads_keywords'),('广告账单','ads_bills'),('ad_creations','ads_audit'),('ads_ops','ads_operations'),('analyses','analysis'),('collection_history','collection_audit'),('failures','collection_audit'),('preflight','collection_audit'),('functions','function_audit')]:
  if marker in text:return name
 if any(x in text for x in ('手续费','费报告','费率','赔偿')):return 'settlement_fees'
 if 'full_exports' in text:return 'collection_audit'
 return None

def inventory(root):
 root=Path(root).resolve();state=root/'state';profile=read(state/'store_profile.json');bid=profile['browser_id'];store=profile['store_id']
 if not re.fullmatch(r'\d+',bid) or store!='ziniao:'+bid:raise ValueError('店铺身份无效')
 known={};unassigned=[];errors=[]
 def add(path,kind,period=None,captured=None,expected=None,provenance='bound_state_root'):
  path=Path(path).resolve()
  if path.is_dir():return
  if not path.is_file():errors.append({'source':str(path),'error':'原文件不存在'});return
  digest=sha(path)
  if expected and digest!=expected:errors.append({'source':str(path),'error':'来源SHA256不匹配'});return
  basis='source_metadata' if captured else 'file_mtime'
  try:t=datetime.fromisoformat(captured) if captured else datetime.fromtimestamp(path.stat().st_mtime,clock.timezone())
  except ValueError:errors.append({'source':str(path),'error':'采集时间无效'});return
  if t.tzinfo is None:t=t.replace(tzinfo=clock.timezone());basis+=':assumed_runtime_timezone'
  t=t.astimezone(clock.timezone())
  if str(path) in known:return
  known[str(path)]={'source':str(path),'store_id':store,'store_name':profile['store_name'],'category':kind,'business_period':period,'captured_at':t.isoformat(),'capture_time_basis':basis,'sha256':digest,'bytes':path.stat().st_size,'provenance':provenance}
 dbfile=state/'report_pipeline.sqlite3'
 if dbfile.exists():
  with sqlite3.connect(f'file:{dbfile}?mode=ro',uri=True) as db:
   db.row_factory=sqlite3.Row
   for b in db.execute('SELECT store,kind,start,end,captured,file,digest FROM batches'):
    if b['store']==store:add(root/b['file'],b['kind'],[b['start'],b['end']],b['captured'],b['digest'],'verified_batch_store')
 # Only manifests bound to this store can supply a file's original period/digest.
 for p in [state/'supplement_run.json',*sorted((state/'full_exports').glob('*/manifest.json'))]:
  if not p.exists():continue
  d=read(p)
  if d.get('store_id',store)!=store:continue
  if d.get('store') and d['store']!=profile['store_name']:continue
  items=d.get('steps',{}).values() if isinstance(d.get('steps'),dict) else d.get('items',[])
  for v in items:
   if v.get('file'):
    f=Path(v['file']);f=f if f.is_absolute() else root/f
    k=category(f) or 'supplement_other'
    period=v.get('export_scope',{}).get('period') or v.get('period')
    if isinstance(period,dict):period=[period.get('start'),period.get('end')] if period.get('start') and period.get('end') else None
    add(f,k,period,v.get('captured_at'),v.get('sha256'),'store_manifest')
 for folder in ('full_exports','reports','analyses','collection_history','ad_creations','ads_ops','functions','product_images','preflight','failures','review_scans'):
  for p in sorted((state/folder).rglob('*')):
   if not p.is_file() or p.is_symlink() or p.suffix.lower() not in ('.xlsx','.xls','.xlsm','.csv','.json','.md','.png','.jpg','.jpeg','.webp','.gif','.txt','.html'):continue
   if str(p.resolve()) in known:continue
   k=category(p)
   # Unregistered spreadsheets must never acquire a store merely from their filename.
   if folder=='reports':
    if '自动补充' in p.parts and bid in p.parts and k:add(p,k,provenance='bound_supplement_store_directory')
    else:unassigned.append({'source':str(p.resolve()),'reason':'无已核验店铺批次或清单'})
   elif k:add(p,k)
   else:unassigned.append({'source':str(p.resolve()),'reason':'数据分类未确认'})
 for filename in ('collection_run.json','daily_workflow.json','supplement_run.json'):
  p=state/filename
  if p.exists():
   value=read(p)
   if value.get('store_id',store)==store:add(p,'workflow_audit')
 cost_file=state/'report_costs.json'
 if cost_file.exists():add(cost_file,'cost_inputs',provenance='bound_local_cost_input_unverified')
 # Opaque legacy cache names have no reliable product or shop mapping.
 for p in sorted((state/'images').glob('*.img')):
  if p.is_file():unassigned.append({'source':str(p.resolve()),'reason':'旧图片缓存缺少可靠店铺及商品映射，保留原文件，不冒充已核验头图'})
 # Read-only inspection of configured browser download folders. Shared/unattributed files stay unassigned.
 for folder in profile.get('download_dirs',[]):
  for p in sorted(Path(folder).expanduser().rglob('*')):
   if not p.is_file() or p.suffix.lower() not in ('.xlsx','.xls','.csv'):continue
   if str(p.resolve()) in known:continue
   same=next((r for r in known.values() if r['sha256']==sha(p)),None)
   if same:add(p,same['category'],same['business_period'],provenance='sha256_matches_verified_source')
   else:unassigned.append({'source':str(p.resolve()),'reason':'下载文件尚无店铺及数据分类验证'})
 # Historical duplicate paths are attributed only through a byte-for-byte verified source.
 unresolved=[]
 by_hash={r['sha256']:r for r in known.values()}
 for item in unassigned:
  p=Path(item['source']);same=by_hash.get(sha(p))
  if same and p.suffix.lower()!='.img':add(p,same['category'],same['business_period'],provenance='sha256_matches_verified_source')
  else:unresolved.append(item)
 unassigned=unresolved
 return {'schema_version':1,'store_id':store,'store_name':profile['store_name'],'timezone':clock.timezone_name(),'files':list(known.values()),'unassigned':unassigned,'errors':errors}

def migrate_layout(base):
 # Old date/category/time layout is retained as a backup, not deleted.
 for day in sorted(base.glob('????-??-??')):
  if not day.is_dir():continue
  children=[p for p in day.iterdir() if p.is_dir()]
  old=[p for p in children if p.name in {'sales_daily','sales_window','inventory','returns','movement','ads_keywords','ads_campaigns','ads_bills','settlement_fees','products','product_images','category','reviews','keywords_traffic','analysis','ads_operations','ads_audit','collection_audit','function_audit','workflow_audit','incomplete_pages','sales_audit','order_audit','review_reconciliation','supplement_other'}]
  for folder in old:
   target=base/'_legacy_layout'/day.name/folder.name
   if target.exists():raise ValueError('旧归档备份已存在，拒绝覆盖：'+str(target))
   target.parent.mkdir(parents=True,exist_ok=True);folder.rename(target)
  for folder in list(day.glob('historical_[0-9]*')):
   target=base/'_legacy_layout/early_runs'/day.name/folder.name
   if target.exists():raise ValueError('历史批次备份已存在，拒绝覆盖')
   target.parent.mkdir(parents=True,exist_ok=True);folder.rename(target)


def publish_basics(root,base,d):
 profile=read(root/'state/store_profile.json');products={};options={};latest_full=None
 lookup={r['source']:r['archive'] for r in d['files']}
 manifests=[]
 for path in (root/'state/product_images').glob('*/*/manifest.json'):
  value=read(path)
  if value.get('store_id')==d['store_id']:manifests.append((value.get('captured_at',''),path,value))
 for captured,path,value in sorted(manifests):
  if value.get('status')=='complete' and value.get('scope')=='all_listed_products':latest_full=value
  for r in value.get('rows',[]):
   if r.get('status')!='complete':continue
   source=Path(r['file']).resolve();file=lookup.get(str(source))
   if not file or sha(source)!=r['sha256']:continue
   products[r['product_id']]={'product_id':r['product_id'],'name':r['name'],'option_ids':r.get('option_ids',[]),'image_file':file,'image_url':r.get('image_url'),'sha256':r['sha256'],'image_scope':r.get('image_scope','product_shared'),'captured_at':captured,'source_manifest':str(path.resolve())}
   for oid in r.get('option_ids',[]):options[oid]={'option_id':oid,'product_id':r['product_id'],'image_file':file,'sha256':r['sha256'],'image_scope':'product_shared','option_specific_verified':False,'captured_at':captured}
  for o in value.get('option_images',[]):
   if o.get('status')!='complete':continue
   file=lookup.get(str(Path(o['file']).resolve()))
   if file:options[o['option_id']]={k:v for k,v in o.items() if k not in ('file','status') }|{'image_file':file,'captured_at':captured}
 if latest_full is not None:
  current={r['product_id'] for r in latest_full['rows']}
  for pid,r in products.items():r['in_latest_full_collection']=pid in current
 cost_file=root/'state/report_costs.json'
 if cost_file.exists():write(base/'cost_inputs.json',{'store_id':d['store_id'],'verification_status':'unverified_local_input','definition':'原本地成本输入；不代表完整批次成本或已核验实际利润','source':str(cost_file),'sha256':sha(cost_file),'data':read(cost_file)})
 captured=max((x[0] for x in manifests),default=None)
 write(base/'products.json',{'store_id':d['store_id'],'updated_from_capture':captured,'coverage':latest_full.get('coverage') if latest_full else None,'products':list(products.values())})
 write(base/'options.json',{'store_id':d['store_id'],'updated_from_capture':captured,'options':list(options.values())})
 write(base/'store.json',{'schema_version':2,'store_id':d['store_id'],'browser_id':profile['browser_id'],'store_name':d['store_name'],'merchant_code':profile.get('ads_identity'),'timezone':clock.timezone_name(),'product_count':len(products),'option_image_mapping_count':len(options),'data_source':'existing_bound_state','original_state':str(root/'state')})
 with (base/'商品基础资料.csv').open('w',encoding='utf-8-sig',newline='') as f:
  writer=csv.writer(f);writer.writerow(['商品ID','商品名称','选项ID','头图文件','图片来源','采集时间','SHA256'])
  for r in products.values():writer.writerow([r['product_id'],r['name'],','.join(r['option_ids']),r['image_file'],r.get('image_url'),r['captured_at'],r['sha256']])
 return {'products':len(products),'options':len(options)}


def organize(root,run_info=None):
 progress("正在整理店铺归档并更新文件索引")
 root=Path(root).resolve();d=inventory(root);bid=d['store_id'].split(':',1)[1]
 base=(root/'data/stores'/bid).resolve();base.mkdir(parents=True,exist_ok=True)
 previous=read(base/'catalog.json') if (base/'catalog.json').exists() else {}
 if previous and previous.get('schema_version')!=2:
  backup=base/'_legacy_layout';backup.mkdir(exist_ok=True)
  for filename in ('catalog.json','catalog.csv','待确认文件.csv'):
   p=base/filename
   if p.exists() and not (backup/filename).exists():shutil.copy2(p,backup/filename)
 migrate_layout(base)
 old={r['source']:r for r in previous.get('files',[])}
 if run_info:
  date.fromisoformat(run_info['date'])
  if not re.fullmatch(r'[A-Za-z0-9_-]+',run_info['id']):raise ValueError('无效运行ID')
 d['schema_version']=2
 for r in d['files']:
  t=datetime.fromisoformat(r['captured_at']);name=Path(r['source']).name
  before=old.get(r['source'],{})
  if before.get('sha256')==r['sha256'] and before.get('run_id') and not re.fullmatch(r'historical_[0-9_]+',before['run_id']):
   run_day,run_id,basis=before['run_date'],before['run_id'],before['run_basis']
  elif run_info and (not before or before.get('sha256')!=r['sha256']):
   run_day,run_id,basis=run_info['date'],run_info['id'],'cli_execution'
  else:
   run_day,run_id,basis=t.strftime('%Y-%m-%d'),'historical_import','historical_capture_or_mtime'
   parts=Path(r['source']).parts
   for marker in ('product_images','full_exports','ads_ops'):
    if marker in parts:
     tail=parts[parts.index(marker)+1:]
     if marker=='product_images' and len(tail)>1:run_id='images_'+tail[1]
     elif marker=='full_exports' and tail:run_id='exports_'+re.sub(r'[^A-Za-z0-9_-]','_',tail[0])
     elif marker=='ads_ops' and len(tail)>2 and tail[0]=='runs':run_id='ads_'+tail[2]
  r.update(run_date=run_day,run_id=run_id,run_basis=basis,run_function=run_info.get('function') if run_info and run_id==run_info['id'] else before.get('run_function'))
  parts=Path(r['source']).parts
  if r['category']=='product_images' and Path(name).suffix.lower() in ('.png','.jpg','.jpeg','.gif','.webp'):
   image_scope='products' if 'products' in parts else 'options' if 'options' in parts else None
   if not image_scope:raise ValueError('图片缺少商品或选项目录身份')
   ident=parts[parts.index(image_scope)+1]
   if not ident.isdigit():raise ValueError('图片目录ID无效')
   dest=base/'images'/image_scope/ident/(r['sha256'][:16]+'_'+name)
  else:dest=base/run_day/run_id/r['category']/(r['sha256'][:16]+'_'+name)
  dest.parent.mkdir(parents=True,exist_ok=True)
  if dest.exists():
   if sha(dest)!=r['sha256']:raise ValueError('归档内容损坏，拒绝覆盖：'+str(dest))
  else:
   tmp=dest.with_suffix(dest.suffix+'.partial');shutil.copy2(r['source'],tmp)
   if sha(tmp)!=r['sha256']:raise ValueError('归档校验失败：'+str(dest))
   tmp.replace(dest)
  r['archive']=str(dest)
 for r in d['unassigned']:
  p=Path(r['source']);digest=sha(p);t=datetime.fromtimestamp(p.stat().st_mtime,clock.timezone())
  dest=(root/'data/unassigned').resolve()/t.strftime('%Y-%m-%d')/(digest[:16]+'_'+p.name);dest.parent.mkdir(parents=True,exist_ok=True)
  if dest.exists() and sha(dest)!=digest:raise ValueError('待确认归档内容损坏：'+str(dest))
  if not dest.exists():
   tmp=dest.with_suffix(dest.suffix+'.partial');shutil.copy2(p,tmp)
   if sha(tmp)!=digest:raise ValueError('待确认归档校验失败')
   tmp.replace(dest)
  r.update(archive=str(dest),sha256=digest,capture_time_basis='file_mtime',file_modified_at=t.isoformat())
 basics=publish_basics(root,base,d)
 groups={}
 for r in d['files']:groups.setdefault((r['run_date'],r['run_id']),[]).append(r)
 if run_info:groups.setdefault((run_info['date'],run_info['id']),[])
 for (run_day,run_id),rows in groups.items():
  write(base/run_day/run_id/'run.json',{'store_id':d['store_id'],'run_date':run_day,'run_id':run_id,'script':'wing-ops' if run_info and run_id==run_info['id'] else None,'function':run_info.get('function') if run_info and run_id==run_info['id'] else rows[0].get('run_function') if rows else None,'basis':rows[0]['run_basis'] if rows else 'cli_execution','file_count':len(rows),'files':[{'source':r['source'],'archive':r['archive'],'category':r['category'],'sha256':r['sha256']} for r in rows]})
 (base/'README.md').write_text('# '+d['store_name']+'：店铺数据目录\n\n店铺ID：'+d['store_id']+'\n\n- store.json：店铺基础信息\n- products.json / 商品基础资料.csv：商品与图片索引\n- options.json：选项资料和图片对应关系\n- cost_inputs.json：已有本地成本输入，未经批次成本核验\n- images/products/、images/options/：已下载图片，按ID和文件摘要保存\n- YYYY-MM-DD/运行批次/数据分类/：每日脚本输出；run.json记录本次文件\n- catalog.csv / catalog.json：全部文件目录\n- 待确认文件.csv：尚未核验归属的文件，实际副本位于data/unassigned\n- _legacy_layout/：改版前分类目录的保留备份\n\n工作台读取本店workbench/latest.json已发布版本；采集器继续通过原入口写入state。历史文件缺乏运行记录时，批次按采集时间或文件修改时间组织，并在run.json标明推定依据。当前基础资料是最新投影，历史原始快照继续保留。\n',encoding='utf-8')
 manifest=base/'catalog.json';write(manifest,d)
 with manifest.with_suffix('.csv').open('w',encoding='utf-8-sig',newline='') as f:
  writer=csv.writer(f);writer.writerow(['店铺','店铺ID','采集时间','时间依据','数据分类','业务周期','运行日期','运行批次','批次依据','原始文件','归档文件','SHA256'])
  for r in d['files']:writer.writerow([r['store_name'],r['store_id'],r['captured_at'],r['capture_time_basis'],r['category'],json.dumps(r['business_period'],ensure_ascii=False) if r['business_period'] else '',r['run_date'],r['run_id'],r['run_basis'],r['source'],r['archive'],r['sha256']])
 with (manifest.parent/'待确认文件.csv').open('w',encoding='utf-8-sig',newline='') as f:
  writer=csv.writer(f);writer.writerow(['原始文件','待确认原因','保留副本','SHA256'])
  for r in d['unassigned']:writer.writerow([r['source'],r['reason'],r['archive'],r['sha256']])
 from publication import publish
 publication=publish(root,base,d) if not d['errors'] else {'status':'not_published','reason':'archive_errors'}
 return {'publication':publication,'status':'complete' if not d['errors'] else 'incomplete','archived':len(d['files']),'unassigned':len(d['unassigned']),'errors':d['errors'],'manifest':str(manifest),'store_directory':str(base),'basics':basics,'originals_preserved':True}
