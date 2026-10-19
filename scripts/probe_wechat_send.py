# -*- coding: utf-8 -*-
"""探针：摸清公众号后台「发表」按钮与弹窗的真实 DOM。

为什么需要这个脚本
------------------
2026-10-19 之前，公众号链路只做到「存草稿」：`wechat_publish.py` 的
`publish_one` 点「保存为草稿」后拿 appmsgid 记账，**「发表」那一步是人工
去后台点的**。要实现自动化，不能凭猜写选择器 —— 本项目在公众号上已经
因为猜选择器栽过三次（`_try_click_login` 的注释里有完整记录）。

所以这个脚本只做三件只读/可撤销的事：
  1. 打开一条已有草稿的编辑页，把顶栏按钮全量 dump 出来；
  2. 点一下「发表」，把弹窗 DOM dump 出来（弹窗不会自动发布）；
  3. 关掉弹窗（Escape / 点取消），**不点确认** —— 什么都发不出去。

用法：
    python -X utf8 scripts/probe_wechat_send.py [--appmsgid 123]

退出码：0 探测完成；1 连不上/没登录；2 没找到草稿。
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wechat_publish as wp                                    # noqa: E402


def dump_buttons(cdp, label):
    """把页面上可见的 button/带 role=button 的元素连同上下文打出来。"""
    js = r"""
    (function(){
      var out = [];
      var els = document.querySelectorAll(
        'button, a[role="button"], div[role="button"], .btn, .btn_primary, ' +
        'span.btn, a.btn, input[type="button"]');
      for (var i = 0; i < els.length; i++) {
        var e = els[i];
        var r = e.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) continue;         // 不可见
        var t = (e.innerText || e.value || e.textContent || '').trim();
        if (!t) continue;
        out.push({
          tag: e.tagName.toLowerCase(),
          id: e.id || '',
          cls: (typeof e.className === 'string' ? e.className : '').slice(0, 80),
          text: t.slice(0, 40),
          x: Math.round(r.x + r.width / 2),
          y: Math.round(r.y + r.height / 2)
        });
      }
      return JSON.stringify(out);
    })()
    """
    raw = cdp.eval(js, refresh_context=True)
    items = json.loads(raw) if isinstance(raw, str) else []
    print("\n== %s：%d 个可见按钮 ==" % (label, len(items)))
    for it in items:
        print("  [%s] id=%-22s cls=%-28s (%d,%d) %r"
              % (it["tag"], it["id"][:22], it["cls"][:28],
                 it["x"], it["y"], it["text"]))
    return items


def dump_dialog(cdp):
    """从**弹窗里的按钮**出发往上找容器，再 dump 它的文本与 HTML。

    ⚠ 别按 class/id 直接抓 `.weui-desktop-dialog`（2026-10-19 实测两次抓错）：
      · 第一次抓到 `display:none` 的上一场残留弹窗；
      · 第二次按 `[id*=dialog]` 抓到可见的**空壳** `#js_vue_head-box-simple_dialog`，
        它包着的 wrp 仍然是隐藏的 —— 壳可见不等于弹窗可见。
    正解：先找「取消 / 确认」这类**只会出现在弹窗里**的可见按钮，从它往上
    走到最近的 dialog 容器。按钮的位置就是铁证。
    """
    js = r"""
    (function(){
      var btns = document.querySelectorAll('button, span, a, div');
      var anchor = null;
      for (var i = 0; i < btns.length; i++) {
        var e = btns[i], r = e.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) continue;
        var t = (e.innerText || e.textContent || '').trim();
        if (t !== '取消' && t !== '确认' && t !== '我知道了') continue;
        anchor = e; break;
      }
      if (!anchor) return null;
      var n = anchor;
      var best = null;
      while (n && n !== document.body) {
        var c = (typeof n.className === 'string' ? n.className : '');
        if (/dialog|weui-desktop-modal/i.test(c) || /dialog/i.test(n.id || '')) {
          best = n;                       // 一路往上记，取**最外层** dialog 容器
        }
        n = n.parentElement;
      }
      var dlg = best || anchor.parentElement;
      var qr = dlg.querySelector('img') !== null;
      var qrcode = dlg.querySelector('[class*=qrcode], [class*=qr_code], canvas') !== null;
      var radio = dlg.querySelector(
        'input[type=radio], input[type=checkbox], [class*=radio], [class*=check]'
      ) !== null;
      var rows = [];
      var labs = dlg.querySelectorAll(
        '.weui-desktop-form__label, .mass-send__td, label');
      for (var k = 0; k < labs.length; k++) {
        var l = labs[k];
        if (l.offsetParent === null) continue;
        var row = l.closest('.weui-desktop-form__control-group, .mass-send__td')
                  || l.parentElement;
        var sw = row.querySelector(
          '[class*=switch], [class*=toggle], input[type=checkbox]');
        var swTxt = sw ? ((sw.innerText || '').trim()
                          || (sw.getAttribute('aria-label') || '')) : '';
        rows.push({
          label: (l.innerText || '').trim().slice(0, 30),
          row: (row.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 80),
          switch: swTxt.slice(0, 20),
          switchCls: sw ? String(sw.className).slice(0, 60) : ''
        });
      }
      return JSON.stringify({
        text: (dlg.innerText || '').trim(),
        html: dlg.outerHTML,
        qr: qr, qrcode: qrcode, radio: radio, rows: rows
      });
    })()
    """
    raw = cdp.eval(js, refresh_context=True)
    if not raw:
        print("\n== 弹窗：没找到（页面上没有 取消/确认/我知道了 按钮） ==")
        return None
    d = json.loads(raw) if isinstance(raw, str) else {}
    print("\n== 弹窗全文 ==\n%s" % (d.get("text") or "(空)"))
    print("\n== 弹窗要素：有图=%s 疑似二维码/画布=%s 单选复选=%s =="
          % ("有" if d.get("qr") else "无",
             "有" if d.get("qrcode") else "无",
             "有" if d.get("radio") else "无"))
    print("\n== 表单行 ==")
    for r in d.get("rows") or []:
        print("  label=%-14s switch=%-12s cls=%-40s | row=%s"
              % (r["label"], r["switch"], r["switchCls"][:40], r["row"][:70]))
    html = d.get("html") or ""
    with open(os.path.join(wp.ROOT, "tmp", "wechat_send_dialog.html"),
              "w", encoding="utf-8") as f:
        f.write(html)
    print("\n（完整弹窗 HTML 已存 tmp/wechat_send_dialog.html，%d 字）" % len(html))
    return d


def switch_states(cdp):
    """读弹窗里三个开关（群发通知/分组通知/定时发表）的实时勾选状态。"""
    js = r"""
    (function(){
      var out = [];
      var boxes = document.querySelectorAll('.new_mass_send_dialog input[type=checkbox]');
      for (var i = 0; i < boxes.length; i++) {
        var b = boxes[i];
        var row = b.closest('.weui-desktop-form__control-group') || b.parentElement;
        var lab = row.querySelector('.weui-desktop-form__label');
        out.push({
          label: lab ? (lab.innerText || '').trim().replace(/\s+/g, ' ') : '(?)',
          checked: b.checked,
          disabled: b.disabled
        });
      }
      return JSON.stringify(out);
    })()
    """
    raw = cdp.eval(js, refresh_context=True)
    return json.loads(raw) if isinstance(raw, str) else []


def toggle_switch(cdp, label_contains):
    """点一下含指定文本那一行的开关。返回点击前后状态。"""
    js = r"""
    (function(label){
      var boxes = document.querySelectorAll('.new_mass_send_dialog input[type=checkbox]');
      for (var i = 0; i < boxes.length; i++) {
        var b = boxes[i];
        var row = b.closest('.weui-desktop-form__control-group') || b.parentElement;
        var lab = row.querySelector('.weui-desktop-form__label');
        var t = lab ? (lab.innerText || '') : '';
        if (t.indexOf(label) >= 0) {
          var before = b.checked;
          var box = row.querySelector('.weui-desktop-switch__box') || b;
          box.click();
          return JSON.stringify({before: before, label: t.trim()});
        }
      }
      return null;
    })(%s)
    """ % json.dumps(label_contains)
    raw = cdp.eval(js, refresh_context=True)
    return json.loads(raw) if isinstance(raw, str) else None


def xhr_trace(cdp):
    """给页面装 XHR 探针：记录 open() 的 method+url，并**拦掉 send()**。

    为什么这么干：点「发表」那一刻到底打哪个接口、带什么参数，是自动化
    要回答的核心问题。但真发出去就是一次真实群发/发布，不能拿线上内容试。
    拦住 send() 让请求根本离开浏览器 —— 页面该走的逻辑（弹二维码、报错）
    照样会发生，只是没有副作用。
    """
    js = r"""
    (function(){
      if (window.__xhrTrace) return 'already';
      window.__xhrTrace = [];
      var oo = XMLHttpRequest.prototype.open;
      XMLHttpRequest.prototype.open = function(m, u) {
        window.__xhrTrace.push({m: m, u: String(u)});
        return oo.apply(this, arguments);
      };
      var ss = XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.send = function() {
        window.__xhrTrace.push({m: 'SEND_BLOCKED', u: '(不发)'});
        return;                       // 拦掉：请求不离开浏览器
      };
      return 'installed';
    })()
    """
    return cdp.eval(js, refresh_context=True)


def xhr_report(cdp):
    js = "(function(){return JSON.stringify(window.__xhrTrace || []);})()"
    raw = cdp.eval(js, refresh_context=True)
    items = json.loads(raw) if isinstance(raw, str) else []
    print("\n== XHR 探针抓到 %d 条 ==" % len(items))
    for it in items:
        u = it["u"]
        if len(u) > 160:
            u = u[:160] + "…"
        print("  %-14s %s" % (it["m"], u))
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--appmsgid", default=None,
                    help="拿哪条草稿做实验；不指定用草稿箱第一条")
    ap.add_argument("--port", type=int, default=wp.CDP_PORT)
    ap.add_argument("--toggle", default=None,
                    help="翻一下含该文本的开关再读状态（如 群发通知）。不点确认，发不出去")
    ap.add_argument("--xhr", action="store_true",
                    help="拦住 send() 只记录 URL，然后点一次弹窗里的「发表」"
                         "（请求不会离开浏览器，什么都不会真发）")
    ap.add_argument("--no-notify", action="store_true",
                    help="先把「群发通知」开关关掉再走流程 = 发布模式（不推送、"
                         "不占每日额度、不需要管理员扫码）")
    args = ap.parse_args()

    cdp = wp.CDP(args.port)
    tid0, tok = wp._connect_mp(cdp)
    if not tok:
        print("✗ 拿不到 token：确认调试窗口里 mp.weixin.qq.com 已登录")
        return 1
    drafts = wp._draft_list(cdp, tok, count=20)
    if not drafts:
        print("✗ 草稿箱是空的（type=77 列表没数据）")
        return 2
    aid = args.appmsgid or str(drafts[0].get("appmsgid"))
    title = next((d.get("title", "") for d in drafts
                  if str(d.get("appmsgid")) == aid), "")
    print("实验草稿：appmsgid=%s title=%r" % (aid, title))

    tid = wp._open_draft_editor(cdp, aid, tok, strict=True)
    try:
        dump_buttons(cdp, "编辑页顶栏（发表前）")

        # 点「发表」—— 只会打开弹窗，不会发布
        r = wp._click_by_text(cdp, "发表")
        print("\n点「发表」→ %s" % r)
        time.sleep(2)
        dump_dialog(cdp)
        dump_buttons(cdp, "弹窗里的按钮")

        print("\n== 开关实时状态 ==")
        for s in switch_states(cdp):
            print("  %-12s checked=%s disabled=%s"
                  % (s["label"][:12], s["checked"], s["disabled"]))

        if args.toggle:
            print("\n== 翻一下「%s」开关 ==" % args.toggle)
            r = toggle_switch(cdp, args.toggle)
            print("  点击结果：%s" % r)
            time.sleep(1)
            print("\n== 翻完之后弹窗全文 ==")
            d2 = dump_dialog(cdp)
            print("\n== 翻完之后开关状态 ==")
            for s in switch_states(cdp):
                print("  %-12s checked=%s disabled=%s"
                      % (s["label"][:12], s["checked"], s["disabled"]))
            # 翻回来，保持原状
            if r:
                toggle_switch(cdp, args.toggle)
                time.sleep(0.5)
                print("\n（已翻回原状）")

        if args.no_notify:
            print("\n== 关掉「群发通知」开关（发布模式） ==")
            r = toggle_switch(cdp, "群发通知")
            print("  结果：%s" % r)
            time.sleep(1)
            for s in switch_states(cdp):
                print("  %-12s checked=%s" % (s["label"][:12], s["checked"]))
            dump_dialog(cdp)

        if args.xhr:
            print("\n== 装 XHR 探针：%s ==" % xhr_trace(cdp))
            # 点弹窗里的主按钮「发表」（对话框 footer 那个，不是顶栏的）
            r = wp._click_in_dialog(cdp, "发表")
            print("点弹窗「发表」→ %s（send 已被拦，不会真发）" % r)
            time.sleep(3)
            dump_dialog(cdp)
            # 第二层确认：「继续发表」。点完看是出二维码还是直接发。
            r = wp._click_in_dialog(cdp, "继续发表")
            print("\n点弹窗「继续发表」→ %s" % r)
            time.sleep(3)
            xhr_report(cdp)
            dump_dialog(cdp)
            dump_buttons(cdp, "最终态按钮")
            # 收尾：撤弹窗
            cdp.eval("document.dispatchEvent(new KeyboardEvent('keydown',"
                     "{key:'Escape', keyCode:27, bubbles:true}))",
                     refresh_context=True)
            time.sleep(1)
            for txt in ("取消", "关闭"):
                r2 = wp._click_by_text(cdp, txt)
                if isinstance(r2, str) and r2.startswith("CLICKED"):
                    break
            return 0

        # 撤：Escape + 找「取消」。绝不点「确认」。
        cdp.eval("document.dispatchEvent(new KeyboardEvent('keydown',"
                 "{key:'Escape', keyCode:27, bubbles:true}))",
                 refresh_context=True)
        time.sleep(1)
        still = cdp.eval(r"""
          (function(){
            var dlg = document.querySelector(
              '.dialog, .weui-desktop-dialog, [class*="dialog"], [id*="dialog"]');
            return dlg ? (dlg.offsetParent !== null) : false;
          })()
        """, refresh_context=True)
        print("\nEscape 后弹窗还在吗：%s" % bool(still))
        if still:
            for txt in ("取消", "关闭", "不了"):
                r2 = wp._click_by_text(cdp, txt)
                if isinstance(r2, str) and r2.startswith("CLICKED"):
                    print("点「%s」→ %s（弹窗已撤）" % (txt, r2))
                    break
        return 0
    finally:
        try:
            cdp.close_target(tid)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
