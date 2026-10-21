#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""让小红书草稿箱里「裂掉」的图片重新显示 —— data-URL 注入，不改任何数据。

背景（2026-10-20 排了一上午的结论，别再从头查一遍）
--------------------------------------------------
草稿箱图片显示为裂图，**不是图片丢了**：

1. 草稿记录（浏览器本地 IndexedDB `draft-database-v1`）里的 fileId、
   签名预览 URL 全部完好；用 Python 直接 GET 那些 URL 全部 200，
   文件本体就是完好 PNG。
2. 裂的根因有两层：
   a. CDN（腾讯 COS）把 spectrum 预览文件的 Content-Type 标成了
      `application/octet-stream`（不是 image/*）；
   b. Chrome 的 ORB（Opaque Response Blocking）对跨域 no-cors 图片响应
      按 Content-Type 拦截 → `net::ERR_BLOCKED_BY_ORB`，naturalWidth=0。
   另：页面内带 Origin 的 cors fetch 会被 CDN 直接 400（Vary: Origin），
   Service Worker 也救不了（no-cors 响应 opaque 读不了 body）。
3. 所以：编辑器/草稿箱里显示裂图 ≠ 数据坏了。**发布不受影响**
   （发布走 fileId，服务端有文件）。别再「删裂图重传」——传完保存，
   重开还是裂（还是 ORB），白折腾还容易把记录搞乱。

原理
----
Python（不在浏览器里，不受 ORB 约束）把每张图下载下来 → base64 →
以 `data:image/png;base64,...` 注入 img.src。data URL 不走网络，
ORB 管不着 → 图就显示出来了。**纯显示层修补，不写任何存储。**

用法
----
    python -u -X utf8 scripts/xhs_draft_view.py                # 修抽屉缩略图并留着页面
    python -u -X utf8 scripts/xhs_draft_view.py --open CheckVibe
        # 再打开标题含关键词的草稿编辑器，大图也修
    --limit N   只修前 N 条缩略图（默认全部）
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.request

os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ["no_proxy"] = "*"

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import xhs_publish as xp          # noqa: E402
from xhs_draft_fix_images import Session   # noqa: E402

_OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))

#: 只修创作预览域的图（头像等正常图不动；blob:/data: 本来就不裂）
_WANT = ("sns-creator-preview", "sns-img", "xhscdn.com")

_JS_BROKEN_IMGS = """(function(){
  var out = [];
  [].slice.call(document.querySelectorAll('img')).forEach(function(im, idx){
    var s = im.src || '';
    if (im.naturalWidth !== 0) return;             // 好图不动
    if (s.indexOf('blob:')===0 || s.indexOf('data:')===0) return;
    if (%(want)s) return;
    var r = im.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) return;        // 隐藏占位不管
    out.push({i: idx, src: s.slice(0, 300)});
  });
  return JSON.stringify(out);})()""" % {"want": " && ".join(
    "s.indexOf('%s')<0" % w for w in _WANT)}

_JS_SET_BY_INDEX = """(function(i){
  var imgs = document.querySelectorAll('img');
  var im = imgs[i]; if (!im) return 'no-img';
  im.src = window.__du; return 'set';})(%d)"""


def fetch_b64(url):
    """下载图片转 data URL（Python 侧，绕开 ORB）。"""
    r = _OP.open(url, timeout=25)
    data = r.read()
    if data[:4] == b"\x89PNG":
        mime = "image/png"
    elif data[:2] == b"\xff\xd8":
        mime = "image/jpeg"
    elif data[:6] in (b"RIFF", ) and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        mime = r.headers.get("Content-Type") or "application/octet-stream"
    return "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii"))


def patch_broken(s, label="", limit=0):
    """把当前页面上所有裂掉的预览图换成 data URL。返回 (修了几张, 剩几张)。"""
    fixed = left = 0
    for _ in range(3):                      # 裂图可能分批渲染，扫三轮
        items = s.js(_JS_BROKEN_IMGS) or []
        if not isinstance(items, list):
            break
        if limit and len(items) > limit:
            items = items[:limit]
        if not items:
            break
        for it in items:
            try:
                du = fetch_b64(it["src"])
            except Exception as e:
                print("  [%s] 下载失败 %s：%s" % (label, it["src"][:60], e))
                left += 1
                continue
            s.sub.eval("window.__du = %s" % json.dumps(du),
                       refresh_context=True)
            r = s.js(_JS_SET_BY_INDEX % it["i"])
            if r == "set":
                fixed += 1
            else:
                left += 1
        time.sleep(1.5)
    return fixed, left


def main():
    ap = argparse.ArgumentParser(description="草稿箱裂图显示修补（不改数据）")
    ap.add_argument("--open", default="", help="打开标题含该关键词的草稿编辑器")
    ap.add_argument("--limit", type=int, default=0, help="最多修几张缩略图")
    ap.add_argument("--no-keep", action="store_true",
                    help="结束时关掉标签页（默认留着给用户看）")
    ap.add_argument("--grace", type=int, default=8,
                    help="修完后留几秒再退出（页面保留，与是否关标签无关）")
    args = ap.parse_args()

    if not xp._port_up(xp.PUB_PORT):
        raise SystemExit("调试 Chrome 没起来（%d）。先跑：\n"
                         "  python scripts/_launch_9223.py --hold 3600"
                         % xp.PUB_PORT)

    s = Session()
    s.open(fresh=True)
    keep_tab = not args.no_keep
    try:
        if not s.open_drawer():
            raise SystemExit("[err] 打不开草稿箱抽屉")
        fixed, left = patch_broken(s, label="抽屉", limit=args.limit)
        print("抽屉缩略图：修了 %d 张，剩 %d 张没修成" % (fixed, left))

        if args.open:
            drafts = s.list_drafts()
            want = xp.norm_title(args.open)
            d = next((x for x in drafts
                      if want in xp.norm_title(x.get("title") or "")), None)
            if not d:
                print("[err] 找不到标题含「%s」的草稿" % args.open)
                return
            ok, _ = s.open_editor(d.get("title"))
            if not ok:
                print("[err] 编辑器没打开")
                return
            s.wait_settled()
            fixed, left = patch_broken(s, label="编辑器")
            total, broken = s.img_health()
            print("编辑器：%d 张，裂 %d（修了 %d）" % (total, broken, fixed))
        print("\n页面留着了 —— 去浏览器看草稿箱（这个标签页）。"
              "数据没动过，看完直接关页面即可。")
        time.sleep(args.grace)
    finally:
        if keep_tab:
            s.tid = None          # 留标签页；脚本退出不关它
        else:
            s.close()


if __name__ == "__main__":
    main()
