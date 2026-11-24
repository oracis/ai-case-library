# -*- coding: utf-8 -*-
"""pathguard.py 的护栏测试（离线，不碰网络/浏览器）。

守的线：**批量操作不允许「静默跳过 + 报成功」**。

背景（2026-11-24 实测）：清理备份时用 Git Bash 调 Windows Python，
路径写成 Bash 习惯的 `/d/open-source-games/.cache/xxx.json`。Windows Python
把它解析成 `D:\\d\\open-source-games\\...`，于是 isfile 返回 False、
删除循环默默跳过、最后还打印「已删除 N 个」。三个 G 的清理漏了一个文件，
靠事后回现场复查才发现。

本测试固定三件事：
1. 判据只命中「盘符真实存在」的 `/x/` 写法，不误伤 `/x/dynamic/feed` 这类 API 路径
2. 批量删除缺一个文件时**整体中止**，不许删一半
3. 删除/移动后必须回读，残留就抛错，不许返回成功

用法：python -m unittest scripts.test_pathguard
"""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pathguard as PG                                       # noqa: E402


def _existing_drive():
    """本机真实存在的盘符（测试不硬编 D，换机器也能跑）。"""
    for ch in "CDEFGH":
        if os.path.exists(ch + ":/"):
            return ch
    return None


def _dead_drive():
    """本机不存在的盘符，用来验证「不误伤」。"""
    for ch in "ZYXWV":
        if not os.path.exists(ch + ":/"):
            return ch
    return None


class DetectionTest(unittest.TestCase):
    """判据本身：命中要有理由，不命中也要有理由。"""

    def setUp(self):
        self.live = _existing_drive()
        self.dead = _dead_drive()
        if not self.live:
            self.skipTest("本机找不到任何可用盘符（非 Windows？）")

    def test_命中真实盘符的斜杠写法(self):
        self.assertTrue(PG.is_msys_drive_path(
            "/%s/ai-case-library/README.md" % self.live.lower()))
        # 大写盘符同样命中
        self.assertTrue(PG.is_msys_drive_path(
            "/%s/ai-case-library/README.md" % self.live.upper()))

    def test_不命中不存在的盘符(self):
        """`/x/dynamic/feed/article/draft/add` 这类接口路径不能被拦。"""
        if not self.dead:
            self.skipTest("本机没有空盘符")
        self.assertFalse(PG.is_msys_drive_path("/%s/dynamic/feed" % self.dead.lower()))

    def test_不命中正常写法(self):
        for p in ("D:/ai-case-library/x", "//server/share/x", "tmp/x",
                  "./tmp/x", "../x", "/", "", "/d", "/dd/x", "C:\\x"):
            self.assertFalse(PG.is_msys_drive_path(p), "误判: %r" % p)

    def test_非字符串对象不猜(self):
        import pathlib
        self.assertFalse(PG.is_msys_drive_path(b"/d/x"))
        self.assertFalse(PG.is_msys_drive_path(None))
        self.assertFalse(PG.is_msys_drive_path(123))
        # Path 对象会被 os.fspath 转成字符串后再判
        self.assertFalse(PG.is_msys_drive_path(pathlib.Path("D:/x")))


class CheckTest(unittest.TestCase):
    """check() 的报错必须直接给出可用的纠正写法。"""

    def setUp(self):
        self.live = _existing_drive()
        if not self.live:
            self.skipTest("非 Windows 环境")
        self.bad = "/%s/ai-case-library/data/x.json" % self.live.lower()
        self.good = "%s:/ai-case-library/data/x.json" % self.live.upper()

    def tearDown(self):
        os.environ.pop("PATHGUARD", None)

    def test_命中时抛MSYSPathError且带建议(self):
        with self.assertRaises(PG.MSYSPathError) as ctx:
            PG.check(self.bad, "删除")
        msg = str(ctx.exception)
        self.assertIn("删除", msg)
        self.assertIn(self.good, msg, "报错里要给出纠正后的写法")
        self.assertIn("PATHGUARD=0", msg, "报错里要说明应急关闭方式")

    def test_报错里的解析结果与真实解析一致(self):
        """盘符归属不能想当然 —— `/c/Users` 可能被解析到 D 盘的 c 目录。"""
        with self.assertRaises(PG.MSYSPathError) as ctx:
            PG.check(self.bad, "删除")
        msg = str(ctx.exception)
        self.assertIn(repr(os.path.abspath(self.bad)), msg,
                      "报错必须给出 Python 的真实解析结果（repr 形式）")
        self.assertIn("当前盘符", msg)

    def test_正常路径原样返回(self):
        self.assertEqual(PG.check(self.good), self.good)

    def test_环境变量可关闭(self):
        os.environ["PATHGUARD"] = "0"
        self.assertEqual(PG.check(self.bad), self.bad)

    def test_check_all一次报全部(self):
        live = self.live.lower()
        bads = ["/%s/a/x" % live, "%s:/ok" % live.upper(), "/%s/b/y" % live]
        with self.assertRaises(PG.MSYSPathError) as ctx:
            PG.check_all(bads, "写入")
        msg = str(ctx.exception)
        self.assertIn("2", msg)      # 两个命中
        self.assertIn("3", msg)      # 三个总数
        self.assertNotIn("'%s:/ok'" % live.upper(), msg)

    def test_非nt平台不启用(self):
        with mock.patch.object(PG.os, "name", "posix"):
            self.assertFalse(PG.enabled())
            self.assertEqual(PG.check("/d/foo"), "/d/foo")


class StatAllTest(unittest.TestCase):
    """预检是「不许删一半」的唯一保障。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pg_stat_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_缺一个文件就整体抛错(self):
        a = os.path.join(self.tmp, "a.txt")
        open(a, "w").close()
        missing = os.path.join(self.tmp, "nope.txt")
        with self.assertRaises(FileNotFoundError) as ctx:
            PG.stat_all([a, missing])
        self.assertIn("nope.txt", str(ctx.exception))
        self.assertTrue(os.path.exists(a), "预检失败不许动文件")

    def test_missing_ok放行并分类(self):
        a = os.path.join(self.tmp, "a.txt")
        open(a, "w").close()
        existing, missing = PG.stat_all(
            [a, os.path.join(self.tmp, "nope.txt")], missing_ok=True)
        self.assertEqual(existing, [a])
        self.assertEqual(len(missing), 1)


class RemoveFilesTest(unittest.TestCase):
    """删除只能有两个结果：全删掉，或一个都没删。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pg_rm_")
        self.files = []
        for i in range(3):
            p = os.path.join(self.tmp, "f%d.txt" % i)
            with open(p, "w") as f:
                f.write("x" * (10 * (i + 1)))
            self.files.append(p)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_全部删除并返回字节数(self):
        res = PG.remove_files(self.files)
        self.assertEqual(len(res["removed"]), 3)
        self.assertEqual(res["bytes"], 60)
        self.assertEqual([p for p in self.files if os.path.exists(p)], [])

    def test_dry_run不动盘(self):
        res = PG.remove_files(self.files, dry_run=True)
        self.assertEqual(len(res["would_remove"]), 3)
        self.assertTrue(all(os.path.exists(p) for p in self.files))

    def test_缺文件时一个都不删(self):
        missing = os.path.join(self.tmp, "nope.txt")
        with self.assertRaises(FileNotFoundError):
            PG.remove_files(self.files + [missing])
        self.assertTrue(all(os.path.exists(p) for p in self.files),
                        "预检失败还删了文件 —— 这就是「删一半」")

    def test_missing_ok时删剩下的(self):
        res = PG.remove_files(
            self.files + [os.path.join(self.tmp, "nope.txt")], missing_ok=True)
        self.assertEqual(len(res["removed"]), 3)

    def test_删除无效时必须抛错不能报成功(self):
        """模拟「os.remove 被调用但文件还在」——上次漏文件的真实形态。"""
        with mock.patch.object(PG.os, "remove"):
            with self.assertRaises(OSError) as ctx:
                PG.remove_files(self.files)
        self.assertIn("残留", str(ctx.exception))
        self.assertTrue(all(os.path.exists(p) for p in self.files))


class MoveFilesTest(unittest.TestCase):
    """移动要回读两端：目标在、源消失。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pg_mv_")
        self.pairs = []
        for i in range(2):
            s = os.path.join(self.tmp, "src%d.txt" % i)
            with open(s, "w") as f:
                f.write("data%d" % i)
            self.pairs.append((s, os.path.join(self.tmp, "sub", "dst%d.txt" % i)))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_移动到新目录并回读两端(self):
        res = PG.move_files(self.pairs)
        self.assertEqual(len(res["moved"]), 2)
        for s, d in self.pairs:
            self.assertFalse(os.path.exists(s), "源应消失")
            self.assertTrue(os.path.exists(d), "目标应存在")

    def test_源缺失时一个都不动(self):
        with self.assertRaises(FileNotFoundError):
            PG.move_files(self.pairs + [(os.path.join(self.tmp, "nope"), "/tmp/x")])
        for s, _ in self.pairs:
            self.assertTrue(os.path.exists(s))

    def test_移动无效时抛错(self):
        with mock.patch("shutil.move"):
            with self.assertRaises(OSError) as ctx:
                PG.move_files(self.pairs)
        self.assertIn("回读不符", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
