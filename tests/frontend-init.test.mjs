import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {createLimiter,estimateRemaining,taskProgressText,submitPending as realSubmitPending} from '../src/ui-state.mjs';

const indexHtml=readFileSync(new URL('../src/www/index.html',import.meta.url),'utf8');
test('深度去噪(BM3D)模式卡片存在',()=>{
 assert.match(indexHtml,/data-mode="denoise_bm3d"/);
 assert.match(indexHtml,/<b>深度去噪<\/b>/);
});

test('操作说明使用可读文字色，飞牛装饰令牌保持不变',()=>{
 const css=readFileSync(new URL('../src/www/style.css',import.meta.url),'utf8');
 assert.match(css,/\.empty small\{color:var\(--text-1\)\}/);
 assert.match(css,/\.directory \.path small\{display:block;color:var\(--text-1\)/);
 assert.match(css,/--text-3:rgba\(var\(--grey-9\),\.25\)/);
});

// 运行实际前端入口，覆盖静态HTML与事件绑定、首次目录加载之间的契约。
class Element {
  constructor(tag='div'){this.tag=tag;this.dataset={};this.style={};this.children=[];this._value='';this.textContent='';this.disabled=false;this.classList=new ClassList();}
  get options(){return this.children;}
  get value(){return this._value || (this.tag==='select'?this.children[0]?.value||'':'');}
  set value(v){this._value=v;}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;this._value='';}
  setAttribute(){}
  querySelector(){return this.paragraph??=new Element('p');}
}
class ClassList{constructor(){this.s=new Set();}add(...c){c.forEach(x=>this.s.add(x));}remove(...c){c.forEach(x=>this.s.delete(x));}contains(c){return this.s.has(c);}toggle(c,f){if(f===undefined)f=!this.s.has(c);f?this.s.add(c):this.s.delete(c);return f;}}
async function start(inputs=['/media'],gpus=[]){
  const html=readFileSync(new URL('../src/www/index.html',import.meta.url),'utf8');
  const nodes=new Map([...html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"/g)].map(m=>[m[2],new Element(m[1])]));
  nodes.get('execution').value='cuda-full';nodes.get('fps').value='60';nodes.get('scale').value='2';
  const calls=[];
  const ctx={console,URLSearchParams,Set,Map,JSON,Date,Number,String,Error,Promise,Math,
    TrimApp:class {ready(){return new Promise(()=>{});}},
    submitPending:async()=>0,createLimiter,estimateRemaining,taskProgressText,
    location:{pathname:'/app/video-enhance/',origin:'http://nas.local'},
    document:{getElementById:id=>nodes.get(id)||null,createElement:tag=>new Element(tag),querySelectorAll:()=>[],body:new Element()},
    window:{addEventListener(){}},setInterval(fn){ctx.poll=fn;},setTimeout(){},clearTimeout(){},
    URL,
    authState:()=>"a".repeat(64),
    fetch:async (url,options)=>{ctx.lastRequestOptions=options;calls.push(url);let data;
      if(url.endsWith('/api/status'))data={version:'test',gpu:{gpus},dependency_profile:'test'};
      else if(url.endsWith('/api/directories'))data={inputs,shared:[],roles:{}};
      else if(url.includes('/api/browse?')){const path=new URLSearchParams(url.split('?')[1]).get('path');data={path,root:inputs.find(r=>path.startsWith(r)),entries:[]};}
      else if(url.endsWith('/api/jobs'))data=[];
      else throw Error('unexpected endpoint '+url);
      return {ok:true,json:async()=>data};
    }};
  vm.createContext(ctx);
  const source=readFileSync(new URL('../src/frontend.js',import.meta.url),'utf8').replace(/^import .*;\n/gm,'').replace('export function','function');
  vm.runInContext(source+`\nglobalThis.inspect=()=>({current,currentRoot});globalThis.refreshApp=refresh;globalThis.browseApp=browse;globalThis.crumbs=renderCrumbs;globalThis.apiCall=api;globalThis.setSelection=(paths,files)=>{selected=new Set(paths);entries=files;updateSelBar();};globalThis.deriveNameFn=deriveName;globalThis.selectModeFn=selectMode;`,ctx);
  // 等待入口的实际异步初始化链。
  await new Promise(resolve=>setImmediate(resolve));
  return {ctx,nodes,calls};
}
test('实际HTML入口正常初始化并打开首个授权目录',async()=>{
  const {ctx,nodes,calls}=await start();
  assert.equal(typeof nodes.get('root').onchange,'function');
  assert.equal(ctx.inspect().current,'/media');
  assert.equal(calls.filter(p=>p.includes('/api/browse?')).length,1);
  assert.equal(nodes.get('up').disabled,true);
});
test('刷新保留子目录，目录切换实际打开新根',async()=>{
  const {ctx,nodes}=await start(['/media','/other']);
  await ctx.browseApp('/media/sub');await ctx.refreshApp();
  assert.equal(ctx.inspect().current,'/media/sub');
  nodes.get('root').value='/other';await nodes.get('root').onchange();
  assert.equal(ctx.inspect().current,'/other');
});
test('根目录面包屑不重复',async()=>{
  const {ctx,nodes}=await start();ctx.crumbs('/media','/media');
  assert.equal(nodes.get('crumbs').children.length,1);
  assert.equal(nodes.get('crumbs').children[0].textContent,'media');
});
test('无授权目录时显示明确空状态并禁用上级',async()=>{
  const {nodes,calls}=await start([]);
  assert.equal(nodes.get('root').children[0].textContent,'请先授权目录');
  assert.equal(nodes.get('up').disabled,true);
  assert.equal(calls.some(p=>p.includes('/api/browse?')),false);
});

test('监测短暂失联后恢复会自动清除旧错误提示',async()=>{
 const {ctx,nodes}=await start(),fetch=ctx.fetch;
 ctx.fetch=async()=>({ok:false,json:async()=>{throw Error('invalid response');}});
 await ctx.poll();
 assert.equal(nodes.get('banner').hidden,false);
 assert.match(nodes.get('banner').textContent,/暂时未返回有效数据/);
 ctx.fetch=fetch;await ctx.poll();
 assert.equal(nodes.get('banner').hidden,true);
});

test('无GPU不暗中降级CPU，禁止提交且不调用任务接口',async()=>{
 const {nodes,calls}=await start();
 assert.equal(nodes.get('execution').value,'cuda-full');
 assert.equal(nodes.get('submit-job').disabled,true);
 assert.match(nodes.get('execution-hint').textContent,/暂不能提交/);
 const before=calls.length;await nodes.get('task-form').onsubmit({preventDefault(){}});
 assert.equal(calls.length,before);
 assert.equal(nodes.get('submit-job').disabled,true);
 assert.match(nodes.get('toast').textContent,/不会自动改为CPU/);
});
test('CPU预览需主动选择，GPU选择器和起点与执行方式同步',async()=>{
 const {nodes}=await start();
 nodes.get('execution').value='cpu';nodes.get('execution').onchange();
 assert.equal(nodes.get('submit-job').disabled,false);
 assert.equal(nodes.get('gpu').disabled,true);
 assert.equal(nodes.get('start').disabled,false);
 assert.match(nodes.get('execution-hint').textContent,/只处理2秒/);
 nodes.get('execution').value='cuda-full';nodes.get('start').value='5';nodes.get('execution').onchange();
 assert.equal(nodes.get('start').disabled,true);
 assert.equal(nodes.get('start').value,'0');
 assert.equal(nodes.get('submit-job').disabled,true);
});
test('有GPU保持整片增强，并在监测消失时禁用而不降级',async()=>{
 const {ctx,nodes}=await start(['/media'],[{uuid:'G',name:'GPU',temperature:70,used_mib:100,total_mib:16000}]);
 assert.equal(nodes.get('submit-job').disabled,false);
 assert.equal(nodes.get('gpu').disabled,false);
 const original=ctx.fetch;
 ctx.fetch=async url=>url.endsWith('/api/status')?{ok:true,json:async()=>({gpu:{gpus:[]}})}:original(url);
 await ctx.poll();
 assert.equal(nodes.get('execution').value,'cuda-full');
 assert.equal(nodes.get('submit-job').disabled,true);
});
test('预检失败禁提交且轮询不能重新启用，改参数清除旧结果',async()=>{
 const {ctx,nodes}=await start(['/media'],[{uuid:'G',name:'GPU'}]);
 ctx.setSelection(['/media/a.mp4'],[{path:'/media/a.mp4'}]);
 const original=ctx.fetch;
 ctx.fetch=async url=>url.includes('/api/preflight?')?{ok:true,json:async()=>({supported:false,reasons:['成品目录不可写']})}:original(url);
 await nodes.get('preflight-job').onclick();
 assert.equal(nodes.get('submit-job').disabled,true);
 await ctx.poll();assert.equal(nodes.get('submit-job').disabled,true);
 nodes.get('scale').onchange();
 assert.equal(nodes.get('preflight-result').textContent,'');
 assert.equal(nodes.get('submit-job').disabled,false);
});
test('参数改变后忽略迟到的旧预检结果',async()=>{
 const {ctx,nodes}=await start(['/media'],[{uuid:'G',name:'GPU'}]);
 ctx.setSelection(['/media/a.mp4'],[{path:'/media/a.mp4'}]);let release;
 ctx.fetch=()=>new Promise(resolve=>{release=resolve;});
 const pending=nodes.get('preflight-job').onclick();
 nodes.get('scale').onchange();
 release({ok:true,json:async()=>({supported:false,reasons:['旧参数失败']})});await pending;
 assert.equal(nodes.get('preflight-result').textContent,'');
 assert.equal(nodes.get('submit-job').disabled,false);
});
test('预检拒绝后直接提交事件也不发请求',async()=>{
 const {ctx,nodes}=await start(['/media'],[{uuid:'G',name:'GPU'}]);
 ctx.setSelection(['/media/a.mp4'],[{path:'/media/a.mp4'}]);let requests=0;
 ctx.fetch=async()=>{requests++;return {ok:true,json:async()=>({supported:false,reasons:['不可写']})};};
 await nodes.get('preflight-job').onclick();const before=requests;
 await ctx.submit({preventDefault(){}});assert.equal(requests,before);
});
test('重复提交事件只运行一轮批处理',async()=>{
 const {ctx,nodes}=await start(['/media'],[{uuid:'G',name:'GPU'}]);let calls=0,release;
 ctx.submitPending=()=>{calls++;return new Promise(resolve=>{release=resolve;});};
 nodes.get('task-dialog').close=()=>{};
 const first=ctx.submit({preventDefault(){}});
 await ctx.submit({preventDefault(){}});assert.equal(calls,1);
 release(0);await first;
});
test('文件全选正确区分空、半选及全选，目录不计入',async()=>{
 const {ctx,nodes}=await start();
 const files=[{path:'/a'},{path:'/b'},{path:'/folder',directory:true}];
 ctx.setSelection(['/a'],files);
 assert.equal(nodes.get('select-all').indeterminate,true);
 assert.equal(nodes.get('select-all').checked,false);
 ctx.setSelection(['/a','/b'],files);
 assert.equal(nodes.get('select-all').indeterminate,false);
 assert.equal(nodes.get('select-all').checked,true);
 ctx.setSelection([],[]);
 assert.equal(nodes.get('select-all').disabled,true);
});

test('任务确认默认返回，确认与Escape均正确结束且不重复打开',async()=>{
 const {ctx,nodes}=await start();const dialog=nodes.get('confirm-dialog');
 let opened=0,closed=0,focused=0;
 dialog.showModal=()=>opened++;dialog.close=()=>closed++;
 nodes.get('confirm-back').focus=()=>focused++;
 const pending=ctx.confirmTaskAction('删除记录？','原片和成品保留，不能续跑');
 assert.equal(opened,1);assert.equal(focused,1);
 assert.match(nodes.get('confirm-description').textContent,/不能续跑/);
 assert.equal(await ctx.confirmTaskAction('重复',''),false);
 nodes.get('confirm-back').onclick();assert.equal(await pending,false);
 const accept=ctx.confirmTaskAction('取消任务？','保留分片');
 nodes.get('confirm-accept').onclick();assert.equal(await accept,true);
 const escape=ctx.confirmTaskAction('删除记录？','');let prevented=false;
 dialog.oncancel({preventDefault(){prevented=true;}});
 assert.equal(await escape,false);assert.equal(prevented,true);assert.equal(closed,3);
});

test('失败卡片显示可操作原因，技术日志完整保留且默认折叠',async()=>{
 const {ctx}=await start();const raw='PermissionError: Permission denied\nTraceback\nworker.py';
 const result=ctx.renderTaskDetail(raw,'failed');
 assert.match(result.children[0].textContent,/应用账号写入权限/);
 assert.equal(result.children[1].tag,'details');
 assert.equal(result.children[1].children[1].textContent,raw);
 assert.equal(ctx.renderTaskDetail('等待调度','queued').textContent,'等待调度');
 assert.match(ctx.renderTaskDetail('No space left on device','failed').children[0].textContent,/存储空间不足/);
 const unknown='UnknownError: sample';assert.equal(ctx.renderTaskDetail(unknown,'failed').children[0].textContent,unknown);
});

// 中文提示词的大小写归一判定：'GPU采样' 归一后是 'gpu采样'，改写匹配方式时最容易回归这一点。
test('失败原因归类对中文关键字大小写不敏感',async()=>{
 const {ctx}=await start();
 assert.match(ctx.renderTaskDetail('GPU采样监测过期','failed').children[0].textContent,/GPU监测暂不可用/);
});

// 接口层的安全边界：BASE 是常量，path 只能是本应用相对接口；
// 任何指向其他主机/协议的 path 都必须在本机被拦下，不能发出去。
test('接口请求只允许同源相对接口路径',async()=>{
 const {ctx,calls}=await start();
 await ctx.apiCall('/api/status');
 assert.equal(ctx.lastRequestOptions.mode,'same-origin','浏览器必须阻止跨源请求');
 assert.equal(ctx.lastRequestOptions.redirect,'error','禁止接口重定向扩展请求范围');
 // 相对路径必须被解析成同源绝对地址：这就是"无法指向别的主机"的可核对证据。
 assert.equal(calls[calls.length-1],'http://nas.local/app/video-enhance/api/status',
   '相对接口应拼成同源绝对地址：'+calls.join(' '));
 const baseline=calls.length; // start() 自身的初始化请求不计入本轮判定。
 for(const bad of ['//evil.example/api','http://evil.example/api','/\\evil','/other/api/status']){
  await assert.rejects(()=>ctx.apiCall(bad),/接口路径非法|非同源/,'应阻止：'+bad);
 }
 // 同源断言本身：绕过前缀白名单也必须被拦下（此处构造出跨主机但同前缀的输入）。
 assert.equal(calls.length,baseline,'被阻止的请求不能发出去，实际多发 '+(calls.length-baseline)+' 个：'+calls.slice(baseline).join(' '));
});


test('真实任务卡片同时显示计算帧和可续跑保存帧',async()=>{
 const {ctx,nodes}=await start();nodes.get('job-filter').value='all';
 ctx.fetch=async()=>({ok:true,json:async()=>[{id:'progress-job',state:'running',
  spec:{source_path:'/media/sample.mp4',mode:'interpolate',target_fps:60,device:'cuda'},
  progress:{stage:'插帧',frames_done:48,working_frame:70,frames_total:120}}]});
 await ctx.loadJobs();
 const text=nodes.get('tasks').children[0].children.filter(n=>n.tag==='small').map(n=>n.textContent).join(' ');
 assert.match(text,/正在处理 70 \/ 120 帧/);assert.match(text,/已保存 48 帧/);
});

test('任务轮询保留技术详情展开状态，并拒绝过期响应覆盖新列表',async()=>{
 const {ctx,nodes}=await start();
 nodes.get('job-filter').value='all';
 const job=(id,name)=>({id,state:'failed',spec:{source_path:'/media/'+name,mode:'both',scale:2,target_fps:60},detail:'UnknownError: '+name,progress:{}});
 ctx.fetch=async()=>({ok:true,json:async()=>[job('detail-job','new.mp4')]});
 ctx.document.querySelectorAll=selector=>selector==='#tasks details[data-job-id]'?[{open:true,dataset:{jobId:'detail-job'}}]:[];
 await ctx.loadJobs();
 const card=nodes.get('tasks').children[0];
 const detail=card.children.find(n=>n.className==='task-error');
 assert.equal(detail.children[1].open,true);
 assert.equal(detail.children[1].dataset.jobId,'detail-job');
 let release;
 ctx.fetch=()=>new Promise(resolve=>{release=resolve;});
 const old=ctx.loadJobs();
 ctx.fetch=async()=>({ok:true,json:async()=>[job('latest','latest.mp4')]});
 await ctx.loadJobs();
 release({ok:true,json:async()=>[job('old','old.mp4')]});await old;
 assert.equal(nodes.get('tasks').children[0].children[0].children[0].textContent,'latest.mp4');
});
test('去噪修复档：产物名带 AI修复、行显隐正确',async()=>{
 const {ctx,nodes}=await start();
 ctx.selectModeFn('denoise');
 assert.equal(ctx.deriveNameFn('/media/sample.mp4','denoise',2,60),'sample[AI修复].mkv');
 assert.equal(nodes.get('fps-row').classList.contains('hidden'),true);
 assert.equal(nodes.get('scale-row').classList.contains('hidden'),true);
 ctx.selectModeFn('denoise_block');
 assert.equal(ctx.deriveNameFn('/media/sample.mp4','denoise_block',2,60),'sample[AI去块].mkv');
 assert.equal(nodes.get('fps-row').classList.contains('hidden'),true);
 assert.equal(nodes.get('scale-row').classList.contains('hidden'),true);
 ctx.selectModeFn('denoise_bm3d');
 assert.equal(ctx.deriveNameFn('/media/sample.mp4','denoise_bm3d',2,60),'sample[AI深度去噪].mkv');
 assert.equal(nodes.get('fps-row').classList.contains('hidden'),true);
 assert.equal(nodes.get('scale-row').classList.contains('hidden'),true);
 ctx.selectModeFn('both');
 assert.equal(ctx.deriveNameFn('/media/sample.mp4','both',2,60),'sample[AI超分×2·60fps].mkv');
 assert.equal(nodes.get('fps-row').classList.contains('hidden'),false);
 assert.equal(nodes.get('scale-row').classList.contains('hidden'),false);
});

test('批量提交预检拒收单片仅跳过该片，其余正常提交（0.3.12 回归：onSkipped 漏 path 形参致 path is not defined）',async()=>{
 const {ctx,nodes}=await start(['/media'],[{uuid:'G',name:'GPU'}]);
 ctx.window.crypto={getRandomValues(){}};
 nodes.get('task-dialog').close=()=>{};
 ctx.submitPending=realSubmitPending;
 let jobs=0;
 ctx.fetch=async(url,options)=>{
   const u=new URL(url);
   if(u.pathname.endsWith('/api/status'))return {ok:true,json:async()=>({version:'t',csrf:'c',gpu:{gpus:[{uuid:'G',name:'GPU'}]},dependency_profile:'t'})};
   if(u.pathname.endsWith('/api/preflight')){
     const path=u.searchParams.get('path');
     return {ok:true,json:async()=>path.endsWith('bad.mp4')?{supported:false,reasons:['帧率不支持或可能为VFR；仅支持CFR']}:{supported:true,reasons:[]}};
   }
   if(options?.method==='POST'&&u.pathname.endsWith('/api/jobs')){jobs++;return {ok:true,json:async()=>({ok:true})};}
   return {ok:true,json:async()=>({inputs:['/media'],shared:[],roles:{}})};
 };
 ctx.setSelection(['/media/bad.mp4','/media/good.mp4'],[{path:'/media/bad.mp4'},{path:'/media/good.mp4'}]);
 await nodes.get('task-form').onsubmit({preventDefault(){}});
 assert.equal(jobs,1);
 assert.match(nodes.get('toast').textContent,/已提交 1 个任务/);
 assert.match(nodes.get('toast').textContent,/1 个未提交：bad\.mp4（帧率不支持/);
});
