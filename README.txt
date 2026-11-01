视频增强 0.3.3：HOMELAB内部预览版（安装版=0.3.3，2026-10-10 已部署并验收；历史：0.1.0首发→0.3.1版本收口→0.3.2完成语义/文案→0.3.3 GPU全功率）

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

安全闭环与版本收口（2026-11-01，0.3.8，源码/CI 已就绪，尚未打包部署）：
背景：接入 GitLab CI 后发现安全扫描"全绿"是假象——allow_failure:false 只约束分析器退出码，不约束发现数量；pipeline 980 的 semgrep 报出 24 条（含 1 条 High）作业仍 success，本实例漏洞看板 API 返回 404（无漏洞管理许可）。
真实修复（不做静默豁免）：①前端接口层补同源边界（path 必须 /api/ 前缀、非协议相对、URL() 解析后断言同源，被阻止的请求一个都不发出）；②失败原因分类的正则改为小写子串匹配（任务日志长度不可控，正则回溯会造成同步卡顿），并补中文关键字大小写归一回归测试；③guard.py 推理监测由 urlopen 改为固定回环主机的 http.client，只接受 200 且不跟随 3xx（跳转目标由被监测端决定，那才是真正可用的出站入口）——已在 NAS 用生产 runtime python3.11 + 真实回环 HTTP 服务实测 200 正常/302 与 500 拒绝/低位端口拒绝，指标解析与改前一致；④三处 hashlib.md5 属 fnOS 包契约（manifest checksum）而非安全摘要，加 usedforsecurity=False 显式声明，digest 值逐位不变。
新增门禁：scripts/ci/assess_sast_findings.py 读取 SAST/密钥/依赖三份报告自建裁决，Critical/High/Medium 默认阻断；豁免须逐条登记指纹+理由+复核人+到期日，过期或已失效均报错。指纹含行号，代码一改即失效、必须重新定性。流水线新增"依赖漏洞扫描"（npm audit→GitLab 依赖扫描报告；官方 gemnasium 镜像已需认证拉取、CI 无 docker 执行器，registry 须显式指向 npmjs，runner 默认 npmmirror 未实现 audit 接口）与"安全裁决门禁"（needs:optional 汇聚三份报告）。门禁自身有 12 项测试。
测试：Python 90 项（含新增 12 项门禁测试与 2 项安全回归）、前端 34 项、构建产物逐字节一致、版本五处一致=0.3.8。PIPELINE_TAG 保持 0.3.0-cfr-segments，检查点向后兼容。
未闭合：0.3.8 的 fpk 尚未在 NAS 用 fnpack 构建，线上仍运行 0.3.7（target 内 index.html 静态串已热修为 0.3.7，包内为 0.3.6 的偏差会在下次打包自然消除）；真机抽帧画质与明暗/窄屏截图验收仍缺；遗留坏件 f8cdb9bc（NO_SAY 下撕裂成品 639MB）仍待用户决定重跑或手动处理，app 不删用户目录文件。
