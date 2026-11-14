#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 `yun_draft_cdp._feed_md_file()`：腾讯云导入 MD 全程不弹文件框。

只建页面、只注入正文，**不点「存草稿」、不写台账、不发请求落库**。

判据（三条全过才算修好）：
  1. 返回 "ok"（内部等 .cm-content 载入）
  2. 首段特征串出现在编辑器里
  3. `Ctrl+End` 跳文末后，末段特征串也命中（证明全文载入，不是只进了开头几行）
     ⚠ 别用 `.cm-content` innerText 长度当判据：CodeMirror 6 是虚拟滚动，
       只渲染视口内的行，3878 字的稿子只读回 768~1452 字（首版就误判过一次）。
另外：点击「导入MD」后脚本能立刻往下跑且 2~3 秒内正文就位 ⇒ 说明没有卡在
Windows 原生文件对话框上（被 `Page.setInterceptFileChooserDialog` 拦掉了）。

用法（必须与起浏览器放同一条命令）：
  CHROME_HEADED=1 python -X utf8 scripts/_oneoff/verify_tc_feed_md.py [md路径]
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import yun_cdp as Y                                   # noqa: E402
import yun_draft_cdp as D                             # noqa: E402

DEFAULT_MD = os.path.expandvars(
    r"D:\open-source-games\content\ep-011-minetest\platform\tencentcloud.md")


def main():
    md = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MD
    if not os.path.isfile(md):
        raise SystemExit("找不到 md: %s" % md)
    src = open(md, encoding="utf-8").read()
    src = "\n".join(ln for ln in src.split("\n")
                    if not ln.startswith("> 平台："))
    print("源稿: %s ｜ %d 字符" % (os.path.basename(md), len(src)))

    proc, cdp = Y._launch_profile(9228)
    rc = 1
    try:
        cdp.open("https://cloud.tencent.com/developer/article/write-new")
        ok = False
        for _ in range(40):
            time.sleep(1)
            if cdp.eval("!!document.querySelector("
                        "'textarea.cdc-article-editor__title-input')"):
                ok = True
                break
        if not ok:
            raise SystemExit("编辑器没起来: %s" % cdp.eval("location.href"))

        t0 = time.time()
        fed = D._feed_md_file(cdp, md)
        cost = time.time() - t0
        print("\n返回: %r ｜ 耗时 %.1fs" % (fed, cost))

        text = cdp.eval("(document.querySelector('.cm-content')||"
                        "{innerText:''}).innerText") or ""
        # ⚠ CodeMirror 6 是虚拟滚动：innerText 只含视口内行，长文必然「变短」，
        #   所以长度不能当判据（首版就误判过一次）。改为「跳文末验末段」。
        print("视口内字符数: %d（仅供参考，非全文）" % len(text))

        def key_line(s):
            """取一段里第一个够长、非装饰性的行作为特征串。"""
            for ln in s.split("\n"):
                t = ln.strip()
                if len(t) >= 12 and not t.startswith(("#", ">", "|", "-",
                                                      "!", "`")):
                    return t[:16]
            return ""

        key_head = key_line(src)
        key_tail = key_line("\n".join(src.split("\n")[::-1]))
        print("首段特征: %r ⇒ %s"
              % (key_head, "命中" if key_head and key_head in text else "未命中"))

        # 跳到文末（CM6 默认 keymap 支持 Ctrl+End），再看末段特征
        cdp.eval("(function(){var e=document.querySelector('.cm-content');"
                 "if(e){e.focus();}return !!e;})()")
        for tp in ("rawKeyDown", "keyUp"):
            cdp.send("Input.dispatchKeyEvent",
                     {"type": tp, "key": "End", "code": "End",
                      "windowsVirtualKeyCode": 35, "nativeVirtualKeyCode": 35,
                      "modifiers": 2})            # 2 = Ctrl
            time.sleep(0.2)
        time.sleep(1.2)
        tail_text = cdp.eval("(document.querySelector('.cm-content')||"
                             "{innerText:''}).innerText") or ""
        hit_tail = bool(key_tail) and key_tail in tail_text
        print("末段特征: %r ⇒ %s" % (key_tail, "命中" if hit_tail else "未命中"))
        print("末段 80 字: %r" % tail_text[-80:].replace("\n", "\\n"))

        checks = [
            ("返回 ok（内部已见正文）", fed == "ok"),
            ("首段特征命中", bool(key_head) and key_head in text),
            ("跳文末后末段特征命中", hit_tail),
        ]
        print("\n===== 判据 =====")
        for name, okc in checks:
            print("  [%s] %s" % ("PASS" if okc else "FAIL", name))
        rc = 0 if all(c[1] for c in checks) else 1
    finally:
        try:
            cdp.send_browser("Browser.close")
        except Exception:                              # noqa: BLE001
            pass
        try:
            proc.wait(timeout=10)
        except Exception:                              # noqa: BLE001
            proc.kill()
    return rc


if __name__ == "__main__":
    sys.exit(main())
