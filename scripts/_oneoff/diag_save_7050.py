#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判定 7050 到底是「token 失效」还是「更新路径不可用」。

一次运行做三段对照，拿同一个新鲜 token：
  ① 新建一篇（pgc_id=None）    → 验 token 本身是否可用
  ② 更新一篇（pgc_id=<gid>）   → 验「带 pgc_id 更新」是否被拒
  ③ 回读目标草稿封面           → 验封面有没有真的变

若 ①成功 ②失败 ⇒ 更新路径不可用（带 pgc_id 保存被拒），
   `cover --api` 这条路不成立，得另找接口。
若 ①②都失败 ⇒ 当前会话写操作被限流（argus 记账），与路径无关。

用法：python scripts/_oneoff/diag_save_7050.py [目标 case id]
"""
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import toutiao_publish as T          # noqa: E402
import wechat_publish as wp          # noqa: E402

PROBE_TITLE = "__cover_probe_a__"


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "trustmrr"
    meta = json.load(io.open(os.path.join(T.OUT, target, "meta.json"),
                             encoding="utf-8"))
    title = meta["title"]
    with io.open(os.path.join(T.OUT, target, "article.html"), encoding="utf-8") as f:
        full = f.read()
    body = full.split("\n", 1)[1] if full.startswith("<h1>") else full

    cdp = wp.CDP(9223)
    t = cdp.new_target(T.TT_EDITOR)
    cdp.connect_target(t["id"])
    time.sleep(10)

    raw = T.fetch_draft_api_raw(cdp) or []
    hit = [x for x in raw if (x.get("title") or "").strip() == title.strip()]
    if not hit:
        raise SystemExit("草稿箱里没找到《%s》" % title)
    gid = str(hit[0].get("gid"))
    before_uri = ((hit[0].get("cover_image") or {}).get("image_uri") or "")
    print("目标 gid=%s" % gid)
    print("before uri=%s" % (before_uri or "-"))

    print("\n抓 token…")
    t0 = time.time()
    tok = T.grab_anti_token(cdp, attempts=2, per_attempt=25)
    print("  len=%d（%.0fs）  head=%s" % (len(tok or ""), time.time() - t0,
                                          (tok or "")[:16]))
    if not tok:
        raise SystemExit("没抓到 token")

    print("\n上传一张不同的封面")
    src = os.path.join(T.OUT, "1lookup", "cover.png")
    cu, curi, cmsg, wh = T.upload_cover_api(cdp, src, tok)
    print("  uri=%s  msg=%s" % ((curi or "-")[:60], cmsg))

    print("\n① 新建（pgc_id=None）")
    pid1, msg1 = T.save_draft_api(cdp, PROBE_TITLE, "<p>probe</p>",
                                  pgc_id=None, anti_token=tok,
                                  cover=(cu, curi, cmsg, wh))
    print("  pgc_id=%r  msg=%s" % (pid1, msg1))

    print("\n② 更新（pgc_id=%s）" % gid)
    pid2, msg2 = T.save_draft_api(cdp, title, body, pgc_id=gid,
                                  anti_token=tok, cover=(cu, curi, cmsg, wh))
    print("  pgc_id=%r  msg=%s" % (pid2, msg2))

    print("\n③ 回读")
    time.sleep(3)
    raw = T.fetch_draft_api_raw(cdp) or []
    hit = [x for x in raw if (x.get("title") or "").strip() == title.strip()]
    after_uri = (((hit[0] if hit else {}).get("cover_image") or {})
                 .get("image_uri") or "")
    print("  after  uri=%s" % (after_uri or "-"))
    print("  上传 uri  =%s" % (curi or "-"))

    print("\n=== 判定 ===")
    ok1 = not str(msg1).startswith("code=") and pid1
    ok2 = not str(msg2).startswith("code=") and pid2
    print("  ①新建 %s / ②更新 %s" % ("成功" if ok1 else "失败",
                                     "成功" if ok2 else "失败"))
    if ok1 and not ok2:
        print("  ⇒ 更新路径被拒（带 pgc_id 保存不可用），cover --api 走不通")
    elif not ok1 and not ok2:
        print("  ⇒ 当前会话写操作被限流，与路径无关")
    elif ok1 and ok2:
        if after_uri == curi and after_uri != before_uri:
            print("  ⇒ 更新有效，封面已换")
        else:
            print("  ⇒ 保存返成功但封面没换（服务端忽略封面字段）")

    if pid1:
        print("\n清理探针草稿 %s" % pid1)
        st = T.delete_draft_api(cdp, pid1)
        print("  %s" % st)

    cdp.close_target(t["id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
