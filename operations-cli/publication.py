"""Publish one immutable, self-contained store data revision; pointer switches last."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,sqlite3

def publish(root,base,catalog):
 from storage import read,write,sha
 if catalog.get('errors'):raise ValueError('原始归档校验有错误，不发布新工作台版本')
 store=read(base/'store.json');by_source={r['source']:r for r in catalog['files']};batches=[]
 dbfile=root/'state/report_pipeline.sqlite3'
 if dbfile.exists():
  with sqlite3.connect(dbfile.as_uri()+'?mode=ro',uri=True) as db:
   db.row_factory=sqlite3.Row
   for row in db.execute('SELECT * FROM batches WHERE store=? ORDER BY captured,id',(store['store_id'],)):
    b=dict(row);entry=by_source.get(str((root/b['file']).resolve()))
    if not entry or entry['sha256']!=b['digest']:raise ValueError('核心批次缺少已校验的新目录原始文件')
    b['file']=str(Path(entry['archive']).relative_to(base));b['payload']=json.loads(b['payload']);batches.append(b)
 metadata={}
 for key,name in [('collection','collection_run.json'),('workflow','daily_workflow.json'),('supplement_collection','supplement_run.json')]:
  path=root/'state'/name;metadata[key]=read(path) if path.exists() else {'status':'not_started'}
 data={'schema_version':1,'published_at':datetime.now(timezone.utc).isoformat(),'store':store,'catalog':catalog,'batches':batches,'products':read(base/'products.json'),'options':read(base/'options.json'),'collection_state':metadata}
 content=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode();revision=hashlib.sha256(content).hexdigest()
 target=base/'workbench/revisions'/(revision+'.json');target.parent.mkdir(parents=True,exist_ok=True)
 if target.exists():
  if sha(target)!=revision:raise ValueError('工作台发布版本已损坏，拒绝覆盖')
 else:
  tmp=target.with_suffix('.tmp');tmp.write_bytes(content);tmp.replace(target)
 write(base/'workbench/latest.json',{'schema_version':1,'store_id':store['store_id'],'revision':revision,'file':str(target.relative_to(base)),'published_at':data['published_at']})
 return {'status':'published','revision':revision,'pointer':str(base/'workbench/latest.json'),'products':len(data['products']['products']),'options':len(data['options']['options'])}
