#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站编辑器**表格能力**探针 v2（只读，不提交任何内容）。

## v1 的失败教训
v1 打开空白 `new-edit` 页，结果 `编辑器就绪: False`、只扫到 28 个**导航栏图标**
（`menu-icon`/`bcc-icon-ic_*`），表格零命中。⚠ 但用户截图（`?aid=342428`
编辑已有稿）里工具栏明明有 T／≡／图片 按钮 ⇒ **v1 是假阴性**：
  · 空白 new-edit 页的编辑器可能不完整渲染；
  · 扫的 `button/[class*=tool]/i/svg` 太宽，捞进来一堆导航图标，
    真正的编辑器工具栏 class 可能是 `editor-toolbar`/`bcc-icon-*` 之类，
    被 `bcc-iconfont` 淹掉。

v2 做法：
  1. 打印所有 `[contenteditable]` 的 class，**先确认编辑器到底在不在**；
  2. 以编辑器元素为根，**只在其祖先链 + 兄弟节点**里找工具栏；
  3. 工具栏识别靠 class 里的 tool/bar/format/editor 关键词，而不是全页面扫；
  4. 报告找不到时**明确说「编辑器没渲染」**，不下「不支持表格」的结论。
     —— 这正是头条那个 bug 的教训：把「没扫到」当成「不存在」。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bilibili_publish as bp                                   # noqa: E402
import wechat_publish as wp                                    # noqa: E402

_PROBE_JS = r"""(function(){
  var out = {url: location.href, editors: [], toolbars: [], tableish: []};

  // 1) 页面里所有 contenteditable —— 先确认编辑器在不在
  var eds = [].slice.call(document.querySelectorAll('[contenteditable]'));
  eds.forEach(function(e){
    var r = e.getBoundingClientRect();
    out.editors.push({
      tag: e.tagName,
      cls: (e.className||'').slice(0,120),
      editable: e.getAttribute('contenteditable'),
      visible: r.width > 0 && r.height > 0,
      w: Math.round(r.width), h: Math.round(r.height)
    });
  });

  // 2) 工具栏候选：class 里带 tool/bar/format/editor/menu 容器
  var cands = [].slice.call(document.querySelectorAll('div,section,nav'));
  cands.forEach(function(e){
    var cls = (typeof e.className === 'string') ? e.className : '';
    if (!/tool|bar|format|editor|operate|func/i.test(cls)) return;
    var r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return;      // 不可见
    // 工具栏通常扁而宽（高 < 80），正文区不会这么扁
    if (r.height > 90) return;
    var kids = e.querySelectorAll('button,a,i,span,div');
    var names = [];
    [].slice.call(kids).slice(0, 40).forEach(function(k){
      var cs = (typeof k.className === 'string') ? k.className :
               ((k.className && k.className.baseVal) || '');
      var t = (k.getAttribute && (k.getAttribute('title') ||
               k.getAttribute('aria-label'))) || '';
      var s = [cs, t, (k.innerText||'').trim().slice(0,12)]
              .filter(Boolean).join(' | ');
      if (s) names.push(k.tagName + ': ' + s.slice(0,90));
    });
    out.toolbars.push({cls: cls.slice(0,100),
      w: Math.round(r.width), h: Math.round(r.height),
      n: kids.length, items: names});
  });

  // 3) 全页面找「表格」字样/ 命名（含 class），这次不限制在工具栏里
  var hits = [].slice.call(document.querySelectorAll('*')).filter(function(e){
    var cls = (typeof e.className === 'string') ? e.className :
               ((e.className && e.className.baseVal) || '');
    var t = (e.getAttribute && (e.getAttribute('title') ||
             e.getAttribute('aria-label'))) || '';
    var x = cls + ' ' + t;
    return /table|grid|表格|单元格/i.test(x);
  });
  hits.slice(0, 25).forEach(function(e){
    var cls = (typeof e.className === 'string') ? e.className :
               ((e.className && e.className.baseVal) || '');
    out.tableish.push(e.tagName + ' cls=' + cls.slice(0,90) +
      ' title=' + ((e.getAttribute && e.getAttribute('title'))||''));
  });
  return JSON.stringify(out);
})()"""


def _iframe_ctx(cdp):
    """拿到 read-editor iframe 的执行上下文 id。

    ⚠ v1/v2 的假阴性根源：编辑器在**同域 iframe** 里，主文档
      `document.querySelectorAll('[contenteditable]')` 恒为 0。
      同域 iframe 可从主文档直接读 `contentDocument`。
    """
    raw = cdp.eval("""(function(){
      var fs = [].slice.call(document.querySelectorAll('iframe'));
      var out = [];
      fs.forEach(function(f, i){
        try {
          var d = f.contentDocument;
          var eds = d ? d.querySelectorAll('[contenteditable]').length : -1;
          out.push({i: i, hasDoc: !!d, eds: eds,
                    cls: d && d.body ? (d.body.className||'').slice(0,60) : ''});
        } catch (e) { out.push({i: i, err: String(e)}); }
      });
      return JSON.stringify(out);
    })()""")
    try:
        return json.loads(raw or "[]")
    except ValueError:
        return []


def probe(url, label):
    cdp = wp.CDP(bp.PUB_PORT)
    tid = None
    for t in cdp.list_targets():
        if "member.bilibili.com" in t.get("url", "") \
                and t.get("webSocketDebuggerUrl"):
            tid = t["id"]
            break
    if not tid:
        tid = cdp.new_target(bp.BILI_ARTICLE)["id"]
    if not cdp.connect_target(tid):
        print("!! 连不上（端口 %d）" % bp.PUB_PORT)
        return None
    cdp.send("Page.navigate", {"url": url})
    time.sleep(12)

    print("\n########## %s ##########" % label)
    print("URL: %s" % (cdp.eval("location.href") or "")[:120])

    frames = _iframe_ctx(cdp)
    print("iframe %d 个：%s" % (
        len(frames), json.dumps(frames, ensure_ascii=False)[:300]))
    if not frames:
        print("⚠️ 没有 iframe —— 编辑器可能不在 iframe 里，需另找入口")

    # 在每个含 contenteditable 的 iframe 里扫工具栏
    found_editor = False
    for f in frames:
        if not f.get("hasDoc") or (f.get("eds") or 0) <= 0:
            continue
        found_editor = True
        idx = f["i"]
        print("\n--- iframe[%d] 里 contenteditable %d 个，扫工具栏 ---"
              % (idx, f["eds"]))
        r = cdp.eval("""(function(){
          var fs = document.querySelectorAll('iframe');
          var d = fs[%d].contentDocument;
          var ed = d.querySelector('.tiptap.ProseMirror.eva3-editor')
                || d.querySelector('[contenteditable]');
          if (!ed) return JSON.stringify({err: 'no editor'});
          var out = {edCls: (ed.className||'').slice(0,100),
                     tableish: [], toolbars: []};
          // 表格相关（含 class）
          [].slice.call(d.querySelectorAll('*')).forEach(function(e){
            var cls = (typeof e.className === 'string') ? e.className
                     : ((e.className && e.className.baseVal) || '');
            var t = (e.getAttribute && (e.getAttribute('title') ||
                   e.getAttribute('aria-label'))) || '';
            if (/table|grid|表格|单元格/i.test(cls + ' ' + t)) {
              out.tableish.push(e.tagName + ' cls=' + cls.slice(0,90) +
                ' title=' + t);
            }
          });
          // 工具栏容器：编辑器向上找，或扁而宽的兄弟容器
          var cands = [].slice.call(d.querySelectorAll('div,section,nav,ul'))
            .filter(function(e){
              var cls = (typeof e.className === 'string') ? e.className : '';
              if (!/tool|bar|format|operate|editor/i.test(cls)) return false;
              var r = e.getBoundingClientRect();
              return r.width > 0 && r.height > 0 && r.height <= 90;
            });
          cands.slice(0, 4).forEach(function(e){
            var cls = (typeof e.className === 'string') ? e.className : '';
            var items = [].slice.call(e.querySelectorAll('button,a,i,span'))
              .slice(0, 30).map(function(k){
                var kc = (typeof k.className === 'string') ? k.className
                       : ((k.className && k.className.baseVal) || '');
                var kt = (k.getAttribute && (k.getAttribute('title') ||
                       k.getAttribute('aria-label'))) || '';
                return k.tagName + ':' + (kc || kt ||
                       (k.innerText||'').trim().slice(0,10)).slice(0,70);
              });
            out.toolbars.push({cls: cls.slice(0,80), items: items});
          });
          return JSON.stringify(out);
        })()""" % idx)
        try:
            d = json.loads(r or "{}")
        except ValueError:
            print("   解析失败：%s" % (r or "")[:200])
            continue
        print("   编辑器 class: %s" % d.get("edCls"))
        print("   表格相关命名: %d 处" % len(d.get("tableish") or []))
        for x in (d.get("tableish") or [])[:15]:
            print("      %s" % x[:110])
        for t in d.get("toolbars") or []:
            print("   工具栏 [%s]" % t["cls"][:60])
            for it in t["items"][:26]:
                print("      %s" % it)
    if not found_editor:
        print("\n⚠️⚠️ 所有 iframe 里都没有 contenteditable ⇒ 编辑器未渲染，"
              "本轮结论**无效**，不能当「不支持表格」的证据")
    return found_editor


def main():
    # 用户截图是编辑已有稿 aid=342428；先探它，再探空白页做对照
    probe("https://member.bilibili.com/platform/upload/text/new-edit?aid=342428",
          "编辑已有稿（用户截图那一页）")
    probe(bp.BILI_NEW_ARTICLE, "空白 new-edit（对照）")
    return 0


if __name__ == "__main__":
    sys.exit(main())