import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import socketserver
import sqlite3
import struct
import subprocess
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler

from media_plan import video_support, plan, derive_output_name, MODES
import guard
import platform_api
from security import content_allowed, matching_root, open_beneath
from version import VERSION, PIPELINE_VERSIONS

APP = Path(__file__).resolve().parent
VAR = Path(os.environ.get('TRIM_PKGVAR', str(APP/'dev-data')))
VAR.mkdir(parents=True, exist_ok=True)
(VAR/'jobs').mkdir(exist_ok=True)
PREFIX = '/app/video-enhance'
RUNTIME = APP/'runtime'
PYTHON = str(RUNTIME/'bin/python3')
FFMPEG = str(RUNTIME/'bin/ffmpeg')
FFPROBE = str(RUNTIME/'bin/ffprobe')
DB = VAR/'tasks.sqlite'
LOCK = threading.RLock()
PROBE_SLOTS = threading.BoundedSemaphore(2)
STOP = threading.Event()
CURRENT = None
SECRET = secrets.token_bytes(32)
EXTS = {'.mp4','.mkv','.mov','.avi','.m4v','.ts'}

def db():
    c=sqlite3.connect(DB, timeout=10);c.row_factory=sqlite3.Row;return c

def request_identity(data,uid):
    key=data.get('request_id')
    if key is None:return uuid.uuid4().hex,None
    if not isinstance(key,str) or len(key)!=64 or any(c not in '0123456789abcdef' for c in key):raise ValueError('提交标识非法')
    jid=hashlib.sha256(f'{uid}:{key}'.encode()).hexdigest()[:32]
    payload={k:v for k,v in data.items() if k!='request_id'}
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    return jid,digest

def initialize():
    (VAR/'demo').mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, uid INTEGER, spec TEXT, state TEXT, detail TEXT, created REAL)')
        rows=c.execute("SELECT id,spec FROM jobs WHERE state IN ('running','queued','held')").fetchall()
        for row in rows:
            modern=json.loads(row['spec']).get('pipeline_version') in PIPELINE_VERSIONS
            c.execute('UPDATE jobs SET state=?,detail=? WHERE id=?',
                      ('held' if modern else 'interrupted','等待检查点恢复' if modern else '旧版流水线任务无可靠检查点，请重新提交',row['id']))

def update(jid, state, detail=''):
    with db() as c:c.execute('UPDATE jobs SET state=?,detail=? WHERE id=?',(state,detail,jid))

def runtime_env():
    e=dict(os.environ)
    e['PYTHONHOME']=str(RUNTIME)
    e['PYTHONPATH']=str(RUNTIME/'lib/python3.11/site-packages')
    e['LD_LIBRARY_PATH']=str(RUNTIME/'lib/native')
    e['OMP_NUM_THREADS']='2';e['OPENBLAS_NUM_THREADS']='2'
    # worker不需要飞牛API token，避免模型进程继承凭据。
    e.pop('TRIM_API_TOKEN',None)
    return e

def roles():
    try:return json.loads((VAR/'directories.json').read_text())
    except FileNotFoundError:return {}

def authorized_roots(uid, write=False):
    system=[str(Path(p)) for p in platform_api.shared_roots() if content_allowed(p)]
    configured=roles()
    # 飞牛系统 API 升级后可能清空；应用内已配置的目录首次授权时已验证，可独立作为候选。
    role_ok=('output','both') if write else ('input','both')
    candidates=list(dict.fromkeys(system + [p for p in configured if content_allowed(p)]))
    selected=[p for p in candidates if configured.get(p,'input') in role_ok]
    # 自带自测源位于应用私有目录：读写不经飞牛授权；用户成果一律落回原片目录。
    demo=str(VAR/'demo')
    try: Path(demo).mkdir(parents=True,exist_ok=True)
    except OSError: pass
    selected.append(demo)
    allowed=[]
    for p in dict.fromkeys(selected):
        try:
            app_acl(uid,p,write)
            fd=open_beneath(p,p,directory=True);os.close(fd);allowed.append(p)
        except (PermissionError, OSError):pass
    return allowed

def app_acl(uid,path,write=False):
    # 应用私有 VAR 区（自带自测源/工作区）由本应用自管权限，不查飞牛 ACL。
    try:
        Path(path).relative_to(VAR);return
    except ValueError:pass
    platform_api.check_acl(uid,path,write)

def input_fd(uid,path):
    if Path(path).suffix.lower() not in EXTS or not content_allowed(path):
        raise PermissionError('文件类型或目录不允许')
    root=matching_root(path,authorized_roots(uid))
    app_acl(uid,path)
    return open_beneath(root,path)

def output_directory(uid,source):
    output=str(Path(source).parent)
    try:
        matching_root(output,authorized_roots(uid,True));app_acl(uid,output,True)
    except (PermissionError,OSError):
        raise PermissionError('成品保存在原片目录；请将该目录用途设为“输入 + 输出”，并确认飞牛写入权限') from None
    check_output_write(uid,output)
    return output

def check_output_write(uid,output):
    # 续跑保留既有输出位置，但执行与新任务相同的授权和应用账号检查。
    root=matching_root(output,authorized_roots(uid,True))
    app_acl(uid,output,True)
    fd=open_beneath(root,output,directory=True)
    try:
        # 系统授权不等于Linux ACL允许应用账号写入；与worker使用相同有效身份。
        if not os.access('.',os.W_OK|os.X_OK,dir_fd=fd,effective_ids=True):
            raise PermissionError('飞牛已授权，但应用账号无法写入成品目录；请在飞牛文件管理中检查该目录的应用写入权限')
    finally:os.close(fd)

def probe_fd(fd):
    if not PROBE_SLOTS.acquire(blocking=False):raise RuntimeError('同时探测已达2个，请稍后重试')
    try:return _probe_fd(fd)
    finally:PROBE_SLOTS.release()

def _probe_fd(fd):
    p=subprocess.run([FFPROBE,'-v','error','-show_streams','-show_format','-of','json',f'/proc/self/fd/{fd}'],
                     capture_output=True,text=True,pass_fds=(fd,),timeout=25,env=runtime_env(),check=True)
    raw=json.loads(p.stdout)
    v=next(s for s in raw['streams'] if s['codec_type']=='video' and not s.get('disposition',{}).get('attached_pic'))
    a,b=map(int,v['avg_frame_rate'].split('/'))
    return dict(width=v['width'],height=v['height'],fps=a/b if b else 0,duration=float(raw['format'].get('duration',0)),
                hdr=v.get('color_transfer') in ('smpte2084','arib-std-b67') or any('dovi' in str(x).lower() for x in v.get('side_data_list',[])),
                unsupported_reasons=video_support(v),
                audio_tracks=sum(s['codec_type']=='audio' for s in raw['streams']),
                subtitle_tracks=sum(s['codec_type']=='subtitle' for s in raw['streams']))

def scheduler():
    global CURRENT
    while not STOP.wait(3):
        with db() as c:
            items=c.execute("SELECT * FROM jobs WHERE state IN ('queued','held') ORDER BY created").fetchall()
        for row in items:
            if STOP.is_set():return
            spec=json.loads(row['spec']);jid=row['id'];fds=[]
            try:
                # 源、输出权限每次开工重查，不把提交时授权永久缓存。
                fd=input_fd(row['uid'],spec['source_path']);fds.append(fd)
                current_stat=os.fstat(fd)
                if current_stat.st_size!=spec['source_size'] or current_stat.st_mtime_ns!=spec['source_mtime_ns']:
                    raise RuntimeError('源文件在提交后发生变化，可能仍在下载；请稳定后重新提交')
                out=spec['output_dir'];root=matching_root(out,authorized_roots(row['uid'],write=True))
                app_acl(row['uid'],out,True)
                ofd=open_beneath(root,out,directory=True);fds.append(ofd)
                # 工作文件（分片/检查点）放应用私有目录，成果目录只落最终成品一个文件。
                workdir=VAR/'work'/jid;workdir.mkdir(parents=True,exist_ok=True)
                wfd=os.open(str(workdir),os.O_DIRECTORY|os.O_RDONLY|os.O_NOFOLLOW);fds.append(wfd)
                if spec['device']=='cuda':
                    g=next((g for g in guard.snapshot()['gpus'] if g['uuid']==spec['gpu_uuid']),None)
                    policy=guard.policy()
                    if spec['gpu_uuid'] not in policy.get('validated_shared_gpus',[])+policy.get('validated_dedicated_gpus',[]):
                        if not(spec['gpu_uuid'] in policy.get('pilot_gpus',[]) and time.time()<float(policy.get('pilot_expires_at',0)) and min(spec['source_duration'],spec['preview_seconds'] if spec['preview'] else spec['source_duration'])<=8):
                            update(jid,'held','共享验收未通过；限时测试只允许8秒以内样片');continue
                    if not g or not g['eligible']:
                        update(jid,'held',g['reason'] if g else 'GPU监测不可用');continue
                spec.update(source_fd=fd,output_fd=ofd,work_fd=wfd,ffmpeg=FFMPEG,ffprobe=FFPROBE,
                            progress_path=str(VAR/'jobs'/f'{jid}.progress.json'))
                work=VAR/'jobs'/f'{jid}.json';work.write_text(json.dumps(spec,ensure_ascii=False));work.chmod(0o600)
                if spec.get('pipeline_version') not in PIPELINE_VERSIONS:
                    raise RuntimeError('旧版任务不允许续跑，请重新提交')
                logfile=VAR/'jobs'/f'{jid}.log'
                with LOCK:
                    with db() as c:state=c.execute('SELECT state FROM jobs WHERE id=?',(jid,)).fetchone()['state']
                    if state not in ('queued','held'):continue
                    log=open(logfile,'ab')
                    full=bool(guard.policy().get('full_power'))
                    argv=([PYTHON,str(APP/'worker.py'),'--spec',str(work)] if full
                          else ['nice','-n','19','ionice','-c','3',PYTHON,str(APP/'worker.py'),'--spec',str(work)])
                    p=subprocess.Popen(argv,
                                       stdout=log,stderr=log,pass_fds=tuple(fds),env=runtime_env(),start_new_session=True)
                    CURRENT=(jid,p);update(jid,'running','正在执行'+('CPU缩小尺寸功能预览' if spec['device']=='cpu' else 'GPU任务'))
                reason=''
                while p.poll() is None:
                    if STOP.wait(2):reason='应用停止';break
                    with db() as c:state=c.execute('SELECT state FROM jobs WHERE id=?',(jid,)).fetchone()['state']
                    if state in ('cancelled','paused'):reason='用户取消/暂停';break
                    try:
                        app_acl(row['uid'],spec['source_path'])
                        app_acl(row['uid'],out,True)
                        matching_root(spec['source_path'],authorized_roots(row['uid']))
                        matching_root(out,authorized_roots(row['uid'],True))
                    except Exception:reason='权限已撤销或授权监测失联';break
                    if spec['device']=='cuda':
                        try:guard.require_gpu(spec['gpu_uuid'],p.pid)
                        except Exception as e:reason=str(e);break
                if p.poll() is None:
                    os.killpg(p.pid,signal.SIGTERM)
                    try:p.wait(timeout=5)
                    except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
                log.close()
                with LOCK:CURRENT=None
                with db() as c:state=c.execute('SELECT state FROM jobs WHERE id=?',(jid,)).fetchone()['state']
                if state in ('cancelled','paused'):pass
                elif reason:update(jid,'held',reason+'；保留检查点等待恢复')
                elif p.returncode==75:update(jid,'held','增强让步，已提交分片保留，等待空闲恢复')
                elif p.returncode==0:
                    try:pr=json.loads((VAR/'jobs'/f'{jid}.progress.json').read_text())
                    except Exception:pr={}
                    actual=(f"{pr.get('width')}×{pr.get('height')} @ {pr.get('fps')}fps" if pr.get('width') else '输出')
                    fdur=pr.get('duration_seconds')
                    fdur=f'，时长 {fdur:g} 秒' if fdur else ''
                    if spec['preview'] and spec['device']=='cpu':
                        detail=f'功能预览通过检查：仅处理 {spec["preview_seconds"]} 秒片段（{actual}{fdur}）。CPU预览先将画面降至320宽再AI超分，分辨率可能与原片持平属正常；验收通过后可提交GPU整片。'
                    elif spec['preview']:
                        detail=f'原尺寸样片通过检查：仅处理 {spec["preview_seconds"]} 秒片段（{actual}{fdur}）。验收通过后可提交GPU整片。'
                    else:
                        detail=f'整片增强通过检查：{actual}{fdur}，音轨字幕原样复制。'
                    update(jid,'completed',detail)
                else:
                    error=logfile.read_text(errors='replace')[-1500:]
                    update(jid,'failed',error or '处理进程失败')
            except Exception as e:
                update(jid,'failed',str(e)[:1000])
            finally:
                for fd in fds:os.close(fd)
                CURRENT=None
            # 一次只执行一个任务，避免显存和CPU堆叠。
            break

class Handler(BaseHTTPRequestHandler):
    server_version='VideoEnhance/'+VERSION
    def log_message(self, fmt, *args):
        # 不记录请求查询串，防止文件路径泄漏。
        print(time.strftime('%F %T'),self.command,urllib.parse.urlsplit(self.path).path,flush=True)
    def send(self,code,obj):
        data=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data)
    def auth(self):
        # 网关root账户接入，其他本地账户不能伪造可信Header。
        creds=self.connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12)
        _,peer_uid,_=struct.unpack('3i',creds)
        if peer_uid!=0:
            raise PermissionError('仅接受系统网关连接')
        uid=self.headers.get('X-Trim-Userid','')
        if not uid.isdigit():
            raise PermissionError('仅允许已登录的飞牛用户')
        return int(uid)
    def csrf(self,uid):
        return hmac.new(SECRET,str(uid).encode(),hashlib.sha256).hexdigest()
    def json_body(self):
        if 'application/json' not in self.headers.get('Content-Type',''):
            raise ValueError('请求必须为JSON')
        n=int(self.headers.get('Content-Length','0'))
        if not 0<n<=16384:raise ValueError('请求大小不允许')
        return json.loads(self.rfile.read(n))
    def handle_error(self,e):
        self.send(403 if isinstance(e,PermissionError) else 400,{'error':str(e)[:1500]})
    def do_GET(self):
        try:
            uid=self.auth();u=urllib.parse.urlsplit(self.path);path=u.path
            if not path.startswith(PREFIX+'/') and path!=PREFIX:
                return self.send(404,{'error':'路由不存在'})
            route=path[len(PREFIX):];q=urllib.parse.parse_qs(u.query)
            if route=='/api/status':
                return self.send(200,dict(version=VERSION,user_uid=uid,csrf=self.csrf(uid),gpu=guard.snapshot(),
                           execution_policy=('全功率：占空/优先级让步已解除，仅温度与显存余量闸门生效' if guard.policy().get('full_power') else '推理优先共享；P95增幅≤5%经实际对照通过后开放'),demo_dir=str(VAR/'demo'),
                           dependency_profile='Python3.11 / torch2.5.1+cu121 / RIFE4.26 / RealESRGAN-general-x4v3',
                           capabilities=['手动选片','独立插帧','独立超分','超分+插帧单任务双增强','成品直接产出在原片旁（随源命名，不建目录）','CPU缩小功能预览','任务取消','暂停与校验续跑','历史清理','原片只读'],
                           limits=['SDR/CFR','HDR/DV不支持','共享生产验收未通过时保持排队','兼容范围仅当前fnOS x86与P100运行包']))
            if route=='/api/directories':
                _all=list(dict.fromkeys(platform_api.shared_roots()+list(roles().keys())))
                return self.send(200,dict(shared=[p for p in _all if content_allowed(p)],roles=roles(),
                                              inputs=authorized_roots(uid),outputs=authorized_roots(uid,True)))
            if route=='/api/browse':
                target=q.get('path',[''])[0];root=matching_root(target,authorized_roots(uid))
                platform_api.check_acl(uid,target);fd=open_beneath(root,target,True)
                try:
                    entries=[]
                    for name in os.listdir(fd):
                        p=str(Path(target)/name)
                        if not content_allowed(p) or name.startswith('.'):continue
                        st=os.stat(name,dir_fd=fd,follow_symlinks=False)
                        import stat
                        isdir=stat.S_ISDIR(st.st_mode)
                        if stat.S_ISLNK(st.st_mode) or not(isdir or Path(name).suffix.lower() in EXTS):continue
                        entries.append(dict(name=name,path=p,directory=isdir,size=st.st_size,mtime=st.st_mtime))
                    if len(entries)>1000:entries=entries[:1000]
                    # 批量逐项ACL检查，不能仅以父目录可读推断子文件可读。
                    acl=platform_api.call('trim.file.checkUserACL',dict(uid=uid,path=[e['path'] for e in entries])) if entries else []
                    readable={x['path'] for x in acl if x.get('readable')}
                    entries=[e for e in entries if e['path'] in readable]
                    return self.send(200,dict(path=target,root=root,entries=sorted(entries,key=lambda x:(not x['directory'],x['name']))))
                finally:os.close(fd)
            if route=='/api/probe':
                fd=input_fd(uid,q.get('path',[''])[0])
                try:return self.send(200,probe_fd(fd))
                finally:os.close(fd)
            if route=='/api/preflight':
                fd=input_fd(uid,q.get('path',[''])[0])
                try:info=probe_fd(fd)
                finally:os.close(fd)
                result=plan(info,q.get('mode',['interpolate'])[0],int(q.get('fps',['60'])[0]),int(q.get('scale',['2'])[0]))
                source=q.get('path',[''])[0];output=str(Path(source).parent)
                try:output_directory(uid,source);writable=True
                except PermissionError as error:result['reasons'].append(str(error));result['supported']=False;writable=False
                return self.send(200,dict(source=info,output_dir=output,output_writable=writable,**result))
            if route=='/api/jobs':
                with db() as c:rows=c.execute('SELECT * FROM jobs WHERE uid=? ORDER BY created DESC LIMIT 100',(uid,)).fetchall()
                result=[]
                for r in rows:
                    j=dict(r);j['spec']=json.loads(j['spec'])
                    # 续跑资格由后端判定，前端不复制版本串。
                    j['resume_capable']=j['spec'].get('pipeline_version') in PIPELINE_VERSIONS and j['state'] in ('paused','held','failed','cancelled','interrupted')
                    try:j['progress']=json.loads((VAR/'jobs'/f"{j['id']}.progress.json").read_text())
                    except Exception:j['progress']={}
                    result.append(j)
                return self.send(200,result)
            if route=='/api/output':
                jid=q.get('id',[''])[0]
                with db() as c:r=c.execute('SELECT * FROM jobs WHERE id=? AND uid=?',(jid,uid)).fetchone()
                if not r or r['state']!='completed':raise PermissionError('任务无可用成果')
                spec=json.loads(r['spec'])
                filename=spec['output_name'];out=str(Path(spec['output_dir'])/filename)
                root=matching_root(spec['output_dir'],authorized_roots(uid,True));app_acl(uid,out)
                fd=open_beneath(root,out)
                try:
                    size=os.fstat(fd).st_size;self.send_response(200)
                    self.send_header('Content-Type','video/x-matroska');self.send_header('Content-Length',str(size))
                    self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+urllib.parse.quote(filename))
                    self.end_headers()
                    with os.fdopen(fd,'rb',closefd=False) as f:
                        while True:
                            chunk=f.read(1024*256)
                            if not chunk:break
                            self.wfile.write(chunk)
                finally:os.close(fd)
                return
            name='index.html' if route in ('','/') else route.lstrip('/')
            if name not in ('index.html','app.js','style.css','callback.html','icon.svg'):
                return self.send(404,{'error':'路由不存在'})
            data=(APP/'www'/name).read_bytes();self.send_response(200)
            self.send_header('Content-Type',(mimetypes.guess_type(name)[0] or 'application/octet-stream')+'; charset=utf-8')
            self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-cache')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'")
            self.end_headers();self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):pass
        except Exception as e:self.handle_error(e)
    def do_POST(self):
        try:
            uid=self.auth()
            if not hmac.compare_digest(self.headers.get('X-VE-CSRF',''),self.csrf(uid)):
                raise PermissionError('请求验证失败，请刷新页面')
            if self.headers.get('Sec-Fetch-Site')=='cross-site':raise PermissionError('不允许跨站请求')
            route=urllib.parse.urlsplit(self.path).path
            data=self.json_body()
            if route==PREFIX+'/api/directories':
                path=data.get('path','');role=data.get('role')
                if role not in ('input','output','both','disabled'):raise ValueError('目录用途不允许')
                # 已配置目录（首次授权时已验证）允许重新设置用途；新路径须飞牛系统 API 确认。
                if path not in platform_api.shared_roots() and path not in roles():
                    raise PermissionError('请先使用飞牛目录授权')
                if not content_allowed(path):
                    raise PermissionError('请先使用飞牛目录授权')
                platform_api.check_acl(uid,path,role in ('output','both'))
                fd=open_beneath(path,path,True);os.close(fd)
                with LOCK:
                    r=roles();r[path]=role;tmp=VAR/'directories.tmp';tmp.write_text(json.dumps(r,ensure_ascii=False));tmp.replace(VAR/'directories.json')
                return self.send(200,{'ok':True})
            if route==PREFIX+'/api/jobs':
                jid,request_hash=request_identity(data,uid)
                with db() as c:existing=c.execute('SELECT spec FROM jobs WHERE id=? AND uid=?',(jid,uid)).fetchone()
                if existing:
                    if json.loads(existing['spec']).get('request_hash')!=request_hash:raise ValueError('同一提交标识不能更改参数')
                    return self.send(200,{'id':jid,'reused':True})
                mode=data.get('mode');device=data.get('device','cuda');preview=data.get('preview',True)
                if mode not in MODES or device not in ('cpu','cuda') or not isinstance(preview,bool):
                    raise ValueError('任务参数不允许')
                if device=='cpu' and not preview:raise ValueError('CPU仅允许缩小尺寸功能预览，不支持整片')
                fd=input_fd(uid,data.get('path',''))
                try:
                    info=probe_fd(fd);st=os.fstat(fd)
                finally:os.close(fd)
                if info['unsupported_reasons']:raise ValueError('；'.join(info['unsupported_reasons']))
                fps=int(data.get('target_fps',60));scale=int(data.get('scale',2));start=float(data.get('start_seconds',0))
                if fps not in (48,50,60) or scale not in (2,4) or not 0<=start<info['duration']:
                    raise ValueError('帧率、倍率或起点不允许')
                if not preview and start!=0:raise ValueError('整片任务起点必须为0；指定起点请使用样片模式')
                if device=='cuda' and data.get('gpu_uuid','') not in {g['uuid'] for g in guard.snapshot()['gpus']}:raise ValueError('所选GPU不可用，请刷新后重新选择')
                decision=plan(info,mode,fps,scale)
                if not decision['supported']:raise ValueError('；'.join(decision['reasons']))
                # 成果落位固定为原片所在目录（随源命名）；该目录须为“输入 + 输出”用途。
                output=output_directory(uid,data['path'])
                name=derive_output_name(data['path'],mode,scale,fps)
                if device=='cpu':name='CPU功能预览-'+name
                elif preview:name='样片-'+name
                if os.statvfs(output).f_bavail*os.statvfs(output).f_frsize<max(2*1024**3,st.st_size*2 if not preview else 0):
                    raise ValueError('输出空间不足：整片至少预留原片两倍且不低于2GiB')
                spec=dict(request_hash=request_hash,pipeline_version=VERSION,source_duration=info['duration'],chunk_frames=48,id=jid,source_path=data['path'],source_size=st.st_size,source_mtime_ns=st.st_mtime_ns,
                          output_dir=output,output_name=name,
                          mode=mode,device=device,preview=preview,target_fps=fps,scale=scale,
                          start_seconds=start,preview_seconds=2 if device=='cpu' else 10,gpu_uuid=data.get('gpu_uuid',''))
                with db() as c:
                    if c.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','held','running')").fetchone()[0]>=50:
                        raise ValueError('待处理任务已达50个')
                    try:c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)',(jid,uid,json.dumps(spec,ensure_ascii=False),'queued','等待调度',time.time()))
                    except sqlite3.IntegrityError:
                        existing=c.execute('SELECT spec FROM jobs WHERE id=? AND uid=?',(jid,uid)).fetchone()
                        if not existing or json.loads(existing['spec']).get('request_hash')!=request_hash:raise
                        return self.send(200,{'id':jid,'reused':True})
                return self.send(201,{'id':jid})
            if route in (PREFIX+'/api/cancel',PREFIX+'/api/pause',PREFIX+'/api/resume'):
                with LOCK:
                    with db() as c:
                        r=c.execute('SELECT * FROM jobs WHERE id=? AND uid=?',(data.get('id'),uid)).fetchone()
                        if not r:raise PermissionError('任务不存在')
                        action=route.rsplit('/',1)[-1]
                        if action=='resume':
                            if r['state'] not in ('paused','held','interrupted','cancelled','failed'):raise ValueError('当前状态不能续跑')
                            spec=json.loads(r['spec'])
                            if spec.get('pipeline_version') not in PIPELINE_VERSIONS:raise ValueError('旧版任务无检查点，请重新提交')
                            fd=input_fd(uid,spec['source_path'])
                            try:
                                st=os.fstat(fd)
                                if st.st_size!=spec['source_size'] or st.st_mtime_ns!=spec['source_mtime_ns']:raise ValueError('原片发生变化，请重新提交')
                            finally:os.close(fd)
                            check_output_write(uid,spec['output_dir'])
                            state,detail='queued','校验检查点后续跑'
                        else:
                            if r['state'] not in ('queued','held','running','paused'):raise ValueError('当前状态不能暂停/取消')
                            state='paused' if action=='pause' else 'cancelled';detail='已提交分片保留；可手动续跑，未完成文件不作为成果'
                        c.execute('UPDATE jobs SET state=?,detail=? WHERE id=?',(state,detail,r['id']))
                return self.send(200,{'ok':True})
            if route==PREFIX+'/api/delete':
                # 只清理任务记录与应用私有工作目录；成果文件永远保留在成果目录。
                with LOCK:
                    with db() as c:
                        r=c.execute('SELECT * FROM jobs WHERE id=? AND uid=?',(data.get('id'),uid)).fetchone()
                        if not r:raise PermissionError('任务不存在')
                        if r['state'] in ('queued','held','running','paused'):raise ValueError('活动任务请先取消再删除')
                        c.execute('DELETE FROM jobs WHERE id=?',(r['id'],))
                shutil.rmtree(VAR/'work'/r['id'],ignore_errors=True)
                for suffix in ('.json','.log','.progress.json'):
                    try:(VAR/'jobs'/(r['id']+suffix)).unlink()
                    except FileNotFoundError:pass
                return self.send(200,{'ok':True})
            return self.send(404,{'error':'路由不存在'})
        except Exception as e:self.handle_error(e)

class Server(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):
    daemon_threads=True

def main():
    initialize()
    monitor=guard.Monitor();monitor.start()
    socket_path=Path(os.environ.get('TRIM_APPDEST',str(APP)))/'app.sock'
    if socket_path.exists():socket_path.unlink()
    srv=Server(str(socket_path),Handler);os.chmod(socket_path,0o600)
    thread=threading.Thread(target=scheduler,daemon=True);thread.start()
    def stop(*_):
        STOP.set()
        with LOCK:
            if CURRENT:
                try:os.killpg(CURRENT[1].pid,signal.SIGTERM)
                except ProcessLookupError:pass
        threading.Thread(target=srv.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:srv.serve_forever(poll_interval=.3)
    finally:
        STOP.set();thread.join(timeout=8);monitor.close();srv.server_close();socket_path.unlink(missing_ok=True)

if __name__=='__main__':main()
