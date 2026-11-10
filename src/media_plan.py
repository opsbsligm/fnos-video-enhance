"""统一片源支持边界及输出预检；不将时长估计当成实际计帧验收。"""
from fractions import Fraction
import math
import re

MODES=('interpolate','upscale','both','denoise','denoise_block')

def derive_output_name(source_path,mode,scale,target_fps):
    """成果名 = 原文件名[AI超分×N·60fps].mkv；随源存放，不再制造任务ID目录。"""
    if mode not in MODES:raise ValueError('操作不允许')
    stem=re.sub(r'\.[^.]+$','',str(source_path).rstrip('/').split('/')[-1]) or '输出'
    stem=stem.replace('/','').replace('\x00','').strip('. ')or '输出'
    tags=[]
    if mode in('upscale','both'):tags.append(f'AI超分×{scale}')
    elif mode=='denoise':tags.append('AI修复')
    elif mode=='denoise_block':tags.append('AI去块')
    if mode in('interpolate','both'):tags.append(f'{target_fps}fps')
    return (stem[:150]+'…' if len(stem)>150 else stem)+f'[{"·".join(tags)}].mkv'

def video_support(video):
    reasons=[]
    w,h=video.get('width',0),video.get('height',0)
    if not isinstance(w,int) or not isinstance(h,int) or w<2 or h<2 or w>3840 or h>2160:reasons.append('输入尺寸超出2像素至4K范围')
    elif w%2 or h%2:reasons.append('奇数尺寸暂不支持，避免像素格式编码失败')
    try:
        fps=Fraction(video.get('avg_frame_rate','0/1'));nominal=Fraction(video.get('r_frame_rate','0/1'))
        if fps<=0 or fps>240 or abs(float(fps-nominal))>.03:reasons.append('帧率不支持或可能为VFR；仅支持CFR')
    except (ValueError,ZeroDivisionError,TypeError):reasons.append('帧率信息非法')
    if video.get('field_order','unknown') not in ('progressive','unknown'):reasons.append('隔行片源暂不支持')
    if video.get('color_transfer') in ('smpte2084','arib-std-b67') or any('dovi' in str(x).lower() for x in video.get('side_data_list',[])):reasons.append('HDR/杜比视界暂不支持，避免色彩损坏')
    if video.get('sample_aspect_ratio','1:1') not in ('1:1','0:1','N/A',None):reasons.append('非方形像素暂不支持，避免画面比例失真')
    rotations=[video.get('tags',{}).get('rotate',0)]+[x.get('rotation',0) for x in video.get('side_data_list',[])]
    try:
        if any(not math.isfinite(float(r)) or float(r)%360!=0 for r in rotations):reasons.append('带旋转标记片源暂不支持，避免解码尺寸错位')
    except (ValueError,TypeError):reasons.append('旋转信息非法')
    return reasons

def plan(info,mode,target_fps=60,scale=2):
    reasons=list(info.get('unsupported_reasons',[]))
    if mode not in MODES:raise ValueError('操作不允许')
    if isinstance(target_fps,bool) or target_fps not in (48,50,60) or isinstance(scale,bool) or scale not in (2,4):raise ValueError('目标参数不允许')
    w,h=info['width'],info['height'];fps=info['fps']
    if mode=='interpolate':
        if fps>=target_fps:reasons.append('原帧率不低于目标，无需插帧')
        ow,oh,ofps=w,h,target_fps
    elif mode=='denoise':
        # 原生分辨率修复：4×上采样推理再缩回源尺寸，清块状/噪点/JPEG 伪影，不改分辨率与帧率。
        ow,oh,ofps=w,h,fps
    elif mode=='denoise_block':
        # 去马赛克（去压缩块）：DCT 域去块滤波，专治 8×8 压缩块边缘锯齿；保细节，不改分辨率与帧率。
        ow,oh,ofps=w,h,fps
    else:
        factor=min(scale,3840/w,2160/h);ow,oh=max(2,int(w*factor)//2*2),max(2,int(h*factor)//2*2);ofps=target_fps if mode=='both' else fps
        if factor<=1:reasons.append('片源已达输出尺寸上限，无需超分')
        if mode=='both' and fps>=target_fps:reasons.append('原帧率不低于目标，双增强的插帧无意义')
    return dict(supported=not reasons,reasons=reasons,output_width=ow,output_height=oh,output_fps=ofps,
                notes=['AI增强不等于原生4K/高帧率','帧率元数据预检不能完全排除VFR；最终仍需实际解码验收','处理前建议先比较GPU原尺寸样片','双增强为单遍流水线：源分辨率插帧后再超分，避免目标分辨率插值的算力爆炸与伪影放大'])
