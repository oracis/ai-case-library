# -*- coding: utf-8 -*-
"""草稿占位符回归锁。

占位符这件事的麻烦在于它是**静默**的：`gen_platform_drafts.py` 批量生成时，
模型会在「为什么能成 / 方法论」「可复制性」「我的判断」三节交白卷，
留下一句「虽然具体的数据待补充，但我们可以……」，而流水线照常写出文件、
台账照常记 pending —— 一路绿灯，直到推到平台上才当场露馅。

所以这里锁三条：

1. `drafts/` 里不许再出现「待补充」（2026-10-27 补掉 21 处后的不变量）；
2. `fill_draft_placeholders.FILLS` 每段替换内容本身不许含占位符
   —— 否则补了等于没补；
3. 每个草稿首行必须是 `# ` 开头 —— `gen_platform_drafts.py` 靠这个判据
   做断点续跑，首行一旦被抹掉，重跑会把整批重新生成一遍（花钱且覆盖）。
"""

import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

DRAFTS = os.path.join(ROOT, "drafts")
PLACEHOLDER = "待补充"


def read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def md_files():
    if not os.path.isdir(DRAFTS):
        return []
    out = []
    for plat in sorted(os.listdir(DRAFTS)):
        pdir = os.path.join(DRAFTS, plat)
        if not os.path.isdir(pdir):
            continue
        for fn in sorted(os.listdir(pdir)):
            if fn.endswith(".md"):
                out.append(os.path.join(pdir, fn))
    return out


class TestDraftPlaceholders(unittest.TestCase):

    def setUp(self):
        self.files = md_files()

    def test_草稿目录存在且有内容(self):
        # 目录被误删时后面的断言会「因为没文件而全绿」，这一条先兜住
        self.assertTrue(self.files, "drafts/ 里一个 .md 都没有")

    def test_全目录不含占位符(self):
        dirty = []
        for p in self.files:
            txt = read(p)
            if PLACEHOLDER in txt:
                n = sum(1 for ln in txt.split("\n") if PLACEHOLDER in ln)
                dirty.append("%s（%d 处）" % (os.path.relpath(p, ROOT), n))
        self.assertFalse(dirty, "仍有占位符：\n  " + "\n  ".join(dirty))

    def test_替换内容本身不含占位符(self):
        import fill_draft_placeholders as m
        self.assertTrue(m.FILLS, "FILLS 表是空的")
        for key, fills in m.FILLS.items():
            self.assertTrue(fills, "%s 的替换内容是空的" % (key,))
            for i, s in enumerate(fills):
                self.assertNotIn(PLACEHOLDER, s,
                                 "%s 第 %d 段里还带着占位符" % (key, i + 1))
                self.assertGreater(len(s), 30,
                                   "%s 第 %d 段太短，像没写完" % (key, i + 1))

    def test_口径纠错的替换目标不带占位符也不为空(self):
        import fill_draft_placeholders as m
        for plat, cid, old, new in m.MISCALIBER_FIXES:
            self.assertTrue(old.strip(), "%s/%s 的原文为空" % (plat, cid))
            self.assertTrue(new.strip(), "%s/%s 的新文为空" % (plat, cid))
            self.assertNotEqual(old, new, "%s/%s 改了个寂寞" % (plat, cid))

    def test_首行井号没被抹掉(self):
        # gen_platform_drafts.py 用 first.startswith("# ") 判断「这个文件
        # 已经生成过了」，首行一丢，重跑就是整批重生成。
        bad = [os.path.relpath(p, ROOT) for p in self.files
               if not read(p).split("\n")[0].startswith("# ")]
        self.assertFalse(bad, "首行不是 # 开头：\n  " + "\n  ".join(bad))

    def test_草稿里的金额都能在候选数据里找到出处(self):
        """所有 $/€/¥ 数字必须能在库里找到同形数字。

        这是从反向样本七篇那边搬过来的判据（PRD §6.1 #19）。草稿是模型写的，
        最容易出的事就是「顺手编一个更像的数」。

        ⚠ 2026-10-29 补 `cases.json` 一起查：原判据只查 candidates.json，
        但**候选一旦晋升就离开候选池**，它的草稿金额立刻「查无出处」——
        这不是编造，是查错了地方。现在按「候选 + 已发布案例」合成出处池，
        判据的本意（这个数是不是凭空来的）不变，只是不再把已发布的案例
        误判成编造。
        """
        import json
        import re
        cand_p = os.path.join(ROOT, "data", "candidates.json")
        case_p = os.path.join(ROOT, "data", "cases.json")
        if not os.path.isfile(cand_p) and not os.path.isfile(case_p):
            self.skipTest("没有 data/candidates.json 也没有 data/cases.json")
        pool = []

        def walk(x):
            if isinstance(x, str):
                pool.append(x)
            elif isinstance(x, list):
                for i in x:
                    walk(i)
            elif isinstance(x, dict):
                for v in x.values():
                    walk(v)

        for p in (cand_p, case_p):
            if os.path.isfile(p):
                d = json.loads(read(p))
                walk(d["cases"] if isinstance(d, dict) and "cases" in d else d)
        pool = "\n".join(pool)

        bad = []
        for p in self.files:
            txt = read(p)
            for n in set(re.findall(r'[$€¥]\s?[\d,.]+[KMB万千]?', txt)):
                core = n.replace("$", "").replace("€", "").replace("¥", "").strip()
                if core and core not in pool:
                    bad.append("%s: %s" % (os.path.relpath(p, ROOT), n))
        self.assertFalse(bad, "金额在候选数据里找不到出处：\n  " + "\n  ".join(bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)
