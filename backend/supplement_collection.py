"""Replay observed Wing UI exports; persistent checkpoints avoid duplicate report requests."""
from pathlib import Path
from datetime import date,datetime,timedelta
import json,time,hashlib,shutil,zipfile
import openpyxl
from operation_progress import progress
from ziniao_environment import redact
import ziniao_exports as browser
from export_downloads import download_directories
from report_pipeline import state as batches

def validated_ad_scope(profile,period,selection):
    if not profile.get('store_id') or not profile.get('ads_identity') or len(period)!=2:raise ValueError('广告导出身份/日期缺失')
    if any(selection.get(k) is not True for k in ('daily','structure','all_campaigns')) or selection.get('click_only') is not False:raise ValueError('广告导出范围未经页面确认，不可确认导出范围')
    return {'store_id':profile['store_id'],'merchant_code':profile['ads_identity'],'period':period,'daily':True,'all_campaigns':True,'all_keywords':True}

from ziniao_preflight import PagePermissionDenied

def check_data_access(info):
    from ziniao_preflight import guard,page_error
    if page_error(info) not in (None,'PERMISSION_DENIED'):guard(info)
    text=info.get('text','')
    notices=('您没有权限','无权访问此功能','未开通此功能','이용 권한이 없습니다','접근 권한이 없습니다')
    if any(notice in text for notice in notices):raise PagePermissionDenied('DATA_PERMISSION_DENIED: 页面明确提示：'+next(notice for notice in notices if notice in text))
    guard(info)

def insight_state(section, text):
    if section == 'category':
        if 'Top竞争商品' in text:
            return 'ready'
        gated = '立即订阅查看详情' in text and '免费试用商业洞察' in text
    else:
        if '搜索词分析数据不足' in text:
            return 'insufficient_data'
        if '搜索曝光量' in text or '查看该关键词的竞争商品' in text:
            return 'ready'
        gated = '订阅商业洞察，查看强化版数据' in text
    if gated or '请订阅商业洞察' in text or '请开通商业洞察' in text:
        raise PagePermissionDenied('DATA_PERMISSION_DENIED: '+('品类榜单' if section=='category' else '关键词明细')+'要求订阅商业洞察')
    return None

class Collector:
    def __init__(self,root,profile,run_date,start=None,end=None,category_id=None):
        self.root=root;self.profile=profile;self.today=date.fromisoformat(run_date)
        self.start=date.fromisoformat(start) if start else None;self.end=date.fromisoformat(end) if end else None
        if (self.start is None)!=(self.end is None) or (self.start and self.start>self.end):raise ValueError('请提供有效的start和end日期范围')
        if category_id is not None and not str(category_id).isdigit():raise ValueError('类目ID必须是数字')
        self.category_id=str(category_id) if category_id is not None else None
        self.file=root/'state/supplement_run.json'
        self.run=json.loads(self.file.read_text()) if self.file.exists() else {}
        if self.run.get('run_date')!=run_date:self.run={'run_date':run_date,'store_id':profile['store_id'],'steps':{},'status':'running','audit':str(root/'state/full_exports'/(run_date+'_daily'))}
        if self.run['store_id']!=profile['store_id']:raise ValueError('补充任务店铺不匹配')
        self.audit=Path(self.run['audit']);self.audit.mkdir(parents=True,exist_ok=True)
        browser.configure_runtime(root,profile)
    def save(self):
        tmp=self.file.with_suffix('.tmp');tmp.write_text(json.dumps(self.run,ensure_ascii=False,indent=2));tmp.replace(self.file)
        (self.audit/'manifest.json').write_text(json.dumps({'store':self.profile['store_name'],'store_id':self.profile['store_id'],'items':list(self.run['steps'].values())},ensure_ascii=False,indent=2))
    def ev(self,js):return browser.evaluate(js)
    def text(self):return self.ev('document.body.innerText')
    def wait(self,check,seconds=25):
        started=time.monotonic();end=started+seconds;next_notice=started+15
        while time.monotonic()<end:
            result=check()
            if result:return result
            now=time.monotonic()
            if now>=next_notice:
                progress(f"仍在等待页面或报表就绪，已等待 {int(now-started)} 秒")
                next_notice=now+15
            check_data_access(self.ev('({url:location.href,text:document.body.innerText})'))
            time.sleep(.5)
        raise RuntimeError('PAGE_NOT_READY: 页面等待超时；可能需登录、验证或页面结构改变，保留断点，不重复下载')
    def nav(self,url,ads=False):
        browser.call('navigate',{'url':url})
        def ready():
            d=self.ev('({url:location.href,title:document.title,text:document.body.innerText})')
            check_data_access(d)
            identity=self.profile.get('ads_identity') if ads else self.profile['expected_title']
            return d if identity and identity in (d['text'] if ads else d['title']) else None
        self.wait(ready)
    def ct(self,text,tag='button'):
        aliases={'기간 설정':'设置时间段','일별':'每天','캠페인을 선택하세요':'请选择广告活动','전체선택':'全选','확인':'确认','캠페인 > 광고그룹 > 상품 > 키워드':'广告活动 > 广告组 > 商品 > 关键词','클릭이 발생한 키워드만 보고서에 포함':'报告中仅包含有点击次数的关键词','보고서 만들기':'生成报告书','목록 새로 고침':'刷新列表','엑셀파일 다운로드':'下载Excel文件'}
        names=[''.join(x.split()) for x in ([text,aliases[text]] if text in aliases else [text])]
        js='''(()=>{document.querySelectorAll('[data-workbench-action]').forEach(e=>e.removeAttribute('data-workbench-action'));let es=[...document.querySelectorAll(%s)].filter(e=>%s.includes(e.innerText.replace(/\\s/g,''))&&e.getClientRects().length);if(es.length!==1)return false;es[0].setAttribute('data-workbench-action','1');return true})()'''%(json.dumps(tag),json.dumps(names))
        self.wait(lambda:self.ev(js),5);browser.click('[data-workbench-action="1"]');time.sleep(.25)
    def files(self):
        return {str(p):[p.stat().st_mtime_ns,p.stat().st_size] for d in download_directories(self.profile) for p in d.rglob('*.xlsx') if p.is_file() and p.resolve().is_relative_to(d.resolve())}
    def receive(self,key,before,seconds=30):
        progress(f"等待下载并校验文件：{key}")
        def fresh():
            found=[]
            for path,sig in self.files().items():
                if before.get(path)==sig:continue
                try:
                    with zipfile.ZipFile(path) as z:
                        if z.testzip() is not None:continue
                    w=openpyxl.load_workbook(path,read_only=True,data_only=True);w.close();found.append(Path(path))
                except (OSError,zipfile.BadZipFile):continue
            if len(found)>1:raise RuntimeError('多个新文件，停止猜测来源')
            return found[0] if found else None
        p=self.wait(fresh,seconds);digest=hashlib.sha256(p.read_bytes()).hexdigest()
        dest=self.root/'state/reports/自动补充'/self.profile['browser_id'];dest.mkdir(parents=True,exist_ok=True)
        target=dest/(datetime.now().strftime('%Y%m%d_%H%M%S_')+key+'_'+digest[:12]+'.xlsx')
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest()!=digest:raise RuntimeError('补充归档已损坏，不覆盖原文件')
        else:
            temporary=target.with_suffix('.xlsx.partial');shutil.copy2(p,temporary)
            if hashlib.sha256(temporary.read_bytes()).hexdigest()!=digest:raise RuntimeError('补充下载复制校验失败，保留下载断点，不再次点击')
            temporary.replace(target)
        self.run['steps'][key]={'status':'complete','file':str(target),'sha256':digest,'captured_at':datetime.now().astimezone().isoformat()};self.save()
        progress(f"文件校验通过，已归档：{target.name}")
    def download(self,key,fn):
        step=self.run['steps'].get(key,{})
        if step.get('status')=='complete':return
        # Resume an in-flight click by observing the filesystem, never clicking twice.
        if step.get('status')=='awaiting_download':return self.receive(key,step['before'])
        directories=download_directories(self.profile)
        if not directories or any(not directory.is_dir() for directory in directories):raise ValueError('DOWNLOAD_DIRECTORY_UNAVAILABLE: 本店下载目录不存在或磁盘未挂载；未点击下载')
        progress(f'正在下载：{key}')
        before=self.files();self.run['steps'][key]={'status':'awaiting_download','before':before};self.save()
        try:fn()
        except Exception as e:self.run['steps'][key]['error']=redact(e);self.save();raise
        self.receive(key,before)
    def ads(self):
        if not self.start or not self.end:raise ValueError('广告下载必须提供--start和--end')
        key='广告逐日关键词'
        if self.run['steps'].get(key,{}).get('status')=='complete':return
        self.nav('https://advertising.coupang.com/marketing-reporting/billboard/reports/pa',True)
        self.wait(lambda:self.ev("!!document.querySelector('[row-id]')"))
        a=str(self.start);b=str(self.end)
        def row():
            matches=self.ev('''(()=>{const rows=[...document.querySelectorAll('[row-id]')].filter(e=>e.innerText.includes(%s)&&['[일별] 캠페인 > 광고그룹 > 상품 > 키워드','[每天] 广告活动 > 广告组 > 商品 > 关键词'].some(t=>e.innerText.includes(t))).map(e=>({id:e.getAttribute('row-id'),ready:['생성 완료','创建成功'].some(t=>e.innerText.includes(t))}));return [...new Map(rows.map(r=>[r.id,r])).values()]})()'''%json.dumps(a+' ~ '+b))
            if not isinstance(matches,list):raise RuntimeError('广告报表列表结构改变，停止选择')
            if len(matches)>1:raise RuntimeError('REPORT_AMBIGUOUS: 同周期同类型存在多个广告报告，停止猜测，不重复创建')
            return matches[0] if matches else None
        if not row():
            if self.run['steps'].get('广告生成',{}).get('status')=='requested':raise RuntimeError('已请求广告报告但列表未找到；停止重复创建')
            self.ct('기간 설정','label');self.ct('일별','label')
            browser.click('.ant-picker-range input:first-of-type');time.sleep(.4)
            # The observed picker initially shows the current month; previous month holds start.
            if not self.ev('!!document.querySelector(\'td[title="'+a+'"]\')'):browser.click('.ant-picker-header-prev-btn');time.sleep(.3)
            browser.click('td[title="'+a+'"]');browser.click('td.ant-picker-cell-in-view[title="'+b+'"]')
            self.wait(lambda:self.ev('[...document.querySelectorAll("input")].filter(e=>e.type==="text").map(e=>e.value)')==[a,b])
            self.ct('캠페인을 선택하세요')
            if not self.ev('[...document.querySelectorAll("label")].find(e=>["전체선택","全选"].includes(e.innerText.trim()))?.querySelector("input").checked'):self.ct('전체선택','label')
            all_campaigns=self.ev('[...document.querySelectorAll("label")].find(e=>["전체선택","全选"].includes(e.innerText.trim())&&e.getClientRects().length)?.querySelector("input").checked')
            self.ct('확인')
            self.ct('캠페인 > 광고그룹 > 상품 > 키워드','label')
            if self.ev('[...document.querySelectorAll("label")].find(e=>["클릭이 발생한 키워드만 보고서에 포함","报告中仅包含有点击次数的关键词"].includes(e.innerText.trim()))?.querySelector("input").checked'):self.ct('클릭이 발생한 키워드만 보고서에 포함','label')
            self.wait(lambda:self.ev('[...document.querySelectorAll("button")].some(e=>["보고서 만들기","生成报告书"].includes(e.innerText.trim())&&!e.disabled)'))
            selection=self.ev('''(()=>{const checked=names=>[...document.querySelectorAll('label')].find(e=>names.includes(e.innerText.trim()))?.querySelector('input')?.checked;return {daily:checked(['일별','每天']),structure:checked(['캠페인 > 광고그룹 > 상품 > 키워드','广告活动 > 广告组 > 商品 > 关键词']),click_only:checked(['클릭이 발생한 키워드만 보고서에 포함','报告中仅包含有点击次数的关键词'])}})()''')
            selection['all_campaigns']=all_campaigns
            scope=validated_ad_scope(self.profile,[a,b],selection)
            self.run['steps']['广告生成']={'status':'requested','period':[a,b],'export_scope':scope};self.save();self.ct('보고서 만들기')
        def ready():
            r=row()
            if r and r['ready']:return r
            self.ct('목록 새로 고침');return None
        progress('等待逐日广告关键词报告生成')
        r=self.wait(ready,120)
        self.download(key,lambda:browser.click('[row-id="'+r['id']+'"] [col-id="viewReport"] button:last-child'))
        request=self.run['steps'].get('广告生成',{})
        if request.get('period')==[a,b] and request.get('export_scope'):
            self.run['steps'][key]['export_scope']={**request['export_scope'],'report_id':r['id']}
            self.save()
    def bills(self,current=None):
        for current in ((False,True) if current is None else (current,)):
            key='广告账单_'+('本月' if current else '上月')
            if self.run['steps'].get(key,{}).get('status')=='complete':continue
            self.nav('https://advertising.coupang.com/marketing-reporting/billboard/settlements/daily',True)
            prefixes=['이번달','本月'] if current else ['지난달','上个月']
            label=self.wait(lambda:self.ev('[...document.querySelectorAll("label")].map(e=>e.innerText.trim()).find(t=>'+json.dumps(prefixes)+'.some(p=>t.startsWith(p)))'))
            self.ct(label,'label');self.ct('보고서 만들기');self.wait(lambda:any(t in self.text() for t in ('엑셀파일 다운로드','下载Excel文件','下载Excel','下载EXCEL','Excel文件下载')))
            self.download(key,lambda:self.ct('엑셀파일 다운로드'))
    def fees(self,only=None,period=None,settlement_batch=None):
        if not isinstance(period,int) or isinstance(period,bool) or period<=0:raise ValueError('费用下载须提供--fee-interval-days正整数')
        labels=['销售手续费报告','仓库操作费/配送费报告','仓储费率报','退货回收费/重新入库费报告','退仓费报告','退仓配送服务费报告','库存损失赔偿报告','附加服务费报告','评价活动费用报告']
        if only is not None:
            if only not in labels:raise ValueError('未知费用类别')
            labels=[only]
        for label in labels:
            key=label.replace('/','与')
            from fee_cadence import reusable,metadata
            if self.run['steps'].get(key,{}).get('status')=='awaiting_download':
                self.receive(key,self.run['steps'][key]['before']);self.run['steps'][key].update(settlement_batch=settlement_batch,fee_kind=key,cadence=metadata(self.run['steps'][key],self.today,period,False));self.save();continue
            cached=reusable(self.root,self.profile,key,self.today,period) if not settlement_batch else None
            if settlement_batch and self.run['steps'].get(key,{}).get('status')=='complete':continue
            if cached:
                self.run['steps'][key]=cached;self.save();continue
            # A reused checkpoint may become due under a different explicit cadence.
            old=self.run['steps'].pop(key,None)
            if old and old.get('cadence',{}).get('reused'):self.run['steps'].pop('申请_'+key,None)
            self.nav('https://wing.coupang.com/tenants/rfm/settlements/status-new')
            # UI exposes each settlement separately: require one batch rather than guess.
            self.wait(lambda:'Excel下载' in self.text())
            notice=self.ev("""(()=>{const b=[...document.querySelectorAll('button')].find(e=>e.innerText.trim()==='关闭'&&e.getBoundingClientRect().width&&e.parentElement.parentElement.innerText.includes('不再显示'));if(!b)return false;b.setAttribute('data-wing-close-settlement-notice','true');return true})()""")
            if notice:browser.click('[data-wing-close-settlement-notice="true"]')
            n=self.ev('[...document.querySelectorAll("button")].filter(e=>e.innerText.trim()==="Excel下载").length')
            if settlement_batch:
                pay,ratio,start,end=settlement_batch
                expected=[pay,ratio,start+'~'+end]
                matches=self.ev("""(()=>{const rows=[...document.querySelectorAll('tr')].filter(r=>JSON.stringify([0,2,3].map(i=>r.cells[i]?.innerText.trim()))===JSON.stringify(%s));if(rows.length!==1)return false;const buttons=[...rows[0].querySelectorAll('button')].filter(b=>b.innerText.trim()==='Excel下载');if(buttons.length!==1)return false;buttons[0].scrollIntoView({block:'center',behavior:'instant'});buttons[0].setAttribute('data-wing-settlement','selected');return true})()"""%json.dumps(expected))
                if not matches:raise RuntimeError('未找到与指定结算日、比例和销售确认期完全匹配的唯一批次')
            elif n!=1:raise RuntimeError(f'结算页面有 {n} 个下载入口，当前命令要求唯一结算批次')
            reportlabel='退仓配送费报告' if label=='退仓配送服务费报告' else label
            if self.run['steps'].get('申请_'+key,{}).get('status')!='requested':
                if settlement_batch:browser.click('[data-wing-settlement="selected"]')
                else:self.ct('Excel下载')
                request_time=datetime.now()
                self.ct(label)
                self.run['steps']['申请_'+key]={'status':'requested','requested_at':request_time.isoformat()};self.save();time.sleep(1)
            else:self.ct('结算管理Excel文件下载列表')
            def ready():
                request=self.run['steps']['申请_'+key]
                stamp=request.get('requested_at')
                if not stamp and settlement_batch:stamp=datetime.fromtimestamp(self.file.stat().st_mtime).isoformat()
                bounds=None
                if stamp:
                    point=datetime.fromisoformat(stamp)
                    bounds=[(point+timedelta(seconds=n)).strftime('%Y-%m-%d %H:%M:%S') for n in (-5,15)]
                r=self.ev("""(()=>{const bounds=%s;const buttons=[...document.querySelectorAll('tr')].filter(e=>e.querySelector('.report-type')?.innerText==='报告 : '+%s&&(!bounds||(e.cells[0].innerText>=bounds[0]&&e.cells[0].innerText<=bounds[1]))).map(e=>e.querySelector('button')).filter(b=>{if(!b||b.disabled)return false;const r=b.getBoundingClientRect();return b.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))});if(buttons.length!==1)return false;document.querySelectorAll('[data-workbench-fee]').forEach(e=>e.removeAttribute('data-workbench-fee'));buttons[0].setAttribute('data-workbench-fee','1');return true})()"""%(json.dumps(bounds),json.dumps(reportlabel)))
                if not r:
                    refresh=self.ev("""(()=>{const b=[...document.querySelectorAll('button')].find(b=>{const r=b.getBoundingClientRect();return b.innerText.trim()==='刷新'&&b.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))});if(!b)return false;b.setAttribute('data-wing-refresh-fees','true');return true})()""")
                    if refresh:browser.click('[data-wing-refresh-fees="true"]');time.sleep(2)
                return r
            progress(f'等待结算报告生成：{key}')
            self.wait(ready,120);self.download(key,lambda:browser.click('[data-workbench-fee="1"]'))
            self.run['steps'][key].update(settlement_batch=settlement_batch,fee_kind=key,cadence=metadata(self.run['steps'][key],self.today,period,False));self.save()
    def pages(self,section=None):
        if section not in (None,'category','products','keywords'):raise ValueError('未知页面类别')
        if section in (None,'category') and not self.category_id:raise ValueError('类目采集必须提供--category-id')
        if section in (None,'keywords') and (not self.start or not self.end):raise ValueError('关键词流量必须提供--start和--end')
        key={None:'页面数据','category':'类目页面','products':'商品目录页面','keywords':'选项关键词流量页面'}[section]
        entry=self.run['steps'].get(key,{})
        if entry.get('status')=='complete' and entry.get('files'):
            for source in entry['files']:
                path=Path(source['file'])
                base=self.root/'state' if hasattr(self,'root') else self.audit
                if not path.resolve().is_relative_to(base.resolve()) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=source['sha256']:raise RuntimeError('页面原始快照校验失败，不复用完成标记')
            return
        pages=[];files=[]
        if section in (None,'category'):
            self.nav(f'https://wing.coupang.com/tenants/business-insight/category-analysis/detail?c_code={self.category_id}')
            self.wait(lambda:insight_state('category',self.text()))
            for tab in ['Top竞争商品','Top关键词','Top品牌']:
                self.ct(tab,'a');self.wait(lambda:('查看Top10关键词' in self.text()) if tab=='Top竞争商品' else ('查看Top10商品' in self.text() and ('搜索量' in self.text() if tab=='Top关键词' else '搜索量' not in self.text())) )
                nums=self.ev('[...document.querySelectorAll("a")].map(e=>e.innerText.trim()).filter(t=>/^[1-9]$/.test(t))') or ['1']
                for n in sorted(set(nums),key=int):
                    if n!='1':self.ct(n,'a');time.sleep(1)
                    body=self.wait(lambda:self.text() if '查看Top10' in self.text() else None)
                    pages.append({'tab':tab,'page':int(n),'text':body,'observed_at':str(self.today)})
            path=self.audit/('类目'+self.category_id+'_页面可见列表.json');path.write_text(json.dumps(pages,ensure_ascii=False,indent=2));files.append(path)
        if section in (None,'products'):
            self.nav('https://wing.coupang.com/vendor-inventory/list');self.wait(lambda:'商品 ID' in self.text())
            rows=self.ev('[...document.querySelectorAll("tbody tr")].map(e=>e.innerText).filter(t=>t.includes("商品 ID"))')
            path=self.audit/'商品目录_页面快照.json';path.write_text(json.dumps({'rows':rows,'text':self.text()},ensure_ascii=False,indent=2));files.append(path)
        if section in ('category','products'):
            self.run['steps'][key]={'status':'complete','category_pages':len(pages),'files':[{'file':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()} for path in files]};self.save();return
        ids=set();bs=batches(self.root,self.profile['store_id'])
        for kind in ('inventory','sales_window'):
            b=max((b for b in bs if b['kind']==kind),key=lambda b:(b['end'],b['captured']),default=None)
            if b:ids.update(r['id'] for r in b['payload'])
        if not ids:raise RuntimeError('DATA_UNAVAILABLE: 没有已核验选项，不能将零个选项当作采集成功')
        dest=self.audit/'选项流量关键词';dest.mkdir(exist_ok=True)
        for index,oid in enumerate(sorted(ids),1):
            progress(f"选项关键词流量 [{index}/{len(ids)}]")
            p=dest/(oid+'.json')
            if p.exists():
                previous=json.loads(p.read_text());body=previous['pages'][0]['text'] if previous.get('pages') else ''
                if not previous.get('errors') and ('查看该关键词的竞争商品' in body or '搜索词分析数据不足' in body):continue
            d={'option_id':oid,'pages':[],'views':{},'errors':[],'requested_period':[str(self.start),str(self.end)]}
            try:
                self.nav(f'https://wing.coupang.com/tenants/business-insight/sales-analysis/vendor-item-summary?vendorItemId={oid}&start_date={self.start}&end_date={self.end}')
                self.wait(lambda:f'选项 ID: {oid}' in self.text())
                d['keyword_status']=self.wait(lambda:insight_state('keywords',self.text()))
                for n in range(1,51):
                    selector=f'span[data-wuic-attrs^="page:{n}"] a'
                    if n>1:
                        if not self.ev('!!document.querySelector('+json.dumps(selector)+')'):break
                        previous=d['pages'][-1]['text'];browser.click(selector)
                        self.wait(lambda:self.text()!=previous and '查看该关键词的竞争商品' in self.text())
                    body=self.text()
                    if d['pages'] and body==d['pages'][-1]['text']:raise RuntimeError('分页内容未变化')
                    d['pages'].append({'page':n,'text':body})
                else:raise RuntimeError('关键词分页超过安全上限')
                self.ct('流入渠道','a');time.sleep(1);d['views']['流量渠道']=self.text()
                self.ct('查看自然流量详情','label');d['views']['自然流量详情']=self.text()
                for name in ['流量趋势','流量漏斗']:self.ct(name,'label');d['views'][name]=self.text()
            except PagePermissionDenied as exc:
                d['errors'].append(str(exc))
                p.write_text(json.dumps(d,ensure_ascii=False,indent=2))
                raise
            except Exception as e:d['errors'].append(str(e))
            p.write_text(json.dumps(d,ensure_ascii=False,indent=2))
            if d['errors']:raise RuntimeError(oid+' 页面采集未完成：'+d['errors'][0])
        files.extend(dest/(oid+'.json') for oid in sorted(ids))
        self.run['steps'][key]={'status':'complete','options':len(ids),'category_pages':len(pages),'files':[{'file':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()} for path in files]};self.save()
    def reviews(self):
        key='商品评价列表'
        entry=self.run['steps'].get(key,{})
        if entry.get('status')=='complete':
            path=Path(entry['file'])
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=entry['sha256']:raise RuntimeError('评价原始归档缺失或改变')
            return
        from review_collection import collect
        try:
            self.run['steps'][key]={'status':'running'};self.save()
            self.run['steps'][key]=collect(self.root,self.profile,str(self.today));self.save()
        except Exception as exc:
            self.run['steps'][key]={'status':'failed','error':str(exc)};self.save();raise
    def validate(self):
        completed=[v for k,v in self.run['steps'].items() if k!='商品评价列表' and v.get('status')=='complete' and v.get('file')]
        if self.run['steps'].get('商品评价列表',{}).get('status')!='complete':raise RuntimeError('商品评价采集未完成')
        self.reviews()
        if len(completed)!=12:raise RuntimeError('补充导出尚未全部完成')
        for entry in completed:
            p=Path(entry['file'])
            if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=entry['sha256']:raise RuntimeError('补充原始报表缺失或已改变')
        info=self.run['steps'].get('页面数据',{})
        files=list((self.audit/'选项流量关键词').glob('*.json'))
        if info.get('status')!='complete' or len(files)!=info.get('options'):raise RuntimeError('选项页面覆盖不完整')
        for p in files:
            d=json.loads(p.read_text())
            if d.get('errors') or not d.get('pages') or len(d.get('views',{}))!=4:raise RuntimeError('选项页面尚有错误：'+p.stem)

    def execute(self,fee_period=None):
        self.run['status']='running';self.run.pop('error',None);self.save()
        try:
            __import__('ziniao_preflight').check(self.root,self.profile)
            self.reviews();self.ads();self.bills();self.fees(period=fee_period);self.pages();self.validate();self.run['status']='complete';self.run.pop('error',None);self.save()
        except Exception as e:self.run['status']='failed';self.run['error']=redact(e);self.save();raise
        return self.run
