#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从已登录的掘金页面里把「真实图片上传路由」挖出来。

为什么不用猜：社区流传的 `content_api/v1/image/upload`、`upload_pic` 实测
全是 `err_no:2 请求路由不存在`。掘金前端 bundle 里必然写了真实路径，
直接在页面上下文（同源、带 cookie、没有跨域限制）里把 js 拉下来 grep 即可。

用法：
    python scripts/_oneoff/jj_probe_upload_route.py
"""
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from juejin_publish import _CDP  # noqa: E402

PORT = 9223
EDITOR = "https://juejin.cn/editor/drafts/7700327310117077018"

JS = r"""
(async () => {
  const urls = performance.getEntriesByType('resource')
    .map(e => e.name).filter(u => u.endsWith('.js'));
  const hits = [];
  for (const u of urls.slice(0, 60)) {
    try {
      const t = await fetch(u).then(r => r.text());
      const m = t.match(/["'`](\/[^"'`\n]{0,80}upload[^"'`\n]{0,40})["'`]/g);
      if (m) hits.push(...m);
    } catch (e) {}
  }
  return JSON.stringify([...new Set(hits)].slice(0, 80));
})()
"""


def main():
    cdp = _CDP(PORT)
    try:
        cdp.open(EDITOR)
        time.sleep(6)
        raw = cdp.eval(JS)
        cands = []
        try:
            import json
            cands = json.loads(raw or "[]")
        except ValueError:
            print("eval 返回异常：%s" % str(raw)[:300])
        paths = set()
        for c in cands:
            c = c.strip("\"'`")
            if "/upload" in c:
                paths.add(c)
        if not paths:
            print("✗ 没挖到含 upload 的路径（bundle 可能做了异步分块）")
            return 1
        print("挖到 %d 条候选：" % len(paths))
        for p in sorted(paths):
            print("   %s" % p)
        return 0
    finally:
        cdp.close()


if __name__ == "__main__":
    sys.exit(main())
