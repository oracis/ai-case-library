#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""腾讯云台账 ↔ 平台侧真值对账（`platform_posts` 单行 UPDATE，缺省只预演）。

**为什么需要**：历史落库是「提交成功即 published」（见 PRD #70/#116），
于是台账 `tencentcloud published 26` 与平台「已发布 17」长期不符；
其中还混着「从未建过稿」的行。本脚本把每一行按**平台侧真值**重判：

  判据优先级（全部只看平台接口，不看本地稿）：
    ① `remote_id` 命中「已发布」（`hostStatus=2`）⇒ published
    ② 台账 `title` 命中已发布标题 ⇒ published 并认领该 articleId
    ③ 本地 md 的 h1 命中已发布标题 ⇒ published 并认领
    ④ 上述三条都不中，但 `title`/本地 h1 命中**草稿箱**
       （`POST /developer/api/article/getUserDrafts`）⇒ draft 并绑定 draftId
    ⑤ 全都不中 ⇒ draft、无 id（平台侧从来没有这个稿子）

  ⛔ 绝不编 `remote_id` / `url` / `published_at`：id 只能来自接口，
     url 只写公开页模板，`published_at` 先沿用台账已有值，缺失才用平台 `createTime`。

  🚨 降级要显式授权：`published → draft` 属于回退，缺 `--allow-downgrade` 直接拒绝。
  🚨 降级行必须清空 `remote_id`：`publish_guard.check_dupe()` 里
     `if state in PUBLISHED_STATES or r["remote_id"]` ⇒ `remote_id` 非空会被
     当成「已发过」，该 case 永远发不出去（这是把驳回稿指向真 id 的副作用）。

用法：
    python scripts/tc_ledger_reconcile.py                 # 只读，打印计划
    python scripts/tc_ledger_reconcile.py --apply --allow-downgrade
"""
import argparse
import datetime
import json
import os
import shutil
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import yun_finalize as Y          # noqa: E402
import tc_manage as T            # noqa: E402
from yun_state import norm        # noqa: E402

DB = os.path.join(ROOT, "data", "ledger.db")
PLAT = "tencentcloud"
PUB_URL = "https://cloud.tencent.com/developer/article/%s"
DRAFTS_API = "/developer/api/article/getUserArticleDrafts"

# 需要额外说明的行（历史上出过「同一稿挂到两个 case」的情况）
EXTRA_NOTE = {
    "plausible":
        "⚠ 疑似 case 改名前的残留行：本行与 plausible-analytics 共用草稿 338076，"
        "在线文章 2779001 记在 plausible-analytics 名下；本行在 case_library 中不存在，"
        "本轮只降为 draft、未删行（删行不在授权范围）。",
    "flodesk":
        "⚠ 曾在草稿箱有 338078，但该草稿已不在平台（已删），故 draft_id 一并清空、"
        "state 降为 draft ⇒ 下次发布须重新建稿。",
}


LIST_JS = r"""
(async function(){
  const out = [];
  for (let p = 1; p <= 4; p++) {
    const r = await fetch('/developer/api/creator/articleList',
      {method:'POST', headers:{'Content-Type':'application/json'}, credentials:'include',
       body: JSON.stringify({hostStatus: 2, sortType:'create', page: p, pageSize: 20})});
    let j = null; try { j = await r.json(); } catch(e) {}
    const list = (j && (j.data && (j.data.list || j.data.records) || j.list || j.records)) || [];
    if (!list.length) break;
    for (const it of list) out.push({id: String(it.articleId), title: it.title,
                                    createTime: it.createTime});
    if (list.length < 20) break;
  }
  return JSON.stringify(out);
})()
"""

DRAFTS_JS = r"""
(async function(){
  const out = [];
  for (let p = 1; p <= 4; p++) {
    const r = await fetch('%s',
      {method:'POST', headers:{'Content-Type':'application/json'}, credentials:'include',
       body: JSON.stringify({page: p, pageSize: 20})});
    let j = null; try { j = await r.json(); } catch(e) {}
    const list = (j && (j.data && (j.data.list || j.data.records) || j.list || j.records)) || [];
    if (!list.length) break;
    for (const it of list) out.push({id: String(it.draftId || it.id), title: it.title,
                                    createTime: it.createTime});
    if (list.length < 20) break;
  }
  return JSON.stringify(out);
})()
""" % DRAFTS_API


def local_h1(case):
    for p in ("drafts/newbatch/tencentcloud/%s.md", "drafts/wechat/%s.md",
              "drafts/newbatch/aliyun/%s.md"):
        path = os.path.join(ROOT, p % case)
        if os.path.isfile(path):
            try:
                from csdn_publish import split_md
                return split_md(open(path, encoding="utf-8").read())[0], path
            except Exception:                              # noqa: BLE001
                return "", path
    return "", ""


def fmt(ts):
    try:
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:                                      # noqa: BLE001
        return ""


def decide(row, pub_by_id, pub_by_title, draft_by_title):
    """返回 (target_state, aid, draft_id, title, how)。"""
    rid = str(row["remote_id"] or "")
    if rid and rid in pub_by_id:
        return "published", rid, row["draft_id"] or "", pub_by_id[rid]["title"], "① remote_id 命中已发布"
    t = norm(row["title"] or "")
    if t and t in pub_by_title:
        a = pub_by_title[t]
        return "published", a["id"], row["draft_id"] or "", a["title"], "② 台账标题命中已发布"
    h1, src = local_h1(row["case_id"])
    h = norm(h1)
    if h and h in pub_by_title:
        a = pub_by_title[h]
        return "published", a["id"], row["draft_id"] or "", a["title"], "③ 本地 h1 命中已发布"
    if t and t in draft_by_title:
        d = draft_by_title[t]
        return "draft", "", d["id"], d["title"], "④ 台账标题命中草稿箱"
    if h and h in draft_by_title:
        d = draft_by_title[h]
        return "draft", "", d["id"], d["title"], "④ 本地 h1 命中草稿箱"
    # ⚠ ⑤ 不保留旧 draft_id：那条草稿在平台上已不存在（否则就是指向已删对象）
    return "draft", "", "", row["title"] or h1, "⑤ 平台无此稿"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="真写库（缺省只打印计划）")
    ap.add_argument("--allow-downgrade", action="store_true",
                    help="允许 published→draft（必须显式给）")
    a = ap.parse_args()

    proc, cdp = Y._connect(headed=False)
    try:
        cdp.open(T.LIST_URL)
        time.sleep(8)
        pub = json.loads(cdp.eval(LIST_JS) or "[]")
        drafts = json.loads(cdp.eval(DRAFTS_JS) or "[]")
    finally:
        try:
            cdp.bws.close()
        except Exception:                                  # noqa: BLE001
            pass
    pub_by_id = {p["id"]: p for p in pub}
    pub_by_title, draft_by_title = {}, {}
    for p in pub:
        pub_by_title.setdefault(norm(p["title"] or ""), p)
    for d in drafts:
        draft_by_title.setdefault(norm(d["title"] or ""), d)
    print("平台侧：已发布 %d ｜ 草稿箱 %d" % (len(pub), len(drafts)))

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "select * from platform_posts where platform=?", (PLAT,))]
    print("台账 %d 行\n" % len(rows))

    plan = []
    for r in rows:
        state, aid, did, title, how = decide(r, pub_by_id, pub_by_title, draft_by_title)
        if state == "published":
            p = pub_by_id.get(aid) or {}
            # ⚠ 换过 remote_id 的行，旧 published_at 属于另一篇文章（shipfast
            #   原来那个 12:50 是已清空的 2772404）⇒ 必须按新 id 的 createTime 重算。
            if str(r["remote_id"] or "") == aid and r["published_at"]:
                pub_at = r["published_at"]
            else:
                pub_at = fmt(p.get("createTime")) or r["published_at"]
            url = PUB_URL % aid
            target = dict(state="published", remote_id=aid, url=url,
                          published_at=pub_at, draft_id=did, title=title)
        else:
            target = dict(state="draft", remote_id="", url="",
                          published_at=None, draft_id=did or "",
                          title=title or r["title"] or "")
        changed = {k: (r.get(k), v) for k, v in target.items()
                   if (r.get(k) or "") != (v or "") and not (r.get(k) is None and v is None)}
        plan.append(dict(row=r, target=target, how=how, changed=changed))

    n_pub = sum(1 for x in plan if x["target"]["state"] == "published")
    n_draft = len(plan) - n_pub
    n_down = sum(1 for x in plan if x["row"]["state"] == "published"
                 and x["target"]["state"] == "draft")
    n_chg = sum(1 for x in plan if x["changed"])
    print("计划：published %d ｜ draft %d ｜ 其中降级 %d ｜ 需改字段的行 %d\n"
          % (n_pub, n_draft, n_down, n_chg))
    for x in plan:
        if not x["changed"]:
            continue
        r = x["row"]
        print("  %-24s %-9s→%-9s  %s" % (r["case_id"], r["state"],
                                        x["target"]["state"], x["how"]))
        for k, (o, v) in sorted(x["changed"].items()):
            print("      %-12s %r → %r" % (k, o, v))

    unmatched = [x["row"]["case_id"] for x in plan if x["how"].startswith("⑤")]
    if unmatched:
        print("\n⑤ 平台侧完全没有（降为 draft、无 id）：%s" % ", ".join(unmatched))

    if not a.apply:
        print("\n（预演，未写盘。真写：--apply%s）"
              % ("" if n_down == 0 or a.allow_downgrade else " --allow-downgrade"))
        conn.close()
        return 0

    if n_down and not a.allow_downgrade:
        print("\n✗ 有 %d 行要 published→draft，但你没给 --allow-downgrade，拒绝执行"
              % n_down)
        conn.close()
        return 1

    tag = time.strftime("%Y%m%d-%H%M%S")
    bak = os.path.join(ROOT, "tmp", "ledger_backup_tc_reconcile_%s.db" % tag)
    src, out = sqlite3.connect(DB), sqlite3.connect(bak)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    print("\n一致性备份：%s" % bak)

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    done = 0
    for x in plan:
        if not x["changed"]:
            continue
        r, t = x["row"], x["target"]
        # 只有「状态 / id / 标题 / 时间」变了才写 note，纯 url 变更不动旧备注
        material = any((r.get(k) or "") != (t.get(k) or "") for k in
                       ("state", "remote_id", "title", "published_at"))
        seg = ("[11-09 #118 腾讯云对账] %s ⇒ %s。变更：%s。"
                "（判据只看平台接口：已发布 hostStatus=2 / 草稿箱 getUserArticleDrafts；"
                "降级行清空 remote_id 是必须的 —— check_dupe 把非空 remote_id 当已发过）"
               % (r["state"], t["state"],
                  ", ".join("%s:%r→%r" % (k, o, v)
                            for k, (o, v) in sorted(x["changed"].items()))))
        seg += EXTRA_NOTE.get(r["case_id"], "")
        old_note = (r.get("note") or "").strip()
        note = old_note if not material else (old_note + " ‖ " if old_note else "") + seg
        cur = conn.execute(
            "update platform_posts set state=?, remote_id=?, url=?, title=?,"
            " draft_id=?, published_at=?, note=?, updated_at=?"
            " where case_id=? and platform=? and state=? and ifnull(remote_id,'')=?",
            (t["state"], t["remote_id"], t["url"], t["title"], t["draft_id"],
             t["published_at"], note, now, r["case_id"], PLAT, r["state"],
             r["remote_id"] or ""))
        if cur.rowcount != 1:
            conn.rollback()
            print("✗ %s rowcount=%s（期望 1）⇒ 已整体回滚"
                  % (r["case_id"], cur.rowcount))
            conn.close()
            return 1
        done += 1
    conn.commit()
    print("已提交，更新 %d 行" % done)

    # 自证：与备份逐字段比对，只有计划内的行应当变化
    def snap(p):
        c = sqlite3.connect(p)
        c.row_factory = sqlite3.Row
        d = {(x["case_id"], x["platform"]): dict(x)
             for x in c.execute("select * from platform_posts")}
        c.close()
        return d
    before, after = snap(bak), snap(DB)
    drift = []
    for k, v in after.items():
        o = before.get(k)
        if o is None:
            drift.append((k, "NEW"))
            continue
        d = sorted(f for f in v if o.get(f) != v[f])
        if d:
            drift.append((k, d))
    print("总行数 前/后：%d / %d ｜ 变化行 %d" % (len(before), len(after), len(drift)))
    expect = {x["row"]["case_id"] for x in plan if x["changed"]}
    got = {k[0] for k, _ in drift if k[1] == PLAT}
    print("预期变化 %d 行，实际 %d 行，一致：%s"
          % (len(expect), len(got), expect == got))
    print("平台口径：%s" % [tuple(x) for x in conn.execute(
        "select state, count(*) from platform_posts where platform=? group by state",
        (PLAT,)).fetchall()])
    conn.close()
    return 0 if expect == got else 1


if __name__ == "__main__":
    sys.exit(main())
