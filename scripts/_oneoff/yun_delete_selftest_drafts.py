# -*- coding: utf-8 -*-
"""删除阿里云草稿箱里所有「自检 / CDP建稿链路测试」草稿（2026-11-26）。

用户裁定：「自检？是不是测试草稿？如果是，一条都不要留」。
⚠ 判据只看标题含「自检」或「CDP建稿链路测试」，不碰任何真实案例稿。
⚠ 默认只读计划；--apply 才真删；删完重新拉全量回读。

用法：
  python scripts/_oneoff/yun_delete_selftest_drafts.py
  python scripts/_oneoff/yun_delete_selftest_drafts.py --apply
"""
import os
import sys
import time
import json

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import yun_draft_cdp as Y  # noqa: E402

EP_LIST = "/developer/api/articleDraft/listDraft?pageSize=30&pageNum=1"
EP_DEL = "/developer/api/articleDraft/deleteDraft?aid=%s"
HOME = "https://developer.aliyun.com/article/new#/"
KEYWORDS = ("自检", "CDP建稿链路测试")


def fetch_all(cdp):
    r = cdp.eval("""
fetch(%s, {credentials:"include"})
  .then(function(x){return x.text().then(function(t){return x.status+"|"+t;});})
  .catch(function(e){return "ERR|"+e;})""" % json.dumps(EP_LIST))
    if not isinstance(r, str) or not r[0].isdigit():
        raise RuntimeError("listDraft 失败: %s" % str(r)[:160])
    _, _, payload = r.partition("|")
    rows = json.loads(payload).get("data") or []
    if isinstance(rows, dict):
        rows = rows.get("list") or rows.get("records") or []
    seen, out = set(), []
    for x in rows:
        aid = int(x.get("aid") or 0)
        if aid in seen:
            continue
        seen.add(aid)
        out.append(x)
    return out


def is_selftest(title):
    return any(k in (title or "") for k in KEYWORDS)


def delete_ali(cdp, aid):
    r = cdp.eval("""
fetch(%s, {credentials:"include"})
  .then(function(x){return x.text().then(function(t){return x.status+"|"+t;});})
  .catch(function(e){return "ERR|"+e;})""" % json.dumps(EP_DEL % aid))
    if not isinstance(r, str):
        return "BAD_RESP"
    _, _, payload = r.partition("|")
    try:
        d = json.loads(payload)
    except Exception:                                       # noqa: BLE001
        return "JSON_FAIL %s" % payload[:100]
    return "success=%s code=%s %s" % (
        d.get("success"), d.get("code"), str(d.get("message") or "")[:40])


def main():
    apply = "--apply" in sys.argv
    proc, cdp = Y._connect()
    print("9228:", "复用常驻" if proc is None else "本次新起")
    try:
        cdp.open(HOME)
        time.sleep(6)
        href = str(cdp.eval("location.href") or "")
        if "account.aliyun.com" in href or "/login" in href:
            print("LOGIN_REQUIRED", href[:160])
            return 3

        before = fetch_all(cdp)
        print("草稿箱全量 %d 条：" % len(before))
        for x in before:
            flag = " <== 自检/测试" if is_selftest(x.get("title")) else ""
            print("  %s | %s%s" % (x.get("aid"), (x.get("title") or "")[:60], flag))
        targets = [x for x in before if is_selftest(x.get("title"))]
        print("\n自检/测试草稿 %d 条：%s" % (
            len(targets), [x.get("aid") for x in targets]))
        if not targets:
            print("无自检草稿，无需删除")
            return 0
        if not apply:
            print("（只读计划；--apply 才真删）")
            return 0

        ok, fail = [], []
        for x in targets:
            aid = x.get("aid")
            r = delete_ali(cdp, aid)
            print("  删 %s → %s" % (aid, r))
            (ok if "success=True" in r else fail).append(aid)
            time.sleep(0.7)
        after = fetch_all(cdp)
        left = {int(x.get("aid") or 0) for x in after}
        still = [a for a in ok if a in left]
        remain = [x.get("aid") for x in after if is_selftest(x.get("title"))]
        print("\n删除成功 %d：%s" % (len(ok), ok))
        print("失败 %d：%s" % (len(fail), fail))
        print("回读 %d→%d；已删仍存在=%s；剩余自检=%s" % (
            len(before), len(after), still, remain))
        return 1 if (fail or still or remain) else 0
    finally:
        try:
            cdp.bws.close()
        except Exception:                                   # noqa: BLE001
            pass
        if proc is not None:
            proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
