#!/usr/bin/env bash
# 从 data 分支取回最新采集队列到本地工作区（只写文件，不进 main 的提交图）。
#
# 背景：采集队列（data/inbox.json 等）现在只在独立的 data 分支累积，main 不追踪它。
# 所以本地要构建「含 inbox 的」dist 预览时，main 上没有这些文件，需要先取回。
# 部署用 build_static.py --no-inbox，不需要这一步。
#
# 用法：bash scripts/sync_inbox.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== 取回采集队列（data 分支）=="
if ! git fetch origin data --quiet; then
  echo "[!] git fetch origin data 失败（检查网络 / 是否首次运行）"
  exit 1
fi

for f in data/inbox.json data/inbox_archive.json data/last_harvest.json; do
  if git cat-file -e "origin/data:$f" 2>/dev/null; then
    git show "origin/data:$f" > "$f"
    echo "  ✔ 已更新 $f"
  else
    echo "  · $f 在 data 分支尚无，跳过"
  fi
done

echo "完成。现在可以跑：python scripts/build_static.py  构建本地 dist（含 inbox）。"
