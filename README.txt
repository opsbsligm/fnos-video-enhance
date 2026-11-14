视频增强 0.3.10：HOMELAB内部预览版（安装版=0.3.10 已热部署生产并POC验收；历史：0.1.0首发→0.3.1版本收口→0.3.2完成语义/文案→0.3.3 GPU全功率→0.3.4产物简化→0.3.5/0.3.6调试修复→0.3.7 fd超限→0.3.8安全闭环→0.3.9去噪修复+UI卡片化→0.3.10去马赛克/去压缩块）

架构：飞牛FPK + package独立账户 + 统一网关Unix socket + 系统目录授权/用户ACL + SQLite任务库 + 独立模型进程。
前端参考飞牛影视/音乐的使用模式：媒体资源、任务中心、目录授权、运行设置。不是它们的私有代码。
运行包：Python3.11、PyTorch2.5.1+cu121、OpenCV、CUDA用户态、FFmpeg与模型；宿主驱动另需存在。当前实测fnOS1.2.0800 x86/P100。

npm ci && npm run build
本地单测建议Python3.14；目标NAS使用封装Python3.11。Apple自带Python3.9的dir_fd实现不可用于这些Linux安全测试。
目标fnpack构建目录：/vol2/@appdata/video-enhance-build/video-enhance；TMPDIR必须在数据盘。

首版：管理员；手动选片；独立插帧/超分；CPU2秒最大320宽功能代理预览；GPU样片/整片受保护默认关闭；取消和应用重启中断识别。
不支持：HDR/DV、VFR/隔行、断点续跑、NVENC自动编码。原片不覆盖，成果MKV保留常见音轨字幕附件和章节；输出尺寸/时长/帧率及轨道数量检查。
GPU策略文件默认没有经过验收的设备，不能从页面绕过；共享P100上的WeKnora模型不动，DGX Flash Next不接入。

原工作区Documents源码被云同步自动置为dataless导致读取超时；以此非云目录与NAS源码备份为维护基线。
完整FPK仅内部验证使用。公开分发前需核对模型权重许可、FFmpeg mediasrv构建的源码和再分发义务、所有依赖授权以及干净机器安装兼容性。

部署验收补充（2026-10-07）：
已通过官方应用中心安装，专用UID980；系统网关UI四页已验证，普通媒体目录已有授权。
飞牛祖先目录仅允许穿越，open_beneath采用Linux O_PATH|O_DIRECTORY|O_NOFOLLOW祖先句柄，最终目录/文件才读取；不放宽源片访问。
本次部署对自带成果目录配置了liguangming的读写及默认ACL，并对上级appshare目录只给穿越权限。全新机器仍需配置成果共享目录的当前管理员权限；尚非公开版一键无配置安装。
接口验收：未登录/非管理员/越界403；RIFE2秒样片120帧60fps、SR48帧256x144/24fps，音轨保留、成果下载200；GPU任务held、运行中取消通过。整片4K/GPU画质与生产负载验收未完成。

追加解码验收纠错：原插帧样片在复制重封装后发生H264码流解析错误（元数据正确不代表可播放）。纯FFmpeg复现后加入h264_mp4toannexb，并在worker实际解码计帧与检查错误；旧样片任务标failed，修复后重新测试。新成果才作为交付基线。原来的初轮“样片通过”仅为元数据验收，不代表逐帧解码通过。
修复后复验通过：eb2e37ea23bc42a9adab3509f381a762插帧120可解码帧/60fps，826f56fde9d64360862642752dc1c16a超分48可解码帧/256x144/24fps；均保留1音轨、2.026秒，ffprobe stderr无解码错误。取消、GPU保护、重启历史保留验收通过。验证JSON在NAS /vol2/@appdata/video-enhance-tests/installed-verification.json。

目录授权热修复（2026-10-07）：
两处入口共用新的结果处理，等待SDK初始化；成功后自动同步并定位所选输入目录，取消或失败不误报成功，禁止重复点击。不要求crypto.randomUUID，使用getRandomValues生成回调state；独立网页回调校验应用、操作、状态与随机state。
新增“输入 + 输出”目录用途，不擅自覆盖现有目录用途。“仅输出”目录明确标注不显示在媒体列表；重新授权不会改变已设用途，授权后给出说明。
现场测试：两个入口打开选择器、确认已有媒体返回与用途说明、取消后的中文未完成提示/按钮恢复。当前飞牛取消会返回模糊Operation failed，无法区分全部取消/真正失败，采用未完成提示而非伪造成功。独立网页成功/取消/错误回调通过单测，未另做实际独立网页登录授权验收。
7项前端授权测试、7项安全单测和4种目录角色输入/输出矩阵测试通过。GPU策略未改，现有模型PID未变、网关健康。

UI 重设计记录（2026-10-08，0.3.0，未部署）：
前端按飞牛 fnOS/Semi Design 令牌重构（基线提取自本机 /usr/trim/www，见 /tmp/fnos-baseline/index.css）。style.css 亮/暗双令牌，body[theme-mode=dark] 切换；已修复暗色下 --text 未随 body 重写导致的深色文字缺陷。index.html 四页侧栏+sticky topbar+toast；资源引用加 ?v=0.3.0 破缓存。app.js 由 esbuild 重新打包（55,457B）。
验证：python单测45通过、node测试9通过；mock 后端（status/gpu/directories/browse/jobs/preflight）在 iab 浏览器截图验证了四页亮/暗渲染、文件勾选→双增强对话框→提交→"已提交2个任务"toast→任务中心四状态卡。备份：src/www.bak-ui-20261007、src/frontend.js.bak-ui-20261007、src/www/style.css.bak-dark-20261008。
待验证：真机 fnOS 内嵌（含 os/theme 联动）、独立网页暗色、端到端授权。空目录 docs/ records/ 已删除；*.bak-* 已加入 .gitignore。

版本收口记录（2026-10-08，0.3.1，未部署）：
版本单一来源 src/version.py：VERSION=0.3.1；PIPELINE_TAG=0.3.0-cfr-segments 作为检查点指纹域与应用版本解耦（升应用小版本不使在跑任务失配）；PIPELINE_VERSIONS 为可续跑集合。server/job_store/worker/package.json/manifest/前端资源引用全部对齐 0.3.1。
行为后端化：GET /api/jobs 每任务返回 resume_capable（后端判定续跑资格），前端任务卡改用该字段；设置页能力/限制/执行策略由 status 动态渲染（#cap-list/#limit-list/#exec-policy），前后端文案不再两处维护。
新增接口合同集成测试 tests/test_integration_api.py：真 Unix socket HTTP 起服务（patch VAR/OUTPUT/DB 与平台API，不起 scheduler），断言 status（version/user_uid/csrf/gpu/capabilities/limits/execution_policy）、directories（shared/roles/inputs/outputs）、browse ACL 过滤、jobs resume_capable 正/反例、POST directories 改用途与 CSRF 403、静态首页 CSP。
回归：python unittest 51 项通过（含集成 6 项）、node --test 9 项通过、esbuild 打包 54.4KB。仍待：真机 fnOS 内嵌部署验证（现网 GPU 任务在跑，升级需拍板停机窗口）。

完成语义与文案修复（2026-10-09，0.3.2，已部署）：
任务"完成"以逐帧解码校验+输出尺寸/帧率/时长/轨道复核为准，成果文件名含增强参数；修复用户实测反馈的完成提示与任务卡文案。当次以 stop→install-fpk 升级生效（install-fpk 对运行中应用只报 installed 不替换 target，属 fnOS 行为，已记踩坑）。

GPU 全功率（2026-10-10，0.3.3，已部署）：
用户指令解除全部算力限制（不与 WeKnora 协作让算力），仅保留硬件安全闸门。
解除项：占空比让步（gpu_duty=1.0 时 worker 不做占空睡眠）、显存预算（budget_mib=0 时不设 set_per_process_memory_fraction，P100 16G 全可用）、nice/ionice 低优先级包装（full_power 时不降优先级）、启动忙碌度闸门（full_power 时跳过 max_start_utilization 检查）。
保留项：温度≥85℃拒启/中止、显存余量<1GiB 拒启（防 OOM 硬崩）。策略文件 /vol2/@appdata/video-enhance/gpu-policy.json 增加 full_power:true；回滚=恢复 gpu-policy.json.bak-fullpower-20261010 并重启应用。
部署：0.3.3 升级时 install-fpk 仍未替换运行中应用的 target（stop 后也未替换），最终 uninstall→install-fpk -v 2 完成；升级前数据备份在 NAS /vol2/@appdata/video-enhance-preupgrade-20261010/（任务库/目录授权/策略完整保留，已回读）。
验收：/api/status 回填 version=0.3.3、execution_policy="全功率…"、policy_config{budget_mib:0, gpu_duty:1.0, reserve_mib:1024, max_temperature:85}；verify_installed.py 全过（未登录/非管理员边界、CPU插帧+超分 completed 且下载200、GPU样片 completed、运行中取消 cancelled、路径越界403）；验证JSON /vol2/@appdata/video-enhance-tests/installed-verification.json。回归：python 单测53项、node 9项、esbuild 55.7KB。
待验证：真实片源 GPU 整片超分/双增强的耗时与画质（用户已确认超分功能可用）；与 WeKnora 推理并发下 P100 温度实测（当前空闲63℃）。

产物逻辑简化与UI收口（2026-10-10，0.3.4，部署中）：
去掉"产出文件夹"概念：POST /api/jobs 不再接受 output_mode/output_root，成果固定直接产出到原片旁边（随源命名）；/api/status 的 output_default 改为 demo_dir（应用私有自测区 VAR/demo）；新增 app_acl：VAR 私有区（自测源/工作区）由应用自管权限不经飞牛 ACL，其余路径照旧走 platform_api.check_acl。initialize() 确保 VAR/demo 存在。packaging/cmd/main 自测源生成位置同步迁移到 $VAR/demo/功能自测.mp4。
前端：提交对话框删除"成果位置/成果目录"两项；四处文案重写为"成品直接生成在原片旁边"；目录页移除默认成果目录行；能力/策略横幅动态显示 status.execution_policy。样式延续 0.3.0 Semi Design 令牌基线。
测试适配：集成测试改断言 demo_dir、outputs⊆inputs 且含 VAR/demo；无 output_root 时成果落原片目录。回归：python 53 项、node 9 项、esbuild 通过。
部署（已验证）：踩坑修正——fnpack 打包源是构建目录顶层 app/（scripts/package.sh 的既有映射：packaging/*→$BUILD、src/*→$BUILD/app），首轮误把 src 同步到 $BUILD/src 导致包内仍是 0.3.3；按 package.sh 映射重新同步后二次打包（约28分钟）部署成功。升级仍走 stop→uninstall→appcenter-cli install-fpk xxx.fpk -v 2（注意：install-fpk 是 appcenter-cli 子命令，不是独立命令）。
验收（已验证）：target/version.py=0.3.4、index.html ?v=0.3.4；/api/status 回填 demo_dir=/vol2/@appdata/video-enhance/demo、execution_policy=全功率；gpu-policy.json 全功率策略经 uninstall 后完整保留（full_power=true, gpu_duty=1.0, budget_mib=0）；verify_installed.py（demo_dir 取源版）EXIT=0 全过：CPU插帧+超分与GPU样片均 completed、下载200、运行中取消 cancelled、越界403。产物逻辑直证：5 个成品 mkv 全部直接落在自测源旁边（VAR/demo/），无新建目录。

三项行为级修复与升级（2026-10-14，0.3.7，已部署并验收）：
①both整片拼接flip撕裂修复：交错合并逐段解码回验+必要时翻转修正，杜绝上下半幅互换（旧坏件：小月喵-01[AI超分×4·60fps].mkv，撕裂产物639MB，位于NO_SAY，未删除）。
②样片封装时间戳偏移修复：样片与整片统一走分片合并封装路径。
③fd超限修复（worker合并阶段）：2370分片concat清单改用/proc/self/fd/{store.fd}/子目录句柄、启动时提升RLIMIT_NOFILE至min(hard,65536)、run()透传ffmpeg stderr尾部到任务日志。实测76d44c79（广深虾酱-私拍-05，113748帧整片插帧）续跑成功，成品8.8GB 4K@60/1895.8s，抽帧目检与源一致。
版本收口：version.py/manifest/package.json=0.3.7；PIPELINE_TAG保持0.3.0-cfr-segments（检查点向后兼容，实测旧任务可续跑）。前端资源引用与brand-meta静态串同步0.3.7（在线target已热修并HTTP 200核验；0.3.7.fpk包内www/index.html静态串仍为0.3.6，构建源已同步，下一版fpk自然对齐）。
测试：NAS runtime python3.11 跑 python 单测77项通过；node前端测试32项0失败；esbuild 62.1kb。
包验收：verify_package.py checksum_verified=true、lifecycle_verified=true、11核心文件齐、无开发残留。fpk=NAS releases/video-enhance-0.3.7.fpk（3051236708B，md5=f90fc3d70fcaedf2d89d7111a8d27493）。
升级：备份/vol2/@appdata/video-enhance-preupgrade-20261014（tasks.sqlite 4任务/directories.json/gpu-policy.json，baseline逐字节比对）→ stop→uninstall→install-fpk -v 2→start。
升级核验：verify_upgrade.py 通过——运行版本0.3.7、4个原任务身份/参数完整保留、integrity ok、directories.json与gpu-policy.json与备份逐字节一致。monitor.json为应用运行时采样状态（温度/时间戳），非用户配置，已从baseline比对清单剔除并记录原因（纳入比对是备份脚本选型失误，已修正认知：配置基线只含用户配置）。
执行策略：全功率（占空/优先级让步解除，仅温度≥85℃与显存余量<1GiB安全闸门），status回填execution_policy与gpu.available=true（P100-16G）。
回滚：恢复preupgrade-20261014各文件 + releases内旧fpk走同一路径重装。
遗留：f8cdb9bc旧坏件成品待用户决定重跑或手动处理（app不擅删用户目录文件）；明暗主题/窄屏真机高清截图验收仍缺。

安全闭环与版本收口（2026-11-01，0.3.8 源码，未发布 FPK）：
GitLab：https://www.bsligm.com:8445/codex/fnos-video-enhance；codex 开发，liguangming 审查批准合并。
修复/加固：前端接口同源校验；错误提示改为子串归类；监测请求固定回环且拒绝重定向；包兼容 MD5 显式声明非安全用途。
CI：版本一致性、Python语法/单测、前端构建逐字节核验/单测、打包清单、SAST、密钥和 npm 依赖扫描、安全裁决。三份安全报告均强制存在、成功且可解析；依赖报告按实例官方 schema 15.2.4 校验。Critical/High/Medium/Unknown 阻断，误报/兼容例外逐项注明 liguangming 裁决、理由、重查条件和到期日。行号变化、过期、重复及已失效条目均阻断；审查批准绑定精确 HEAD。
审查例外：三处 MD5 为现有 fnOS 包格式兼容校验，不作为防篡改证明；浏览器 fetch 告警在当前同源边界下判误报。仅对构建可复现的 src/www/app.js 排除重复 SAST，自有源码、其余静态资源仍在扫描范围，第三方 npm 依赖由 npm audit 覆盖。
边界：本次是源码/CI交付；NAS 外置 PyTorch/CUDA/FFmpeg 运行包未纳入此 npm 扫描，不宣称 FPK 全供应链验收。上线需另外生成运行包清单、漏洞核查、fnpack 打包、备份升级和真机抽帧验收。线上0.3.7继续运行，PIPELINE_TAG 保持0.3.0-cfr-segments。

去噪修复模式与UI卡片化（2026-11-10，0.3.9，已部署并POC验收）：
新增第四种增强方式「去噪修复」：RealESRGAN 4×上采样推理再缩回源尺寸，清除块状伪影/噪点/JPEG压缩痕迹，分辨率与帧率不变；成品命名 原片名[AI修复].mkv。
后端：media_plan.MODES 加 denoise，plan() 原生尺寸修复（不改 ow/oh/ofps）；worker do_sr 含 denoise 且跳过放大计算，stage 显示「去噪修复」。
前端：增强方式从顶部 seg 组移入提交对话框，改 2×2 卡片网格（插帧/超分/去噪修复/双增强），窄屏单列；帧率/倍率行按所选模式显隐（applyModeRows）；对话框 520→560px；四模式均有独立文案说明。
POC验收（真机 P100）：demo 源 4K双增强自测-1080p24.mp4（1920×1080@24）→ 样片-4K双增强自测-1080p24[AI修复].mkv，48帧 162.8s 完成，逐帧解码校验通过，1920×1080@24 保持；抽帧目检与源场景一致无撕裂，PSNR y:32.6/average:34.3（去噪改图重编码合理区间）。
request_id 发现：服务端校验 64 位小写 hex（uuid4），与前端 authState 生成的 32 字节随机 hex 对齐；API 直调若带 request_id 须用 64 hex（不带则由服务端自动生成）。
测试：python unittest 95 项、node --test 36 项全绿；esbuild app.js 63.4kb 逐字节可复现。
版本收口：version.py/manifest/package.json/index.html 资源引用同步 0.3.9；PIPELINE_TAG 保持 0.3.0-cfr-segments（检查点兼容），PIPELINE_VERSIONS 追加 0.3.9。
部署：构建目录 tar 分步同步（nohup/setsid 后台脚本在此会话环境不可靠，已验证踩坑）→ fnpack build 成功；releases/video-enhance-0.3.9.fpk（3051194863B，md5=5311b9d1ee9b9857c328ea417fac70e9）；verify_package 全过。升级合同 stop→uninstall→install-fpk -v 2→start（start 报 10500 但 25s 后 running，fnOS 行为）；升级前备份 /vol2/@appdata/video-enhance-preupgrade-20261110/（tasks.sqlite md5=5475db0438fbc310af9457355da339ec）。
验收：NAS 现场 version=0.3.9、directories.json 保留；GPU 当时被 WeKnora 占用与本 app 无关。
回滚：releases 内旧 fpk + preupgrade-20261110 备份走同一升级合同。
遗留：明暗主题/窄屏真机高清截图验收仍缺（Safari 截图工具只返回缩略图）。

系统授权列表为空的授权回归修复（2026-11-10，热修已部署，MR !3 走 CI 门禁后随 0.3.10 收口）：
根因：0.3.9 升级（uninstall→install）后飞牛 getSharedAccessibleFolders 返回空列表，而 authorized_roots 只认系统 API 路径，directories.json 已配置目录（首次授权时已经过飞牛 ACL 验证）全部被过滤；ec3dfc13（异星迷航 4K 双增强整片，67.9%，检查点完好）续跑即 failed「未获应用目录授权」。现场直证：checkUserACL 对 /vol2/1000/媒体 读/写/删全 true，system API 返回 []。
修复：authorized_roots 以系统 API 路径与 directories.json 已配置路径的并集作候选（role 语义不变）；GET /api/directories 的 shared 列表同取并集（目录授权页不再空白）；POST /api/directories 已配置路径允许重新设置用途，新路径仍需系统 API 确认（原安全语义保留）。
验证：python3.11 96 项全绿（含新增回归 test_configured_directories_survive_empty_system_api）；NAS 真机热修（server.py 备份 server.py.bak-authfix-20261110）重启后 /api/directories 恢复返回 4 个已配置目录（typo 目录 /vol2/1000/Dowanloads 不存在被 open_beneath 自动过滤）；ec3dfc13 续跑受理→running，worker 起、帧数推进（220944→221088，68.0%），GPU 79℃ 低于 95℃ 闸门。
回滚：cp server.py.bak-authfix-20261110 server.py 并重启应用（改动仅授权候选集来源，无数据结构变化）。
已知坏件处置：f8cdb9bc（小月喵-01 双增强，flip 撕裂修复前产物）任务已从库清理；源片小月喵-01.mp4 已不在 NO_SAY（用户侧仅留 -02 与坏件成品 611MB），成品留原位待用户决定是否重跑或删除。

去马赛克（去压缩块）模式（2026-11-10，0.3.10，已热部署生产并POC验收）：
新增第五种增强方式「去马赛克」denoise_block：ffmpeg `deblock` DCT 域去块滤波挂在解码与编码之间，
专治 H.264/H.265 8×8 压缩块边缘锯齿，保细节、不改分辨率与帧率；纯 CPU 不占 GPU（区别于 denoise 的 RealESRGAN 神经修复）。
成品命名 原片名[AI去块].mkv；4K 实测 ~11fps 纯 CPU（12 核），1080p 更快。
与去噪的语义区分（用户澄清后定）：denoise=神经去噪/去伪影（GPU，RealESRGAN），denoise_block=去压缩块边缘（CPU，DCT 去块）；
二者都能"减块"，但去马赛克专治块边缘且保细节、不动整体纹理，故单列一档。
后端：media_plan.MODES 加 denoise_block，plan() 保源分辨率/帧率，输出名标签 [AI去块]；
worker 编码侧条件挂 -vf deblock，denoise_block 在 device=cuda 下 gpu 置 False（按源尺寸、不 proxy、不加载模型、不占显存）；
server 对 denoise_block 免 GPU 共享验收与 require_gpu；capabilities 补「去马赛克（去压缩块）」。
前端：增强方式卡片加「去马赛克 · 去压缩块·保细节」；修正 modeLabel 此前漏 denoise 会误显示"超分→2倍"的问题。
POC验收（真机 P100/12核，纯CPU deblock，与 ec3dfc13 GPU 大任务解耦验证并发）：
提交 4bfe877e denoise_block 10s 样片（源 IPX-811-U 2560×1440@30）→ 样片-IPX-811-U-美颜版[AI去块].mkv（9.5MB），
completed「原尺寸样片通过检查 2560×1440@30.0001fps 10.01s」（分辨率/帧率不变）；抽帧同一区域(800,400)放大目检：去块后头发/墙面块边更平滑、细节未糊。
测试：python3.11 97 项全绿（新增 test_denoise_block）、node --test 36 项全绿（新增 denoise_block 命名与行显隐）；esbuild app.js 63.4kb。
版本收口：version.py/manifest/package.json/index.html 同步 0.3.10；PIPELINE_TAG 保持 0.3.0-cfr-segments（检查点兼容），PIPELINE_VERSIONS 追加 0.3.10。
部署：0.3.10 热部署生产（顶层 worker.py/server.py/media_plan.py/version.py + www 全量同步，顶层=运行时权威，src/ 一并对齐）；
升级前备份 /vol2/@appdata/video-enhance-prehotfix-20261110/；appcenter-cli stop/start 后 version=0.3.10、capabilities 含去马赛克。
遗留：本次为热部署（非 fnpack 正式 0.3.10 fpk）；ec3dfc13（异星迷航 4K 双增强，~70%）重启后续跑中，尚未跑完抽帧验收；
明暗主题/窄屏真机高清截图验收仍缺；正式 0.3.10 fpk 打包可随下次统一收口。

深度去噪模式 denoise_bm3d（2026-11-10，0.3.11，已热部署生产并POC验收）：
新增第六种增强方式「深度去噪」denoise_bm3d：ffmpeg `bm3d` 块匹配三维去噪，纯 CPU 保分辨率/帧率，
与 denoise_block（deblock 快速档）形成去马赛克双档：deblock 快（4K ~11fps）治块边缘，bm3d 慢（1440p ~0.66fps）深度去噪保结构。
成品命名 原片名[AI深度去噪].mkv。
后端：media_plan.MODES 加 denoise_bm3d（标签 AI深度去噪）；worker pure_cpu 覆盖两模式、-vf bm3d 分支、stage「深度去噪」；
server cuda 准入排除 bm3d（纯 CPU 不占显存）、capabilities 补「深度去噪/去块（BM3D）」。
前端：增强方式卡片加「深度去噪 · BM3D·保结构」；modeTitle/modeLine/deriveName/modeLabel 补全；版本 0.3.11。
POC验收（真机 12核纯CPU）：d2bb2592 denoise_bm3d 10s 样片（源 IPX-811-U 2560×1440@30）→ 样片-IPX-811-U-美颜版[AI深度去噪].mkv（9.68MB），
completed「原尺寸样片通过检查 2560×1440@30.0001fps 10.01s」；t=2/5/8s 抽帧与源对比目检：构图/细节一致无撕裂，噪点/压缩痕迹更平滑。
实测 1440p 300帧 296s（~1.0fps，12核全功率），比 1080p 5s 125s 的预测试略快。
测试：python3.11 98 项全绿（新增 test_denoise_bm3d）、node --test 37 项全绿（新增 indexHtml/selectMode/deriveName 断言）；esbuild app.js 64.6kb。
版本收口：version.py/manifest/package.json/index.html 同步 0.3.11；PIPELINE_TAG 保持 0.3.0-cfr-segments（检查点兼容），PIPELINE_VERSIONS 追加 0.3.11。
部署：0.3.11 热部署生产（顶层+src 四 py 全量同步 + www 全量同步，逐文件 md5 回读一致）；升级前备份 /vol2/@appdata/video-enhance-prehotfix-bm3d-20261110/；
appcenter-cli stop/start 后 API 回读 version=0.3.11、capabilities 含 BM3D、gpu.available=True。
CI收口：分支 feat/denoise-bm3d-0.3.11 → MR !5（codex 提交 4f138d5，11 文件 +46/-25，与 main 内容差异即 0.3.11 全部改动）；
流水线 1708 等待共享 runner（runner 8 被 moonlight project 6 mutation-battery 长跑占用，单并发），结果与 liguangming 审查合并记录待补。
回滚：cp /vol2/@appdata/video-enhance-prehotfix-bm3d-20261110 各文件回位并重启应用（无数据结构变化）。
遗留：本次为热部署（正式 0.3.11 fnpack fpk 未打，可随下次统一收口）；MR !5 审查/合并待 runner 空档跑完 1708；
ec3dfc13（异星迷航 4K 双增强，暂停于 ~71.6%）BM3D 样片验收后已 resume 续跑；明暗主题/窄屏真机高清截图验收仍缺。
