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
        for line in ("例：`scripts/xxx.py`（占位）", "`tmp/foo.py` 这种写法",
                     "`scripts/_oneoff/x.py` 比 `tmp/x.py` 深一层"):
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
        """基线入库，否则每次 CI 都跳过「tmp 新增」这一项（静默假绿）。"""
        base = C.load_baseline()
        self.assertIsNotNone(base, "缺 scripts/tmp_leaks_baseline.json")
        self.assertGreater(len(base), 0)

    def test_baseline_is_sorted_unique(self):
        import json
        data = json.loads(C.BASELINE.read_text(encoding="utf-8"))
        names = data.get("legacy_tmp_files") or data.get("legacy_tmp_scripts")
        self.assertIsNotNone(names)
        self.assertEqual(names, sorted(set(names)))

    def test_legacy_bare_name_format_still_readable(self):
        """首版基线只记裸文件名，升级格式后仍要读得懂（否则老 clone 全红）。"""
        import json
        import tempfile
        from pathlib import Path
        old = C.BASELINE
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "b.json"
            p.write_text(json.dumps({"legacy_tmp_scripts": ["a.py"]}), encoding="utf-8")
            C.BASELINE = p
            try:
                self.assertEqual(C.load_baseline(), {"a.py"})
            finally:
                C.BASELINE = old


class TestDepthRewrite(unittest.TestCase):
    """归档到 _oneoff/ 比 tmp/ 深一层，ROOT 必须跟着 +1 层。

    写成两层不报错，只是静默指向 scripts/ —— 等 ROOT/'data' 才炸，
    2026-10-18 搬进去的 15 个脚本就是这么坏的。
    """

    def test_os_path_dirname(self):
        src = "ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
        new, hits = C.rewrite_depth(src)
        self.assertEqual(hits, 1)
        self.assertIn("os.path.dirname(os.path.dirname(os.path.dirname(", new)

    def test_here_style(self):
        src = "HERE = os.path.dirname(os.path.abspath(__file__))\nROOT = os.path.dirname(HERE)\n"
        new, hits = C.rewrite_depth(src)
        self.assertEqual(hits, 1)
        self.assertIn("os.path.dirname(os.path.dirname(HERE))", new)

    def test_pathlib_style(self):
        src = "ROOT = Path(__file__).parent.parent\n"
        new, hits = C.rewrite_depth(src)
        self.assertEqual(hits, 1)
        self.assertIn("Path(__file__).parent.parent.parent", new)

    def test_indented_match(self):
        new, hits = C.rewrite_depth("    ROOT = Path(__file__).parent.parent\n")
        self.assertEqual(hits, 1)
        self.assertTrue(new.startswith("    ROOT"))

    def test_no_match_untouched(self):
        """已经三层的不要再加，避免二次归档把 ROOT 顶到仓库外。"""
        src = "ROOT = Path(__file__).parent.parent.parent\n"
        _, hits = C.rewrite_depth(src)
        self.assertEqual(hits, 0)


class TestVerifyRoot(unittest.TestCase):
    def test_wrong_depth_is_rejected(self):
        """tmp/ 里的两层写法搬到 _oneoff/ 后必须被判错，不能静默通过。"""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.py"
            p.write_text(
                "import os\nROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n",
                encoding="utf-8",
            )
            got, err = C.verify_root(p)
        self.assertIsNone(got)
        self.assertIn("ROOT 解析为", err or "")

    def test_correct_depth_passes_in_oneoff(self):
        """真在 _oneoff/ 下、三层写法 ⇒ 解析回仓库根。"""
        import tempfile
        from pathlib import Path
        C.ARCHIVE.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(suffix=".py", dir=str(C.ARCHIVE))
        os.close(fd)
        p = Path(name)
        p.write_text(
            "from pathlib import Path\nROOT = Path(__file__).parent.parent.parent\n",
            encoding="utf-8",
        )
        try:
            got, err = C.verify_root(p)
        finally:
            p.unlink()
        self.assertIsNone(err)
        self.assertEqual(got, C.ROOT.resolve())


class TestCandidateScope(unittest.TestCase):
    """判据边界：脚本拦、产物不拦 —— 假红会让闸被无视。"""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self._td = tempfile.TemporaryDirectory()
        self._old = C.TMP
        C.TMP = Path(self._td.name)
        for rel, body in (
            ("a.py", "x"),
            ("sub/b.sh", "x"),
            ("sub/c.ps1", "x"),
            ("note.md", "x"),
            ("sub/backup.md", "x"),
            ("out.txt", "x"),
            ("app.js", "x"),
            ("snap.json", "x"),
            ("shot.png", "x"),
        ):
            p = C.TMP / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")

    def tearDown(self):
        C.TMP = self._old
        self._td.cleanup()

    def test_scripts_every_depth(self):
        got = C.tmp_candidates()
        self.assertIn("a.py", got)
        self.assertIn("sub/b.sh", got)
        self.assertIn("sub/c.ps1", got)

    def test_notes_off_by_default(self):
        self.assertNotIn("note.md", C.tmp_candidates())

    def test_include_notes_only_toplevel(self):
        got = C.tmp_candidates(include_notes=True)
        self.assertIn("note.md", got)
        self.assertNotIn("sub/backup.md", got)  # 子目录 md 是备份产物

    def test_products_never_listed(self):
        for name in ("out.txt", "app.js", "snap.json", "shot.png"):
            self.assertNotIn(name, C.tmp_candidates(include_notes=True), name)

    def test_baseline_excludes_known(self):
        from unittest import mock
        with mock.patch.object(C, "load_baseline", return_value={"a.py"}):
            got = C.fresh_tmp_files()
        self.assertNotIn("a.py", got)
        self.assertIn("sub/b.sh", got)


class TestFreeName(unittest.TestCase):
    def test_never_overwrites(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "x.py").write_text("old", encoding="utf-8")
            got = C._free_name(d, "x.py")
            self.assertNotEqual(got.name, "x.py")
            self.assertTrue(got.name.endswith(".py"))


if __name__ == "__main__":
    unittest.main()
