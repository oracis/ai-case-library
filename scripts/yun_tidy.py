#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""腾讯云草稿正文「空段落清理」。

起因（2026-10-31，用户实拍反馈）：腾讯云文章正文行间距过大、不协调。
  `yun_inspect.py` 体检结果：
    子块 69 / p 75 / **空块 35**，每个空块是 `<p><span> </span></p>`，
    各带 margin-bottom:20px ⇒ 真实段间距被撑成 20+20+20 ≈ 60px。
  根因：腾讯云「切换 MD 编辑器」把 markdown 的**每个空行也转成一个空段落**
    （逐行转块），不是平台样式问题。

修法：在已打开的草稿页里，把「无文字且不含图/线/表」的顶层 `<p>` 逐个
  选中整节点 → 按 Delete 删除 → 重新扫描，直到清零，再点「存草稿」。

⚠ 判据必须排除含 img/hr/video/pre 的段落：图片段落 innerText 也是空的，
  按「空」删掉会把正文配图一起删。
⚠ 首段的 `<p><br class="ProseMirror-trailingBreak">` 也算空段落，一并清。

用法：
  python scripts/yun_tidy.py --port 9228                 # 只清理，不保存
  python scripts/yun_tidy.py --port 9228 --save          # 清理并点「存草稿」
  python scripts/yun_tidy.py --port 9228 --save --dry    # 只报数
"""
import argparse
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from juejin_publish import _WS                    # noqa: E402


def _list(port):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return json.loads(op.open("http://127.0.0.1:%d/json/list" % port,
                              timeout=8).read())


class Tab:
    def __init__(self, ws_url):
        self.ws = _WS(ws_url)
        self._id = 0

    def send(self, method, params=None):
        self._id += 1
        self.ws.send_text(json.dumps({"id": self._id, "method": method,
                                      "params": params or {}}))
        while True:
            r = json.loads(self.ws.recv_text())
            if r.get("id") == self._id:
                return r

    def eval(self, js):
        r = self.send("Runtime.evaluate",
                      {"expression": js, "returnByValue": True,
                       "awaitPromise": True})
        return r.get("result", {}).get("result", {}).get("value")

    def key(self, key, code, vk):
        for tp in ("keyDown", "keyUp"):
            self.send("Input.dispatchKeyEvent", {
                "type": tp, "key": key, "code": code,
                "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk,
                "nativeVirtualKeyCode": vk})


def pick(port, want="write-new"):
    for t in _list(port):
        if t.get("type") == "page" and want in (t.get("url") or "") \
                and t.get("webSocketDebuggerUrl"):
            return t
    return None


JS_STATS = """
(function(){
  var ed = document.querySelector('#cdc-rich-editor');
  if (!ed) return JSON.stringify({err:'no editor'});
  var kids = ed.children, empty = 0, real = 0, pic = 0;
  for (var i = 0; i < kids.length; i++) {
    var k = kids[i];
    if (k.tagName !== 'P') { real++; continue; }
    var txt = (k.innerText || '').trim();
    if (txt) { real++; continue; }
    if (k.querySelector('img,hr,video,iframe,pre,table')) { pic++; real++; continue; }
    empty++;
  }
  return JSON.stringify({total: kids.length, empty: empty, real: real,
                         withMedia: pic});
})()
"""

JS_SELECT_EMPTY = """
(function(){
  var ed = document.querySelector('#cdc-rich-editor');
  if (!ed) return 'no editor';
  ed.focus();
  var kids = ed.children, target = null;
  for (var i = 0; i < kids.length; i++) {
    var k = kids[i];
    if (k.tagName !== 'P') continue;
    if ((k.innerText || '').trim()) continue;
    if (k.querySelector('img,hr,video,iframe,pre,table')) continue;
    target = k; break;
  }
  if (!target) return 'none';
  var r = document.createRange();
  r.selectNode(target);
  var s = window.getSelection();
  s.removeAllRanges();
  s.addRange(r);
  return 'sel:' + (target.innerText || '').length;
})()
"""


JS_TIDY = """
(function(){
  var ed = document.querySelector('#cdc-rich-editor');
  var E = ed && ed.editor;
  if (!E || !E.state || !E.view) return 'no-tiptap';
  var doc = E.state.doc;
  var ranges = [];
  doc.forEach(function(node, offset){
    if (node.type.name !== 'paragraph') return;
    if (node.textContent.trim()) return;
    var media = false;
    node.descendants(function(n){
      if (n.type.name === 'image' || n.type.name === 'horizontalRule' ||
          n.type.name === 'table') media = true;
    });
    if (media) return;
    ranges.push([offset, offset + node.nodeSize]);
  });
  if (!ranges.length) return 'clean:' + doc.childCount;
  // schema 是 block+ ⇒ 必须至少留一个块，否则 doc 非法
  var list = (doc.childCount - ranges.length >= 1) ? ranges
                                                   : ranges.slice(0, ranges.length - 1);
  var tr = E.state.tr;
  for (var i = list.length - 1; i >= 0; i--) tr.delete(list[i][0], list[i][1]);
  E.view.dispatch(tr);
  return 'deleted:' + list.length + '/' + ranges.length;
})()
"""


def stats(tab):
    raw = tab.eval(JS_STATS)
    try:
        return json.loads(raw)
    except Exception:                                       # noqa: BLE001
        return {"err": str(raw)[:120]}


JS_FIX_HR = """
(function(){
  var E=document.querySelector('#cdc-rich-editor').editor;
  if (!E || !E.state) return 'no-tiptap';
  var s = E.schema, doc = E.state.doc, hits = [];
  doc.forEach(function(node, offset){
    if (node.type.name !== 'paragraph') return;
    var t = node.textContent.trim();
    if (t !== '---' && t !== '***' && t !== '___') return;
    hits.push([offset, offset + node.nodeSize]);
  });
  if (!hits.length) return 'nohr:0';
  var tr = E.state.tr;
  for (var i = hits.length - 1; i >= 0; i--)
    tr.replaceWith(hits[i][0], hits[i][1], s.nodes.horizontalRule.create());
  E.view.dispatch(tr);
  return 'hr:' + hits.length;
})()
"""


def fix_horizontal_rules(client, log=print):
    """把正文里字面的 `---` 段落换成真水平线，返回替换个数。

    腾讯云的 MD 转换**不认 `---`**（只当普通段落文本），用户实拍反馈
    「红圈里是不是分割线，样式丢了」。用 transaction 换成
    `horizontalRule` 节点，平台会渲染成 div.horizontal-rule-container > hr
    （1px / #d6dbe3 / 上下 20px），与所见即所得一致。
    """
    r = str(client.eval(JS_FIX_HR))
    log("    fix-hr: %s" % r)
    try:
        return int(r.split(":")[1])
    except (IndexError, ValueError):
        return 0


JS_CURSOR_FIRST_P = """
(function(){
  var E=document.querySelector('#cdc-rich-editor').editor;
  if (!E || !E.state) return 'no-tiptap';
  var pos = null;
  E.state.doc.forEach(function(n, o){
    if (pos !== null) return;
    if (n.type.name === 'paragraph' && n.content.size > 0) pos = o + 1 + n.content.size;
  });
  if (pos === null) return 'no-para';
  E.view.dispatch(E.state.tr.setSelection(
      E.state.selection.constructor.near(E.state.doc.resolve(pos))));
  E.view.focus();
  return 'ok@' + pos;
})()
"""


def refresh_wordcount(client, log=print):
    """触发平台页脚「正文字数」重算，返回 True/False。

    🚨 2026-10-31 实测：`tr.delete` / `tr.replaceWith` 这类**程序化 transaction
      ＜不会＞**让腾讯云的页脚字数重算 —— reload 后甚至一直挂 0，用户实拍
      「正文字数：0」以为正文被清空（实际 1107 字、2 表 1 线都在）。
      只有真实键盘输入才触发。⇒ 在第一个非空段落末尾敲一个 'x' 再退格，
      内容零变化，计数恢复。
    """
    r = str(client.eval(JS_CURSOR_FIRST_P))
    if not r.startswith("ok@"):
        log("    字数重算跳过: %s" % r)
        return False

    def send(p):
        client.send("Input.dispatchKeyEvent", p)

    send({"type": "keyDown", "key": "x", "code": "KeyX",
          "windowsVirtualKeyCode": 88, "nativeVirtualKeyCode": 88})
    send({"type": "char", "text": "x", "unmodifiedText": "x", "key": "x",
          "windowsVirtualKeyCode": 88, "nativeVirtualKeyCode": 88})
    send({"type": "keyUp", "key": "x", "code": "KeyX",
          "windowsVirtualKeyCode": 88, "nativeVirtualKeyCode": 88})
    time.sleep(0.4)
    send({"type": "keyDown", "key": "Backspace", "code": "Backspace",
          "windowsVirtualKeyCode": 8, "nativeVirtualKeyCode": 8})
    send({"type": "keyUp", "key": "Backspace", "code": "Backspace",
          "windowsVirtualKeyCode": 8, "nativeVirtualKeyCode": 8})
    time.sleep(0.7)
    log("    字数重算: %s" % wordcount(client))
    return True


def wordcount(client):
    """读页脚「正文字数：N」。"""
    return client.eval("""
(function(){
  var els = document.querySelectorAll('.cdc-article-editor__footer-main span');
  for (var i = 0; i < els.length; i++) {
    var t = els[i].textContent || '';
    if (t.indexOf('正文字数') >= 0) return t.trim();
  }
  return 'n/a';
})()""")


def tidy_paragraphs(client, max_rounds=3, log=print):
    """清掉腾讯云编辑器里的空段落，返回 (删除数, 清理后统计)。

    🚨 2026-10-31 血泪：**不要用「选中整节点 + 按 Delete」** 来逐个删。
      实测在含水平线的文档上按到第 80 次时，整个正文被一次 Delete 抹光
      （34 块 → 1 块；因未保存且页面重载才捡回来）。
      正解：`#cdc-rich-editor.editor` 就是 TipTap 的 Editor 实例（有
      `state`/`view`/`chain`），直接构造 transaction 精确 `delete`,
      一次 dispatch 到位 —— 原子、可预测、不会误伤相邻块。

    client 只需有 eval（`_CDP` 与本地 `Tab` 都满足）。
    供 `yun_draft_cdp.create_tencent` 在建稿后自动调用 —— 否则每新建一篇
    都会带进 30+ 个空段落，正文行距全是 60px。
    """
    before = stats(client)
    if before.get("err"):
        return 0, before
    removed = 0
    for _ in range(max_rounds):
        r = str(client.eval(JS_TIDY))
        log("    tidy: %s" % r)
        if r.startswith("deleted:"):
            try:
                removed += int(r.split(":")[1].split("/")[0])
            except (IndexError, ValueError):
                pass
            continue
        break
    after = stats(client)
    # 安全阀：块数只能变少这么多个，绝不允许整篇消失
    if before.get("total") and after.get("total", 0) < before["total"] - removed - 1:
        raise SystemExit("🚨 清理异常：块数 %s → %s，请 reload 页面重来"
                         % (before.get("total"), after.get("total")))
    return removed, after



def click_text(tab, text):
    """按文案真实点击（腾讯云底部「存草稿」）。"""
    js = """
(function(){
  var want = %s, bs = document.querySelectorAll('button,[role=button],div,span,a');
  var best = null;
  for (var i = 0; i < bs.length; i++) {
    var b = bs[i];
    if ((b.textContent || '').trim() !== want) continue;
    if (b.offsetParent === null) continue;
    var r = b.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    var a = r.width * r.height;
    if (!best || a < best.a) best = {b: b, a: a,
      x: r.x + r.width / 2, y: r.y + r.height / 2};
  }
  if (!best) return '';
  return JSON.stringify({x: best.x, y: best.y});
})()""" % json.dumps(text)
    pos = tab.eval(js)
    if not pos:
        print("  ⚠ 没找到「%s」" % text)
        return False
    pos = json.loads(pos)
    for tp, btns in (("mousePressed", 1), ("mouseReleased", 0)):
        tab.send("Input.dispatchMouseEvent", {
            "type": tp, "x": pos["x"], "y": pos["y"], "button": "left",
            "clickCount": 1, "buttons": btns})
        time.sleep(0.08)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9228)
    ap.add_argument("--url", default="write-new")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--max-rounds", type=int, default=80)
    args = ap.parse_args()

    t = pick(args.port, args.url)
    if not t:
        print("没有 URL 含 %r 的页面" % args.url)
        return 2
    print("tab: %s" % t["url"][:110])
    tab = Tab(t["webSocketDebuggerUrl"])
    tab.send("Page.bringToFront")

    before = stats(tab)
    print("清理前: %s" % json.dumps(before, ensure_ascii=False))
    if before.get("err"):
        return 2
    if args.dry:
        return 0

    removed = 0
    hrs = 0
    try:
        hrs = fix_horizontal_rules(tab)
        removed, after = tidy_paragraphs(tab, log=lambda m: print(m))
    except SystemExit as e:
        print(str(e))
        return 1

    print("替换水平线 %d 处 ｜ 删除 %d 个空段落" % (hrs, removed))
    print("清理后: %s" % json.dumps(after, ensure_ascii=False))

    if args.save:
        if after.get("empty"):
            print("⚠ 仍有 %s 个空段落，先不保存" % after["empty"])
            return 1
        refresh_wordcount(tab, log=lambda m: print(m))
        if not click_text(tab, "存草稿"):
            return 1
        print("  已点「存草稿」，等落库…", flush=True)
        time.sleep(9)
        print("  URL: %s" % tab.eval("location.href"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
