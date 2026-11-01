"""沿用fnpack生成的app.tgz和校验manifest，仅修复生命周期脚本；安装器再次验收。"""
import gzip,io,sys,tarfile
from pathlib import Path
src,dst,main=map(Path,sys.argv[1:])
with tarfile.open(src,'r|gz') as inp,open(dst,'wb') as raw,gzip.GzipFile(fileobj=raw,mode='wb',compresslevel=1,mtime=0) as gz,tarfile.open(fileobj=gz,mode='w|',format=tarfile.GNU_FORMAT) as out:
 for member in inp:
  if member.name.strip('./')=='cmd/main':
   data=main.read_bytes();member.size=len(data);out.addfile(member,io.BytesIO(data))
  else:out.addfile(member,inp.extractfile(member) if member.isfile() else None)
print(dst,flush=True)
