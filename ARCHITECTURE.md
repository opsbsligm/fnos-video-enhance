# 视频增强：飞牛原生应用架构

## 使用方式
参照飞牛影视、音乐的入口与管理模式，采用桌面图标、统一登录、原生目录选择和四页界面：媒体资源、任务中心、目录授权、运行设置。插帧与超分独立提交，不自动批量改写文件。

## 数据流
飞牛桌面 → 系统网关 → Unix socket Python API → SQLite队列 → 独立算法worker → 新目录MKV成果。
系统授权API提供共享目录清单及当前用户ACL；每次提交、启动和运行中持续检查。只读输入fd与逐级O_NOFOLLOW限制路径，原片不覆盖。音轨、字幕、附件尽量复制并核验轨道数量；不声称复杂时间轴的所有格式已通过。

## 封装
FPK带Python3.11、torch2.5.1+cu121、CUDA用户态、OpenCV、FFmpeg、RIFE4.26和RealESRGAN general-x4v3。使用专用应用账户；不映射Docker socket，不接入模型网关、不修改DGX。NVIDIA驱动由宿主提供，首版不能承诺所有GPU开箱即用。模型与依赖许可需在公开发布前独立审核。

## 生产负载保护
P100已有WeKnora模型。应用默认无任何已验收GPU，GPU任务进入held。即使监测到瞬时空闲也不能自动放行。共享GPU的显存门槛、温度和进程监控只是安全兜底，不能保证零影响。生产放行应采用专用设备或经实测认可的资源窗口；不能通过停止现有模型实现验收。
CPU代理预览限制2秒、320像素宽、2线程、低CPU/IO优先级，并屏蔽CUDA设备。它仅证明算法与流程功能，不代表4K最终画质和耗时。

## 首版限制
SDR、恒定帧率、逐行；HDR/DV/VFR/隔行拒绝；CPU libx264编码；没有断点续跑。重启中未完成任务标记interrupted；取消整个进程组；成果须验尺寸、帧率、时长和轨道。原片只读，输出独立目录；不处理已知色情目录。

## 回滚
仅停止/卸载video-enhance；保留源码、FPK与成果副本。勿删除用户媒体或修改其他推理服务。应用中心卸载是否删除appdata需在执行前确认，成果另行备份。

## 前后端能力矩阵（0.3.0 核对，2026-10-08）
| 前端操作 | 接口 |
| --- | --- |
| 页面刷新/GPU卡/运行设置 | GET /api/status（version、gpu、inference、dependency_profile、output_default） |
| 目录清单与用途切换 | GET/POST /api/directories（roles: input/output/both/disabled） |
| 浏览目录/上级/面包屑 | GET /api/browse（带请求代际标识） |
| 查看参数 | GET /api/probe |
| 提交前预检 | GET /api/preflight（片源支持原因+全尺寸目标参数） |
| 提交任务（插帧/超分/双增强） | POST /api/jobs（request_id 幂等；CPU预览/GPU样片/整片） |
| 任务列表/搜索/筛选/进度卡 | GET /api/jobs |
| 暂停/续跑/取消 | POST /api/pause、/api/resume、/api/cancel（保留检查点） |
| 删除记录（成品保留） | POST /api/delete |
| 下载成果 | GET /api/output?id=（受目录ACL约束） |
前端无孤儿调用；后端无未暴露路由缺口。已收口项（0.3.1）：status 的 capabilities/limits/execution_policy 由设置页 renderPolicy() 动态渲染，前端不再硬编码边界文案；任务续跑资格由后端 GET /api/jobs 的 resume_capable 统一判定（spec.pipeline_version ∈ PIPELINE_VERSIONS 且处于可续状态），前端不复制版本串。版本单一来源 src/version.py（VERSION 与 PIPELINE_TAG 解耦，检查点指纹域不随应用小版本漂移）。
接口合同集成测试：tests/test_integration_api.py 以真 Unix socket HTTP 起服务（不起 scheduler），对 status/directories/browse/jobs/output 路由与 CSRF/静态 CSP 做形状断言，覆盖前端全部消费字段。
