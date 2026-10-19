#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""B站草稿账同步：线上草稿箱 ↔ 本地台账，一一对上出厂部件号。

## 为什么单开一个脚本（而不是塞进 ledger_health）

巡检能回答「账本有没有病」，回答不了「这条草稿是哪篇稿子」。原因是**归属
要读正文** —— 2026-10-19 那次补登的教训：纯按标题匹配会错配，正文才是硬证据
（harperai 的草稿标题叫「把英国公开抵押记录连成一张网…」，一个产品名都没有，
只有正文首句写着「Harper 把…」）。这类语义活不适合放在毫秒级巡检里。

分工：
  · `ledger_health.py --online` → ID 集合差（错 yes/no，快）
  · **`bili_draft_sync.py`**      → 归属认领（要不要写进账本，慢，要判断）

## 认领判据（硬证据优先，不猜）

1. 台账里已经记过这个 `article_id` ⇒ 已认领（幂等，重复跑不会重复写）
2. 标题与本地包（`out/bili/<id>/note.json` 的 title，或母版的 `<h1>`）
   **归一化后精确相等**
3. 正文前缀与本地内容包逐字相等；窗口从 200 字逐档收到 120 / 60 字，
   任一档**唯一**命中即可（换锚点，不降门槛 —— 见 `by_body` 的注释）
4. 都不中，或一档撞上多个 case ⇒ **不认领**，列出来等人看。
   绝不按标题相似度投票。

## ⚠ 一个 case 在线上可能是**两个对象**

  · 草稿箱里那条（ID 形如 `342372`）
  · 已发布的正文（ID 形如 `53460777`）

所以同步**不会**把已发布的 case 拉回草稿态 —— 那是把发出去的文章记成没发。
用 `ledger.set_draft_id()` 只补草稿那一侧，状态照旧。

2026-10-19 首跑实测：线上 49 条草稿 + 11 篇已发布，**零交集**（发过的那些
草稿确实没了），49 条全部认领成功。两个 ID 并存的情况要在「已发布的 case
又跑了一次 `publish`（= 再存一条草稿）」时才会出现，账本已经能装下它。

用法：
    python bili_draft_sync.py                 # 只读报告（默认）
    python bili_draft_sync.py --apply         # 认领成功的写进 data/bili_drafts.json
    python bili_draft_sync.py --json out.json # 机器可读
退出码：0=全部认领干净；1=有未认领的草稿（或拉不到线上）。
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import ledger as L          # noqa: E402

OUT_BILI = os.path.join(ROOT, "out", "bili")
OUT_ART = os.path.join(ROOT, "out", "articles")
# 正文比对取多长：200 字足够唯一，又不会被"封面段下面多插一句"打乱
SIG_LEN = 200


def _norm(s):
    """归一化成"只有字"：去 HTML 标签、空白、标点，只留字母数字和汉字。

    ⚠ 连箭头 / 星号 / 顿号这类装饰符也要去掉 —— 提交给 B站的正文会被
      `clean_body` 加工（段落标题前加 `▶`、加粗变形状），线上草稿的
      `summary` 是**加工后**的版本，本地是加工前的。只去标点比两版必错，
      看上去像"没归属"，其实整篇都在。2026-10-19：augora-ai 等 5 条就是
      这么丢的。
    """
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    s = re.sub(r"\s+", "", s)
    return re.sub(r"[^\w一-鿿]", "", s)


def _drop_h1(html):
    """去掉正文里的 <h1> 那一行 —— 标题单独提交（title 字段），
    正文开头是从 h1 之后算起的，留着它两边就永远差一段。"""
    return re.sub(r"<h1[^>]*>.*?</h1>", "", html or "", flags=re.S | re.I)


def local_index():
    """{cid: {"title": str, "sig": str}} —— 本地内容包是唯一权威来源。

    候选来源不止一个，按优先级填（都填，命中谁算谁）：
      · `out/bili/<id>/article.html` —— 发 B站时实际提交的那一版，最准
      · `out/articles/<id>.html`     —— 母版。**有些 case 只在草稿箱存过、
        从没生成过 out/bili 包**（vid-ai / vectosolve / publbee / podawaa /
        augora-ai 这 5 条就是这样），只认前一个来源会把它们判成"没归属"。
    """
    idx = {}

    def touch(cid):
        return idx.setdefault(cid, {"title": "", "sig": ""})

    if os.path.isdir(OUT_BILI):
        for cid in sorted(os.listdir(OUT_BILI)):
            d = os.path.join(OUT_BILI, cid)
            if not os.path.isdir(d):
                continue
            rec = touch(cid)
            np = os.path.join(d, "note.json")
            if os.path.isfile(np):
                try:
                    with open(np, encoding="utf-8") as f:
                        rec["title"] = (json.load(f).get("title") or "")
                except ValueError:
                    pass
            ap = os.path.join(d, "article.html")
            if os.path.isfile(ap):
                with open(ap, encoding="utf-8", errors="replace") as f:
                    rec["sig"] = _norm(_drop_h1(f.read()))[:SIG_LEN]

    if os.path.isdir(OUT_ART):
        for fn in sorted(os.listdir(OUT_ART)):
            if not fn.endswith(".html"):
                continue
            cid = fn[:-5]
            rec = touch(cid)
            with open(os.path.join(OUT_ART, fn), encoding="utf-8",
                      errors="replace") as f:
                html = f.read()
            if not rec["sig"]:
                rec["sig"] = _norm(_drop_h1(html))[:SIG_LEN]
            if not rec["title"]:
                m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
                if m:
                    rec["title"] = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return idx


def pull_drafts(port=None):
    """从线上草稿箱拉 [{article_id, title, summary, mtime}]。"""
    import bilibili_publish as bp
    import bili_draft_api as ba
    import wechat_publish as wp
    cdp = wp.CDP(port or bp.PUB_PORT)
    ba._bili_tab(cdp)
    return ba.list_drafts(cdp) or []


def _ymd(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ts)))
    except Exception:                                   # noqa: BLE001
        return ""


def plan(drafts, idx, recs=None):
    """给每条线上草稿找归属 → {"claimed": [...], "unclaimed": [...]}。

    `how` ∈ already / title / body —— 写进 note，日后回看知道这条当初是
    怎么认出来的（标题口径一旦改过，只有这个字段还能解释）。
    """
    recs = recs if recs is not None else L.load("bilibili")
    owned = {}
    for cid, r in recs.items():
        for key in ("draft_remote_id", "remote_id"):
            v = r.get(key)
            if v:
                owned.setdefault(str(v), cid)

    titles = {}
    for cid, rec in idx.items():
        if rec["title"]:
            titles.setdefault(_norm(rec["title"]), []).append(cid)

    def by_body(body, win):
        """按正文前缀认领。窗口逐档放短，直到唯一命中。

        🚨 为什么要放短而不是"模糊匹配"：线上 `summary` 是**提交时被加工过
           的那一版**（可能少一句、可能整段顺序有微调），跟本地母版逐字比
           比不动。放短只是换个更稳的锚点，判据仍然是**逐字相等**，
           不是相似度打分 —— 后者正是 2026-10-19 误判 13 条幽灵的那一套。
        """
        key = body[:win]
        if len(key) < 40:
            return []
        bucket = _sig_buckets.setdefault(win, {})
        if not bucket:
            for cid, rec in idx.items():
                if rec["sig"]:
                    bucket.setdefault(rec["sig"][:win], []).append(cid)
        return bucket.get(key) or []

    _sig_buckets = {}
    claimed, unclaimed = [], []
    for d in drafts:
        aid = str(d.get("article_id") or "")
        title = d.get("title") or ""
        summary = d.get("summary") or d.get("content") or ""
        item = {"article_id": aid, "title": title,
                "at": _ymd(d.get("mtime")), "mtime": d.get("mtime")}
        if not aid:
            unclaimed.append(dict(item, why="草稿没有 article_id"))
            continue
        if aid in owned:
            # 幂等：已经认领过就跳过（--apply 时不重写，避免刷新 at 掩盖真相）
            claimed.append(dict(item, cid=owned[aid], how="already",
                                note="台账已记"))
            continue
        hit = titles.get(_norm(title)) or []
        if len(hit) == 1:
            claimed.append(dict(item, cid=hit[0], how="title",
                                note="标题精确命中"))
            continue
        if len(hit) > 1:
            unclaimed.append(dict(item, why="标题同时命中多个 case：%s"
                                       % "/".join(hit)))
            continue
        # 正文认领：窗口从 200 字逐档收到 60 字，任一一档**唯一**命中就算数
        body = _norm(summary)
        got = None
        for win in (SIG_LEN, 120, 60):
            hit = by_body(body, win)
            if len(hit) == 1:
                got = (win, hit[0])
                break
            if len(hit) > 1:
                unclaimed.append(dict(item, why="正文前 %d 字同时命中多个 "
                                           "case：%s" % (win, "/".join(hit))))
                got = "ambiguous"
                break
        if got and got != "ambiguous":
            win, cid = got
            claimed.append(dict(item, cid=cid, how="body",
                                note="正文前 %d 字精确命中" % win))
            continue
        if got == "ambiguous":
            continue
        unclaimed.append(dict(item, why="标题和正文都没在本地内容包里找到对应"))
    return {"claimed": claimed, "unclaimed": unclaimed}


def apply_plan(p):
    """落账：只补草稿侧 ID，**不动已发布状态**。

    同一个 case 可能同时有草稿和正文两个对象；动状态就等于宣布一篇已经
    发出去的文章没发。
    """
    done = []
    for c in p["claimed"]:
        if c["how"] == "already":
            continue
        try:
            L.set_draft_id("bilibili", c["cid"], c["article_id"],
                           title=c["title"], at=c["at"],
                           note="｜草稿同步(%s): %s 认领 aid=%s"
                                % (c["at"], c["how"], c["article_id"]))
            done.append(c)
        except Exception as e:                          # noqa: BLE001
            print("  ! %s 写台账失败：%s" % (c["cid"], str(e)[:80]))
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="把认领到的写进台账")
    ap.add_argument("--json", help="把报告写成 JSON")
    ap.add_argument("--port", type=int, default=None, help="CDP 端口，默认 9223")
    ap.add_argument("--verbose", action="store_true", help="连已认领的一起打")
    a = ap.parse_args()

    try:
        drafts = pull_drafts(a.port)
    except Exception as e:                              # noqa: BLE001
        print("✗ 拉线上草稿失败：%s" % str(e)[:120])
        return 1

    before = len(L.ids("bilibili", L.DRAFT))
    p = plan(drafts, local_index())
    claimed = p["claimed"]
    fresh = [c for c in claimed if c["how"] != "already"]
    unclaimed = p["unclaimed"]

    print("线上草稿 %d 条 ｜ 认领 %d（其中新 %d）｜ 未认领 %d"
          % (len(drafts), len(claimed), len(fresh), len(unclaimed)))
    if a.verbose:
        for c in claimed:
            print("  ✓ %-22s aid=%-8s %s（%s）"
                  % (c["cid"], c["article_id"], c["title"][:30], c["note"]))
    else:
        for c in fresh:
            print("  + %-22s aid=%-8s %s（%s）"
                  % (c["cid"], c["article_id"], c["title"][:30], c["note"]))
        if claimed and not fresh:
            print("  · %d 条已在台账里，无需改动" % len(claimed))
    for u in unclaimed:
        print("  ? aid=%-8s %s\n      %s" % (u["article_id"], u["title"][:34],
                                             u["why"]))

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False, indent=1)
        print("  → %s" % a.json)

    if a.apply and fresh:
        done = apply_plan(p)
        after = len(L.ids("bilibili", L.DRAFT))
        print("写台账 %d 条：草稿条数 %d → %d" % (len(done), before, after))

    rc = 0 if not unclaimed else 1
    print("%s" % ("✓ 全部认领完毕" if rc == 0
                  else "✗ %d 条草稿没认领到 case（未自动写账）" % len(unclaimed)))
    return rc


if __name__ == "__main__":
    sys.exit(main())
