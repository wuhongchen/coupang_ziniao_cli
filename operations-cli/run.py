#!/usr/bin/env python3
"""Explicit single-capability operations. No scheduler or implicit advertising writes."""
import argparse,json,sys,subprocess
from contextlib import ExitStack
from pathlib import Path
from datetime import date,datetime,timedelta
BASE=Path(__file__).resolve().parent;BACKEND=BASE.parent/'backend'
sys.path.insert(0,str(BACKEND))
import runtime_clock as clock
from storage import inventory,organize,read,write
from export_profiles import load_profile,browser_lease
import ziniao_environment as runtime
import ziniao_preflight as preflight

CORE={'sales-daily':'sales_daily','sales-window':'sales_window','inventory':'inventory','returns':'returns','movement':'movement'}
FEES={'fee-sales':'销售手续费报告','fee-fulfillment':'仓库操作费/配送费报告','fee-storage':'仓储费率报','fee-return':'退货回收费/重新入库费报告','fee-removal':'退仓费报告','fee-removal-delivery':'退仓配送服务费报告','fee-compensation':'库存损失赔偿报告','fee-extra':'附加服务费报告','fee-review':'评价活动费用报告'}
DESCRIPTIONS={'sales-daily':'指定一天的销售','sales-window':'指定期间销售','inventory':'可售及待入库库存','returns':'退货明细','movement':'全部库存变动','ads-keywords':'逐日商品关键词广告报表','ads-bill-current':'本月广告账单','ads-bill-previous':'上月广告账单','category':'指定类目可见榜单','products':'商品目录页面','keywords-traffic':'已知选项的关键词与流量','reviews':'商品评价列表','images':'商品列表头图及已核验选项对应关系',**FEES}
CATALOG=[{'id':k,'description':v,'platform_writes':False,'requires_login_check':True,'operation':'page_export_and_verified_import' if k in CORE else 'page_export'} for k,v in DESCRIPTIONS.items()]

def core_steps(name,day,start,end):
 kind=CORE[name]
 if name=='sales-window':
  if not start or not end:raise ValueError('期间销售必须提供 --start 和 --end')
  date.fromisoformat(start);date.fromisoformat(end)
  if start>end:raise ValueError('起止日期无效')
 else:
  if not day:raise ValueError('必须提供 --date')
  date.fromisoformat(day);start=end=day
 return [{'kind':kind,'start':start,'end':end}]

def collect_core(root,profile,name,day,start,end):
 import ziniao_exports as z
 steps=core_steps(name,day,start,end);run_day=day or end
 token=name+'_'+steps[0]['start']+'_'+steps[0]['end']
 checkpoint=root/'state/functions'/run_day/(token+'.json')
 # An unrelated unfinished export owns the browser; never replay its click from a new entry.
 old=root/'state/collection_run.json'
 if old.exists() and read(old).get('status')!='complete':raise ValueError('旧导出断点未完成，请先用原入口恢复；不新建下载点击')
 for p in (root/'state/functions').rglob('*.json'):
  if p!=checkpoint and read(p).get('status') not in ('complete','skipped_permission'):raise ValueError('其他独立采集断点未完成：'+str(p))
 z.configure_runtime(root,profile);z.STATE=checkpoint
 run=read(checkpoint) if checkpoint.exists() else {'run_date':run_day,'status':'ready','index':0,'steps':steps,'results':[],'options':{'function':name}}
 if run.get('store_id',profile['store_id'])!=profile['store_id'] or [{k:s.get(k) for k in ('kind','start','end')} for s in run['steps']]!=steps:raise ValueError('独立断点身份或日期冲突')
 z.write(run)
 if run['status']=='complete':
  z.final_gate(run['results'],steps,root,profile['store_id']);return run
 waits=0
 while True:
  out=z.next_export(run)
  if out.get('status')=='imported':waits=0;continue
  if out.get('status')=='awaiting_download' and waits<2:waits+=1;continue
  if out.get('status')!='complete':raise RuntimeError('独立采集未完成：'+json.dumps(out,ensure_ascii=False))
  return run

def collect_supplement(root,profile,name,day,period=None,start=None,end=None,category_id=None):
 from supplement_collection import Collector
 checkpoint=root/'state/functions'/day/(name+'.json')
 checkpoint.parent.mkdir(parents=True,exist_ok=True)
 c=Collector(root,profile,day,start=start,end=end,category_id=category_id)
 # Independent parameter-bound checkpoints never reuse another query's completion.
 initial=c.run
 c.file=checkpoint
 c.run=read(checkpoint) if checkpoint.exists() else {**initial,'steps':{}}
 if c.run.get('store_id')!=profile['store_id']:raise ValueError('独立断点店铺不匹配')
 requested={'start':start,'end':end,'category_id':category_id}
 if c.run.get('parameters',requested)!=requested:raise ValueError('断点参数不同，请使用原参数续跑或新的运行日期')
 c.run['parameters']=requested
 c.audit=root/'state/full_exports'/(day+'_functions')/name;c.audit.mkdir(parents=True,exist_ok=True);c.run['audit']=str(c.audit)
 if name=='ads-keywords':c.ads()
 elif name.startswith('ads-bill-'):c.bills(current=name=='ads-bill-current')
 elif name in FEES:c.fees(only=FEES[name],period=period)
 elif name in ('category','products','keywords-traffic'):
  try:c.pages(section={'keywords-traffic':'keywords'}.get(name,name))
  except RuntimeError as exc:
   if 'DATA_PERMISSION_DENIED:' not in str(exc):raise
   c.run.update(status='skipped_permission',function=name,error=runtime.redact(exc));c.save();return c.run
 elif name=='reviews':c.reviews()
 for entry in c.run['steps'].values():
  if entry.get('status')=='complete' and entry.get('file'):
   from storage import sha
   path=Path(entry['file'])
   if not path.is_file() or sha(path)!=entry['sha256']:raise ValueError('补充原文件缺失或SHA256不匹配')
 c.run['status']='complete';c.run['function']=name;c.save();return c.run

def main(argv=None):
 p=argparse.ArgumentParser(description='店铺独立功能CLI：无调度、无隐式广告调整')
 p.add_argument('--output-root',type=Path,default=BASE.parent);runtime.options(p);sub=p.add_subparsers(dest='command',required=True)
 sub.add_parser('doctor');sub.add_parser('stores');sub.add_parser('check-store');sub.add_parser('catalog');sub.add_parser('inventory');sub.add_parser('archive')
 q=sub.add_parser('collect');q.add_argument('function',choices=DESCRIPTIONS);q.add_argument('--date');q.add_argument('--start');q.add_argument('--end');q.add_argument('--target-id');q.add_argument('--product-id');q.add_argument('--option-id');q.add_argument('--all',action='store_true');q.add_argument('--fee-interval-days',type=int);q.add_argument('--category-id')
 q=sub.add_parser('import');q.add_argument('--file',type=Path,required=True);q.add_argument('--kind',choices=CORE.values(),required=True);q.add_argument('--start',required=True);q.add_argument('--end',required=True)
 sub.add_parser('analyze')
 q=sub.add_parser('sales-days',help='指定天数逐日导出并生成分析');q.add_argument('--end',required=True);q.add_argument('--days',type=int,required=True);q.add_argument('--refresh',action='store_true')
 for group in ('bills','advertising','analytics'):
  q=sub.add_parser(group);q.add_argument('--date');q.add_argument('--start');q.add_argument('--end');q.add_argument('--category-id');q.add_argument('--fee-interval-days',type=int)
 q=sub.add_parser('ads');q.add_argument('args',nargs=argparse.REMAINDER,help='仅download/collect下载广告关键词报表；不提供配置或分析')
 a=p.parse_args(argv);root=a.output_root.expanduser().resolve();now=datetime.now(clock.timezone());run_info={'id':now.strftime('%H%M%S_%f')+'_'+a.command,'date':str(now.date()),'function':a.command+(':'+a.function if hasattr(a,'function') else '')}
 env=ExitStack()
 try:
  env.enter_context(runtime.environment(a))
  now=clock.now();run_info['date']=str(now.date())
  if hasattr(a,'date') and a.date is None:a.date=str(now.date())
  if a.command in ('advertising','analytics') and not (a.start and a.end):raise ValueError('组合必须明确--start和--end')
  if a.command=='analytics' and not a.category_id:raise ValueError('数据分析组合必须明确--category-id')
  if a.command=='bills' and (a.fee_interval_days is None or a.fee_interval_days<=0):raise ValueError('账单组合必须明确--fee-interval-days正整数')
  if a.command=='sales-days' and (a.days<=0 or a.days>31):raise ValueError('销售天数须为1–31')
  if hasattr(a,'start') and (a.start or a.end) and a.command not in ('sales-days',):
   if not a.start or not a.end or date.fromisoformat(a.start)>date.fromisoformat(a.end):raise ValueError('请提供有效的--start和--end')
  if hasattr(a,'category_id') and a.category_id is not None and not a.category_id.isdigit():raise ValueError('类目ID必须是数字')
  if a.command=='collect':
   if a.function in ('ads-keywords','keywords-traffic') and not (a.start and a.end):raise ValueError('必须明确--start和--end')
   if a.function=='category' and not a.category_id:raise ValueError('必须明确--category-id')
   if a.function in FEES and (a.fee_interval_days is None or a.fee_interval_days<=0):raise ValueError('必须明确--fee-interval-days正整数')
  if a.command=='catalog':out={'capabilities':CATALOG,'local':['doctor','stores','check-store','inventory','archive','import','analyze','sales-days'],'ads':['download','collect'],'groups':['bills','advertising','analytics'],'scheduling':'disabled: CLI never creates schedules'}
  elif a.command=='doctor':out={'status':'complete','environment':preflight.doctor()}
  elif a.command=='stores':out=preflight.stores()
  else:
   profile=runtime.select_store(load_profile(root),a.store_id)
   if a.command in ('bills','advertising','analytics'):
    date.fromisoformat(a.date)
    from groups import run_group
    out=run_group(root,profile,a.command,a.date,a.fee_interval_days,a.start,a.end,a.category_id)
   elif a.command=='check-store':
    with browser_lease(root):out=preflight.check(root,profile)
   elif a.command=='inventory':out=inventory(root)
   elif a.command=='ads':
    if not a.args or a.args[0] not in ('download','collect'):raise ValueError('广告CLI仅保留download/collect报表下载；配置、现状读取及分析入口已移除')
    result=subprocess.run([sys.executable,str(BASE/'run.py'),'--output-root',str(root),'--store-id',profile['browser_id'],'collect','ads-keywords',*a.args[1:]],shell=False)
    return result.returncode
   else:
    with browser_lease(root):
     if a.command in ('collect','sales-days'):
      preflight.check(root,profile)
     if a.command=='sales-days':
      from sales_trend import run as sales_seven
      out=sales_seven(root,profile,a.end,a.days,a.refresh)
     elif a.command=='archive':out=organize(root,run_info=run_info)
     elif a.command=='collect':
      if a.fee_interval_days is not None and a.function not in FEES:raise ValueError('--fee-interval-days仅适用于fee-*费用采集')
      if a.function in FEES and (a.start or a.end):raise ValueError('费用不支持任意账期；--date为检查日期，--fee-interval-days为采集间隔')
      if a.function=='sales-daily' and not a.date:raise ValueError('单日销售必须明确 --date，不自动采集当日暂定数据')
      day=a.date or str(datetime.now(clock.timezone()).date());date.fromisoformat(day)
      if a.function not in CORE:
       legacy=root/'state/collection_run.json'
       if legacy.exists() and read(legacy).get('status')!='complete':raise ValueError('旧核心采集断点未完成，先恢复原入口')
       expected=root/'state/functions'/day/(a.function+'.json')
       for checkpoint in (root/'state/functions').rglob('*.json'):
        if checkpoint!=expected and read(checkpoint).get('status') not in ('complete','skipped_permission'):raise ValueError('其他独立采集尚未完成，先恢复：'+str(checkpoint))
      if a.function=='images':
       from product_images import collect
       result=collect(root,profile,a.target_id,a.product_id,a.option_id,a.all)
      else:
       if any((a.target_id,a.product_id,a.option_id,a.all)):raise ValueError('target-id、product-id、option-id仅用于images')
       result=collect_core(root,profile,a.function,day,a.start,a.end) if a.function in CORE else collect_supplement(root,profile,a.function,day,a.fee_interval_days,a.start,a.end,a.category_id)
      out={'status':result.get('status','complete'),'function':a.function,'result':result,'archive':organize(root,run_info=run_info)}
     elif a.command=='import':
      from report_pipeline import accept_export
      result=accept_export(a.file,a.kind,a.start,a.end,root=root,store=profile['store_id']);out={'status':'complete','result':result,'archive':organize(root,run_info=run_info)}
     elif a.command=='analyze':
      from report_pipeline import export_analysis
      result=export_analysis(root,profile['store_id'],profile['store_name'])
      target=root/'state/analyses'/(datetime.now(clock.timezone()).strftime('%Y-%m-%d_%H%M%S_%f')+'_store.json');write(target,result)
      out={'status':'complete','analysis':str(target),'scope':'已核验销售与库存；非全链路完整分析','archive':organize(root,run_info=run_info)}
  print(json.dumps(out,ensure_ascii=False,default=str));return 1 if out.get('status') in ('failed','incomplete') or ('archive' in out and out['archive'].get('status')!='complete') else 0
 except Exception as exc:
  try:failure=runtime.record_failure(root,a.command+(':'+a.function if hasattr(a,'function') else ''),exc)
  except OSError:failure=None
  print(json.dumps({'status':'failed','error':runtime.redact(exc),'failure_record':failure},ensure_ascii=False));return 1
 finally:env.close()
if __name__=='__main__':raise SystemExit(main())
