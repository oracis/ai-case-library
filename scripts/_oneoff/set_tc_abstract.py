#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只改腾讯云「已发布文章」的摘要，不动正文（PRD #173）。

与 `_oneoff/update_tc_article.py` 的区别：那支是替换正文（`.cm-content`
全选 + insertText）+ 顺带补四要素；本支**不碰正文**，只把发布抽屉里的
摘要 textarea 换成新文本再提交。

为什么要单开：摘要只在发布抽屉里
（`textarea.editor-publish-drawer__textarea-main`，React 受控，必须用
value setter + input 事件），编辑页正文区没有摘要字段；而替换正文那支会
连带做长度校验、封面注入等动作，改摘要时全是多余风险。

🚨 提交 = 重新走发布流程 ⇒ 文章重新进审核（即便只改摘要一个字）。
   宁可先干跑把新旧摘要打印出来给人看，再 `--go --confirm`。

用法：
  # 干跑：只读抽屉现状（摘要、字数上限、四要素），不填不提交
  python scripts/_oneoff/set_tc_abstract.py --article 2784172
  # 填入新摘要但仍不提交
  python scripts/_oneoff/set_tc_abstract.py --article 2784172 --text-file <txt> --go
  # 填 + 提交 + 独立回读
  python scripts/_oneoff/set_tc_abstract.py --article 2784172 --text-file <txt> --go --confirm
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
           "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

import yun_draft_cdp as Y  # noqa: E402
import yun_finalize as F   # noqa: E402

EDITOR = "https://cloud.tencent.com/developer/article/write-new/%s"
PUBLIC = "https://cloud.tencent.com/developer/article/%s"
SEL_TITLE = "textarea.cdc-article-editor__title-input"
SEL_ABS = "textarea.editor-publish-drawer__textarea-main"

ap = argparse.ArgumentParser()
ap.add_argument("--article", required=True, help="已发布文章 articleId")
ap.add_argument("--text", default="", help="新摘要（与 --text-file 二选一）")
ap.add_argument("--text-file", default="", help="新摘要文件（UTF-8 纯文本）")
ap.add_argument("--cover", default="",
                help="封面文件（抽屉封面若为 empty 才用；缺省 out/toutiao/<case>/cover.png）")
ap.add_argument("--go", action="store_true", help="真的填入摘要（缺省只读抽屉）")
ap.add_argument("--confirm", action="store_true", help="填完点「确认发布」提交")
a = ap.parse_args()

new_abs = a.text
if a.text_file:
    new_abs = open(a.text_file, encoding="utf-8").read().strip()
new_abs = re.sub(r"\s*\n\s*", " ", new_abs).strip()

proc, cdp = Y._connect()
print("9228:", "复用常驻" if proc is None else "本次新起")

cdp.open(EDITOR % a.article)
time.sleep(9)
Y._wait_for(cdp, "!!document.querySelector(%s)" % json.dumps(SEL_TITLE),
            timeout=60)

url_now = cdp.eval("location.href") or ""
title = cdp.eval("(function(){var e=document.querySelector(%s);"
                 "return e?e.value:'';})()" % json.dumps(SEL_TITLE)) or ""
blen = cdp.eval("(document.querySelector('.cm-content')||{innerText:''})"
                ".innerText.length")
print("编辑页:", url_now[:100])
print("标题:", repr(title), "| 正文（视口内）", blen, "字")
if not title:
    raise SystemExit("✗ 编辑页没回填标题（入口不对，停下来）")

# ---- 开抽屉 ----
pos = F._pos_of(cdp, """
  [].filter.call(document.querySelectorAll('button,[role=button],a,span,div'),
    function(e){ return e.offsetParent !== null
      && (e.textContent||'').trim() === '去发布'; })[0]""")
if not isinstance(pos, dict):
    raise SystemExit("✗ 没找到「去发布」按钮")
F._click_real(cdp, pos)
time.sleep(6)

state = cdp.eval("""(function(){
  var d = document.querySelector('.editor-publish-drawer');
  if (!d) return JSON.stringify({open:false});
  var abs = document.querySelector(%s);
  var formers = document.querySelectorAll('.t-radio__former');
  var tags = [];
  document.querySelectorAll('.cdc-tags-input__tag, .cdc-tag').forEach(function(t){
    var s = (t.innerText||'').trim(); if (s) tags.push(s.slice(0,14));
  });
  var tagInput = document.querySelector('.cdc-tags-input__input');
  var cover = document.querySelector('.editor-publish-drawer__img-cover');
  return JSON.stringify({
    open: true,
    abstract: abs ? abs.value : null,
    abstract_len: abs ? (abs.value||'').length : null,
    abstract_max: abs ? abs.maxLength : null,
    has_origin_ctrl: formers.length > 0,
    origin_checked: formers.length ? !!formers[0].checked : null,
    tags: tags,
    tag_input_value: tagInput ? tagInput.value : null,
    cover_class: cover ? cover.className : 'no-slot',
    has_cover_input: !!document.querySelector('input.img-cover-input')
  });
})()""" % json.dumps(SEL_ABS))
st = json.loads(state) if isinstance(state, str) else {}
if not st.get("open"):
    raise SystemExit("✗ 发布抽屉没打开")
print("\n===== 抽屉现状 =====")
print("当前摘要(%s字/上限%s): %s"
      % (st.get("abstract_len"), st.get("abstract_max"), st.get("abstract")))
print("原创: %s（有控件=%s）| 标签: %s | 封面: %s"
      % (st.get("origin_checked"), st.get("has_origin_ctrl"),
         st.get("tags") or st.get("tag_input_value"), st.get("cover_class")))

if not new_abs:
    print("\n（干跑，未给 --text/--text-file）")
    sys.exit(0)

print("\n===== 拟改摘要 =====")
print("旧(%d字): %s" % (st.get("abstract_len") or 0, st.get("abstract")))
print("新(%d字): %s" % (len(new_abs), new_abs))
mx = st.get("abstract_max")
if isinstance(mx, int) and mx > 0 and len(new_abs) > mx:
    raise SystemExit("✗ 新摘要 %d 字超过平台上限 %d，先删短" % (len(new_abs), mx))

if not a.go:
    print("\n（干跑，未写入；加 --go 填入）")
    sys.exit(0)

# ---- 填摘要：React 受控 ⇒ value setter + input 事件 ----
n = cdp.eval("""
(function(){var t=document.querySelector(%s);
  if(!t) return -1;
  var s=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,
                                        'value').set;
  s.call(t,%s);
  t.dispatchEvent(new Event('input',{bubbles:true}));
  return t.value.length;})()""" % (json.dumps(SEL_ABS), json.dumps(new_abs)))
print("摘要写入:", n, "字")
if not isinstance(n, int) or n <= 0:
    raise SystemExit("✗ 摘要注入失败")

back = cdp.eval("(document.querySelector(%s)||{}).value" % json.dumps(SEL_ABS))
if (back or "").strip() != new_abs.strip():
    raise SystemExit("✗ 抽屉摘要回读不一致，停止（未提交）")
print("✓ 抽屉摘要回读一致")

# ---- 四要素（缺一项点确认发布就是静默存草稿）----
cover_state = "set" if "is-empty" not in (st.get("cover_class") or "") else "empty"
if cover_state != "set" and a.cover and os.path.isfile(a.cover):
    cst, cinfo = F.inject_cover(cdp, a.cover)
    print("封面注入:", cst, "|", str(cinfo)[:80])
    cover_state = cst
elif cover_state != "set":
    print("⚠ 抽屉封面为空且没给可用 --cover：提交会清掉线上封面")

origin_ok = (st.get("origin_checked") is True) or (not st.get("has_origin_ctrl"))
if st.get("has_origin_ctrl") and st.get("origin_checked") is not True:
    r = cdp.eval("""(function(){var r=document.querySelectorAll('.t-radio__former');
        if(!r.length) return null; r[0].click(); return !!r[0].checked;})()""")
    print("原创选项: 点击后 =", r)
    origin_ok = (r is True)
tags_ok = bool(st.get("tags") or st.get("tag_input_value"))
cover_ok = cover_state in ("set", "landed", "staged")
print("四要素:", "齐全" if (origin_ok and cover_ok and tags_ok) else "不全",
      "(原创=%s/标签=%s/封面=%s)" % (origin_ok, tags_ok, cover_state))

if not a.confirm:
    print("\n（未提交；加 --confirm 才点确认发布）")
    sys.exit(0)
if not (origin_ok and cover_ok and tags_ok):
    raise SystemExit("✗ 四要素不全，点确认发布只会静默存草稿")

# ---- 提交 ----
pos = F._pos_of(cdp, """
  [].filter.call(document.querySelectorAll('button,[role=button],a,span,div'),
    function(e){ return e.offsetParent !== null
      && (e.textContent||'').trim() === '确认发布'; })[0]""")
if not isinstance(pos, dict):
    raise SystemExit("✗ 没找到「确认发布」")
F._click_real(cdp, pos)
time.sleep(12)
print("提交后 URL:", (cdp.eval("location.href") or "")[:120])

# ---- 独立回读 ----
# 🚨 2026-11-14 实测踩到：**编辑页抽屉回填的是旧快照**。提交成功后再重开编辑页
#    点「去发布」，抽屉里读回的仍是旧摘要（174 字），会误判成 CHECK_NEEDED，
#    而同一刻公开页「概述」区已经是新摘要。
# 🚨 真正可信的判据只有公开页 `div.mod-header2__summary-content`（文章页顶部
#    「概述」就是摘要）。别用 `<meta name="description">` —— 那是平台运营文案，
#    与抽屉摘要**不同源**（实测抽屉 174 字 / meta 92 字，改了摘要它不变）。
print("\n独立回读：匿名 GET 公开页…")
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
req = urllib.request.Request(PUBLIC % a.article,
                             headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
live = ""
try:
    resp = op.open(req, timeout=30)
    html = resp.read().decode("utf-8", "ignore")
    print("公开页: HTTP %s / %d bytes" % (resp.status, len(html)))
    m = re.search(r'mod-header2__summary-content">(.*?)</div>', html, re.S)
    live = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""
    print("线上概述(%d字): %s" % (len(live), live[:200] or "(未读到)"))
    ok_head = new_abs[:20] in live
    ok_full = live.strip() == new_abs.strip()
    print("新摘要前 20 字命中:", ok_head, "| 全文一致:", ok_full)
    print("VERDICT:", "ABSTRACT_LIVE" if ok_full else (
        "CHECK_NEEDED" if not ok_head else "PARTIAL"))
except urllib.error.HTTPError as e:
    print("公开页 HTTPError %s ⇒ 已重新进审核（过后再核）" % e.code)
except Exception as e:                                     # noqa: BLE001
    print("公开页读失败:", type(e).__name__, e)

# 辅助：抽屉回填值（可能是旧快照，仅供参考，别当判据）
pos = F._pos_of(cdp, """
  [].filter.call(document.querySelectorAll('button,[role=button],a,span,div'),
    function(e){ return e.offsetParent !== null
      && (e.textContent||'').trim() === '去发布'; })[0]""")
if isinstance(pos, dict):
    F._click_real(cdp, pos)
    time.sleep(6)
    dr = cdp.eval("(document.querySelector(%s)||{}).value"
                  % json.dumps(SEL_ABS)) or ""
    print("（参考）编辑页抽屉摘要(%d字): %s" % (len(dr), dr[:80]))
    if dr.strip() != new_abs.strip():
        print("     ⚠ 抽屉不是新摘要 —— 已知的旧快照现象，以公开页概述为准")
