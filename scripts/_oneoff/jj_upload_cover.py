#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给掘金草稿上传封面 —— 走浏览器真实上传，不猜 REST 图床路由。

为什么这么做：`content_api/v1/image/upload`、`upload_pic` 那几个网传路由实测
全是 `err_no:2 请求路由不存在`；外链封面也不行（GitHub raw 国内拉不到）。
编辑器页面里有现成的 `<input type=file>`，直接把 File 塞进去触发它的 change，
由掘金前端自己去调它真正的图床接口。

用法：
    python scripts/_oneoff/jj_upload_cover.py out/toutiao/mcdpoints/cover.png
"""
import base64
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from juejin_publish import _CDP, list_drafts  # noqa: E402

DRAFT = "7700327310117077018"
EDITOR = "https://juejin.cn/editor/drafts/" + DRAFT

UPLOAD_JS = r"""
(b64) => {
  const btn = [...document.querySelectorAll('button')]
      .find(b => (b.innerText || '').trim() === '上传封面');
  if (!btn) return 'NO_BTN';
  let node = btn, input = null;
  for (let i = 0; i < 8 && node; i++) {
    input = node.querySelector && node.querySelector('input[type=file]');
    if (input) break;
    node = node.parentElement;
  }
  if (!input) return 'NO_INPUT';
  const bin = atob(b64);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  const file = new File([arr], 'cover.png', { type: 'image/png' });
  const dt = new DataTransfer();
  dt.items.add(file);
  input.files = dt.files;
  input.dispatchEvent(new Event('change', { bubbles: true }));
  input.dispatchEvent(new Event('input', { bubbles: true }));
  return 'DISPATCHED';
}
"""

STATE_JS = r"""
(() => {
  const imgs = [...document.querySelectorAll('img')]
      .map(i => i.src).filter(s => s && s.includes('byteimg'));
  return JSON.stringify(imgs.slice(0, 5));
})()
"""


def draft_cover():
    for d in list_drafts(limit=10) or []:
        a = d.get("article", d)
        if str(a.get("draft_id") or a.get("id")) == DRAFT:
            return a.get("cover_image")
    return None


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        "out", "toutiao", "mcdpoints", "cover.png")
    src = os.path.join(ROOT, src) if not os.path.isabs(src) else src
    if not os.path.isfile(src):
        raise SystemExit("✗ 封面不存在：%s" % src)
    b64 = base64.b64encode(open(src, "rb").read()).decode()
    print("封面：%s（%d 字节 → base64 %d 字符）" % (src, len(open(src, 'rb').read()), len(b64)))
    print("上传前 cover_image = %s" % draft_cover())

    cdp = _CDP(9223)
    try:
        cdp.open(EDITOR)
        time.sleep(12)
        r = cdp.eval("(%s)(%r)" % (UPLOAD_JS, b64))
        print("注入结果：%s" % r)
        if r != "DISPATCHED":
            return 1
        for i in range(30):
            time.sleep(4)
            imgs = []
            try:
                imgs = json.loads(cdp.eval(STATE_JS) or "[]")
            except ValueError:
                pass
            cov = draft_cover()
            if cov:
                print("✓ 封面已进草稿（第 %d 秒）：%s" % (i * 4, cov))
                return 0
            if imgs:
                print("  页面已出现图床图片（第 %d 秒），等草稿保存：%s"
                      % (i * 4, imgs[0][:90]))
        print("✗ 2 分钟了草稿里还没有封面。可能编辑器没触发自动保存，"
              "去 %s 手动点一下「上传封面」。" % EDITOR)
        return 1
    finally:
        cdp.close()


if __name__ == "__main__":
    sys.exit(main())
