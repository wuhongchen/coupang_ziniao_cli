"""Verified exported files -> versioned local facts. No platform API calls."""
from pathlib import Path
from datetime import datetime, date, timedelta, time
from zoneinfo import ZoneInfo
import hashlib
import json
import shutil
import sqlite3
from contextlib import contextmanager
import openpyxl
from report_paths import ROOT, number
from export_profiles import store_identity


@contextmanager
def connect(root=ROOT):
    state=root/'state';state.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(state/'report_pipeline.sqlite3',timeout=20)
    db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS batches (id INTEGER PRIMARY KEY, store TEXT, kind TEXT, start TEXT, end TEXT, digest TEXT, captured TEXT, provisional INTEGER, file TEXT, payload TEXT, UNIQUE(store,kind,start,end,digest))')
    try:
        with db:
            yield db
    finally:
        db.close()

def accept_export(path, kind, start, end, provisional=False, root=ROOT, store=None, fresh_download=False):
    store=store_identity(root,store)
    path=Path(path);date.fromisoformat(start);date.fromisoformat(end)
    if start>end:raise ValueError('日期范围无效')
    # Wing explicitly reports yesterday's traffic/conversion as available after 12:30 KST.
    # The warning can disappear during SPA rendering; do not infer finality from its absence.
    korea_now=datetime.now(ZoneInfo('Asia/Seoul'))
    if kind in ('sales_daily','sales_window') and (end>=str(korea_now.date()) or (end==str(korea_now.date()-timedelta(days=1)) and korea_now.time()<time(12,30))):
        provisional=True
    original_digest=hashlib.sha256(path.read_bytes()).hexdigest()
    wb=openpyxl.load_workbook(path,read_only=True,data_only=True)
    matrices=[list(s.values) for s in wb];wb.close()
    rows=matrices[0];head=rows[0] if rows else []
    payload=[]
    if kind in ('sales_daily','sales_window'):
        if kind=='sales_daily' and start!=end:raise ValueError('每日事实必须是单日导出')
        required=['选项ID','注册商品ID','购买数量','销售额(₩)']
        if not all(h in head for h in required):raise ValueError('不是支持的按商品销售导出表')
        indexes={h:head.index(h) for h in head if h}
        seen=set()
        for r in rows[1:]:
            if not r or r[0] in (None,'','TOTAL'):continue
            ident=str(r[indexes['选项ID']])
            if not ident.isdigit() or ident in seen:raise ValueError('选项ID无效或重复')
            seen.add(ident)
            obj={'id':ident,'pid':str(r[indexes['注册商品ID']]),'name':r[indexes.get('商品名称',1)]}
            for key,h in [('units','购买数量'),('revenue','销售额(₩)'),('orders','订单数'),('visitors','商品页面访问人数'),('pv','商品页面浏览次数'),('carts','加购数量')]:
                obj[key]=number(r[indexes[h]]) if h in indexes else None
            if '购买转化率' in indexes:
                raw=r[indexes['购买转化率']]
                obj['conversion']=number(str(raw).rstrip('%'))
                if obj['conversion'] is not None and str(raw).endswith('%'):obj['conversion']/=100
            payload.append(obj)
    elif kind=='inventory':
        if len(head)<31 or head[2]!='属性ID' or '可销售库存' not in str(head[7]):raise ValueError('不是支持的库存现状表')
        for r in rows[2:]:
            if len(r)>=31 and r[2]:payload.append({'id':str(r[2]),'pid':str(r[1]),'sku':str(r[3]),'name':r[4],'variant':r[5],
                'grade':'新品' if r[6]=='NEW' else '退货-中等','stock':number(r[7]),'inbound':number(r[8]),'units7':number(r[12]),'units30':number(r[13]),'doc':number(r[14]),'suggested':number(r[18]),'replenish_date':r[19],'storage_fee':number(r[21]),'age':[number(x) for x in r[22:28]]})
    elif kind in ('returns','movement'):
        if kind=='returns' and (len(rows)<6 or '退货编号' not in rows[5]):raise ValueError('不是退货明细')
        if kind=='movement' and len(matrices)<2:raise ValueError('不是库存变动报表')
        payload={'matrices':matrices}
    else:raise ValueError('未支持的报表类型')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    if digest!=original_digest:raise ValueError('导入时源文件发生变化，未入库；请等待文件稳定后续跑')
    archive=root/'state/reports/自动采集'/hashlib.sha256(store.encode()).hexdigest()[:16];archive.mkdir(parents=True,exist_ok=True)
    target=archive/f'{kind}_{start}_{end}_{digest[:16]}.xlsx'
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest()!=digest:raise ValueError('已有归档损坏，停止导入，不覆盖原文件')
    else:
        temporary=target.with_suffix('.xlsx.partial');shutil.copy2(path,temporary)
        if hashlib.sha256(temporary.read_bytes()).hexdigest()!=digest:raise ValueError('导入复制期间源文件变化或归档校验失败，未入库')
        temporary.replace(target)
    with connect(root) as db:
        conflict='DO UPDATE SET captured=excluded.captured, provisional=excluded.provisional, payload=excluded.payload' if fresh_download else 'DO NOTHING'
        db.execute('INSERT INTO batches (store,kind,start,end,digest,captured,provisional,file,payload) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(store,kind,start,end,digest) '+conflict,
            (store,kind,start,end,digest,datetime.now().astimezone().isoformat(),int(provisional),str(target.relative_to(root)),json.dumps(payload,ensure_ascii=False,default=str)))
        row=db.execute('SELECT id,provisional FROM batches WHERE store=? AND kind=? AND start=? AND end=? AND digest=?',(store,kind,start,end,digest)).fetchone()
    count=len(payload) if isinstance(payload,list) else sum(bool(len(r)>5 and r[5]) for r in rows[7:]) if kind=='returns' else sum(bool(len(r)>2 and r[2]) for r in rows[17:])
    return {'batch':row['id'],'kind':kind,'start':start,'end':end,'records':count,'provisional':bool(row['provisional']),'sha256':digest}

def state(root=ROOT,store=None):
    if not (root/'state/report_pipeline.sqlite3').exists():return []
    store=store_identity(root,store)
    with connect(root) as db:
        batches=[dict(r) for r in db.execute('SELECT * FROM batches WHERE store=? ORDER BY captured,id',(store,))]
    latest={}
    for b in batches:
        b['payload']=json.loads(b['payload']);latest[(b['kind'],b['start'],b['end'])]=b
    return list(latest.values())

def final_gate(results,steps,root=ROOT,store=None):
    store=store_identity(root,store)
    if len(results)!=len(steps):raise ValueError('尚有导出步骤未完成')
    with connect(root) as db:
        for result,step in zip(results,steps):
            row=db.execute('SELECT kind,start,end,file,digest FROM batches WHERE id=? AND store=?',(result['batch'],store)).fetchone()
            if not row or any(row[k]!=step[k] for k in ('kind','start','end')):raise ValueError('批次不属于本店铺或当前日期步骤')
            path=root/row['file']
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=row['digest']:raise ValueError('原始导出文件缺失或已变化')

def export_analysis(root,store,name):
    batches=state(root,store)
    latest=lambda kind:max((b for b in batches if b['kind']==kind),key=lambda b:(b['end'],b['captured']),default=None)
    inventory=latest('inventory');sales=latest('sales_window')
    rows=inventory['payload'] if inventory else []
    sales_rows=sales['payload'] if sales else []
    def total(items,key):
        known=[r[key] for r in items if r.get(key) is not None]
        return sum(known) if known else None
    return {'store':name,'store_id':store,'summary':{'stock':total(rows,'stock'),'revenue':total(sales_rows,'revenue'),'units':total(sales_rows,'units')},
        'inventory_date':inventory['end'] if inventory else None,'sales_period':[sales['start'],sales['end']] if sales else None,
        'insights':[{'id':r['id'],'name':r['name'],'suggested':r['suggested'],'stock':r['stock'],'basis':'平台库存报表建议，需复核交期与成本'} for r in rows if r.get('suggested') and r['suggested']>0],
        'batches':[{k:v for k,v in b.items() if k!='payload'} for b in batches],
        'limitations':['仅含已验证导出范围；缺失不补零','广告、类目、关键词不属于固定步骤','未核对采购批次与结算，不计算实际利润']}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--file',required=True);p.add_argument('--kind',required=True);p.add_argument('--start',required=True);p.add_argument('--end',required=True);p.add_argument('--provisional',action='store_true');p.add_argument('--store');p.add_argument('--output-root',type=Path,default=ROOT)
    a=p.parse_args();print(json.dumps(accept_export(a.file,a.kind,a.start,a.end,a.provisional,root=a.output_root,store=a.store),ensure_ascii=False))
