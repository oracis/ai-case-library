r"""一次性回读小红书草稿箱明细（起 Chrome + 读 + 退出，同一进程）。

⚠ 为什么不能拆成两次调用：本机硬限制「Chrome 活不过一次父进程调用」，
第一个进程退出时 Chrome 被回收，第二个进程连 9223 就10061。
⇒ 起 Chrome、读列表、打印必须写在**同一个进程**里。
"""
from __future__ import annotations

import os
import sys

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chrome_cdp_launch as cl          # noqa: E402
import wechat_publish as wp             # noqa: E402
import xhs_publish as xp                # noqa: E402

PORT = 9223


def main():
    wp._autostart_chrome_if_needed(PORT, profile=cl.PLAT_PROFILE)
    # fetch_drafts() 内部自己 _draft_cdp() 连9223，不复用外部 cdp
    ds = xp.fetch_drafts()
    print("远端草稿总数:", len(ds))
    for i, d in enumerate(ds, 1):
        print("  %2d. %s" % (i, (d.get("title") or "").strip()[:60]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
