#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性探测：头条「群发」接口里 save 字段的取值语义（静态扫 bundle）。

## 为什么必须静态先看

`article/publish` 已知能存草稿（`save=0`）。群发若在同一条链路上，差别只在
`save` 的取值。直接拿真实草稿试 `save=1` 有**真发到线上**的风险，
所以先从前端 bundle 里找调用点，确认前端自己传了什么。

## 实现要点（第一版超时的教训）

第一版把每个 JS 全量 `text()` 拉回 Python 再搜，6MB 文本过 CDP 传输直接卡死
（eval 无 socket 超时 ⇒ 永久挂住）。改成**在浏览器里搜、只回命中片段**：
每个 fetch 带 4s 超时，整轮 `Promise.all` 并发，回包体积可控。

## 用法

    python -X utf8 scripts/_oneoff/probe_tt_publish.py
    python -X utf8 scripts/_oneoff/probe_tt_publish.py --needle "save:"
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import wechat_publish as wp  # noqa: E402

PORT = int(os.environ.get("CDP_PORT", 9223))
EDITOR = "https://mp.toutiao.com/profile_v4/graphic/publish"

SCAN_JS = r"""(async function(cfg){
  var needles = cfg.needles, width = cfg.width, maxHits = cfg.maxHits;
  var urls = performance.getEntriesByType('resource')
    .map(function(e){ return e.name; })
    .filter(function(u){ return /\.js(\?|$)/.test(u); });
  var out = {total: urls.length, files: []};
  function withTimeout(p, ms){
    return Promise.race([p, new Promise(function(_, rej){
      setTimeout(function(){ rej(new Error('timeout')); }, ms); })]);
  }
  var jobs = urls.map(function(u){
    return withTimeout(fetch(u, {credentials:'include'}).then(function(r){
      if (!r.ok) return null;
      return r.text();
    }), 6000).then(function(t){
      if (!t) return null;
      var hits = [];
      needles.forEach(function(n){
        var start = 0, c = 0;
        while (c < 3) {
          var i = t.indexOf(n, start);
          if (i < 0) break;
          hits.push({n: n, ctx: t.slice(Math.max(0, i - width), i + width)});
          start = i + n.length; c++;
        }
      });
      if (!hits.length) return null;
      return {u: u.split('/').pop().split('?')[0].slice(0, 60), len: t.length, hits: hits};
    }).catch(function(){ return null; });
  });
  var rs = await Promise.all(jobs);
  rs.forEach(function(r){ if (r && out.files.length < maxHits) out.files.push(r); });
  return JSON.stringify(out);
})(%s)"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--needle", action="append",
                    help="关键词，可重复（默认 article/publish）")
    ap.add_argument("--width", type=int, default=260)
    ap.add_argument("--max-hits", type=int, default=6)
    ap.add_argument("--url", default=EDITOR, help="要扫哪个页面（默认编辑器页）")
    args = ap.parse_args()
    needles = args.needle or ["article/publish"]

    cdp = wp.CDP(PORT)
    t = cdp.new_target(args.url)
    if not cdp.connect_target(t["id"]):
        print("✗ 连不上编辑器页")
        return 1
    try:
        time.sleep(10)
        print("页面：%s" % str(cdp.eval("location.href") or "")[:90], flush=True)
        cfg = json.dumps({"needles": needles, "width": args.width,
                          "maxHits": args.max_hits})
        print("扫 JS（每个 6s 超时，并发）…", flush=True)
        raw = cdp.eval(SCAN_JS % cfg)
        r = json.loads(raw or '{"total":0,"files":[]}')
        print("共 %d 个 JS，命中 %d 个文件" % (r["total"], len(r["files"])))
        for f in r["files"]:
            print("\n=== %s（%d 字节）" % (f["u"], f["len"]))
            for h in f["hits"]:
                print("   [%s] …%s…\n" % (h["n"], h["ctx"].replace("\n", " ")))
        if not r["files"]:
            print("✗ 无命中。试 --needle 'save:' / 'govern_forward' / 'pgc_id'")
        return 0
    finally:
        try:
            cdp.close_target(t["id"])
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
