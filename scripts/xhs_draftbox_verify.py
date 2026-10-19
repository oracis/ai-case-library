# -*- coding: utf-8 -*-
r"""草稿箱打开后的「侧边栏计数」只读回读 —— 给 `xhs_open_right.bat` 用。

## 存在意义

bat 以前在「端口已起」分支里执行 `start "" "%DRAFT%"`
—— 这会把草稿箱页交给**默认浏览器**（用户的日常 Chrome，草稿箱 0），
控制台却打印 "opening draft box in that window"。用户体感：
**第一次能用，之后再开就是 0**（端口一旦被上次的 CfT 占着，
后续每次运行都走进空 profile）。

2026-10-17 起 bat 改为「同 user-data-dir 单例唤起」，本脚本负责把
**侧边栏 `草稿箱(N)`** 读回来打印到控制台，让用户当场确认打开对了。

## 判据（2026-10-02 立，2026-10-17 复验仍有效）

- ✅ **侧边栏 `草稿箱(N)` 文本** —— 唯一可信判据
- ❌ DOM 卡片计数 —— 草稿列表懒加载，`querySelectorAll` 恒返回 0
- ❌ Cookie 文件大小 / 条数 —— App-Bound 加密，两个 profile 都"正常"

## 用法

    python -X utf8 scripts/xhs_draftbox_verify.py [--port 9223] [--wait 10]

**永不裸崩**：读不到只打印原因，退出码恒 0 —— bat 里失败也不能
影响已经打开的窗口。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DRAFT_HOST = "creator.xiaohongshu.com"

# 侧边栏计数 + 登录态探针（全部在页面内完成，回传极小 JSON）
_SIG_JS = r"""(function(){
  var t = document.body.innerText || '';
  var m = t.match(/草稿箱\((\d+)\)/);
  var head = t.slice(0, 300);
  return JSON.stringify({
    count: m ? m[1] : null,
    login_prompt: /扫码|请登录|登录/.test(head),
    len: t.length
  });
})()"""


def main(argv=None):
    ap = argparse.ArgumentParser(description="回读小红书草稿箱侧边栏计数")
    ap.add_argument("--port", type=int, default=9223)
    ap.add_argument("--wait", type=int, default=12,
                    help="最多等多少秒让侧边栏渲染出来")
    a = ap.parse_args(argv)

    try:
        import wechat_publish as wp

        cdp = wp.CDP(a.port)
        pages = [t for t in cdp.list_targets()
                 if t.get("type") == "page" and DRAFT_HOST in (t.get("url") or "")]
        if not pages:
            print("[verify] %d 端口里还没有小红书页面（刚打开还在加载？直接看窗口）" % a.port)
            return 0
        # 多个标签页时取最后一个（最新打开的那个）
        cdp.connect_target(pages[-1]["id"])

        sig = {}
        for _ in range(max(1, a.wait)):
            time.sleep(1)
            try:
                raw = cdp.eval(_SIG_JS, refresh_context=True) or "{}"
                sig = json.loads(raw)
            except BaseException:                    # noqa: BLE001
                continue
            if sig.get("count") or sig.get("login_prompt"):
                break

        if sig.get("count"):
            print("[verify] 侧边栏 草稿箱(%s) —— 标签页落在有草稿的窗口 ✅"
                  % sig["count"])
        elif sig.get("login_prompt"):
            print("[verify] 页面在要登录 —— chrome-debug-profile 的小红书")
            print("         登录态过期了，请在那个窗口里重新扫码登录一次")
        elif not sig.get("len"):
            print("[verify] 页面还是空白（加载中），过两秒直接看窗口")
        else:
            print("[verify] 没读到侧边栏计数（可能侧边栏被收起），直接看窗口")
        return 0
    except BaseException as e:                        # noqa: BLE001
        # 包括 SystemExit（CDP 连不上时 wechat_publish 会打印长帮助后退出）
        print("[verify] 回读失败（不影响已打开的窗口）: %s" % e)
        return 0


if __name__ == "__main__":
    sys.exit(main())
