# -*- coding: utf-8 -*-
"""独立只读复核：8 条改稿平台真值 + 同批垃圾 title 的平台实读 + 重复草稿检查。"""
import html, io, json, os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import yun_draft_cdp as Y
from csdn_publish import split_md

CASES = [
    ("3ak-track-field","1699450"),("flodesk","1698291"),("getdavid","1699817"),
    ("headshotpro","1699451"),("klar","1699633"),("pdfai","1698882"),
    ("refactoringui","1699076"),("upvoty","1699818"),
]
EXTRA = ["1711205", "1701663"]
AIDS = [a for _, a in CASES] + EXTRA
norm = lambda s: "".join((s or "").split()).lstrip(">")  # noqa: E731

DETAIL = """
(function(){
  var ids=%s;
  return Promise.all(ids.map(function(id){
    return fetch("/developer/api/articleDraft/draftDetail?aid="+id,{credentials:"include"})
      .then(function(r){return r.json();})
      .then(function(d){var x=(d&&d.data)||{};
        return {aid:id, code:(d||{}).code||'', title:x.title||'', content:x.content||'',
                abstract:x.abstractContent||''};})
      .catch(function(e){return {aid:id, err:String(e)};});
  })).then(function(a){return JSON.stringify(a);});
})()""" % json.dumps(AIDS)

LIST = """
(function(){
  return fetch("/developer/api/articleDraft/listDraft?pageSize=50&pageNum=1",{credentials:"include"})
    .then(function(r){return r.json();})
    .then(function(d){var x=(d&&d.data)||[]; if(!Array.isArray(x)) x=x.list||[];
      return JSON.stringify(x.map(function(y){return {aid:String(y.aid), title:y.title||''};}));})
    .catch(function(e){return '[]';});
})()"""

PROBE = """
(function(){
  var t=document.querySelector('#title'), b=document.querySelector('.mditor textarea.textarea');
  return JSON.stringify({title:t?(t.value||''):'', bodyLen:b?(b.value||'').length:0,
                         bodyHead:b?(b.value||'').slice(0,45):''});
})()"""

bak = {r["aid"]: r for r in json.load(
    io.open(os.path.join(ROOT,"tmp","aliyun_rewrite_backup_20261109_v2.json"), encoding="utf-8"))}

pod, cdp = Y._connect()
out = {}
try:
    cdp.open("https://developer.aliyun.com/")
    time.sleep(5)
    details = {x["aid"]: x for x in json.loads(cdp.eval(DETAIL))}
    listing = json.loads(cdp.eval(LIST) or "[]")
    print("=== 8 条改稿独立复核（draftDetail 按 aid 回读）===")
    ok = 0
    for case, aid in CASES:
        p = os.path.join(ROOT,"drafts","newbatch","tencentcloud", case+".md")
        title, body = split_md(io.open(p, encoding="utf-8").read())
        d = details.get(aid, {})
        c = html.unescape(d.get("content") or "")
        nc = norm(c)
        h2 = [l.strip("# ").strip() for l in body.splitlines() if l.startswith("## ")]
        head = norm(body.strip().splitlines()[0][:24])
        tail = norm(body.strip().splitlines()[-1])[:20]
        oldhead = norm(bak.get(aid,{}).get("content","")[:20])
        checks = {
            "code=200": d.get("code")=="200",
            "标题==本地h1": (d.get("title") or "").strip()==title.strip(),
            "首句": head in nc, "末句": tail in nc,
            "h2全中": all(norm(h) in nc for h in h2),
            "旧首句消失": bool(oldhead) and oldhead not in nc,
            "长度": 0.85*len(body) <= len(c) <= 1.35*len(body),
        }
        good = all(checks.values())
        ok += good
        print("  %s %-16s aid=%s %4d字 h2 %d/%d  %s" % (
            "✓" if good else "✗", case, aid, len(c),
            sum(1 for h in h2 if norm(h) in nc), len(h2),
            "" if good else [k for k,v in checks.items() if not v]))
        out[case] = {"aid":aid, "platform_title":d.get("title"), "chars":len(c),
                     "checks":checks, "abstract":d.get("abstract","")[:30]}
    print("小计: %d/8 全绿" % ok)

    print("\n=== 草稿列表视图（%d 条）===" % len(listing))
    targets = set(a for _, a in CASES)
    local_titles = {}
    for case, aid in CASES:
        t,_ = split_md(io.open(os.path.join(ROOT,"drafts","newbatch","tencentcloud",case+".md"),encoding="utf-8").read())
        local_titles[t] = case
    dup = []
    for x in listing:
        mark = ""
        if x["aid"] in targets: mark = " [改稿目标]"
        elif x["title"] in local_titles: mark = " ⚠ 重复技术稿（aid 不同）"; dup.append(x)
        print("  %-9s %s%s" % (x["aid"], x["title"][:44], mark))
    print("重复草稿:", dup or "无")
    out["_list"] = listing; out["_dup"] = dup

    print("\n=== 同批垃圾 title 平台实读 ===")
    for aid in EXTRA:
        d = details.get(aid, {})
        cdp.open("https://developer.aliyun.com/article/new?edit=%s#/" % aid)
        time.sleep(10)
        ed = json.loads(cdp.eval(PROBE) or "{}")
        rec = {"aid":aid, "draftDetail_title":d.get("title"), "code":d.get("code"),
               "editor_title":ed.get("title"), "editor_bodyLen":ed.get("bodyLen"),
               "editor_bodyHead":ed.get("bodyHead")}
        out[aid] = rec
        print(" ", json.dumps(rec, ensure_ascii=False))
    open(os.path.join(ROOT,"tmp","aliyun_rewrite_verify_20261109.json"),"w",encoding="utf-8").write(
        json.dumps(out, ensure_ascii=False, indent=1))
finally:
    if pod is not None:
        try: pod.terminate()
        except Exception: pass
