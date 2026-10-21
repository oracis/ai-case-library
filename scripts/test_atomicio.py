#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""atomicio 的回归测试：原子写到底「原子」在哪（2026-10-21）。

为什么值得单独钉：`data/*.json` 被截断写的后果不是「格式难看」，而是整本
台账/候选池读不出来 —— 更坏的是上层 `except` 把它当空库，重复检测全失效。
这里锁三件事：内容原样落盘（不翻译换行）、不留临时文件、失败时收干净。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import atomicio  # noqa: E402


class TestWriteText(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "x.json")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_内容与换行原样落盘(self):
        # newline="" ⇒ `\n` 不许被 Windows 翻成 `\r\n`（翻了整文件 diff 全红）
        atomicio.write_text(self.p, '{"a": 1}\n{"b": 2}\n')
        with open(self.p, "rb") as f:
            self.assertEqual(f.read(), b'{"a": 1}\n{"b": 2}\n')

    def test_不留临时文件(self):
        atomicio.write_text(self.p, "x")
        self.assertEqual(sorted(os.listdir(self.d)), ["x.json"])

    def test_覆盖是替换不是追加(self):
        atomicio.write_text(self.p, "long content" * 100)
        atomicio.write_text(self.p, "short")
        with open(self.p, encoding="utf-8") as f:
            self.assertEqual(f.read(), "short")

    def test_父目录不存在会建(self):
        p = os.path.join(self.d, "a", "b", "c.json")
        atomicio.write_text(p, "{}")
        self.assertTrue(os.path.isfile(p))

    def test_写失败要收掉临时文件(self):
        # 目标是目录 ⇒ os.replace 必然失败；半截 tmp 不许留在盘上
        target = os.path.join(self.d, "dir")
        os.makedirs(target)
        with self.assertRaises(OSError):
            atomicio.write_text(target, "x")
        self.assertEqual(sorted(os.listdir(self.d)), ["dir"])

    def test_write_json可读回且中文不转义(self):
        atomicio.write_json(self.p, {"中文": [1, 2]})
        with open(self.p, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"中文": [1, 2]})
        with open(self.p, "rb") as f:
            self.assertIn("中文".encode("utf-8"), f.read())


class TestDataWritersUseAtomicIO(unittest.TestCase):
    """三个 `data/*.json` 写入口必须真的走原子写（2026-10-21 补）。"""

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_contentpack_ready按原格式原子写回(self):
        import contentpack_ready as CR

        p = os.path.join(self.d, "candidates.json")
        with open(p, "wb") as f:                       # 原格式：LF + indent=2 + 无尾换行
            f.write('[\n  {\n    "id": "a"\n  }\n]'.encode("utf-8"))
        old = CR.CAND_PATH
        CR.CAND_PATH = p
        try:
            CR.save_candidates([{"id": "a"}, {"id": "b"}])
        finally:
            CR.CAND_PATH = old
        with open(p, "rb") as f:
            raw = f.read()
        self.assertNotIn(b"\r\n", raw, "换行格式不许被翻成 CRLF")
        self.assertFalse(raw.endswith(b"\n"), "原文件没有末尾换行，也不该凭空多一个")
        self.assertEqual(len(json.loads(raw.decode("utf-8"))), 2)
        self.assertEqual(sorted(os.listdir(self.d)), ["candidates.json"])

    def test_draft_case_text落盘进度是原子写(self):
        import draft_case_text as DC

        p = os.path.join(self.d, "draft_text.json")
        old = DC.STAGE
        DC.STAGE = p
        try:
            DC.save_stage({"a": {"title": "标题"}})
        finally:
            DC.STAGE = old
        with open(p, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"a": {"title": "标题"}})
        self.assertEqual(sorted(os.listdir(self.d)), ["draft_text.json"])

    def test_fit_ready写cases走原子写(self):
        # fit_ready 的写盘在 main() 内联，没有可单测的函数 ⇒ 钉源码调用点
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "fit_ready.py"), encoding="utf-8").read()
        self.assertIn("atomicio.write_text(CASES_PATH, text)", src)
        self.assertNotIn('open(CASES_PATH, "w"', src)


if __name__ == "__main__":
    unittest.main()
