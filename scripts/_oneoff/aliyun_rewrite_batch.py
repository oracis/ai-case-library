# -*- coding: utf-8 -*-
"""在阿里云**已有草稿**上就地改成技术向稿（PRD #123）。

为什么不用 scripts/rewrite_yun_draft.py：它的阿里云分支用 `listDraft` 判存在，
而该接口只给「最近活跃 10 条」视图（pageNum 无效，11-09 日志已记），
本批 8 条里有 7 条不在视图里 ⇒ 会被误判成「目标草稿不存在」。
这里改用按 aid 的 `draftDetail` 做改前/改后硬判据。

用法：
  python -u -X utf8 tmp/aliyun_rewrite_batch.py                # 干跑（只本地校验）
  python -u -X utf8 tmp/aliyun_rewrite_batch.py --go --only klar
  python -u -X utf8 tmp/aliyun_rewrite_batch.py --go           # 全部 8 条

默认不写平台；--go 才覆盖。**每条旧稿先落盘备份**，任何一条双回读不过立即停止。
"""
import argparse
import html
import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import yun_draft_cdp as Y  # noqa: E402
import yun_finalize as F  # noqa: E402
import db  # noqa: E402
from csdn_publish import split_md  # noqa: E402

CASES = [
    ("3ak-track-field", "1699450", "3ak-track-field.md"),
    ("flodesk",         "1698291", "flodesk.md"),
    ("getdavid",        "1699817", "getdavid.md"),
    ("headshotpro",     "1699451", "headshotpro.md"),
    ("klar",            "1699633", "klar.md"),
    ("pdfai",           "1698882", "pdfai.md"),
    ("refactoringui",   "1699076", "refactoringui.md"),
    ("upvoty",          "1699818", "upvoty.md"),
]

ap = argparse.ArgumentParser()
ap.add_argument("--go", action="store_true")
ap.add_argument("--only", action="append", default=[])
a = ap.parse_args()
targets = [x for x in CASES if not a.only or x[0] in a.only]
assert targets, "--only 没有命中任何 case"

BAD_WORDS = ("ydtgo.top", "case.ydtgo", "万物解释者", "微信公众号")

DETAIL_JS = """
(function(){
  return fetch("/developer/api/articleDraft/draftDetail?aid=%s", {credentials:"include"})
    .then(function(r){return r.json();})
    .then(function(d){
      var x=(d&&d.data)||{};
      return JSON.stringify({code:(d||{}).code||'', aid:String(x.aid||''),
        title:x.title||'', content:x.content||''});
    }).catch(function(e){return JSON.stringify({err:String(e)});});
})()"""

IDS_JS = """
(function(){
  return fetch("/developer/api/articleDraft/listDraft?pageSize=50&pageNum=1",
               {credentials:"include"})
    .then(function(r){return r.json();})
    .then(function(d){ var x=(d&&d.data)||[]; if(!Array.isArray(x)) x=x.list||[];
      return JSON.stringify(x.map(function(y){return String(y.aid);})); })
    .catch(function(e){return '[]';});
})()"""

PROBE_JS = """
(function(){
  var t=document.querySelector('#title'), b=document.querySelector('.mditor textarea.textarea');
  return JSON.stringify({title:t?(t.value||''):'', body:b?(b.value||''):''});
})()"""

INJECT_JS = """
(function(){
  var seen=[], out=[];
  ['.mditor textarea.textarea','#article-editor'].forEach(function(s){
    var e=document.querySelector(s);
    if (!e || seen.indexOf(e)>=0) return;
    seen.push(e);
    var setter=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;
    setter.call(e, %s);
    e.dispatchEvent(new Event('input', {bubbles:true}));
    out.push(s+':'+e.value.length);
  });
  return out.join(' | ');
})()"""

norm = lambda s: "".join((s or "").split()).lstrip(">")  # noqa: E731


def detail(cdp, aid, tries=4):
    for i in range(tries):
        raw = cdp.eval(DETAIL_JS % aid)
        try:
            d = json.loads(raw)
        except Exception:
            d = {}
        if d.get("code") == "200" and d.get("content"):
            return d
        time.sleep(2)
    return d


def ids(cdp):
    try:
        return set(json.loads(cdp.eval(IDS_JS) or "[]"))
    except Exception:
        return set()


# ---------------------------------------------------------------- 本地校验
print("=== 本地稿件预检 ===")
prepared = []
for case, aid, fn in targets:
    path = os.path.join(ROOT, "drafts", "newbatch", "tencentcloud", fn)
    raw = io.open(path, encoding="utf-8").read()
    title, body = split_md(raw)
    bad = [w for w in BAD_WORDS if w in raw]
    assert title and len(body) > 2000 and not bad, "稿件不合格: %s %s" % (case, bad)
    h2 = [l.strip("# ").strip() for l in body.splitlines() if l.startswith("## ")]
    prepared.append((case, aid, title, body, h2))
    print("  %-16s aid=%s  %4d 字  %d 个 h2  %s" % (case, aid, len(body), len(h2), title[:34]))

if not a.go:
    print("\n（干跑）加 --go 才写平台侧。")
    sys.exit(0)

# ---------------------------------------------------------------- 平台改稿
pod, cdp = Y._connect()
print("\n9228:", "复用常驻" if pod is None else "本次新起")
results = []
try:
    cdp.open("https://developer.aliyun.com/")
    time.sleep(5)
    before_ids = ids(cdp)
    print("listDraft 视图(改前) %d 条" % len(before_ids))
    for case, aid, title, body, h2 in prepared:
        print("\n---- %s / aid=%s ----" % (case, aid))
        url = "https://developer.aliyun.com/article/new?edit=%s#/" % aid
        cdp.open(url)
        time.sleep(9)
        F._close_disclaimer(cdp)
        Y._wait_for(cdp, "!!document.querySelector('#title')", timeout=60)
        old = json.loads(cdp.eval(PROBE_JS) or "{}")
        if not old.get("body"):
            raise SystemExit("✗ 编辑页无正文，停止（aid=%s）" % aid)
        bak = os.path.join(ROOT, "tmp", "aliyun_rewrite_%s_%s.json" % (case, aid))
        with open(bak, "w", encoding="utf-8") as f:
            json.dump({"case": case, "aid": aid, "title": old["title"],
                       "body": old["body"], "at": time.strftime("%Y-%m-%d %H:%M:%S")},
                      f, ensure_ascii=False, indent=1)
        print("  旧稿备份 %s（%d 字）｜旧标题 %s" % (os.path.basename(bak),
              len(old["body"]), old["title"][:30]))

        head = body.strip().splitlines()[0][:24]
        print("  标题注入:", Y._fill_text(cdp, "#title", title))
        print("  正文注入:", cdp.eval(INJECT_JS % json.dumps(body)))
        time.sleep(1)
        print("  摘要注入:", Y._fill_text(cdp, "#abstractContent", head))
        now = json.loads(cdp.eval(PROBE_JS) or "{}")
        if now.get("title") != title or norm(now.get("body", "")[:40]) != norm(body[:40]):
            raise SystemExit("✗ 编辑器注入未生效，停止（不点存为草稿）")
        print("  同帧回读: title ✓ body %d 字 ✓" % len(now.get("body", "")))
        if not Y._click_by_text(cdp, "存为草稿"):
            raise SystemExit("✗ 找不到「存为草稿」按钮，停止")
        time.sleep(8)

        d = detail(cdp, aid)
        content = html.unescape(d.get("content") or "")
        nc = norm(content)
        h2_hit = sum(1 for h in h2 if norm(h) in nc)
        tail = norm(body.strip().splitlines()[-1])[:20]
        checks = {
            "aid 不变": d.get("aid") == aid,
            "code=200": d.get("code") == "200",
            "标题一致": (d.get("title") or "").strip() == title.strip(),
            "新首句命中": norm(head) in nc,
            "新末句命中": bool(tail) and tail in nc,
            "h2 全命中": h2_hit == len(h2),
            "旧首句消失": norm(old["body"][:20]) not in nc,
            "长度合理": 0.85 * len(body) <= len(content) <= 1.35 * len(body),
        }
        for k, v in checks.items():
            print("    %s %s" % ("✓" if v else "✗", k))
        if not all(checks.values()):
            raise SystemExit("✗ 双回读未通过，停止后续（不登记台账）")

        # 台账：只改 title/url/note，不动 state/draft_id/remote_id/published_at
        conn = db.get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT note FROM platform_posts WHERE case_id=? AND platform='aliyun' AND draft_id=?",
                        (case, aid))
            row = cur.fetchone()
            assert row is not None, "台账无 (case, aliyun, aid) 行"
            add = (" ‖ [11-09 #123 就地改稿] 旧故事稿 → 技术向稿，平台 aid 不变、未新建草稿、未发布；"
                   "draftDetail 双回读：title 与本地 h1 一致、%d 个 h2 全命中、首末句命中、旧首句消失、正文 %d 字；"
                   "旧稿备份 tmp/aliyun_rewrite_%s_%s.json" % (len(h2), len(content), case, aid))
            cur.execute("UPDATE platform_posts SET title=?, url=?, note=?, "
                        "updated_at=datetime('now','localtime') "
                        "WHERE case_id=? AND platform='aliyun' AND draft_id=?",
                        (title, url, (row[0] or "") + add, case, aid))
            assert cur.rowcount == 1, "台账 rowcount != 1"
            conn.commit()
            print("    ✓ 台账 title 同步 + note 追加（rowcount=1）")
        finally:
            db.close_conn()
        results.append((case, aid, len(content), h2_hit, len(h2)))

    after_ids = ids(cdp)
    added = sorted(after_ids - before_ids)
    print("\n=== listDraft 视图(改后) %d 条 ｜ 新增 aid: %s ===" % (len(after_ids), added or "无"))
    if added:
        print("⚠ 视图出现新 aid，需人工复核是否误建草稿（不据此判定改稿失败）")
finally:
    if pod is not None:
        try: pod.terminate()
        except Exception: pass

print("\n=== 汇总：%d/%d 条通过 ===" % (len(results), len(prepared)))
for case, aid, n, hit, total in results:
    print("  ✓ %-16s aid=%s  %d 字  h2 %d/%d" % (case, aid, n, hit, total))
if len(results) != len(prepared):
    print("⚠ 未全部完成，请检查中断原因")
    sys.exit(1)
