#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性：带 tt-anti-token 重试删除已发布文章。

前两次不带 token 都返 `code:0 success` 但没删 —— 头条「写操作必带
tt-anti-token」是全局约束（存草稿缺它恒 7050），删除大概率同样。
本次：编辑器页抓 token → 同源作品页调 delete（带 token）→ 回读核对。
"""
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import toutiao_publish as T  # noqa: E402
import wechat_publish as wp  # noqa: E402

PORT = int(os.environ.get("CDP_PORT", 9223))


def read_items(cdp):
    cdp.send("Page.navigate", {"url": T._article_api_url(0, 1)})
    time.sleep(6)
    d = json.loads(cdp.eval("document.body.innerText") or "{}")
    return d.get("data") or []


def base(it):
    return ((it.get("assembleCell") or {}).get("itemCell") or {}).get("articleBase") or {}


def main():
    gid = sys.argv[1] if len(sys.argv) > 1 else "7399999999999999999"
    cdp = wp.CDP(PORT)
    t = cdp.new_target(T.TT_EDITOR)
    cdp.connect_target(t["id"])
    try:
        time.sleep(10)
        tokfile = os.path.join(ROOT, "tmp", "tt_token.txt")
        if os.path.isfile(tokfile):
            tok = io.open(tokfile, encoding="utf-8").read().strip()
            print("复用 token len=%d" % len(tok))
        else:
            print("抓 token…", flush=True)
            t0 = time.time()
            tok = T.grab_anti_token(cdp, attempts=2, per_attempt=25)
            print("  token len=%d（%.0fs）" % (len(tok or ""), time.time() - t0))
        if not tok:
            print("✗ 没抓到 token")
            return 2

        # 同源页面即可，不需要在作品页
        items = read_items(cdp)
        cur = next((x for x in items if str(base(x).get("gidStr")) == gid), None)
        if not cur:
            print("✓ 目标已不在列表里")
            return 0
        uid = (((cur.get("assembleCell") or {}).get("itemCell") or {})
               .get("userInfo") or {}).get("userID")
        print("目标：%s uid=%s" % (base(cur).get("title"), uid))

        payload = {"type": 1, "gid": gid, "slave_id": 0, "mp_id": uid,
                   "is_question": False, "question_id": 0, "is_comment": False}
        q = "&".join("%s=%s" % (k, str(v).lower() if isinstance(v, bool) else v)
                     for k, v in payload.items())
        url = "/mp/agw/creator_center/delete?" + q
        body = json.dumps(payload, ensure_ascii=False)
        hdr = {"Accept": "application/json", "Content-Type": "application/json",
               "tt-anti-token": tok}
        js = ("(function(){return fetch(%s,{method:'POST',credentials:'include',"
              "headers:%s,body:%s})"
              ".then(function(r){return r.text();})"
              ".catch(function(e){return 'ERR:'+e.message;})})()"
              % (json.dumps(url), json.dumps(hdr), json.dumps(body)))
        print("返回：%s" % str(cdp.eval(js) or "")[:300])

        time.sleep(5)
        items = read_items(cdp)
        still = any(str(base(x).get("gidStr")) == gid for x in items)
        print("回读：%d 条，目标 %s" % (len(items), "仍在 ⚠" if still else "已消失 ✓"))
        return 0 if not still else 4
    finally:
        try:
            cdp.close_target(t["id"])
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
