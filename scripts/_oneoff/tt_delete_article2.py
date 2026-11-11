#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性：用**完整字段**重试删除已发布文章。

上一版只传 `{type,gid}` → 返 `code:0 success` 但回读还在。头条对形态不对的
body 一律返 `code:0`（幂等宽容），所以必须补全前端 `Delete(t)` 实际序列化的
那些键，并**以回读为准**。

前端原文（vendors.d1505bc60ce087d047b1.js）：

    Delete(t, e) {
      const r = Xl({type, gid, slave_id, mp_id, is_question, question_id, is_comment});
      const i = `${uriPrefix}/mp/agw/creator_center/delete${r}`;
      return ferryFetch(i, {method:"POST", headers:o.A, body: JSON.stringify(t)}, e);
    }
"""
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
    t = cdp.new_target(T.TT_ARTICLES_PAGE)
    cdp.connect_target(t["id"])
    try:
        time.sleep(5)
        items = read_items(cdp)
        cur = next((x for x in items if str(base(x).get("gidStr")) == gid), None)
        if not cur:
            print("✓ 列表里已经没有 %s（已删）" % gid)
            return 0
        cell = cur["assembleCell"]["itemCell"]
        uid = (cell.get("userInfo") or {}).get("userID")
        print("目标：%s  gid=%s  uid=%s" % (base(cur).get("title"), gid, uid))

        payload = {"type": 1, "gid": gid, "slave_id": 0, "mp_id": uid,
                   "is_question": False, "question_id": 0, "is_comment": False}
        q = "?" + "&".join("%s=%s" % (k, str(v).lower() if isinstance(v, bool) else v)
                           for k, v in payload.items())
        url = "/mp/agw/creator_center/delete" + q
        body = json.dumps(payload, ensure_ascii=False)
        print("POST %s\nbody %s" % (url, body))

        js = ("(function(){return fetch(%s,{method:'POST',credentials:'include',"
              "headers:{'Accept':'application/json',"
              "'Content-Type':'application/json'},body:%s})"
              ".then(function(r){return r.text();})"
              ".catch(function(e){return 'ERR:'+e.message;})})()"
              % (json.dumps(url), json.dumps(body)))
        print("返回：%s" % str(cdp.eval(js) or "")[:300])

        time.sleep(4)
        print("\n回读核对…")
        items = read_items(cdp)
        still = any(str(base(x).get("gidStr")) == gid for x in items)
        print("  列表 %d 条，目标 %s" % (len(items), "仍在 ⚠" if still else "已消失 ✓"))
        return 0 if not still else 4
    finally:
        try:
            cdp.close_target(t["id"])
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
