# -*- coding: utf-8 -*-
"""一次性探针：用**编辑器真实抓到的 arg** + submit=1 重放 draft/add。

要验证的假设（2026-10-20 晚，用户提的思路）：
  「都是 add，参数不一样，一个存为草稿，一个就正式发布了」。

背景：10-20 白天测过「submit=1 只存草稿」，但当时用的是
`build_arg()` 自己拼的 arg。现在手里有了**编辑器自己发出去的完整 arg**
（`.cache/bili_publish_capture.json，含 opus.content 全量段落）。如果
之前不发布是因为 arg 结构不够真（服务端静默降级），那原样 arg + submit:1
就该走发布逻辑。

安全性：今天投稿额度已满（编辑器提示「已达到当日投稿上限」）。
若 submit=1 真走发布 ⇒ 服务端以额度错误拒掉，**不会真发出去**；
若仍返回 code:0 + article_id ⇒ 实锤 submit=1 就是存草稿。
arg 原样回传、内容不变，最坏情况只是「又存了一次草稿」。
"""
import json
import os
import sys
import time

sys.path.insert(0, ".")
import bili_draft_api as bapi          # noqa: E402
import wechat_publish as wp            # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CAP = os.path.join(ROOT, ".cache", "bili_publish_capture.json")


def main():
    with open(CAP, encoding="utf-8") as f:
        cap = json.load(f)
    req = next(c for c in cap["requests"]
               if c.get("kind") == "fetch" and "/draft/add" in c.get("url", ""))
    body = json.loads(req["body"])
    arg = body["arg"]
    print("抓到真实 arg 顶层键: %s" % sorted(arg.keys()))
    print("submit 原值: %r" % arg.get("submit"))

    cdp = wp.CDP(9223)
    bapi._bili_tab(cdp)
    time.sleep(2)

    for submit in (1,):
        a = dict(arg)
        a["submit"] = submit
        r = bapi.save_draft(cdp, a)
        print("\nsubmit=%s -> code=%s msg=%s"
              % (submit, r.get("code"), r.get("message")))
        print("  data:", json.dumps(r.get("data"), ensure_ascii=False))
        if r.get("code") == 0:
            print("  ⚠ 还是 code:0 —— 要么被当草稿存了，要么发布成功了（不可能，"
                  "额度满）")
        else:
            print("  ⇒ 服务端明确拒绝（code=%s）—— 这就是发布逻辑才会给的回复"
                  % r.get("code"))

    # 回读：这条草稿还在不在箱里（在 ⇒ 没发出去）
    time.sleep(2)
    drafts = bapi.list_drafts(cdp) or []
    still = [d for d in drafts
             if str(d.get("article_id")) == str(cap.get("aid"))]
    print("\n草稿箱回读：aid=%s %s"
          % (cap.get("aid"), "还在箱里（没发出去）" if still else "已不在箱里！"))
    pubs = bapi.list_published_articles(cdp, max_page=3) or []
    hit = [a for a in pubs if str(a.get("id")) == str(cap.get("aid"))]
    print("已发表列表：%s" % ("命中！" if hit else "没命中"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
