import gzip,hashlib,re,sys,tarfile
from pathlib import Path
root,asset,out=map(Path,sys.argv[1:]);h=hashlib.md5()
with open(asset,'rb') as f:
 for chunk in iter(lambda:f.read(1024**2),b''):h.update(chunk)
p=root/'manifest';text=re.sub(r'^checksum\s*=.*$','',p.read_text(),flags=re.M).rstrip()+'\nchecksum = '+h.hexdigest()+'\n';p.write_text(text)
# app.tgz已压缩；外层用存储块避免对3GB重复高压缩。格式仍为gzip/tar，须由原生安装器验收。
with open(out,'wb') as raw,gzip.GzipFile(fileobj=raw,mode='wb',compresslevel=0,mtime=0) as gz,tarfile.open(fileobj=gz,mode='w|',format=tarfile.GNU_FORMAT) as t:
 t.add(asset,arcname='app.tgz')
 for name in ('cmd','config','ICON.PNG','ICON_256.PNG','manifest','wizard'):t.add(root/name,arcname=name)
print(out,out.stat().st_size,flush=True)
