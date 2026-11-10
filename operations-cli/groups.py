"""Compose existing independent commands; preserve their locks and checkpoints."""
import runtime_clock as clock
import json,subprocess,sys
from pathlib import Path
from datetime import datetime
from storage import write

BILLS=['ads-bill-previous','ads-bill-current','fee-sales','fee-fulfillment','fee-storage','fee-return','fee-removal','fee-removal-delivery','fee-compensation','fee-extra','fee-review']
GROUPS={'bills':BILLS,'advertising':['ads-keywords'],'analytics':['products','category','keywords-traffic']}

def execute(root,store,args):
 p=subprocess.run([sys.executable,str(Path(__file__).with_name('run.py')),'--output-root',str(root),'--store-id',store,*args],capture_output=True,text=True,encoding='utf-8',shell=False)
 results=[]
 for line in p.stdout.splitlines():
  try:d=json.loads(line)
  except ValueError:continue
  if isinstance(d,dict):results.append(d)
 if not results:raise RuntimeError('GROUP_CHILD_NO_RESULT: 子命令未返回结构化结果')
 terminal={'complete','complete_with_skips','skipped_permission'}
 failed=next((d for d in results if d.get('status') not in terminal or (isinstance(d.get('archive'),dict) and d['archive'].get('status')!='complete')),None)
 if p.returncode or failed:raise RuntimeError((failed or results[-1]).get('error','GROUP_CHILD_INCOMPLETE: 子命令或归档未完成，保留原断点'))
 return results

def run_group(root,profile,name,day,period,start=None,end=None,category_id=None):
 now=datetime.now(clock.timezone())
 file=root/'state/analyses'/('group_'+now.strftime('%Y-%m-%d_%H%M%S_%f')+'.json')
 steps=[['collect',f,'--date',day]+(['--fee-interval-days',str(period)] if f.startswith('fee-') else []) for f in GROUPS[name]]
 for command in steps:
  if command[1] in ('ads-keywords','keywords-traffic'):command+=['--start',start,'--end',end]
  if command[1]=='category':command+=['--category-id',category_id]
 if name=='analytics':steps+=[['analyze']]
 result={'status':'running','group':name,'store_id':profile['store_id'],'run_date':day,'steps':[{'command':s,'status':'not_started'} for s in steps],'manifest':str(file)};write(file,result)
 for step in result['steps']:
  try:
   command=list(step['command'])
   step['status']='running';write(file,result)
   outputs=execute(root,profile['browser_id'],command);step['results']=outputs
   step['status']='skipped_permission' if any(o.get('status')=='skipped_permission' for o in outputs) else 'incomplete_data' if any(o.get('facts_complete') is False for o in outputs) else 'complete'
  except Exception as exc:
   from ziniao_environment import redact
   step.update(status='failed',error=redact(exc));result['status']='failed';write(file,result);return result
  write(file,result)
 result['status']='incomplete' if any(s['status']=='incomplete_data' for s in result['steps']) else 'complete_with_skips' if any(s['status']=='skipped_permission' for s in result['steps']) else 'complete'
 write(file,result);return result
