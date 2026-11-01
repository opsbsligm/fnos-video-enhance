"""本地FPK兼容打包：沿用fnpack manifest与目录合同，gzip1避免重复最高压缩。"""
import gzip,hashlib,re,tarfile,sys
from pathlib import Path
root=Path(sys.argv[1]);out=Path(sys.argv[2]);asset=root.parent/'app-clean.tgz'
with open(asset,'wb') as raw,gzip.GzipFile(fileobj=raw,mode='wb',compresslevel=1,mtime=0) as gz,tarfile.open(fileobj=gz,mode='w|',format=tarfile.GNU_FORMAT) as t:
 for p in sorted((root/'app').rglob('*')):
  if p.is_symlink():raise RuntimeError('运行包不可含符号链接: '+str(p))
  if '__pycache__' in p.parts or p.name in ('.DS_Store','app.sock') or p.name.startswith('._'):continue
  t.add(p,arcname=str(p.relative_to(root/'app')),recursive=False)
h=hashlib.md5()
with open(asset,'rb') as f:
 for c in iter(lambda:f.read(1024**2),b''):h.update(c)
manifest=root/'manifest';text=manifest.read_text()
text=re.sub(r'^checksum\s*=.*$', '',text,flags=re.M).rstrip()+'\nchecksum = '+h.hexdigest()+'\n';manifest.write_text(text)
with open(out,'wb') as raw,gzip.GzipFile(fileobj=raw,mode='wb',compresslevel=1,mtime=0) as gz,tarfile.open(fileobj=gz,mode='w|',format=tarfile.GNU_FORMAT) as t:
 t.add(asset,arcname='app.tgz')
 for name in ('cmd','config','ICON.PNG','ICON_256.PNG','manifest','wizard'):t.add(root/name,arcname=name)
print(str(out),out.stat().st_size,flush=True)
