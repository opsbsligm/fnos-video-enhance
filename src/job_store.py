"""仅在已固定的成果目录句柄内工作；分片校验通过后才提交检查点。"""
import hashlib
from version import PIPELINE_TAG
import json
import os
from pathlib import Path
import stat
import uuid

class Store:
    def __init__(self,root_fd,jid):
        if len(jid)!=32 or any(c not in '0123456789abcdef' for c in jid):raise ValueError('任务ID非法')
        try:os.mkdir(jid,0o750,dir_fd=root_fd)
        except FileExistsError:pass
        self.fd=os.open(jid,os.O_DIRECTORY|os.O_RDONLY|os.O_NOFOLLOW,dir_fd=root_fd)
        self.path=Path(f'/proc/self/fd/{self.fd}')
    def close(self):os.close(self.fd)
    def open_read(self,name):
        self.check(name);fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=self.fd)
        if not stat.S_ISREG(os.fstat(fd).st_mode):os.close(fd);raise ValueError('非普通成果文件')
        return fd
    def read(self,name):
        fd=self.open_read(name)
        with os.fdopen(fd,'rb') as f:
            if os.fstat(f.fileno()).st_size>16*1024**2:raise ValueError('检查点过大')
            return f.read()
    def check(self,name):
        if not name or '/' in name or name in ('.','..'):raise ValueError('成果文件名非法')
    def create(self,suffix):
        name='tmp-'+uuid.uuid4().hex+suffix
        fd=os.open(name,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640,dir_fd=self.fd)
        return name,fd
    def rename(self,old,new):
        self.check(old);self.check(new)
        # 目标为本任务派生固定名字；rename不跟随目标软链接。
        os.replace(old,new,src_dir_fd=self.fd,dst_dir_fd=self.fd)
    def remove(self,name):
        self.check(name)
        try:os.unlink(name,dir_fd=self.fd)
        except FileNotFoundError:pass
    def atomic_json(self,name,data):
        tmp,fd=self.create('.json')
        try:
            with os.fdopen(fd,'w') as f:json.dump(data,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
            self.rename(tmp,name);os.fsync(self.fd)
        finally:self.remove(tmp)
    def sha(self,name):
        h=hashlib.sha256();fd=self.open_read(name)
        with os.fdopen(fd,'rb') as f:
            for b in iter(lambda:f.read(1024**2),b''):h.update(b)
        return h.hexdigest()

def fingerprint(spec):
    keys=('id','source_size','source_mtime_ns','mode','preview','target_fps','scale','start_seconds','preview_seconds')
    value={k:spec.get(k) for k in keys};value['pipeline']=PIPELINE_TAG
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()

def load_checkpoint(store,spec):
    try:data=json.loads(store.read('checkpoint.json'))
    except FileNotFoundError:return dict(version=1,fingerprint=fingerprint(spec),parts=[],frames_done=0)
    if data.get('version')!=1 or data.get('fingerprint')!=fingerprint(spec):raise ValueError('检查点不匹配当前源/参数')
    if not isinstance(data.get('parts'),list):raise ValueError('检查点分片字段非法')
    done=0
    for i,p in enumerate(data.get('parts',[])):
        if not isinstance(p,dict):raise ValueError('检查点分片格式非法')
        if p.get('name')!=f'part-{i:06d}.mkv' or p.get('start')!=done or not isinstance(p.get('frames'),int) or not 0<p['frames']<=120:
            raise ValueError('检查点分片顺序/帧数非法')
        if store.sha(p['name'])!=p['sha256']:raise ValueError('已完成分片校验和变化；拒绝续跑')
        done+=p['frames']
    if done!=data.get('frames_done'):raise ValueError('检查点计帧不一致')
    return data
