"""Match a completed Chrome download to a local file without any native dialog."""
from datetime import datetime
from pathlib import Path
import zipfile

PATTERNS={'sales_daily':'SELLER_INSIGHTS_VENDOR_ITEM_METRICS*.xlsx',
          'sales_window':'SELLER_INSIGHTS_VENDOR_ITEM_METRICS*.xlsx',
          'inventory':'inventory_health_sku_info*.xlsx','returns':'inventory_health_creturn*.xlsx',
          'movement':'inventory_reconciliation_report*.xlsx'}

def download_directories(profile):
    result=[]
    for value in profile.get('download_dirs',[]):
        base=Path(value).expanduser().resolve()
        child=base/profile['store_name']
        result.append(child if base.name=='ziniao browser' or (base.name!=profile['store_name'] and child.is_dir()) else base)
    return result

def download_snapshot(kind,directories):
    result={}
    for base in directories:
        if not base.is_dir():continue
        for path in base.rglob(PATTERNS[kind]):
            if path.is_file() and path.resolve().is_relative_to(base.resolve()):
                stat=path.stat();result[str(path.resolve())]=[stat.st_mtime_ns,stat.st_size]
    return result

def locate_fresh_export(kind,started_at,directories,before=None):
    """Fallback for brokers that return a previous Chrome event: verify a new, complete XLSX."""
    started=datetime.fromisoformat(started_at).timestamp();candidates=[]
    for filename,signature in download_snapshot(kind,directories).items():
        path=Path(filename)
        if path.stat().st_mtime<started or (before is not None and before.get(filename)==signature):continue
        try:
            with zipfile.ZipFile(path) as archive:
                if '[Content_Types].xml' not in archive.namelist() or archive.testzip() is not None:continue
            stat=path.stat()
            if [stat.st_mtime_ns,stat.st_size]!=signature:continue
            candidates.append(path)
        except (zipfile.BadZipFile,OSError):continue
    if len(candidates)>1:raise ValueError('本次导出存在多个新文件，请使用本店专用download-dir，不能猜测')
    return candidates[0] if candidates else None
