"""Reuse verified fee downloads for a rolling interval; never invent a billing period."""
import runtime_clock as clock
from pathlib import Path
from datetime import datetime,timedelta
import json,hashlib
def metadata(entry,on,period,reused):
 if not isinstance(period,int) or isinstance(period,bool) or period<=0:raise ValueError('费用间隔必须是正整数天数')
 captured=datetime.fromisoformat(entry['captured_at'])
 if captured.tzinfo is None:raise ValueError('费用下载时间缺少时区')
 last=captured.astimezone(clock.timezone()).date()
 return {'period':period,'interval_days':period,'evaluated_on':str(on),'last_download_date':str(last),'next_due_date':str(last+timedelta(days=period)),'reused':reused}
def reusable(root,profile,key,on,period):
 if not isinstance(period,int) or isinstance(period,bool) or period<=0:raise ValueError('费用间隔必须是正整数天数')
 state=Path(root)/'state';paths=[state/'supplement_run.json',*sorted((state/'full_exports').glob('*/manifest.json')),*sorted((state/'functions').rglob('*.json'))];candidates=[]
 for path in paths:
  if not path.is_file():continue
  d=json.loads(path.read_text())
  if d.get('store_id'):
   if d['store_id']!=profile['store_id']:continue
  elif d.get('store')!=profile['store_name']:continue
  rows=d.get('steps',{});entry=rows.get(key) if isinstance(rows,dict) else None
  if entry is None:
   entry=next((r for r in d.get('items',[]) if r.get('fee_kind')==key or ('_'+key+'_') in Path(r.get('file','')).name),None)
  if not entry or entry.get('settlement_batch') or entry.get('status')!='complete' or not all(entry.get(k) for k in ('file','sha256','captured_at')):continue
  m=metadata(entry,on,period,True);candidates.append((m['last_download_date'],entry,m,str(path)))
 if not candidates:return None
 _,entry,m,source=max(candidates,key=lambda x:datetime.fromisoformat(x[1]['captured_at']))
 if str(on)<m['last_download_date']:raise ValueError('费用文件采集日期晚于本次检查日期，不能作为历史数据复用')
 path=Path(entry['file']).resolve()
 if not path.is_relative_to((Path(root)/'state').resolve()):raise ValueError('费用原文件不在本店state目录，停止跨店铺复用')
 if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=entry['sha256']:raise ValueError('上次费用原文件缺失或摘要不符，停止自动下载并核对原档')
 if str(on)>=m['next_due_date']:return None
 return {**entry,'fee_kind':key,'cadence':{**m,'source_checkpoint':source}}
