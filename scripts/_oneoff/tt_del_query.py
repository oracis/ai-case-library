#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性：按前端真实形态删已发布文章 —— **body 为空，字段只在 query**。

前端原文（这是本轮最关键的一条）：

    Delete(t, e) {
      const r = Xl({type, gid, slave_id, mp_id, is_question, question_id, is_comment});
      const i = `${uriPrefix}/mp/agw/creator_center/delete${r}`;
      const a = {Accept, Content-Type};
      delete e.type; delete e.gid; delete e.slave_id; delete e.mp_id;
      delete e.is_question; delete e.question_id; delete e.is_comment;
      const o = JSON.stringify(e);      // ← 序列化发生在 delete 之后
      return ferryFetch(i, {method:"POST", headers:a, body:o}, e);
    }

即 query 带 7 个字段，**body 只剩业务字段（通常是 `{}`）**。前几轮把
type/gid 也塞进 body，服务端静默 `code:0` 不删 —— 与「形态不对就宽容返回」一致。

用法：`tt_del_query.py <gid> [--types 1,2,3]`，逐个 type 试，以**回读消失**为准。
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


def read_items(cdp):
    cdp.send("Page.navigate", {"url": T._article_api_url(0, 1)})
    time.sleep(6)
    d = json.loads(cdp.eval("document.body.innerText") or "{}")
    return d.get("data") or []


def base(it):
    return ((it.get("assembleCell") or {}).get("itemCell") or {}).get("articleBase") or {}


def main():
    gid = sys.argv[1] if len(sys.argv) > 1 else "7399999999999999999"
    types = sys.argv[3].split(",") if len(sys.argv) > 3 else ["1", "2", "3", "5", "8", "9"]
    cdp = wp.CDP(9223)
    t = cdp.new_target(T.TT_ARTICLES_PAGE)
    cdp.connect_target(t["id"])
    try:
        time.sleep(5)
        items = read_items(cdp)
        cur = next((x for x in items if str(base(x).get("gidStr")) == gid), None)
        if not cur:
            print("✓ 已经不在了")
            return 0
        uid = (((cur.get("assembleCell") or {}).get("itemCell") or {})
               .get("userInfo") or {}).get("userID")
        print("目标：%s uid=%s" % (base(cur).get("title"), uid))

        for typ in types:
            q = ("?type=%s&gid=%s&slave_id=0&mp_id=%s&is_question=false"
                 "&question_id=0&is_comment=false" % (typ, gid, uid))
            url = "/mp/agw/creator_center/delete" + q
            js = ("(function(){return fetch(%s,{method:'POST',credentials:'include',"
                  "headers:{'Accept':'application/json',"
                  "'Content-Type':'application/json'},body:'{}'})"
                  ".then(function(r){return r.text();})"
                  ".catch(function(e){return 'ERR:'+e.message;})})()" % json.dumps(url))
            out = str(cdp.eval(js) or "")
            print("type=%s → %s" % (typ, out[:150]), flush=True)
            time.sleep(3)
            items = read_items(cdp)
            if not any(str(base(x).get("gidStr")) == gid for x in items):
                print("\n✓ type=%s 生效：目标已从列表消失（%d 条）" % (typ, len(items)))
                return 0
        print("\n⚠ 所有 type 试完都没删掉")
        return 4
    finally:
        try:
            cdp.close_target(t["id"])
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
