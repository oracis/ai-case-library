#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""看一眼掘金编辑器页面里有什么：file input、按钮、封面区。

纯只读探测，不改任何东西。用于决定封面上传走哪条路。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from juejin_publish import _CDP  # noqa: E402

DRAFT = "7700327310117077018"
EDITOR = "https://juejin.cn/editor/drafts/" + DRAFT

JS = r"""
(() => {
  const files = document.querySelectorAll('input[type=file]');
  const btns = [...document.querySelectorAll('button')]
      .map(b => (b.innerText||'').trim()).filter(t => t && t.length < 12);
  const accept = [...files].map(f => f.getAttribute('accept') || '');
  return JSON.stringify({
    url: location.href,
    fileInputs: files.length,
    accept: accept,
    buttons: [...new Set(btns)].slice(0, 25)
  });
})()
"""


def main():
    cdp = _CDP(9223)
    try:
        cdp.open(EDITOR)
        time.sleep(10)
        print(cdp.eval(JS))
    finally:
        cdp.close()


if __name__ == "__main__":
    main()
