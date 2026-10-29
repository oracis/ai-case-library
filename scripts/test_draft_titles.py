# -*- coding: utf-8 -*-
"""草稿标题的「禁金额」回归锁（PRD §6.1 #19 立的铁律）。

禁金额不是审美问题，是**机器会改坏**：公众号的金额清洗器会把标题里的
数字剥掉，「从 0 到 40K MRR，这个营销工具是如何……」剩下
「从 0 到 ，这个营销工具是如何……」。所以禁的是「会被机器改坏」，
不是「不够醒目」。

更硬的一层是口径：把年化收入写成月收入、把累计写成月度，
跟反向样本那 33 条里 23 条「ARR 当 MRR」是同一个错——
只不过错在了我们自己要发出去的稿子上。这里一并锁住。
"""

import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

DRAFTS = os.path.join(ROOT, "drafts")


def read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def md_files():
    out = []
    if not os.path.isdir(DRAFTS):
        return out
    for plat in sorted(os.listdir(DRAFTS)):
        pdir = os.path.join(DRAFTS, plat)
        if not os.path.isdir(pdir):
            continue
        for fn in sorted(os.listdir(pdir)):
            if fn.endswith(".md"):
                out.append((plat, fn[:-3], os.path.join(pdir, fn)))
    return out


class TestDraftLedgerSync(unittest.TestCase):
    """台账 data/drafts_pending.json 的 title 必须与草稿首行一致。

    台账是「这条草稿叫什么」的唯一记录，将来发布脚本大概率直接取它的
    `title` 字段。而它只在 `gen_platform_drafts.py` 生成时被写入一次——
    之后任何对草稿首行的改动都不会回写。2026-10-27 实测：标题去金额改了
    41 条首行，台账里 46 条 title 仍留着带金额的旧版，推上去就是绕过铁律。

    同步用 `scripts/sync_draft_ledger.py`（幂等），这里只锁状态。
    """

    def setUp(self):
        self.ledger_path = os.path.join(ROOT, "data", "drafts_pending.json")
        if not os.path.isfile(self.ledger_path):
            self.skipTest("没有待推送台账，跳过")
        import json
        self.ledger = json.loads(read(self.ledger_path))
        self.records = self.ledger.get("drafts") or []

    def test_台账非空(self):
        self.assertTrue(self.records, "台账里一条草稿都没有")

    def test_台账_title_与草稿首行一致(self):
        bad = []
        for rec in self.records:
            rel = (rec.get("file") or "").replace("\\", "/")
            path = os.path.join(ROOT, rel)
            if not os.path.isfile(path):
                bad.append("草稿文件不存在：%s" % rel)
                continue
            head = read(path).split("\n")[0].strip()
            head = head[2:].strip() if head.startswith("# ") else ""
            if head != (rec.get("title") or "").strip():
                bad.append("%s\n      台账：%s\n      首行：%s"
                           % (rel, (rec.get("title") or "")[:50], head[:50]))
        self.assertFalse(bad, "台账 title 与草稿首行不一致（跑 "
                              "scripts/sync_draft_ledger.py）：\n  "
                         + "\n  ".join(bad))

    def test_台账_title_不许带金额(self):
        import fix_draft_titles as m
        bad = []
        for rec in self.records:
            t = rec.get("title") or ""
            if m.MONEY_RE.search(t):
                bad.append("%s  %s" % (rec.get("file"), t[:60]))
        self.assertFalse(bad, "台账 title 仍带金额：\n  " + "\n  ".join(bad))

    def test_台账_file_用正斜杠(self):
        # 入库后要能在 Linux 上打开；os.path.relpath 在 Windows 下给反斜杠
        bad = [rec.get("file") for rec in self.records
               if "\\" in (rec.get("file") or "")]
        self.assertFalse(bad, "file 字段含反斜杠：\n  " + "\n  ".join(bad[:5]))


class TestDraftTitles(unittest.TestCase):

    def setUp(self):
        import fix_draft_titles as m
        self.m = m
        self.files = md_files()

    def test_草稿目录非空(self):
        # 目录被误删时后面的断言会「因为没文件而全绿」，这一条先兜住
        self.assertTrue(self.files, "drafts/ 里一个 .md 都没有")

    def test_全目录标题不含金额(self):
        bad = []
        for plat, cid, path in self.files:
            head = read(path).split("\n")[0]
            if self.m.MONEY_RE.search(head):
                bad.append("%s/%s  %s" % (plat, cid, head[:60]))
        self.assertFalse(bad, "标题仍带金额：\n  " + "\n  ".join(bad))

    def test_标题表里的新标题本身合规(self):
        for key, t in self.m.TITLE_FIXES.items():
            self.assertTrue(t.startswith("# "),
                            "%s 新标题不是 # 开头：%s" % (key, t))
            self.assertFalse(self.m.MONEY_RE.search(t),
                             "%s 新标题仍带金额：%s" % (key, t))
            self.assertGreater(len(t), 10, "%s 新标题太短" % (key,))

    def test_正文纠错表不空不重(self):
        for plat, cid, old, new in self.m.BODY_FIXES:
            self.assertTrue(old.strip(), "%s/%s 原文为空" % (plat, cid))
            self.assertTrue(new.strip(), "%s/%s 新文为空" % (plat, cid))
            self.assertNotEqual(old, new, "%s/%s 改了个寂寞" % (plat, cid))

    def test_六个字的口径词不许出现在标题里(self):
        """「月入 / 月收入 / 月流水」是最容易把年化、累计说成月度的三个词。

        就算不带数字也不许出现——一旦出现，读者默认读成「每月」。
        """
        bad = []
        for plat, cid, path in self.files:
            head = read(path).split("\n")[0]
            for w in ("月入", "月收入", "月流水"):
                if w in head:
                    bad.append("%s/%s 含「%s」" % (plat, cid, w))
        self.assertFalse(bad, "\n  ".join(bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)
