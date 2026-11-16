"""查询火箭选项价格、关闭自动调价或降低销售价。"""
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
    if args.action == 'lower' and not (args.to and args.expect and 0 < args.to < args.expect):
        raise ValueError('降价需要 --expect 和低于原价的正整数 --to')
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
        read = """return {name:row.cells[1].innerText,sale:price(),final:row.querySelector('.price-detail-text')?.innerText}"""
        if args.action != 'lower':
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
    if args.action != 'lower':
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
    result={'option_id':args.option_id,'before':before,'target':args.to}
    if before['sale']==args.to:return dict(result,status='already_at_target')
    if before['sale']!=args.expect:raise ValueError(f'当前售价与预期不符：{before}')
    if not args.apply:return dict(result,status='preview')
    progress('已核对火箭选项与原售价，准备修改单个选项')
    ev("""if(rows.length!==1||!row||checked().length)throw Error('商品选择异常');
          row.querySelector('td:first-child input').click();return true""")
    ev("""if(checked().length!==1||checked()[0]!==row)throw Error('选项变化');
          one('button','批量修改所选商品').click();return true""")
    ev("""const labels=[...document.querySelectorAll('span,div,a,button,li')].filter(
              e=>!e.children.length&&visible(e)&&e.innerText.trim()==='变更价格');
          if(labels.length!==1)throw Error('找不到唯一变更价格入口');
          labels[0].click();return true""")
    modal="""const box=document.querySelector('.price-desc')?.closest('[data-wuic-partial=widget]');
      if(!box||box.querySelector('.price-desc').innerText.trim()!=='您所选的1项商品将全部更改' ||
        box.querySelector('.price-option').innerText.trim()!=='减少' ||
        box.querySelector('.price-unit .dropdown-part').innerText.trim()!=='韩元' ||
        checked().length!==1 || checked()[0]!==row || Number(price().replace(/[^0-9]/g,''))!==EXPECTED)
        throw Error('选项或价格变更条件不匹配');
      const field=box.querySelector('.price-unit input');""".replace('EXPECTED',str(args.expect))
    delta=str(args.expect-args.to)
    ev(modal+"input(field,'"+delta+"');return true")
    ev(modal+"if(field.value.replace(/,/g,'')!=='"+delta+"')throw Error('差额不一致');one('button','确认',box).click();return true")
    time.sleep(2)
    notice=ev("return document.body.innerText.split('对此页进行评分').pop().trim().slice(0,400)")
    if notice:progress(notice)
    after=query()
    status='verified' if after['sale']==args.to else 'unverified'
    if status=='unverified' and '프로모션을 종료' in notice:
        status='blocked_promotion'
    return dict(result,after=after,status=status,notice=notice)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root',type=Path,required=True)
    parser.add_argument('--option-id',required=True)
    parser.add_argument('action',nargs='?',choices=('lower','auto-status','auto-off'),default='lower')
    parser.add_argument('--expect',type=int)
    parser.add_argument('--to',type=int)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    with browser_lease(args.output_root):
        result=run(args,load_profile(args.output_root))
    print(json.dumps(result,ensure_ascii=False))
    sys.exit(int(result['status'] not in ('preview','verified','already_at_target','observed','already_disabled')))
