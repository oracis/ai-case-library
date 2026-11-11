#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""排障收尾的独立回读：确认两组探针（改标题/新建空白稿）没有污染平台草稿。

判据（照 PRD #129 口径，不认命令回显，只认关页重开）：
  1) draft_list 条数 == 38（基线）；
  2) chatbase 的 gid 仍在，标题仍是原标题，无 `__probe` 残留稿；
  3) 全新 CDP 对象重开 chatbase 编辑页：正文 1652 字、注记仍在
     ⇒ 说明探针期间没有任何写入落库。

    /c/Python315/python.exe -X utf8 tmp/verify_tt_probe_clean.py
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import wechat_publish as wp                                   # noqa: E402
import toutiao_publish as tt                                  # noqa: E402

GID = "7699904114575753737"
PUB = "https://mp.toutiao.com/profile_v4/graphic/publish?pgc_id=" + GID
LIST = "https://mp.toutiao.com/mp/agw/creator_center/draft_list?type=0&count=200&app_id=1231"

JS_READ = r"""(function(){
  var t=document.querySelector("textarea[placeholder*='文章标题'],input[placeholder*='文章标题']");
  var eds=[].slice.call(document.querySelectorAll(".ProseMirror[contenteditable='true']"));
  eds.sort(function(a,b){return (b.innerText||'').length-(a.innerText||'').length});
  var b=eds.length?(eds[0].innerText||''):'';
  return JSON.stringify({title:(t&&t.value)||'', bodylen:b.length,
                         mark:b.indexOf('我去核了一遍')>=0});
})()"""


def main():
    # ① 草稿箱列表
    c = wp.CDP(tt.CDP_PORT)
    t = c.new_target(LIST)
    if not t or not c.connect_target(t["id"]):
        print("✗ 连不上草稿箱")
        return 1
    rows = tt.fetch_draft_api_raw(c) or []
    hit = [r for r in rows if str(r.get("gid") or r.get("pgc_id") or "") == GID]
    probes = [r for r in rows if "__probe" in str(r.get("title") or "")]
    print("草稿箱 %d 条（基线 38）" % len(rows))
    print("chatbase gid 命中 %d 条，title=%r" %
          (len(hit), (hit[0].get("title") if hit else None)))
    print("__probe 残留 %d 条" % len(probes))
    c.close_target(t["id"])

    # ② 关页重开，独立读编辑器正文
    c2 = wp.CDP(tt.CDP_PORT)
    t2 = c2.new_target("about:blank")
    c2.connect_target(t2["id"])
    try:
        c2.send("Emulation.setDeviceMetricsOverride",
                {"width": 1440, "height": 900, "deviceScaleFactor": 1,
                 "mobile": False})
        c2.send("Page.navigate", {"url": PUB})
        st = {}
        for _ in range(18):
            time.sleep(1.5)
            try:
                st = json.loads(c2.eval(JS_READ, refresh_context=True) or "{}")
            except Exception:                                    # noqa: BLE001
                continue
            if st.get("bodylen", 0) > 200:
                break
        print("独立回读 chatbase：%s" % json.dumps(st, ensure_ascii=False))
        ok = (len(rows) == 38 and len(hit) == 1 and not probes
              and st.get("bodylen") == 1652 and st.get("mark") is True
              and "·" not in (st.get("title") or ""))
        print("结论：%s" % ("✓ 平台草稿未被探针改动" if ok
                          else "✗ 有差异，须人工核"))
        return 0 if ok else 1
    finally:
        c2.close_target(t2["id"])


if __name__ == "__main__":
    sys.exit(main())
