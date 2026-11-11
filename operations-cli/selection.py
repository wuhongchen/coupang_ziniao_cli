"""Resolve user selections from reports bound to the current store."""
import hashlib
import json
import sys

import openpyxl


def candidates(root, store_id, kind):
    records = {}
    seen_files = set()
    state = (root / 'state').resolve()
    for checkpoint in sorted((state / 'functions').rglob('fee-*.json')):
        run = json.loads(checkpoint.read_text())
        if run.get('store_id') != store_id or run.get('status') != 'complete':
            continue
        for entry in run.get('steps', {}).values():
            if entry.get('status') != 'complete' or not entry.get('file'):
                continue
            if kind == 'category' and entry.get('fee_kind') != '销售手续费报告':
                continue
            path = (state / entry['file']).resolve()
            if not path.is_relative_to(state) or not path.is_file():
                raise ValueError('候选项来源文件缺失或不在本店归档内')
            if path in seen_files:
                continue
            seen_files.add(path)
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry.get('sha256'):
                raise ValueError('候选项来源文件校验失败：' + path.name)
            if kind == 'settlement':
                batch = entry.get('settlement_batch')
                if batch and len(batch) == 4:
                    pay, ratio, first, last = batch
                    records[tuple(batch)] = {'value': batch, 'label': f'销售确认 {first} 至 {last}，结算 {pay}，支付 {ratio}%'}
                continue
            book = openpyxl.load_workbook(path, read_only=True, data_only=True)
            try:
                for sheet in book:
                    rows = iter(sheet.values)
                    header = next((r for r in rows if '品类ID' in r and '品类名称' in r), None)
                    if header is None:
                        continue
                    id_col, name_col = header.index('品类ID'), header.index('品类名称')
                    for row in rows:
                        code, name = str(row[id_col]), row[name_col]
                        if code.isdigit() and name:
                            records[code] = {'value': code, 'label': f'{code} {name}'}
            finally:
                book.close()
    return [records[key] for key in sorted(records)]


def choose(items, query):
    words = query.casefold().split()
    matches = [item for item in items if all(word in item['label'].casefold() for word in words)]
    if len(matches) == 1:
        return matches[0]['value']
    if not matches:
        raise ValueError('已有报表没有匹配项。请运行 choices 查看候选项，或显式提供参数。')
    menu = '\n'.join(f"{i}. {item['label']}" for i, item in enumerate(matches, 1))
    if not sys.stdin.isatty():
        raise ValueError('匹配到多项，请用 --match 加上日期、比例或类目名称缩小范围。\n' + menu)
    print(menu, file=sys.stderr)
    print('请选择编号：', end='', file=sys.stderr, flush=True)
    index = sys.stdin.readline().strip()
    if not index.isdigit() or not 1 <= int(index) <= len(matches):
        raise ValueError('未选择有效编号')
    return matches[int(index) - 1]['value']
