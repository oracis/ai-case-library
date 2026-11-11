#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按 2026-10-19 三方对账结论修正头条台账（幂等，可重复跑）。

对账结论（证据见 out/toutiao_reconcile.json）：

  真幽灵 3 条 —— 草稿箱与已发布列表**双侧都无**，本地却有内容包：
      aeo-engine / augora-ai / le19emetrou
    历史上 10-13 23:41 那批 `drafts --write` 回填进来的，之后线上
    消失（被删或从未建成）。留在草稿台账会永久挡住重发 ⇒ 移出，
    记进 data/toutiao_ghost.json 留痕。

  已发布但台账仍挂在草稿侧 5 条（草稿升格后没迁移）：
      vid-ai   『两年做到累计 $162 万，然后峰值留存掉到 46%』
      cometly  新篇『Cometly：广告数据归因分析』+ 旧篇『$20 万/月的营销归因生意』
      fastwrite-io / growingupvideo
    ⇒ 从 drafts 移到 published，note 写明远端标题（下次对账直接认）。

  映射修正 1 条：sierra 的 note 原写『$20 万/月的营销归因生意』，
  实测那篇正文讲的是 **Cometly**（「35.4% 是 Cometly 的 MRR 增长率」）；
  Sierra 自己那篇是『年收$200M：AI 客服解决才收钱』。

用法：
  python _oneoff/toutiao_fix_ledger.py --dry
  python _oneoff/toutiao_fix_ledger.py
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
ROOT = os.path.dirname(SCRIPTS)

DRAFT_F = os.path.join(ROOT, "data", "toutiao_drafts.json")
PUB_F = os.path.join(ROOT, "data", "toutiao_published.json")
GHOST_F = os.path.join(ROOT, "data", "toutiao_ghost.json")

GHOSTS = {
    "aeo-engine": "2026-10-19 对账：草稿箱 38 条 + 已发布 23 条双侧都无",
    "augora-ai": "2026-10-19 对账：草稿箱 38 条 + 已发布 23 条双侧都无",
    "le19emetrou": "2026-10-19 对账：草稿箱 38 条 + 已发布 23 条双侧都无",
}

# id -> (远端标题, 备注)
PROMOTE = {
    "vid-ai": ("两年做到累计 $162 万，然后峰值留存掉到 46%",
               "2026-10-19 对账正名：旧口径标题，正文＝「把想法/脚本变成"
               "可直接发布的短视频」，即 Vid.AI。早先误判为 GHOST。"),
    "cometly": ("Cometly：广告数据归因分析",
                "2026-10-19 对账：新旧两篇都在线上。旧篇『$20 万/月的营销"
                "归因生意，但不是一个人做的』（正文首行即 Cometly）。"
                "早先这条旧篇被错挂在 sierra 名下。"),
    "fastwrite-io": ("fastwrite.io：提升学术写作质量",
                     "2026-10-19 对账：草稿已升格为已发布，台账未迁移"),
    "growingupvideo": ("GrowingUpVideo：照片变脸电影级视频",
                       "2026-10-19 对账：草稿已升格为已发布，台账未迁移"),
}

FIX_NOTE = {
    "sierra": ("2026-10-19 对账修正：本条远端标题是『年收$200M：AI 客服解决"
               "才收钱』。原 note 写的『$20 万/月的营销归因生意』经正文核验"
               "是 Cometly（原文「35.4% 是 Cometly 的 MRR 增长率」），已改挂。"),
}


def _read(p):
    """返回 (rows, nl, indent)。两个台账缩进不一致（published 用 1 空格、
    drafts 用 2），按原样还原，否则 diff 会整本重排。"""
    if not os.path.isfile(p):
        return [], "\n", 2
    raw = open(p, "rb").read()
    lf = raw.count(b"\n") - raw.count(b"\r\n")
    crlf = raw.count(b"\r\n")
    nl = "\r\n" if crlf > lf else "\n"
    depth = 0
    i = raw.find(b"\n")
    if i >= 0:
        j = i + 1
        while j < len(raw) and raw[j:j + 1] == b" ":
            j += 1
        depth = j - i - 1
    indent = depth if 1 <= depth <= 4 else 2
    try:
        d = json.loads(raw.decode("utf-8"))
    except ValueError:
        d = []
    out = []
    for e in d:
        out.append(e if isinstance(e, dict) else {"id": e, "at": ""})
    return out, nl, indent


def _write(p, rows, nl, indent):
    text = json.dumps(rows, ensure_ascii=False, indent=indent)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text.replace("\n", nl))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--reconcile", default="out/toutiao_reconcile.json",
                    help="对账结果（用来判定「已发布且草稿箱已无稿」的冗余条目）")
    a = ap.parse_args()

    drafts, nl_d, in_d = _read(DRAFT_F)
    pubs, nl_p, in_p = _read(PUB_F)
    now = time.strftime("%Y-%m-%d %H:%M")

    # 已发布、且草稿箱里已经没有对应稿 ⇒ drafts 台账那条纯冗余
    # （pending_cases 取两个台账并集，删掉不影响幂等）。
    # 只有 voklit 例外：它的旧草稿确实还躺在草稿箱里，保留。
    redundant = set()
    rp = a.reconcile if os.path.isabs(a.reconcile) else os.path.join(ROOT, a.reconcile)
    if os.path.isfile(rp):
        with open(rp, "r", encoding="utf-8") as f:
            rc = json.load(f)
        for r in rc.get("rows", []):
            if r["state"] == "published" and r["in_draft_ledger"] \
                    and not r["draft_title"]:
                redundant.add(r["id"])
        print("对账显示：%d 条已发布条目在草稿箱已无稿" % len(redundant))

    keep_d, moved, ghosts = [], [], []
    for e in drafts:
        cid = e["id"]
        if cid in GHOSTS:
            ghosts.append(cid)
        elif cid in PROMOTE:
            moved.append(cid)
        elif cid in redundant:
            continue
        else:
            keep_d.append(e)

    pub_ids = {e["id"] for e in pubs}
    new_pub = list(pubs)
    for cid in moved:
        if cid in pub_ids:
            continue
        old = next((e for e in drafts if e["id"] == cid), {})
        title, note = PROMOTE[cid]
        new_pub.append({"id": cid, "at": old.get("at") or now,
                        "note": "%s（远端：『%s』）" % (note, title)})

    for e in new_pub:                       # sierra 的 note 修正
        if e["id"] in FIX_NOTE:
            e["note"] = FIX_NOTE[e["id"]]

    print("草稿台账 %d → %d（移出已发布 %d、冗余 %d、幽灵 %d）"
          % (len(drafts), len(keep_d), len(moved),
             len(drafts) - len(keep_d) - len(moved) - len(ghosts), len(ghosts)))
    print("已发台账 %d → %d" % (len(pubs), len(new_pub)))
    for c in moved:
        print("  ↑ %s  %s" % (c, PROMOTE[c][0][:40]))
    for c in ghosts:
        print("  ✗ %s  （记入 toutiao_ghost.json）" % c)

    if a.dry:
        print("\n（预演，未写盘）")
        return 0

    _write(DRAFT_F, keep_d, nl_d, in_d)
    _write(PUB_F, new_pub, nl_p, in_p)

    gh, nl_g, in_g = _read(GHOST_F)
    have = {e["id"] for e in gh}
    for cid in ghosts:
        if cid not in have:
            gh.append({"id": cid, "at": now, "reason": GHOSTS[cid],
                       "content_pack": "out/toutiao/%s/" % cid})
    _write(GHOST_F, gh, nl_g or "\n", in_g)
    print("\n已写 %s / %s / %s"
          % (os.path.basename(DRAFT_F), os.path.basename(PUB_F),
             os.path.basename(GHOST_F)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
