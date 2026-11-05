#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""`publish_one --yes` 的回读定案（回归锁）。

钉住 2026-11-05 修的洞：点完「预览并发布」**不再无条件 `mark_published`**，
而要回读「已发表列表 / 草稿箱」再定案。三种结局各自记账：

  ① 已发表命中        → published（拿到 gid 就带上）
  ② 草稿箱还有        → draft（= 只到预览步，可以重试）
  ③ 两侧都没有        → auditing（**挡住重发**，等人工复核）

为什么值得一组测试：旧实现点完就写「已发表」且 `remote_id` 空，
`publish_guard` 的 dedupe 只认 state ⇒ 一篇其实还躺在草稿箱的稿
**再也不会被发出去**，且全程零报错（危害方向是漏发，不是重发）。
"""
import ast
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import db                      # noqa: E402
import publish_guard as G      # noqa: E402
import toutiao_publish as T    # noqa: E402


class _FakeCDP:
    """`_settle_ui_publish` 只要一个能被调用 eval 的对象（网络全被替换）。"""

    def eval(self, _js):
        return "{}"


class TestSettleUiPublish(unittest.TestCase):
    """三种结局 → 三种台账写法。"""

    PATCHED = ("fetch_published_articles", "fetch_draft_api_raw",
               "mark_published_remote", "mark_drafted", "mark_auditing")

    def setUp(self):
        self.calls = []
        self.pub = []          # fetch_published_articles 的返回
        self.draft = []        # fetch_draft_api_raw 的返回
        self._orig = {n: getattr(T, n) for n in self.PATCHED}
        T.fetch_published_articles = lambda cdp, max_page=5: list(self.pub)
        T.fetch_draft_api_raw = lambda cdp: list(self.draft)
        T.mark_published_remote = (
            lambda cid, gid, ev: self.calls.append(("published", cid, gid, ev)))
        T.mark_drafted = lambda cid: self.calls.append(("draft", cid))
        T.mark_auditing = lambda cid, ev="": self.calls.append(("auditing", cid, ev))
        self.c = {"id": "chessigma", "name": "Chessigma：国际象棋对局分析"}
        self.title = "Chessigma：把棋谱变成训练计划"

    def tearDown(self):
        for n, v in self._orig.items():
            setattr(T, n, v)

    # ---------- ① 已发表命中 ----------
    def test_已发表命中_记published且带gid(self):
        self.pub = [{"title": self.title, "url": "", "gid": "7401"}]
        self.assertTrue(T._settle_ui_publish(_FakeCDP(), self.c, self.title))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][:3], ("published", "chessigma", "7401"))

    def test_已发表命中但接口没回gid_照样记published(self):
        # 不能因为「拿不到 gid」就漏记 —— 漏记等于放它重发一遍
        self.pub = [{"title": self.title, "url": "", "gid": ""}]
        self.assertTrue(T._settle_ui_publish(_FakeCDP(), self.c, self.title))
        self.assertEqual(self.calls[0][0], "published")
        self.assertEqual(self.calls[0][2], "")

    def test_标题被平台改写_靠产品名仍能命中(self):
        self.pub = [{"title": "Chessigma：把棋谱变成训练计划（2026 版）",
                     "url": "", "gid": "7402"}]
        self.assertTrue(T._settle_ui_publish(_FakeCDP(), self.c, "另外一套旧标题"))
        self.assertEqual(self.calls[0][0], "published")

    # ---------- ② 停在前端预览步 ----------
    def test_还在草稿箱_记draft且返回False(self):
        self.draft = [{"title": self.title, "gid": "d-1"}]
        self.assertFalse(T._settle_ui_publish(_FakeCDP(), self.c, self.title))
        self.assertEqual(self.calls, [("draft", "chessigma")])

    def test_已发表与草稿箱都有_以已发表为准(self):
        # 草稿箱里可能是已发表稿的残留：先查已发表，不能被残留骗
        self.pub = [{"title": self.title, "url": "", "gid": "7403"}]
        self.draft = [{"title": self.title, "gid": "d-1"}]
        self.assertTrue(T._settle_ui_publish(_FakeCDP(), self.c, self.title))
        self.assertEqual(self.calls[0][0], "published")

    # ---------- ③ 两侧都没有 ----------
    def test_两侧都没命中_记auditing不算成功(self):
        self.assertFalse(T._settle_ui_publish(_FakeCDP(), self.c, self.title))
        self.assertEqual(self.calls[0][0], "auditing")
        self.assertIn("未命中", self.calls[0][2])

    def test_草稿箱读取失败也退auditing(self):
        T.fetch_draft_api_raw = lambda cdp: None       # 读失败 ≠ 没有
        self.assertFalse(T._settle_ui_publish(_FakeCDP(), self.c, self.title))
        self.assertEqual(self.calls[0][0], "auditing")


class TestSourceGuards(unittest.TestCase):
    """源码级回归：主链路不许再退回「点完就标已发表」。"""

    def setUp(self):
        with open(os.path.join(HERE, "toutiao_publish.py"), encoding="utf-8") as f:
            self.src = f.read()
        self.tree = ast.parse(self.src)

    def _func(self, name):
        for n in ast.walk(self.tree):
            if isinstance(n, ast.FunctionDef) and n.name == name:
                return n
        self.fail("找不到函数 %s" % name)

    def test_publish_one里不再直接调mark_published(self):
        fn = self._func("publish_one")
        called = {n.func.id for n in ast.walk(fn)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertNotIn("mark_published", called)
        self.assertIn("_settle_ui_publish", called)

    def test_点击发布后必须走回读定案(self):
        # `_click_text` 的返回值之后必须出现 `_settle_ui_publish` 调用，
        # 否则就是「点了就记账」的老写法
        fn = self._func("publish_one")
        body = ast.dump(fn)
        self.assertIn("_click_text", body)
        self.assertIn("_settle_ui_publish", body)

    def test_占号状态与守卫同值(self):
        # 两处各写一份是不得已（循环 import），靠这条断言防漂移
        self.assertEqual(tuple(T._OCCUPIED_STATES), tuple(G.PUBLISHED_STATES))


class TestAuditingOccupiesSlot(unittest.TestCase):
    """auditing 必须「占号」：既不重发，也不算已发表。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tt_settle_")
        self._old = os.environ.get("LEDGER_DB")
        os.environ["LEDGER_DB"] = os.path.join(self.dir, "ledger.db")

    def tearDown(self):
        db.close_conn()
        if self._old is None:
            os.environ.pop("LEDGER_DB", None)
        else:
            os.environ["LEDGER_DB"] = self._old
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_auditing进待发跳过集(self):
        T.mark_auditing("case-a", "test:双读未命中")
        self.assertIn("case-a", T._ids(T.PUB_FILE))

    def test_auditing被dedupe拦下(self):
        T.mark_auditing("case-a", "test:双读未命中")
        r = G.check_dupe("toutiao", "case-a",
                         db_path=os.environ["LEDGER_DB"],
                         sync_path=os.path.join(self.dir, "no_sync.json"))
        self.assertFalse(r["ok"])
        self.assertEqual(r["level"], "published")

    def test_auditing不写已发表JSON快照(self):
        # `toutiao_published.json` 是「已发表」口径，塞进去会把篇数算多
        before = os.path.getmtime(T.PUB_FILE) if os.path.isfile(T.PUB_FILE) else None
        T.mark_auditing("case-b", "test")
        after = os.path.getmtime(T.PUB_FILE) if os.path.isfile(T.PUB_FILE) else None
        self.assertEqual(before, after)


class TestUpsertPostCommits(unittest.TestCase):
    """`upsert_post` 必须自己提交（2026-11-05 修的**静默丢写**）。

    调用方普遍是 `get_conn() → upsert_post() → close_conn()`，而
    `close_conn()` 只是 `conn.close()` —— sqlite3 对未提交事务静默回滚。
    症状极具欺骗性：JSON 快照里有、SQLite 里没有，而 dedupe 只认 SQLite。
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tt_commit_")
        self.path = os.path.join(self.dir, "ledger.db")
        self._old = os.environ.get("LEDGER_DB")
        os.environ["LEDGER_DB"] = self.path

    def tearDown(self):
        db.close_conn()
        if self._old is None:
            os.environ.pop("LEDGER_DB", None)
        else:
            os.environ["LEDGER_DB"] = self._old
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_关连接后仍能读到(self):
        T.mark_auditing("case-p", "commit 测试")
        db.close_conn()
        import sqlite3
        c = sqlite3.connect(self.path)          # 另开连接 = 另一条读视图
        try:
            rows = c.execute(
                "SELECT state FROM platform_posts WHERE case_id='case-p'").fetchall()
        finally:
            c.close()
        self.assertEqual(rows, [("auditing",)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
