"""分片提交、校验续跑、推理优先让步。原片只读，模型/用户原文件均不改。"""
import argparse
from fractions import Fraction
import errno
import json
import math
import os
from pathlib import Path
import shutil
import resource
import signal
import subprocess
import sys
import threading
import time
import queue

from media_plan import video_support
import guard
from job_store import Store, load_checkpoint
from version import VERSION

STOP_REASON=''
class YieldWork(RuntimeError):pass

def _threads():
    # 全功率策略下解码/编码不设线程上限（ffmpeg '0'=auto）；否则维持让行配置2线程
    return '0' if guard.policy().get('full_power') else '2'

def stopped(*_):
    global STOP_REASON
    STOP_REASON='用户暂停/取消或应用停止；已提交分片保留'

class Frames:
    def __init__(self,spec,src_fd,frame,fps,width,height,proxy,video_index):
        self.stderr=None
        argv=[spec['ffmpeg'],'-nostdin','-v','error','-threads',_threads(),'-ss',str(float(Fraction(frame,1)/fps)),
              '-i',f'/proc/self/fd/{src_fd}','-map',f'0:{video_index}','-an','-sn','-dn']
        if proxy:argv+=['-vf',f'scale={width}:{height}']
        argv+=['-fps_mode','passthrough','-f','rawvideo','-pix_fmt','bgr24','pipe:1']
        self.p=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL,pass_fds=(src_fd,))
        self.size=width*height*3;self.width=width;self.height=height
        # 解码预取线程：ffmpeg 解码与 GPU 推理/编码并行，raw 帧经有界队列传递（内存上限=depth×帧大小）
        self.q=queue.Queue(maxsize=4);self.err=None;self.stop=threading.Event()
        self.t=threading.Thread(target=self._pump,daemon=True,name='frame-decode');self.t.start()
    def _read_raw(self):
        raw=bytearray()
        while len(raw)<self.size:
            b=self.p.stdout.read(self.size-len(raw))
            if not b:break
            raw.extend(b)
        if len(raw)!=self.size:return None
        return bytes(raw)
    def _pump(self):
        try:
            while not self.stop.is_set():
                raw=self._read_raw()
                if raw is None:break
                self.q.put(raw)
        except Exception as e:self.err=repr(e)
        finally:self.q.put(None)
    def read(self,np):
        if self.err:raise RuntimeError('解码线程异常：'+self.err)
        raw=self.q.get()
        if raw is None:
            if self.err:raise RuntimeError('解码线程异常：'+self.err)
            raise RuntimeError('源解码提前结束/帧数据不完整')
        return np.frombuffer(raw,dtype=np.uint8).reshape(self.height,self.width,3).copy()
    def close(self):
        self.stop.set()
        try:
            while True:self.q.get_nowait()
        except (queue.Empty,ValueError):pass
        if self.p.poll() is None:
            self.p.terminate()
            try:self.p.wait(timeout=3)
            except subprocess.TimeoutExpired:self.p.kill();self.p.wait()

class AsyncWriter:
    """编码器 stdin 的异步写入线程：GPU 出帧即返回，编码压缩与下一帧推理重叠。"""
    def __init__(self,pipe):
        self.pipe=pipe;self.q=queue.Queue(maxsize=8);self.err=None
        self.t=threading.Thread(target=self._pump,daemon=True,name='encode-write');self.t.start()
    def write(self,frame):
        if self.err:raise RuntimeError('编码写入线程异常：'+self.err)
        self.q.put(memoryview(frame))
    def _pump(self):
        try:
            while True:
                buf=self.q.get()
                if buf is None:break
                self.pipe.write(buf)
        except Exception as e:self.err=repr(e)
        finally:
            # 写完必须关闭编码器 stdin，否则 ffmpeg 等 EOF 直到 wait 超时
            try:self.pipe.close()
            except Exception as e:self.err=self.err or repr(e)
    def join(self):
        self.q.put(None);self.t.join(timeout=120)
        if self.err:raise RuntimeError('编码写入线程异常：'+self.err)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--spec',required=True);spec=json.loads(Path(ap.parse_args().spec).read_text())
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,stopped)
    # 长任务分片可达数千：启动即抬升 fd 软限制（不超过硬限制），双保险防后续扩展路径再触顶。
    try:
        _sf,_hf=resource.getrlimit(resource.RLIMIT_NOFILE);resource.setrlimit(resource.RLIMIT_NOFILE,(min(_hf,max(_sf,65536)),_hf))
    except Exception:pass
    src_fd,out_fd,work_fd=spec['source_fd'],spec['output_fd'],spec['work_fd'];gpu=spec['device']=='cuda'
    if gpu:guard.require_gpu(spec['gpu_uuid'])
    os.environ['CUDA_VISIBLE_DEVICES']=spec['gpu_uuid'] if gpu else ''
    if gpu:os.environ.setdefault('PYTORCH_CUDA_ALLOC_CONF','expandable_segments:True')
    # 分片/检查点等工作文件在应用私有 work 目录；成果目录只落最终成品一个文件。
    store=Store(work_fd,spec['id']);fds=[src_fd,out_fd,work_fd,store.fd];part_fds=[];reader=None;encoder=None;temps=[];net=None
    checkpoint=load_checkpoint(store,spec)
    state=Path(spec['progress_path']);started=time.monotonic();yield_count=checkpoint.get('yield_count',0)
    def report(**kw):
        value=dict(elapsed_seconds=round(time.monotonic()-started,2),frames_done=checkpoint['frames_done'],yield_count=yield_count,**kw)
        tmp=state.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False));tmp.replace(state)
    def check():
        if STOP_REASON:raise YieldWork(STOP_REASON)
        if gpu:
            try:guard.require_gpu(spec['gpu_uuid'],own_pid=os.getpid())
            except guard.ProtectedWork as e:raise YieldWork(str(e))
    def run(argv,timeout=120,extra=()):
        check()
        try:r=subprocess.run(argv,pass_fds=tuple(fds)+tuple(extra),capture_output=True,check=True,timeout=timeout)
        except subprocess.CalledProcessError as e:
            # 子进程 stderr 之前被吞掉，只留 exit code；透传尾部错误便于诊断（进任务日志）
            if e.stderr:print('FFMPEG-STDERR: '+e.stderr.decode(errors='replace')[-2000:],flush=True)
            if STOP_REASON:raise YieldWork(STOP_REASON)
            raise
        except Exception:
            if STOP_REASON:raise YieldWork(STOP_REASON)
            raise
        check();return r
    def decoded(fd,packets=False):
        # 分片验收用 -count_packets（解包计数，CPU开销小）；最终成品用 -count_frames 逐帧解码验收。
        mode='-count_packets' if packets else '-count_frames'
        p=run([spec['ffprobe'],'-v','error','-threads',_threads(),mode,'-show_streams','-show_format','-of','json',f'/proc/self/fd/{fd}'],
              timeout=max(120,source_duration*20),extra=(fd,))
        if p.stderr.strip():raise RuntimeError('成果实际解码失败：'+p.stderr.decode(errors='replace')[-300:])
        return json.loads(p.stdout)
    def allocate(suffix):
        name,fd=store.create(suffix);fds.append(fd);temps.append(name);return name,fd
    def close_fd(fd):
        if fd in fds:fds.remove(fd)
        os.close(fd)
    p=guard.policy()
    duty_full=gpu and float(p.get('gpu_duty',.1))>=1
    def inference(call,wait=True):
        check();before=time.monotonic();value=call()
        if gpu:
            # 全功率：kernel 保持排队，由调用方在整帧/整批边界统一同步；
            # 非全功率维持逐次同步+占空让步语义。
            if duty_full:
                if wait:torch.cuda.synchronize()
                check()
            else:
                torch.cuda.synchronize()
                elapsed=time.monotonic()-before
                duty=max(.02,float(p.get('gpu_duty',.1)))
                until=time.monotonic()+elapsed*(1/duty-1)
                while time.monotonic()<until:check();time.sleep(max(0,min(.025,until-time.monotonic())))
        return value
    try:
        report(stage='校验源与检查点')
        st=os.fstat(src_fd)
        if st.st_size!=spec['source_size'] or st.st_mtime_ns!=spec['source_mtime_ns']:raise RuntimeError('源文件已改变，拒绝续跑')
        source=f'/proc/self/fd/{src_fd}'
        raw=json.loads(run([spec['ffprobe'],'-v','error','-show_streams','-show_format','-of','json',source]).stdout)
        video=next(v for v in raw['streams'] if v['codec_type']=='video' and not v.get('disposition',{}).get('attached_pic'))
        unsupported=video_support(video)
        if unsupported:raise RuntimeError('；'.join(unsupported))
        source_duration=float(raw['format']['duration']);fps=Fraction(video['avg_frame_rate'])
        if fps<=0 or abs(float(fps)-float(Fraction(video.get('r_frame_rate',video['avg_frame_rate']))))>.03:
            raise RuntimeError('仅支持恒定帧率；VFR暂拒绝')
        if video.get('field_order','progressive') not in ('progressive','unknown'):raise RuntimeError('隔行片源暂拒绝')
        if video.get('color_transfer') in ('smpte2084','arib-std-b67') or any('dovi' in str(x).lower() for x in video.get('side_data_list',[])):
            raise RuntimeError('HDR/DV暂拒绝，避免错误色彩转换')
        if video['width']>3840 or video['height']>2160:raise RuntimeError('输入最大4K')
        count=checkpoint.get('source_frames')
        if not count:
            if str(video.get('nb_frames','')).isdigit():count=int(video['nb_frames'])
            else:
                # 已通过CFR校验：包数=帧数，解包计数即可（逐帧解码计数在4K整片上要数分钟）
                counted=run([spec['ffprobe'],'-v','error','-threads',_threads(),'-count_packets','-select_streams',str(video['index']),
                             '-show_entries','stream=nb_read_packets','-of','json',source],timeout=max(120,source_duration*20))
                if counted.stderr.strip():raise RuntimeError('源计帧异常')
                count=int(json.loads(counted.stdout)['streams'][0]['nb_read_packets'])
            if count<=0:raise RuntimeError('源计帧失败')
            checkpoint['source_frames']=count;store.atomic_json('checkpoint.json',checkpoint)
        offset=round(float(fps)*spec['start_seconds']) if spec['preview'] else 0
        count=min(count-offset,max(1,round(float(fps)*spec['preview_seconds']))) if spec['preview'] else count
        if count<=0:raise RuntimeError('样片起点无视频帧')
        do_sr=spec['mode'] in ('upscale','both','denoise');do_fi=spec['mode'] in ('interpolate','both')
        target=Fraction(spec['target_fps']) if do_fi else fps
        if do_fi and target<=fps:raise RuntimeError('插帧目标必须高于原帧率')
        total=round(Fraction(count,1)/fps*target)
        duration=float(Fraction(count,1)/fps)
        width,height=video['width'],video['height'];proxy=not gpu and spec['preview']
        if proxy and width>320:height=max(2,round(height*320/width/2)*2);width=320
        out_width,out_height=width,height
        if do_sr and spec['mode']!='denoise':
            factor=min(spec['scale'],3840/width,2160/height)
            if factor<=1:raise RuntimeError('源已达到输出尺寸上限')
            out_width=max(2,int(width*factor)//2*2);out_height=max(2,int(height*factor)//2*2)
        report(stage='加载封装模型',frames_total=total,percent=round(checkpoint['frames_done']/total*100,1))
        check()
        import numpy as np
        import cv2
        import torch
        import torch.nn.functional as F
        native_th=max(1,int(_threads())) if _threads().isdigit() else max(4,(os.cpu_count() or 4)//2)
        cv2.setNumThreads(native_th);torch.set_num_threads(native_th)
        p=guard.policy()
        if gpu:
            check()
            if not torch.cuda.is_available():raise RuntimeError('CUDA不可用')
            major,minor=torch.cuda.get_device_capability()
            if f'sm_{major}{minor}' not in torch.cuda.get_arch_list():raise RuntimeError('本运行包不支持此显卡架构')
            budget=int(p.get('budget_mib',1024))
            if budget>0:torch.cuda.set_per_process_memory_fraction(budget/(torch.cuda.get_device_properties(0).total_memory/1024**2))
            torch.cuda.reset_peak_memory_stats()
        device=torch.device('cuda' if gpu else 'cpu');engine=Path(__file__).parent/'engine';sys.path.insert(0,str(engine))
        check()
        finet=fsrn=None
        if do_fi:
            from train_log.IFNet_HDv3 import IFNet
            finet=IFNet();d=torch.load(engine/'train_log/flownet.pkl',map_location='cpu',weights_only=True)
            finet.load_state_dict({k.removeprefix('module.'):v for k,v in d.items() if k.removeprefix('module.') in finet.state_dict()},strict=True)
        if do_sr:
            from srvgg_arch import SRVGGNetCompact
            fsrn=SRVGGNetCompact(num_in_ch=3,num_out_ch=3,num_feat=64,num_conv=32,upscale=4,act_type='prelu')
            d=torch.load(engine/'realesr-general-x4v3.pth',map_location='cpu',weights_only=True);fsrn.load_state_dict(d.get('params_ema',d.get('params')))
        if not do_fi and not do_sr:raise RuntimeError('未知操作')
        check();net=[m.to(device).eval() for m in (finet,fsrn) if m]
        half_gpu=gpu and bool(p.get('full_power')) and torch.cuda.is_available() and torch.cuda.get_device_capability()>=(7,0)
        if half_gpu:net=[m.half() for m in net]
        def tensor(im,pad=False):
            # uint8 原子上 GPU（PCIe 传输量 1/3），BGR→RGB 与归一化在 GPU 内完成，CPU 零像素转换
            t=torch.from_numpy(im).unsqueeze(0).to(device).flip(-1).permute(0,3,1,2).float().div_(255.0)  # flip 必须在 permute 前：(N,H,W,3) 的末维是通道；permute 后 flip(-1) 会镜像宽度
            if half_gpu:t=t.half()
            h,w=im.shape[:2]
            return F.pad(t,(0,(-w)%256,0,(-h)%256)) if pad else t  # IFNet多尺度级联需256对齐（64实测形状不匹配）
        def sr(im):
            # 256基tile+10重叠；按同形状分组批量推理，整帧一次同步回收，减少launch开销与D2H次数。
            result=np.empty((height*4,width*4,3),np.uint8);tile,pad=256,10
            jobs=[]
            for y in range(0,height,tile):
                for x in range(0,width,tile):
                    x0,y0=max(0,x-pad),max(0,y-pad);x1,y1=min(width,x+tile+pad),min(height,y+tile+pad)
                    jobs.append((y,x,y0,x0,im[y0:y1,x0:x1]))
            groups={}
            for k,j in enumerate(jobs):groups.setdefault(j[4].shape,[]).append(k)
            outs={}
            for shp,keys in groups.items():
                for i in range(0,len(keys),8):
                    bk=keys[i:i+8]
                    inp=torch.cat([tensor(jobs[k][4]) for k in bk])
                    out=inference(lambda inp=inp:fsrn(inp),wait=False)
                    outs[bk[0]]=(bk,out)
            if gpu:torch.cuda.synchronize()
            for k0,(bk,out) in outs.items():
                # 量化/RGB→BGR 在 GPU 内完成，D2H 仅传 uint8（带宽 1/4）
                big=out.clamp_(0,1).mul_(255).round_().to(torch.uint8).permute(0,2,3,1).flip(-1).cpu().numpy()
                for n,k in enumerate(bk):
                    y,x,y0,x0,_=jobs[k];ey,ex=min(height,y+tile),min(width,x+tile)
                    result[y*4:ey*4,x*4:ex*4]=big[n][(y-y0)*4:(ey-y0)*4,(x-x0)*4:(ex-x0)*4]
            return cv2.resize(result,(out_width,out_height),interpolation=cv2.INTER_AREA)
        def rife(a,b,t):
            h0,w0=a.shape[:2]
            diff=np.mean(cv2.absdiff(cv2.resize(a,(64,36)),cv2.resize(b,(64,36))))/255
            if diff>.22:return a if t<.5 else b
            # 512基tile+80重叠；同形状分组批量推理（全功率下显著降低launch/同步开销）。
            result=np.empty_like(a);tile=512;pad=80
            jobs=[]
            for y in range(0,h0,tile):
                for x in range(0,w0,tile):
                    x0,y0=max(0,x-pad),max(0,y-pad);x1,y1=min(w0,x+tile+pad),min(h0,y+tile+pad)
                    jobs.append((y,x,y0,x0,a[y0:y1,x0:x1],b[y0:y1,x0:x1]))
            groups={}
            for k,j in enumerate(jobs):groups.setdefault(j[4].shape,[]).append(k)
            outs={}
            for shp,keys in groups.items():
                for i in range(0,len(keys),4):
                    bk=keys[i:i+4]
                    pair=torch.cat([torch.cat((tensor(jobs[k][4],True),tensor(jobs[k][5],True)),1) for k in bk])
                    out=inference(lambda pair=pair:finet(pair,float(t),[32,16,8,4,2])[2][-1],wait=False)
                    outs[bk[0]]=(bk,out)
            if gpu:torch.cuda.synchronize()
            for k0,(bk,out) in outs.items():
                # 量化/RGB→BGR 在 GPU 内完成，D2H 仅传 uint8（带宽 1/4）
                big=out.clamp_(0,1).mul_(255).round_().to(torch.uint8).permute(0,2,3,1).flip(-1).cpu().numpy()
                for n,k in enumerate(bk):
                    y,x,y0,x0,_,_=jobs[k];ey,ex=min(h0,y+tile),min(w0,x+tile)
                    result[y:ey,x:ex]=big[n][(y-y0):(ey-y0),(x-x0):(ex-x0)]
            return result
        jstart=checkpoint['frames_done'];index=math.floor(Fraction(jstart,1)*fps/target)
        b=None
        if jstart<total:
            reader=Frames(spec,src_fd,offset+index,fps,width,height,proxy,video['index']);a=reader.read(np)
            if index+1<count:b=reader.read(np)
        chunk_frames=max(1,min(120,int(spec.get('chunk_frames',12))))
        stage='双增强（插帧+超分）' if do_sr and do_fi else ('去噪修复' if spec['mode']=='denoise' else ('超分' if do_sr else '插帧'))
        with torch.inference_mode():
            while checkpoint['frames_done']<total:
                check();part_start=checkpoint['frames_done'];part_end=min(total,part_start+chunk_frames)
                name,outfd=allocate('.mkv');errname,errfd=allocate('.log')
                cmd=[spec['ffmpeg'],'-nostdin','-v','error','-y','-f','rawvideo','-pix_fmt','bgr24','-s',f'{out_width}x{out_height}',
                     '-r',str(target),'-i','pipe:0','-an','-c:v','libx264','-threads',_threads(),'-preset','fast','-crf','18','-pix_fmt','yuv420p',
                     '-f','matroska',f'/proc/self/fd/{outfd}']
                encoder=subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=errfd,pass_fds=tuple(fds))
                ew=AsyncWriter(encoder.stdin)
                for j in range(part_start,part_end):
                    check();pos=Fraction(j,1)*fps/target;i=math.floor(pos);t=pos-i
                    while index<i:
                        if b is None:raise RuntimeError('源序列计帧不一致')
                        a=b;index+=1;b=reader.read(np) if index+1<count else None
                    # 双增强固定“先源分辨率插帧、再超分”：RIFE在源尺寸最省且无目标分辨率伪影放大。
                    base=a if (not do_fi or not t or b is None) else rife(a,b,t)
                    frame=sr(base) if do_sr else base
                    ew.write(frame)
                    report(stage=stage,frames_total=total,
                           working_frame=j+1,percent=round((j+1)/total*100,1),width=out_width,height=out_height,fps=float(target))
                ew.join()
                if encoder.wait(timeout=90)!=0:
                    os.lseek(errfd,0,0);raise RuntimeError('编码错误：'+os.read(errfd,4096).decode(errors='replace')[-300:])
                encoder=None;close_fd(errfd);store.remove(errname)
                verified=decoded(outfd,packets=True);v=next(x for x in verified['streams'] if x['codec_type']=='video')
                if int(v.get('nb_read_packets',0))!=part_end-part_start or v['width']!=out_width or v['height']!=out_height or abs(float(Fraction(v['avg_frame_rate'])-target))>.02:
                    raise RuntimeError('分片帧数/尺寸/帧率验收失败')
                os.fsync(outfd);close_fd(outfd);part_name=f"part-{len(checkpoint['parts']):06d}.mkv";store.rename(name,part_name)
                checkpoint['parts'].append(dict(name=part_name,start=part_start,frames=part_end-part_start,sha256=store.sha(part_name)))
                checkpoint['frames_done']=part_end
                if gpu:checkpoint['peak_allocated_mib']=round(torch.cuda.max_memory_allocated()/1024**2,2)
                store.atomic_json('checkpoint.json',checkpoint)
                report(stage='分片已校验提交',frames_total=total,percent=round(part_end/total*100,1),resumable=True,
                       peak_allocated_mib=checkpoint.get('peak_allocated_mib',0))
        if reader:reader.close();reader=None
        # 先释放模型与CUDA缓存；最终封装/逐帧检查使用CPU，不为其保留增强显存。
        if gpu:
            finet=fsrn=net=None;torch.cuda.empty_cache()
        report(stage='合并分片与原音轨字幕',frames_total=total,percent=99)
        lines=['ffconcat version 1.0']
        # 合并不再预开全部分片 fd：分片数超过 fd 软限制会 Too many open files（实测 2370 分片失败）。
        # 经工作目录 fd 的 /proc/self/fd/{work_fd} 遍历，让 ffmpeg concat 按需自行打开（保持 fd 相对安全模型）。
        workbase=f'/proc/self/fd/{store.fd}'  # 注意：分片在 store 子目录 fd 下，不是 work_fd 父层
        for part in checkpoint['parts']:
            lines+= [f"file '{workbase}/{part['name']}'",f"duration {float(Fraction(part['frames'],1)/target):.12f}"]
        concatname,concatfd=allocate('.ffconcat');os.write(concatfd,('\n'.join(lines)+'\n').encode());os.lseek(concatfd,0,0)
        finaltemp,finalfd=allocate('.mkv')
        # 音轨/字幕/附件只在最终封装复制，不在每个分片重复截取或重新编码。
        # 样片截取两步走：对 concat 输入做输入侧 -ss 会使视频时间戳整体偏移（实测 +5.3s）；
        # 改为先从源把音频/字幕/附件精确截成 0 基时间戳的临时轨，再与完整视频轨封装。
        if spec['preview']:
            auxname,auxfd=allocate('.aux.mka')
            run([spec['ffmpeg'],'-nostdin','-v','error','-y','-threads',_threads(),'-i',source,
                 '-map','0:a?','-map','0:s?','-map','0:t?','-c','copy',
                 '-ss',str(float(Fraction(offset,1)/fps)),'-t',str(duration),'-f','matroska',f'/proc/self/fd/{auxfd}'],
                timeout=max(120,source_duration*20))  # 输出侧截取：输入侧 -ss+copy 对音频截断不可靠（实测会带入后续15s）
            os.lseek(auxfd,0,0)
            aux_in=auxname
        else:
            aux_in=None
        aux_fd=auxfd if spec['preview'] else src_fd
        cmd=[spec['ffmpeg'],'-nostdin','-v','error','-y','-f','concat','-safe','0','-protocol_whitelist','file,pipe,fd',
             '-i',f'/proc/self/fd/{concatfd}','-i',f'/proc/self/fd/{aux_fd}',
             '-map','0:v:0','-map','1:a?','-map','1:s?','-map','1:t?',
             '-map_metadata','1','-map_chapters','1','-c','copy','-bsf:v','h264_mp4toannexb','-f','matroska',f'/proc/self/fd/{finalfd}']
        run(cmd,timeout=max(120,source_duration*20),extra=(aux_fd,))
        if aux_in:store.remove(aux_in)
        final=decoded(finalfd);fv=next(x for x in final['streams'] if x['codec_type']=='video')
        expected_duration=duration if spec['preview'] else source_duration
        if abs(float(final['format']['duration'])-expected_duration)>.25:raise RuntimeError('最终时长校验失败')
        if int(fv.get('nb_read_frames',0))!=total or fv['width']!=out_width or fv['height']!=out_height or abs(float(Fraction(fv['avg_frame_rate'])-target))>.02:
            raise RuntimeError('最终实际解码/帧数/尺寸/帧率验收失败')
        for kind in ('audio','subtitle','attachment'):
            if sum(s['codec_type']==kind for s in final['streams'])!=sum(s['codec_type']==kind for s in raw['streams']):raise RuntimeError('轨道保留失败：'+kind)
        # 成品直接落到成果目录（通常即原片目录），随源命名；同名不覆盖，自动加序号。
        finalname=spec['output_name'];stem=finalname[:-4] if finalname.endswith('.mkv') else finalname
        outname=finalname;seq=2
        while True:
            try:os.stat(outname,dir_fd=out_fd)
            except FileNotFoundError:break
            outname=f'{stem}-{seq}.mkv';seq+=1
        os.fsync(finalfd);close_fd(finalfd)
        try:
            os.rename(finaltemp,outname,src_dir_fd=store.fd,dst_dir_fd=out_fd)
        except OSError as e:
            if e.errno!=errno.EXDEV:raise
            sfd=store.open_read(finaltemp)
            dfd=os.open(outname,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640,dir_fd=out_fd)
            try:
                with os.fdopen(sfd,'rb') as fsrc,os.fdopen(dfd,'wb') as fdst:
                    shutil.copyfileobj(fsrc,fdst,1024**2);fdst.flush();os.fsync(fdst.fileno())
            except Exception:
                try:os.unlink(outname,dir_fd=out_fd)
                except OSError:pass
                raise
            os.unlink(finaltemp,dir_fd=store.fd)
        os.fsync(out_fd);finalname=outname
        # 验收通过后删除中间分片，工作目录只留元数据。
        nparts=len(checkpoint['parts'])
        for part in checkpoint['parts']:store.remove(part['name'])
        checkpoint['parts']=[];checkpoint['frames_done']=total;checkpoint['done']=True
        store.atomic_json('checkpoint.json',checkpoint)
        clean={k:v for k,v in spec.items() if k not in ('source_fd','output_fd','work_fd','progress_path','ffmpeg','ffprobe')}
        clean.update(output_path=str(Path(spec['output_dir'])/finalname),output_filename=finalname,
                     output_width=out_width,output_height=out_height,output_fps=float(target),frames_decoded=total,
                     duration=float(final['format']['duration']),pipeline_version=VERSION,parts=nparts,
                     peak_allocated_mib=checkpoint.get('peak_allocated_mib',0),ai_processed=True,cpu_proxy_preview=proxy,
                     completed_at=time.time())
        store.atomic_json('processing.json',clean)
        report(stage='完成（实际逐帧解码通过）',frames_total=total,percent=100,output_filename=finalname,
               output_path=str(Path(spec['output_dir'])/finalname),
               width=out_width,height=out_height,fps=float(target),
               duration_seconds=round(float(final['format']['duration']),2),
               peak_allocated_mib=checkpoint.get('peak_allocated_mib',0),resumable=True)
    except (YieldWork,guard.ProtectedWork) as e:
        yield_count+=1;checkpoint['yield_count']=yield_count;checkpoint['last_yield_reason']=str(e)
        store.atomic_json('checkpoint.json',checkpoint)
        report(stage='让步/暂停，进程退出释放GPU',reason=str(e),resumable=True,frames_total=locals().get('total',0),percent=round(checkpoint['frames_done']/total*100,1) if locals().get('total') else 0)
        print('YIELD: '+str(e),flush=True);return 75
    finally:
        if reader:reader.close()
        if encoder and encoder.poll() is None:encoder.kill();encoder.wait()
        for fd in part_fds:
            try:os.close(fd)
            except OSError:pass
        # src/out由进程退出回收；store.fd只能由store.close()关闭一次。
        for fd in fds:
            if fd in (src_fd,out_fd,store.fd):continue
            try:os.close(fd)
            except OSError:pass
        for name in temps:store.remove(name)
        store.close()
    return 0

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as e:
        import traceback
        print(type(e).__name__+': '+str(e),file=sys.stderr,flush=True)
        traceback.print_exc()
        sys.exit(75 if STOP_REASON or isinstance(e,guard.ProtectedWork) else 1)
