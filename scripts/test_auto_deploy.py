#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_deploy.py 的回归测试。

这个脚本的价值全在**判断得准**：

  · 判「有差异」判错（把没变的说成变了）→ 每天空转部署一次，纯浪费
  · 判「没差异」判错（把变了的说成没变）→ 改了东西永远不上线，而且没有提示

两种错都不会报错、不会崩，只会安静地做错事。所以这里既测归一化
（时间戳不能算差异），也用**真实构建的两份产物**验证一遍 —— 后者才是
决定性证据：如果归一化没覆盖某个字段，那条断言立刻红。

只读，不部署、不联网。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import auto_deploy as AD                                       # noqa: E402


class TestNormalize(unittest.TestCase):
    """归一化：抹掉「内容等价但字节不同」的部分，别的一律不动。"""

    def test_strips_generated_at_value(self):
        a = AD.normalize("data.json", b'{"generated_at": "2026-09-20 16:29:15"}')
        b = AD.normalize("data.json", b'{"generated_at": "2026-09-21 09:00:00"}')
        self.assertEqual(a, b)

    def test_strips_stats_inbox(self):
        """stats.inbox 是源数据的采集条数，随每日采集变 ——
        但它不代表读者看到的内容变了，不能算差异（否则每天空转）。"""
        a = AD.normalize("data.json", b'{"stats": {"inbox": 400}}')
        b = AD.normalize("data.json", b'{"stats": {"inbox": 441}}')
        self.assertEqual(a, b)

    def test_keeps_real_content_change(self):
        """真正的数据变化必须保留 —— 否则「改了却不上线」。"""
        a = AD.normalize("data.json", b'{"cases": [{"id": "x"}]}')
        b = AD.normalize("data.json", b'{"cases": [{"id": "x"}, {"id": "y"}]}')
        self.assertNotEqual(a, b)

    def test_keeps_dates_in_body(self):
        """正文里的日期不能被误伤 —— 那是真实内容（比如采集日期）。
        所以只替换 generated_at 的**值**，不做全文件日期替换。"""
        a = AD.normalize("data.json", '{"note": "采集于 2026-09-19"}'.encode())
        self.assertIn("2026-09-19", a.decode())

    def test_non_json_files_untouched(self):
        raw = b"body { color: red } 2026-09-20"
        self.assertEqual(AD.normalize("style.css", raw), raw)
        self.assertEqual(AD.normalize("index.html", raw), raw)

    def test_applies_to_data_js_too(self):
        """data.js 是同一份数据的 JS 版，同样含时间戳。"""
        raw = b'window.DATA = {"generated_at": "2026-09-20 16:29:15"};'
        self.assertNotIn(b"2026-09-20", AD.normalize("data.js", raw))

    def test_digest_ignores_timestamps(self):
        a = AD.digest("data.json", b'{"generated_at": "x", "n": 1}')
        b = AD.digest("data.json", b'{"generated_at": "y", "n": 1}')
        self.assertEqual(a, b)


class TestDiffManifest(unittest.TestCase):
    def test_identical(self):
        c, m, e = AD.diff_manifest({"a": "1"}, {"a": "1"})
        self.assertEqual((c, m, e), ([], [], []))

    def test_changed(self):
        c, m, e = AD.diff_manifest({"a": "1"}, {"a": "2"})
        self.assertEqual(c, ["a"])
        self.assertEqual((m, e), ([], []))

    def test_missing_on_remote(self):
        """线上取不到（首次上线 / 新增页）也算需要部署。"""
        c, m, e = AD.diff_manifest({"a": "1"}, {"a": None})
        self.assertEqual(m, ["a"])
        self.assertEqual((c, e), ([], []))

    def test_extra_remote_files_reported_but_do_not_trigger(self):
        """线上多出来的文件**不触发部署** —— 部署也不会删它们，
        拿它去触发等于每天白跑。所以只报告，让人自己判断。"""
        c, m, e = AD.diff_manifest({"a": "1"}, {"a": "1", "old.html": "9"})
        self.assertEqual(e, ["old.html"])
        self.assertEqual((c, m), ([], []))

    def test_deterministic_order(self):
        """输出顺序要稳定，便于人读和断言。"""
        c, _, _ = AD.diff_manifest({"b": "1", "a": "1"}, {"a": "2", "b": "2"})
        self.assertEqual(c, ["a", "b"])


class TestRealBuildsNormalizeEqual(unittest.TestCase):
    """决定性断言：用**真实构建**的两份产物验证归一化够不够。

    同一份数据连续构建两次，本来就该判定为「无差异」。
    实测 39 个产物里只有 data.json / data.js 会变 —— 这个测试把
    「只有这两个」和「这两个归一化后一致」同时钉死：

      · 如果哪天 build_static 往别的产物里塞了时间戳，第一条会红
        （表现会是每天空转部署一次，不红的话根本发现不了）
      · 如果 data.json 里新增了别的易变字段，第二条会红

    慢（要构建两次），但这是整个脚本正确性的地基。
    """

    @classmethod
    def tearDownClass(cls):
        for d in getattr(cls, "_dirs", []):
            shutil.rmtree(d, ignore_errors=True)

    def test_two_builds_are_equivalent_after_normalize(self):
        import build_static as B

        tmp = tempfile.mkdtemp()
        self._dirs = [tmp]
        a, b = os.path.join(tmp, "a"), os.path.join(tmp, "b")
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()):
            B.build(a, include_inbox=False)
            B.build(b, include_inbox=False)

        ma, mb = AD.local_manifest(a), AD.local_manifest(b)
        self.assertTrue(ma, "构建没产出文件")
        self.assertEqual(sorted(ma), sorted(mb), "两次构建的文件清单不同")

        # 1) 哪些文件是**不稳定**的（含时间戳）
        raw_diff = []
        for rel in ma:
            ra = open(os.path.join(a, rel), "rb").read()
            rb = open(os.path.join(b, rel), "rb").read()
            if ra != rb:
                raw_diff.append(rel)

        # 允许且只允许 data.json / data.js 不稳定
        self.assertEqual(sorted(raw_diff), ["data.js", "data.json"],
                         "有别的产物不稳定（含时间戳？）—— 那会让兜底部署每天空转。"
                         "把它们加进 auto_deploy._TIMESTAMPY 或查清原因。")

        # 2) 归一化之后，全清单必须一致
        self.assertEqual(ma, mb,
                         "归一化后仍有差异：说明 data.json/data.js 里还有别的易变字段。")


class TestSiteBase(unittest.TestCase):
    def test_reads_url_from_site_json(self):
        base = AD.site_base()
        self.assertTrue(base.startswith("http"), "data/site.json 的 url 读不出来")
        self.assertFalse(base.endswith("/"))

    def test_public_dir_is_relative_to_root(self):
        self.assertEqual(AD.PUBLIC, os.path.join(ROOT, "public"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
