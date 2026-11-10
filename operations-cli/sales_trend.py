"""Seven daily UI exports, source-bound comparisons and an AI-readable local package."""
import runtime_clock as clock
from datetime import date,datetime,timedelta
from pathlib import Path
import json,shutil
import openpyxl
from storage import write

def dates(end,count):
 if not isinstance(count,int) or isinstance(count,bool) or count<=0:raise ValueError('销售天数必须是正整数')
 end=date.fromisoformat(end)
 if end>clock.now().date():raise ValueError('截止日期不能晚于今天')
 return [str(end-timedelta(days=i)) for i in range(count)]

def change(current,previous):
 if current is None or previous is None:return {'delta':None,'rate':None,'status':'未取得，不能比较'}
 delta=current-previous
 return {'delta':delta,'rate':delta/previous if previous>0 else None,'status':'前日为零' if previous==0 else '前日为负值，调整口径待核对' if previous<0 else '可比较'}

def analyze(batches,days):
 days=sorted(days);by_day={b['start']:b for b in batches};options={r['id']:r for b in batches for r in b['payload']}
 rows=[]
 for oid,identity in sorted(options.items()):
  daily=[]
  for day in days:
   b=by_day.get(day);r=next((r for r in b['payload'] if r['id']==oid),None) if b else None
   previous=daily[-1] if daily else {}
   item={'date':day,'units':r.get('units') if r else None,'revenue':r.get('revenue') if r else None,'status':'暂定' if b and b['provisional'] and r else '已取得' if r else '未取得'}
   item['adjustment_note']='平台原始负值，需核对取消或调整口径，未改成零' if any(item[k] is not None and item[k]<0 for k in ('units','revenue')) else None
   for key in ('units','revenue'):item[key+'_change']=change(item[key],previous.get(key))
   daily.append(item)
  coverage=sum(d['units'] is not None for d in daily)
  rows.append({'option_id':oid,'product_id':identity['pid'],'name':identity['name'],'daily':daily,'coverage_days':coverage,
   'units_total':sum(d['units'] for d in daily) if coverage==len(days) else None,
   'known_units':sum(d['units'] for d in daily if d['units'] is not None),
   'revenue_total':sum(d['revenue'] for d in daily) if all(d['revenue'] is not None for d in daily) else None,
   'first_last_units':change(daily[-1]['units'],daily[0]['units'])})
 products=[]
 for pid in sorted({r['product_id'] for r in rows}):
  children=[r for r in rows if r['product_id']==pid];daily=[]
  for i,day in enumerate(days):
   entry={'date':day,'covered_skus':sum(r['daily'][i]['units'] is not None for r in children),'expected_skus':len(children),
    'status':'暂定' if any(r['daily'][i]['status']=='暂定' for r in children) else '已取得'}
   for key in ('units','revenue'):
    values=[r['daily'][i][key] for r in children]
    entry[key]=sum(values) if all(v is not None for v in values) else None
    entry['known_'+key]=sum(v for v in values if v is not None)
    entry[key+'_change']=change(entry[key],daily[-1][key] if daily else None)
   if entry['units'] is None:entry['status']='未完整取得'
   daily.append(entry)
  products.append({'product_id':pid,'name':children[0]['name'],'sku_count':len(children),'daily':daily,
   'units_total':sum(d['units'] for d in daily) if all(d['units'] is not None for d in daily) else None,
   'revenue_total':sum(d['revenue'] for d in daily) if all(d['revenue'] is not None for d in daily) else None})
 return {'dates':days,'options':rows,'products':products,'definition':'按本轮单日报表的选项并集核对；未出现的SKU不补零，不能证明覆盖店铺所有在售SKU。商品销量为销售单位相加，不等于散件库存数量。暂定日不与完整日作确定性增长结论；前日为零或负值不计算百分比；原始负值保留并标记为需核对调整口径。无订单级证据，不合并去重访客/订单，不把增长归因为广告或调价。'}

def export_excel(path,result,sources):
 wb=openpyxl.Workbook();wb.remove(wb.active)
 def sheet(name,headers,rows):
  ws=wb.create_sheet(name);ws.append(headers)
  for row in rows:
   ws.append(row)
   for cell in ws[ws.max_row]:
    if isinstance(cell.value,str):cell.data_type='s'
  from openpyxl.styles import Font,PatternFill
  for cell in ws[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='315D9B')
  ws.freeze_panes='D2';ws.auto_filter.ref=ws.dimensions
  for col in ws.columns:ws.column_dimensions[col[0].column_letter].width=22
 for label,key in [('SKU每日对比','options'),('商品每日对比','products')]:
  headers=['商品ID','选项ID','商品名称','业务日期','销量','销售额₩','销量日增减','销量日增幅','销售额日增减','销售额日增幅','数据状态','调整提示']
  sheet(label,headers,[[r['product_id'],r.get('option_id'),r['name'],d['date'],d['units'],d['revenue'],d['units_change']['delta'],d['units_change']['rate'],d['revenue_change']['delta'],d['revenue_change']['rate'],d['status'],d.get('adjustment_note')] for r in result[key] for d in r['daily']])
  ws=wb[label]
  for row in ws.iter_rows(min_row=2):
   row[7].number_format='0.00%';row[9].number_format='0.00%'
 sheet('SKU期间趋势',['商品ID','选项ID','名称',*result['dates'],'期间合计销量','已知销量小计','覆盖天数','期间销售额₩'],[[r['product_id'],r['option_id'],r['name'],*[d['units'] for d in r['daily']],r['units_total'],r['known_units'],r['coverage_days'],r['revenue_total']] for r in result['options']])
 sheet('商品期间趋势',['商品ID','名称',*result['dates'],'期间合计销量','期间销售额₩'],[[r['product_id'],r['name'],*[d['units'] for d in r['daily']],r['units_total'],r['revenue_total']] for r in result['products']])
 sheet('来源与口径',['项目','内容'],[['口径',result['definition']],*[[s['date'],json.dumps(s,ensure_ascii=False)] for s in sources]])
 from openpyxl.chart import LineChart,Reference
 ws=wb['商品期间趋势']
 if result['dates'] and ws.max_row>1:
  chart=LineChart();chart.title='商品期间每日销量（前10个商品）';chart.y_axis.title='销售数量';chart.x_axis.title='业务日期';chart.display_blanks='gap'
  chart.add_data(Reference(ws,min_col=2,max_col=2+len(result['dates']),min_row=2,max_row=min(ws.max_row,11)),from_rows=True,titles_from_data=True)
  chart.set_categories(Reference(ws,min_col=3,max_col=2+len(result['dates']),min_row=1,max_row=1));ws.add_chart(chart,'M2')
 wb.save(path)

def run(root,profile,end,count,refresh=False):
 import ziniao_exports as z
 from report_pipeline import connect,final_gate
 from ziniao_preflight import check
 from storage import organize,sha
 days=dates(end,count);now=clock.now();day=str(now.date())
 checkpoint=root/'state/functions'/day/('sales-days_'+end+'_'+str(count)+'.json')
 for path in (root/'state/functions').rglob('*.json'):
  if path!=checkpoint and json.loads(path.read_text()).get('status') not in ('complete','skipped_permission'):raise ValueError('其他采集断点未完成：'+str(path))
 old=root/'state/collection_run.json'
 if old.exists() and json.loads(old.read_text()).get('status')!='complete':raise ValueError('旧核心采集尚未完成，请先恢复原入口')
 z.configure_runtime(root,profile);z.STATE=checkpoint
 steps=[{'kind':'sales_daily','start':d,'end':d,'date_picker':True,'provisional':d==day} for d in days]
 previous=json.loads(checkpoint.read_text()) if checkpoint.exists() else None
 if previous and (previous.get('store_id')!=profile['store_id'] or [s['start'] for s in previous['steps']]!=days):raise ValueError('期间断点身份或日期不符')
 if refresh and previous:
  if previous['status']!='complete':raise ValueError('未完成轮次不能refresh，先续跑')
  history=root/'state/collection_history'/('sales_days_'+now.strftime('%Y%m%d_%H%M%S_%f')+'.json');write(history,previous);previous=None
 run=previous or {'run_date':day,'status':'ready','index':0,'steps':steps,'results':[],'options':{'function':'sales-days'}}
 z.write(run)
 if run['status']!='complete':
  waits=0
  while True:
   check(root,profile) # Every individual daily export checks the current account/session.
   out=z.next_export(run)
   if out.get('status')=='imported':waits=0;continue
   if out.get('status')=='awaiting_download' and waits<2:waits+=1;continue
   if out.get('status')!='complete':raise RuntimeError('期间导出未完成，续跑同一命令：'+json.dumps(out,ensure_ascii=False))
   break
 final_gate(run['results'],run['steps'],root,profile['store_id'])
 folder=root/'state/analyses'/('sales_days_'+now.strftime('%Y-%m-%d_%H%M%S_%f'));folder.mkdir(parents=True)
 batches=[];sources=[]
 with connect(root) as db:
  for item in run['results']:
   b=dict(db.execute('SELECT * FROM batches WHERE id=? AND store=?',(item['batch'],profile['store_id'])).fetchone());b['payload']=json.loads(b['payload']);batches.append(b)
   captured=datetime.fromisoformat(b['captured']).astimezone(clock.timezone())
   name='按商品销售_业务日期'+b['start']+'_下载日期'+captured.strftime('%Y-%m-%d_%H%M%S')+'.xlsx';dest=folder/name;shutil.copy2(root/b['file'],dest)
   sources.append({'date':b['start'],'downloaded_at':b['captured'],'file':str(dest.resolve()),'sha256':sha(dest),'batch':b['id'],'provisional':bool(b['provisional'])})
 result=analyze(batches,days);result.update(store_id=profile['store_id'],captured_at=now.isoformat(),sources=sources)
 write(folder/'AI分析数据.json',result);export_excel(folder/'期间销售增长分析.xlsx',result,sources)
 (folder/'AI分析说明.md').write_text('# 期间销售分析\n\n请结合AI分析数据.json和Excel，逐商品解释每日销量/销售额走势、显著变化及待核对事项。\n\n'+result['definition']+'\n\n所有暂定和缺失必须保留；只做数据解释，不编造广告效果、成本、利润或因果。')
 archive=organize(root,run_info={'date':day,'id':now.strftime('%H%M%S_%f')+'_sales_days','function':'sales-days'})
 return {'status':'complete','dates':sorted(days),'store_id':profile['store_id'],'source_files':sources,'excel':str((folder/'期间销售增长分析.xlsx').resolve()),'ai_data':str((folder/'AI分析数据.json').resolve()),'sku_count':len(result['options']),'product_count':len(result['products']),'archive':archive,'definition':result['definition']}
