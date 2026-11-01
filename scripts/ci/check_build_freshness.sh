#!/usr/bin/env bash
# 构建一致性门禁：仓库内的 src/www/app.js 必须等于 src/frontend.js 的当前构建结果。
#
# 背景：app.js 是提交进仓库的产物（fnpack 直接取 www/，不在 NAS 上跑 node）。
# 一旦改了 frontend.js 忘记 npm run build，装到飞牛上的还是旧界面，
# 而本地"看起来是对的"。此门禁在 CI 用同一命令重建并逐字节比对。
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root"

out="$(mktemp -t ve-app-js-XXXXXX.js)"
trap 'rm -f "$out"' EXIT

./node_modules/.bin/esbuild src/frontend.js --bundle --minify --target=es2020 --outfile="$out" >/dev/null 2>&1

if cmp -s "$out" src/www/app.js; then
  echo "构建一致：src/www/app.js 与 src/frontend.js 的构建结果逐字节相同。"
  exit 0
fi

echo "构建不一致：src/www/app.js 不是 src/frontend.js 的当前构建结果。" >&2
echo "修复：npm ci && npm run build，然后提交 src/www/app.js。" >&2
echo "大小对比 仓库=$(wc -c < src/www/app.js) 重建=$(wc -c < "$out")" >&2
exit 1
