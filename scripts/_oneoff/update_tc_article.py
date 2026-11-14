#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""更新腾讯云「已发布文章」的正文（编辑页是路径式 `write-new/<articleId>`）。

为什么要单开一支：`rewrite_yun_draft.py` 只认 draftId（`write-new?draftId=xxx`）。
已发布文章的编辑入口是路径式 `write-new/<articleId>`；2026-11-14 实测：
`write-new?articleId=xxx` 打开是**空白编辑器**（标题空、正文空、不报错），
照那个地址写正文会发出一篇空稿，所以这里把入口写死成路径式。

实测（2026-11-14，Minetest 2784172）：
  - 公开页「编辑」是 `a.mod-header2__operate`（href 为空，JS 路由），点完跳
    `https://cloud.tencent.com/developer/article/write-new/<articleId>`
  - 编辑页回填旧标题与旧正文（`.cm-content` innerText 619 字）
  - 提交按钮是「去发布」+「存草稿」；发布抽屉的四项沿用原文章
  - 正文替换仍只能走 `.cm-content` 全选 + `Input.insertText`
    （「导入MD」不替换已有正文，见 rewrite_yun_draft 的实测注记）

用法：
  # 只替换正文并打印抽屉字段状态，不提交
  python scripts/_oneoff/update_tc_article.py --article 2784172 \
      --file <md> --case ext-ep-011-minetest --go
  # 替换 + 提交
  python scripts/_oneoff/update_tc_article.py --article 2784172 \
      --file <md> --case ext-ep-011-minetest --go --confirm
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
           "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

import yun_draft_cdp as Y  # noqa: E402
import yun_finalize as F   # noqa: E402
from csdn_publish import split_md  # noqa: E402

EDITOR = "https://cloud.tencent.com/developer/article/write-new/%s"
PUBLIC = "https://cloud.tencent.com/developer/article/%s"
SEL_TITLE = "textarea.cdc-article-editor__title-input"

ap = argparse.ArgumentParser()
ap.add_argument("--article", required=True, help="已发布文章的 articleId（7 位）")
ap.add_argument("--file", required=True, help="新稿 markdown（首行 # 标题）")
ap.add_argument("--case", required=True)
ap.add_argument("--go", action="store_true", help="真的替换正文（缺省只看差异）")
ap.add_argument("--confirm", action="store_true", help="替换后点「确认发布」提交")
ap.add_argument("--cover", default="", help="封面文件（缺省 out/toutiao/<case>/cover.png）")
a = ap.parse_args()

if not a.cover:
    a.cover = os.path.join(ROOT, "out", "toutiao", a.case, "cover.png")
print("封面文件:", a.cover, "| 存在:", os.path.isfile(a.cover))

raw = open(a.file, encoding="utf-8").read()
title, body = split_md(raw)
if not title or not body.strip():
    raise SystemExit("✗ 稿件标题或正文为空：%s" % a.file)

print("目标文章:", a.article)
print("新标题:", title, "（%d 字）" % len(title))
# 🚨 腾讯云靠 content 开头的 `<!--markdown-->` 判断这是 markdown（建稿接口体里就是
# `<!--markdown-->\n正文`）。漏了这个前缀，编辑器会把正文当**纯文本**存：
# 2026-11-14 第一次更新就踩了——线上直接显示字面的 `## 先说清楚` 和 `\n\n`，
# 段落与标题全不渲染。所以插入内容必须带前缀。
body_wire = "<!--markdown-->\n" + body
print("新正文:", len(body), "字符（wire +%d）" % (len(body_wire) - len(body)),
      "/ 首句:", body.strip().split("\n")[0][:40])

proc, cdp = Y._connect()
print("9228:", "复用常驻" if proc is None else "本次新起")

cdp.open(EDITOR % a.article)
time.sleep(9)
Y._wait_for(cdp, "!!document.querySelector(%s)" % json.dumps(SEL_TITLE), timeout=60)

url_now = cdp.eval("location.href") or ""
old_title = cdp.eval("(function(){var e=document.querySelector(%s);return e?e.value:'';})()"
                     % json.dumps(SEL_TITLE)) or ""
old_body = cdp.eval("(document.querySelector('.cm-content')||{innerText:''}).innerText") or ""
print("编辑页 URL:", url_now[:110])
print("旧标题:", repr(old_title[:50]), "| 旧正文长度:", len(old_body))

if not old_title or len(old_body) < 50:
    raise SystemExit("✗ 编辑页没回填旧内容（多半进错了入口，别在这里写正文）")

bak = os.path.join(ROOT, "tmp", "yun_article_backup_%s.json" % a.article)
os.makedirs(os.path.dirname(bak), exist_ok=True)
with open(bak, "w", encoding="utf-8") as f:
    json.dump({"article": a.article, "title": old_title, "body": old_body,
               "url": url_now, "at": time.strftime("%Y-%m-%d %H:%M:%S")},
              f, ensure_ascii=False, indent=1)
print("✓ 旧稿备份:", bak)

if not a.go:
    print("\n（干跑）新旧差异：旧 %d → 新 %d 字符；加 --go 才替换。"
          % (len(old_body), len(body)))
    sys.exit(0)

# ---- 1) 替换正文：.cm-content 全选 + insertText，同帧读回 ----
r = cdp.eval("""
(function(){
  var e = document.querySelector('.cm-content');
  if (!e) return JSON.stringify({err:'no cm-content'});
  e.focus();
  document.execCommand('selectAll', false, null);
  document.execCommand('insertText', false, %s);
  var t = e.innerText || '';
  return JSON.stringify({len:t.length, head:t.slice(0,70), tail:t.slice(-70)});
})()""" % json.dumps(body_wire))
got = {}
try:
    got = json.loads(r) if isinstance(r, str) else {}
except Exception:
    got = {}
print("正文替换: len=%s head=%r tail=%r"
      % (got.get("len"), got.get("head", "")[:40], got.get("tail", "")[:40]))
head12 = body.strip().split("\n")[0][:12]
if head12 not in got.get("head", ""):
    raise SystemExit("✗ 编辑器正文没换成新稿，停止（未提交）")
if body.strip()[-40:] not in (got.get("tail", "") + got.get("head", "")):
    raise SystemExit("✗ 正文结尾不是新稿（疑似只插入一部分），停止")
# 🚨 别用「旧稿开头是否出现」当追加判据：本稿新旧首句完全相同（同一句引言），
# 2026-11-14 就这么把一次成功的替换判成「追加」。CodeMirror 是虚拟滚动，
# 同帧只能拿到首尾各 70 字，没法全文比对 ⇒ 用**长度**判追加/替换：
# 追加会让 len ≈ 旧+新（4471），替换才是 ≈ 新稿（3852）。
nlen = got.get("len") or 0
base = max(len(body), len(body_wire))
if nlen > base * 1.15 or nlen < base * 0.85:
    raise SystemExit("✗ 编辑器长度 %d 与预期 %d 不符（疑似追加或截断），停止"
                     % (nlen, base))
print("长度校验: 编辑器 %d ≈ 预期 %d（旧稿 %d，未被追加）"
      % (nlen, base, len(old_body)))

# 标题一致就不动（改标题会让文章重新过标题审核，没必要）
if old_title.strip() != title.strip():
    print("标题注入:", Y._fill_text(cdp, SEL_TITLE, title))
else:
    print("标题一致，不动")

# ---- 2) 开发布抽屉 ----
pos = F._pos_of(cdp, """
  [].filter.call(document.querySelectorAll('button,[role=button],a,span,div'),
    function(e){ return e.offsetParent !== null
      && (e.textContent||'').trim() === '去发布'; })[0]""")
if not isinstance(pos, dict):
    raise SystemExit("✗ 没找到「去发布」按钮")
F._click_real(cdp, pos)
time.sleep(6)

drawer = cdp.eval("""(function(){
  var d = document.querySelector('.editor-publish-drawer');
  if (!d) return JSON.stringify({open:false});
  var formers = document.querySelectorAll('.t-radio__former');
  var orig = formers.length ? formers[0] : null;
  var origOn = orig ? !!orig.checked : null;
  var tags = [];
  document.querySelectorAll('.cdc-tags-input__tag, .cdc-tag').forEach(function(t){
    var s = (t.innerText||'').trim(); if (s) tags.push(s.slice(0,12));
  });
  var tagInput = document.querySelector('.cdc-tags-input__input');
  var abs = document.querySelector('textarea.editor-publish-drawer__textarea-main');
  var cover = document.querySelector('.editor-publish-drawer__img-cover');
  return JSON.stringify({
    open: true,
    has_origin_ctrl: formers.length > 0,
    has_cover_input: !!document.querySelector('input.img-cover-input'),
    origin_checked: origOn,
    tags: tags,
    tag_input_value: tagInput ? tagInput.value : null,
    abstract_len: abs ? (abs.value||'').length : null,
    cover: cover ? (cover.className.indexOf('is-empty') >= 0 ? 'empty' : 'set') : 'no-slot'
  });
})()""")
print("发布抽屉:", drawer)
st = json.loads(drawer) if isinstance(drawer, str) else {}
if not st.get("open"):
    raise SystemExit("✗ 发布抽屉没打开")

# 🚨 编辑已发布文章时抽屉里的封面是 empty（实测 2026-11-14）：
# 直接「确认发布」会把线上封面清掉，事后补图还会让文章重进审核 ⇒ 先注入。
cover_state = st.get("cover")
inject_done, inject_state = False, ""
if cover_state != "set" and st.get("has_cover_input"):
    cst, cinfo = F.inject_cover(cdp, a.cover)
    inject_done, inject_state = True, str(cst)
    print("封面注入:", cst, "|", str(cinfo)[:90])
    time.sleep(3)
    # ⚠ 别再用 `.is-empty` 复查：文档实测该类不可信（staged 时仍带 is-empty）。
    # 判据只认 inject_cover 的三态：landed / staged 算通过。

origin_ok = (st.get("origin_checked") is True) or (not st.get("has_origin_ctrl"))
if st.get("has_origin_ctrl") and st.get("origin_checked") is not True:
    # 与发布链路同法：点一下再读 .checked（.t-radio__former 本身是 input，没有子 input，
    # 2026-11-14 一开始按 className/sub-input 判，恒读回 null）
    r = cdp.eval("""(function(){var r=document.querySelectorAll('.t-radio__former');
        if(!r.length) return null; r[0].click(); return !!r[0].checked;})()""")
    print("原创选项: 点击后 =", r)
    st["origin_checked"] = r
    origin_ok = (r is True)
cover_ok = (cover_state == "set") or (inject_done and inject_state in ("landed", "staged"))
tags_ok = bool(st.get("tags") or st.get("tag_input_value"))
ok_fields = origin_ok and cover_ok and tags_ok
print("抽屉四要素齐全:", bool(ok_fields),
      "（原创=%s（控件=%s）/ 标签=%s / 摘要=%s字 / 封面=%s）"
      % (st.get("origin_checked"), st.get("has_origin_ctrl"),
         st.get("tags") or st.get("tag_input_value"),
         st.get("abstract_len"), cover_state))

if not a.confirm:
    print("\n（未提交）要提交加 --confirm。")
    sys.exit(0)
if not ok_fields:
    raise SystemExit("✗ 抽屉字段不全，点「确认发布」会静默存草稿，先补齐再提交")

# ---- 3) 确认发布 ----
pos = F._pos_of(cdp, """
  [].filter.call(document.querySelectorAll('button,[role=button],a,span,div'),
    function(e){ return e.offsetParent !== null
      && (e.textContent||'').trim() === '确认发布'; })[0]""")
if not isinstance(pos, dict):
    raise SystemExit("✗ 没找到「确认发布」")
F._click_real(cdp, pos)
time.sleep(12)
print("提交后 URL:", (cdp.eval("location.href") or "")[:120])

# ---- 4) 独立回读：另起匿名请求读公开页（不信页内乐观态） ----
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
req = urllib.request.Request(PUBLIC % a.article,
                             headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
try:
    resp = op.open(req, timeout=30)
    htmlp = resp.read().decode("utf-8", "ignore")
    print("公开页: HTTP %s / %d bytes" % (resp.status, len(htmlp)))
    probes = ["MapBlock", "mono-block", "IrrlichtMt", "RUN_IN_PLACE", "Mapchunk"]
    hits = {p: (p in htmlp) for p in probes}
    print("新稿独有词命中:", hits)
    print("旧稿残留（现名 Luanti 那句）:", "Minetest（现名 Luanti）" in htmlp)
    # 🚨 正文有没有被当成 markdown 渲染：没渲染时页面会直接吐字面的 `## 标题` 和 \n\n
    rendered = ("<h2" in htmlp) and ("\\n\\n## " not in htmlp) and ("\\n\\n" not in htmlp)
    print("markdown 已渲染:", bool(rendered),
          "（含 <h2>=%s / 字面 \\n\\n##=%s）"
          % ("<h2" in htmlp, "\\n\\n## " in htmlp))
    ok = all(hits.values()) and rendered
    print("VERDICT:", "NEW_BODY_LIVE" if ok else "CHECK_NEEDED")
except urllib.error.HTTPError as e:
    print("公开页 HTTPError %s ⇒ 可能已重新进审核（按三态记 auditing，别当已发布）" % e.code)
except Exception as e:
    print("公开页读失败:", type(e).__name__, e)
