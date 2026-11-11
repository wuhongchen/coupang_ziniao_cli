"""Aggregate daily exports before joining them by date and advertised option."""
import csv
import json
import tempfile
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

import openpyxl

from business_analysis import verified
from operation_progress import progress
from report_paths import number
from report_pipeline import state

SALES = ('购买件数', '销售额₩')
ADS = ('广告展示', '广告点击', '广告费₩', '14天归因销售额₩')
FIELDS = SALES + ADS
HEADERS = (
    ('日期', '날짜'), ('广告投放属性ID', '광고집행 옵션ID'),
    ('展示次数', '노출수'), ('点击次数', '클릭수'), ('广告费', '광고비'),
    ('总转化销售额（14天）', '총 전환매출액(14일)'),
)


def add(record, fields, values):
    for field, value in zip(fields, values):
        n = number(value)
        if n is not None:
            record[field] = record.get(field, 0) + n


def day_text(value):
    if isinstance(value, date):
        return value.strftime('%Y-%m-%d')
    text = str(value).removesuffix('.0')
    if len(text) == 8 and text.isdigit():
        text = f'{text[:4]}-{text[4:6]}-{text[6:]}'
    return date.fromisoformat(text).isoformat()


def ad_sources(root, store, first, last):
    # Assign each business day to one complete export, even if that day had no rows.
    selected = {}
    for path in sorted((root/'state/functions').rglob('ads-keywords*.json')):
        run = json.loads(path.read_text())
        if run.get('store_id') != store or run.get('status') != 'complete':
            continue
        entry = run.get('steps', {}).get('广告逐日关键词', {})
        if entry.get('status') != 'complete':
            continue
        params = run.get('parameters', {})
        period = entry.get('export_scope', {}).get('period') or [params.get('start'), params.get('end')]
        if not all(period):
            raise ValueError('广告报表缺少日期范围：' + path.name)
        start, end = period
        cursor = date.fromisoformat(max(start, first) if first else start)
        stop = date.fromisoformat(min(end, last) if last else end)
        stamp = datetime.fromisoformat(entry['captured_at']).timestamp()
        while cursor <= stop:
            day = cursor.isoformat()
            if day not in selected or stamp > selected[day][0]:
                selected[day] = (stamp, entry['file'], entry['sha256'])
            cursor += timedelta(days=1)
    return selected


def stats(root, profile, first=None, last=None):
    store = profile['store_id']
    merged = {}
    sales_days = set()
    provisional = []
    sources = []
    progress('正在合并已校验的逐日销售与广告原表')
    for batch in state(root, store):
        day = batch['start']
        if batch['kind'] != 'sales_daily' or (first and day < first) or (last and day > last):
            continue
        path = verified(root, batch['file'], batch['digest'])
        sources.append(str(path))
        sales_days.add(day)
        if batch['provisional']:
            provisional.append(day)
        for row in batch['payload']:
            key = (day, str(row['id']))
            add(merged.setdefault(key, {}), SALES, [row.get('units'), row.get('revenue')])

    ads = ad_sources(root, store, first, last)
    for file, digest in sorted({(v[1], v[2]) for v in ads.values()}):
        path = verified(root, file, digest)
        sources.append(str(path))
        with closing(openpyxl.load_workbook(path, read_only=True, data_only=True)) as book:
            rows = iter(book.active.values)
            header = next(rows, ())
            indices = [next((header.index(h) for h in names if h in header), None) for names in HEADERS]
            if None in indices:
                raise ValueError('广告报表字段未匹配：' + path.name)
            for row in rows:
                raw_day, option, *metrics = (row[i] for i in indices)
                if raw_day is None and all(v is None for v in metrics):
                    continue
                day = day_text(raw_day)
                if day not in ads or ads[day][1:] != (file, digest):
                    continue
                key = (day, str(option).removesuffix('.0') if option is not None else '未标注广告选项')
                add(merged.setdefault(key, {}), ADS, metrics)

    days = sorted(sales_days | ads.keys())
    totals = {}
    by_day = {day: {} for day in days}
    for (day, _), row in merged.items():
        for bucket in (totals, by_day[day]):
            add(bucket, row.keys(), row.values())
    def roas(row):
        if row.get('广告费₩') and '14天归因销售额₩' in row:
            return round(row['14天归因销售额₩']/row['广告费₩'], 4)
        return None

    parent = root/'state/analyses'
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as tmp:
        folder = Path(tmp)/'results'
        folder.mkdir()
        for filename, keys, data in [
            ('按日统计.csv', ['日期'], [((day,), by_day[day]) for day in days]),
            ('按日与选项.csv', ['日期', '选项ID'], sorted(merged.items())),
        ]:
            with (folder/filename).open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow([*keys, *FIELDS, 'ROAS'])
                for key, row in data:
                    writer.writerow([*key, *(row.get(f) for f in FIELDS), roas(row)])
        summary = {'store_id': store, 'requested_period': [first,last], 'sales_dates': sorted(sales_days),
                   'provisional_sales_dates': sorted(provisional), 'ads_dates': sorted(ads),
                   'totals': {f: totals.get(f) for f in FIELDS}, 'ROAS': roas(totals),
                   'option_day_rows': len(merged), 'sources': sources,
                   'definition': '仅汇总已知值；缺失留空。广告按投放选项归集，14天归因销售额与店铺销售额分别统计。'}
        (folder/'统计摘要.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        dest = parent/('stats_'+Path(tmp).name)
        folder.rename(dest)
    return {'status':'complete' if days else 'incomplete', 'directory':str(dest), **summary}
