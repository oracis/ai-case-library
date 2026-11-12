#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""candidate_store 的回归测试（2026-11-06 收口 50 处调用点时补）。

钉住四件事：
  1. **格式**：写回必须逐字节保持原文件的 indent / 换行 / 末尾换行。
     收口前这里有三套互相矛盾的口径（`apply_draft_text` 写死 CRLF、
     `verify_case` 文本模式写 + 补尾换行、其余各自探测），一次写入就整本 diff 全红。
  2. **原子性/隔离**：走 atomicio，不留 .tmp / .lock；`path=` 绝不碰真台账。
  3. **坏数据不当空池**：半截 JSON 必须响亮抛错。
  4. **收口本身**：生产脚本不许再直接 open/json.dump 候选池；通用层必须委托 CSt。
     这类退化不会报错，只会在某次断电后留半截 JSON、或某次写入翻掉换行。

测试全程只写临时目录；唯一接触真 `data/candidates.json` 的是只读的
`check()`，不写盘。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import candidate_store as CSt  # noqa: E402


class TestFormatRoundTrip(unittest.TestCase):
    """写回格式 = 读到的格式，不写死任何常量。"""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "candidates.json")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _seed(self, raw_bytes):
        with open(self.p, "wb") as f:
            f.write(raw_bytes)

    def test_LF缩进2无尾换行_磁盘现状(self):
        obj = [{"id": "a", "name": "中文"}, {"id": "b"}]
        raw = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self._seed(raw)
        self.assertEqual(CSt.detect_fmt(self.p), (2, "\n", False))
        cands = CSt.load_candidates(self.p)
        cands.append({"id": "c"})
        self.assertEqual(CSt.save_candidates(cands, path=self.p), (2, "\n", False))
        with open(self.p, "rb") as f:
            out = f.read()
        self.assertNotIn(b"\r\n", out)
        self.assertFalse(out.endswith(b"\n"))
        self.assertEqual(json.loads(out.decode("utf-8"))[-1], {"id": "c"})
        self.assertTrue(CSt.check(self.p)[0])

    def test_CRLF缩进1有尾换行_非默认形态也原样保持(self):
        """test_publish_ready 造的就是这种文件，探测不到就会洗成默认格式。"""
        obj = [{"id": "a"}]
        raw = (json.dumps(obj, ensure_ascii=False, indent=1) + "\n") \
            .replace("\n", "\r\n").encode("utf-8")
        self._seed(raw)
        self.assertEqual(CSt.detect_fmt(self.p), (1, "\r\n", True))
        CSt.save_candidates([{"id": "a"}, {"id": "b"}], path=self.p)
        with open(self.p, "rb") as f:
            out = f.read()
        self.assertIn(b"\r\n", out)
        self.assertTrue(out.endswith(b"\r\n"))
        # indent=1：数组元素缩进 1 空格、元素内字段缩进 2 空格
        self.assertIn('\n  "id"', out.decode("utf-8").replace("\r\n", "\n"))
        self.assertTrue(CSt.check(self.p)[0])

    def test_中文不转义(self):
        self._seed(json.dumps([{"id": "x"}], ensure_ascii=False,
                             indent=2).encode("utf-8"))
        CSt.save_candidates([{"id": "x", "note": "月收入"}], path=self.p)
        with open(self.p, "rb") as f:
            self.assertIn("月收入".encode("utf-8"), f.read())

    def test_真台账只读自检_格式逐字节一致(self):
        """唯一碰真文件的一项：只读，不写盘。"""
        ok, msg = CSt.check()
        self.assertTrue(ok, msg)
        cands = CSt.load_candidates()
        self.assertIsInstance(cands, list)
        self.assertTrue(all(isinstance(c, dict) for c in cands))
        with open(CSt.CAND_PATH, "rb") as f:
            self.assertEqual(CSt.serialize(cands, fmt=CSt.detect_fmt()).encode("utf-8"),
                             f.read())

    def test_格式被写坏时check报FAIL(self):
        self._seed(b'[{"id":"a"}]')  # indent=None 的紧凑格式，探测不到
        ok, msg = CSt.check(self.p)
        self.assertFalse(ok)
        self.assertIn("格式不一致", msg)

    def test_空数组不许被误判成indent1(self):
        """2026-11-12 实测：`[]` 在 1/2/4 三种 indent 下字节完全相同，
        旧实现把循环第一个枚举值 1 当成「磁盘格式」，空候选池晋升 81 条后
        整本写成单空格缩进，与仓库历史非空版本（indent=2）漂移。
        """
        self._seed(b"[]")
        self.assertEqual(CSt.detect_fmt(self.p), CSt.DEFAULT_FMT)
        CSt.save_candidates([{"id": "a"}, {"id": "b"}], path=self.p)
        self.assertEqual(CSt.detect_fmt(self.p), CSt.DEFAULT_FMT)
        with open(self.p, "rb") as f:
            self.assertEqual(
                f.read(),
                json.dumps([{"id": "a"}, {"id": "b"}],
                           ensure_ascii=False, indent=2).encode("utf-8"))
        self.assertTrue(CSt.check(self.p)[0])

    def test_非空文件仍按真实字节探测格式(self):
        """根因修复只管退化输入，不许改变非空文件的既有行为。"""
        obj = [{"id": "a", "name": "中文"}]
        self._seed(json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8"))
        self.assertEqual(CSt.detect_fmt(self.p), (1, "\n", False))
        CSt.save_candidates(obj, path=self.p)
        self.assertEqual(CSt.detect_fmt(self.p), (1, "\n", False))


class TestUpsert(unittest.TestCase):

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "candidates.json")
        seed = [{"id": "a", "n": 1, "keep": "yes"}, {"id": "b", "n": 2}]
        with open(self.p, "wb") as f:
            f.write(json.dumps(seed, ensure_ascii=False, indent=2).encode("utf-8"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_合并更新_保留未带字段且原位(self):
        cands, added = CSt.upsert_candidate({"id": "a", "n": 9}, path=self.p)
        self.assertFalse(added)
        self.assertEqual(cands[0], {"id": "a", "n": 9, "keep": "yes"})
        self.assertEqual([c["id"] for c in cands], ["a", "b"])

    def test_replace_整条替换(self):
        cands, _ = CSt.upsert_candidate({"id": "a", "only": 1},
                                        path=self.p, replace=True)
        self.assertEqual(cands[0], {"id": "a", "only": 1})

    def test_新增默认追加末尾(self):
        cands, added = CSt.upsert_candidate({"id": "c"}, path=self.p)
        self.assertTrue(added)
        self.assertEqual([c["id"] for c in cands], ["a", "b", "c"])

    def test_prepend_新候选置顶_与server既有顺序一致(self):
        cands, added = CSt.upsert_candidate({"id": "c"}, path=self.p, prepend=True)
        self.assertTrue(added)
        self.assertEqual([c["id"] for c in cands], ["c", "a", "b"])

    def test_prepend_更新已有条目也移到开头(self):
        cands, added = CSt.upsert_candidate({"id": "b", "n": 8},
                                            path=self.p, prepend=True)
        self.assertFalse(added)
        self.assertEqual([c["id"] for c in cands], ["b", "a"])
        self.assertEqual(cands[0]["n"], 8)

    def test_缺id响亮报错(self):
        with self.assertRaises(ValueError):
            CSt.upsert_candidate({"name": "没 id"}, path=self.p)

    def test_upsert后格式不变且不留临时文件(self):
        CSt.upsert_candidate({"id": "c"}, path=self.p)
        self.assertTrue(CSt.check(self.p)[0])
        self.assertEqual(sorted(os.listdir(self.d)), ["candidates.json"])


class TestFailureAndIsolation(unittest.TestCase):

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "candidates.json")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_文件不存在当空池(self):
        self.assertEqual(CSt.load_candidates(self.p), [])

    def test_文件不存在时新建用默认格式(self):
        CSt.save_candidates([{"id": "new"}], path=self.p)
        self.assertEqual(CSt.detect_fmt(self.p), CSt.DEFAULT_FMT)
        self.assertTrue(CSt.check(self.p)[0])

    def test_半截JSON必须响亮抛错_不许当空池(self):
        with open(self.p, "w", encoding="utf-8") as f:
            f.write('[{"id": "a", ')
        with self.assertRaises(ValueError):
            CSt.load_candidates(self.p)

    def test_顶层不是列表要报错(self):
        with open(self.p, "w", encoding="utf-8") as f:
            json.dump({"id": "a"}, f)
        with self.assertRaises(ValueError):
            CSt.load_candidates(self.p)

    def test_path注入不碰默认真台账(self):
        with open(CSt.CAND_PATH, "rb") as f:
            before = f.read()
        CSt.save_candidates([{"id": "tmp"}], path=self.p)
        CSt.upsert_candidate({"id": "tmp2"}, path=self.p)
        with open(CSt.CAND_PATH, "rb") as f:
            self.assertEqual(f.read(), before)

    def test_load辅助接口(self):
        with open(self.p, "wb") as f:
            f.write(json.dumps([{"id": "a"}, {"id": "b"}],
                               ensure_ascii=False, indent=2).encode("utf-8"))
        self.assertEqual(CSt.load_ids(self.p), ["a", "b"])
        self.assertEqual(CSt.load_by_id(self.p)["b"], {"id": "b"})
        self.assertEqual(CSt.get_candidate("a", self.p), {"id": "a"})
        self.assertIsNone(CSt.get_candidate("zz", self.p))
        self.assertEqual(CSt.count(self.p), 2)


class TestCallSitesStayClosed(unittest.TestCase):
    """静态闸：生产脚本不许退回各自 open/json.dump 候选池。

    刻意例外（不是漏网）：
      * `test_*.py` / `e2e_verify_write.py` —— 独立验证与「写脏再恢复」固件，
        用被测层读就失去独立性；
      * `verify_case.py` 的备份还原是 `shutil.copy2`，要的是原字节；
      * `contentpack_ready.CAND_PATH` 只是兼容常量，不直接读写；
      * `tmp/` 一次性历史脚本是复现证据，不回改。
    """

    def test_通用层都委托candidate_store(self):
        checks = [
            ("server.py", "CSt.load_candidates(path=data_path(name))"),
            ("server.py", "CSt.save_candidates(payload, path=data_path(name))"),
            (os.path.join("scripts", "harvest.py"), "CSt.load_candidates(path=p)"),
            (os.path.join("scripts", "harvest.py"), "CSt.save_candidates(payload, path="),
            (os.path.join("scripts", "ai_verify.py"), "return CSt.load_candidates(path="),
            (os.path.join("scripts", "triage.py"), "return CSt.load_candidates(path="),
            (os.path.join("scripts", "htm_increment.py"), "return CSt.load_candidates(path="),
            (os.path.join("scripts", "promote_ready.py"), "return CSt.load_candidates(path="),
            (os.path.join("scripts", "audit_delisted.py"), "CSt.save_candidates(rows, path=CANDS)"),
        ]
        for rel, needle in checks:
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                src = f.read()
            self.assertIn(needle, src, "%s 丢了 candidate_store 收口：%s" % (rel, needle))

    def test_生产脚本不再直接写死换行或原地截断写候选池(self):
        banned = {
            os.path.join("scripts", "apply_draft_text.py"): '.replace("\\n", "\\r\\n")',
            os.path.join("scripts", "sync_headline_numbers.py"): 'open(CANDS, "w"',
            os.path.join("scripts", "audit_delisted.py"): 'save_candidates(rows)\n',
            os.path.join("scripts", "fill_case_text.py"): "save_like(raw, cands)",
            os.path.join("scripts", "verify_case.py"): 'json.dump(cands, f, ensure_ascii=False',
        }
        for rel, needle in banned.items():
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                src = f.read()
            self.assertNotIn(needle, src, "%s 又退回自写盘：%s" % (rel, needle))

    def test_contentpack_ready是薄兼容层且保留CAND_PATH(self):
        p = os.path.join(ROOT, "scripts", "contentpack_ready.py")
        with open(p, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("return CSt.load_candidates(path=CAND_PATH)", src)
        self.assertIn("return CSt.save_candidates(cands, path=CAND_PATH)", src)
        self.assertIn("CAND_PATH = ", src)  # test_atomicio / test_publish_ready 依赖


if __name__ == "__main__":
    unittest.main(verbosity=2)
