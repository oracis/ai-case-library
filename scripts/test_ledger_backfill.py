#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ledger_backfill.py 的回归锁：纯判据 + 临时目录写盘 + 失败回滚。

不联网、不碰仓库真实台账：所有写盘用例都复制 data/ 到临时目录后用
--data-dir / --tmp-dir 指过去。锁的是 2026-11-28 四次补账踩过的坑：
  · 公众号 appmsgid 是草稿镜像，真 id 在 published_appmsgid；
  · 已补过的必须 SKIP，id 冲突的必须 REFUSE；
  · 草稿→已发要同时移出草稿行、补已发行、清 db.draft_id；
  · JSON 写成功但 db 写失败必须整组回滚。
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import ledger_backfill as B  # noqa: E402

REAL_DATA = ROOT / "data"
FILES = ("ledger.db", "bili_drafts.json", "bili_published.json",
         "wechat_published.json", "xhs_drafts.json", "xhs_published.json")


def _tmp_store(cls):
    td = tempfile.TemporaryDirectory()
    cls.addCleanup(td.cleanup)
    base = Path(td.name)
    data, tmp = base / "data", base / "tmp"
    data.mkdir()
    for name in FILES:
        shutil.copy2(str(REAL_DATA / name), str(data / name))
    return B.Store(data, tmp)


class TestPlans(unittest.TestCase):
    def test_bili_apply(self):
        drafts = [{"id": "wagiff-app", "article_id": "343836"}]
        self.assertEqual(B.plan_bili(drafts, [], {"state": "draft", "remote_id": "343836"},
                                     "wagiff-app", "343836", "54107760", "2026-11-27 10:15",
                                     "Wagiff")[0], "apply")

    def test_bili_skip_when_applied(self):
        pubs = [{"id": "wagiff-app", "article_id": "54107760"}]
        self.assertEqual(B.plan_bili([], pubs,
                                     {"state": "published", "remote_id": "54107760"},
                                     "wagiff-app", "343836", "54107760", "t", "Wagiff")[0],
                         "skip")

    def test_bili_refuse_id_conflict(self):
        pubs = [{"id": "wagiff-app", "article_id": "99999999"}]
        self.assertEqual(B.plan_bili([], pubs, None, "wagiff-app", "343836",
                                     "54107760", "t", "Wagiff")[0], "refuse")

    def test_wechat_skip_draft_mirror(self):
        row = {"appmsgid": "100001035", "published_appmsgid": "2247485070",
               "status": "published"}
        self.assertEqual(B.plan_wechat({"chatsell-co": row}, None, "chatsell-co",
                                       "2247485070", "t", "Chatsell")[0], "skip")

    def test_wechat_apply_missing(self):
        self.assertEqual(B.plan_wechat({}, None, "brand-new", "2247485099", "t", "X")[0],
                         "apply")

    def test_wechat_refuse_conflict(self):
        row = {"appmsgid": "100000001", "published_appmsgid": "2247480001",
               "status": "published"}
        self.assertEqual(B.plan_wechat({"x": row}, None, "x", "2247489999", "t", "X")[0],
                         "refuse")

    def test_xhs_skip_and_apply_and_refuse(self):
        pub = [{"id": "static-run", "note_id": "abc"}]
        self.assertEqual(B.plan_xhs([], pub, None, "static-run", "abc", "Static")[0], "skip")
        self.assertEqual(B.plan_xhs([{"id": "static-run", "at": "old"}],
                                    [{"id": "static-run"}], None, "static-run",
                                    "abc", "Static")[0], "apply")
        self.assertEqual(B.plan_xhs([], pub, None, "static-run", "different", "Static")[0],
                         "refuse")

    def test_norm_ts_rejects_now_style(self):
        with self.assertRaises(ValueError):
            B.norm_ts("今天")


class TestWriteLike(unittest.TestCase):
    def test_preserves_crlf_and_indent(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.json"
            p.write_bytes(b'{\r\n "a": 1\r\n}')
            B.write_like(str(p), {"a": 2})
            raw = p.read_bytes()
            self.assertIn(b"\r\n", raw)
            self.assertNotIn(b"\n ", raw.replace(b"\r\n", b""))
            self.assertEqual(json.loads(raw.decode("utf-8")), {"a": 2})


class TestIntegration(unittest.TestCase):
    def _bili_prestate(self, store):
        drafts = B.read_json(store.data / "bili_drafts.json")
        pubs = B.read_json(store.data / "bili_published.json")
        rec = dict([r for r in pubs if r.get("id") == "wagiff-app"][0])
        rec["article_id"] = "343836"   # 还原成当时的草稿 id
        rec.pop("verified_at", None)
        B.write_like(store.data / "bili_published.json",
                     [r for r in pubs if r.get("id") != "wagiff-app"])
        B.write_like(store.data / "bili_drafts.json", drafts + [rec])
        conn = sqlite3.connect(str(store.db))
        conn.execute("update platform_posts set state='draft', remote_id='343836', "
                     "draft_id='343836', published_at=NULL where case_id='wagiff-app' "
                     "and platform='bilibili'")
        conn.commit()
        conn.close()

    def test_bili_promote_apply(self):
        s = _tmp_store(self)
        self._bili_prestate(s)
        rc = B.main(["bili-promote", "--case", "wagiff-app", "--draft-id", "343836",
                     "--article-id", "54107760", "--at", "2026-11-27 10:15",
                     "--title", "Wagiff：中东北非男性的戒断社区", "--apply",
                     "--data-dir", str(s.data), "--tmp-dir", str(s.tmp)])
        self.assertEqual(rc, 0)
        self.assertFalse(B._find(B.read_json(s.data / "bili_drafts.json"), "wagiff-app"))
        pub = B._find(B.read_json(s.data / "bili_published.json"), "wagiff-app")
        self.assertEqual(len(pub), 1)
        self.assertEqual(pub[0]["article_id"], "54107760")
        conn = sqlite3.connect(str(s.db))
        row = conn.execute("select state,remote_id,draft_id,published_at from platform_posts "
                           "where case_id='wagiff-app' and platform='bilibili'").fetchone()
        conn.close()
        self.assertEqual(row, ("published", "54107760", "", "2026-11-27 10:15"))
        self.assertTrue(list(s.tmp.glob("backup_bili_*")))

    def test_wechat_add_apply(self):
        s = _tmp_store(self)
        path = s.data / "wechat_published.json"
        data = B.read_json(path)
        del data["chatsell-co"]
        B.write_like(path, data)
        conn = sqlite3.connect(str(s.db))
        conn.execute("delete from platform_posts where case_id='chatsell-co' and platform='wechat'")
        conn.commit()
        conn.close()
        rc = B.main(["wechat-add", "--case", "chatsell-co", "--appmsgid", "2247485070",
                     "--at", "2026-11-28 10:49", "--title", "Chatsell.co", "--apply",
                     "--data-dir", str(s.data), "--tmp-dir", str(s.tmp)])
        self.assertEqual(rc, 0)
        row = B.read_json(path)["chatsell-co"]
        self.assertEqual(row["published_appmsgid"], "2247485070")
        conn = sqlite3.connect(str(s.db))
        got = conn.execute("select remote_id,published_at from platform_posts "
                           "where case_id='chatsell-co' and platform='wechat'").fetchone()
        conn.close()
        self.assertEqual(got, ("2247485070", "2026-11-28 10:49"))

    def test_xhs_fix_apply(self):
        s = _tmp_store(self)
        drafts = B.read_json(s.data / "xhs_drafts.json")
        pubs = B.read_json(s.data / "xhs_published.json")
        drafts.append({"id": "static-run", "at": "2026-10-25 01:48"})
        for r in pubs:
            if r.get("id") == "static-run":
                r.pop("note_id", None)
                r.pop("title", None)
        B.write_like(s.data / "xhs_drafts.json", drafts)
        B.write_like(s.data / "xhs_published.json", pubs)
        rc = B.main(["xhs-fix", "--case", "static-run",
                     "--note-id", "6b0a4319000000002b0182bc",
                     "--title", "Static.run：静态网站免费托管", "--apply",
                     "--data-dir", str(s.data), "--tmp-dir", str(s.tmp)])
        self.assertEqual(rc, 0)
        self.assertFalse(B._find(B.read_json(s.data / "xhs_drafts.json"), "static-run"))
        row = B._find(B.read_json(s.data / "xhs_published.json"), "static-run")[0]
        self.assertEqual(row["note_id"], "6b0a4319000000002b0182bc")
        self.assertEqual(row["title"], "Static.run：静态网站免费托管")

    def test_db_failure_rolls_back_json(self):
        s = _tmp_store(self)
        self._bili_prestate(s)
        before_drafts = (s.data / "bili_drafts.json").read_bytes()
        before_pubs = (s.data / "bili_published.json").read_bytes()
        conn = sqlite3.connect(str(s.db))
        conn.execute("create trigger boom before update on platform_posts "
                     "begin select raise(abort, 'boom'); end")
        conn.commit()
        conn.close()
        rc = B.main(["bili-promote", "--case", "wagiff-app", "--draft-id", "343836",
                     "--article-id", "54107760", "--at", "2026-11-27 10:15",
                     "--title", "Wagiff", "--apply",
                     "--data-dir", str(s.data), "--tmp-dir", str(s.tmp)])
        self.assertEqual(rc, 2)
        self.assertEqual((s.data / "bili_drafts.json").read_bytes(), before_drafts)
        self.assertEqual((s.data / "bili_published.json").read_bytes(), before_pubs)

    def test_skip_does_not_write(self):
        s = _tmp_store(self)
        before = (s.data / "wechat_published.json").read_bytes()
        rc = B.main(["wechat-add", "--case", "chatsell-co", "--appmsgid", "2247485070",
                     "--at", "2026-11-28 10:49", "--title", "Chatsell.co", "--apply",
                     "--data-dir", str(s.data), "--tmp-dir", str(s.tmp)])
        self.assertEqual(rc, 0)
        self.assertEqual((s.data / "wechat_published.json").read_bytes(), before)
        self.assertFalse(list(s.tmp.glob("backup_wx_*")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
