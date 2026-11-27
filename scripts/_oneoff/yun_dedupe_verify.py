# -*- coding: utf-8 -*-
"""独立回读：验阿里云重复草稿是否真删（不信任 deleteDraft 的 success=True）。"""
import os, sys, time, json
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import yun_draft_cdp as Y

EP = "/developer/api/articleDraft/listDraft?pageSize=30&pageNum=1"
proc, cdp = Y._connect()
print("9228:", "reuse" if proc is None else "new-start")
try:
    cdp.open("https://developer.aliyun.com/article/new#/")
    time.sleep(7)
    r = cdp.eval("""
fetch(%s, {credentials:"include"})
  .then(function(x){return x.text().then(function(t){return x.status+"|"+t;});})
  .catch(function(e){return "ERR|"+e;})""" % json.dumps(EP))
    status, _, payload = r.partition("|")
    data = json.loads(payload).get("data") or []
    print("HTTP", status, "listDraft 仍返回", len(data), "条")
    sup = [x for x in data if "SuperTux" in (x.get("title") or "")]
    print("SuperTux", len(sup))
    for x in sorted(sup, key=lambda a: int(a.get("aid") or 0)):
        print("  aid=%s isDelete=%r gmtModified=%s title=%s" % (
            x.get("aid"), x.get("isDelete"), x.get("gmtModified"),
            (x.get("title") or "")[:40]))

    # UI 对话框真实条目
    cdp.eval("(function(){var t=document.querySelector('.draft-box');if(t)t.click();return 1;})()")
    time.sleep(5)
    txt = cdp.eval("""
(function(){var d=document.querySelector('.next-dialog');
  return d ? (d.textContent||'').replace(/\\s+/g,' ') : 'no-dialog';})()""")
    print("UI 对话框 SuperTux 出现次数:", str(txt).count("SuperTux"))
    print("UI 对话框文本:", str(txt)[:700])
finally:
    try:
        cdp.bws.close()
    except Exception:                                       # noqa: BLE001
        pass
    if proc is not None:
        proc.terminate()
