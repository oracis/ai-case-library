# -*- coding: utf-8 -*-
"""阿里云草稿箱去重（全量 10 条，接口忽略 pageNum）。

2026-11-26 实测：listDraft?pageSize=30&pageNum=N **无论 N 是几都返回同样 10 条**，
服务端忽略分页参数 ⇒ 草稿箱全量就是这 10 条（UI 草稿箱对话框亦为 10 条）。
第一轮已删 SuperTux 7 份；本轮处理剩余「马力欧兄弟×2」「自检×4」。
⚠ 同 aid 在多页反复出现时按 aid 去重，别把同一条当多条计数。

用法：
  python scripts/_oneoff/yun_dedupe_all.py            # 只读计划
  python scripts/_oneoff/yun_dedupe_all.py --apply    # 真删并回读
"""
import os
import sys
import time
import json
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import yun_draft_cdp as Y  # noqa: E402

EP_LIST = "/developer/api/articleDraft/listDraft?pageSize=30&pageNum=%d"
EP_DEL = "/developer/api/articleDraft/deleteDraft?aid=%s"
HOME = "https://developer.aliyun.com/article/new#/"


def fetch_all(cdp):
    """接口忽略 pageNum，单页即全量；同 aid 去重防重复计数。"""
    r = cdp.eval("""
fetch(%s, {credentials:"include"})
  .then(function(x){return x.text().then(function(t){return x.status+"|"+t;});})
  .catch(function(e){return "ERR|"+e;})""" % json.dumps(EP_LIST % 1))
    if not isinstance(r, str) or not r[0].isdigit():
        raise RuntimeError("listDraft 失败: %s" % str(r)[:160])
    _, _, payload = r.partition("|")
    d = json.loads(payload)
    rows = d.get("data") or []
    if isinstance(rows, dict):
        rows = rows.get("list") or rows.get("records") or []
    seen, out = set(), []
    for x in rows:
        aid = int(x.get("aid") or 0)
        if aid in seen:
            continue
        seen.add(aid)
        out.append(x)
    print("  listDraft → %d 条（去重后）" % len(out), flush=True)
    return out


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
        print("拉取全量草稿：")
        before = fetch_all(cdp)
        print("草稿箱全量 %d 条" % len(before))
        for x in before:
            print("  aid=%s isDelete=%s %s" % (
                x.get("aid"), x.get("isDelete"), (x.get("title") or "")[:46]))

        groups = defaultdict(list)
        for x in before:
            groups[(x.get("title") or "").strip()].append(x)
        dups = {t: v for t, v in groups.items() if len(v) > 1}
        print("\n同标题重复组 %d" % len(dups))
        plan = []
        for title, items in sorted(dups.items(), key=lambda kv: -len(kv[1])):
            aids = sorted(int(x.get("aid") or 0) for x in items)
            keep = aids[-1]
            print("  ×%d %s ｜ 保留 %d，删 %s" % (len(aids), title[:48], keep, aids[:-1]))
            for a in aids[:-1]:
                plan.append((a, title, keep))
        if not plan or not apply:
            print("\n（只读计划；--apply 才真删）" if plan else "\n无重复")
            return 0

        ok, fail = [], []
        for aid, title, keep in plan:
            r = delete_ali(cdp, aid)
            print("  删 %s → %s" % (aid, r))
            (ok if "success=True" in r else fail).append(aid)
            time.sleep(0.7)

        print("\n重新拉全量回读：")
        after = fetch_all(cdp)
        left = {int(x.get("aid") or 0) for x in after}
        left_titles = defaultdict(list)
        for x in after:
            left_titles[(x.get("title") or "").strip()].append(int(x.get("aid") or 0))
        still = [a for a in ok if a in left]
        lost_keep = [k for _, _, k in plan if k not in left]
        remain_dup = {t: v for t, v in left_titles.items() if len(v) > 1}
        print("删除成功 %d：%s" % (len(ok), ok))
        print("失败 %d：%s" % (len(fail), fail))
        print("回读 %d→%d；保留 aid 丢失=%s；已删仍存在=%s；剩余重复组=%s" % (
            len(before), len(after), lost_keep, still, remain_dup))
        return 1 if (fail or lost_keep or still or remain_dup) else 0
    finally:
        try:
            cdp.bws.close()
        except Exception:                                   # noqa: BLE001
            pass
        if proc is not None:
            proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
