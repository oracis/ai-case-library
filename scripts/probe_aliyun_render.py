# -*- coding: utf-8 -*-
"""把长尾稿填进阿里云 mditor 编辑器并验证渲染（分块写入，避免 WS 超长帧）。"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from juejin_publish import _CDP, _WS          # noqa: E402

MD_FILE = os.path.join(ROOT, "drafts", "longtail", "markdown-structure.md")
TITLE = "同一份 Markdown 分发到 4 个技术社区，标题层级为什么只在一处丢失"
CHUNK = 240


def page_ws(cdp, needle):
    # ⚠ 默认 timeout=20s 不够：一次性 set 整篇会同步跑一遍 markdown 渲染，
    #   大稿子会超 20s，表现为 recv 超时而不是报错。给到 120s。
    for t in cdp._http("/json"):
        if t.get("type") == "page" and needle in (t.get("url") or ""):
            return _WS(t["webSocketDebuggerUrl"], timeout=120)
    return None


def main():
    md = io.open(MD_FILE, encoding="utf-8").read()
    cdp = _CDP(9223)
    try:
        cdp.open("https://developer.aliyun.com/article/new")
        time.sleep(14)
        cdp.ws = page_ws(cdp, "article/new")
        if not cdp.ws:
            raise SystemExit("没找到发文页")

        # 标题
        got = cdp.eval(
            "(() => {const i=document.querySelector('input[placeholder*=\\u6807\\u9898]');"
            "if(!i) return 'nf'; i.focus();"
            "const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;"
            "s.call(i, %s); i.dispatchEvent(new Event('input',{bubbles:true}));"
            "return i.value;})()" % json.dumps(TITLE))
        print("标题 =", got)

        # 一次性写入：分块追加会被 mditor 的 input 重建吃掉尾巴（实测 4893 → 3453）
        total = cdp.eval(
            "(() => {const t=document.querySelector('textarea.textarea');"
            "const s=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;"
            "s.call(t, %s); t.dispatchEvent(new Event('input',{bubbles:true}));"
            "return t.value.length;})()" % json.dumps(md))
        print("正文写入字符数 =", total, "（源 %d）" % len(md))
        time.sleep(4)

        tags = cdp.eval(
            "(() => {const o={};"
            "document.querySelectorAll('.editor-box h1,.editor-box h2,.editor-box h3,"
            ".editor-box strong,.editor-box ul,.editor-box li,.editor-box pre,"
            ".editor-box code,.editor-box table,.editor-box p').forEach(e=>{"
            "o[e.tagName]=(o[e.tagName]||0)+1}); return o;})()")
        print("预览结构标签 =", json.dumps(tags, ensure_ascii=False))

        raw = cdp.eval(
            "(() => {const t=document.querySelector('.editor-box').innerText||'';"
            "return {hash:(t.match(/^#{1,6} /gm)||[]).length,"
            "star:(t.match(/[*][*]/g)||[]).length};})()")
        print("字面符号残留 =", json.dumps(raw, ensure_ascii=False))

        cdp.send("Page.enable")
        shot = cdp.send("Page.captureScreenshot", {"format": "png"})
        out = os.path.join(ROOT, "tmp", "_ali_render_check.png")
        with open(out, "wb") as f:
            f.write(base64.b64decode(shot["result"]["data"]))
        print("截图 =", out)
    finally:
        cdp.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
