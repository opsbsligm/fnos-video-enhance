# fnos-video-enhance

飞牛 fnOS 应用中心第三方应用「视频增强」：本地 GPU 插帧（Practical-RIFE）、
超分（Real-ESRGAN realesr-general-x4v3）、双增强、去噪修复（神经去噪）与
去马赛克（DCT 域去压缩块，纯 CPU），接入飞牛统一登录与目录授权。

- 全部处理在 NAS 本机完成，帧数据不出机；原片只读，成品直接落在原片旁边。
- 分片检查点续跑；任务中心含进度、预计剩余、成品路径与历史清理。
- 仅支持 SDR/CFR，不支持 HDR/DV。兼容包针对 fnOS x86 + Tesla P100。

| 目录 | 说明 |
| --- | --- |
| `src/` | 后端（`server.py` 网关、`worker.py` 推理、`guard.py` GPU 闸门）、前端源码 `frontend.js`、静态资源 `www/` |
| `packaging/` | fnOS 应用包清单、生命周期脚本、图标 |
| `scripts/` | 打包、包验收、升级核验、CI 门禁脚本 |
| `tests/` | Python 单测（unittest）与前端单测（node:test） |
| `README.txt` | 逐版本变更与真机验收记录（含回滚办法） |

## 增强方式

| 模式 | 说明 | 输出 |
| --- | --- | --- |
| 插帧 | Practical-RIFE 提升流畅度 | 目标帧率，分辨率不变 |
| 超分 | Real-ESRGAN 放大清晰度 | 放大至目标分辨率，帧率不变 |
| 双增强 | 先源分辨率插帧再超分，单遍流水线 | 放大 + 目标帧率 |
| 去噪修复 | Real-ESRGAN 4× 上采样再缩回，清噪点与轻微压缩瑕疵 | 分辨率、帧率不变 |
| 去马赛克 | ffmpeg `deblock` DCT 域去块，专治 8×8 压缩块边缘，保细节；纯 CPU 不占 GPU | 分辨率、帧率不变 |

## 开发约束

- `src/version.py` 是版本单一来源；`PIPELINE_TAG` 改动会使现存任务检查点失配，需知情再改。
- `src/www/app.js` 是提交进仓库的构建产物（fnpack 取 `www/`，不在 NAS 上跑 node）。
  改 `src/frontend.js` 后必须 `npm run build` 并提交产物，CI 会逐字节校验。
- 本地 Python 单测需 3.11+（依赖 Linux `dir_fd`/`O_PATH` 语义），Apple 自带 3.9 不可用。
- 模型权重、运行包、`.fpk` 产物不入库。

```
npm ci && npm run build
python3.11 -m unittest discover -s tests
node --test tests/*.test.mjs
```
