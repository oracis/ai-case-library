"""按标题反查小红书远端草稿的真实 case id，核对「该发的发了、不该发的没发」。

## 为什么需要这个工具

`publish --all` 的输出只给 case **名**（`ProSP`）不给 id，
`drafts` 命令只给**标题**（`月收$128K：销售挖客户线索的工具`）。
两者都**不能证明**远端某条草稿对应哪个 case —— 而标题里带金额，
光看标题根本认不出是prosp 还是别的。

⇒ 必须走 `note.json` → 标题 → `match_drafts()` 这条链做反查。
本脚本额外按 **case** 聚合，能发现「同一 case 有两份草稿」这种
在标题视角下看不见的重复。

## 判据

- `note.json` 的 title 是发布口径的真实来源（`build_note` 产出）。
- `match_drafts` 已有包含匹配兜底，但**同一 case 两条草稿**它只会都算「对上」，
  所以重复检测必须在这里按 case 二次聚合。

用法：python -u -X utf8 scripts/xhs_draft_audit.py
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import xhs_publish as xp        # noqa: E402


def main():
    # ⚠ 必须先显式 autostart —— `fetch_drafts()` 内部的 `wp.CDP()` 在端口不通时
    # 抛的是 **SystemExit**（继承 BaseException），会把本脚本静默终止掉。
    import chrome_cdp_launch as cl
    import wechat_publish as wp
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY"):
        os.environ.pop(k, None)
    wp._autostart_chrome_if_needed(xp.PUB_PORT, profile=cl.PLAT_PROFILE)
    drafts = xp.fetch_drafts()
    print("远端草稿 %d 条" % len(drafts))

    cases = xp.load_cases()
    all_ids = [c["id"] for c in cases]
    matched, orphans = xp.match_drafts(drafts, cases)

    by = defaultdict(list)
    for m in matched:
        by[m["id"]].append(m)

    print("对上 %d 个 case｜孤儿 %d 条" % (len(by), len(orphans)))
    dup = {c: v for c, v in by.items() if len(v) > 1}
    if dup:
        print("\n⚠ 同一 case 多份草稿：")
        for c, v in sorted(dup.items()):
            print("  %-22s %d 份：%s"
                  % (c, len(v), " / ".join(x["saved"] for x in v)))
    if orphans:
        print("\n⚠ 孤儿草稿（认不出 case）：")
        for o in orphans:
            print("  %s | %s" % (o["saved"], o["title"]))

    missing = [c for c in all_ids if c not in by]
    if missing:
        print("\n未出现在远端草稿箱的 case（%d）：%s"
              % (len(missing), "、".join(missing)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
