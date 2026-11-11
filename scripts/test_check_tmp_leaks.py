#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_tmp_leaks 的回归锁（PRD #138，2026-11-11）。

守的是「修复脚本散落在仓库外」这一类：`tmp/` 与 `scripts/_oneoff/` 曾被
`.gitignore` 忽略，而 `docs/PRD.md` 把它们当复现证据引用 —— 换机 / clone 后
脚本不在，同一个修复被迫重写。这里把判定规则钉死，防止闸本身被改松：

  ① docs 里出现 `tmp/x.py` ⇒ 判泄漏（「mv tmp/a.py scripts/a.py」式迁移记录除外）
  ② docs 里出现 `scripts/x.py` 但文件不存在 ⇒ 判陈旧引用
  ③ 示例占位符（xxx.py / foo.py）与**别的仓库**路径（open-source-games/scripts/…）
     不算 —— 假红会让闸被无视，这比漏报更坏

全离线：只读文本，不碰 git、不写盘。
跑法：cd scripts && python -X utf8 -m unittest test_check_tmp_leaks -v
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import check_tmp_leaks as C  # noqa: E402


def refs(line):
    return list(C.iter_doc_refs(line))


class TestTmpRefs(unittest.TestCase):
    def test_plain_tmp_ref_is_leak(self):
        self.assertEqual(
            refs("证据见 `tmp/fix_coral_title_20261111.py` 的输出"), 
            [("tmp", "tmp/fix_coral_title_20261111.py")],
        )

    def test_tmp_subdir_ref_is_leak(self):
        self.assertEqual(
            refs("跑 `tmp/htm_screen/parse_index.py`"),
            [("tmp", "tmp/htm_screen/parse_index.py")],
        )

    def test_migration_line_is_allowed(self):
        """「mv tmp/a.py scripts/a.py」是迁移记录：跳过 tmp 侧，目标仍要校验存在。"""
        self.assertEqual(
            refs("（`mv tmp/rewrite_yun_draft.py scripts/rewrite_yun_draft.py`）"),
            [("scripts", "scripts/rewrite_yun_draft.py")],
        )

    def test_scripts_ref_is_resolved_kind(self):
        self.assertEqual(
            refs("见 `scripts/publish_one.py`"), [("scripts", "scripts/publish_one.py")]
        )


class TestFalsePositiveGuards(unittest.TestCase):
    def test_placeholder_skipped(self):
        for line in ("例：`scripts/xxx.py`（占位）", "`tmp/foo.py` 这种写法"):
            self.assertEqual(refs(line), [], line)

    def test_other_repo_path_skipped(self):
        """别的仓库里的 scripts/ 不是本仓库的文件，不能判成缺失。"""
        self.assertEqual(refs("游戏仓 `open-source-games/scripts/strip_column_line.py`"), [])

    def test_dot_slash_normalized(self):
        self.assertEqual(refs("`./scripts/release.py`"), [("scripts", "scripts/release.py")])

    def test_non_py_untouched(self):
        self.assertEqual(refs("产物 `tmp/ledger_cleanup.json`"), [])


class TestBaseline(unittest.TestCase):
    def test_shipped_baseline_exists(self):
        """基线入库，否则每次 CI 都跳过「tmp 新增脚本」这一项（静默假绿）。"""
        base = C.load_baseline()
        self.assertIsNotNone(base, "缺 scripts/tmp_leaks_baseline.json")
        self.assertGreater(len(base), 0)

    def test_baseline_is_sorted_unique(self):
        import json
        data = json.loads(C.BASELINE.read_text(encoding="utf-8"))
        names = data["legacy_tmp_scripts"]
        self.assertEqual(names, sorted(set(names)))


if __name__ == "__main__":
    unittest.main()
