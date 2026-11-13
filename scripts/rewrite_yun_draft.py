# -*- coding: utf-8 -*-
"""在两朵云**已有草稿**上就地改稿（不新建），双回读通过后才登记台账。

用法：
  python -u -X utf8 scripts/rewrite_yun_draft.py --platform tencentcloud \
      --draft 338076 --file drafts/newbatch/tencentcloud/plausible-analytics.md \
      --case plausible-analytics [--go]

默认只备份旧稿 + 打印将改什么；--go 才写入平台侧。

⚠ 保存判据必须「独立列箱」：旧稿备份写 `tmp/`（不进 git），但**判据不能看
编辑器页内的乐观态** —— 11-11 腾讯云事故就是页内回读假绿、实际没落库，
把 klar 旧稿发成了 Mindustry 那篇。现在点保存前 hook fetch/XHR 记响应，
保存后导航离开编辑器页、重载首页连拉两次一致才算真值；不过就不登记台账、
不发表。发布侧 `yun_finalize.publish_tencent()` 不注入正文、只提交草稿 id，
所以本脚本的落库判定就是唯一防线。
"""
import argparse
import html
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import yun_draft_cdp as Y  # noqa: E402
import yun_finalize as F  # noqa: E402
from csdn_publish import split_md  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--platform", choices=["tencentcloud", "aliyun"], required=True)
ap.add_argument("--draft", required=True)
ap.add_argument("--file", required=True)
ap.add_argument("--case", required=True)
ap.add_argument("--go", action="store_true")
a = ap.parse_args()

src = a.file if os.path.isabs(a.file) else os.path.join(ROOT, a.file)
raw = open(src, encoding="utf-8").read()
title, body = split_md(raw)
if not title or not body:
    raise SystemExit("✗ 稿件标题或正文为空：%s" % src)  # 别用 assert，-O 会被剥离
head = body.strip().split("\n")[0][:24]
# 平台草稿列表的 plain 字段会把空格全部压掉（2026-11-08 实测：
# '> OpenTTD 是经典…' 回读成 'OpenTTD是经典…'）。判据必须归一化，
# 否则改稿明明成功也会假报「双回读未通过」。
norm = lambda s: "".join((s or "").split()).lstrip(">")  # noqa: E731


def old_only_probe(old, new, win=30):
    """从旧稿里挑第一个「新稿中不存在」的 win 字窗口，作为 OLD_GONE 探针。

    🚨 别再用「旧稿前 20 字是否还在」判 OLD_GONE：同一案例重写时首段常被
    原样沿用（11-13 headshotpro 实测），那个判据恒为 False ⇒ 平台正文明明
    已是新稿（标题已换、2518 字、新稿结尾在正文、旧稿专属段落消失），仍报
    「服务端仍是旧稿」的假红，把一次成功的改稿判成失败。
    """
    o, n = norm(old), norm(new)
    for i in range(0, max(1, len(o) - win), 10):
        seg = o[i:i + win]
        if len(seg) == win and seg not in n:
            return seg
    return ""

print("新标题:", title)
print("新正文首句:", head)
print("正文长度:", len(body))

proc, cdp = Y._connect()
print("9228:", "复用常驻" if proc is None else "本次新起")

# 🚨 PRD #136（2026-11-11 事故后补）：列表请求带 no-store + 时间戳，
# 否则编辑器页内的 fetch 可能命中页面乐观缓存 / 浏览器缓存，
# 表现为「脚本自报双回读全绿、平台侧其实没落库」。
LIST_TC = """
fetch("/developer/api/article/getUserArticleDrafts?_="+Date.now(),
      {method:"POST", credentials:"include", cache:"no-store",
       headers:{"Content-Type":"application/json"},
       body: JSON.stringify({page:1, pageSize:50, contentType:"rich"})})
  .then(function(x){return x.text();}).catch(function(e){return "ERR|"+e;})"""

HOOK_SAVES = """
(function(){
  window.__wb_saves=[];
  var O=window.fetch;
  window.fetch=function(u,o){
    var p=O.apply(this,arguments);
    try{
      var url=String((u && u.url) || u || '');
      if(/save|draft|article/i.test(url)){
        p.then(function(r){ try{ r.clone().text().then(function(t){
          window.__wb_saves.push({via:'fetch',url:url,status:r.status,
                                 body:String(t).slice(0,500)});}); }catch(e){} });
      }
    }catch(e){}
    return p;
  };
  var Op=XMLHttpRequest.prototype.open, S=XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open=function(m,u){this.__wb_url=String(u);
    return Op.apply(this,arguments);};
  XMLHttpRequest.prototype.send=function(b){
    var x=this;
    try{ x.addEventListener('loadend',function(){
      window.__wb_saves.push({via:'xhr',url:x.__wb_url,status:x.status,
                              body:String(x.responseText||'').slice(0,500)});
    }); }catch(e){}
    return S.apply(this,arguments);
  };
  return 'HOOKED';
})()"""
EP_ALI = "/developer/api/articleDraft/listDraft?pageSize=30&pageNum=1"
LIST_ALI = """
fetch(%s, {credentials:"include"})
  .then(function(x){return x.text().then(function(t){return x.status+"|"+t;});})
  .catch(function(e){return "ERR|"+e;})""" % json.dumps(EP_ALI)


def list_rows():
    if a.platform == "tencentcloud":
        r = cdp.eval(LIST_TC)
        if not isinstance(r, str) or r.startswith("ERR|"):
            raise SystemExit("腾讯云草稿箱读失败: %s" % str(r)[:200])
        d = json.loads(r)
        items = d.get("list") or d.get("data") or []
        if isinstance(items, dict):
            items = items.get("list") or []
        return [(str(x.get("draftId")), x.get("title") or "", x.get("plain") or "")
                for x in items if isinstance(x, dict)]
    r = cdp.eval(LIST_ALI)
    if not isinstance(r, str) or r.startswith("ERR|"):
        raise SystemExit("阿里云草稿箱读失败: %s" % str(r)[:200])
    payload = r.partition("|")[2]
    data = json.loads(payload).get("data") or []
    if isinstance(data, dict):
        data = data.get("list") or []
    return [(str(x.get("aid")), x.get("title") or "",
             html.unescape(x.get("content") or "")) for x in data if isinstance(x, dict)]


def independent_list_rows():
    """🚨 PRD #136 独立列箱硬闸（不认脚本自报 / 不认编辑器页内乐观态）。

    11-11 事故：改稿脚本在 `write-new?draftId=...` **编辑器页内** fetch 列表，
    拿到的是页面内存里的乐观值，自报「title=新稿、OLD_GONE、总数不变」全绿；
    换个进程打开首页再列箱，标题仍是旧稿 ⇒ 保存根本没落库，随后发出旧文。
    正解：保存后**导航离开编辑器页**、重新加载，在新页面上下文里拉两次，
    两次一致才算服务端真值。

    两朵云都必须离开编辑器页：
    - 腾讯云：回 write-new 编辑器首页。
    - 阿里云：回 developer.aliyun.com **首页**。不能用 `article/new` ——
      打开新编辑器有新建空草稿的风险，会把 NO_NEW_DRAFT 判据
      （len(rows_after)==len(rows_before)）打假红。
    """
    if a.platform == "tencentcloud":
        cdp.open("https://cloud.tencent.com/developer/article/write-new")
    else:
        cdp.open("https://developer.aliyun.com/")
    time.sleep(8)
    r1 = list_rows()
    time.sleep(2)
    r2 = list_rows()
    if r1 != r2:
        raise SystemExit("✗ 独立列箱两次结果不一致 ⇒ 服务端状态不稳定，判 FAIL")
    print("独立列箱: 已离开编辑器页重新加载，连拉两次一致（%d 条）" % len(r1))
    return r1


if a.platform == "tencentcloud":
    url = "https://cloud.tencent.com/developer/article/write-new?draftId=%s" % a.draft
else:
    url = "https://developer.aliyun.com/article/new?edit=%s" % a.draft
cdp.open(url)
time.sleep(7)
if a.platform == "tencentcloud":
    print("保存请求 hook:", cdp.eval(HOOK_SAVES))
if a.platform == "aliyun":
    F._close_disclaimer(cdp)

sel_title = ("textarea.cdc-article-editor__title-input"
             if a.platform == "tencentcloud" else "#title")
Y._wait_for(cdp, "!!document.querySelector(%s)" % json.dumps(sel_title), timeout=60)
old_title = cdp.eval("(function(){var e=document.querySelector(%s);"
                     "return e?(e.value||''):'';})()" % json.dumps(sel_title))
if a.platform == "tencentcloud":
    old_body = cdp.eval("(document.querySelector('.cm-content')||{innerText:''}).innerText")
else:
    old_body = cdp.eval("(function(){var e=document.querySelector('.mditor textarea.textarea');"
                        "return e?e.value:'';})()")
rows_before = list_rows()
old_row = [r for r in rows_before if r[0] == a.draft]
print("旧标题:", repr(old_title[:80]))
print("旧正文首句:", repr((old_body or "")[:60]))
print("草稿箱总数(改前):", len(rows_before), "｜目标行存在:", bool(old_row))
if not old_row:
    raise SystemExit("✗ 目标草稿不在列表里，拒绝改稿")

bak = os.path.join(ROOT, "tmp", "yun_draft_backup_%s_%s.json" % (a.platform, a.draft))
with open(bak, "w", encoding="utf-8") as f:
    json.dump({"draft": a.draft, "title": old_title, "body": old_body,
               "list_row": old_row[0], "at": time.strftime("%Y-%m-%d %H:%M:%S")},
              f, ensure_ascii=False, indent=1)
print("✓ 旧稿备份:", bak)

if not a.go:
    print("\n（干跑）加 --go 才覆盖。")
    sys.exit(0)

# ---- 覆盖 ----
print("标题注入:", Y._fill_text(cdp, sel_title, title))
if a.platform == "tencentcloud":
    # 实测：「导入MD」在已有草稿上不会替换正文（编辑器仍是旧文）。
    # 改成在 CodeMirror 的 contenteditable 里全选后插入，让它自己处理
    # 一次 beforeinput/input，等价于人工粘贴整篇新稿。
    r = cdp.eval("""
(function(){
  var e = document.querySelector('.cm-content');
  if (!e) return JSON.stringify({err:'no cm-content'});
  e.focus();
  document.execCommand('selectAll', false, null);
  document.execCommand('insertText', false, %s);
  // 🚨 必须在**同一帧**里读回：CodeMirror 是虚拟滚动，插入瞬间全文都在
  // DOM，几秒后只保留视口（实测 3432 字符 -> 2 秒后只剩 874，22 行 684 字符）。
  // 上一版在 sleep(2) 之后才校验，把一个成功的替换误判成失败。
  var t = e.innerText || '';
  return JSON.stringify({len:t.length, head:t.slice(0,70), tail:t.slice(-70)});
})()""" % json.dumps(body))
    print("正文替换:", r)
    got = {}
    try:
        got = json.loads(r) if isinstance(r, str) else {}
    except Exception:
        got = {}
    now = got.get("head", "") + got.get("tail", "")
    print("同帧回读: len=%s head=%r tail=%r"
          % (got.get("len"), got.get("head", "")[:40], got.get("tail", "")[:40]))
    if head[:12] not in got.get("head", ""):
        raise SystemExit("✗ 编辑器正文没换成新稿，停止（不点存草稿）")
    if body.strip()[-40:] not in (got.get("tail", "") + got.get("head", "")):
        raise SystemExit("✗ 正文结尾不是新稿（疑似只插入了一部分），停止")
    if old_body[:20] and old_body[:20] in now:
        raise SystemExit("✗ 新正文里仍含旧稿开头（疑似追加而非替换），停止")
    button = "存草稿"
else:
    r = cdp.eval("""
(function(){
  var real = document.querySelector('.mditor textarea.textarea');
  if (!real) return 'no real textarea';
  var setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype, 'value').set;
  setter.call(real, %s);
  real.dispatchEvent(new Event('input', {bubbles: true}));
  return 'ok len=' + real.value.length;
})()""" % json.dumps(body))
    print("正文注入:", r)
    time.sleep(2)
    mirror = cdp.eval("(function(){var m=document.querySelector('#article-editor');"
                      "var e=document.querySelector('.mditor textarea.textarea');"
                      "return JSON.stringify({realLen:e?e.value.length:-1,"
                      "mirrorLen:m?m.value.length:-1});})()")
    print("镜像回读:", mirror)
    print("摘要注入:", Y._fill_text(cdp, "#abstractContent", head))
    button = "存为草稿"

if not Y._click_by_text(cdp, button):
    raise SystemExit("✗ 找不到「%s」按钮" % button)
time.sleep(10)
print("URL:", cdp.eval("location.href"))
if a.platform == "tencentcloud":
    print("保存请求响应:", str(cdp.eval("JSON.stringify(window.__wb_saves||[])"))[:1600])

# 🚨 编辑器页内的回读只作诊断日志，绝不作为判据（11-11 假绿根因）。
optimistic = list_rows()
opt_row = [r for r in optimistic if r[0] == a.draft]
print("编辑器页内回读（乐观态，⚠ 不作判据）: %s"
      % (repr(opt_row[0][1][:50]) if opt_row else "目标行不存在"))

rows_after = independent_list_rows()
new_row = [r for r in rows_after if r[0] == a.draft]
print("草稿箱总数(改后/独立列箱):", len(rows_after))
if not new_row:
    raise SystemExit("✗ 改后目标草稿消失")
rid, rtitle, rbody = new_row[0]
probe = old_only_probe(old_body, body)
old_gone = (not probe) or (probe not in norm(rbody))
ok = (rid == a.draft and rtitle.strip() == title.strip()
      and norm(head[:12]) in norm(rbody)
      and old_gone
      and len(rows_after) == len(rows_before))
print("独立双回读: id=%s title=%r body_head=%r OLD_GONE=%s NO_NEW_DRAFT=%s"
      % (rid, rtitle[:50], rbody[:50], old_gone,
         len(rows_after) == len(rows_before)))
print("  旧稿专属探针=%r ⇒ 仍在正文? %s" % (probe[:30], probe and probe in norm(rbody)))
if not ok:
    raise SystemExit("✗ 独立列箱判 FAIL（服务端仍是旧稿）⇒ 不登记台账、不发表。"
                     "看上面「保存请求响应」定位保存失败原因")

Y._remember_db(a.case, {a.platform: {"ok": True, "draftId": a.draft, "url": url}},
               title=title)
print("✓ 改稿完成（草稿实体未新增），台账已登记 draft:", a.draft)
