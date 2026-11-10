"""Non-secret per-output-root identity configuration for Wing exports."""
from pathlib import Path
import json
import re

def load_profile(root):
    path=Path(root)/'state/store_profile.json'
    if not path.exists():raise ValueError('尚未配置店铺，请先使用 --configure 指定 browser-id、store-name 和 expected-title')
    profile=json.loads(path.read_text(encoding='utf-8'))
    if not re.fullmatch(r'\d+',str(profile.get('browser_id',''))):raise ValueError('browser-id 必须是紫鸟店铺浏览器数字ID')
    if not str(profile.get('store_name','')).strip() or not str(profile.get('expected_title','')).strip():raise ValueError('店铺名称和页面身份标记不能为空')
    if profile.get('store_id')!='ziniao:'+profile['browser_id']:raise ValueError('store-id 与 browser-id 不一致')
    return profile

def configure_profile(root,browser_id,store_name,expected_title,download_dirs=None,ads_identity=None):
    root=Path(root).expanduser().resolve()
    profile={'schema_version':1,'browser_id':str(browser_id),'store_id':'ziniao:'+str(browser_id),
             'store_name':store_name.strip(),'expected_title':expected_title.strip()}
    if not re.fullmatch(r'\d+',profile['browser_id']) or not profile['store_name'] or not profile['expected_title']:
        raise ValueError('browser-id 必须是数字，店铺名称和页面身份标记必填')
    if ads_identity is not None:
        if not re.fullmatch(r'[A-Za-z0-9_-]+',ads_identity):raise ValueError('广告商家代码格式无效')
        profile['ads_identity']=ads_identity
    path=root/'state/store_profile.json'
    if path.exists():
        old=load_profile(root)
        if any(old[k]!=profile[k] for k in ('browser_id','store_id','store_name','expected_title')):raise ValueError('此目录已绑定其他配置，请使用新的 --output-root；不覆盖既有身份或采集断点')
        if ads_identity is not None:
            if old.get('ads_identity') not in (None,ads_identity):raise ValueError('不覆盖已有广告账户身份')
            old['ads_identity']=ads_identity
        if download_dirs is not None:
            old['download_dirs']=[str(Path(p).expanduser().resolve()) for p in download_dirs]
            tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(old,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)
        tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(old,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)
        return old
    dbpath=root/'state/report_pipeline.sqlite3'
    if dbpath.exists():
        import sqlite3
        with sqlite3.connect(dbpath) as db:
            stores={row[0] for row in db.execute('SELECT DISTINCT store FROM batches')}
        if stores and stores!={profile['store_id']}:raise ValueError('目录中的已有批次属于其他店铺，请使用新目录')
    if download_dirs is not None:profile['download_dirs']=[str(Path(p).expanduser().resolve()) for p in download_dirs]
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f:json.dump(profile,f,ensure_ascii=False,indent=2)
    return profile

def store_identity(root,store=None):
    if store is not None:
        if not re.fullmatch(r'[A-Za-z0-9:_-]+',store):raise ValueError('无效店铺标识')
        return store
    return load_profile(root)['store_id']

# Standalone exports and the ads CLI use this same per-store lock filename.
from contextlib import contextmanager,ExitStack
import tempfile,os
@contextmanager
def _file_lease(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+') as f:
        try:
            import fcntl
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except ImportError:
            import msvcrt
            f.seek(0);f.write('0');f.flush();f.seek(0)
            try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise RuntimeError('店铺浏览器已有采集任务，不重复运行')
        except BlockingIOError:raise RuntimeError('店铺浏览器已有采集任务，不重复运行')
        try:yield
        finally:
            if 'fcntl' in locals():fcntl.flock(f,fcntl.LOCK_UN)
            else:f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)

@contextmanager
def browser_lease(root):
    state=Path(root)/'state';state.mkdir(parents=True,exist_ok=True)
    with ExitStack() as locks:
        if (state/'store_profile.json').is_file():
            profile=load_profile(root)
            directory=Path(tempfile.gettempdir())/('wing-ops-browser-locks-'+str(os.getuid() if hasattr(os,'getuid') else 'local'))
            locks.enter_context(_file_lease(directory/(profile['browser_id']+'.lock')))
        locks.enter_context(_file_lease(state/'daily_workflow.lock'))
        yield
