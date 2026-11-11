#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""删除腾讯云已驳回稿 2776839（forever:0 → 回收站，可找回），前后双回读。

判据（全部必须成立）：
  ① 删前 hostStatus=3 恰好 1 条且 articleId=2776839
  ② 删前 hostStatus=0 共 N 条
  ③ 删后 hostStatus=3 为 0 条、hostStatus=0 为 N-1 条、2776839 不在任何视图
  ④ delete-content 响应里没有明确失败标记

⚠ 匿名页对该 id 删前删后都是 404（驳回稿本来不公开），**不作为证据**。
⚠ 本脚本只删平台侧，不碰台账。
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import yun_finalize as Y          # noqa: E402
import tc_manage as T            # noqa: E402

AID = "2776839"

LIST_JS = r"""
(async function(hs){
  const out = [];
  for (let page = 1; page <= 8; page++) {
    const r = await fetch('/developer/api/creator/articleList',
      {method:'POST', headers:{'Content-Type':'application/json'},
       credentials:'include',
       body: JSON.stringify({hostStatus: hs, sortType:'create', page: page, pageSize: 20})});
    let j = null; try { j = await r.json(); } catch(e) {}
    const list = (j && (j.data && (j.data.list || j.data.records) || j.list || j.records)) || [];
    if (!list.length) break;
    for (const it of list) out.push(it);
    if (list.length < 20) break;
  }
  return JSON.stringify(out);
})
"""

DEL_JS = r"""
(async function(){
  const r = await fetch('/developer/api/common/delete-content',
    {method:'POST', headers:{'Content-Type':'application/json'},
     credentials:'include',
     body: JSON.stringify({objId: %s, module: 1, forever: 0})});
  let j = null; try { j = await r.json(); } catch(e) {}
  return JSON.stringify({status: r.status, body: j});
})()
""" % AID


def fetch(cdp, hs):
    return json.loads(cdp.eval("(%s)(%d)" % (LIST_JS, hs)) or "[]")


def main():
    proc, cdp = Y._connect(headed=True)
    try:
        cdp.open(T.LIST_URL)
        time.sleep(9)
        rej = fetch(cdp, 3)
        allv = fetch(cdp, 0)
        pub = fetch(cdp, 2)
        print("删前：hostStatus=0 → %d 条 ｜ =2(已发布) → %d 条 ｜ =3(已驳回) → %d 条"
              % (len(allv), len(pub), len(rej)))
        hit = [a for a in rej if str(a.get("articleId")) == AID]
        print("目标：%s" % (json.dumps([{k: hit[0].get(k) for k in
                     ("articleId", "title", "hostStatus")}],
                     ensure_ascii=False) if hit else "未命中"))
        if len(rej) != 1 or not hit:
            print("✗ 前置断言不成立，中止（不盲删）")
            return 1

        r = json.loads(cdp.eval(DEL_JS) or "{}")
        print("delete-content：", json.dumps(r, ensure_ascii=False)[:300])

        time.sleep(4)
        rej2 = fetch(cdp, 3)
        allv2 = fetch(cdp, 0)
        pub2 = fetch(cdp, 2)
        print("删后：hostStatus=0 → %d 条 ｜ =2 → %d 条 ｜ =3 → %d 条"
              % (len(allv2), len(pub2), len(rej2)))
        ids = {str(a.get("articleId")) for a in allv2}
        gone = AID not in ids and not any(
            str(a["articleId"]) == AID for a in rej2 + pub2)
        print("2776839 是否已从全部视图消失：%s" % gone)
        print("已发布条目数变化：%d → %d（应不变）" % (len(pub), len(pub2)))
        print("回收站可找回：forever=0 ⇒ 创作者中心-文章管理-回收站")

        ok = (gone and len(rej2) == 0 and len(allv2) == len(allv) - 1
              and len(pub2) == len(pub))
        print("结论：%s" % ("✓ 已删进回收站，且只少了这一条"
                          if ok else "✗ 未达预期，见上"))
        return 0 if ok else 1
    finally:
        try:
            cdp.bws.close()
        except Exception:                                  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
