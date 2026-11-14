#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探针：腾讯云「导入MD」怎样喂文件才不弹系统文件选择框。

背景：`yun_draft_cdp.create_tencent()` 原来是「真鼠标点击『导入MD』」再回头
找 `input[type=file]` 喂文件 ⇒ 那一下点击会弹出 Windows 原生文件选择框，
人得手动点取消，无人值守时整条命令卡住。

本探针只读 / 只注入，**绝不点「导入MD」、绝不点「存草稿」、不写台账**，
依次验证三条零弹窗路径，每条都在**全新页面**上跑（互不污染）：

  A. DOM 结构：确认 `input[type=file]` 是否常驻、accept 值、父节点
  B. `DOM.setFileInputFiles`（CDP 直喂，完全无点击）
  C. 页内构造 `File` + DataTransfer 赋给 `input.files` + 派发 input/change
     —— `yun_finalize.inject_cover()` 对付封面用的就是这招

哪条能让 `.cm-content` 出现正文，哪条就是答案。

用法（必须与起浏览器放在同一条命令里，本机后台任务会被回收）：
  CHROME_HEADED=1 python -X utf8 scripts/_oneoff/probe_tc_import_md.py
"""
import base64
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import yun_cdp as Y                                   # noqa: E402

TENCENT_NEW = "https://cloud.tencent.com/developer/article/write-new"

DUMP_JS = r"""
(function(){
  function info(e){
    return {
      tag: e.tagName,
      accept: e.getAttribute('accept') || '',
      visible: e.offsetParent !== null,
      style: (e.getAttribute('style') || '').slice(0, 140),
      parent_cls: ((e.parentElement && e.parentElement.className) || '')
                   .toString().slice(0, 100)
    };
  }
  var ins = [].slice.call(document.querySelectorAll('input[type=file]'));
  return JSON.stringify({
    url: location.href,
    file_input_count: ins.length,
    file_inputs: ins.map(info)
  }, null, 2);
})()
"""

CM_LEN_JS = ("(function(){var e=document.querySelector('.cm-content');"
             "return e ? (e.innerText||'').length : -1;})()")

MD_BODY = ("# 探针标题\n\n第一段用来验证正文是否真的载入。\n\n"
           "## 小标题\n\n- 甲\n- 乙\n")


def open_editor(cdp):
    """开一个新页面并等编辑器就绪，返回 targetId。"""
    tid = cdp.open(TENCENT_NEW)
    for _ in range(40):
        time.sleep(1)
        if cdp.eval("!!document.querySelector("
                    "'textarea.cdc-article-editor__title-input')"):
            return tid
    raise SystemExit("编辑器没起来: %s" % cdp.eval("location.href"))


def watch(cdp, label, seconds=24):
    """轮询 .cm-content 长度，返回是否载入。"""
    for i in range(seconds // 2):
        time.sleep(2)
        n = cdp.eval(CM_LEN_JS)
        print("    +%2ds 字数=%s" % ((i + 1) * 2, n))
        if isinstance(n, int) and n > 20:
            head = cdp.eval("(document.querySelector('.cm-content')"
                            ".innerText||'').slice(0,70)")
            print("  ✓ %s 生效，首段: %r" % (label, head))
            return True
    print("  ✗ %s 无效（正文仍空）" % label)
    return False


def route_b(cdp, md_path):
    """CDP 直喂：DOM.setFileInputFiles。"""
    cdp.send("DOM.enable")
    root = cdp.send("DOM.getDocument")["result"]["root"]["nodeId"]
    q = cdp.send("DOM.querySelector",
                 {"nodeId": root, "selector": "input[type=file]"})
    nid = (q.get("result") or {}).get("nodeId") or 0
    print("  input nodeId =", nid)
    if not nid:
        return False
    cdp.send("DOM.setFileInputFiles",
             {"nodeId": nid, "files": [md_path.replace("\\", "/")]})
    return watch(cdp, "B/CDP直喂")


def route_c(cdp, md_path):
    """页内注入：构造 File 赋给 input.files。"""
    with open(md_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    js = r"""
(function(){
  try{
    var b64="%s", bin=atob(b64), n=bin.length, arr=new Uint8Array(n);
    for(var i=0;i<n;i++) arr[i]=bin.charCodeAt(i);
    var f=new File([arr], "import.md", {type:"text/markdown"});
    var dt=new DataTransfer(); dt.items.add(f);
    var inp=document.querySelector('input[type=file]');
    if(!inp) return 'NO_INPUT';
    inp.files=dt.files;
    inp.dispatchEvent(new Event('input',{bubbles:true}));
    inp.dispatchEvent(new Event('change',{bubbles:true}));
    return 'DISPATCHED';
  }catch(e){ return 'ERR:'+e.message }
})()""" % b64
    print("  注入:", str(cdp.eval(js))[:40])
    return watch(cdp, "C/页内File注入")


def route_d(cdp, md_path):
    """拦截 fileChooser 后**真实点击**「导入MD」，再按 backendNodeId 喂文件。

    这是 `yun_finalize.upload_image()` 对阿里云已验证的姿势：拦截开着时
    页面自己 `input.click()` 不会弹系统对话框，只发 `Page.fileChooserOpened`，
    我们拿到 backendNodeId 再把文件塞进去，页面照常走 change 加载。
    """
    cdp.send("Page.enable")
    cdp.send("DOM.enable")
    cdp.send("Page.setInterceptFileChooserDialog", {"enabled": True})
    try:
        disp = cdp.eval("""(function(){
          var its = document.querySelectorAll('.tool-list-item');
          for (var i=0;i<its.length;i++){
            if ((its[i].textContent||'').trim() === '导入MD'){
              its[i].click(); return 'clicked';
            }
          }
          return 'notfound';
        })()""")
        print("  点「导入MD」:", disp)
        if disp != "clicked":
            return False
        # 拦截开着的证据：等到 fileChooserOpened（未拦截时不会有这个事件）
        got = None
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                msg = json.loads(cdp.ws.recv_text())
            except Exception:                          # noqa: BLE001
                continue
            if msg.get("method") == "Page.fileChooserOpened":
                got = msg["params"].get("backendNodeId")
                break
        print("  fileChooserOpened backendNodeId =", got,
              "⇒ 对话框已被拦截（否则不会发此事件）" if got else "⇒ 未收到")
        time.sleep(1.5)
        # 点击后重新枚举 input（页面可能刚创建了真正那一个）
        root = cdp.send("DOM.getDocument")["result"]["root"]["nodeId"]
        ids = (((cdp.send("DOM.querySelectorAll",
                          {"nodeId": root,
                           "selector": "input[type=file]"})
                 .get("result") or {}).get("nodeIds")) or [])
        print("  点击后 file input 数 =", len(ids))
        targets = [got] if got else []
        for nid in ids:
            d = cdp.send("DOM.describeNode", {"nodeId": nid})
            node = d.get("result", {}).get("node", {})
            acc = ""
            attrs = node.get("attributes") or []
            for i in range(0, len(attrs) - 1, 2):
                if attrs[i] == "accept":
                    acc = attrs[i + 1]
            print("    nodeId=%s backendNodeId=%s accept=%r"
                  % (nid, node.get("backendNodeId"), acc))
            if "md" in acc.lower() and node.get("backendNodeId") not in targets:
                targets.append(node.get("backendNodeId"))
        print("  待喂 backendNodeId:", targets)
        for be in targets:
            try:
                cdp.send("DOM.setFileInputFiles",
                         {"files": [md_path.replace("\\", "/")],
                          "backendNodeId": be})
            except Exception as e:                     # noqa: BLE001
                print("    setFileInputFiles 失败:", str(e)[:60])
        return watch(cdp, "D/拦截+点击")
    finally:
        cdp.send("Page.setInterceptFileChooserDialog", {"enabled": False})


def main():
    md = os.path.join(ROOT, "tmp", "_probe_import.md")
    os.makedirs(os.path.dirname(md), exist_ok=True)
    with open(md, "w", encoding="utf-8") as f:
        f.write(MD_BODY)

    proc, cdp = Y._launch_profile(9228)
    result = {}
    try:
        # A：DOM 结构
        open_editor(cdp)
        print("\n===== A. 点击前 DOM =====")
        raw = cdp.eval(DUMP_JS)
        try:
            print(json.dumps(json.loads(raw), ensure_ascii=False, indent=2))
        except Exception:                              # noqa: BLE001
            print(raw)

        # B：CDP 直喂（全新页面）
        print("\n===== B. DOM.setFileInputFiles =====")
        cdp.open("about:blank")
        open_editor(cdp)
        result["B"] = route_b(cdp, md)

        # C：页内 File 注入（全新页面）
        print("\n===== C. 页内构造 File =====")
        cdp.open("about:blank")
        open_editor(cdp)
        result["C"] = route_c(cdp, md)

        # D：拦截 fileChooser + 真实点击（全新页面）
        print("\n===== D. 拦截 fileChooser + 点击「导入MD」 =====")
        cdp.open("about:blank")
        open_editor(cdp)
        result["D"] = route_d(cdp, md)

        print("\n===== 结论 =====")
        for k in ("B", "C", "D"):
            print("  %s: %s" % (k, "可用（零弹窗）" if result[k] else "无效"))
    finally:
        try:
            cdp.send_browser("Browser.close")
        except Exception:                              # noqa: BLE001
            pass
        try:
            proc.wait(timeout=10)
        except Exception:                              # noqa: BLE001
            proc.kill()
    return 0 if any(result.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
