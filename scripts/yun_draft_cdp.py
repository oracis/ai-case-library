#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""两朵云草稿创建（走 9228 CDP，**不依赖 CSDN 同步助手扩展**）。

为什么有这个脚本（2026-10-30）：
  建稿原先只走 `sync_longtail.py` → CSDN 同步助手 MCP（ws://127.0.0.1:3721）。
  实测两个硬伤：
    ① 扩展 WS 会瞬时断开 ⇒ 整篇同步静默失败（3ak-track-field 就这么丢过）；
    ② 扩展里的阿里云登录态是**易失**的，会自己掉（要重新扫码），
       而同一时刻腾讯云/掘金/头条/B站都在线 ⇒ 平台特异性掉线。
  ⇒ 治本：建稿改走**常驻 CDP Chrome**（`chrome-yun-profile`，端口 9228）。
  该 profile 的登录态是**磁盘持久**的（阿里云/腾讯云账号 2026-10-30 实测
  都在线），且 `yun_finalize.py` 的收尾/发表早已验证这条通道可用。
  ⇒ 本脚本把「建草稿」也搬到 9228，扩展只留作历史路径。

各平台建稿机制（2026-10-30 实测）：
  阿里云  /article/new —— `#title` + 正文（见下）+ 点「存为草稿」
          → 平台 POST articleDraft/* 返回新 aid
  腾讯云  /article/write-new —— `.cdc-article-editor__title` + `#editor-container`
          → 点「存草稿」→ 平台 POST 草稿接口返回新 draftId

⚠⚠ 两个把本脚本坑了一整轮的 CDP 事实（照抄前先读）：
  ① **IIFE 必须以 `)()` 结尾**。写成 `(function(){...})` 时 V8 把它当**函数
     表达式**求值，返回函数对象本身（type=function, value={}），函数体**压根
     没执行**；而 `_CDP.eval` 只取 `result.value`，于是拿到 falsy 的 {}，
     表现为「元素明明存在却找不到」。所有 eval 的 JS 一律 `)()` 收尾。
  ② **阿里云正文的真身不是 `#article-editor`**。那是 `display:none` 的
     `mditor-hidden` **镜像**（focus() 失败、activeElement 停在 BODY）；
     真身是 `.mditor` 内的 `textarea.textarea`（split 模式的输入层）。
     往真身注入并派发 input 后，**镜像会自动同步**，应用层即拿到 markdown。

用法：
  python scripts/yun_draft_cdp.py --case plausible
  python scripts/yun_draft_cdp.py --case plausible --platforms aliyun
  python scripts/yun_draft_cdp.py --file drafts/wechat/x.md --case x
  python scripts/yun_draft_cdp.py --case plausible --dry      # 只建不记账

红线：本脚本**只建草稿，不发表**。发表仍走 yun_finalize 的 --publish 双门禁。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import chrome_cdp_launch as L                     # noqa: E402
from juejin_publish import _CDP                   # noqa: E402  stdlib CDP 客户端
from csdn_publish import md_to_html, split_md        # noqa: E402

PORT = 9228
PROFILE = os.path.expandvars(r"%USERPROFILE%\chrome-yun-profile")
LEDGER = os.path.join(ROOT, "data", "longtail_sync.json")

ALIYUN_NEW = "https://developer.aliyun.com/article/new"
TENCENT_NEW = "https://cloud.tencent.com/developer/article/write-new"

# 页面内埋点：把 fetch/XHR 的**请求体与响应体**都记下来，
# 这样建稿走的是哪个接口、返回了什么新 id，一次看清（不用猜）。
JS_HOOK = """
(function(){
  if (window.__calls) return 'already';
  window.__calls = [];
  function rec(o) { try { window.__calls.push(o); } catch (e) {} }
  var of = window.fetch;
  window.fetch = function(u, o) {
    var m = (o && o.method) || 'GET';
    var b = (o && o.body) ? String(o.body).slice(0, 1200) : null;
    var p = of.apply(this, arguments);
    if (m !== 'GET') {
      p.then(function(res) {
        return res.clone().text().then(function(t) {
          rec({k: 'fetch', url: String(u), method: m, body: b, resp: t.slice(0, 800)});
        });
      }).catch(function() {});
    }
    return p;
  };
  var oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(m, u) {
    this.__m = m; this.__u = u; return oo.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function(b) {
    var m = this.__m, u = this.__u;
    var body = b ? String(b).slice(0, 1200) : null;
    this.addEventListener('load', function() {
      var t = '';
      try { t = String(this.responseText || '').slice(0, 800); } catch (e) {}
      rec({k: 'xhr', url: String(u), method: m, body: body, resp: t});
    });
    return os.apply(this, arguments);
  };
  return 'hooked';
})()
"""

JS_CALLS = "JSON.stringify(window.__calls || [])"


def _connect():
    """9228 已起就直连；否则起带登录态 profile（默认无头）。返回 (proc|None, cdp)。"""
    import urllib.request
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        op.open("http://127.0.0.1:%d/json/version" % PORT, timeout=3).read()
        return None, _CDP(PORT)
    except Exception:
        pass
    headed = str(os.environ.get("CHROME_HEADED", "")).lower() in ("1", "true", "yes")
    L.clear_proxy()
    L.remove_lock(PROFILE)
    chrome, _ = L.pick_chrome()
    if not chrome:
        raise SystemExit("找不到 Chrome")
    extra = () if headed else ("--window-size=1440,900",)
    proc = subprocess.Popen(
        L.build_args(chrome, PROFILE, port=PORT, headless=not headed, extra=extra),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL)
    if not L.wait_cdp(proc, timeout=45, port=PORT):
        raise SystemExit("Chrome CDP 没起来")
    return proc, _CDP(PORT)


def _click_by_text(cdp, text, tags="button,[role=button]"):
    """按按钮文案真实鼠标点击。返回 True 表示点到了。

    ⚠ 阿里云底部固定栏 `.publish-fixed` 是 position:fixed（offsetParent=null），
      且它整条文本链（publish-fixed → wrapper → box → box-btn）都含
      「草稿箱存为草稿发布文章」这串文字 ⇒ 按「全文档找文字命中」会被祖先
      节点干扰。按标签 + 全等文案定位才稳。
    ⚠ tags 可放宽到含 div/span（腾讯云的「切换 MD 编辑器」就是 DIV），
      此时用**面积最小**的命中者，避免命中外层容器。
    ⚠ IIFE 必须 `)()` 收尾，否则函数体不执行（见模块 docstring ①）。
    """
    js = """
(function(){
  var want = %s;
  var bs = document.querySelectorAll(%s);
  var best = null;
  for (var i = 0; i < bs.length; i++) {
    var b = bs[i];
    if ((b.textContent || '').trim() !== want) continue;
    if (b.offsetParent === null) continue;
    var r = b.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    var area = r.width * r.height;
    if (!best || area < best.area) {
      best = {b: b, area: area, x: r.x + r.width / 2, y: r.y + r.height / 2};
    }
  }
  if (!best) return '';
  var hit = document.elementFromPoint(best.x, best.y);
  return JSON.stringify({
    x: best.x, y: best.y,
    covered: !(hit && (best.b === hit || best.b.contains(hit))),
    hit: hit ? (hit.tagName + '.' + (hit.className || '').toString().slice(0, 40))
             : 'null'
  });
})()""" % (json.dumps(text), json.dumps(tags))
    pos = cdp.eval(js)
    if not pos:
        print("    ⚠ 没找到「%s」按钮" % text)
        return False
    pos = json.loads(pos)
    if pos.get("covered"):
        # 被遮挡 ⇒ 真实鼠标会打在遮挡物上，改用原生 click 兜底
        print("    ⚠ 按钮被「%s」遮挡，改用原生 click()" % pos.get("hit"))
        cdp.eval("""
(function(){
  var bs = document.querySelectorAll('button,[role=button],div,span,a');
  var want = %s, best = null;
  for (var i = 0; i < bs.length; i++) {
    var b = bs[i];
    if ((b.textContent || '').trim() !== want) continue;
    if (b.offsetParent === null) continue;
    var r = b.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    var a = r.width * r.height;
    if (!best || a < best.a) best = {b: b, a: a};
  }
  if (!best) return 'not found';
  best.b.click();
  return 'clicked';
})()""" % json.dumps(text))
        time.sleep(1)
        return True
    for tp, buttons in (("mousePressed", 1), ("mouseReleased", 0)):
        cdp.send("Input.dispatchMouseEvent",
                 {"type": tp, "x": pos["x"], "y": pos["y"], "button": "left",
                  "clickCount": 1, "buttons": buttons})
        time.sleep(0.08)
    return True


def _wait_for(cdp, expr, timeout=40):
    """轮询等页面条件成立（返回 True/False）。

    ⚠ 预埋点在 reload 后**立刻**生效（脚本一执行就装上了），但那时阿里云
      的 SPA 还没渲染完 ⇒ 直接注入会得到「not found」。必须等编辑器就位。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if cdp.eval(expr):
                return True
        except Exception:                                       # noqa: BLE001
            pass
        time.sleep(1.5)
    return False


def _read_calls(cdp, only=None):
    """回读页面埋点里的出站调用。only 为 URL 关键词过滤。"""
    raw = cdp.eval(JS_CALLS)
    try:
        arr = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception:
        return []
    if only:
        arr = [c for c in arr if only in (c.get("url") or "")]
    return arr


# 日志/埋点上报噪声：跟建稿无关，捞aid 时要滤掉
NOISE = ("log-global", "logstores", "xconsole", "arms-retcode", "cn-shanghai.log",
         "aliyun.com/r.png", "tracker", "g.alicdn", "alicdn.com", "beacon")


def _is_noise(url):
    u = (url or "").lower()
    return any(n in u for n in NOISE)


def _arm_hook(cdp):
    """**在任何页面脚本执行前**埋点（2026-10-30 关键修正）。

    ⚠ 事后替换 `window.fetch` 拦不到调用 —— 页面脚本在模块初始化时就把
      fetch 捕获进自己作用域了，事后改 window.fetch 对它无效（实测：
      点「存为草稿」后只捞到一堆日志上报，建稿接口一个没抓到）。
      ⇒ 用 Page.addScriptToEvaluateOnNewDocument 在脚本执行前埋，
        再 Page.reload 让埋点生效。
      ⚠ Page.reload 之前必须先 Page.enable，否则 reload 是空操作
        （实测：不 enable 时埋点未生效，因为页面压根没重载过）。
    """
    try:
        cdp.send("Page.enable")
        r1 = cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": JS_HOOK})
        print("    埋点注册: %s" % json.dumps(r1.get("result", {}), ensure_ascii=False)[:80])
        cdp.send("Page.reload", {"ignoreCache": False})
    except Exception as e:                                      # noqa: BLE001
        print("    ⚠ 预埋点失败(%s)，退回事后埋点" % str(e)[:60])
        cdp.eval(JS_HOOK)
        return
    ok = False
    for _ in range(12):
        time.sleep(1.5)
        try:
            if cdp.eval("typeof window.__calls !== 'undefined'") is True:
                ok = True
                break
        except Exception:                                       # noqa: BLE001
            continue
    print("    预埋点: %s" % ("生效" if ok else "未生效"))


def _read_toast(cdp):
    """读页面上的 toast / 校验提示（保存失败时平台会弹提示）。"""
    return cdp.eval("""
(function(){
  var out = [];
  var sels = ['.next-message','.next-notice','.next-notification','.next-toast',
              '[class*=message-notice]','.publish-message','[role=alert]',
              '.ant-message','.ant-notification'];
  for (var i = 0; i < sels.length; i++) {
    var ns = document.querySelectorAll(sels[i]);
    for (var j = 0; j < ns.length && j < 4; j++) {
      var e = ns[j];
      var t = (e.innerText || e.textContent || '').trim();
      if (t) out.push(t.slice(0, 60));
    }
  }
  return JSON.stringify(out.slice(0, 6));
})()""")


def _extract_id(blob, loose=False):
    """从请求/响应文本里抠出草稿 id。

    ⚠ 2026-10-30 踩过：宽松匹配 `"id":(\\d+)` 会把无关接口（如
      getTaskGroup 的 {"id":748}）当成草稿 id ⇒ **假成功**。
      默认只认 aid/draftId 这类**草稿语义**字段；loose=True 才放开 id。
    """
    for pat in (r'"aid"\s*:\s*(\d+)', r'"draftId"\s*:\s*"?(\d+)',
                r'\baid=(\d+)', r'\bdraftId=(\d+)'):
        m = re.search(pat, blob or "")
        if m:
            return m.group(1)
    if loose:
        m = re.search(r'"id"\s*:\s*(\d+)', blob or "")
        if m:
            return m.group(1)
    return ""


def _load_ledger():
    if not os.path.exists(LEDGER):
        return {}
    with open(LEDGER, encoding="utf-8") as f:
        return json.load(f) or {}


def _new_key(case):
    """<case>-<date>-<序号>，与 sync_longtail 同构，台账可互相读。"""
    ledger = _load_ledger()
    day = time.strftime("%Y-%m-%d")
    n = 1
    while "%s-%s-%d" % (case, day, n) in ledger:
        n += 1
    return "%s-%s-%d" % (case, day, n)


def _remember(key, entry):
    """写台账：保原文件格式 + 原子写。

    ⚠ 2026-10-30 MEMORY 铁律「写 data/*.json 保格式」：longtail_sync.json
    实测是 indent=1 + CRLF + 末尾换行。用默认 indent=2 写会制造整文件格式
    噪音（2026-10-20 已在别处踩过，3156 行全红）。行尾用 detect_newline 跟
    随原文件，避免一次写入把整个文件的行尾换掉。
    """
    from atomicio import detect_newline, write_text     # noqa: PLC0415
    ledger = _load_ledger()
    ledger[key] = entry
    nl = detect_newline(LEDGER, default="\r\n")
    text = json.dumps(ledger, ensure_ascii=False, indent=1) + "\n"
    write_text(LEDGER, text, newline=nl)


def _fill_text(cdp, selector, value):
    """往 input/textarea 填值（React 受控 ⇒ 必须用原生 setter + 事件）。"""
    return cdp.eval("""
(function(){
  var el = document.querySelector(%s);
  if (!el) return 'not found';
  var proto = el.tagName === 'TEXTAREA'
    ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
  var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
  setter.call(el, %s);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return 'ok len=' + (el.value || '').length;
})()""" % (json.dumps(selector), json.dumps(value)))


# --------------------------------------------------------------------------
# 阿里云
# --------------------------------------------------------------------------
def create_aliyun(cdp, title, body_md, abstract=""):
    """阿里云建草稿。返回 (aid, 失败原因)。"""
    cdp.open(ALIYUN_NEW)
    time.sleep(3)
    _arm_hook(cdp)                      # 预埋点（含一次 reload）
    if not _wait_for(cdp, "!!document.querySelector('#title')"):
        return "", "等不到阿里云编辑器（#title 不出现）"
    import yun_finalize as F                             # noqa: PLC0415
    F._close_disclaimer(cdp)
    time.sleep(1)

    r = _fill_text(cdp, "#title", title)
    print("    标题注入: %s" % r)
    if r == "not found":
        return "", "没有 #title"

    # 正文：真身是 .mditor 内的 textarea.textarea（#article-editor 只是镜像）
    r = cdp.eval("""
(function(){
  var real = document.querySelector('.mditor textarea.textarea');
  if (!real) return 'no real textarea';
  var setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype, 'value').set;
  setter.call(real, %s);
  real.dispatchEvent(new Event('input', {bubbles: true}));
  return 'ok len=' + real.value.length;
})()""" % json.dumps(body_md))
    print("    正文注入（.mditor 真身）: %s" % r)
    if r == "no real textarea":
        return "", "找不到 .mditor textarea.textarea"
    time.sleep(2)
    # 镜像是否同步（应用层拿到 markdown 的证据）
    mirror = cdp.eval("""
(function(){
  var m = document.querySelector('#article-editor');
  var real = document.querySelector('.mditor textarea.textarea');
  return JSON.stringify({realLen: real ? real.value.length : -1,
                         mirrorLen: m ? m.value.length : -1});
})()""")
    print("    镜像回读: %s" % mirror)
    if abstract:
        print("    摘要注入: %s" % _fill_text(cdp, "#abstractContent", abstract))

    if not _click_by_text(cdp, "存为草稿"):
        return "", "没找到「存为草稿」按钮"
    print("    已点「存为草稿」，等平台落库…", flush=True)
    time.sleep(9)
    print("    URL: %s" % cdp.eval("location.href"))

    aid = _harvest_id(cdp)
    if aid:
        return aid, ""
    print("    未抓到建稿接口 ⇒ 走草稿箱回读兜底")
    print("    验证码态: %s" % str(_captcha_present(cdp))[:200])
    print("    toast: %s" % str(_read_toast(cdp))[:200])
    aid = _aliyun_find_draft(cdp, title)
    return aid, ("没抓到建稿接口且草稿箱未匹配到" if not aid else "")


def _harvest_id(cdp):
    """从埋点里的写操作中捞新草稿 id。

    ⚠ 只看应用自己的 `/developer/api/` 调用 —— 页面还会并发打一堆阿里云
      控制台/日志/验证码接口，全打出来会淹掉真正的建稿调用。
    ⚠ 只认 URL 含 draft/article/save 的调用：message/task/captcha 这类
      接口的返回里也有 id，取错就是**假成功**（2026-10-30 踩过 getTaskGroup
      的 {"id":748}）。
    """
    cands = []
    for c in _read_calls(cdp):
        url = c.get("url") or ""
        if _is_noise(url) or "/developer/api/" not in url:
            continue
        low = url.lower()
        if not any(k in low for k in ("draft", "article", "save", "publish")):
            continue
        # ⚠ 列表接口的返回里也有 draftId（旧草稿的）⇒ 不能当建稿证据
        if "getuserarticledrafts" in low or "draftlist" in low:
            continue
        cands.append(c)
    aid = ""
    for c in cands:
        print("    → %s %s" % (c.get("method"), (c.get("url") or "")[:88]))
        if c.get("body"):
            print("      body: %s" % (c.get("body") or "")[:220])
        if c.get("resp"):
            print("      resp: %s" % (c.get("resp") or "")[:320])
        got = _extract_id((c.get("resp") or "") + " " + (c.get("body") or ""))
        if got:
            aid = got
    return aid


def _captcha_present(cdp):
    """检测阿里云风控验证码是否弹了（滑块/拼图）。

    2026-10-30 实测：9228 无头跑时，存草稿会触发
    `POST /developer/api/captcha2/encryptedSceneId` + `InitCaptchaV3`
    （CaptchaType=PUZZLE）⇒ 保存被风控拦下。日志里明写 BROWSER=HeadlessChrome
    ⇒ 无头指纹是首要触发点，有头模式可绕。
    """
    r = cdp.eval("""
(function(){
  var out = [];
  var ifr = document.querySelectorAll('iframe');
  for (var i = 0; i < ifr.length; i++) {
    var s = ifr[i].src || '';
    if (s.indexOf('captcha') >= 0) out.push('iframe:' + s.slice(0, 90));
  }
  var sels = ['[class*=captcha]','.aliyun-captcha','#captcha',
              '[id*=captcha]','[class*=verify]'];
  for (var k = 0; k < sels.length; k++) {
    var ns = document.querySelectorAll(sels[k]);
    for (var j = 0; j < ns.length && j < 3; j++) {
      var e = ns[j];
      if (e.offsetParent === null) continue;
      out.push(sels[k] + ':' + (e.className || '').toString().slice(0, 40));
    }
  }
  return JSON.stringify(out.slice(0, 6));
})()""")
    return r


def _aliyun_find_draft(cdp, title):
    """草稿箱里按标题找 aid（兜底路径）。

    先把「草稿箱」入口的真实形态 dump 出来 —— 实测它**不是 <a>**（href 查询
    为空），多半是 JS 跳转的 div/span ⇒ 取其 href 或直接点它看跳转。
    """
    info = cdp.eval("""
(function(){
  var all = document.querySelectorAll('*');
  for (var i = 0; i < all.length; i++) {
    var e = all[i];
    if (e.children.length) continue;
    if ((e.textContent || '').trim() !== '草稿箱') continue;
    var r = e.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    return JSON.stringify({tag: e.tagName, html: e.outerHTML.slice(0, 260),
      parent: e.parentElement ? e.parentElement.outerHTML.slice(0, 320) : null});
  }
  return 'not found';
})()""")
    print("    草稿箱入口: %s" % str(info)[:340])
    href = ""
    if isinstance(info, str) and '"html"' in info:
        try:
            d = json.loads(info)
            blob = "%s %s" % (d.get("html") or "", d.get("parent") or "")
            m = re.search(r'href="([^"]+)"', blob)
            if m:
                href = m.group(1)
        except Exception:                                       # noqa: BLE001
            pass
    if href:
        url = href if href.startswith("http") else "https://developer.aliyun.com" + href
        print("    打开草稿箱: %s" % url)
        cdp.open(url)
        time.sleep(8)
        for c in _read_calls(cdp):
            url2 = c.get("url") or ""
            if _is_noise(url2) or "draft" not in url2.lower():
                continue
            print("    → 列表接口 %s" % url2[:90])
            resp = c.get("resp") or ""
            if title in resp:
                aid = _extract_id(resp)
                if aid:
                    return aid
    for ep in ("/developer/api/articleDraft/draftList?pageSize=20&pageNum=1",
               "/developer/api/article/draft/list?pageSize=20&pageNum=1",
               "/developer/api/articleDraft/list?pageSize=20&pageNum=1"):
        r = cdp.eval("""
fetch(%s, {credentials: "include"})
  .then(function(r){ return r.text().then(function(t){ return r.status + "|" + t; }); })
  .catch(function(e){ return "ERR|" + e; })""" % json.dumps(ep))
        print("    草稿箱回读 %s → %s" % (ep.split("?")[0], str(r)[:200]))
        if isinstance(r, str) and title in r:
            aid = _extract_id(r)
            if aid:
                return aid
    return ""


# --------------------------------------------------------------------------
# 腾讯云
# --------------------------------------------------------------------------
def create_tencent(cdp, title, body_md, abstract=""):
    """腾讯云建草稿。返回 (draftId, 失败原因)。"""
    cdp.open(TENCENT_NEW)
    time.sleep(3)
    _arm_hook(cdp)                      # 预埋点（含一次 reload）
    if not _wait_for(cdp, "!!document.querySelector('#cdc-rich-editor')"):
        return "", "等不到腾讯云编辑器（#cdc-rich-editor 不出现）"

    # 标题：真身是 textarea.cdc-article-editor__title-input（不是 input）
    r = _fill_text(cdp, "textarea.cdc-article-editor__title-input", title)
    print("    标题注入: %s" % r)
    if r == "not found":
        return "", "找不到标题输入框"

    # 正文：**优先切 MD 编辑器**（平台自己把 markdown 转富文本，与扩展
    # 路线一致，最保真）；切不了才退回 ProseMirror 灌 HTML。
    md_ok = False
    if _click_by_text(cdp, "切换 MD 编辑器",
                      tags="button,[role=button],div,span,a"):
        time.sleep(4)
        r = cdp.eval("""
(function(){
  var ts = document.querySelectorAll('textarea');
  for (var i = 0; i < ts.length; i++) {
    if (ts[i].offsetParent === null) continue;
    if ((ts[i].className || '').indexOf('title-input') >= 0) continue;
    var setter = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype, 'value').set;
    setter.call(ts[i], %s);
    ts[i].dispatchEvent(new Event('input', {bubbles: true}));
    ts[i].dispatchEvent(new Event('change', {bubbles: true}));
    return 'md textarea len=' + ts[i].value.length;
  }
  return 'no md textarea';
})()""" % json.dumps(body_md))
        print("    正文注入（MD 模式）: %s" % r)
        md_ok = "len=" in str(r)

    if not md_ok:
        # 兜底：TipTap/ProseMirror（#cdc-rich-editor, contenteditable）
        # ⇒ 不用 innerHTML（ProseMirror 状态不同步），走 execCommand('insertHTML')
        #    才会产生它认的 beforeinput/input 事件。
        html = md_to_html(body_md)
        r = cdp.eval("""
(function(){
  var ed = document.querySelector('#cdc-rich-editor');
  if (!ed) return 'no editor';
  ed.focus();
  var sel = window.getSelection();
  var range = document.createRange();
  range.selectNodeContents(ed);
  sel.removeAllRanges();
  sel.addRange(range);
  var ok = document.execCommand('insertHTML', false, %s);
  return (ok ? 'insertHTML ok' : 'execCommand false') +
         ' len=' + (ed.innerHTML || '').length;
})()""" % json.dumps(html))
        print("    正文注入（ProseMirror）: %s" % r)
    time.sleep(2)
    print("    编辑器回读: %s" % cdp.eval("""
(function(){
  var ed = document.querySelector('#cdc-rich-editor');
  var ti = document.querySelector('textarea.cdc-article-editor__title-input');
  return JSON.stringify({edLen: ed ? ed.innerHTML.length : -1,
                         edText: ed ? (ed.innerText || '').slice(0, 40) : '',
                         titleLen: ti ? ti.value.length : -1,
                         title: ti ? ti.value.slice(0, 40) : ''});
})()"""))

    if not _click_by_text(cdp, "存草稿"):
        return "", "没找到「存草稿」按钮"
    print("    已点「存草稿」，等平台落库…", flush=True)
    time.sleep(10)
    print("    URL: %s" % cdp.eval("location.href"))

    did = _harvest_id(cdp)
    if not did:
        print("    未抓到建稿接口 ⇒ 走草稿箱回读兜底")
        did = _tencent_find_draft(cdp, title)
    return did, ("" if did else "未抓到建稿接口，草稿箱也没匹配到")


def _tencent_find_draft(cdp, title):
    """腾讯云草稿箱按标题找 draftId（兜底路径）。

    列表接口已知：`POST /developer/api/article/getUserArticleDrafts`
    body {page, pageSize, contentType:"rich"}。
    ⚠ 这个接口的返回里**也有 draftId**（旧草稿的），所以绝不能拿它当
      「建稿成功」的证据 —— 必须按标题匹配到我们刚建的那条。
    """
    r = cdp.eval("""
fetch("/developer/api/article/getUserArticleDrafts",
      {method: "POST", credentials: "include",
       headers: {"Content-Type": "application/json"},
       body: JSON.stringify({page: 1, pageSize: 30, contentType: "rich"})})
  .then(function(r){ return r.text(); })
  .catch(function(e){ return "ERR|" + e; })""")
    print("    草稿箱回读: %s" % str(r)[:160])
    if not isinstance(r, str):
        return ""
    try:
        data = json.loads(r)
    except Exception:                                           # noqa: BLE001
        return ""
    items = data.get("list") or data.get("data") or []
    if isinstance(items, dict):
        items = items.get("list") or []
    for it in items:
        if not isinstance(it, dict):
            continue
        t = it.get("title") or ""
        if t.strip() == title.strip():
            did = it.get("draftId") or it.get("articleId")
            print("    匹配到草稿: title=%s draftId=%s" % (t[:30], did))
            return str(did) if did else ""
    return ""


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="两朵云草稿创建（9228 CDP，不依赖 CSDN 扩展）")
    ap.add_argument("--case", help="案例 id（对应 drafts/wechat/<case>.md）")
    ap.add_argument("--file", help="或直接指定 markdown 文件")
    ap.add_argument("--platforms", nargs="+", default=["aliyun", "tencentcloud"])
    ap.add_argument("--abstract", default="", help="阿里云摘要（缺省取正文首段）")
    ap.add_argument("--cover", default="",
                    help="封面外链（http(s)）。给则在正文首行插入该图 —— "
                         "与 MCP 路线的「正文首图」口径一致（腾讯云适配器会"
                         "丢弃 cover 参数，封面只能走正文图）")
    ap.add_argument("--dry", action="store_true", help="只建不记账")
    args = ap.parse_args()

    src = args.file or os.path.join(ROOT, "drafts", "wechat", "%s.md" % args.case)
    if not os.path.exists(src):
        raise SystemExit("草稿不存在: %s" % src)
    case = args.case or os.path.splitext(os.path.basename(src))[0]
    title, body = split_md(open(src, encoding="utf-8").read())
    body = "\n".join(ln for ln in body.split("\n") if not ln.startswith("> 平台："))
    if args.cover:
        body = "![](%s)\n\n%s" % (args.cover, body)
    print("案例 %s ｜ 标题: %s ｜ 正文 %d 字" % (case, title, len(body)))
    if not args.abstract:
        import yun_finalize as F                            # noqa: PLC0415
        args.abstract = F.first_paragraph(body)

    proc, cdp = _connect()
    print("9228 CDP: %s" % ("已连上（复用常驻实例）" if proc is None else "新起"))
    results = {}
    try:
        for plat in args.platforms:
            print("\n=== %s ===" % plat, flush=True)
            if plat == "aliyun":
                aid, err = create_aliyun(cdp, title, body, args.abstract)
                if aid:
                    url = "https://developer.aliyun.com/article/new?edit=%s#/" % aid
                    results[plat] = {"ok": True, "draftId": aid, "url": url}
                else:
                    results[plat] = {"ok": False, "err": err or "未知失败"}
            elif plat in ("tencentcloud", "tencent"):
                did, err = create_tencent(cdp, title, body, args.abstract)
                if did:
                    url = ("https://cloud.tencent.com/developer/article/"
                           "write-new?draftId=%s" % did)
                    results[plat] = {"ok": True, "draftId": did, "url": url}
                else:
                    results[plat] = {"ok": False, "err": err or "未知失败"}
            else:
                print("  跳过未知平台 %s" % plat)
    finally:
        try:
            cdp.bws.close()
        except Exception:                                     # noqa: BLE001
            pass

    print("\n--- 结果 ---")
    for plat, info in results.items():
        print("  %-14s %s %s" % (plat, "✓" if info.get("ok") else "✗",
                                  info.get("draftId") or info.get("err")))
    if args.dry:
        print("（--dry：不落账）")
        return
    key = _new_key(case)
    _remember(key, {"case": case, "title": title, "platforms": results,
                    "via": "cdp-9228", "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
    print("✓ 台账: %s ← %s" % (os.path.relpath(LEDGER, ROOT), key))
    print("  收尾/发表沿用: python scripts/sync_longtail.py --finalize --case %s "
          "--al-draft <aid> --tc-draft <did>" % case)


if __name__ == "__main__":
    main()
