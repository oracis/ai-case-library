# -*- coding: utf-8 -*-
"""两朵云存量重复草稿清理（2026-11-26）。

起因：用户要求「两朵云要删存量重复」。平台真值实测：
  - 阿里云草稿箱：SuperTux 同标题 8 份（aid 1718046~1719402，11-16 反复建稿）
  - 腾讯云草稿箱：4 条，无同标题重复
根因同 B站二号：建稿前不认亲，重试就新建（PRD #228）。

⚠ 这是**一次性运维脚本**，保留在仓库供复现/审计，不是常驻发布链的一环。
⚠ 默认只读（只列计划）；--apply 才真删。删走阿里云前端同款接口
   `/developer/api/articleDraft/deleteDraft?aid=<aid>`（同源 fetch 带登录 cookie，
   从 publishArticle.js 逆向确认：`deleteDraft({aid: a})` → `q(t)` → 该端点）。
⚠ 保留判据：同标题取 **aid 最大**（与 yun_draft_cdp._aliyun_find_draft 一致），
   单份草稿一律不碰；8 份正文实测完全一致（chars=1105，同一篇 markdown）。

用法：
  python scripts/_oneoff/yun_dedupe_drafts.py            # 只读计划
  python scripts/_oneoff/yun_dedupe_drafts.py --apply    # 真删并回读
"""
import os
import sys
import time
import json
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import yun_draft_cdp as Y  # noqa: E402

EP_LIST = "/developer/api/articleDraft/listDraft?pageSize=30&pageNum=1"
EP_DEL = "/developer/api/articleDraft/deleteDraft?aid=%s"
HOME = "https://developer.aliyun.com/article/new#/"


def fetch_ali(cdp):
    r = cdp.eval("""
fetch(%s, {credentials:"include"})
  .then(function(x){return x.text().then(function(t){return x.status+"|"+t;});})
  .catch(function(e){return "ERR|"+e;})""" % json.dumps(EP_LIST))
    if not isinstance(r, str) or not r[0].isdigit():
        raise RuntimeError("listDraft 失败: %s" % str(r)[:200])
    status, _, payload = r.partition("|")
    d = json.loads(payload)
    if not d.get("success"):
        raise RuntimeError("listDraft success=false: %s" % payload[:200])
    data = d.get("data") or []
    if isinstance(data, dict):
        data = data.get("list") or data.get("records") or []
    return data


def delete_ali(cdp, aid):
    r = cdp.eval("""
fetch(%s, {credentials:"include"})
  .then(function(x){return x.text().then(function(t){return x.status+"|"+t;});})
  .catch(function(e){return "ERR|"+e;})""" % json.dumps(EP_DEL % aid))
    if not isinstance(r, str):
        return "BAD_RESP %s" % str(r)[:120]
    status, _, payload = r.partition("|")
    try:
        d = json.loads(payload)
    except Exception:                                       # noqa: BLE001
        return "HTTP%s JSON_FAIL %s" % (status, payload[:120])
    return "HTTP%s success=%s code=%s %s" % (
        status, d.get("success"), d.get("code"), str(d.get("message") or "")[:60])


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

        before = fetch_ali(cdp)
        print("草稿箱共 %d 条" % len(before))
        groups = defaultdict(list)
        for x in before:
            groups[(x.get("title") or "").strip()].append(x)
        dups = {t: v for t, v in groups.items() if len(v) > 1}
        print("同标题重复组 %d" % len(dups))
        plan = []
        for title, items in sorted(dups.items(), key=lambda kv: -len(kv[1])):
            aids = sorted(int(x.get("aid") or 0) for x in items)
            keep = aids[-1]
            print("  ×%d %s ｜ 保留 aid=%d，删 %s" % (
                len(aids), title[:50], keep, aids[:-1]))
            for a in aids[:-1]:
                plan.append((a, title, keep))
        if not plan:
            print("没有存量重复，无需删除")
            return 0
        if not apply:
            print("\n（只读计划；--apply 才真删）")
            return 0

        ok, fail = [], []
        for aid, title, keep in plan:
            r = delete_ali(cdp, aid)
            print("  删 aid=%s → %s" % (aid, r))
            (ok if "success=True" in r else fail).append(aid)
            time.sleep(0.8)

        after = fetch_ali(cdp)
        left = {int(x.get("aid") or 0) for x in after}
        lost_keep = [k for _, _, k in set((a, t, k) for a, t, k in plan)
                     if k not in left]
        still = [a for a in ok if a in left]
        print("\n删除成功 %d：%s" % (len(ok), ok))
        print("失败 %d：%s" % (len(fail), fail))
        print("回读草稿箱 %d→%d；应保留的 aid 丢失：%s；已删仍存在：%s" % (
            len(before), len(after), lost_keep, still))
        if fail or lost_keep or still:
            return 1
        return 0
    finally:
        try:
            cdp.bws.close()
        except Exception:                                   # noqa: BLE001
            pass
        if proc is not None:
            proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
