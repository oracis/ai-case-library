r"""打开小红书草稿箱（用**有草稿的那份profile**）。

## 为什么必须走这个脚本而不是随便开个Chrome

本机两个 Chrome profile，**同一账号、两份互不可见的草稿箱**：

| profile | 端口 | 草稿箱 |
|---|---|---|
| `C:\Users\DELL\chrome-debug-profile` | 9223 | **39 条** ✅ |
| `%LOCALAPPDATA%\Google\ChromeCDP` | 9222 | 0 条 ❌ |

草稿是**绑 profile 的本地数据**，不是绑账号的服务器数据。
公众号流水线用 ChromeCDP，所以「随手复用 9222」会落到空箱。

⚠ 与 `scripts/xhs_open_right.bat` 的区别：那个交给用户双击；
本脚本把「起 Chrome + 导航 + 保持存活」写进**同一个进程** ——
本机硬限制「Chrome 活不过一次父进程调用」，父进程一退Chrome 就被回收，
窗口会从桌面消失。`--hold` 就是为此存在。

用法：
    python -u -X utf8 scripts/xhs_open_draft_box.py            # 开一次就退
    python -u -X utf8 scripts/xhs_open_draft_box.py --hold 3600
        # 保持 1 小时，窗口稳定留在桌面供你人工检测
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DRAFT = "https://creator.xiaohongshu.com/publish/publish?target=draft"
SIDEBAR_RE = re.compile(r"草稿箱\s*[（(]?\s*(\d+)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, nargs="?", const=3600, default=0,
                    help="保持本进程存活 N 秒，让 Chrome 窗口留在桌面上")
    a = ap.parse_args()

    import chrome_cdp_launch as cl
    import wechat_publish as wp

    prof = cl.PLAT_PROFILE
    print("profile :", prof)
    print("port    : 9223")

    if not os.path.isdir(prof):
        print("✗ profile 不存在:", prof)
        return 1

    wp._autostart_chrome_if_needed(9223, profile=prof)
    time.sleep(2)
    cdp = wp.CDP(9223)

    # ⚠ 必须**新开**草稿箱页而不是复用现有 tab —— 实测复用会停在编辑器页
    #（URL 带编辑器状态），侧边栏读不到草稿数。
    tid = cdp.new_target(DRAFT)["targetId"]
    cdp.connect_target(tid)
    time.sleep(8)
    # 再显式导航一次，确保落在 target=draft 上
    cdp.eval("location.href=%r" % DRAFT, refresh_context=True)
    time.sleep(8)

    url = cdp.eval("location.href") or ""
    txt = cdp.eval("document.body.innerText") or ""
    m = SIDEBAR_RE.findall(txt)

    print("\nURL:", url[:100])
    print("侧边栏草稿数:", m or "没读到（侧边栏可能收起了）")
    print("-" * 60)
    print(txt[:700])
    print("-" * 60)
    print("⚠ 判据看侧边栏「草稿箱(N)」的数字，应为 39。")
    print("  DOM 卡片计数在小红书恒为 0（懒加载），别拿它当判据。")

    if a.hold:
        print("\n保持 Chrome 存活 %d 秒…" % a.hold)
        t0 = time.time()
        while time.time() - t0 < a.hold:
            time.sleep(10)
            try:
                # 探活，顺便防 WS 静默断开
                if cdp.eval("1+1") != 2:
                    raise RuntimeError("CDP 无响应")
            except BaseException:              # noqa: BLE001
                print("⚠ Chrome 已退出（CDP 不通）")
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
