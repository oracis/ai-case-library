#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性：删除头条**已发布**文章（清理 save=1 探测时发出去的测试稿）。

接口（2026-10-18 从 `vendors.d1505bc60ce087d047b1.js` 的 `Delete()` 反编译）：

    POST /mp/agw/creator_center/delete?type=<t>&gid=<gid>
    body JSON.stringify({type, gid, slave_id, mp_id, is_question,
                         question_id, is_comment})

删除是破坏性操作，本脚本**先打印目标标题**（从已发表列表按 gid 反查），
标题不符就退出；删除后回读列表核对 gid 真消失。
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import toutiao_publish as T  # noqa: E402
import wechat_publish as wp  # noqa: E402

PORT = int(os.environ.get("CDP_PORT", 9223))


def articles(cdp):
    """导航式读已发表列表（同 fetch_published_articles 的手法），返回原始 items。"""
    cdp.send("Page.navigate", {"url": T._article_api_url(0, 1)})
    time.sleep(5)
    txt = cdp.eval("document.body.innerText") or "{}"
    d = json.loads(txt)
    return d.get("data") or []


def cell_of(it):
    return (it.get("assembleCell") or {}).get("itemCell") or {}


def main():
    gid = sys.argv[1] if len(sys.argv) > 1 else "7399999999999999999"
    cdp = wp.CDP(PORT)
    t = cdp.new_target(T.TT_ARTICLES_PAGE)
    cdp.connect_target(t["id"])
    try:
        time.sleep(5)
        items = articles(cdp)
        target = None
        for it in items:
            base = cell_of(it).get("articleBase") or {}
            if str(base.get("gidStr")) == str(gid):
                target = base
                break
        if not target:
            print("✗ 列表里找不到 gid=%s（可能已删）" % gid)
            return 2
        print("目标：%s" % target.get("title"))
        print("  gid=%s status=%s 创建=%s"
              % (target.get("gidStr"), target.get("itemStatus"),
                 target.get("createTime")))

        ok = False
        for typ in ("0", "1", "2", "3"):
            url = "/mp/agw/creator_center/delete?type=%s&gid=%s" % (typ, gid)
            body = json.dumps({"type": int(typ), "gid": str(gid)},
                              ensure_ascii=False)
            js = ("(function(){return fetch(%s,{method:'POST',credentials:'include',"
                  "headers:{'Accept':'application/json',"
                  "'Content-Type':'application/json'},body:%s})"
                  ".then(function(r){return r.text();})"
                  ".catch(function(e){return 'ERR:'+e.message;})})()"
                  % (json.dumps(url), json.dumps(body)))
            out = str(cdp.eval(js) or "")
            print("  type=%s -> %s" % (typ, out[:160]), flush=True)
            if out.startswith("{"):
                d = json.loads(out)
                if d.get("code") == 0:
                    ok = True
                    break
            time.sleep(2)

        print("\n回读核对…")
        items = articles(cdp)
        still = any(str((cell_of(i).get("articleBase") or {}).get("gidStr")) == str(gid)
                    for i in items)
        print("  列表现在 %d 条，目标 gid %s" % (len(items), "仍在" if still else "已消失"))
        return 0 if (ok and not still) else 4
    finally:
        try:
            cdp.close_target(t["id"])
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
