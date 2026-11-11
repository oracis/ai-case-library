#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证 `save_draft_api(pgc_id=...)` 到底能不能改已有草稿的封面。

背景：`cover --api --force` 返 ok，但回读 image_url / update_time 一点没变。
两种可能：
  A. 真改了，只是本地 cover.png 与线上是同一张 ⇒ uri 相同，看不出差异；
  B. 没改（save 带 pgc_id 对封面字段无效），而 verify_draft_cover 只检查
     uri 非空 ⇒ 假成功。

做法：拿**另一条案例**的封面（图上完全不同）换到目标草稿上，
     这样 uri 只要变了就说明改成功，与"同图同 uri"彻底区分开。

用法：python scripts/_oneoff/verify_cover_update.py <目标 case id> <图源 case id>
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


def find_draft(cdp, title):
    raw = T.fetch_draft_api_raw(cdp) or []
    hit = [x for x in raw if (x.get("title") or "").strip() == title.strip()]
    return hit[0] if hit else None


def show(tag, d):
    ci = (d or {}).get("cover_image") or {}
    print("  %-10s uri=%s" % (tag, (ci.get("image_uri") or "-")[:60]))
    print("  %-10s url=%s" % ("", (ci.get("image_url") or "-")[:90]))
    print("  %-10s size=%sx%s  update_time=%s"
          % ("", ci.get("width"), ci.get("height"), (d or {}).get("update_time")))
    return ci.get("image_uri") or ""


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "trustmrr"
    source = sys.argv[2] if len(sys.argv) > 2 else "1lookup"
    mp = os.path.join(T.OUT, target, "meta.json")
    meta = json.load(io.open(mp, encoding="utf-8"))
    title = meta["title"]
    with io.open(os.path.join(T.OUT, target, "article.html"), encoding="utf-8") as f:
        full = f.read()
    body = full.split("\n", 1)[1] if full.startswith("<h1>") else full

    src_cover = os.path.join(T.OUT, source, "cover.png")
    if not os.path.isfile(src_cover):
        raise SystemExit("图源封面不存在：%s" % src_cover)
    print("目标：《%s》" % title)
    print("图源：%s（%d 字节）" % (src_cover, os.path.getsize(src_cover)))

    cdp = wp.CDP(9223)
    t = cdp.new_target(T.TT_EDITOR)
    cdp.connect_target(t["id"])
    time.sleep(10)
    print("编辑器页：%s" % (cdp.eval("location.href") or "")[:80])

    print("\n① 换之前")
    before = show("before", find_draft(cdp, title))

    print("\n② 抓 tt-anti-token")
    t0 = time.time()
    tok = T.grab_anti_token(cdp, attempts=2, per_attempt=25)
    print("  len=%d（%.0fs）" % (len(tok or ""), time.time() - t0))
    if not tok:
        raise SystemExit("没抓到 token")

    print("\n③ 上传图源封面")
    cu, curi, msg, wh = T.upload_cover_api(cdp, src_cover, tok)
    print("  url=%s" % (cu or "-")[:90])
    print("  uri=%s" % (curi or "-")[:60])
    print("  msg=%s  size=%sx%s" % (msg, wh[0], wh[1]))
    if not curi:
        raise SystemExit("上传失败，实验无法继续")

    print("\n④ 带 pgc_id 保存（应更新同一篇）")
    pid, smsg = T.save_draft_api(cdp, title, body, pgc_id=meta.get("pgc_id") or None,
                                 anti_token=tok, cover=(cu, curi, msg, wh))
    print("  pgc_id=%s  msg=%s" % (pid, smsg))

    print("\n⑤ 等 3s 后回读")
    time.sleep(3)
    after = show("after", find_draft(cdp, title))

    print("\n=== 判定 ===")
    print("  before uri = %s" % (before or "-"))
    print("  上传返回   = %s" % (curi or "-"))
    print("  after  uri = %s" % (after or "-"))
    if after and after == curi and after != before:
        print("  ✅ 封面确实换成新图（save+pgc_id 有效）")
    elif after == before:
        print("  ❌ 封面没变（save+pgc_id 改不了已有草稿的封面）")
    else:
        print("  ⚠ 出现第三种情况：既不是旧 uri 也不是上传返回的 uri")

    # 说明草稿正文有没有被动过
    print("\n  草稿 gid=%s" % (find_draft(cdp, title) or {}).get("gid"))
    cdp.close_target(t["id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
