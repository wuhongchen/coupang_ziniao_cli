"""Resumable ZiNiao UI exports. Native save dialogs are handled by the scheduled agent."""
import runtime_clock as clock
import argparse
from datetime import datetime,date,timedelta
from pathlib import Path
import json
import subprocess
import time
import uuid
import shutil
from urllib.parse import urlsplit,parse_qs
from contextlib import ExitStack
import ziniao_environment as runtime
import ziniao_preflight as preflight
from report_paths import ROOT as APP_ROOT
from export_profiles import load_profile,configure_profile
from report_pipeline import accept_export,final_gate,export_analysis
from export_downloads import download_directories,download_snapshot,locate_fresh_export

VERSION='1.2.0'
ROOT=APP_ROOT
STATE=ROOT/'state/collection_run.json'
PROFILE=None
CLI='zn-open-eco'
ALL_REPORTS=('sales_daily','sales_window','inventory','returns','movement')

def configure_runtime(root,profile,cli='zn-open-eco'):
    global ROOT,STATE,PROFILE,CLI
    ROOT=Path(root).expanduser().resolve();STATE=ROOT/'state/collection_run.json';PROFILE=profile;CLI=cli

def make_steps(run_date,days=None,window=None,reports=ALL_REPORTS):
    if days is not None and not 1<=days<=31 or window is not None and not 1<=window<=90:raise ValueError('days需为1–31，window需为1–90')
    if not reports or any(k not in ALL_REPORTS for k in reports) or len(set(reports))!=len(reports):raise ValueError('报表类型无效或重复')
    today=date.fromisoformat(str(run_date));steps=[]
    if 'sales_daily' in reports and (not isinstance(days,int) or days<=0):raise ValueError('请明确--days正整数')
    if 'sales_window' in reports and (not isinstance(window,int) or window<=0):raise ValueError('请明确--window正整数')
    if 'sales_daily' in reports:steps.extend({'kind':'sales_daily','start':str(today-timedelta(days=i)),'end':str(today-timedelta(days=i))} for i in range(days,0,-1))
    if 'sales_window' in reports:steps.append({'kind':'sales_window','start':str(today-timedelta(days=window)),'end':str(today-timedelta(days=1))})
    steps.extend({'kind':k,'start':str(today),'end':str(today)} for k in ('inventory','returns','movement') if k in reports)
    return steps

def url_steps(url,run_date,reports=None):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.netloc!='wing.coupang.com':raise ValueError('仅支持https://wing.coupang.com的已验证导出页面')
    if parsed.path=='/tenants/rfm-inventory/management/list':
        reports=reports or ('inventory',)
        if any(k not in ('inventory','returns','movement') for k in reports):raise ValueError('库存地址只支持inventory、returns、movement')
        steps=make_steps(run_date,reports=reports)
    elif parsed.path=='/tenants/business-insight/sales-analysis':
        query=parse_qs(parsed.query)
        if any(len(query.get(k,[]))!=1 for k in ('start_date','end_date')):raise ValueError('销售地址必须有唯一start_date和end_date')
        start=date.fromisoformat(query['start_date'][0]);end=date.fromisoformat(query['end_date'][0])
        if start>end:raise ValueError('销售日期起止无效')
        kind='sales_daily' if start==end else 'sales_window'
        if reports and reports!=(kind,):raise ValueError('报表类型与销售地址的日期范围不一致')
        steps=[{'kind':kind,'start':str(start),'end':str(end)}]
    else:raise ValueError('此地址尚无固定导出步骤，不会猜测下载按钮')
    for step in steps:step['url']=url
    return steps

def assert_identity(title):
    if not PROFILE or PROFILE['expected_title'] not in title:raise RuntimeError('当前页面身份不匹配配置，停止导出；请核对店铺登录态与expected-title')

def local_call(action,params):
    if action=='snapshot':return ''  # All local interactions use observed CSS selectors.
    args=['ziniao-cli','page']
    if action=='navigate':args+=['visit','--url',params['url']]
    elif action=='evaluate':args+=['exec','--script','JSON.stringify({value:eval('+json.dumps(params['js'])+'),page:'+preflight.PAGE+'})']
    elif action=='click':
        if str(params['ref']).isdigit():raise RuntimeError('本机通道要求已核验CSS，不能复用旧ECO数字ref')
        args+=['click','--selector',params['ref']]
    elif action=='type':args+=['input','--selector',params['ref'],'--text',params['text'],'--clear']+(['--submit'] if params.get('enter') else [])
    else:raise RuntimeError('本机通道不支持此操作：'+action)
    args+=['--store-id',PROFILE['browser_id']]
    if PROFILE.get('target_id'):args+=['--target-id',PROFILE['target_id']]
    result=subprocess.run(args,capture_output=True,text=True,timeout=50)
    if result.returncode:raise RuntimeError('本机紫鸟页面操作失败：'+result.stderr[:200])
    if action!='evaluate':return {}
    data=json.loads(result.stdout)
    if not data.get('ok'):raise RuntimeError('本机紫鸟返回失败')
    data=data['data']['data']
    if data.get('exceptionDetails'):raise RuntimeError('页面脚本异常：'+str(data['exceptionDetails'].get('text','unknown')))
    payload=json.loads(data['result']);preflight.guard(payload.get('page',{}));return {'result':payload.get('value')}

def call(action,params):
    if PROFILE is None:raise RuntimeError('尚未配置店铺')
    if CLI in ('zn-open-eco','ziniao-cli') and shutil.which('ziniao-cli'):return local_call(action,params)
    result=subprocess.run([CLI,'agent','tool','--browser-id',PROFILE['browser_id'],'--task-id','wing-exports-'+PROFILE['browser_id'],'--tool-call-id',str(uuid.uuid4()),'--name','browser','--arguments-json',json.dumps({'action':action,'params':params},ensure_ascii=False),'--wait-seconds','20'],capture_output=True,text=True,timeout=50)
    if result.returncode:raise RuntimeError(result.stderr[:200])
    data=json.loads(result.stdout)
    if data.get('status')!='succeeded':raise RuntimeError(str(data.get('error','浏览器调用未成功')))
    content=data.get('output',{}).get('content',[])
    text=next((x.get('text','') for x in content if x.get('type')=='text'),'')
    try:return json.loads(text)
    except json.JSONDecodeError:return text

def evaluate(js):return call('evaluate',{'js':js,'download':False}).get('result')
def click(ref,download=False):
    call('snapshot',{})
    params={'ref':ref,'expect_file_chooser':False,'download':download}
    if download:params['downloadConfig']={'timeoutMs':1500}
    result=call('click',params);call('snapshot',{})
    return result

def write(run):
    run.update(store_id=PROFILE['store_id'],browser_id=PROFILE['browser_id'],store_name=PROFILE['store_name'],cli_version=VERSION)
    STATE.parent.mkdir(parents=True,exist_ok=True)
    tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(run,ensure_ascii=False,indent=2));tmp.replace(STATE)

def begin(run_date=None,days=None,window=None,reports=ALL_REPORTS,url=None):
    today=date.fromisoformat(run_date) if run_date else datetime.now(clock.timezone()).date()
    steps=url_steps(url,today,reports) if url else make_steps(today,days,window,reports)
    options={'days':days,'window':window,'reports':list(reports)}
    if url:options['url']=url
    if STATE.exists():
        old=json.loads(STATE.read_text(encoding='utf-8'))
        if old.get('store_id',PROFILE['store_id'])!=PROFILE['store_id'] or old.get('browser_id',PROFILE['browser_id'])!=PROFILE['browser_id']:raise ValueError('断点属于其他店铺，停止执行')
        if run_date and old.get('run_date')!=str(today):raise ValueError('指定日期与断点冲突，请使用另一个output-root保存回补轮次')
        if old.get('run_date')==str(today) or old.get('status')!='complete':
            if old.get('options') and old['options']!=options:raise ValueError('参数与既有断点不一致，不能更改正在执行的轮次')
            return old
    run={'run_date':str(today),'status':'ready','index':0,'steps':steps,'results':[],
         'options':options,
         'scope':'仅所选报表；广告和类目不属于固定导出步骤'}
    write(run);return run

def arm_download(run,ref):
    directories=download_directories(PROFILE)
    if not directories or any(not directory.is_dir() for directory in directories):raise ValueError('DOWNLOAD_DIRECTORY_UNAVAILABLE: 本店下载目录不存在或磁盘未挂载；未点击下载')
    run['status']='awaiting_download';run['started_at']=datetime.now().astimezone().isoformat()
    run['download_before']=download_snapshot(run['steps'][run['index']]['kind'],download_directories(PROFILE))
    run.pop('download',None);write(run)
    run['download']=click(ref,True).get('download',{});write(run)

def receive_download(run):
    step=run['steps'][run['index']];directories=download_directories(PROFILE)
    if not directories:raise ValueError('自动模式需要configure --download-dir指定紫鸟实际保存目录')
    path=None;deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        path=locate_fresh_export(step['kind'],run['started_at'],directories,run.get('download_before'))
        if path:break
        time.sleep(.25)
    if path is None:
        return {'status':'awaiting_download','step':step,
                'instruction':'尚无可校验的新XLSX。检查紫鸟自动保存开关、重启是否生效及本店下载目录；保持参数重跑run续传，不重复点击。'}
    result=accept_export(path,step['kind'],step['start'],step['end'],step.get('provisional',False),ROOT,PROFILE['store_id'],fresh_download=True)
    run['results'].append(result);run['index']+=1;run['status']='ready';run.pop('download',None);run.pop('download_before',None);run.pop('error',None);write(run)
    return {'status':'imported','completion_source':'new-file-zip-crc-and-schema','file':str(path),**result}

def select_sales_day(day):
    """Observed Wing calendar, same-day start/end, normal page clicks only."""
    deadline=time.monotonic()+20
    while True:
        ready=evaluate("""(()=>{const es=[...document.querySelectorAll('.context-trigger-filter')].filter(e=>e.getClientRects().length&&e.querySelector('i[title="calendar"]'));if(es.length!==1)return false;es[0].setAttribute('data-wing-date-picker','true');return true})()""")
        if ready:break
        if time.monotonic()>deadline:raise RuntimeError('销售日期选择器未就绪')
        time.sleep(.4)
    click('[data-wing-date-picker="true"]')
    selector='#dp-'+day
    deadline=time.monotonic()+10
    while not evaluate('!!document.querySelector('+json.dumps(selector)+')?.getClientRects().length'):
        if time.monotonic()>deadline:raise RuntimeError('日期不在可见日历内，停止而不猜测月份：'+day)
        time.sleep(.3)
    for _ in range(2):
        # Dual-month calendars repeat IDs for empty adjacent-month placeholders.
        marked=evaluate("""(()=>{document.querySelectorAll('[data-wing-day]').forEach(e=>e.removeAttribute('data-wing-day'));const es=[...document.querySelectorAll('[id='+JSON.stringify(%s)+']')].filter(e=>e.getClientRects().length&&e.innerText.trim()===%s&&!e.querySelector('.dp__cell_disabled'));if(es.length!==1)return false;es[0].setAttribute('data-wing-day','true');return true})()"""%(json.dumps('dp-'+day),json.dumps(str(int(day[-2:])))))
        if not marked:raise RuntimeError('日历日期无法唯一定位或已禁用：'+day)
        click('[data-wing-day="true"]')
    deadline=time.monotonic()+10
    while True:
        ready=evaluate("""(()=>{const es=[...document.querySelectorAll('button')].filter(e=>e.getClientRects().length&&e.innerText.trim().startsWith('选中')&&!e.disabled);if(es.length!==1)return false;es[0].setAttribute('data-wing-date-apply','true');return true})()""")
        if ready:break
        if time.monotonic()>deadline:raise RuntimeError('日期确认按钮未就绪')
        time.sleep(.3)
    selected=evaluate("""[...document.querySelectorAll('[role="gridcell"][aria-selected="true"]')].map(e=>e.id)""")
    if selected!=['dp-'+day]:raise RuntimeError('日历未确认唯一单日，停止下载：'+str(selected))
    click('[data-wing-date-apply="true"]')
    deadline=time.monotonic()+20
    while True:
        url=evaluate('location.href');query=parse_qs(urlsplit(url).query)
        if query.get('start_date')==[day] and query.get('end_date')==[day]:return
        if time.monotonic()>deadline:raise RuntimeError('日历提交后的业务日期未匹配')
        time.sleep(.4)

def next_export(run):
    if run.get('store_id',PROFILE['store_id'])!=PROFILE['store_id']:raise ValueError('断点店铺不匹配')
    if run['index']>=len(run['steps']):
        final_gate(run['results'],run['steps'],ROOT,PROFILE['store_id'])
        if not run.get('analysis_path'):
            data=export_analysis(ROOT,PROFILE['store_id'],PROFILE['store_name'])
            if ROOT==APP_ROOT and (APP_ROOT/'backend/report_data.py').exists():
                from report_data import build_workspace
                detailed=build_workspace()
                data.update({k:detailed[k] for k in ('as_of','summary','periods','insights','daily_dates','batches')})
            out=ROOT/'state/analyses';out.mkdir(parents=True,exist_ok=True)
            target=out/(run['run_date']+'_'+datetime.now().strftime('%H%M%S_%f')+'.json')
            target.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
            run['analysis_path']=str(target)
        run['status']='complete';write(run);return {'status':'complete','results':run['results'],'analysis_path':run['analysis_path']}
    if run.get('status') in ('awaiting_save','awaiting_download'):return receive_download(run)
    run.pop('error',None)
    step=run['steps'][run['index']];kind=step['kind']
    if kind.startswith('sales'):
        url=step.get('url') or f"https://wing.coupang.com/tenants/business-insight/sales-analysis?start_date={step['start']}&end_date={step['end']}"
        if step.get('date_picker'):
            call('navigate',{'url':'https://wing.coupang.com/tenants/business-insight/sales-analysis'})
            select_sales_day(step['start'])
        else:call('navigate',{'url':url})
        for _ in range(12):
            info=evaluate('({title:document.title,url:location.href,ready:document.body.innerText.includes("选项列表"),pending:document.body.innerText.includes("昨天数据可于今天下午12:30查看"),selectors:Array.from(document.querySelectorAll("span")).filter(e=>e.textContent.trim()==="下载Excel").map(e=>({tag:e.tagName,cls:e.className}))})')
            if info.get('ready') and info.get('selectors'):break
            time.sleep(1)
        else:raise RuntimeError('销售页面未就绪，可能需登录或验证')
        assert_identity(info['title'])
        parsed=urlsplit(info['url']);query=parse_qs(parsed.query)
        if parsed.hostname!='wing.coupang.com' or parsed.path!='/tenants/business-insight/sales-analysis' or query.get('start_date')!=[step['start']] or query.get('end_date')!=[step['end']]:raise RuntimeError('页面请求日期不匹配')
        step['provisional']=bool(step.get('provisional') or info.get('pending'))
        call('snapshot',{})
        selector='span.'+'.'.join(info['selectors'][0]['cls'].split())
        click(selector)
        menu=evaluate("""Array.from(document.querySelectorAll('div[class*="_dropdown_"] > div')).map((e,i)=>({i:i+1,text:e.innerText}))""")
        target=next((x for x in menu if x['text'].strip()=='按商品销售'),None)
        if not target:raise RuntimeError('按商品销售导出入口已变化')
        evaluate("""Array.from(document.querySelectorAll('div[class*="_dropdown_"] > div')).filter(e=>e.innerText.trim()==='按商品销售').forEach(e=>e.setAttribute('data-wing-sales-export','true'));true""")
        arm_download(run,'[data-wing-sales-export="true"]')
    else:
        call('navigate',{'url':step.get('url') or 'https://wing.coupang.com/tenants/rfm-inventory/management/list'})
        for _ in range(12):
            info=evaluate('({title:document.title,ready:document.body.innerText.includes("库存现状"),buttons:Array.from(document.querySelectorAll("button")).map((e,i)=>({i,text:e.innerText})).filter(b=>b.text.includes("Excel"))})')
            if info.get('ready') and info.get('buttons'):break
            time.sleep(1)
        assert_identity(info['title'])
        call('snapshot',{})
        item=next((b for b in info['buttons'] if b['text'].strip().startswith('Excel下载')),None)
        if not item:raise RuntimeError('库存导出按钮未找到')
        # Set a stable observed marker only on the exact known export button.
        evaluate('Array.from(document.querySelectorAll("button")).filter(e=>e.innerText.trim().startsWith("Excel下载")).forEach(e=>e.setAttribute("data-wing-export","inventory"));true')
        click('button[data-wing-export="inventory"]')
        index={'inventory':1,'returns':3,'movement':4}[kind]
        if kind=='movement':
            evaluate("""Array.from(document.querySelectorAll('div.selector > div > span')).filter(e=>e.innerText.trim()==='下载库存变动明细Excel').forEach(e=>e.setAttribute('data-wing-movement','true'));true""")
            click('span[data-wing-movement="true"]')
            found=evaluate("""(()=>{const labels=Array.from(document.querySelectorAll('label')).filter(e=>e.innerText.trim()==='全部库存变动详情'&&e.getClientRects().length);const buttons=Array.from(document.querySelectorAll('button')).filter(e=>e.innerText.trim()==='下载'&&e.getClientRects().length);if(labels.length!==1||buttons.length!==1)return false;labels[0].setAttribute('data-wing-all-movement','true');buttons[0].setAttribute('data-wing-download-movement','true');return true})()""")
            if not found:raise RuntimeError('库存变动下载选择框未识别')
            click('[data-wing-all-movement="true"]')
            arm_download(run,'[data-wing-download-movement="true"]')
        else:
            arm_download(run,f'div.selector > div:nth-child({index})')
    return receive_download(run)


def main(argv=None):
    import sys
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser(description='Coupang Wing 紫鸟页面导出 CLI（无平台API）')
    runtime.options(p)
    p.add_argument('--version',action='version',version=VERSION)
    actions=p.add_mutually_exclusive_group()
    for name in ('next','status','start','run','configure','plan','check-store'):actions.add_argument('--'+name,action='store_true')
    actions.add_argument('--accept-file')
    p.add_argument('--output-root',type=Path,default=APP_ROOT)
    p.add_argument('--ads-identity',help='本店广告商家代码，由用户提供');p.add_argument('--browser-id');p.add_argument('--store-name');p.add_argument('--expected-title');p.add_argument('--download-dir',action='append')
    p.add_argument('--run-date');p.add_argument('--days',type=int);p.add_argument('--window',type=int)
    p.add_argument('--reports');p.add_argument('--cli',default='zn-open-eco');p.add_argument('--url')
    a=p.parse_args(argv);run=None;env=ExitStack()
    try:
        env.enter_context(runtime.environment(a))
        root=a.output_root.expanduser().resolve()
        reports=tuple(a.reports.split(',')) if a.reports else None
        today=a.run_date or str(datetime.now(clock.timezone()).date())
        if a.configure:
            reports=();steps=[]
        elif a.status or a.check_store:
            reports=reports or ALL_REPORTS;steps=[]
        elif a.url:
            steps=url_steps(a.url,today,reports);reports=reports or tuple(s['kind'] for s in steps)
        else:
            reports=reports or ALL_REPORTS;steps=make_steps(today,a.days,a.window,reports)
        if a.configure:
            if a.store_id and str(a.store_id).removeprefix('ziniao:')!=a.browser_id:raise ValueError('STORE_BINDING_MISMATCH: 指定店铺ID与待配置browser-id不同')
            if not all((a.browser_id,a.store_name,a.expected_title)):raise ValueError('configure需要browser-id、store-name、expected-title')
            profile=configure_profile(root,a.browser_id,a.store_name,a.expected_title,a.download_dir,a.ads_identity)
            print(json.dumps({'status':'configured','profile':profile,'output_root':str(root)},ensure_ascii=False));return 0
        if any((a.browser_id,a.store_name,a.expected_title,a.download_dir,a.ads_identity)):raise ValueError('身份参数仅用于--configure，执行时从本目录配置读取，避免误用店铺')
        profile=runtime.select_store(load_profile(root),a.store_id);configure_runtime(root,profile,a.cli)
        if a.plan or not any((a.next,a.start,a.run,a.status,a.accept_file,a.check_store)):
            out={'status':'plan','version':VERSION,'profile':profile,'output_root':str(root),'steps':steps,'browser_actions_sent':False}
        elif a.check_store:
            preflight.check(root,profile,require_wing=True)
            info=evaluate('({title:document.title,url:location.origin+location.pathname})')
            assert_identity(info['title']);out={'status':'identity_verified','browser_id':profile['browser_id'],'store_name':profile['store_name'],'page':info}
        elif a.status:
            out=json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {'status':'not_started','store_id':profile['store_id']}
            if out.get('store_id',profile['store_id'])!=profile['store_id']:raise ValueError('断点属于其他店铺')
        else:
            if a.next or a.start or a.run:preflight.check(root,profile)
            if (a.start or a.run) and STATE.exists():
                previous=json.loads(STATE.read_text(encoding='utf-8'))
                if previous.get('store_id',profile['store_id'])!=profile['store_id']:raise ValueError('断点属于其他店铺')
                if previous.get('status')=='complete':
                    history=STATE.parent/'collection_history';history.mkdir(exist_ok=True)
                    STATE.replace(history/(previous['run_date']+'_'+datetime.now().strftime('%H%M%S_%f')+'.json'))
            run=begin(a.run_date,a.days,a.window,reports,a.url)
            if a.accept_file:
                if run['status'] not in ('awaiting_save','awaiting_download'):raise ValueError('当前没有等待中的导出')
                step=run['steps'][run['index']]
                if run.get('started_at') and Path(a.accept_file).stat().st_mtime+2<datetime.fromisoformat(run['started_at']).timestamp():raise ValueError('文件早于本次导出，不能作为新下载确认')
                result=accept_export(a.accept_file,step['kind'],step['start'],step['end'],step.get('provisional',False),ROOT,profile['store_id'])
                run['results'].append(result);run['index']+=1;run['status']='ready';write(run);out={'status':'imported',**result}
            elif a.run:
                waits=0
                while True:
                    out=next_export(run)
                    if out.get('status')=='imported':
                        waits=0;print(json.dumps(out,ensure_ascii=False),flush=True);continue
                    if out.get('status')=='awaiting_download' and waits<2:
                        waits+=1;print(json.dumps(out,ensure_ascii=False),flush=True);continue
                    break
            elif a.next:out=next_export(run)
            else:out=run
        print(json.dumps(out,ensure_ascii=False));return 10 if out.get('status') in ('awaiting_save','awaiting_download') else 0
    except Exception as exc:
        if run is not None:run['error']=runtime.redact(exc);write(run)
        failure=runtime.record_failure(a.output_root,'core-export',exc)
        print(json.dumps({'status':'failed','error':runtime.redact(exc),'failure_record':failure},ensure_ascii=False));return 1

    finally:env.close()

if __name__=='__main__':
    # Standalone export processes share the same browser lease as daily collection/ads.
    from export_profiles import browser_lease
    guard=argparse.ArgumentParser(add_help=False);guard.add_argument('--output-root',type=Path,default=APP_ROOT)
    location,_=guard.parse_known_args()
    try:
        with browser_lease(location.output_root.expanduser().resolve()):raise SystemExit(main())
    except RuntimeError as exc:
        print(json.dumps({'status':'failed','error':runtime.redact(exc)},ensure_ascii=False));raise SystemExit(1)
