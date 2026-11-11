#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""同步公众号 2247485052 改标题/摘要后的本地三处镜像（PRD #135）。

只改 `wx-100000530` 这一条已发表记录的 title/desc，不动：
  · `cases.json` 的 one_liner（案例库字段，真值在 SQLite case_library）
  · `公众号-coral拆解.html` 的「摘要（建议）」/ 小红书正文 / dist 构建产物
    （都是内容素材里的同一句话，不是标题；且站点是静态预渲染，本轮不部署）
先备份 db 与两个 json；写盘用原序列化格式 round-trip 校验，失败就拒绝写。
"""
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OLD_TITLE = "Coral：帮你把开源 AI agent 部署、托管、接上各种业务系统——卖的是「配置」，不是模型"
NEW_TITLE = "Coral：帮你把开源 AI agent 部署、托管、接上各种业务系统，卖的是「配置」，不是模型"
OLD_DIGEST = OLD_TITLE[len("Coral："):]
NEW_DIGEST = NEW_TITLE[len("Coral："):]
KEY = "wx-100000530"
STAMP = datetime.now().strftime("%H%M%S")


def backup(path):
    dst = os.path.join(ROOT, "tmp", "%s.%s.bak" % (os.path.basename(path), STAMP))
    shutil.copy2(path, dst)
    print("  备份", os.path.relpath(dst, ROOT))


def main():
    db = os.path.join(ROOT, "data", "ledger.db")
    wp = os.path.join(ROOT, "data", "wechat_published.json")
    ws = os.path.join(ROOT, "data", "wechat_standalone.json")

    # --- 只读核对：三处都还是旧值才动手 ---
    print("[只读核对]")
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    row = c.execute("select case_id,platform,state,title from platform_posts "
                    "where case_id=? and platform='wechat'", (KEY,)).fetchone()
    print("  db:", dict(row) if row else None)
    assert row and row["title"] == OLD_TITLE, "db 不是预期旧标题，停住"
    for p in (wp, ws):
        d = json.load(open(p, encoding="utf-8"))
        print("  %s: title=%r desc=%r" % (os.path.basename(p),
                                          d.get(KEY, {}).get("title"),
                                          d.get(KEY, {}).get("desc")))
        assert d[KEY]["title"] == OLD_TITLE, "%s title 不是旧值" % p

    print("\n[备份]")
    for p in (db, wp, ws):
        backup(p)

    # --- 1) SQLite 单行 UPDATE ---
    print("\n[写 db]")
    cur = c.execute("update platform_posts set title=? "
                    "where case_id=? and platform='wechat' and title=?",
                    (NEW_TITLE, KEY, OLD_TITLE))
    assert cur.rowcount == 1, "UPDATE 行数 %s ≠ 1" % cur.rowcount
    c.commit()
    got = c.execute("select title from platform_posts where case_id=? "
                    "and platform='wechat'", (KEY,)).fetchone()[0]
    print("  回读 title =", repr(got))
    assert got == NEW_TITLE
    c.close()

    # --- 2) 两个 json：原格式 round-trip，逐字只改目标条目 ---
    for p, change_desc in ((wp, False), (ws, True)):
        print("\n[写 %s]" % os.path.basename(p))
        raw = open(p, encoding="utf-8").read()
        d = json.loads(raw)
        matched = None
        for kw in (dict(indent=2, ensure_ascii=False),
                   dict(indent=2, ensure_ascii=False, sort_keys=True),
                   dict(indent=1, ensure_ascii=False, sort_keys=True)):
            for nl in ("", "\n"):
                if json.dumps(d, **kw) + nl == raw:
                    matched = (kw, nl)
                    break
            if matched:
                break
        assert matched, "%s 序列化格式无法逐字 round-trip，拒绝写盘" % p
        print("  格式", matched[0], "尾换行", bool(matched[1]))
        before = json.dumps(d, ensure_ascii=False)
        d[KEY]["title"] = NEW_TITLE
        if change_desc and "desc" in d[KEY]:
            assert d[KEY]["desc"] == OLD_DIGEST, "desc 不是预期旧摘要"
            d[KEY]["desc"] = NEW_DIGEST
        out = json.dumps(d, **matched[0]) + matched[1]
        # 差异只能落在目标条目的 title/desc
        after = json.loads(out)
        diffs = [k for k in d if d[k] != after.get(k)]
        assert diffs == [], "序列化丢内容：%s" % diffs
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(out)
        got = json.load(open(p, encoding="utf-8"))[KEY]
        print("  回读 title =", repr(got["title"]))
        print("  回读 desc  =", repr(got.get("desc")))
        assert got["title"] == NEW_TITLE
        assert not change_desc or got.get("desc") == NEW_DIGEST

    # --- 3) 全库残留复核：只该剩「内容素材」里的同一句话 ---
    print("\n[复核：旧标题仍镜像为已发表 title 的地方应为 0]")
    for p in (wp, ws):
        d = json.load(open(p, encoding="utf-8"))
        n = sum(1 for k, v in d.items()
                if isinstance(v, dict) and v.get("title") == OLD_TITLE)
        print("  %s 残留 title 条数 = %d" % (os.path.basename(p), n))
        assert n == 0

    print("\n✓ 三处镜像已同步；内容素材（one_liner / 源稿摘要建议 / 小红书正文 / dist）未动。")


if __name__ == "__main__":
    sys.exit(main() or 0)
