#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""两朵云草稿收尾（sync_longtail.py --finalize 的实现模块）。

腾讯云（UI 路线，今天实测）：
  write-new?draftId=<id> 直达编辑器 → 点「去发布」开抽屉（真实鼠标）
  → 点正文图缩略图设封面（synthetic click 即可）→ 点「一键提取」填摘要。
阿里云（API 直写路线，比 UI 稳）：
  putDraft 直接带 abstractContent + coverUrl（p_csrf = c_csrf cookie）。
  社区是发布时字段，草稿阶段平台不存（putDraft/draftDetail 均无该字段，
  发表 payload 才带 relationshipVOS:[{exId,...}]）——所以 finalize 阶段
  用真实鼠标在编辑器 UI 选中社区（页面态保持到发表），并记入台账
  data/yun_finalize.json 供发表步骤复用。

发表（--publish --i-said-publish 双门禁，缺一拒绝执行）：
  阿里云 UI 路线：编辑器选好社区 → 真实鼠标点「发布文章」→
  出现敏感信息确认框自动点「继续发布」→ 跳转 /article/<id> 即成功。

红线：不拿双门禁，谁也点不了「确认发布」。
"""
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import chrome_cdp_launch as L                     # noqa: E402
from juejin_publish import _CDP                   # noqa: E402

PORT = 9228
PROFILE = os.path.expandvars(r"%USERPROFILE%\chrome-yun-profile")
LEDGER = os.path.join(ROOT, "data", "yun_finalize.json")

# 社区候选优先级：--community 未给时按序取 UI 里第一个命中的**全等**选项
COMMUNITY_PREF = ["云原生", "人工智能"]


def ledger_get(case):
    if os.path.exists(LEDGER):
        with open(LEDGER, encoding="utf-8") as f:
            return (json.load(f) or {}).get(case) or {}
    return {}


def ledger_set(case, **kv):
    d = {}
    if os.path.exists(LEDGER):
        with open(LEDGER, encoding="utf-8") as f:
            d = json.load(f) or {}
    rec = d.get(case) or {}
    rec.update(kv)
    rec["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    d[case] = rec
    from atomicio import write_json                    # noqa: PLC0415
    write_json(LEDGER, d)
    return rec

JS_CLICK_REAL = """
(function(){
  var b = %(finder)s;
  if (!b) return null;
  b.scrollIntoView({block: 'center'});
  var r = b.getBoundingClientRect();
  return {x: r.x + r.width / 2, y: r.y + r.height / 2};
})()
"""


def _pos_of(cdp, finder_js):
    """eval(JS_CLICK_REAL % {"finder": finder_js}) —— 返回坐标 dict 或非 dict。"""
    return cdp.eval(JS_CLICK_REAL % {"finder": finder_js})


def _close_disclaimer(cdp):
    """阿里云会话首次交互弹免责声明模态框，关掉。"""
    cdp.eval("""
(function(){
  var b = document.querySelector('.next-dialog-close');
  if (b && b.offsetParent !== null) b.click();
})()""")


def _click_real(cdp, pos):
    for tp, extra in (("mousePressed", {"buttons": 1}),
                      ("mouseReleased", {"buttons": 0})):
        p = {"type": tp, "x": pos["x"], "y": pos["y"], "button": "left",
             "clickCount": 1}
        p.update(extra)
        cdp.send("Input.dispatchMouseEvent", p)
        time.sleep(0.08)


def _connect(headed=False):
    """9228 已起就直连；否则起带登录态 profile。返回 (proc|None, cdp)。

    默认无头（--headless=new，2026-10-28 实测：登录态/draftDetail/社区
    下拉/fileChooser 传图全通，需 --window-size=1440,900 防小视口点偏）。
    headed=True 或环境 CHROME_HEADED=1 走有头（扫码登录、风控兜底）。
    """
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        op.open("http://127.0.0.1:%d/json/version" % PORT, timeout=3).read()
        return None, _CDP(PORT)
    except Exception:
        pass
    headed = headed or str(os.environ.get("CHROME_HEADED", "")).lower() \
        in ("1", "true", "yes")
    L.clear_proxy()
    L.remove_lock(PROFILE)
    chrome, _ = L.pick_chrome()
    if not chrome:
        raise SystemExit("找不到 Chrome")
    extra = () if headed else ("--window-size=1440,900",)
    proc = subprocess.Popen(
        L.build_args(chrome, PROFILE, port=PORT, headless=not headed,
                     extra=extra),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL)
    if not L.wait_cdp(proc, timeout=45, port=PORT):
        raise SystemExit("Chrome CDP 没起来")
    return proc, _CDP(PORT)


def finalize_tencent(cdp, draft_id):
    """封面（正文图选）+ 摘要（一键提取）。返回摘要文本。"""
    cdp.open("https://cloud.tencent.com/developer/article/write-new"
             "?draftId=%s" % draft_id)
    time.sleep(7)
    # 1) 开发布抽屉（去发布按钮：真实鼠标）
    pos = _pos_of(cdp, """
      [].filter.call(document.querySelectorAll('button,[role=button],a,span,div'),
        function(e){ return e.offsetParent !== null
          && (e.textContent||'').trim() === '去发布'; })[0]""")
    if not isinstance(pos, dict):
        raise SystemExit("腾讯云：没找到「去发布」按钮")
    _click_real(cdp, pos)
    time.sleep(5)
    # 2) 封面：点第一张正文图缩略图（synthetic click 实测一次成功）
    cover = cdp.eval("""
(function(){
  var it = document.querySelector('.img-box-bd-item');
  if (!it) return 'no-body-img';
  it.click();
  return 'ok';
})()""")
    time.sleep(2)
    state = cdp.eval("""
(function(){
  var c = document.querySelector('.editor-publish-drawer__img-cover');
  return c ? (c.className.indexOf('is-empt') >= 0 ? 'empty' : 'set') : 'no-slot';
})()""")
    print("  腾讯云封面: %s（%s）" % (state, cover))
    # 3) 摘要：一键提取（真实鼠标）
    pos = _pos_of(cdp, """
      [].filter.call(document.querySelectorAll('button,a,span,div'),
        function(e){ return e.offsetParent !== null
          && (e.textContent||'').trim() === '一键提取'; })[0]""")
    abstract = ""
    if isinstance(pos, dict):
        _click_real(cdp, pos)
        for _ in range(6):
            time.sleep(3)
            abstract = cdp.eval(
                "(document.querySelector('.editor-publish-drawer__textarea-main')"
                "||{}).value || ''") or ""
            if abstract:
                break
    print("  腾讯云摘要: %s" % (abstract[:40] + "…" if abstract else "(提取失败，人工补)"))
    return abstract


def select_community(cdp, draft_id, community=None):
    """阿里云编辑器 UI 真实鼠标选中「发布子社区」。

    community=None 时按 COMMUNITY_PREF 取第一个 UI 里全等命中的选项。
    返回选中的社区名；失败返回 ""（不硬失败，人工兜底）。
    """
    cdp.open("https://developer.aliyun.com/article/new?edit=%s" % draft_id)
    time.sleep(7)
    _close_disclaimer(cdp)
    # 1) 点开下拉（真实鼠标）
    pos = _pos_of(cdp, """
      (function(){
        var lab = [].filter.call(document.querySelectorAll('label'),
          function(l){ return /发布子社区/.test(l.textContent||''); })[0];
        if (!lab) return null;
        var item = lab.closest('.next-form-item');
        if (!item) return null;
        return item.querySelector('.next-select, [class*=select]');
      })()""")
    if not isinstance(pos, dict):
        print("  阿里云社区: 没找到下拉（人工兜底）")
        return ""
    _click_real(cdp, pos)
    time.sleep(2)
    # 2) 选目标项（全等匹配）
    want = json.dumps(community or "")
    opt = cdp.eval("""
(function(){
  var want = %s;
  var items = [].filter.call(
    document.querySelectorAll('.next-menu-item'),
    function(e){ return e.offsetParent !== null; });
  var pick = null;
  if (want) {
    pick = items.filter(function(e){
      return (e.textContent||'').trim() === want; })[0];
  }
  if (!pick) {
    var pref = %s;
    for (var i = 0; i < pref.length && !pick; i++) {
      pick = items.filter(function(e){
        return (e.textContent||'').trim() === pref[i]; })[0];
    }
  }
  if (!pick) return {err: items.map(function(e){
    return e.textContent.trim(); }).slice(0, 25).join('|')};
  var r = pick.getBoundingClientRect();
  return {x: r.x + r.width / 2, y: r.y + r.height / 2,
          name: pick.textContent.trim()};
})()""" % (want, json.dumps(COMMUNITY_PREF)))
    if not isinstance(opt, dict) or "x" not in opt:
        print("  阿里云社区: 选项没命中（%s）" % str(opt)[:120])
        cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape",
                 "code": "Escape", "windowsVirtualKeyCode": 27})
        cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape",
                 "code": "Escape", "windowsVirtualKeyCode": 27})
        return ""
    _click_real(cdp, opt)
    time.sleep(1.5)
    # 3) 确认显示值
    cur = cdp.eval("""
(function(){
  var lab = [].filter.call(document.querySelectorAll('label'),
    function(l){ return /发布子社区/.test(l.textContent||''); })[0];
  var item = lab && lab.closest('.next-form-item');
  var sel = item && item.querySelector('.next-select, [class*=select]');
  return sel ? sel.textContent.trim() : '?';
})()""")
    name = opt.get("name", community or "?")
    ok = (cur == name)
    print("  阿里云社区: %s（%s）" % (name, "UI 已选中" if ok else "校验失败→%s" % cur))
    return name if ok else ""


def upload_image(cdp, cover):
    """阿里云「文章图片」UI 传图（fileChooser 拦截 + DOM.setFileInputFiles）。

    ⚠ 实测该列表**不随草稿持久化**（重载即空），发表 payload 的
    firstImg 取自它——所以 finalize（页面值守）和 publish（发表前）
    都要调它。返回上传后的 alicdn URL（失败返回 ""）。
    """
    cdp.send("Page.enable")
    cdp.send("DOM.enable")
    cdp.send("Page.setInterceptFileChooserDialog", {"enabled": True})
    try:
        pos = _pos_of(cdp, """
          [].filter.call(document.querySelectorAll('button'),
            function(e){ return (e.textContent||'').trim() === '上传图片'; })[0]""")
        if not isinstance(pos, dict):
            print("  阿里云传图: 没找到「上传图片」按钮")
            return ""
        _click_real(cdp, pos)
        backend = None
        deadline = time.time() + 12
        while time.time() < deadline:
            try:
                msg = json.loads(cdp.ws.recv_text())
            except Exception:
                continue
            if msg.get("method") == "Page.fileChooserOpened":
                backend = msg["params"].get("backendNodeId")
                break
        if not backend:
            print("  阿里云传图: fileChooser 未触发")
            return ""
        cdp.send("DOM.setFileInputFiles",
                 {"files": [os.path.abspath(cover)], "backendNodeId": backend})
    finally:
        cdp.send("Page.setInterceptFileChooserDialog", {"enabled": False})
    # 等上传出列表项
    url = ""
    deadline = time.time() + 25
    while time.time() < deadline:
        time.sleep(2)
        n = cdp.eval(
            "document.querySelectorAll('.next-upload-list-item').length") or 0
        if isinstance(n, int) and n > 0:
            url = cdp.eval(r"""
(function(){
  var it = document.querySelector('.next-upload-list-item img');
  return it ? (it.src || '') : '';
})()""") or ""
            break
    print("  阿里云传图: %s" % (url or "(列表项出现但没抓到 URL)"))
    return url


def finalize_aliyun(cdp, draft_id, abstract, cover_url, community=None):
    """API 直写：putDraft 带 abstractContent + coverUrl。

    cover_url 为空时保留服务器现值（不拿空串覆盖已存封面）。
    """
    draft_id = int(draft_id)
    cdp.open("https://developer.aliyun.com/article/new")
    time.sleep(6)
    js = ("""
fetch("/developer/api/articleDraft/draftDetail?aid=%d", {credentials: "include"})
  .then(function(r){ return r.json(); })
  .then(function(j){
    var d = j.data;
    var cs = (document.cookie.match(/c_csrf=([^;]+)/) || ["",""])[1];
    var payload = {title: d.title,
      abstractContent: %s,
      content: d.content, contentRender: "", format: d.format,
      aid: d.aid, coverUrl: (%s) || d.coverUrl || "",
      productTags: [], freeTierVOS: []};
    return fetch("/developer/api/articleDraft/putDraft?p_csrf="
      + encodeURIComponent(cs),
      {method: "POST", credentials: "include",
       headers: {"Content-Type": "application/json"},
       body: JSON.stringify(payload)})
      .then(function(r){ return r.text(); });
  })""" % (draft_id, json.dumps(abstract), json.dumps(cover_url)))
    r = cdp.eval(js)
    print("  阿里云 putDraft → %s" % str(r)[:120])
    # 复核
    v = cdp.eval("""
fetch("/developer/api/articleDraft/draftDetail?aid=%d", {credentials: "include"})
  .then(function(r){ return r.json(); })
  .then(function(j){ var d = j.data;
    return JSON.stringify({abstractLen: (d.abstractContent||"").length,
      coverUrl: d.coverUrl, gmtModified: d.gmtModified}); })""" % draft_id)
    time.sleep(2)
    print("  阿里云复核: %s" % v)
    # 社区：UI 选中（页面态）+ 台账留档（发表步骤复用）
    name = select_community(cdp, draft_id, community)
    if name:
        ledger_set("__community__%s" % draft_id, community=name)
    # 文章图片：UI 传图（页面态，不随草稿持久化——重载/发表前要重传）
    if cover_url and cover_url.startswith("http"):
        # cover_url 是外链；本地图从 case 反查 out/ 目录
        local = _find_local_cover(cdp, draft_id)
        if local:
            u = upload_image(cdp, local)
            if u:
                ledger_set("__community__%s" % draft_id, uploaded_img=u)
    return r


def _find_local_cover(cdp, draft_id):
    """从台账反查 case 名，回本地封面文件（toutiao 优先，和 find_cover 同口径）。"""
    for case, rec in _ledger_all().items():
        if rec.get("al_draft") == str(draft_id):
            for p in (os.path.join(ROOT, "out", "toutiao", case, "cover.png"),
                      os.path.join(ROOT, "out", "agnes", "%s.png" % case)):
                if os.path.exists(p):
                    return p
    return ""


def _ledger_all():
    if os.path.exists(LEDGER):
        with open(LEDGER, encoding="utf-8") as f:
            return json.load(f) or {}
    return {}


def _cover_src(cdp):
    """发表抽屉里封面图当前的 `img.src`。

    ⚠ 判断封面状态**只能看 src**，不能看 class：`.editor-publish-drawer__img-cover`
      设好封面后**仍然是 `is-empty`**（11-03 实测），照 class 判断会把成功的当失败。
    """
    return str(cdp.eval(
        "(function(){var w=document.querySelector('.editor-publish-drawer__img-cover');"
        "var i=w&&w.querySelector('.img-cover-img img');"
        "return (i&&i.src)||'';})()") or "")


def cover_landed(cdp):
    """封面是否**真的落库**。返回 (bool, src)。

    `blob:` / `data:` 只是本地预览（文件选中了但还没传上去）；
    `https://developer.qcloudimg.com/...` 才是传进 COS 后的正式地址 ——
    这个地址印在文章上，是「封面真的存在」的唯一硬证据。
    """
    src = _cover_src(cdp)
    return (src.startswith("http") and not src.startswith("http://blob")), src


def inject_cover(cdp, cover_file, timeout=20):
    """把本地图片挂到封面区，返回 (state, info)。

    state 三态（**别再拿 `files.length` 或 `is-empty` 判断，两者都是假的**）：
      `landed` —— src 已是 `https://developer.qcloudimg.com/...`，真落库
      `staged` —— src 变成 `blob:` / 弹出裁剪框：文件已被接受，
                  **上传发生在「确认发布」那一刻**，提交前不可能看到正式地址
      `failed` —— 覆盖区毫无变化 / 找不到 input / 抛错

    🚨 为什么不能用 `DOM.setFileInputFiles`：对该隐藏 input 无论传 nodeId 还是
       objectId，调用都返回成功、**但不报错地什么都没做**（files.length 恒 0，
       封面区毫无变化）。这是 11-03「发布成功但封面静默丢失」的真凶。
       可用路径是页内构造 File 赋给 `input.files`（React 只认这个）。
    🚨 注入后 `input.files.length` 会读回 **0**（React 处理完清空了 input），
       但文件确实进去了 —— 判据只能是封面区 `img.src` 有没有变化。
    """
    if not (cover_file and os.path.isfile(cover_file)):
        return "failed", "封面文件不存在：%s" % cover_file
    before = _cover_src(cdp)
    with open(cover_file, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    name = os.path.basename(cover_file)
    mime = ("image/png" if name.lower().endswith(".png")
            else "image/jpeg" if name.lower().endswith((".jpg", ".jpeg"))
            else "image/png")
    r = cdp.eval("""
      (function(){
        try{
          var b64="%s", bin=atob(b64), n=bin.length, arr=new Uint8Array(n);
          for(var i=0;i<n;i++) arr[i]=bin.charCodeAt(i);
          var f=new File([arr], %s, {type:%s});
          var dt=new DataTransfer(); dt.items.add(f);
          var inp=document.querySelector('input.img-cover-input');
          if(!inp) return 'NO_INPUT';
          inp.files=dt.files;
          inp.dispatchEvent(new Event('input',{bubbles:true}));
          inp.dispatchEvent(new Event('change',{bubbles:true}));
          return 'DISPATCHED';
        }catch(e){ return 'ERR:'+e.message }
      })()""" % (b64, json.dumps(name), json.dumps(mime)))
    tip = str(r or "")
    if tip == "NO_INPUT":
        return "failed", "抽屉里找不到 input.img-cover-input"
    if tip.startswith("ERR"):
        return "failed", "注入抛错：%s" % tip[4:70]

    deadline = time.time() + timeout
    src = before
    while time.time() < deadline:
        time.sleep(1.5)
        src = _cover_src(cdp)
        if src and src != before:
            break
    if not src or src == before:
        return "failed", "注入后封面区无变化（文件可能未被接受），src=%s" % (src or "(空)")
    if src.startswith("http"):
        return "landed", src
    if src.startswith("blob:") or cdp.eval(
            "!!document.querySelector('.cropper-container')"):
        return "staged", src
    return "failed", "封面区状态异常：%s" % src[:70]


def _hook_publish(cdp):
    """提交前挂抓包钩子：记下 article/publish 类接口的响应，用来取 articleId。

    ⚠ 为什么要抓：从草稿发布成功后 URL 只回到 `write-new`（**不带 id**），
      而「封面是否落库」必须到文章页 `og:image` 才能验 —— 没有 id 就无从复核。
    """
    cdp.eval("""(function(){
      if(window.__pub) return 'already';
      window.__pub=[];
      function grab(u, p){
        try{ p.then(function(r){ try{ r.clone().text().then(function(t){
          if(/article|publish|draft/i.test(u) && t && t.length < 200000)
            window.__pub.push({u:u, t:String(t).slice(0,4000)});
        }).catch(function(){}); }catch(e){} }); }catch(e){}
      }
      var of = window.fetch;
      window.fetch = function(){
        var u = String((arguments[0] && arguments[0].url) || arguments[0]);
        var p = of.apply(this, arguments); grab(u, p); return p;
      };
      var oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.open = function(m,u){ this.__u = String(u);
        return oo.apply(this, arguments); };
      XMLHttpRequest.prototype.send = function(){
        var x = this;
        this.addEventListener('load', function(){
          try{ if(/article|publish|draft/i.test(x.__u) && x.responseText
                 && x.responseText.length < 200000)
            window.__pub.push({u:x.__u, t:String(x.responseText).slice(0,4000)}); }catch(e){}
        });
        return os.apply(this, arguments);
      };
      return 'hooked';
    })()""")


def _published_article_id(cdp):
    """从 `_hook_publish` 抓到的响应里取 7 位 articleId；取不到返回空串。"""
    try:
        items = json.loads(str(cdp.eval("JSON.stringify(window.__pub||[])") or "[]"))
    except Exception:                                      # noqa: BLE001
        return ""
    for it in reversed(items):
        for m in re.finditer(r'"(articleId|article_id)"\s*:\s*"?(\d{6,9})',
                             str(it.get("t") or "")):
            return m.group(2)
    # 兜底：发布响应里可能只回一个裸 id（7 位 = articleId，6 位是 draftId，别混）
    for it in reversed(items):
        u = str(it.get("u") or "")
        if not re.search(r"publish|article/save|article/write", u):
            continue
        for m in re.finditer(r'"(?:id|aid)"\s*:\s*"?(\d{7})"?', str(it.get("t") or "")):
            return m.group(1)
    return ""


def _open_publish_drawer(cdp):
    """在已打开的编辑器页点「去发布」把抽屉叫出来。"""
    pos = _pos_of(cdp, """
      [].filter.call(document.querySelectorAll('button,[role=button],a,span,div'),
        function(e){ return e.offsetParent !== null
          && (e.textContent||'').trim() === '去发布'; })[0]""")
    if not isinstance(pos, dict):
        return False
    _click_real(cdp, pos)
    time.sleep(5)
    return bool(cdp.eval("!!document.querySelector('.editor-publish-drawer')"))


def publish_tencent(cdp, draft_id, abstract="", cover_file="", tags=("架构设计",),
                    require_cover=True):
    """腾讯云发表：一次会话内填完 4 个必填项再提交。返回 (ok, 说明)。

    🚨 为什么必须「一次填完」（2026-11-03 实测，白折腾近一小时）：
      抽屉是**临时表单**，任何 Page.navigate / 重开页面都会清空 ——
      回读会看到 `{abs:0, orig:false}`。当初为了「再确认一眼」反复重载，
      填了三轮都被清掉，最后误判成「腾讯云发布不可用」。

    缺任一项，点「确认发布」都是**静默失败**：只弹「文章已于 刚刚 保存到草稿」，
    URL 停在 write-new?draftId=xxx，不报错。四步分别是：
      1. 文章来源（.t-radio__former[0] = 原创）—— 必须回读 checked
      2. 标签（.cdc-tags-input__input[0]）—— 真实键盘输入后**点下拉候选**，
         只输入不点候选等于没选（回读 value 是空）
      3. 摘要（textarea.editor-publish-drawer__textarea-main）—— React 受控，
         用 value setter + input 事件；「一键提取」不稳
      4. 封面（input.img-cover-input）—— 走 `inject_cover()` 注入后必须判**文件是否
         真被接受**（`DOM.setFileInputFiles` 对该框无效且不报错，是「静默丢封面」
         真凶；`files.length`、`is-empty` 两个判据都是假的）
    专栏**不是**必填（这个号没有专栏，列表只有「创建专栏」）。

    成功判据：URL 从 write-new?draftId=xxx 变成 write-new（无 draftId）。
    封面判据（两段，缺一不可）：
      · 提交前：`inject_cover()` 必须是 landed/staged；failed 且 `require_cover=True`
        就直接 return False，不带着空封面提交（否则线上留一篇无图文章，事后补图
        还会让文章重回审核）；
      · 提交后：拿提交响应里的 articleId 回读创作中心卡片缩略图（CSS background-image），
        读不到即判定封面没落库并 return False（附 `--fix-cover` 补救命令）。
    """
    cdp.open("https://cloud.tencent.com/developer/article/write-new"
             "?draftId=%s" % draft_id)
    time.sleep(10)
    if not _open_publish_drawer(cdp):
        return False, "没找到「去发布」或发布抽屉没打开"

    # 1) 文章来源 = 原创
    if cdp.eval("""(function(){var r=document.querySelectorAll('.t-radio__former');
        if(!r.length) return false; r[0].click(); return r[0].checked;})()""") is not True:
        return False, "文章来源（原创）没选中"

    # 2) 标签：真实键盘 + 点候选
    for tag in list(tags)[:5]:
        p = _pos_of(cdp, "document.querySelectorAll('.cdc-tags-input__input')[0]")
        if not isinstance(p, dict):
            return False, "找不到标签输入框"
        _click_real(cdp, p)
        time.sleep(1)
        for ch in tag:
            cdp.send("Input.dispatchKeyEvent", {"type": "char", "text": ch})
            time.sleep(0.12)
        time.sleep(3)
        opt = _pos_of(cdp, """
          [].filter.call(document.querySelectorAll('li,div[class*=option],span,div'),
            function(e){ return e.offsetParent !== null
              && (e.textContent||'').trim() === %s
              && e.children.length === 0; })[0]""" % json.dumps(tag))
        if not isinstance(opt, dict):
            time.sleep(3)
            opt = _pos_of(cdp, """
              [].filter.call(document.querySelectorAll('li,div[class*=option],span,div'),
                function(e){ return e.offsetParent !== null
                  && (e.textContent||'').trim() === %s
                  && e.children.length === 0; })[0]""" % json.dumps(tag))
        if not isinstance(opt, dict):
            return False, "标签「%s」没有下拉候选" % tag
        _click_real(cdp, opt)
        time.sleep(2)
        # 回读：标签区必须真的挂上这个标签（只输入不点候选时这里是空的，
        # 而点「确认发布」只会静默存草稿 —— 11-03 就卡在这一步）
        zone = str(cdp.eval("(document.querySelector('.cdc-tags-input')"
                            "||{}).innerText||''"))
        if tag not in zone:
            return False, "标签「%s」点了但没选中" % tag

    # 3) 摘要
    if abstract:
        n = cdp.eval("""
        (function(){var t=document.querySelector('.editor-publish-drawer__textarea-main');
          if(!t) return -1;
          var s=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;
          s.call(t,%s); t.dispatchEvent(new Event('input',{bubbles:true}));
          return t.value.length;})()""" % json.dumps(abstract))
        if not n or int(n) <= 0:
            return False, "摘要注入失败"

    # 4) 封面 —— 必须确认文件真的被接受（以前只打一行 warn 就提交，才会静默丢图）
    cover_state = ""
    if cover_file:
        if not os.path.isfile(cover_file):
            return False, "封面文件不存在：%s" % cover_file
        cover_state, cmsg = inject_cover(cdp, cover_file)
        if cover_state == "failed":
            # 宁可让调用方重跑，也不要在线上留一篇无封面的文章 ——
            # 事后补图会让已发布文章重回审核（11-03 实测 3 分钟）。
            if require_cover:
                return False, "封面没挂上，已拦下不提交：%s" % cmsg
            print("  ⚠ 封面没挂上（%s），require_cover=False 继续提交" % cmsg)
        elif cover_state == "staged":
            print("  ✓ 封面文件已接受（裁剪框），正式地址由提交时上传产生")
        else:
            print("  ✓ 封面已在线上：%s" % cmsg[:80])

    # 5) 提交（先挂网络钩子：提交响应里带 articleId，用来回读封面）
    _hook_publish(cdp)
    p2 = _pos_of(cdp, """
      [].filter.call(document.querySelectorAll('button'),
        function(e){ return e.offsetParent !== null
          && (e.textContent||'').trim() === '确认发布'; })[0]""")
    if not isinstance(p2, dict):
        return False, "没找到「确认发布」"
    _click_real(cdp, p2)
    time.sleep(16)
    url = str(cdp.eval("location.href") or "")
    if "draftId=" in url:
        return False, "点了确认发布但 URL 仍带 draftId（静默失败）：%s" % url[:90]

    # 6) 封面复核：只有线上 og:image 有地址才算真的落库
    if cover_state == "staged":
        aid = _published_article_id(cdp)
        if not aid:
            return True, ("%s\n    封面：已随提交上传，但没抓到 articleId，未能线上复核"
                          "（用 tc_manage.py status 或文章页 og:image 复查）" % url[:80])
        ok, info = verify_cover_online(cdp, aid, timeout=180)
        if ok:
            return True, "%s\n    封面已落库：%s" % (url[:70], str(info)[:80])
        return False, ("已发布，但封面没落库（%s）。文章 id=%s，"
                       "跑 `--fix-cover %s --cover <图>` 可补上" % (info, aid, aid))
    return True, url


def update_cover(cdp, article_id, cover_file, verify=True):
    """给**已发布**文章补/换封面。返回 (ok, 说明)。

    走编辑页 `write-new/<articleId>`（不是 draftId，两者不可混用：
    articleId 7 位、draftId 6 位），点「去发布」→ 注图 → 落库校验 → 「确认发布」。

    ⚠ 已知副作用：改已发布文章会**重回「审核中」**，通常几分钟内二次过审
      （11-03 实测 3 分钟），期间列表卡片显示「正在审核中」、缩略图不显示。
    ⚠ 线上判据是卡片缩略图的 CSS `background-image`，不是 `og:image`（那是平台
      默认分享图标），也不是卡片里的 `<img>`（缩略图不是 img 标签）。
    """
    if not (cover_file and os.path.isfile(cover_file)):
        return False, "封面文件不存在：%s" % cover_file
    prev = ""
    cdp.open("https://cloud.tencent.com/developer/creator/article")
    time.sleep(10)
    prev, _st = _card_of(cdp, article_id)
    if prev:
        print("  改前线上封面：%s" % prev[:80])
    cdp.open("https://cloud.tencent.com/developer/article/write-new/%s" % article_id)
    time.sleep(10)
    if not _open_publish_drawer(cdp):
        return False, "没找到「去发布」或发布抽屉没打开"
    state, info = inject_cover(cdp, cover_file)
    if state == "failed":
        return False, "封面没挂上，已拦下不提交：%s" % info
    print("  ✓ 封面文件已接受（%s），提交后回读卡片缩略图复核" % state)
    p = _pos_of(cdp, """
      [].filter.call(document.querySelectorAll('button'),
        function(e){ return e.offsetParent !== null
          && (e.textContent||'').trim() === '确认发布'; })[0]""")
    if not isinstance(p, dict):
        return False, "没找到「确认发布」"
    _click_real(cdp, p)
    time.sleep(16)
    if not verify:
        return True, str(info)
    ok, got = verify_cover_online(cdp, article_id, baseline=prev)
    if not ok:
        return False, "已提交，但封面没换成功：%s" % got
    if prev:
        return True, "封面已更换：%s" % got
    return True, "封面已上线：%s" % got


CARD_JS = """(function(){
  var a=[].filter.call(document.querySelectorAll('a[href*="/developer/article/%s"]'),
    function(e){ return e.offsetParent!==null; })[0];
  if(!a) return JSON.stringify({cover:'', state:''});
  var b=a; for(var k=0;k<10 && b.parentElement;k++){ b=b.parentElement;
    if(/编辑|删除/.test(b.textContent||'')) break; }
  var out='';
  b.querySelectorAll('*').forEach(function(e){
    if(out) return;
    var bi=getComputedStyle(e).backgroundImage||'';
    var m=bi.match(/url\\("?([^")]+)"?\\)/);
    if(m && /qcloudimg|yehe/.test(m[1])) out=m[1];
  });
  var st=(b.innerText||'').match(/已发布|审核中|未通过/);
  return JSON.stringify({cover:out, state:st?st[0]:''});
})()"""

CARDSEL_COVER_JS = CARD_JS          # 兼容旧引用


def _card_of(cdp, article_id):
    """读创作中心某篇文章卡片上的 (封面缩略图 url, 状态)。"""
    try:
        d = json.loads(str(cdp.eval(CARD_JS % article_id) or "{}"))
    except Exception:                                      # noqa: BLE001
        return "", ""
    return str(d.get("cover") or ""), str(d.get("state") or "")


def verify_cover_online(cdp, article_id, baseline="", timeout=360):
    """回读**创作中心卡片缩略图**，确认封面真的挂在线上。返回 (ok, url|原因)。

    ⚠ 判据只能用卡片的 CSS `background-image`：
      · 文章页 `og:image` 是平台默认分享图标（`.../shareicons/cloud.png`），与封面无关；
      · 卡片缩略图**不是 `<img>`**，`querySelectorAll('img')` 恒为空。
    🚨 提交后卡片不会立刻换图（CDN/缓存 + 文章重回审核）：**必须轮询**，
       而且给了 `baseline` 时要等 URL **真的变了**才算成功 —— 11-03 就是提交后
       立刻读，读到旧图，把一次成功的换图误判成「没换成」。
    """
    deadline = time.time() + timeout
    last = ("", "")
    while time.time() < deadline:
        cdp.open("https://cloud.tencent.com/developer/creator/article")
        time.sleep(10)
        last = _card_of(cdp, article_id)
        cover, state = last
        if cover.startswith("http") and state == "已发布":
            if not baseline or cover.split("?")[0] != baseline.split("?")[0]:
                return True, cover
        time.sleep(15)
    cover, state = last
    if not cover.startswith("http"):
        # 卡片一直读不到缩略图时兜底：开编辑页抽屉读 `img.src`（https 才算落库）
        ok2, msg2 = verify_cover_in_drawer(cdp, article_id, timeout=90)
        if ok2:
            return True, msg2
        return False, "卡片读不到缩略图（状态=%s），抽屉里也没有封面：%s" % (
            state or "未知", msg2)
    return False, "封面仍是改前那张（状态=%s），等了 %ds 没换" % (state or "未知", timeout)


def verify_cover_in_drawer(cdp, article_id, timeout=90):
    """备用判据：打开编辑页抽屉，读封面 `img.src`（`https://` 才算落库）。

    卡片读不到缩略图时用它兜底。注意抽屉会保持临时表单状态，读完别复用该页提交。
    """
    deadline = time.time() + timeout
    src = ""
    while time.time() < deadline:
        cdp.open("https://cloud.tencent.com/developer/article/write-new/%s" % article_id)
        time.sleep(10)
        if _open_publish_drawer(cdp):
            src = _cover_src(cdp)
            if src.startswith("http"):
                return True, "抽屉封面：%s" % src
        time.sleep(15)
    return False, "抽屉里也没有封面（src=%s）" % (src or "(空)")


def publish_aliyun(cdp, draft_id, community=None, cover_file=""):
    """发表阿里云草稿。**只准由 --publish --i-said-publish 双门禁调用。**

    UI 路线（全新页面态，三项页面态字段一次配齐）：
    选社区 → 传文章图片（firstImg）→ 真实鼠标点「发布文章」
    → 敏感信息确认框自动「继续发布」→ 跳转 /article/<id> 即成功。
    """
    # 社区：显式参数 > 台账 > 首选
    name = community or ledger_get("__community__%s" % draft_id).get("community")
    if name:
        name = select_community(cdp, draft_id, name)   # 内含 open 草稿页
    elif getattr(cdp, "ws", None) is None:
        # ⚠ 不选社区时也必须先把草稿页开出来：`_connect()` 只建了 browser 级
        #   WS，没 attach 任何 page ⇒ 直接 eval 会 `NoneType.send_text` 崩。
        #   （「不发布到技术圈」走首页 feed 是平台允许的，社区可以留空。）
        cdp.open("https://developer.aliyun.com/article/new?edit=%s" % draft_id)
        time.sleep(8)
        _close_disclaimer(cdp)
    # 文章图片（firstImg，页面态必传）：显式路径 > 台账反查
    local = cover_file or _find_local_cover(cdp, draft_id)
    if local and os.path.exists(local):
        u = upload_image(cdp, local)
        if u:
            ledger_set("__community__%s" % draft_id, uploaded_img=u)
    elif local:
        print("  ⚠ 封面文件不存在: %s（跳过传图）" % local)
    # 点「发布文章」（真实鼠标）
    pos = _pos_of(cdp, """
      [].filter.call(document.querySelectorAll('button'),
        function(e){ return (e.textContent||'').trim() === '发布文章'; })[0]""")
    if not isinstance(pos, dict):
        raise SystemExit("阿里云发表：没找到「发布文章」按钮")
    _click_real(cdp, pos)
    time.sleep(3)
    # 确认框。⚠ 实测文案是「确认 / 取消」（2026-11-01），老版本才是「继续发布」。
    #   必须限定在 .next-dialog 内找，否则会命中页面上别处的「确认」。
    for label in ("继续发布", "确认"):
        pos = _pos_of(cdp, """
      [].filter.call(document.querySelectorAll('.next-dialog button'),
        function(e){ return (e.textContent||'').trim() === %s; })[0]"""
                      % json.dumps(label))
        if isinstance(pos, dict):
            print("  发布确认框 → %s" % label)
            _click_real(cdp, pos)
            break
    # 滑块验证码（阿里云 Captcha，.aliyunCaptcha-top）：CDP 合成拖动会被判 bot
    #   ——2026-11-01 实测：JS 派发能让滑块走到 140px，但松手即重置；
    #   CDP Input 拖动则连 press 都接不住。只有人工拖能过。
    time.sleep(2)
    try:
        cap = cdp.eval("!!document.querySelector('.aliyunCaptcha-top')")
    except Exception:                                          # noqa: BLE001
        cap = False
    if cap:
        raise SystemExit(
            "阿里云发表：弹出滑块验证码（.aliyunCaptcha-top）。CDP 合成事件过不了，"
            "请在 9228 窗口人工拖一次滑块，通过后再重跑本命令（草稿内容已保存，"
            "重复发表不会建新稿）。")
    # 等跳转 /article/<id>
    for _ in range(20):
        time.sleep(2)
        url = cdp.eval("location.href") or ""
        import re
        m = re.search(r"/article/(\d+)", url)
        if m:
            print("  ✅ 已发表: https://developer.aliyun.com/article/%s" % m.group(1))
            ledger_set("__community__%s" % draft_id, published=m.group(1),
                       published_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            return m.group(1)
    raise SystemExit("阿里云发表：20s×2 内未见跳转，去页面人工确认（可能卡审核/弹窗）")


def first_paragraph(body, limit=300):
    """正文首个非空段（剥 markdown 标记）作摘要兜底。

    ⚠ 跳过 `>` 引用段与 `---` 分隔线：公众号稿的导语常写成引用块，
      原样塞进阿里云摘要框会带出一个 `&gt;` 前缀（2026-11-01 实拍踩到）。
    """
    import re
    for para in body.split("\n"):
        p = para.strip()
        if not p or p.startswith("#") or p.startswith("!["):
            continue
        if p.startswith(">") or p.startswith("---"):
            continue
        p = re.sub(r"[*`\[\]]|\[([^\]]*)\]\([^)]*\)", r"\1", p)
        return p[:limit]
    return ""


def run(case=None, file=None, tc_draft=None, al_draft=None, abstract=None,
        cover_url="", community=None, headed=False):
    """入口：sync_longtail --finalize 调这里。"""
    src = file or os.path.join(ROOT, "drafts", "wechat", "%s.md" % case)
    body = ""
    if os.path.exists(src):
        from csdn_publish import split_md
        _, body = split_md(open(src, encoding="utf-8").read())
    # 摘要来源优先级：显式参数 > 台账留档 > 正文首段（防止短首段覆盖好摘要）
    abstract = (abstract or (ledger_get(case).get("abstract") if case else "")
                or first_paragraph(body))
    proc, cdp = _connect()
    try:
        if tc_draft:
            print("腾讯云 finalize draftId=%s" % tc_draft)
            finalize_tencent(cdp, tc_draft)
            if case:
                ledger_set(case, tc_draft=str(tc_draft))
        if al_draft:
            print("阿里云 finalize aid=%s" % al_draft)
            if not abstract:
                print("  ⚠ 没有摘要来源，跳过")
            else:
                finalize_aliyun(cdp, al_draft, abstract, cover_url, community)
            if case:
                ledger_set(case, al_draft=str(al_draft), cover_url=cover_url,
                           abstract=abstract[:100],
                           community=community or ledger_get(
                               "__community__%s" % al_draft).get("community", ""))
        print("\n⚠ 红线：「确认发布」永远留给人工（要发表见 --publish 双门禁）。")
    finally:
        try:
            cdp.bws.close()
        except Exception:                                      # noqa: BLE001
            pass


if __name__ == "__main__":
    # 独立用法：python scripts/yun_finalize.py --tc-draft 337119
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--case")
    ap.add_argument("--file")
    ap.add_argument("--tc-draft")
    ap.add_argument("--al-draft")
    ap.add_argument("--abstract", default="")
    ap.add_argument("--cover-url", default="")
    ap.add_argument("--community", default="",
                    help="阿里云发布子社区（缺省按 COMMUNITY_PREF 全等匹配）")
    ap.add_argument("--publish", action="store_true",
                    help="发表模式（阿里云）。必须同时给 --i-said-publish")
    ap.add_argument("--headed", action="store_true",
                    help="有头模式（默认无头；扫码登录/风控兜底用，或 CHROME_HEADED=1）")
    ap.add_argument("--cover", default="",
                    help="发表时的本地图（文章图片/firstImg），缺省台账反查")
    ap.add_argument("--fix-cover", default="", metavar="ARTICLE_ID",
                    help="给已发布文章补/换封面（7 位 articleId），需同时给 --cover")
    ap.add_argument("--i-said-publish",
                    help="发表确认口令，值必须 = 用户原话 i-said-publish")
    a = ap.parse_args()
    if a.fix_cover:
        # 输出示例：python scripts/yun_finalize.py --fix-cover 2774554 \
        #             --cover out/toutiao/cod-dex/cover.png
        if not a.cover:
            raise SystemExit("🚫 --fix-cover 需要同时给 --cover <本地图片>")
        proc, cdp = _connect()
        try:
            ok, info = update_cover(cdp, a.fix_cover, a.cover)
            print(("✓ " if ok else "✗ ") + str(info))
            if ok:
                ledger_set("__cover__%s" % a.fix_cover, cover_url=info.split()[0],
                           at=time.strftime("%Y-%m-%d %H:%M"))
            sys.exit(0 if ok else 1)
        finally:
            try:
                cdp.bws.close()
            except Exception:                              # noqa: BLE001
                pass
    if a.publish:
        if a.i_said_publish != "i-said-publish":
            raise SystemExit("🚫 拒绝发表：--publish 必须与 --i-said-publish "
                             "i-said-publish 成对（用户明确说「发布」才允许）")
        if not a.al_draft:
            raise SystemExit("🚫 拒绝发表：--publish 需要 --al-draft")
        proc, cdp = _connect()
        try:
            publish_aliyun(cdp, int(a.al_draft), a.community or None,
                           a.cover or "")
        finally:
            try:
                cdp.bws.close()
            except Exception:                                  # noqa: BLE001
                pass
    else:
        run(case=a.case, file=a.file, tc_draft=a.tc_draft, al_draft=a.al_draft,
            abstract=a.abstract or None, cover_url=a.cover_url,
            community=a.community or None)
