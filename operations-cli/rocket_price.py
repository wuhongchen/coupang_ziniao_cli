"""查询火箭选项价格、关闭自动调价或设置销售价格。"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import ziniao_exports as browser
from export_profiles import browser_lease, load_profile
from operation_progress import progress

URL = 'https://wing.coupang.com/tenants/seller-price-management/'


def auto_result(action, before, query, disable, apply, expected):
    if action == 'auto-status':
        return {'status': 'observed', 'before': before}
    if before['sale'] != expected:
        raise ValueError(f"当前售价 {before['sale']} 与预期 {expected} 不符")
    if not before['auto_enabled']:
        return {'status': 'already_disabled', 'before': before}
    if not apply:
        return {'status': 'preview', 'before': before}
    disable()
    after = query()
    verified = after['auto_enabled'] is False and after['sale'] == expected
    return {'status': 'verified' if verified else 'unverified', 'before': before, 'after': after}


def run(args, profile):
    if not re.fullmatch('[0-9]+', args.option_id):
        raise ValueError('选项 ID 必须为数字')
    if args.action in ('set','lower') and (args.expect is None or args.expect <= 0):
        raise ValueError('请用 --expect 指定当前售价')
    if args.action in ('auto-status','auto-off') and (args.minimum is not None or args.maximum is not None):
        raise ValueError('仅改价操作接受价格区间')
    if args.action == 'auto-off' and not (args.expect and args.expect > 0 and args.to is None):
        raise ValueError('关闭自动调价需要 --expect 当前售价')
    if args.action == 'auto-status' and (args.apply or args.expect is not None or args.to is not None):
        raise ValueError('auto-status 仅查询，无需价格或 --apply')
    browser.configure_runtime(args.output_root, profile)
    prefix = """
      if(location.origin+location.pathname!==url || !document.title.includes(title))
        throw Error('店铺或价格页面不匹配');
      const rows=[...document.querySelectorAll('tr[data-vendor-item-id]')];
      const row=rows.find(r=>r.dataset.vendorItemId===id);
      const visible=e=>e.getBoundingClientRect().width>0;
      const one=(selector,label,root=document)=>{
        const found=[...root.querySelectorAll(selector)].filter(e=>visible(e)&&e.innerText.trim()===label);
        if(found.length!==1 || found[0].disabled)throw Error('控件不唯一或不可用: '+label);
        return found[0];
      };
      const input=(e,v)=>{e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}))};
      const price=()=>row?.querySelector('.inline-price-input input')?.value;
      const checked=()=>rows.filter(r=>r.querySelector('td:first-child input')?.checked);
    """
    def ev(script):
        params = json.dumps([URL, profile['expected_title'], args.option_id])
        return browser.evaluate('(([url,title,id])=>{'+prefix+script+'})('+params+')')

    def wait(script, timeout=20):
        deadline = time.monotonic()+timeout
        while time.monotonic()<deadline:
            result=ev(script)
            if result:return result
            time.sleep(.4)
        raise RuntimeError('价格管理页未出现预期记录或控件')

    auto_controls = """
        const toggle=row?.querySelector('[data-wuic-props*="name:toggle"] input[type=checkbox]');
        const panel=toggle?.closest('td');
    """
    target_check = """rows.length===1 && row && row.innerText.includes('韩国本地火箭仓') &&
        new URLSearchParams(location.search).get('searchInputValue')===id"""
    def query():
        browser.call('navigate', {'url':URL+'?itemWinnerStatus=WIN_NOT_SUPPRESSED'})
        wait("return !!document.querySelector('.advanced-search-input input')")
        # Switch the status filter through the page if the default hides winners.
        ev("""const all=[...document.querySelectorAll('button')].find(e=>visible(e)&&e.innerText==='查看所有商品');
              if(all)all.click();return true""")
        wait("return !!document.querySelector('.advanced-search-input .btn-search')")
        ev("""const e=document.querySelector('.advanced-search-input input');input(e,id);
              document.querySelector('.advanced-search-input .btn-search').click();return true""")
        read = auto_controls + r"""
                if(!toggle)return null;
                const range=panel.innerText.match(/₩([\d,]+)\s*[~～]\s*₩([\d,]+)/);
                if(!range && toggle.checked)return null;
                return {name:row.cells[1].innerText,sale:price(),final:row.querySelector('.price-detail-text')?.innerText,
                        auto_enabled:toggle.checked,
                        minimum:range?Number(range[1].replaceAll(',','')):null,
                        maximum:range?Number(range[2].replaceAll(',','')):null};
            """
        data=wait("if(!("+target_check+")||!price())return null;"+read)
        if not re.fullmatch(r'₩?[\d,]+',data['sale']):raise ValueError('无法识别韩元售价')
        data['sale']=int(data['sale'].replace('₩','').replace(',',''))
        data['read_at']=datetime.now().astimezone().isoformat(timespec='seconds')
        return data

    before=query()
    if args.action in ('auto-status','auto-off'):
        def disable():
            guard = "if(!("+target_check+"))throw Error('选项变化');"+auto_controls
            guard += f"if(Number(price().replace(/[^0-9]/g,''))!=={args.expect})throw Error('售价变化');"
            ev(guard+"if(!toggle?.checked||toggle.disabled)throw Error('开关状态变化');toggle.click();return true")
            wait("return [...document.querySelectorAll('button')].some(b=>visible(b)&&b.innerText.trim()==='不使用自动调价')")
            ev(guard+"one('button','不使用自动调价').click();return true")
            # Wait for Wing to acknowledge the change before navigating away.
            try:
                wait("return document.body.innerText.includes('更改已应用')", timeout=8)
            except RuntimeError:
                progress('未观察到成功提示，正在重新查询实际状态')
        return {'option_id': args.option_id, 'store_id': profile['store_id'],
                **auto_result(args.action, before, query, disable, args.apply, args.expect)}
    target = price_target(before, args.to, args.minimum, args.maximum, args.action)
    result={'option_id':args.option_id,'before':before,'target':target}
    if matches_target(before, target):return dict(result,status='already_at_target')
    if before['sale']!=args.expect:raise ValueError(f'当前售价与预期不符：{before}')
    if not args.apply:return dict(result,status='preview')

    # Guard each write against a changed option, pricing mode or base price.
    guard = "if(!("+target_check+"))throw Error('选项变化');"+auto_controls
    guard += f"if(toggle?.checked!=={json.dumps(before['auto_enabled'])} || Number(price().replace(/[^0-9]/g,''))!=={args.expect})throw Error('调价模式或售价变化');"
    ev(guard+"""if(checked().length)throw Error('商品选择异常');
          row.querySelector('td:first-child input').click();return true""")
    selected=guard+"if(checked().length!==1||checked()[0]!==row)throw Error('选项变化');"
    ev(selected+"one('button','批量修改所选商品').click();return true")
    label='批量编辑自动调价' if before['auto_enabled'] else '变更价格'
    ev(selected+"const label="+json.dumps(label)+""";
          const labels=[...document.querySelectorAll('span,div,a,button,li')].filter(
              e=>!e.children.length&&visible(e)&&e.innerText.trim()===label);
          if(labels.length!==1)throw Error('找不到唯一的价格编辑入口');
          labels[0].click();return true""")
    if before['auto_enabled']:
        edit_range(ev, wait, selected, before, target)
    else:
        edit_sale(ev, selected, args.expect, args.to)
    time.sleep(2)
    notice=ev("return document.body.innerText.split('对此页进行评分').pop().trim().slice(0,400)")
    if notice:progress(notice)
    after=query()
    status='verified' if matches_target(after,target) else 'unverified'
    if status=='unverified' and '프로모션을 종료' in notice:
        status='blocked_promotion'
    return dict(result,after=after,status=status,notice=notice)


def price_target(before, sale, minimum, maximum, action):
    if before['auto_enabled']:
        if minimum is None or maximum is None or not 0 < minimum < maximum:
            raise ValueError('该选项已开启自动调价，请同时传 --min-price 和 --max-price，最低价须为正数并低于最高价')
        if sale is not None and sale != maximum:
            raise ValueError('自动调价按区间设置；如提供 --to，其值须等于最高设定价')
        return {'auto_enabled':True,'minimum':minimum,'maximum':maximum}
    if minimum is not None or maximum is not None or sale is None or sale <= 0:
        raise ValueError('该选项为固定售价，请只用 --to 指定正整数售价')
    if action=='lower' and sale>before['sale']:
        raise ValueError('提高售价请使用 set')
    return {'auto_enabled':False,'sale':sale}


def matches_target(state, target):
    return all(state.get(key)==value for key,value in target.items())


def edit_range(ev, wait, selected, before, target):
    form = selected + r"""
        const boxes=[...document.querySelectorAll('[data-wuic-partial=widget]')].filter(
            b=>visible(b)&&b.innerText.includes('批量修改自动调价'));
        if(boxes.length!==1)throw Error('自动调价表单不唯一');
        const box=boxes[0], entries=box.querySelectorAll('tbody tr');
        if(entries.length!==1)throw Error('表单须只包含一个选项');
        const entry=entries[0], name=entry.querySelector('.product-title')?.innerText.trim();
        if(!name||!row.cells[1].innerText.split('\n').map(t=>t.trim()).includes(name)||
           Number(entry.cells[1].innerText.replaceAll(',',''))!==Number(price().replace(/[^0-9]/g,'')))
            throw Error('表单商品或售价不一致');
        const cells=[...entry.querySelectorAll('td.editable-td')];
        if(cells.length!==2)throw Error('未找到上下限字段');
        const values=()=>cells.map(c=>Number(c.querySelector('input').value.replaceAll(',','')));
    """
    expected=[before['minimum'],before['maximum']]
    desired=[target['minimum'],target['maximum']]
    wait("return !!document.querySelector('td.editable-td')")
    # Expand the range first when the new minimum exceeds the old maximum.
    order=(1,0) if desired[0]>=expected[1] else (0,1)
    for i in order:
        if desired[i]==expected[i]:continue
        check=f"if(JSON.stringify(values())!==JSON.stringify({expected}))throw Error('调价区间已变化');"
        ev(form+check+f"const e=cells[{i}].querySelector('input');if(e.disabled)throw Error('字段不可编辑');e.focus();input(e,'{desired[i]}');return true")
        expected[i]=desired[i]
    ev(form+f"if(JSON.stringify(values())!==JSON.stringify({desired}))throw Error('上下限回读不符');one('button','确认',box).click();return true")


def edit_sale(ev, selected, expected, sale):
    direction='减少' if sale<expected else '增加'
    box_script=selected+"""const box=document.querySelector('.price-desc')?.closest('[data-wuic-partial=widget]');
      if(!box||box.querySelector('.price-desc').innerText.trim()!=='您所选的1项商品将全部更改' ||
        box.querySelector('.price-unit .dropdown-part').innerText.trim()!=='韩元' ||
        checked().length!==1 || checked()[0]!==row || Number(price().replace(/[^0-9]/g,''))!==EXPECTED)
        throw Error('选项或价格变更条件不匹配');
      const field=box.querySelector('.price-unit input');""".replace('EXPECTED',str(expected))
    if sale>expected:
        ev(box_script+"box.querySelector('.price-option button').click();return true")
        ev(box_script+"one('span:not(:has(*)),li:not(:has(*)),div:not(:has(*)),button:not(:has(*))','增加').click();return true")
    modal=box_script+f"if(box.querySelector('.price-option').innerText.trim()!=='{direction}')throw Error('调价方向不匹配');"
    delta=str(abs(expected-sale))
    ev(modal+"input(field,'"+delta+"');return true")
    ev(modal+"if(field.value.replace(/,/g,'')!=='"+delta+"')throw Error('差额不一致');one('button','确认',box).click();return true")


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root',type=Path,required=True)
    parser.add_argument('--option-id',required=True)
    parser.add_argument('action',nargs='?',choices=('set','lower','auto-status','auto-off'),default='set')
    parser.add_argument('--expect',type=int)
    parser.add_argument('--to',type=int)
    parser.add_argument('--min-price',dest='minimum',type=int)
    parser.add_argument('--max-price',dest='maximum',type=int)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    with browser_lease(args.output_root):
        result=run(args,load_profile(args.output_root))
    print(json.dumps(result,ensure_ascii=False))
    sys.exit(int(result['status'] not in ('preview','verified','already_at_target','observed','already_disabled')))
