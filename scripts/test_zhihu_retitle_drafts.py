#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zhihu_retitle_drafts 单测：不联网，FakeApi 注入。

重点守住四条：① 草稿箱没命中的历史留痕不许改 ② 只认「详情 + 列表」双回读
③ 改标题不许洗正文 ④ 本地三本台账只动 zhihu 那一条 title（cnblogs 同串不动）。
"""
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import zhihu_retitle_drafts as R                                    # noqa: E402

OLD3 = "游戏厅：Freedoom，免费合法的毁灭战士"
NEW3 = "Freedoom，免费合法的毁灭战士"
OLD2 = "游戏厅：2048，一个网页就能玩的数字合并"
NEW2 = "2048，一个网页就能玩的数字合并"
AID3 = "2102032057182446679"
AID2 = "2101282414362472778"


class FakeApi(object):
    def __init__(self, drafts=None, details=None, wash_content=False,
                 list_lags=False):
        self.remote = dict(drafts or {})
        self.details = dict(details or {})
        self.patches = []
        self.wash_content = wash_content
        self.list_lags = list_lags

    def drafts(self):
        if self.list_lags:
            # 详情已变、列表仍是旧值（模拟「提交成功但未生效」）
            return dict(getattr(self, "_frozen", None) or self.remote)
        return dict(self.remote)

    def detail(self, aid):
        return json.loads(json.dumps(self.details[aid]))

    def patch(self, aid, payload):
        self.patches.append((aid, dict(payload)))
        d = self.details[aid]
        if "title" in payload:
            d["title"] = payload["title"]
            if self.wash_content:
                d["content"] = ""
        if "content" in payload:
            d["content"] = payload["content"]
        if not self.list_lags:
            self.remote[aid] = d["title"]
        else:
            self._frozen = dict(self.remote)
        return {"id": aid}


def detail(aid, title, content="<p>正文</p>"):
    return {"id": aid, "title": title, "content": content, "state": "draft"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = self.tmp.name
        self.drafts = os.path.join(p, "zhihu_drafts.json")
        self.manifest = os.path.join(p, "publish_manifest.json")
        self.titles = os.path.join(p, "toutiao_titles.json")

        ledger = {
            "ext-ep-003-freedoom": {"article_id": AID3, "title": OLD3,
                                    "status": "draft", "url": "u3", "ts": "t3"},
            "ext-ep-002-2048": {"article_id": AID2, "title": OLD2,
                                "status": "draft", "url": "u2", "ts": "t2"},
            # 草稿箱早已没有这两篇（已发表/已删）⇒ 历史留痕，不许动
            "ep-001-super-mario-bros": {"article_id": "2099555668348949274",
                                        "title": "游戏厅 #1：Super Mario Bros",
                                        "status": "draft"},
            "ext-ep-001-super-mario-bros": {
                "article_id": "2100938408583611480",
                "title": "游戏厅：马力欧兄弟，被 JS 重写的 1985 经典",
                "status": "draft"},
            "flodesk": {"article_id": "x", "title": "普通标题", "status": "published"},
        }
        manifest = {
            "ext-ep-003-freedoom": {
                "zhihu": {"state": "draft_saved", "title": OLD3,
                          "draft_id": AID3, "remote_id": AID3, "attempt": 0},
                # 同一个旧标题，但是博客园已发表留痕 ⇒ 绝不能被改
                "cnblogs": {"state": "published", "title": OLD3, "attempt": 2},
            },
            "ext-ep-002-2048": {
                "zhihu": {"state": "draft_saved", "title": OLD2,
                          "draft_id": AID2, "remote_id": AID2},
                "cnblogs": {"state": "published", "title": OLD2},
            },
        }
        titles = {"ext-ep-003-freedoom": {"title": NEW3},
                  "ext-ep-002-2048": {"title": NEW2}}
        for f, obj, ind in ((self.drafts, ledger, 2),
                            (self.manifest, manifest, 1),
                            (self.titles, titles, 2)):
            with open(f, "w", encoding="utf-8", newline="") as fh:
                fh.write(json.dumps(obj, ensure_ascii=False, indent=ind))

        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.execute("CREATE TABLE platform_posts "
                          "(case_id TEXT, platform TEXT, title TEXT, "
                          " remote_id TEXT, draft_id TEXT, state TEXT)")
        for cid, aid, t in (("ext-ep-003-freedoom", AID3, OLD3),
                            ("ext-ep-002-2048", AID2, OLD2)):
            self.conn.execute("INSERT INTO platform_posts VALUES (?,?,?,?,?,?)",
                              (cid, "zhihu", t, aid, aid, "draft"))
        self.conn.commit()

        self.api = FakeApi(
            drafts={AID3: OLD3, AID2: OLD2, "other": "别的稿"},
            details={AID3: detail(AID3, OLD3), AID2: detail(AID2, OLD2)})

    def paths(self):
        return dict(drafts_path=self.drafts, manifest_path=self.manifest,
                    titles_path=self.titles, conn=self.conn)

    def sync_paths(self):
        return dict(drafts_path=self.drafts, manifest_path=self.manifest,
                    conn=self.conn)

    def raw(self, f):
        with open(f, "rb") as fh:
            return fh.read()

    def jload(self, f):
        with open(f, encoding="utf-8") as fh:
            return json.load(fh)


class TestDiscover(Base):
    def test_只挑线上草稿箱命中的两篇_历史留痕跳过(self):
        got = R.discover(self.drafts, self.titles, self.api)
        self.assertEqual([(c, a, o, n) for c, a, o, n in got],
                         [("ext-ep-002-2048", AID2, OLD2, NEW2),
                          ("ext-ep-003-freedoom", AID3, OLD3, NEW3)])
        self.assertNotIn("ep-001-super-mario-bros", [g[0] for g in got])
        self.assertNotIn("flodesk", [g[0] for g in got])

    def test_线上标题与台账对不上就跳过_不猜着改(self):
        self.api.remote[AID3] = "别的标题"
        self.assertNotIn("ext-ep-003-freedoom",
                         [g[0] for g in R.discover(self.drafts, self.titles, self.api)])

    def test_标题表里没有新标题就跳过(self):
        with open(self.titles, "w", encoding="utf-8") as f:
            json.dump({"ext-ep-002-2048": {"title": NEW2}}, f, ensure_ascii=False)
        self.assertEqual([g[0] for g in R.discover(self.drafts, self.titles, self.api)],
                         ["ext-ep-002-2048"])

    def test_新标题本身还带前缀就跳过(self):
        with open(self.titles, "w", encoding="utf-8") as f:
            json.dump({"ext-ep-003-freedoom": {"title": "游戏厅：别的"}}, f,
                      ensure_ascii=False)
        self.assertNotIn("ext-ep-003-freedoom",
                         [g[0] for g in R.discover(self.drafts, self.titles, self.api)])


class TestRetitleRemote(Base):
    def test_双回读命中才返回成功(self):
        ok, why = R.retitle_remote("ext-ep-003-freedoom", AID3, OLD3, NEW3, self.api)
        self.assertTrue(ok, why)
        self.assertEqual(self.api.patches, [(AID3, {"title": NEW3})])
        self.assertEqual(self.api.details[AID3]["content"], "<p>正文</p>")

    def test_改标题把正文洗掉就判失败且原样补回(self):
        self.api.wash_content = True
        ok, why = R.retitle_remote("ext-ep-003-freedoom", AID3, OLD3, NEW3, self.api)
        self.assertFalse(ok)
        self.assertIn("正文", why)
        self.assertEqual(self.api.details[AID3]["content"], "<p>正文</p>",
                         "正文必须被原样补回")
        self.assertEqual(self.api.patches[-1], (AID3, {"content": "<p>正文</p>"}))

    def test_详情变了但草稿箱列表没变_判失败(self):
        self.api.list_lags = True
        ok, why = R.retitle_remote("ext-ep-003-freedoom", AID3, OLD3, NEW3, self.api)
        self.assertFalse(ok)
        self.assertIn("列表", why)

    def test_已是新标题幂等_不发PATCH(self):
        self.api.details[AID3]["title"] = NEW3
        self.api.remote[AID3] = NEW3
        ok, why = R.retitle_remote("ext-ep-003-freedoom", AID3, OLD3, NEW3, self.api)
        self.assertTrue(ok)
        self.assertEqual(self.api.patches, [])

    def test_详情标题与台账旧值不符_拒绝改(self):
        self.api.details[AID3]["title"] = "莫名其妙的标题"
        ok, _ = R.retitle_remote("ext-ep-003-freedoom", AID3, OLD3, NEW3, self.api)
        self.assertFalse(ok)
        self.assertEqual(self.api.patches, [])


class TestSyncLocal(Base):
    def test_三本账只动zhihu的title_cnblogs同串不动(self):
        notes = R.sync_local("ext-ep-003-freedoom", AID3, OLD3, NEW3, **self.sync_paths())
        d = self.jload(self.drafts)
        m = self.jload(self.manifest)
        self.assertEqual(d["ext-ep-003-freedoom"]["title"], NEW3)
        self.assertEqual(d["ext-ep-003-freedoom"]["ts"], "t3", "别的时间字段不许动")
        self.assertEqual(d["ext-ep-002-2048"]["title"], OLD2, "另一篇不许被顺手改")
        self.assertEqual(d["ep-001-super-mario-bros"]["title"],
                         "游戏厅 #1：Super Mario Bros", "历史留痕不许动")
        self.assertEqual(m["ext-ep-003-freedoom"]["zhihu"]["title"], NEW3)
        self.assertEqual(m["ext-ep-003-freedoom"]["cnblogs"]["title"], OLD3)
        self.assertEqual(m["ext-ep-003-freedoom"]["zhihu"]["attempt"], 0)
        row = self.conn.execute("SELECT title,state FROM platform_posts "
                                "WHERE case_id='ext-ep-003-freedoom'").fetchone()
        self.assertEqual(row, (NEW3, "draft"), "只改 title，state 不许被 upsert 重建")
        self.assertTrue(any("db" in n for n in notes))

    def test_写盘格式保真_LF且无尾换行(self):
        R.sync_local("ext-ep-003-freedoom", AID3, OLD3, NEW3, **self.sync_paths())
        for f in (self.drafts, self.manifest):
            b = self.raw(f)
            self.assertNotIn(b"\r\n", b)
            self.assertFalse(b.endswith(b"\n"))

    def test_同步幂等_二次不改字节(self):
        R.sync_local("ext-ep-003-freedoom", AID3, OLD3, NEW3, **self.sync_paths())
        a = self.raw(self.drafts), self.raw(self.manifest)
        notes = R.sync_local("ext-ep-003-freedoom", AID3, OLD3, NEW3, **self.sync_paths())
        b = self.raw(self.drafts), self.raw(self.manifest)
        self.assertEqual(a, b)
        self.assertFalse(any("→" in n for n in notes))


class TestRun(Base):
    def test_干跑不发PATCH也不写盘(self):
        before = self.raw(self.drafts)
        rc = R.run(go=False, out=io.StringIO(), api=self.api, **self.paths())
        self.assertEqual(rc, 0)
        self.assertEqual(self.api.patches, [])
        self.assertEqual(self.raw(self.drafts), before)

    def test_回读失败绝不写本地台账(self):
        self.api.list_lags = True
        before = self.raw(self.drafts)
        rc = R.run(go=True, out=io.StringIO(), api=self.api, **self.paths())
        self.assertEqual(rc, 1)
        self.assertEqual(self.raw(self.drafts), before)
        self.assertEqual(self.conn.execute(
            "SELECT title FROM platform_posts WHERE case_id='ext-ep-003-freedoom'"
        ).fetchone()[0], OLD3)

    def test_go成功两篇且check归零(self):
        rc = R.run(go=True, out=io.StringIO(), api=self.api, **self.paths())
        self.assertEqual(rc, 0)
        self.assertEqual(R.discover(self.drafts, self.titles, self.api), [])
        self.assertEqual(R.run(check=True, out=io.StringIO(),
                               api=self.api, **self.paths()), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
