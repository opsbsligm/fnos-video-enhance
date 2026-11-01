#!/bin/bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
NAS=${NAS:-root@192.168.3.30}
BUILD=/vol2/@appdata/video-enhance-build/video-enhance
(cd "$ROOT" && npm ci && npm run build)
# GPU模型和依赖已在本任务NAS构建目录封装。不要重新下载安装驱动。
COPYFILE_DISABLE=1 tar -C "$ROOT/packaging" --exclude=.DS_Store --exclude="._*" -cf - . | ssh "$NAS" "tar -C '$BUILD' -xf -"
COPYFILE_DISABLE=1 tar -C "$ROOT/src" --exclude=__pycache__ --exclude=frontend.js --exclude='*.mjs' --exclude='*.bak*' --exclude=trap.js --exclude=.DS_Store --exclude="._*" -cf - . | ssh "$NAS" "tar -C '$BUILD/app' -xf -"
ssh "$NAS" "python3 - '$BUILD/app' '$BUILD/../excluded-package-files'" < "$ROOT/scripts/clean_package_stage.py"
ssh "$NAS" "chmod +x '$BUILD'/cmd/*; cd '$BUILD'; TMPDIR=/vol2/@appdata/video-enhance-packtmp nice -n 19 ionice -c 3 fnpack build"
