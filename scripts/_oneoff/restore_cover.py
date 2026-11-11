#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 TrustMRR 草稿的封面恢复成本项目自己的图，并清理诊断留下的探针稿。

背景：验证「save+pgc_id 能否改封面」时，故意把 1lookup 的封面换到了
TrustMRR 的草稿上（才分辨得出「换了」和「同图看不出」）。验证完必须换回。
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

TARGET = sys.argv[1] if len(sys.argv) > 1 else "trustmrr"


def main():
    meta = json.load(io.open(os.path.join(T.OUT, TARGET, "meta.json"),
                             encoding="utf-8"))
    title = meta["title"]
    with io.open(os.path.join(T.OUT, TARGET, "article.html"), encoding="utf-8") as f:
        full = f.read()
    body = full.split("\n", 1)[1] if full.startswith("<h1>") else full
    own_cover = os.path.join(T.OUT, TARGET, "cover.png")
    if not os.path.isfile(own_cover):
        raise SystemExit("本地封面不存在：%s" % own_cover)

    cdp = wp.CDP(9223)
    t = cdp.new_target(T.TT_EDITOR)
    cdp.connect_target(t["id"])
    time.sleep(10)

    raw = T.fetch_draft_api_raw(cdp) or []
    hit = [x for x in raw if (x.get("title") or "").strip() == title.strip()]
    if not hit:
        raise SystemExit("找不到草稿《%s》" % title)
    gid = str(hit[0].get("gid"))
    before = ((hit[0].get("cover_image") or {}).get("image_uri") or "")
    print("gid=%s  当前 uri=%s（这是 1lookup 的图）" % (gid, before))

    tok = T.grab_anti_token(cdp, attempts=2, per_attempt=25)
    print("token len=%d" % len(tok or ""))
    if not tok:
        raise SystemExit("没抓到 token")

    cu, curi, cmsg, wh = T.upload_cover_api(cdp, own_cover, tok)
    print("上传本地封面 uri=%s msg=%s" % ((curi or "-")[:60], cmsg))
    if not curi:
        raise SystemExit("上传失败")

    pid, msg = T.save_draft_api(cdp, title, body, pgc_id=gid, anti_token=tok,
                                cover=(cu, curi, cmsg, wh))
    print("保存 pgc_id=%s msg=%s" % (pid, msg))
    if str(msg).startswith("code="):
        raise SystemExit("保存失败，封面未恢复")

    time.sleep(3)
    raw = T.fetch_draft_api_raw(cdp) or []
    hit = [x for x in raw if (x.get("title") or "").strip() == title.strip()]
    after = (((hit[0] if hit else {}).get("cover_image") or {})
             .get("image_uri") or "")
    ci = ((hit[0] if hit else {}).get("cover_image") or {})
    print("回读 uri=%s  %sx%s" % (after, ci.get("width"), ci.get("height")))
    if after == curi:
        print("✅ 封面已恢复成本地图（%s）" % os.path.basename(own_cover))
    else:
        print("❌ 封面没恢复：期望 %s，实际 %s" % (curi, after))

    print("\n清理探针稿")
    n = T.cleanup_probe_draft(cdp, "__diag_tok", rounds=3, pause=2.0)
    n += T.cleanup_titles(cdp, ["__cover_probe_a__"], rounds=2, pause=2.0)
    print("  删除 %d 条" % n)

    cdp.close_target(t["id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
