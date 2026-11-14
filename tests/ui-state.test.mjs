import test from 'node:test';
import assert from 'node:assert/strict';
import {submitPending,createLimiter,estimateRemaining,taskProgressText} from '../src/ui-state.mjs';
test('分开显示当前计算和已提交分片，不伪造续跑进度',()=>{
 const p={stage:'插帧',frames_done:18144,working_frame:18167,frames_total:113748};
 assert.equal(taskProgressText('running',p),'插帧 · 正在处理 18167 / 113748 帧 · 已保存 18144 帧');
 assert.equal(taskProgressText('paused',p),'插帧 · 18144 / 113748 帧');
});
test('零帧和已完成进度均明确显示，非法工作帧不能越界',()=>{
 assert.equal(taskProgressText('running',{stage:'加载模型',frames_done:0,frames_total:120}),'加载模型 · 0 / 120 帧');
 assert.equal(taskProgressText('completed',{stage:'完成',frames_done:120,frames_total:120,working_frame:121}),'完成 · 120 / 120 帧');
 assert.equal(taskProgressText('running',{frames_done:48,frames_total:120,working_frame:999}),'48 / 120 帧');
 assert.equal(taskProgressText('running',{}),'');
});
test('部分成功后重试不重复成功任务',async()=>{const selected=new Set(['a','b','c']),calls=[];await assert.rejects(submitPending(selected,async p=>{calls.push(p);if(p==='b')throw Error('失败');}),/已成功提交 1 个/);assert.deepEqual([...selected],['b','c']);assert.equal(await submitPending(selected,async p=>calls.push(p)),2);assert.deepEqual(calls,['a','b','b','c']);});
test('可跳过错误不中断批次，其余照常提交',async()=>{const selected=new Set(['a','b','c']),skipped=[],calls=[];const skip=Error('片源不适用');skip.__kind='skip';const count=await submitPending(selected,async p=>{if(p==='b'){const e=Error('已达4K上限');e.skipKind='preflight';throw e;}calls.push(p);},()=>{},(e)=>e.skipKind==='preflight');assert.equal(count,2);assert.deepEqual(calls,['a','c']);assert.deepEqual([...selected],[]);});
test('无跳过回调时错误仍终止批次',async()=>{const selected=new Set(['a','b']);await assert.rejects(submitPending(selected,async p=>{if(p==='a')throw Error('网络失败');}),/已成功提交 0 个/);assert.deepEqual([...selected],['a','b']);});
test('全部成功清空选择',async()=>{const selected=new Set(['a']);assert.equal(await submitPending(selected,async()=>{}),1);assert.equal(selected.size,0);});

test('探测队列最多两个并发，单个失败不阻塞余下视频',async()=>{
  const limit=createLimiter(2);let active=0,peak=0,finished=0;
  const results=await Promise.allSettled(Array.from({length:7},(_,i)=>limit(async()=>{
    active++;peak=Math.max(peak,active);
    await new Promise(resolve=>setImmediate(resolve));active--;finished++;
    if(i===1)throw Error('单片失败');return i;
  })));
  assert.equal(peak,2);assert.equal(active,0);assert.equal(finished,7);
  assert.equal(results.filter(r=>r.status==='rejected').length,1);
});

test('续跑剩余时间使用观测增量，不把历史帧误算成新吞吐',()=>{
 const h=new Map();const p=(elapsed,frames)=>({elapsed_seconds:elapsed,frames_done:frames,frames_total:100000});
 assert.equal(estimateRemaining(h,'job',p(5,50000)),null);
 assert.equal(estimateRemaining(h,'job',p(25,50060)),49940/3);
 assert.equal(estimateRemaining(h,'job',p(2,50060)),null);
 assert.equal(estimateRemaining(h,'job',p(22,50120)),49880/3);
});
test('采样不足或无处理进展时不显示剩余时间',()=>{
 const h=new Map(),p={elapsed_seconds:1,frames_done:100,frames_total:1000};
 assert.equal(estimateRemaining(h,'job',p),null);
 assert.equal(estimateRemaining(h,'job',{...p,elapsed_seconds:100}),null);
});
