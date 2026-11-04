#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 CSDN 草稿补封面（不发表）。

一篇一次，全流程一个浏览器会话：
  1) 打开文章编辑器（articleId=xxx），读出草稿现有标题/正文
  2) 点「发布文章」开抽屉 → 把图喂给 `input.el-upload__input` → 确认裁剪 → 取 csdnimg URL
  3) 关掉抽屉（不点发布、也不点抽屉里的保存）
  4) 用「保存草稿」同款 payload（id + status:2 + cover_images）把正文与封面写回

用法：
    python -X utf8 scripts/csdn_draft_cover.py --aid 170045008 --cover a.png [--md drafts/x.md]

⚠ 不带 --md 时用编辑器里读到的正文回写；读到空正文直接中止，绝不写空。
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ.setdefault("NO_PROXY", "*")

import chrome_cdp_launch as L          # noqa: E402
import csdn_publish as C               # noqa: E402
import csdn_rules as R                 # noqa: E402
import csdn_save_draft as D            # noqa: E402
import csdn_set_cover as SC            # noqa: E402
import wechat_publish as wp            # noqa: E402

PORT = 9224

READ_EDITOR = r"""(function(){
  var pre = document.querySelector('.editor__inner');
  var ti = document.querySelector('input.article-bar__title');
  return JSON.stringify({
    title: ti ? ti.value : '',
    body: pre ? (pre.innerText||'') : ''
  });
})()"""

CLOSE_DRAWER = r"""(function(){
  var btns = document.querySelectorAll('button,span,div');
  for (var i=0;i<btns.length;i++){
    var t=(btns[i].innerText||'').trim();
    if (t === '取消' && btns[i].getBoundingClientRect().height>0){ btns[i].click(); return 'cancel'; }
  }
  document.body.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',keyCode:27,bubbles:true}));
  return 'esc';
})()"""


def open_cdp(timeout=45):
    L.clear_proxy()
    L.remove_lock(C.LOGIN_PROFILE)
    chrome, _ = L.pick_chrome()
    args = L.build_args(chrome, C.LOGIN_PROFILE, port=PORT, headless=False)
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    if not L.wait_cdp(proc, timeout=timeout, port=PORT):
        proc.terminate()
        raise SystemExit("✗ Chrome CDP 没起来（端口 %d）" % PORT)
    time.sleep(2)
    return proc, wp.CDP(PORT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aid", required=True)
    ap.add_argument("--cover", required=True)
    ap.add_argument("--md", default="", help="给定则用这份 md 覆盖正文")
    a = ap.parse_args()

    # 开浏览器之前先把封面处理掉：>2MB 的图 CSDN 抽屉传不上去，白白等 40s
    a.cover = SC.ensure_cover(a.cover)

    md_title = md_body = None
    if a.md:
        md_title, md_body = R.read_md(a.md)
        md_body = R.clean(md_body)
        r = R.scan(md_title, md_body)
        if r["block"]:
            print("✗ 合规闸未过：%s" % [x["rule"] for x in r["block"]])
            return 1

    proc, cdp = open_cdp()
    try:
        tid = cdp.new_target("https://editor.csdn.net/md/?articleId=%s" % a.aid)["id"]
        cdp.connect_target(tid)
        print("[1] 等编辑器 24s", flush=True)
        time.sleep(24)
        here = str(cdp.eval("location.href"))
        if "articleId=%s" % a.aid not in here:
            # 2026-11-04 踩过：审核中/回收站的文章，editor?articleId=X 会被重定向成空白新稿，
            # 此时任何「发布」动作都会新建一篇。必须停下，先去列表里把这篇恢复成草稿。
            print("✗ 编辑器把 articleId 吞了（当前 %s）" % here[:80])
            print("  ⇒ 这篇多半在审核中/回收站/已下架：先用 csdn_list_drafts.py 查状态，"
                  "回收站里点「回收至草稿箱」恢复后再来")
            return 1
        raw = str(cdp.eval(READ_EDITOR))
        try:
            got = json.loads(raw)
        except ValueError:          # 正文里的换行是裸控制字符，放开 strict
            got = json.loads(raw, strict=False)
        print("    标题：%s" % (got.get("title") or "")[:50], flush=True)
        print("    正文：%d 字" % len(got.get("body") or ""), flush=True)

        title = md_title or (got.get("title") or "").strip()
        body = md_body or (got.get("body") or "")
        if not body.strip():
            print("✗ 正文为空，中止（不写空稿）")
            return 1

        print("[2] 开抽屉", flush=True)
        print("    %s" % str(cdp.eval(SC.JS_OPEN_DRAWER)), flush=True)
        time.sleep(9)
        url, err = SC._feed_and_confirm(cdp, os.path.abspath(a.cover))
        if err:
            print("✗ 上传失败：%s" % err)
            return 1
        print("    ✓ 封面 URL %s" % url, flush=True)
        print("[3] 关抽屉：%s" % str(cdp.eval(CLOSE_DRAWER)), flush=True)
        time.sleep(2)

        print("[4] 写回草稿（正文 + 封面）", flush=True)
        aid, u = D.save_draft(a.aid, title, body, cover_images=[url], via="browser")
        print("    ✓ id=%s %s" % (aid, u), flush=True)
        return 0
    finally:
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
