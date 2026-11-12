#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""2026-11-12 定点补正（一次性，可复核）。

两件事，均先备份（tmp/*20261112_103547*）：
  A. wechat_standalone.json：两条历史 draft 记录仍指向已删除的公众号草稿
     100001350 / 100001362 ⇒ 去掉悬空 appmsgid、标 deleted、原 id 留痕。
  B. 11-06 知乎历史漏记 ext-ep-001-super-mario-bros：
     db platform_posts 单行 UPDATE + zhihu_drafts.json 定点改。

远端真值（三路独立回读，2026-11-12）：
  zhihu_creator.py articles  → aid 2100938408583611480, created 2026-11-06 13:38:51
  zhihu_creator.py check     → 匿名 200 / state=published / article / 标题命中
  zhihu_creator.py list      → 草稿箱 13 条里已无该 aid

铁律：JSON 用文本级定点替换（保持 CRLF），禁整本 json.dump；
      db 只 UPDATE 一行；改前后逐条断言其余记录完全不变。
"""
from __future__ import print_function

import hashlib
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

DATA = os.path.join(ROOT, "data")
SA = os.path.join(DATA, "wechat_standalone.json")
ZD = os.path.join(DATA, "zhihu_drafts.json")
MF = os.path.join(DATA, "publish_manifest.json")
DB = os.path.join(DATA, "ledger.db")
NL = "\r\n"

NOW = time.strftime("%Y-%m-%d %H:%M:%S")
PUB_TS = "2026-11-06 13:38:51"
AID = "2100938408583611480"


def rd(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def wr(path, text):
    # 二进制写，避免 text mode 把 CRLF 再翻一次
    with open(path, "wb") as f:
        f.write(text.encode("utf-8"))


def swap_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise SystemExit("✗ %s：期望精确命中 1 次，实际 %d 次" % (label, n))
    return text.replace(old, new, 1)


def assert_rest_unchanged(before, after, pairs, label):
    """把目标块 mask 掉后逐字节比对，证明只有目标块变了。"""
    a, b = before, after
    for old, new in pairs:
        a = a.replace(old, "<MASK>")
        b = b.replace(new, "<MASK>")
    if a != b:
        raise SystemExit("✗ %s：目标块之外还有字节变化" % label)
    print("  ✓ %s：目标块之外逐字节一致" % label)


def assert_other_records(before_text, after_text, touched, label):
    """语义层断言：除 touched 的 key 外，其余记录 dict 完全相同。"""
    x = json.loads(before_text)
    y = json.loads(after_text)
    if set(x) != set(y):
        raise SystemExit("✗ %s：顶层 key 集合变了" % label)
    diff = [k for k in x if k not in touched and x[k] != y[k]]
    if diff:
        raise SystemExit("✗ %s：无关记录被改动 %s" % (label, diff))
    print("  ✓ %s：其余 %d 条记录逐条不变" % (label, len(x) - len(touched)))


# ---------------------------------------------------------------- A · standalone
sa_old1 = NL.join([
    '  "ext-ep-001-super-mario-bros": {',
    '    "title": "游戏厅：Super Mario Bros",',
    '    "desc": "",',
    '    "tag": "万物解释者",',
    '    "published_at": "2026-11-03 13:18",',
    '    "status": "draft",',
    '    "appmsgid": "100001350",',
    '    "file": "ext-ep-001-super-mario-bros.html"',
    '  },',
    '',
])
sa_new1 = NL.join([
    '  "ext-ep-001-super-mario-bros": {',
    '    "title": "游戏厅：Super Mario Bros",',
    '    "desc": "",',
    '    "tag": "万物解释者",',
    '    "published_at": "2026-11-03 13:18",',
    '    "status": "deleted",',
    '    "deleted_appmsgid": "100001350",',
    '    "note": "2026-11-12 清理：已发 case 的重复草稿，远端草稿 100001350 已删除；同名文章已发表 appmsgid=2247484934，原 id 移入 deleted_appmsgid 留痕",',
    '    "file": "ext-ep-001-super-mario-bros.html"',
    '  },',
    '',
])
sa_old2 = NL.join([
    '  "ext-ep-002-2048": {',
    '    "title": "游戏厅：2048",',
    '    "desc": "",',
    '    "tag": "万物解释者",',
    '    "published_at": "2026-11-05 09:59",',
    '    "status": "draft",',
    '    "appmsgid": "100001362",',
    '    "file": "ext-ep-002-2048.html"',
    '  },',
    '',
])
sa_new2 = NL.join([
    '  "ext-ep-002-2048": {',
    '    "title": "游戏厅：2048",',
    '    "desc": "",',
    '    "tag": "万物解释者",',
    '    "published_at": "2026-11-05 09:59",',
    '    "status": "deleted",',
    '    "deleted_appmsgid": "100001362",',
    '    "note": "2026-11-12 清理：已发 case 的重复草稿，远端草稿 100001362 已删除；同名文章已发表 appmsgid=2247484935，原 id 移入 deleted_appmsgid 留痕",',
    '    "file": "ext-ep-002-2048.html"',
    '  },',
    '',
])

# ---------------------------------------------------------------- B · zhihu_drafts
zd_old = NL.join([
    '  "ext-ep-001-super-mario-bros": {',
    '    "article_id": "%s",' % AID,
    '    "title": "游戏厅：马力欧兄弟，被 JS 重写的 1985 经典",',
    '    "url": "https://zhuanlan.zhihu.com/p/%s",' % AID,
    '    "status": "draft",',
    '    "ts": "2026-11-03 13:12:30"',
    '  },',
    '',
])
zd_new = NL.join([
    '  "ext-ep-001-super-mario-bros": {',
    '    "article_id": "%s",' % AID,
    '    "title": "游戏厅：马力欧兄弟，被 JS 重写的 1985 经典",',
    '    "url": "https://zhuanlan.zhihu.com/p/%s",' % AID,
    '    "status": "published",',
    '    "ts": "2026-11-03 13:12:30",',
    '    "published_ts": "%s",' % PUB_TS,
    '    "verified_at": "%s"' % NOW,
    '  },',
    '',
])


def main():
    print("补正时刻 %s" % NOW)
    print()

    # ---- 只读前置：manifest 本来就是 published，不动它 -------------------
    mf = json.load(open(MF, encoding="utf-8"))
    z = (mf.get("ext-ep-001-super-mario-bros") or {}).get("zhihu") or {}
    if z.get("state") != "published" or str(z.get("remote_id")) != AID:
        raise SystemExit("✗ manifest.zhihu 与远端不一致，需人工核：%r" % z)
    print("✓ publish_manifest.json 该条早已 published/aid 正确 ⇒ 本轮不动它")
    print()

    # ---- A -----------------------------------------------------------------
    sa_before = rd(SA)
    sa_after = swap_once(sa_before, sa_old1, sa_new1, "standalone / Mario")
    sa_after = swap_once(sa_after, sa_old2, sa_new2, "standalone / 2048")
    assert_rest_unchanged(sa_before, sa_after,
                          [(sa_old1, sa_new1), (sa_old2, sa_new2)],
                          "wechat_standalone.json")
    assert_other_records(sa_before, sa_after,
                         {"ext-ep-001-super-mario-bros", "ext-ep-002-2048"},
                         "wechat_standalone.json")
    d = json.loads(sa_after)
    for cid, dead in (("ext-ep-001-super-mario-bros", "100001350"),
                      ("ext-ep-002-2048", "100001362")):
        r = d[cid]
        assert r.get("status") == "deleted", r
        assert "appmsgid" not in r, r
        assert r.get("deleted_appmsgid") == dead, r
    print("  ✓ 两条记录已无 appmsgid 悬空引用；status=deleted；原 id 留痕")
    wr(SA, sa_after)
    print("  → 已写 %s" % os.path.relpath(SA, ROOT))
    print()

    # ---- B · zhihu_drafts.json --------------------------------------------
    zd_before = rd(ZD)
    zd_after = swap_once(zd_before, zd_old, zd_new, "zhihu_drafts / Mario")
    assert_rest_unchanged(zd_before, zd_after, [(zd_old, zd_new)],
                          "zhihu_drafts.json")
    assert_other_records(zd_before, zd_after,
                         {"ext-ep-001-super-mario-bros"},
                         "zhihu_drafts.json")
    wr(ZD, zd_after)
    print("  → 已写 %s（status=published / published_ts=%s / verified_at=%s）"
          % (os.path.relpath(ZD, ROOT), PUB_TS, NOW))
    print()

    # ---- B · SQLite 单行 UPDATE -------------------------------------------
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    def snapshot(exclude_id):
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM platform_posts WHERE id<>? ORDER BY id", (exclude_id,))]
        return hashlib.sha256(json.dumps(rows, ensure_ascii=False,
                                         sort_keys=True).encode("utf-8")).hexdigest()

    before_hash = snapshot(8013)
    row = conn.execute(
        "SELECT * FROM platform_posts WHERE id=8013").fetchone()
    if not row or row["case_id"] != "ext-ep-001-super-mario-bros" \
            or row["platform"] != "zhihu" or row["state"] != "draft":
        raise SystemExit("✗ 目标 db 行状态与预期不符：%r"
                         % (dict(row) if row else None))
    note = ("2026-11-12 补记：远端已发表列表 + 匿名 check state=published + "
            "草稿箱无该 id 三路回读命中；11-06 %s 经 UI 发表但未回写，"
            "11-09 错账清理曾把 published_at 清零，现按远端真值补回。" % PUB_TS)
    cur = conn.execute(
        "UPDATE platform_posts SET state=?, published_at=?, updated_at=?, note=? "
        "WHERE id=8013 AND case_id=? AND platform='zhihu' AND state='draft'",
        ("published", PUB_TS, NOW, note, "ext-ep-001-super-mario-bros"))
    if cur.rowcount != 1:
        conn.rollback()
        raise SystemExit("✗ UPDATE rowcount=%d（应为 1），已回滚" % cur.rowcount)
    after_hash = snapshot(8013)
    if before_hash != after_hash:
        conn.rollback()
        raise SystemExit("✗ 其余 platform_posts 行被改动，已回滚")
    conn.commit()
    got = dict(conn.execute(
        "SELECT id,case_id,platform,state,remote_id,published_at,updated_at "
        "FROM platform_posts WHERE id=8013").fetchone())
    conn.close()
    print("✓ ledger.db：单行 UPDATE 成功，其余行 hash 不变（%s）" % before_hash[:12])
    print("  ", json.dumps(got, ensure_ascii=False))
    print()

    # ---- 收尾：确认悬空引用真的没了 ---------------------------------------
    raw = rd(SA)
    for dead in ("100001350", "100001362"):
        hits = [k for k, v in json.loads(raw).items()
                if str(v.get("appmsgid")) == dead]
        print("  appmsgid=%s 现存引用：%s（留痕字段 deleted_appmsgid 不算引用）"
              % (dead, hits or "无"))
        if hits:
            raise SystemExit("✗ 仍有悬空引用")
    print()
    print("全部完成。")


if __name__ == "__main__":
    main()
