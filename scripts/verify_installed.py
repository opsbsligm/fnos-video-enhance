"""在NAS以root验收本应用Unix接口；不导出系统令牌或用户会话。"""
import http.client,json,socket,time,pathlib,subprocess
SOCKET='/var/apps/video-enhance/target/app.sock';BASE='/app/video-enhance'
class Client(http.client.HTTPConnection):
 def __init__(self):super().__init__('localhost',timeout=35)
 def connect(self):self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(35);self.sock.connect(SOCKET)
csrf=''
def request(path,body=None,admin=True,authenticated=True):
 c=Client();headers={}
 if authenticated:headers.update({'X-Trim-Userid':'1000','X-Trim-Isadmin':'true' if admin else 'false'})
 if body is not None:headers.update({'Content-Type':'application/json','X-VE-CSRF':csrf})
 c.request('POST' if body is not None else 'GET',BASE+path,json.dumps(body) if body is not None else None,headers)
 r=c.getresponse();payload=r.read();code=r.status;c.close()
 try:return code,json.loads(payload)
 except Exception:return code,{'bytes':len(payload)}
results={}
code,j=request('/api/status',authenticated=False);assert code==403;results['unauthenticated_rejected']=code
code,j=request('/api/status',admin=False);assert code==200;results['nonadmin_accepted_031']=code
code,status=request('/api/status');assert code==200;csrf=status['csrf'];results['status_version']=status['version']
code,dirs=request('/api/directories');assert code==200;results['directory_api']=dirs
source=status['demo_dir']+'/功能自测.mp4'
code,info=request('/api/probe?path='+__import__('urllib.parse').parse.quote(source));assert code==200,(code,info);results['source_probe']=info
code,j=request('/api/browse?path=/etc');assert code==403;results['path_escape_rejected']=code
ids=[]
for mode in ['interpolate','upscale']:
 code,j=request('/api/jobs',{'path':source,'mode':mode,'device':'cpu','preview':True,'scale':2,'target_fps':60,'start_seconds':0})
 assert code==201,(code,j);ids.append(j['id'])
if status['gpu']['gpus']:
 code,j=request('/api/jobs',{'path':source,'mode':'interpolate','device':'cuda','gpu_uuid':status['gpu']['gpus'][0]['uuid'],'preview':True,'target_fps':60,'scale':2})
 assert code==201;gpu_id=j['id']
else:gpu_id=None
for _ in range(80):
 time.sleep(3);code,jobs=request('/api/jobs');assert code==200
 states={j['id']:j['state'] for j in jobs};print('states', {i:states.get(i) for i in ids},flush=True)
 if all(states.get(i) in ('completed','failed','interrupted') for i in ids):break
else:raise RuntimeError('CPU测试超时')
for jid in ids:
 job=next(j for j in jobs if j['id']==jid);assert job['state']=='completed',job
 results[job['spec']['mode']]=dict(id=jid,state=job['state'],progress=job['progress'])
 code,data=request('/api/output?id='+jid);assert code==200;results[job['spec']['mode']]['download_bytes']=data['bytes']
if gpu_id:
 for _ in range(60):
  _,jobs=request('/api/jobs');job=next(j for j in jobs if j['id']==gpu_id)
  # 0.3.1策略：GPU未通过共享验收时保持held排队；通过验收（含P95试点）则允许直接运行完成。
  if job['state'] in ('held','completed','failed'):break
  time.sleep(2)
 assert job['state'] in ('held','completed'),job
 results['gpu_guard']=dict(id=gpu_id,state=job['state'],detail=job['detail'])
 if job['state']=='held':
  code,j=request('/api/cancel',{'id':gpu_id});assert code==200
# 验收运行中取消：退出整个进程组，不留下编码器或partial假成果。
code,j=request('/api/jobs',{'path':source,'mode':'interpolate','device':'cpu','preview':True,'scale':2,'target_fps':60});assert code==201
cancel_id=j['id']
for _ in range(30):
 time.sleep(1);_,jobs=request('/api/jobs');job=next(j for j in jobs if j['id']==cancel_id)
 if job['state']=='running':break
else:raise RuntimeError('取消测试任务未开始')
code,j=request('/api/cancel',{'id':cancel_id});assert code==200
time.sleep(7);_,jobs=request('/api/jobs');job=next(j for j in jobs if j['id']==cancel_id);assert job['state']=='cancelled'
results['running_cancel']=dict(id=cancel_id,state=job['state'])
results['gpu_after']=request('/api/status')[1]['gpu']
pathlib.Path('/vol2/@appdata/video-enhance-tests/installed-verification.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
print(json.dumps(results,ensure_ascii=False,indent=2),flush=True)
