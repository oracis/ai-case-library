#!/usr/bin/env bash
# 重建 6 条服务端损坏的公众号草稿（2026-10-01 确认的 320003 记录级损坏）。
#
# 为什么必须重建而不是 refresh：
#   这 6 条在服务端**打不开编辑页**（等多久都没用），refresh 刷不进去。
#   唯一出路是当作新草稿重发 —— `publish --case` 走的正是这条路径
#   （resume_id=None → 新建草稿 → 拿到新 appmsgid）。
#
# ⚠ 前置条件（必须都满足，脚本会自查并给出提示）：
#   1) Chrome 以 CDP 模式在跑：
#      chrome.exe --remote-debugging-port=9222 ^
#        --user-data-dir="%LOCALAPPDATA%\Google\ChromeCDP"
#   2) 该 profile 已登录 mp.weixin.qq.com（token 能取到）
#   3) 没有残留的 Default/LOCK（强杀进程后 Chrome 会启动即退）
#
# 用法：
#   bash scripts/wechat_rebuild_broken.sh          # 先干跑看计划
#   bash scripts/wechat_rebuild_broken.sh --go     # 真跑
set -uo pipefail

cd "$(dirname "$0")/.."
BROKEN="magicslides-app stan 1lookup gojiberryai sierra genius-ai"
GO=0
[ "${1:-}" = "--go" ] && GO=1

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
export PYTHONIOENCODING=utf-8

echo "=== 0) 前置检查 ==="
if ! curl -s --noproxy '*' --max-time 5 http://127.0.0.1:9222/json/version \
        | grep -q webSocketDebuggerUrl; then
    echo "✗ CDP 端口不通。先用这条命令启动 Chrome："
    echo '  chrome.exe --remote-debugging-port=9222 ^'
    echo '    --user-data-dir="%LOCALAPPDATA%\Google\ChromeCDP"'
    exit 1
fi
echo "✓ CDP 端口通"

LOCK="$LOCALAPPDATA/Google/ChromeCDP/Default/LOCK"
[ -f "$LOCK" ] && echo "⚠ 存在残留 LOCK（可能让 Chrome 启动即退）：$LOCK"

echo
echo "=== 1) 先删掉服务端那6 条损坏记录（否则会撞黑名单/占位）==="
python -X utf8 scripts/wechat_publish.py delete \
    --appmsgid 100000125,100000130,100000134,100000138,100000142,100000146 \
    --dry

echo
echo "=== 2) 逐条重建（每条独立，失败不影响下一条）==="
for cid in $BROKEN; do
    echo
    echo "---- $cid ----"
    if [ "$GO" = "1" ]; then
        python -X utf8 -u scripts/wechat_publish.py publish --case "$cid" 2>&1 \
            | tee "_rebuild_${cid}.log"
    else
        python -X utf8 -u scripts/wechat_publish.py publish --case "$cid" --dry 2>&1
    fi
done

echo
if [ "$GO" = "1" ]; then
    echo "=== 3) 回读对账（按篇号，别信『保存: OK』）==="
    python -X utf8 scripts/wechat_verify_refresh.py --all 2>&1 | tail -40
    echo
    echo "完成后请人工确认：6 条新草稿的标题/正文/封面是否正常。"
    echo "正常后把新appmsgid 写回 data/wechat_published.json，"
    echo "并从 data/wechat_broken_drafts.json 移除对应条目。"
else
    echo "=== 这是干跑。加 --go 才真跑 ==="
fi