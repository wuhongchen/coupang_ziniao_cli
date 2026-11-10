"""Per-invocation credentials: never put API keys in argv or the store archive."""
import runtime_clock as clock
from contextlib import contextmanager
from pathlib import Path
import os,re,sys,subprocess,tempfile,shutil,getpass
_SECRET=None
def redact(value):
 text=str(value)
 for secret in (_SECRET,os.environ.get('WING_ZINIAO_REDACT_SECRET')):
  if secret:text=text.replace(secret,'[REDACTED]')
 text=re.sub(r'(https://xauth\.coupang\.com/[^\s?\"\'<>]*)(?:\?[^\s\"\'<>]*)',r'\1?[REDACTED_AUTH_QUERY]',text)
 return text
def executable():
 path=shutil.which('ziniao-cli')
 if not path:raise RuntimeError('ZINIAO_NOT_INSTALLED: 请安装ziniao-cli')
 p=Path(path)
 if p.suffix.lower() in ('.cmd','.bat'):
  native=p.parent/'node_modules/@ziniao-open/cli/bin/ziniao-cli.exe'
  if not native.is_file():raise RuntimeError('ZINIAO_NATIVE_REQUIRED: 未找到Windows原生CLI')
  return str(native)
 return path
def options(parser):
 parser.add_argument('--timezone',help='运行日期时区；省略则沿用系统本地时区')
 group=parser.add_mutually_exclusive_group()
 group.add_argument('--ziniao-api-key-env',help='保存动态API Key的环境变量名称，不能传Key明文')
 group.add_argument('--ziniao-api-key-stdin',action='store_true',help='从标准输入读取动态API Key，仅在本次隔离环境中使用')
 parser.add_argument('--ziniao-config-dir',type=Path,help='已初始化的紫鸟CLI环境配置目录')
 parser.add_argument('--store-id',help='明确选择紫鸟数字浏览器ID；必须与output-root绑定一致')
def select_store(profile,selected):
 if selected is not None and str(selected).removeprefix('ziniao:')!=profile['browser_id']:raise ValueError('STORE_BINDING_MISMATCH: 店铺ID与本地目录不一致，请为其他店铺使用独立output-root，不覆盖当前身份')
 return profile
def verify_config_isolation(directory):
 # Two harmless probes prove the installed CLI reads this directory, before any key is sent.
 marker=Path(directory)/'config.json'
 if marker.exists():raise RuntimeError('ISOLATION_PROBE_NOT_EMPTY')
 first=subprocess.run([executable(),'config','show'],capture_output=True,text=True,encoding='utf-8',timeout=20,shell=False)
 marker.write_text('{',encoding='utf-8')
 try:second=subprocess.run([executable(),'config','show'],capture_output=True,text=True,encoding='utf-8',timeout=20,shell=False)
 finally:marker.unlink()
 missing=first.returncode and '未找到配置文件' in (first.stdout+first.stderr)
 invalid=second.returncode and '配置文件格式无效' in (second.stdout+second.stderr)
 if not (missing and invalid):raise RuntimeError('CONFIG_ISOLATION_UNVERIFIED: 当前紫鸟CLI未确认隔离目录支持，未传入Key、未改动默认配置')

@contextmanager
def environment(args):
 global _SECRET
 selected_timezone=getattr(args,'timezone',None)
 if selected_timezone:
  from zoneinfo import ZoneInfo
  ZoneInfo(selected_timezone)
 old_timezone=os.environ.get('WING_TIMEZONE')
 name=getattr(args,'ziniao_api_key_env',None);stdin=getattr(args,'ziniao_api_key_stdin',False);directory=getattr(args,'ziniao_config_dir',None)
 if directory and (name or stdin):raise ValueError('请选择已有config-dir或临时API Key，不同时指定')
 if name and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',name):raise ValueError('API Key参数应是环境变量名称')
 secret=os.environ.get(name) if name else ((getpass.getpass('请输入紫鸟API Key（隐藏输入）：') if sys.stdin.isatty() else sys.stdin.readline(8193).rstrip('\r\n')) if stdin else None)
 if (name or stdin) and (not secret or len(secret)>8192):raise ValueError('API_KEY_MISSING: 未取得有效长度的API Key')
 old_dir=os.environ.get('ZINIAO_CLI_CONFIG_DIR');old_secret=os.environ.get('WING_ZINIAO_REDACT_SECRET');previous=_SECRET
 try:
  if selected_timezone:os.environ['WING_TIMEZONE']=selected_timezone
  if secret:
   _SECRET=secret;os.environ['WING_ZINIAO_REDACT_SECRET']=secret
   with tempfile.TemporaryDirectory(prefix='wing-ziniao-') as temp:
    os.chmod(temp,0o700);os.environ['ZINIAO_CLI_CONFIG_DIR']=temp
    verify_config_isolation(temp)
    result=subprocess.run([executable(),'config','init','--api-key-stdin'],input=secret+'\n',text=True,encoding='utf-8',capture_output=True,timeout=60,shell=False)
    if result.returncode:raise RuntimeError('API_KEY_INIT_FAILED: 临时认证失败，请核对Key、网络及终端绑定；未改动默认配置')
    yield
  else:
   if directory:
    path=Path(directory).expanduser().resolve()
    if not path.is_dir():raise ValueError('ZINIAO_CONFIG_MISSING: 配置目录不存在')
    with tempfile.TemporaryDirectory(prefix='wing-ziniao-probe-') as probe:
     os.environ['ZINIAO_CLI_CONFIG_DIR']=probe;verify_config_isolation(probe)
    os.environ['ZINIAO_CLI_CONFIG_DIR']=str(path)
   yield
 finally:
  _SECRET=previous
  for key,value in [('WING_TIMEZONE',old_timezone),('ZINIAO_CLI_CONFIG_DIR',old_dir),('WING_ZINIAO_REDACT_SECRET',old_secret)]:
   if value is None:os.environ.pop(key,None)
   else:os.environ[key]=value

def record_failure(root,operation,error):
 """Persist a redacted diagnostic, never a credential or a false success."""
 from datetime import datetime
 import json,uuid
 now=datetime.now(clock.timezone());path=Path(root)/'state/failures'/str(now.date())/(now.strftime('%H%M%S_')+uuid.uuid4().hex[:8]+'.json')
 path.parent.mkdir(parents=True,exist_ok=True)
 value={'status':'failed','operation':operation,'observed_at':now.isoformat(),'error':redact(error)[:1200],'download_replayed':False,'recovery':'保留原数据和断点；正常恢复后用同一命令续跑，不补零或改用其他账户'}
 path.write_text(json.dumps(value,ensure_ascii=False,indent=2));return str(path)
