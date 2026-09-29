#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站专栏发布链路（试点版）。

形态确认（2026-09-29）：B站「专栏」是图文长文载体——标题 + 富文本正文 + 封面，
和今日头条图文同构（不像小红书 9 图卡片）。所以：
  · 架构复用 xhs_publish.py 的 CDP（开 9222 调试 Chrome、真实鼠标点击、跳确认框）
  · 投稿逻辑复用 toutiao_publish.py（富文本长文 + 横版封面）

内容源：out/articles/<id>.html（公众号长文，纯文字无图）→ 清洗成 B站专栏富文本。
封面：复用 out/toutiao/<id>/cover.png（横版 16:9，已生成，不另造）。
合规：B站对站外导流零容忍（二维码 / 账号 / 第三方链接 / 公众号 CTA），比小红书更严，
      全部剥光；且发布后不可编辑、每天限 5 篇 → 只存草稿、人工终审。

用法：
  python scripts/bilibili_publish.py build --all            # 全量生成 B站版稿
  python scripts/bilibili_publish.py build --case prosp     # 单条
  python scripts/bilibili_publish.py probe                  # 连 9222 dump 投稿页 DOM，校准 SEL
  python scripts/bilibili_publish.py publish --case prosp [--dry]   # 存草稿（--dry 只探入口）
  python scripts/bilibili_publish.py publish --all          # 批量存草稿箱（草稿不限 5 篇/天）

SEL 已按 2026-09-29 实测填定（read-draft → 新的创作 → read-editor 编辑器）。
投稿页 DOM 随 B站改版会漂，若 publish 连续失败先重跑 `probe` 校准。
封面/分区草稿阶段不填（B站草稿不强制），人工终审时补。
"""

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wechat_publish as wp  # noqa: E402  复用 CDP / 案例加载

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out", "bili")
OUT_ART = os.path.join(ROOT, "out", "articles")
OUT_TT = os.path.join(ROOT, "out", "toutiao")
CASES = os.path.join(ROOT, "data", "cases.json")
PUB_PORT = 9222

# 专栏投稿入口（登录后确认；可能跳创作中心新版 URL）
BILI_ARTICLE = "https://member.bilibili.com/platform/upload/text"

# ---- 选择器（2026-09-29 实测：投稿页结构） -------------------------------
# 投稿入口 /platform/upload/text/new-article → 同域 read-draft iframe 显示
# 「创作指南」，点「新的创作」(.new-creation_button) 后进 read-editor iframe
# （同域、in-process，可从主文档 context 直接操作其 contentDocument）：
#   · 标题：<textarea class="title-input__inner">
#   · 正文：<div class="tiptap ProseMirror eva3-editor" contenteditable>
#            （Tiptap/ProseMirror，与头条同构 → execCommand('insertHTML') 灌入）
#   · 存草稿：按钮文本「保存为草稿」
# 封面/分区在发布设置里，草稿不强制；试点先不填，人工核对。
SEL = {
    "new_creation": ".new-creation_button",   # read-draft iframe 内「新的创作」
    "title": ".title-input__inner",           # read-editor iframe 内 标题 textarea
    "body": ".tiptap.ProseMirror.eva3-editor",  # read-editor iframe 内 正文编辑区
    # 存草稿按钮按文本点（无稳定 id），见 _click_by_text
    "draft_text": "保存为草稿",
    "publish_text": "发布",
}

# 必须就位的选择器（封面/分区草稿不强制）
REQUIRED_SEL = ("new_creation", "title", "body", "draft_text")

# ---- 合规：B站对站外导流零容忍 ------------------------------------------
# 只拦「导流 CTA 句式」；正文里对微信/公众号/私信等平台的**事实性提及**
# （如「预约流程必须接微信小程序」「公众号的发布接口不开放」）不算导流，
# 不拦 —— 2026-09-29 逐条核过 12 处报警全是前者。
RISK_PATTERNS = [
    "万物解释者", "拆解海外", "案例库", "完整核实记录", "每天筛",
    "加微信", "微信号", "加我", "扫码", "二维码",
    "关注公众号", "公众号：", "私信我", "后台私信", "联系我",
]

# 结尾导流段关键词（整段删除）
LEADOUT_KW = ["案例库", "社群", "每天筛", "完整核实记录", "核实记录", "我每天筛"]


# --------------------------------------------------------------------------
# 加载
# --------------------------------------------------------------------------
def load_cases():
    if not os.path.isfile(CASES):
        return {}
    with open(CASES, encoding="utf-8") as f:
        d = json.load(f)
    # cases.json 可能是 {cases:[...]} 或 [...]
    if isinstance(d, dict):
        return d.get("cases", d)
    return d


def list_ids():
    """out/articles 下所有 <id>.html。"""
    if not os.path.isdir(OUT_ART):
        return []
    return sorted(re.sub(r"\.html$", "", f) for f in os.listdir(OUT_ART)
                 if f.endswith(".html"))


def _js(cdp, expr):
    r = cdp.eval(expr, refresh_context=True)
    if isinstance(r, str):
        try:
            r = json.loads(r)
        except (ValueError, TypeError):
            pass
    return r


# --------------------------------------------------------------------------
# 内容转换：公众号长文 → B站专栏富文本
# --------------------------------------------------------------------------
def load_article_html(cid):
    p = os.path.join(OUT_ART, cid + ".html")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return f.read()


def extract_title(html):
    """从 <h1> 取标题文本。"""
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
    if not m:
        return ""
    return re.sub(r"<[^>]+>", "", m.group(1)).strip()


def make_title(cid, html):
    """B站标题 ≤40 字（建议 ≤30）。

    优先复用头条精修标题（out/toutiao/<id>/meta.json，已按「月收$xxx：钩子」
    打磨过且 ≤30 字）；没有才退回文章 h1（那是数据串，只能截断兜底）。
    """
    mp = os.path.join(OUT_TT, cid, "meta.json")
    if os.path.isfile(mp):
        try:
            with open(mp, encoding="utf-8") as f:
                t = (json.load(f).get("title") or "").strip()
            if t:
                return t[:30]
        except (ValueError, OSError):
            pass
    t = extract_title(html)
    if not t:
        t = cid
    # 去掉会占字数的括号数据说明，优先保留核心名
    t = re.sub(r"[（(].*?[)）]", "", t).strip()
    if len(t) > 30:
        t = t[:30]
    return t


def clean_body(html):
    """清洗成 B站专栏富文本片段（直接 insertHTML 进编辑器）。

    · 取 <section> 内正文
    · 删 HTML 注释
    · 删结尾站外导流段——**逐段匹配**：关键词只允许出现在单个 <p>/<blockquote>
      内部。早期版本用 `<p>.*?关键词.*?</p>` + re.S，`.*?` 会从正文第一个 <p>
      一路吞到关键词所在段，1lookup 1808 字被吃剩 241 字（2026-09-29 实测）。
    · **x** → <strong>x</strong>
    · 去 style 属性（B站用自己的样式，微信灰字 style 会显脏）
    · 保留 h2/p/blockquote/hr
    """
    m = re.search(r"<section[^>]*>(.*)</section>", html, re.S | re.I)
    body = m.group(1) if m else html
    # 标题单独填标题框，正文区不再留 <h1>（否则与标题框重复）
    body = re.sub(r"<h1[^>]*>.*?</h1>", "", body, flags=re.S | re.I)
    # 删注释
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)

    def _drop_if_leadout(match):
        seg = match.group(0)
        return "" if any(kw in seg for kw in LEADOUT_KW) else seg

    # 逐段删导流段（</p> 内不会嵌 p，单段匹配不会跨界）
    body = re.sub(r"<p[^>]*>.*?</p>", _drop_if_leadout, body, flags=re.S)
    body = re.sub(r"<blockquote[^>]*>.*?</blockquote>", _drop_if_leadout,
                  body, flags=re.S)
    # **加粗** → strong
    body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", body)
    # 去所有 style 属性
    body = re.sub(r"\s+style=\"[^\"]*\"", "", body)
    # 去空段落/空引用块
    body = re.sub(r"<p[^>]*>\s*</p>", "", body)
    body = re.sub(r"<blockquote[^>]*>\s*</blockquote>", "", body)
    return body.strip()


def cover_path(cid):
    """横版封面复用头条已生成的 cover.png。"""
    p = os.path.join(OUT_TT, cid, "cover.png")
    return p if os.path.isfile(p) else None


def risk_check(text):
    return [w for w in RISK_PATTERNS if w in (text or "")]


# --------------------------------------------------------------------------
# build：生成 out/bili/<id>/{article.html, note.json}
# --------------------------------------------------------------------------
def build_one(cid):
    html = load_article_html(cid)
    if not html:
        print("  ✗ 找不到 out/articles/%s.html" % cid)
        return False
    os.makedirs(os.path.join(OUT, cid), exist_ok=True)
    title = make_title(cid, html)
    body = clean_body(html)
    cp = cover_path(cid)

    art = body  # 标题单独填标题框，正文区不含 <h1>
    with open(os.path.join(OUT, cid, "article.html"), "w", encoding="utf-8") as f:
        f.write(art)

    note = {
        "id": cid,
        "title": title,
        "body_len": len(re.sub(r"<[^>]+>", "", body)),
        "cover": os.path.basename(cp) if cp else None,
        "risk": risk_check(title + re.sub(r"<[^>]+>", "", body)),
    }
    with open(os.path.join(OUT, cid, "note.json"), "w", encoding="utf-8") as f:
        json.dump(note, f, ensure_ascii=False, indent=2)

    flag = ("  ⚠ 风险词:%s" % note["risk"]) if note["risk"] else ""
    print("  ✓ %s《%s》正文 %d 字 封面:%s%s"
          % (cid, title, note["body_len"], "有" if cp else "缺", flag))
    return True


def build_all():
    ids = list_ids()
    print("build 全部 %d 篇 → out/bili/" % len(ids))
    ok = 0
    for cid in ids:
        if build_one(cid):
            ok += 1
    print("完成 %d/%d" % (ok, len(ids)))


# --------------------------------------------------------------------------
# probe：连 9222 dump 投稿页 DOM，校准 SEL
# --------------------------------------------------------------------------
def probe():
    if None in SEL.values():
        print("（SEL 当前为占位，跑 probe 拿真实选择器后填 scripts/bilibili_publish.py）")
    cdp = wp.CDP(PUB_PORT)
    tid = None
    for t in cdp.list_targets():
        if t.get("type") == "page" and "bilibili.com" in (t.get("url") or ""):
            tid = t["id"]
            break
    if not tid:
        tid = cdp.new_target(BILI_ARTICLE)["id"]
        time.sleep(6)
    sub = wp.CDP(PUB_PORT)
    sub.connect_target(tid)
    sub.send("Page.navigate", {"url": BILI_ARTICLE})
    time.sleep(15)
    ctxs = sub.collect_contexts(timeout=15)
    print("=== contexts ===")
    for c in ctxs:
        print("  ctx %s %s name=%s" % (c["id"], "default" if c["isDefault"] else "iframe", c.get("name")))
    for c in ctxs:
        cid = c["id"]
        try:
            d = sub.eval("""(function(){
              function cls(e){ return (e.className||'').toString().slice(0,60); }
              return {
                edits: [].slice.call(document.querySelectorAll('[contenteditable]')).map(function(e){
                  return {cls:cls(e), tag:e.tagName};
                }).slice(0,8),
                inputs: [].slice.call(document.querySelectorAll('textarea,input')).map(function(e){
                  return {ph:(e.getAttribute('placeholder')||''), cls:cls(e)};
                }).slice(0,8),
                btns: [].slice.call(document.querySelectorAll('button')).map(function(e){
                  return (e.innerText||'').trim();
                }).filter(Boolean).slice(0,20)
              };
            })()""", context_id=cid)
        except Exception as ex:
            d = "eval err: %s" % ex
        print("--- ctx %s (%s) ---" % (cid, "default" if c["isDefault"] else "iframe"))
        print(json.dumps(d, ensure_ascii=False))


# --------------------------------------------------------------------------
# publish：填一稿进专栏投稿页并存草稿
# --------------------------------------------------------------------------
BILI_NEW_ARTICLE = BILI_ARTICLE.rstrip("/") + "/new-article"
BILI_DRAFT_LIST = BILI_ARTICLE

# 同域 iframe 文档获取 + 编辑器就绪探测（在主文档 context 执行）
_JS_FDOC = """(function(kw){
  var f=[].slice.call(document.querySelectorAll('iframe')).find(
    function(f){return (f.src||'').indexOf(kw)>=0;});
  if(!f) return 'no-iframe';
  var d=f.contentDocument;
  if(!d) return 'no-doc';
  return d.body ? 'ready' : 'empty';
})"""


def _in_iframe_doc(sub, kw):
    """返回 (kind, doc)：kind = 'draft'|'editor'|'none'，doc 是同域 iframe 文档。"""
    r = sub.eval("""(function(kw){
      var f=[].slice.call(document.querySelectorAll('iframe')).find(
        function(f){return (f.src||'').indexOf(kw)>=0;});
      if(!f) return 'no-iframe';
      var d=f.contentDocument;
      return d && d.body ? 'ready' : 'no-doc';
    })(""" + json.dumps(kw) + ")", refresh_context=True)
    if r != "ready":
        return "none", None
    return ("draft" if kw == "read-draft" else "editor"), None


def _open_existing_draft(sub, keys):
    """草稿箱里点标题卡片的「编辑」，返回是否成功（会换到编辑器页）。

    keys 是**多个候选前缀**（按序试）：改过标题的稿在草稿箱里存的是旧标题
    （实测 prosp 首版「PROSP：把 B2B 销售的一整天…」vs 新版「月收$128K：…」），
    所以除新标题前缀外还要带上 case id（英文名 id 往往就是旧标题开头）。
    """
    for key in keys:
        r = sub.eval("""(function(t){
          var f=[].slice.call(document.querySelectorAll('iframe')).find(
            function(f){return (f.src||'').indexOf('read-draft')>=0;});
          if(!f||!f.contentDocument) return 'no-iframe';
          var d=f.contentDocument;
          // 直接在 .draft-card 里找，别从标题往上爬 —— 爬会落到图片占位
          // 这类更小的子元素上（它们文本相同但没有编辑按钮）。
          var card=[].slice.call(
            d.querySelectorAll('.draft-card, [class*=draft-card]')).find(
            function(c){return (c.innerText||'').indexOf(t)>=0;});
          if(!card) return 'no-card';
          var btn=card.querySelector(
            '.draft-card_action-edit, [class*=action-edit]');
          if(!btn) return 'no-edit-btn';
          btn.click();
          return 'clicked';
        })(""" + json.dumps(key) + ")", refresh_context=True)
        if r == "clicked":
            return True
    return False


def _need_sel():
    miss = [k for k in REQUIRED_SEL if not SEL.get(k)]
    if miss:
        raise SystemExit("SEL 缺: %s —— 先 `probe` 拿真实选择器填进脚本" % miss)


def _open_editor_tab(cdp):
    """找/开 B站 tab，导航到新建专栏页，返回 (sub, tid)。"""
    tid = None
    for t in cdp.list_targets():
        if t.get("type") == "page" and "member.bilibili.com" in (t.get("url") or ""):
            tid = t["id"]
            break
    if not tid:
        tid = cdp.new_target(BILI_NEW_ARTICLE)["id"]
    sub = wp.CDP(PUB_PORT)
    sub.connect_target(tid)
    if "new-article" not in (sub.eval("location.href") or ""):
        sub.send("Page.navigate", {"url": BILI_NEW_ARTICLE})
        time.sleep(8)
    return sub, tid


def _wait_iframe_ready(sub, kw, probe_sel, timeout=25):
    """轮询等 <iframe src~kw> 里的文档渲染出 probe_sel。"""
    end = time.time() + timeout
    while time.time() < end:
        r = sub.eval(_JS_FDOC + "(" + json.dumps(kw) + ")")
        if r == "ready":
            # 再确认目标元素已渲染
            r2 = sub.eval("(function(kw,s){"
                          "var f=[].slice.call(document.querySelectorAll('iframe'))"
                          ".find(function(f){return (f.src||'').indexOf(kw)>=0;});"
                          "return f&&f.contentDocument?"
                          "!!f.contentDocument.querySelector(s):false;})("
                          + json.dumps(kw) + "," + json.dumps(probe_sel) + ")")
            if r2 is True or r2 == "true" or r2 is True:
                return True
        time.sleep(1.5)
    return False


def _iframe_click(sub, kw, sel, timeout=8):
    """在 src 含 kw 的同域 iframe 里点 sel（先 JS click，失败回退真实坐标点击）。"""
    js = ("(function(kw,s){"
          "var f=[].slice.call(document.querySelectorAll('iframe'))"
          ".find(function(f){return (f.src||'').indexOf(kw)>=0;});"
          "if(!f||!f.contentDocument) return 'no-iframe';"
          "var el=f.contentDocument.querySelector(s);"
          "if(!el) return 'none';"
          "el.scrollIntoView({block:'center'});"
          "el.click();"
          "return 'clicked';})(" + json.dumps(kw) + "," + json.dumps(sel) + ")")
    end = time.time() + timeout
    while time.time() < end:
        r = sub.eval(js)
        if r == "clicked":
            return True
        time.sleep(1.2)
    return False


def publish_one(cid, dry=False, replace=False):
    _need_sel()
    html = load_article_html(cid)
    if not html:
        print("  ✗ 找不到 out/articles/%s.html" % cid)
        return False
    title = make_title(cid, html)
    body = clean_body(html)
    cp = cover_path(cid)
    plain = re.sub(r"<[^>]+>", "", body)
    risk = risk_check(title + plain)
    if risk:
        print("  ✗ 风险词未清干净：%s —— 先修 clean_body/LEADOUT_KW" % risk)
        return False
    if len(plain) < 200:
        print("  ✗ 正文仅 %d 字（B站专栏建议 ≥300），跳过" % len(plain))
        return False

    cdp = wp.CDP(PUB_PORT)
    sub, tid = _open_editor_tab(cdp)

    try:
        if dry:
            ok1 = _wait_iframe_ready(sub, "read-draft", SEL["new_creation"], 20)
            print("  [dry] 入口 iframe 就绪: %s" % ("✓" if ok1 else "✗"))
            print("  [dry] 标题=%s 正文=%s 存草稿文本=%r（未实际填写）"
                  % (SEL["title"], SEL["body"], SEL["draft_text"]))
            return ok1

        # 0) replace：草稿箱点该标题卡片的「编辑」，进已有草稿覆盖
        if replace:
            if not _wait_iframe_ready(sub, "read-draft", SEL["new_creation"], 25):
                print("  ✗ 草稿箱 iframe 没就绪")
                return False
            # 候选键：新标题前 8 字、case id、case id 首段大写（PROSP）
            cid_up = cid.upper()
            keys = [title[:8], cid, cid_up, cid_up.split("-")[0]]
            if not _open_existing_draft(sub, [k for k in keys if k]):
                print("  ✗ 草稿箱里没找到《%s》的编辑入口（试过 %s）"
                      % (title, "/".join(k for k in keys if k)))
                return False
            time.sleep(6)

        # 1) 非 replace：创作指南页 → 点「新的创作」
        else:
            if not _wait_iframe_ready(sub, "read-draft", SEL["new_creation"], 25):
                print("  ✗ 入口 iframe（创作指南）没出来")
                return False
            if not _iframe_click(sub, "read-draft", SEL["new_creation"]):
                print("  ✗ 「新的创作」点不到")
                return False

        # 2) 等编辑器 iframe（read-editor）就绪
        if not _wait_iframe_ready(sub, "read-editor", SEL["title"], 30):
            print("  ✗ 编辑器 iframe 没就绪（title-input 没出现）")
            return False

        # 2b) replace 模式：先清空原有标题/正文，否则新内容会叠加在旧草稿上
        if replace:
            cleared = sub.eval(
                "(function(kw,ts,bs){"
                "var f=[].slice.call(document.querySelectorAll('iframe'))"
                ".find(function(f){return (f.src||'').indexOf(kw)>=0;});"
                "var d=f&&f.contentDocument?f.contentDocument:null;"
                "if(!d) return 'no-iframe';"
                "var t=d.querySelector(ts), b=d.querySelector(bs);"
                "if(t){t.focus();t.value='';"
                "t.dispatchEvent(new Event('input',{bubbles:true}));}"
                "if(b){b.focus();"
                "d.execCommand('selectAll');d.execCommand('delete');"
                "d.execCommand('insertHTML', false, '<p></p>');}"
                "return 'cleared';})(" +
                json.dumps("read-editor") + "," + json.dumps(SEL["title"]) +
                "," + json.dumps(SEL["body"]) + ")", refresh_context=True)
            print("    清空旧内容: %s" % cleared)
            time.sleep(1.5)

        # 3) 填标题：聚焦 textarea → Input.insertText（真实键入，框架必收）
        focus_js = ("(function(kw,s){"
                    "var f=[].slice.call(document.querySelectorAll('iframe'))"
                    ".find(function(f){return (f.src||'').indexOf(kw)>=0;});"
                    "var el=f&&f.contentDocument?"
                    "f.contentDocument.querySelector(s):null;"
                    "if(!el) return 'none';"
                    "el.focus();"
                    "return 'focused';"
                    "})(" + json.dumps("read-editor") + "," +
                    json.dumps(SEL["title"]) + ")")
        if sub.eval(focus_js) != "focused":
            print("  ✗ 标题框聚焦失败")
            return False
        sub.send("Input.insertText", {"text": title})
        time.sleep(1)

        # 4) 灌正文：聚焦 ProseMirror → execCommand('insertHTML')（同头条范式）
        insert_js = ("(function(kw,s,html){"
                     "var f=[].slice.call(document.querySelectorAll('iframe'))"
                     ".find(function(f){return (f.src||'').indexOf(kw)>=0;});"
                     "var d=f.contentDocument;"
                     "var el=d.querySelector(s);"
                     "if(!el) return 'none';"
                     "el.focus();"
                     "d.execCommand('insertHTML', false, html);"
                     "return JSON.stringify({len:el.textContent.length});"
                     "})(" + json.dumps("read-editor") + "," +
                     json.dumps(SEL["body"]) + "," + json.dumps(body) + ")")
        r = sub.eval(insert_js)
        try:
            blen = json.loads(r).get("len", 0)
        except (ValueError, TypeError):
            print("  ✗ 正文灌入失败: %s" % str(r)[:80])
            return False
        time.sleep(2)

        # 5) 点「保存为草稿」
        save_js = ("(function(kw,txt){"
                   "var f=[].slice.call(document.querySelectorAll('iframe'))"
                   ".find(function(f){return (f.src||'').indexOf(kw)>=0;});"
                   "if(!f||!f.contentDocument) return 'no-iframe';"
                   "var btn=[].slice.call(f.contentDocument.querySelectorAll("
                   "'button, a, [role=button]')).find("
                   "function(b){return (b.innerText||'').trim()===txt;});"
                   "if(!btn) return 'none';"
                   "btn.click();"
                   "return 'clicked';})(" +
                   json.dumps("read-editor") + "," + json.dumps(SEL["draft_text"]) + ")")
        clicked = False
        end = time.time() + 10
        while time.time() < end:
            r = sub.eval(save_js)
            if r == "clicked":
                clicked = True
                break
            time.sleep(1.5)
        if not clicked:
            print("  ✗ 「保存为草稿」按钮没找到")
            return False
        time.sleep(4)  # 等保存请求发出/toast

        print("  ✓ 标题《%s》正文 %d 字（编辑器实测 %d 字）已填，点了保存为草稿%s"
              % (title, len(plain), blen, "（封面未传，人工核对）" if not cp else ""))
        return True
    finally:
        # 留着 tab 让人工核对刚保存的草稿页；连续批量时关掉防止堆积
        pass


def publish_all(replace=False):
    ids = list_ids()
    print("publish 全部 %d 篇 → B站草稿箱%s" % (len(ids), "（覆盖已有草稿）" if replace else ""))
    ok = 0
    for i, cid in enumerate(ids, 1):
        print("[%d/%d] %s" % (i, len(ids), cid))
        if publish_one(cid, replace=replace):
            ok += 1
        time.sleep(3)
    print("完成 %d/%d" % (ok, len(ids)))


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="B站专栏发布")
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("build")
    b.add_argument("--all", action="store_true")
    b.add_argument("--case", default=None)
    sub.add_parser("probe")
    p = sub.add_parser("publish")
    p.add_argument("--all", action="store_true")
    p.add_argument("--case", default=None)
    p.add_argument("--dry", action="store_true")
    p.add_argument("--replace", action="store_true",
                   help="草稿箱点该标题卡片的「编辑」覆盖旧稿（改文案后重存用）")
    args = ap.parse_args()

    if args.cmd == "build":
        if args.case:
            build_one(args.case)
        else:
            build_all()
    elif args.cmd == "probe":
        probe()
    elif args.cmd == "publish":
        if args.case:
            publish_one(args.case, dry=args.dry, replace=args.replace)
        else:
            publish_all(replace=args.replace)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
