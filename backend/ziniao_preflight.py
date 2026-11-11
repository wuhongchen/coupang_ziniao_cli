"""Fail closed before downloads; store discovery never switches the bound account."""
from pathlib import Path
from datetime import datetime
from urllib.parse import urlsplit
import json,re,subprocess,time
from operation_progress import progress
from ziniao_environment import executable,redact
PAGE="({url:location.href,title:document.title,text:document.body.innerText.slice(0,2000),captcha:[...document.querySelectorAll('iframe[src*=captcha],input[name=captcha]')].some(e=>e.getClientRects().length)})"
FIRST_USE = (
 "首次使用请按照《Agent CLI 使用方式及能力介绍》完成紫鸟 API 开通与配置。\n"
 "https://yxgb3sicy7.feishu.cn/wiki/JXOLwgkFIinBlSkKov6cpTEXn4g"
)

def page_error(info):
 url=info.get('url','');text=info.get('text','')
 if info.get('captcha') or '/captcha' in url.lower() or re.search(r'^(?:请完成安全验证|安全验证|자동입력 방지)',text.strip()):return 'CAPTCHA_REQUIRED'
 if urlsplit(url).hostname=='xauth.coupang.com' or info.get('password') or re.search(r'/(?:login|signin)(?:[/?#]|$)',url,re.I):return 'LOGIN_REQUIRED'
 if re.search(r'^(?:Service Unavailable|服务不可用|503 Service)',text.strip(),re.I):return 'NETWORK_UNAVAILABLE'
 if url.startswith('chrome-error:') or re.search(r'ERR_(?:SOCKS|CONNECTION|NAME_NOT_RESOLVED|TIMED_OUT)',text):return 'NETWORK_UNAVAILABLE'
 if re.search(r'^(?:Access Denied|Forbidden|权限不足|没有访问权限|접근 권한)',text.strip(),re.I):return 'PERMISSION_DENIED'
 return None
class PagePermissionDenied(RuntimeError):
 pass

def guard(info):
 code=page_error(info)
 if code:
  error=PagePermissionDenied if code=='PERMISSION_DENIED' else RuntimeError
  raise error(code+' ('+urlsplit(info.get('url',''))._replace(query='',fragment='').geturl()+'): 页面不可用于下载；保留断点，正常恢复登录、验证码、权限或网络后续跑，不重放下载点击')
def invoke(args):
 for attempt in range(3):
  result=subprocess.run([executable(),*args],capture_output=True,text=True,encoding='utf-8',timeout=50,shell=False)
  if not (result.returncode and 'ERR_SOCKS_CONNECTION_FAILED' in (result.stdout+result.stderr) and args[:2] in (['page','visit'],['page','exec']) and attempt<2):break
  time.sleep(5)
 if result.returncode and args[:2] in (['page','visit'],['page','exec']) and 'xauth.coupang.com' in (result.stdout+result.stderr):raise RuntimeError('LOGIN_REQUIRED: Wing跳转到官方登录页，请在当前店铺正常登录后续跑；未开始下载')
 if result.returncode and args!=['doctor']:raise RuntimeError('ZINIAO_COMMAND_FAILED: '+redact(result.stderr or result.stdout)[:500])
 return result.stdout+('\n'+result.stderr if args==['doctor'] else '')
def doctor():
 text=re.sub(r'\x1b\[[0-9;]*m','',invoke(['doctor']))
 if '未找到配置文件' in text:
  raise RuntimeError('ZINIAO_NOT_READY: '+FIRST_USE)
 if not all(marker in text for marker in ('API Key 有效','客户端登录用户','Bridge 连通正常')) or any(mark in text.split('检查 Skills')[0] for mark in ('✗','✕','❌')) or re.search(r'CLIENT_LOGGED_OUT|CLIENT_ACCOUNT_MISMATCH|API Key (?:无效|失效)|账[号户]不(?:一致|匹配)',text):raise RuntimeError('ZINIAO_NOT_READY: API Key、客户端登录或Bridge未通过检查；请运行ziniao-cli doctor核对，不开始下载')
 account=re.search(r'客户端登录用户[:：]\s*([^\r\n]+)',text)
 return {'client_account':account.group(1).strip() if account else None,'api_key_valid':True,'client_logged_in':True,'bridge_connected':True,'terminal_binding_verified':'不支持终端绑定' not in text}
def store_items():
 d=json.loads(invoke(['store','list','--all','--format','json']))
 if not d.get('ok'):raise RuntimeError('STORE_LIST_FAILED: 未取得授权店铺列表')
 data=d.get('data',{});items=data.get('items',[]) if isinstance(data,dict) else data
 if not isinstance(items,list):raise RuntimeError('STORE_LIST_FORMAT_CHANGED: 停止猜测店铺')
 return [{'browser_id':str(r['storeId']),'store_name':r.get('storeName',''),'platform':r.get('platformName','')} for r in items if r.get('storeId')]
def stores():return {'status':'complete','environment':doctor(),'stores':store_items(),'selection':'使用--store-id数字ID及对应独立output-root；不按列表顺序选择'}
def unpack(text):
 d=json.loads(text)
 if not d.get('ok'):raise RuntimeError('PAGE_READ_FAILED')
 while isinstance(d,dict) and 'result' not in d and isinstance(d.get('data'),dict):d=d['data']
 if d.get('exceptionDetails') or 'result' not in d:raise RuntimeError('PAGE_READ_FAILED: 页面结构或读取能力异常')
 return json.loads(d['result']) if isinstance(d['result'],str) else d['result']
def check(root,profile,require_wing=False):
 progress('正在核验紫鸟连接、登录状态和店铺身份')
 report={'status':'running','store_id':profile['store_id'],'browser_id':profile['browser_id'],'checked_at':datetime.now().astimezone().isoformat()};file=Path(root)/'state/preflight/latest.json';file.parent.mkdir(parents=True,exist_ok=True)
 try:
  checkpoint=Path(root)/'state/collection_run.json'
  if checkpoint.is_file() and json.loads(checkpoint.read_text()).get('store_id',profile['store_id'])!=profile['store_id']:raise RuntimeError('STORE_CHECKPOINT_MISMATCH: 原采集断点属于其他店铺，不开始访问')
  report['environment']=doctor();items=[r for r in store_items() if r['browser_id']==profile['browser_id']]
  if len(items)!=1 or items[0]['store_name']!=profile['store_name']:raise RuntimeError('STORE_ACCOUNT_MISMATCH: 授权店铺ID或名称与本地绑定不一致')
  report['account']=items[0];navigated=False
  deadline=time.monotonic()+20
  while True:
   info=unpack(invoke(['page','exec','--store-id',profile['browser_id'],'--script','JSON.stringify('+PAGE+')']))
   report['wing_page']={'title':redact(info.get('title','')),'url':urlsplit(info.get('url',''))._replace(query='',fragment='').geturl()};guard(info)
   host=urlsplit(info.get('url','')).hostname
   wing=host=='wing.coupang.com' and profile['expected_title'] in info.get('title','')
   merchant=profile.get('ads_identity')
   ads=host=='advertising.coupang.com' and merchant and re.search(r'(?<![A-Za-z0-9])'+re.escape(merchant)+r'(?![A-Za-z0-9])',info.get('text',''))
   if wing or (ads and not require_wing):break
   if not navigated and (host not in ('wing.coupang.com','advertising.coupang.com') or (require_wing and host=='advertising.coupang.com')):
    invoke(['page','visit','--store-id',profile['browser_id'],'--url','https://wing.coupang.com/']);navigated=True;deadline=time.monotonic()+20;continue
   if time.monotonic()>=deadline:raise RuntimeError('STORE_PAGE_UNVERIFIED: 当前页面未能确认本店身份，停止下载；不反复返回首页')
   time.sleep(.5)
  report.update(status='complete',identity_verified=True,wing_identity_verified=bool(wing),page_verification='navigated_to_wing_once' if navigated else 'reused_current_page',merchant_verification='本店广告商家代码已核验' if ads else '广告页面另行核验本店merchant_code')
 except Exception as exc:
  report.update(status='failed',error=redact(exc));raise
 finally:
  temp=file.with_suffix('.tmp');temp.write_text(json.dumps(report,ensure_ascii=False,indent=2));temp.replace(file)
 return report
