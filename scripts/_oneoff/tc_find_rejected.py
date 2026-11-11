#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读探针：按 hostStatus 0..8 逐个调 articleList，找 2776839 落在哪个视图。"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import yun_finalize as Y          # noqa: E402
import tc_manage as T            # noqa: E402

AID = "2776839"

JS = r"""
(async function(){
  const out = {};
  for (const hs of [0,1,2,3,4,5,6,7,8,9]) {
    const r = await fetch('/developer/api/creator/articleList',
      {method:'POST', headers:{'Content-Type':'application/json'},
       credentials:'include',
       body: JSON.stringify({hostStatus: hs, sortType:'create', page:1, pageSize:50})});
    let j = null; try { j = await r.json(); } catch(e) {}
    const list = (j && (j.data && (j.data.list || j.data.records) || j.list || j.records)) || [];
    out[hs] = {n: list.length,
               ids: list.map(x => String(x.articleId)),
               hit: list.filter(x => String(x.articleId) === "%s")
                        .map(x => ({t: x.title, hs: x.hostStatus, st: x.status}))};
  }
  return JSON.stringify(out);
})()
""" % AID


def main():
    proc, cdp = Y._connect(headed=True)
    try:
        cdp.open(T.LIST_URL)
        time.sleep(9)
        d = json.loads(cdp.eval(JS) or "{}")
        for hs, v in d.items():
            print("hostStatus=%s → %d 条  hit=%s" % (hs, v["n"], v["hit"]))
            if v["n"] and v["n"] <= 50:
                print("     ids:", ", ".join(v["ids"][:12]))
        return 0
    finally:
        try:
            cdp.bws.close()
        except Exception:                                  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
