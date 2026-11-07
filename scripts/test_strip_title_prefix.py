# -*- coding: utf-8 -*-
"""`strip_title_prefix.py`（A 档 / PRD #79）的契约测试。

全部在 TemporaryDirectory 里跑：`--games-root` / `--titles-path` 显式注入，
绝不读、绝不写真仓的 content/ 或 data/toutiao_titles.json。
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import strip_title_prefix as S                       # noqa: E402

PREFIX = "游戏厅："
TITLE = "2048，一个网页就能玩的数字合并"
BODY = "\n## 正文\n这是本篇专有内容，不含栏目字样。\n"


def _md(path, title, body=BODY):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(("# " + title + "\n" + body).encode("utf-8"))


def _titles(path, entries, crlf=True, trailing=False):
    b = json.dumps(entries, ensure_ascii=False, indent=2).encode("utf-8")
    if crlf:
        b = b.replace(b"\n", b"\r\n")
    if trailing:
        b += b"\r\n" if crlf else b"\n"
    with open(path, "wb") as f:
        f.write(b)


def _run(S_, args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = S_.main(args)
    return rc, out.getvalue()


class TestStripTitlePrefix(unittest.TestCase):

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = d.name
        self.games = os.path.join(self.root, "games")
        self.tp = os.path.join(self.root, "titles.json")
        self.md = os.path.join(self.games, "content", "ep-001-x",
                               "platform", "aliyun.md")
        _md(self.md, PREFIX + TITLE)
        _titles(self.tp, {"ext-ep-001-x": {
            "summary": "4×4 方格滑动合并相同数字。", "title": PREFIX + TITLE}})
        self.argv = ["--games-root", self.games, "--titles-path", self.tp]

    def _raw(self):
        return S._read_bytes(self.md)

    def _traw(self):
        return S._read_bytes(self.tp)

    def test_干跑不写盘(self):
        m0, t0 = self._raw(), self._traw()
        rc, out = _run(S, self.argv)
        self.assertEqual(rc, 0)
        self.assertIn("干跑", out)
        self.assertEqual(self._raw(), m0)
        self.assertEqual(self._traw(), t0)

    def test_go_只改md首行_其余字节逐字相同(self):
        old = self._raw()
        rc, _ = _run(S, self.argv + ["--go"])
        self.assertEqual(rc, 0)
        new = self._raw()
        oh, osep, orest = old.partition(b"\n")
        nh, nsep, nrest = new.partition(b"\n")
        self.assertEqual(nh, b"# " + oh[len(S.H1):])
        self.assertEqual((nsep, nrest), (osep, orest))
        self.assertEqual(new.count(b"\n"), old.count(b"\n"))
        self.assertEqual(len(old) - len(new), len(S.PREFIX_B))
        self.assertNotIn(b"\r", new)

    def test_go_标题表保持CRLF与无尾换行(self):
        old = self._traw()
        n_crlf = old.count(b"\r\n")
        _run(S, self.argv + ["--go"])
        new = self._traw()
        self.assertEqual(new.count(b"\r\n"), n_crlf)
        self.assertFalse(new.endswith(b"\n"))
        self.assertEqual(len(old) - len(new), len(S.PREFIX_B))
        self.assertEqual(json.loads(new.decode("utf-8"))["ext-ep-001-x"]["title"],
                         TITLE)
        # summary 与其它字段一字不动
        self.assertEqual(json.loads(new.decode("utf-8"))["ext-ep-001-x"]["summary"],
                         json.loads(old.decode("utf-8"))["ext-ep-001-x"]["summary"])

    def test_幂等_第二次零改动(self):
        _run(S, self.argv + ["--go"])
        m1, t1 = self._raw(), self._traw()
        rc, out = _run(S, self.argv + ["--go"])
        self.assertEqual(rc, 0)
        self.assertIn("无改动（幂等）", out)
        self.assertEqual(self._raw(), m1)
        self.assertEqual(self._traw(), t1)

    def test_check_有残留返回1_清零后返回0(self):
        rc1, _ = _run(S, self.argv + ["--check"])
        self.assertEqual(rc1, 1)
        _run(S, self.argv + ["--go"])
        rc2, out = _run(S, self.argv + ["--check"])
        self.assertEqual(rc2, 0)
        self.assertIn("0 残留", out)

    def test_首行变体只报告不动(self):
        variant = os.path.join(self.games, "content", "ep-001-x",
                               "platform", "csdn.md")
        _md(variant, "别的写法：" + PREFIX + TITLE)
        v0 = S._read_bytes(variant)
        rc, out = _run(S, self.argv + ["--go"])
        self.assertEqual(rc, 0)
        self.assertIn("首行变体（不动）：1", out)
        self.assertEqual(S._read_bytes(variant), v0)
        rc2, _ = _run(S, self.argv + ["--check"])
        self.assertEqual(rc2, 1, "变体必须让 --check 报出来，不能静默算干净")

    def test_title字节串重复时中止且不写任何文件(self):
        # summary 故意与 title 完全相同 ⇒ 精确字节串出现 2 次
        _titles(self.tp, {"ext-ep-001-x": {
            "summary": PREFIX + TITLE, "title": PREFIX + TITLE}})
        m0, t0 = self._raw(), self._traw()
        with self.assertRaises(SystemExit) as cm:
            _run(S, self.argv + ["--go"])
        self.assertIn("出现 2 次", str(cm.exception))
        self.assertEqual(self._raw(), m0, "md 也不许先写")
        self.assertEqual(self._traw(), t0)

    def test_去前缀后标题过发布闸不block(self):
        import publish_guard as G
        _run(S, self.argv + ["--go"])
        t = G.title_of(self.md)
        self.assertEqual(t, TITLE)
        blocks = [f for f in G.scan_title("aliyun", t, self.md)
                  if f["level"] == "block"]
        self.assertEqual(blocks, [])


if __name__ == "__main__":
    unittest.main()
