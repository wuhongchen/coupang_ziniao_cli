"""Persistent image cache keyed by Coupang CDN path, never by SKU alone."""
import hashlib, threading, urllib.request, urllib.parse
from pathlib import Path

LIMIT=12*1024*1024

def image_type(data):
 if data.startswith(b'\x89PNG\r\n\x1a\n'):return 'image/png'
 if data.startswith(b'\xff\xd8\xff'):return 'image/jpeg'
 if data[:6] in (b'GIF87a',b'GIF89a'):return 'image/gif'
 if data.startswith(b'RIFF') and data[8:12]==b'WEBP':return 'image/webp'
 raise ValueError('Unsupported image response')

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise ValueError('Image redirects disabled')

def download(path):
 url='https://image1.coupangcdn.com/image/'+urllib.parse.quote(path,safe='/')
 with urllib.request.build_opener(NoRedirect()).open(url,timeout=20) as response:
  data=response.read(LIMIT+1)
 if len(data)>LIMIT:raise ValueError('Image exceeds cache size limit')
 return data

class ImageCache:
 def __init__(self,directory,fetch=download):
  self.directory=Path(directory);self.fetch=fetch;self.guard=threading.Lock();self.locks={}
 def get(self,path):
  if not isinstance(path,str) or not path.startswith('vendor_inventory/') or '..' in path.split('/') or any(c in path for c in ['\\','\x00','?','#']) or len(path)>1500:raise ValueError('Invalid image path')
  key=hashlib.sha256(path.encode()).hexdigest()
  with self.guard:lock=self.locks.setdefault(key,threading.Lock())
  with lock:
   target=self.directory/(key+'.img')
   if target.exists():
    data=target.read_bytes()
    try:return data,image_type(data),key,True
    except ValueError:target.unlink()
   data=self.fetch(path);kind=image_type(data)
   if len(data)>LIMIT:raise ValueError('Image exceeds cache size limit')
   self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
   temp=target.with_suffix('.tmp');temp.write_bytes(data);temp.chmod(0o600);temp.replace(target)
   return data,kind,key,False
