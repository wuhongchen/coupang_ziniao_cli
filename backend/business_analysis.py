"""Build a local operating report from the selected store's archived exports."""
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
import hashlib
import json
from datetime import date

import openpyxl

from report_pipeline import state


def number(value):
    try:
        return float(str(value).replace(',', ''))
    except (ValueError, TypeError):
        return None


def total(values):
    known = [n for v in values if (n := number(v)) is not None]
    return sum(known) if known else None


def fmt(value):
    return '未提供' if value is None else f'{value:,.0f}'


def verified(root, file, digest):
    path = (root / file).resolve()
    if not path.is_relative_to((root / 'state').resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError('分析来源未通过归属或摘要校验：' + path.name)
    return path


def ads_section(sheet):
    rows = iter(sheet.values)
    header = next(rows, ())
    aliases = [('캠페인 ID', '广告活动ID'), ('캠페인명', '广告活动名'), ('클릭수', '点击次数'),
               ('광고비', '广告费'), ('총 전환매출액(14일)', '总转化销售额（14天）')]
    indices = [next((header.index(h) for h in names if h in header), None) for names in aliases]
    if None in indices:
        return ['表头未匹配，未汇总广告金额。']
    groups = defaultdict(lambda: [[], [], []])
    for row in rows:
        campaign, name, *metrics = (row[i] for i in indices)
        if campaign:
            for column, v in zip(groups[name], metrics):
                column.append(v)
    if not groups:
        return ['原表无活动明细。']
    lines = []
    combined = [total(v for columns in groups.values() for v in columns[i]) for i in range(3)]
    for label, metrics in [('合计', combined), *[(name, list(map(total, cols))) for name, cols in groups.items()]]:
        clicks, spend, revenue = metrics
        ratio = f'{revenue/spend:.2f} 倍' if spend and revenue is not None else '无法计算'
        lines.append(f'- {label}：点击 {fmt(clicks)}，广告费 ₩{fmt(spend)}，14 天归因销售额 ₩{fmt(revenue)}，ROAS {ratio}。')
    if combined[1] and combined[2] is not None and combined[2] < combined[1]:
        lines.append("按当前归因结果，广告花费高于归因销售额。请结合窗口是否结束及商品毛利复核投放。")
    return lines


def fee_lines(book):
    lines = []
    for sheet in book:
        rows = iter(sheet.values)
        for header in rows:
            if '最终费用' in header:
                data = next(rows, ())
                idx = header.index('最终费用')
                amount = number(data[idx]) if idx < len(data) else None
                lines.append(f'{sheet.title} ₩{fmt(amount)}')
                break
            if '销售手续费' in header and '结算金额' in header and '订单ID' in header:
                orders = [r for r in rows if r[header.index('订单ID')]]
                for field in ('销售手续费', '销售手续费（含增值税）', '结算金额'):
                    if field in header:
                        lines.append(f'原表「{field}」列合计 ₩{fmt(total(r[header.index(field)] for r in orders))}')
                break
            for field in ('赔偿金额', '优惠后金额(A-B-C)'):
                if field in header:
                    amount = total(r[header.index(field)] for r in rows)
                    lines.append(f'{field} ₩{fmt(amount)}' if amount is not None else f'{field}未见数值明细')
                    break
            else:
                continue
            break
    return '；'.join(lines) or '原表已归档，未识别可汇总金额列'


def render_report(root, profile):
    store = profile['store_id']
    batches = state(root, store)
    latest = {}
    for b in sorted(batches, key=lambda x: (x['end'], x['captured'])):
        latest[b['kind']] = b
    lines = [f"# {profile['store_name']} 经营分析", '', '金额单位为韩元。各节使用本店对应报表的最新已归档周期。']
    def section(title, content):
        lines.extend(['', '## ' + title, '', *(content or ['尚无已核验数据。'])])

    def core(kind):
        batch = latest.get(kind)
        if batch:
            verified(root, batch['file'], batch['digest'])
        return batch

    sales = core('sales_window') or core('sales_daily')
    detail = []
    if sales:
        rows = sales['payload']
        detail.append(f"{sales['start']} 至 {sales['end']}，销售额 ₩{fmt(total(r.get('revenue') for r in rows))}，购买 {fmt(total(r.get('units') for r in rows))} 件。")
        if sales['provisional']:
            detail.append('该销售报表仍为暂定数据。')
        products = defaultdict(list)
        for row in rows:
            products[row['pid']].append(row)
        ranked = sorted(products.values(), key=lambda p: total(r.get('revenue') for r in p) or 0, reverse=True)
        for product in ranked[:5]:
            detail.append(f"- {product[0]['name']}：销售额 ₩{fmt(total(r.get('revenue') for r in product))}，购买 {fmt(total(r.get('units') for r in product))} 件。")
        daily=[]
        for b in sorted(batches, key=lambda x:x['start']):
            if b['kind']=='sales_daily' and sales['start']<=b['start']<=sales['end']:
                verified(root,b['file'],b['digest'])
                detail.append(f"- {b['start']} 日报：₩{fmt(total(r.get('revenue') for r in b['payload']))}{'（暂定）' if b['provisional'] else ''}。")
                daily.append((b['start'],total(r.get('revenue') for r in b['payload']),total(r.get('units') for r in b['payload'])))
        days=(date.fromisoformat(sales['end'])-date.fromisoformat(sales['start'])).days+1
        if sales['kind']=='sales_window' and len(daily)==days and all(v is not None for row in daily for v in row[1:]):
            amount=sum(row[1] for row in daily)
            units=sum(row[2] for row in daily)
            matches=amount==total(r.get('revenue') for r in rows) and units==total(r.get('units') for r in rows)
            detail.append(f'{days} 份日报合计 ₩{fmt(amount)}、{fmt(units)} 件。与区间表核对'+('一致。' if matches else '有差异，请复核报表更新时间。'))
            peak=max(daily,key=lambda row:row[1])
            if amount>0:detail.append(f'销售额最高日为 {peak[0]}，占同期销售额 {peak[1]/amount:.1%}。')
    section('销售与商品', detail)

    inv = core('inventory')
    detail = []
    if inv:
        rows = inv['payload']
        detail.append(f"{inv['end']} 快照共 {len(rows)} 条，可售 {fmt(total(r.get('stock') for r in rows))} 件，待入库 {fmt(total(r.get('inbound') for r in rows))} 件。")
        for r in sorted((r for r in rows if (r.get('suggested') or 0)>0),key=lambda r:-r['suggested'])[:5]:
            detail.append(f"- {r['name']} {r['variant']}：可售 {fmt(r['stock'])}，在途 {fmt(r['inbound'])}，平台建议补 {fmt(r['suggested'])} 件。")
    section('库存与补货', detail)

    detail = []
    for kind, label, field in [('returns','退货','处理状态'),('movement','库存变动','注册商品ID')]:
        b = core(kind)
        if not b: continue
        matrix = b['payload']['matrices'][0]
        if kind=='returns' and len(matrix)>6 and field in matrix[5]:
            idx=matrix[5].index(field)
            counts=Counter(r[idx] for r in matrix[7:] if r[5] and r[idx])
            detail.append(f"- {label}（{b['end']} 导出）："+'；'.join(f'{k} {v} 条' for k,v in counts.items()))
        elif kind=='movement':
            detail.append(f"- {label}（{b['end']} 导出）：{sum(bool(r[2]) for r in matrix[17:] if len(r)>2)} 条库存流水汇总明细。")
    section('退货与库存变动',detail)

    functions = {}
    for p in sorted((root/'state/functions').rglob('*.json')):
        run=json.loads(p.read_text())
        if run.get('store_id')==store and run.get('function'):
            functions[run['function']]=run
    for kind,title in [('ads-keywords','广告效果'),('ads-bill-previous','上月广告账单'),('ads-bill-current','本月广告账单')]:
        detail=[]
        run=functions.get(kind,{})
        for v in run.get('steps',{}).values():
            if run.get('status')!='complete' or v.get('status')!='complete' or not v.get('file'):continue
            with closing(openpyxl.load_workbook(verified(root,v['file'],v['sha256']),read_only=True,data_only=True)) as book:
                if kind=='ads-keywords':
                    params=run['parameters'];detail=[f"{params['start']} 至 {params['end']}。归因可能回补。",*ads_section(book.active)]
                else:
                    rows=iter(book.active.values);header=next(rows,())
                    amount=next((header.index(h) for h in ('청구금액(+부가가치세)','账单金额(含增值税)') if h in header),None)
                    if amount is not None:
                        bill=total(r[amount] for r in rows if r[0] in ('전체','合计','全部'))
                        detail.append(f"{run['run_date']} 采集，含税应付 ₩{fmt(bill)}（仅汇总配送类型合计行）。")
                    else:detail.append('未识别含税账单金额列。')
        section(title,detail)

    detail=[]
    for kind,run in sorted(functions.items()):
        if not kind.startswith('fee-') or run.get('status')!='complete':continue
        for label,v in run['steps'].items():
            if v.get('status')!='complete' or not v.get('file'):continue
            batch=v.get('settlement_batch')
            period=f'{batch[2]} 至 {batch[3]}，{batch[0]} 结算 {batch[1]}%' if batch else f"{run['run_date']} 采集，批次未标注"
            with closing(openpyxl.load_workbook(verified(root,v['file'],v['sha256']),read_only=True,data_only=True)) as book:
                detail.append(f'- {label}（{period}）：{fee_lines(book)}。')
    section('结算费用',detail)

    detail=[]
    for kind in ('products','reviews'):
        run=functions.get(kind,{})
        for v in run.get('steps',{}).values():
            if run.get('status')!='complete' or v.get('status')!='complete':continue
            if kind=='reviews':
                reviews=json.loads(verified(root,v['file'],v['sha256']).read_text())['rows']
                scores=Counter(r['评分'] for r in reviews)
                detail.append(f'- 当前筛选可见评价 {len(reviews)} 条。'+'；'.join(f'{k} 星 {n} 条' for k,n in sorted(scores.items())))
            else:
                for src in v.get('files',[]):
                    data=json.loads(verified(root,src['file'],src['sha256']).read_text())
                    detail.append(f"- 商品目录页面快照有 {len(data['rows'])} 条商品行。")
    for p in sorted((root/'state/product_images').glob('*/*/manifest.json'),reverse=True):
        image=json.loads(p.read_text())
        if image.get('store_id')==store and image.get('status')=='complete':
            detail.append(f"- 最新完整头图归档覆盖 {len(image['rows'])} 件商品、{len(image['option_images'])} 个选项映射。")
            break
    section('商品资料与评价',detail)
    gaps=[f"- {name}：{run['error']}" for name,run in functions.items() if run.get('status')=='skipped_permission' and run.get('error')]
    section('分析范围',gaps+['销售、广告归因和结算使用各自周期。采购、跨境物流成本与完整回款尚需另行核对。'])
    return '\n'.join(lines)+'\n'
