#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读诊断：小红书草稿箱里那 13 条草稿，正文还带不带内部核验注记？

背景（已定事实）
--------------
`tmp/batch_clean_log.json` 记的是 11-10 16:43 那次「72 个存量脏产物」清理：
扫描 72 个 `out/articles/*.html`，**36 个真改了**（清掉「我去核了一遍」等内部核验注记）。
但那是**本地产物**。各平台草稿箱是**建稿那一刻的副本**，本地怎么改都影响不到它。
按台账，这 36 个 case 在各平台还有草稿：bilibili 16 / xhs 13 / toutiao 16 /
wechat 15 / csdn 1 / zhihu 2。B站 16 条已清完，本脚本盘 **xhs 这 13 条**。

判据（与 B站同一套，不靠锚点、不靠符号表）
--------------------------------------
  · 平台正文含 MARK（「我去核了一遍」或其它核验词）⇒ 脏，需要清
  · `diff_stats(note.body, 平台正文)` 的 `dele` ⇒ 平台缺母版多少字（>40 = 平台旧版）
  · 两者都干净 ⇒ OK

⚠ 小红书正文里 tags 是单独的（编辑器里正文含 `#标签`），所以比对前要把
  note.json 的 tags 拼回 body 尾部，否则每条都会假报「平台多几十字」。

⛔ 只读：只开草稿箱 + 点「编辑」读正文，不输入、不保存、不改任何字段。
用法：
    python -X utf8 tmp/diag_xhs_13.py                 # 全部 13 条
    python -X utf8 tmp/diag_xhs_13.py --limit 4       # 分块（受管任务会被回收）
    python -X utf8 tmp/diag_xhs_13.py --only kibu
"""
import argparse
import difflib
import html
import json
import os
import re
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

import xhs_publish as xp                # noqa: E402
import wechat_publish as wp             # noqa: E402
import chrome_cdp_launch as cl          # noqa: E402

MARK = "我去核了一遍"
# 其它形态的内部核验词（B站 3 条旧版里出现过的真实字样，别只看 MARK）
MARKS_EXTRA = ("能不能搬回国内", "中国移植分", "获客迁移", "改造成本",
               "竞争空位", "需求端毫无问题")
DEL_MAX = 40
DRAFT_URL = "https://creator.xiaohongshu.com/publish/publish?target=draft"

JS_OPEN_EDIT = "(function(){%s\n" % xp.JS_CLICK_CHAIN + """
  var sv=%s;
  var cards=[].slice.call(document.querySelectorAll('*')).filter(function(x){
    var t=(x.innerText||'');
    return t.indexOf('保存于')>=0 && t.indexOf('编辑')>=0 && t.indexOf('删除')>=0; });
  cards = cards.filter(function(c){
    return !cards.some(function(o){ return o!==c && c.contains(o); }); });
  for(var i=0;i<cards.length;i++){
    if((cards[i].innerText||'').indexOf(sv)>=0){
      var e=[].slice.call(cards[i].querySelectorAll('*')).filter(function(x){
        return (x.innerText||'').trim()==='编辑' && x.children.length===0; })[0];
      if(!e) return 'noedit';
      jclick(e);
      return 'clicked'; }
  }
  return 'notfound';
})()"""


def norm(s):
    t = re.sub(r"<[^>]+>", "", str(s or ""))
    return re.sub(r"\s+", "", html.unescape(t)).strip()


def diff_stats(master, plat):
    sm = difflib.SequenceMatcher(None, master, plat, autojunk=False)
    dele = ins = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "delete":
            dele += i2 - i1
        elif tag == "insert":
            ins += j2 - j1
        elif tag == "replace":
            dele += i2 - i1
            ins += j2 - j1
    return dele, ins


def dirty_marks(text):
    """命中哪些内部核验字样（返回列表，空 = 干净）。"""
    hit = []
    if MARK in text:
        hit.append(MARK)
    for m in MARKS_EXTRA:
        if m in text:
            hit.append(m)
    return hit


def load_targets():
    d = json.load(open(os.path.join(ROOT, "tmp", "batch_clean_log.json"),
                       encoding="utf-8"))
    cids = [os.path.splitext(os.path.basename(r[0]))[0]
            for r in d["results"] if r[1] != "skip"]
    conn = sqlite3.connect(os.path.join(ROOT, "data", "ledger.db"))
    cur = conn.cursor()
    q = ",".join("?" * len(cids))
    cur.execute("SELECT case_id, remote_id, draft_id FROM platform_posts "
                "WHERE platform='xhs' AND state='draft' AND case_id IN (%s)" % q,
                cids)
    rows = cur.fetchall()
    conn.close()
    return [(r[0], (r[1] or r[2])) for r in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true",
                    help="已 dump 过的也重查（默认跳过，便于分块跑完 13 条）")
    ap.add_argument("--dumpdir", default=os.path.join(ROOT, "tmp", "xhs13"))
    a = ap.parse_args()

    targets = load_targets()
    if a.only:
        targets = [t for t in targets if t[0] == a.only]
    print("目标 %d 条（小红书 draft 且属于 11-10 清过注记的 36 个 case）\n"
          % len(targets))
    if not targets:
        return 0
    want = dict(targets)
    os.makedirs(a.dumpdir, exist_ok=True)

    # fetch_drafts 内部的 wp.CDP 在端口不通时抛 SystemExit（BaseException），
    # 会静默终止脚本 ⇒ 必须先显式起浏览器。
    wp._autostart_chrome_if_needed(xp.PUB_PORT, profile=cl.PLAT_PROFILE)
    # ⚠ 必须先建 tab 再 fetch，且**共用同一个连接**：`fetch_drafts()` 无参时
    #   会自己 `_draft_cdp()` 再开一个新 tab 并开抽屉，之后再 `_draft_cdp()`
    #   拿到的是**另一个没开抽屉的 tab** ⇒ 卡片全 notfound（11-10 踩过）。
    sub = xp._draft_cdp()
    cards = xp.fetch_drafts(sub=sub) or []
    print("草稿箱 %d 条" % len(cards))
    matched, orphans = xp.match_drafts(cards)
    by_cid = {}
    for m in matched:
        by_cid.setdefault(m["id"], []).append(m)
    print("对上 %d 个 case｜孤儿 %d 条\n" % (len(by_cid), len(orphans)))

    js = lambda e: sub.eval(e)                             # noqa: E731
    rows = []
    done = 0
    attempted = 0
    for idx, (cid, rid) in enumerate(targets):
        if a.limit and done >= a.limit:
            print("… 已达 --limit %d" % a.limit)
            break
        prev = os.path.join(a.dumpdir, cid + ".txt")
        if os.path.isfile(prev) and not a.force:
            print("%-26s SKIP 已查过（%s）" % (cid, prev))
            continue
        done += 1
        # ⚠ 每条都要重拉列表：读完一条 navigate 回列表会把**抽屉关掉**，
        #   且草稿顺序会重排、`saved` 快照失效 ⇒ 不刷新就全 notfound（11-10 踩过）。
        if attempted:
            cards = xp.fetch_drafts(sub=sub) or []
            matched, _ = xp.match_drafts(cards)
            by_cid = {}
            for m in matched:
                by_cid.setdefault(m["id"], []).append(m)
        attempted += 1
        ms = by_cid.get(cid) or []
        if not ms:
            print("%-26s ✗ 草稿箱里没找到对应草稿（可能已发/已删）" % cid)
            rows.append((cid, "未找到", 0, 0))
            continue
        if len(ms) > 1:
            print("%-26s ⚠ 同一 case 有 %d 份草稿，只查第一份" % (cid, len(ms)))
        m = ms[0]
        note = {}
        np_ = os.path.join(ROOT, "out", "xhs", cid, "note.json")
        if os.path.isfile(np_):
            note = json.load(open(np_, encoding="utf-8"))
        master = note.get("body") or ""
        tags = note.get("tags") or []
        if tags:
            master = master + "\n" + " ".join(tags)

        r = js(JS_OPEN_EDIT % json.dumps(m.get("saved") or ""))
        if r != "clicked":
            print("%-26s ✗ 点不到编辑（%s）" % (cid, r))
            rows.append((cid, "点不到", 0, 0))
            continue
        time.sleep(6)
        sub.eval("1", refresh_context=True)
        body = xp.read_editor_body(sub)
        with open(os.path.join(a.dumpdir, cid + ".txt"), "w",
                  encoding="utf-8") as f:
            f.write(body or "")
        hits = dirty_marks(body)
        dele, ins = diff_stats(norm(master), norm(body))
        verdict = ("脏：含 %s" % "/".join(hits) if hits else
                   ("平台旧版：缺母版 %d 字" % dele if dele > DEL_MAX else
                    "干净"))
        rows.append((cid, verdict, dele, len(norm(body))))
        print("%-26s 平台 %4d / 母版 %4d ｜ 缺%4d 多%4d ｜ %s"
              % (cid, len(norm(body)), len(norm(master)), dele, ins, verdict))
        # 回草稿箱列表，重置编辑器状态（改过的会重排，所以每条都重导航）
        sub.send("Page.navigate", {"url": DRAFT_URL})
        time.sleep(6)
        sub.eval("1", refresh_context=True)

    print("\n--- 汇总 ---")
    dirty = [r for r in rows if str(r[1]).startswith("脏")]
    oldv = [r for r in rows if str(r[1]).startswith("平台旧版")]
    clean = [r for r in rows if r[1] == "干净"]
    miss = [r for r in rows if r[1] in ("未找到", "点不到")]
    print("  脏 %d ｜ 平台旧版 %d ｜ 干净 %d ｜ 查不到 %d"
          % (len(dirty), len(oldv), len(clean), len(miss)))
    for r in dirty:
        print("    脏：%-26s %s" % (r[0], r[1]))
    for r in oldv:
        print("    旧：%-26s %s" % (r[0], r[1]))
    for r in miss:
        print("    缺：%-26s %s" % (r[0], r[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
