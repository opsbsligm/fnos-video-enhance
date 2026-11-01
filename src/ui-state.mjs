// 已提交的文件立即移出待提交集合，失败重试不会重复创建成功任务。
export async function submitPending(selected, submitOne, onCommitted=()=>{}) {
  let count=0;
  for (const path of [...selected]) {
    try {await submitOne(path);} catch (error) {
      throw new Error(`已成功提交 ${count} 个；剩余 ${selected.size} 个未提交。${error.message||'请求失败'}`);
    }
    selected.delete(path);count++;onCommitted(path);
  }
  return count;
}

// 与后端探测并发预算一致；队列失败后仍可继续处理后续请求。
export function createLimiter(maximum) {
  const pending=[];let active=0;
  const drain=()=>{
    while(active<maximum&&pending.length){
      const {run,resolve,reject}=pending.shift();active++;
      Promise.resolve().then(run).then(resolve,reject).finally(()=>{active--;drain();});
    }
  };
  return run=>new Promise((resolve,reject)=>{pending.push({run,resolve,reject});drain();});
}

// 工作帧尚未通过分片校验，不能冒充可续跑的已保存进度。
export function taskProgressText(state, progress={}) {
  const pieces=[];
  if(progress.stage)pieces.push(String(progress.stage));
  const saved=Number(progress.frames_done),total=Number(progress.frames_total);
  const working=Number(progress.working_frame);
  const validSaved=Number.isInteger(saved)&&saved>=0;
  const validTotal=Number.isInteger(total)&&total>0;
  if(validSaved&&validTotal){
    const committed=Math.min(saved,total);
    if(state==='running'&&Number.isInteger(working)&&working>committed&&working<=total){
      pieces.push(`正在处理 ${working} / ${total} 帧`, `已保存 ${committed} 帧`);
    }else{
      pieces.push(`${committed} / ${total} 帧`);
    }
  }
  if(progress.width)pieces.push(`${progress.width}×${progress.height} @ ${progress.fps}fps`);
  return pieces.join(' · ');
}

// 以当前运行窗口的帧增量估算，续跑不能把历史帧数除以新进程耗时。
export function estimateRemaining(history,id,progress) {
  const elapsed=Number(progress.elapsed_seconds),frames=Number(progress.working_frame??progress.frames_done),total=Number(progress.frames_total);
  if(!Number.isFinite(elapsed)||!Number.isFinite(frames)||!(total>frames))return null;
  let samples=history.get(id)||[];
  const last=samples.at(-1);
  if(last&&(elapsed<last.elapsed||frames<last.frames))samples=[];
  if(!last||elapsed!==last.elapsed||frames!==last.frames||!samples.length)samples.push({elapsed,frames});
  while(samples.length>2&&elapsed-samples[1].elapsed>120)samples.shift();
  history.set(id,samples);
  const first=samples[0],seconds=elapsed-first.elapsed,delta=frames-first.frames;
  if(seconds<15||delta<48)return null;
  return (total-frames)*seconds/delta;
}
