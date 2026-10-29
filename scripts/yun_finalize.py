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
import json
import os
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


def publish_aliyun(cdp, draft_id, community=None, cover_file=""):
    """发表阿里云草稿。**只准由 --publish --i-said-publish 双门禁调用。**

    UI 路线（全新页面态，三项页面态字段一次配齐）：
    选社区 → 传文章图片（firstImg）→ 真实鼠标点「发布文章」
    → 敏感信息确认框自动「继续发布」→ 跳转 /article/<id> 即成功。
    """
    # 社区：显式参数 > 台账 > 首选
    name = community or ledger_get("__community__%s" % draft_id).get("community")
    name = select_community(cdp, draft_id, name) if name else ""
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
    # 敏感信息确认框（30 位以上连续字母数字才触发）→ 继续发布
    pos = _pos_of(cdp, """
      [].filter.call(document.querySelectorAll('button'),
        function(e){ return (e.textContent||'').trim() === '继续发布'; })[0]""")
    if isinstance(pos, dict):
        print("  敏感信息确认框 → 继续发布")
        _click_real(cdp, pos)
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
    raise SystemExit("阿里云发表：10s×2 内未见跳转，去页面人工确认（可能卡审核/弹窗）")


def first_paragraph(body, limit=300):
    """正文首个非空段（剥 markdown 标记）作摘要兜底。"""
    import re
    for para in body.split("\n"):
        p = para.strip()
        if not p or p.startswith("#") or p.startswith("!["):
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
    ap.add_argument("--i-said-publish",
                    help="发表确认口令，值必须 = 用户原话 i-said-publish")
    a = ap.parse_args()
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
