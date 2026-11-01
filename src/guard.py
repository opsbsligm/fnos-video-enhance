"""只读采样、生产推理优先；软件让步不是硬件隔离。"""
import csv
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import http.client
from concurrent.futures import ThreadPoolExecutor

VAR = Path(os.environ.get('TRIM_PKGVAR', str(Path(__file__).parent/'dev-data')))
STATE = VAR/'monitor.json'
POLICY = VAR/'gpu-policy.json'
METRICS = ('requests_processing','requests_deferred','prompt_tokens_total','tokens_predicted_total')
# 推理监测只允许访问本机回环；主机名固定为常量，不接受任何配置项，
# 因此不存在"配置里写个别的地址就能让应用去访问内网其他服务"的路径。
METRICS_HOST = '127.0.0.1'

class ProtectedWork(RuntimeError):
    pass

def policy():
    try:
        p=json.loads(POLICY.read_text())
        if not isinstance(p,dict):raise ValueError()
        return p
    except Exception:return {}

def raw_snapshot():
    try:
        out=subprocess.check_output(['nvidia-smi','--query-gpu=uuid,name,memory.total,memory.used,temperature.gpu,utilization.gpu','--format=csv,noheader,nounits'],text=True,timeout=2)
        # 时间对应实际GPU读数，不包括之前的推理metrics等待时间。
        sampled_at=time.time()
        apps=subprocess.check_output(['nvidia-smi','--query-compute-apps=gpu_uuid,pid,process_name,used_memory','--format=csv,noheader,nounits'],text=True,timeout=2)
        ps=list(csv.reader(io.StringIO(apps),skipinitialspace=True));gpus=[]
        for row in csv.reader(io.StringIO(out),skipinitialspace=True):
            uid,name,total,used,temp,util=row
            processes=[dict(pid=int(p[1]),name=p[2],memory_mib=int(p[3])) for p in ps if len(p)>=4 and p[0]==uid]
            gpus.append(dict(uuid=uid,name=name,total_mib=int(total),used_mib=int(used),temperature=int(temp),utilization=int(util),processes=processes))
        return dict(available=bool(gpus),gpus=gpus,sampled_at=sampled_at)
    except Exception as error:return dict(available=False,gpus=[],error='GPU指标采集失败：'+type(error).__name__)

def parse_metrics(text):
    values={}
    for line in text.splitlines():
        m=re.fullmatch(r'llamacpp:([a-z_]+)(?:\{[^}]*\})?\s+([0-9.eE+\-]+)',line.strip())
        if m and m[1] in METRICS:
            values[m[1]]=values.get(m[1],0)+float(m[2])
    if any(k not in values for k in METRICS) or any(not math.isfinite(v) or v<0 for v in values.values()):
        raise ValueError('推理metrics字段缺失/非法')
    return values

def read_target(target):
    # 部署配置只允许本机回环端口，不提供任意URL/SSRF入口。
    port=int(target['port'])
    if not 1024<=port<=65535:raise ValueError('推理端口不允许')
    before=time.monotonic()
    # 用 http.client 而不是 urllib.urlopen：固定主机为回环，且**不跟随重定向**。
    # urlopen 会对被监测端返回的 3xx 再次发起请求，重定向目标由对端决定，
    # 那才是真正的出站入口；这里只要 200，其他状态一律判失败。
    conn=http.client.HTTPConnection(METRICS_HOST,port,timeout=.6)
    try:
        conn.request('GET','/metrics')
        r=conn.getresponse();status=r.status
        if status!=200:raise ValueError('metrics失败')
        body=r.read(131072).decode()
    finally:
        conn.close()
    return dict(name=str(target['name']),port=port,rpc_ms=round((time.monotonic()-before)*1000,2),**parse_metrics(body))

def evaluate(gpu,p,inference,own_pid=None,now=None):
    now=time.time() if now is None else now
    uid=gpu['uuid'];protected={int(t['pid']) for t in p.get('protected_targets',[]) if isinstance(t,dict) and 'pid' in t}
    processes=[x for x in gpu['processes'] if x['pid']!=own_pid]
    shared=uid in p.get('validated_shared_gpus',[]) or (uid in p.get('pilot_gpus',[]) and now<float(p.get('pilot_expires_at',0)))
    dedicated=uid in p.get('validated_dedicated_gpus',[])
    headroom=bool(p.get('headroom_mode'))
    if not(shared or dedicated):return '共享生产验收未通过；不会加载增强模型'
    if gpu['temperature']>=int(p.get('max_temperature',85)):return '温度保护：停止增强并释放显存'
    # 余量闸门只做启动准入（own_pid=None）；运行态把自身占存计入used会自锁死循环，
    # 运行中改由 OOM 捕获兜底转让步（下次准入自然等余量恢复）。
    if own_pid is None and gpu['total_mib']-gpu['used_mib']<int(p.get('reserve_mib',2048))+int(p.get('budget_mib',1024)):
        return '显存余量保护：停止增强并释放显存'
    if headroom:
        # 协作共享：以温度/显存余量为闸门，不要求生产进程清单与metrics监测（实际对照验收后启用）。
        if own_pid is not None:
            own=next((x for x in gpu['processes'] if x['pid']==own_pid),None)
            if int(p.get('budget_mib',1024))>0 and own and own['memory_mib']>int(p.get('budget_mib',1024))+512:return '增强显存预算超限'
        # full_power：GPU利用率不设启动闸门，与推理并发竞争、由CUDA时间片公平调度。
        if own_pid is None and not p.get('full_power') and gpu['utilization']>int(p.get('max_start_utilization',70)):return 'GPU当前忙碌，增强让步'
        return ''
    if dedicated and not shared and processes:return '独占模式发现其他GPU进程'
    if shared:
        if not protected:return '缺少已核验的生产进程清单'
        if any(x['pid'] not in protected for x in processes):return '发现未知GPU计算进程，停止增强'
        if any(pid not in {x['pid'] for x in processes} for pid in protected):return '生产模型进程变化，需重新核验'
        if not inference or not inference.get('available'):return '生产推理监测失联，停止增强'
        if now-inference.get('updated_at',0)>1.5:return '生产推理采样过期，停止增强'
        if inference.get('processing',0)>0 or inference.get('deferred',0)>0:return '生产推理有活跃/排队请求，增强让步'
        if now-inference.get('last_active_at',now)<float(p.get('idle_seconds',3)):return '等待推理空闲稳定窗口'
        if own_pid is None and gpu['utilization']>int(p.get('max_start_utilization',70)):return 'GPU当前忙碌，增强让步'
        if own_pid is not None:
            own=next((x for x in gpu['processes'] if x['pid']==own_pid),None)
            if own and own['memory_mib']>int(p.get('budget_mib',1024))+512:return '增强显存预算超限'
    return ''

def snapshot(own_pid=None):
    p=policy()
    try:
        state=json.loads(STATE.read_text())
        if time.time()-state['updated_at']>2:raise ValueError('GPU采样过期')
    except Exception:
        state=raw_snapshot();state['inference']=dict(available=False,error='监测尚未就绪')
    gpus=[]
    for original in state.get('gpus',[]):
        g=dict(original);reason=evaluate(g,p,state.get('inference'),own_pid)
        g.update(eligible=not reason,reason=reason);gpus.append(g)
    return dict(available=bool(gpus),gpus=gpus,inference=state.get('inference',{}),
                policy='inference-first-cooperative',policy_config={k:p.get(k) for k in ('budget_mib','reserve_mib','max_temperature','idle_seconds','gpu_duty','validated_shared_gpus','pilot_expires_at')},
                disclaimer='让步/节流不等于硬件隔离；只按实际对照报告验收，不保证绝对零延迟变化')

def require_gpu(uuid,own_pid=None):
    try:
        state=json.loads(STATE.read_text())
        age=time.time()-state['updated_at']
        if age>2:
            # 后台发布偶发迟到时复核真实读数，不伪造旧样本时间、不放宽2秒保护。
            # raw_snapshot有采集超时；推理监测仍沿用原时间，由evaluate独立检查。
            fresh=raw_snapshot()
            if not fresh.get('available'):
                raise ValueError(fresh.get('error','GPU指标复核失败'))
            fresh_age=time.time()-fresh.get('sampled_at',0)
            if fresh_age>2:raise ValueError(f'GPU复核采样过期（{fresh_age:.1f}秒）')
            state=dict(fresh,inference=state.get('inference',{}))
        if not state.get('available'):raise ValueError(state.get('error','GPU采样不可用'))
        gpu=next(g for g in state['gpus'] if g['uuid']==uuid)
        reason=evaluate(gpu,policy(),state.get('inference'),own_pid)
        if reason:raise ProtectedWork(reason)
        return gpu
    except ProtectedWork:raise
    except Exception as error:raise ProtectedWork('GPU监测不可用：'+str(error)[:120]) from None

class Monitor:
    def __init__(self):
        self.stop=threading.Event();self.thread=None;self.pool=ThreadPoolExecutor(max_workers=4)
    def start(self):
        self.thread=threading.Thread(target=self.run,daemon=True,name='inference-monitor');self.thread.start()
    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=4)
        self.pool.shutdown(wait=False,cancel_futures=True)
    def run(self):
        previous={};last_active=time.time();raw=dict(available=False,gpus=[]);last_gpu=0
        while not self.stop.is_set():
            start=time.monotonic();p=policy();targets=p.get('protected_targets',[]);now=time.time()
            try:
                if not targets:raise ValueError('未配置生产监测')
                futures=[self.pool.submit(read_target,t) for t in targets]
                samples=[f.result(timeout=.8) for f in futures]
                processing=sum(x['requests_processing'] for x in samples);deferred=sum(x['requests_deferred'] for x in samples)
                counters={x['name']:(x['prompt_tokens_total'],x['tokens_predicted_total']) for x in samples}
                if processing or deferred or any(counters[k]!=previous.get(k) for k in counters):last_active=now
                previous=counters
                inf=dict(available=True,updated_at=now,last_active_at=last_active,processing=processing,deferred=deferred,targets=samples)
            except Exception as e:
                inf=dict(available=False,updated_at=now,last_active_at=now,error=str(e)[:100]);last_active=now
            if time.time()-last_gpu>=.75:
                raw=raw_snapshot();last_gpu=raw.get('sampled_at',time.time())
            # 不把NVML失败伪造成空闲，也不把旧GPU快照更新时间刷新。
            state=dict(raw,updated_at=last_gpu,inference=inf)
            try:
                tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(state,ensure_ascii=False));tmp.replace(STATE)
            except Exception:pass
            self.stop.wait(max(.01,.25-(time.monotonic()-start)))
