# -*- coding: utf-8 -*-
"""wechat_sync.py 的测试（**纯离线**，不碰浏览器、不碰网络）。

守的四条线（每条都对应一次真实踩坑）：
  1. **认亲优先用 appmsgid**，不是标题 —— 群发后服务端 id 不变；
     只用标题会在你后台改过标题时全盘判失联。
  2. **草稿箱必须分页** —— 只拉 begin=0 会把「分页截断」误判成「远端没有」。
  3. **UNKNOWN ≠ 已删** —— 任一侧接口没读成功，一律不下 REMOTE_MISSING。
     这是最危险的一条：误判会让 sync 建议删掉其实还在的草稿。
  4. **published 不自动改** —— 列表读到了但没有它，只能 WARN 让人核对。

用法：python -m unittest scripts.test_wechat_sync -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wechat_sync as S                                       # noqa: E402


# ---------------------------------------------------------------- 假数据
def case(cid, title=None):
    """构造一个最小 case，title_matcher 直接返回 title（不依赖发布脚本）。"""
    return {"id": cid, "name": cid, "title_want": title or ("标题-" + cid)}


def matcher(cid, c):
    return c.get("title_want")


class TestNormTitle(unittest.TestCase):
    """标题归一化：全角空格 / 不间断空格 / 大小写差异不该产生假警报。"""

    def test_去掉半角与全角空格(self):
        self.assertEqual(S.norm_title("Auto Reels"), S.norm_title("AutoReels"))
        self.assertEqual(S.norm_title("A　B"), S.norm_title("AB"))

    def test_大小写不敏感(self):
        self.assertEqual(S.norm_title("Kibu"), S.norm_title("kibu"))

    def test_空值安全(self):
        for v in (None, "", 0, []):
            self.assertEqual(S.norm_title(v), "")


class TestTitleIndex(unittest.TestCase):
    """认亲索引：期望标题优先、台账旧标题兜底、撞车要报出来。"""

    def test_期望标题优先于台账旧标题(self):
        cases = {"a": case("a", "新标题")}
        local = {"a": {"title": "旧标题"}}
        want, dup = S.build_title_index(cases, local, matcher)
        # 期望标题是主键
        self.assertEqual(want[S.norm_title("新标题")], "a")
        # 台账旧标题**保留**作兜底：改了标题的 case，远端老草稿还得靠它认亲
        self.assertEqual(want[S.norm_title("旧标题")], "a")
        self.assertEqual(dup, set())

    def test_台账旧标题不覆盖别人的期望标题(self):
        cases = {"a": case("a", "新标题")}
        local = {"b": {"title": "新标题"}}      # b 的台账标题撞上 a 的期望标题
        want, _ = S.build_title_index(cases, local, matcher)
        self.assertEqual(want[S.norm_title("新标题")], "a")   # a 赢

    def test_两条案例标题撞车要报_dup(self):
        cases = {"a": case("a", "同名"), "b": case("b", "同名")}
        want, dup = S.build_title_index(cases, {}, matcher)
        self.assertEqual(dup, {"a", "b"})

    def test_无_matcher_时只用台账标题(self):
        want, _ = S.build_title_index({}, {"a": {"title": "台账标题"}}, None)
        self.assertEqual(want[S.norm_title("台账标题")], "a")

    def test_matcher_抛异常不当成没标题(self):
        def boom(cid, c):
            raise RuntimeError("炸")

        want, _ = S.build_title_index({"a": case("a")}, {}, boom)
        self.assertEqual(want, {})


class TestMatchRemote(unittest.TestCase):
    """认亲优先级：appmsgid > 标题。"""

    def setUp(self):
        self.local = {
            "a": {"title": "标题-a", "appmsgid": "100"},
            "b": {"title": "标题-b", "appmsgid": "200"},
        }
        self.cases = {"a": case("a", "标题-a"), "b": case("b", "标题-b")}

    def test_按_appmsgid_认亲(self):
        remote = {"200": {"title": "你后台改过的标题", "appmsgid": 200}}
        m = S.match_remote(remote, [], self.local, self.cases, matcher)
        # 标题认不出来，但 id 对得上 ⇒ 仍认亲成功
        self.assertEqual(m["draft_by_cid"], {"b": "200"})
        self.assertEqual(m["draft_orphan"], [])

    def test_只有标题也能认亲(self):
        remote = {"999": {"title": "标题-b", "appmsgid": 999}}
        m = S.match_remote(remote, [], self.local, self.cases, matcher)
        self.assertEqual(m["draft_by_cid"], {"b": "999"})

    def test_认不出算_orphan_不硬塞(self):
        remote = {"777": {"title": "完全无关的一篇", "appmsgid": 777}}
        m = S.match_remote(remote, [], self.local, self.cases, matcher)
        self.assertEqual(m["draft_by_cid"], {})
        self.assertEqual(len(m["draft_orphan"]), 1)

    def test_已发表字段名带下划线(self):
        # appmsgpublish 用 appmsg_id（带下划线），草稿箱用 appmsgid
        pub = [{"appmsg_id": 100, "title": "标题-a", "publish_time": "1700000000"}]
        m = S.match_remote({}, pub, self.local, self.cases, matcher)
        self.assertEqual(list(m["pub_by_cid"].keys()), ["a"])

    def test_已发表_也能靠标题认亲(self):
        pub = [{"appmsg_id": 555, "title": "标题-a"}]
        m = S.match_remote({}, pub, self.local, self.cases, matcher)
        self.assertEqual(list(m["pub_by_cid"].keys()), ["a"])


class TestClassify(unittest.TestCase):
    """四态判定。**核心是 UNKNOWN 不能变成 REMOTE_MISSING。**"""

    def setUp(self):
        self.cases = {
            "ok": case("ok", "标题-ok"),
            "pub": case("pub", "标题-pub"),
            "gone": case("gone", "标题-gone"),
            "localpub": case("localpub", "标题-localpub"),
        }
        self.local = {
            "ok": {"status": "draft", "title": "标题-ok", "appmsgid": "1"},
            "pub": {"status": "draft", "title": "标题-pub", "appmsgid": "2"},
            "gone": {"status": "draft", "title": "标题-gone", "appmsgid": "3"},
            "localpub": {"status": "published", "title": "标题-localpub",
                         "appmsgid": "4"},
        }
        self.draft = {
            "1": {"title": "标题-ok", "appmsgid": 1},
            "4": {"title": "标题-localpub", "appmsgid": 4},
        }
        self.pub = [{"appmsg_id": 2, "title": "标题-pub",
                     "publish_time": "1700000000"}]

    def codes(self, diffs, level=None):
        return sorted(c for lv, c, cid, _ in diffs
                      if level is None or lv == level)

    def test_一致时零差异(self):
        local = {"ok": {"status": "draft", "title": "标题-ok", "appmsgid": "1"}}
        d = S.classify(local, {"1": {"title": "标题-ok"}}, [], self.cases,
                       title_matcher=matcher)
        self.assertEqual(d, [])

    def test_草稿没了但已发表_改published(self):
        d = S.classify(self.local, self.draft, self.pub, self.cases,
                       title_matcher=matcher)
        self.assertIn("DRAFT_GONE_BUT_PUBLISHED", self.codes(d, "FIX"))
        hit = [x for x in d if x[1] == "DRAFT_GONE_BUT_PUBLISHED"]
        self.assertEqual(hit[0][2], "pub")

    def test_两侧都没有_判REMOTE_MISSING(self):
        local = {"gone": {"status": "draft", "title": "标题-gone",
                          "appmsgid": "3"}}
        d = S.classify(local, {}, [], self.cases,
                       title_matcher=matcher)
        self.assertIn("REMOTE_MISSING", self.codes(d, "FIX"))

    def test_本地published且远端有_一致(self):
        d = S.classify(self.local, self.draft,
                       [{"appmsg_id": 4, "title": "标题-localpub"}],
                       self.cases, title_matcher=matcher)
        self.assertEqual(self.codes(d, "WARN"), [])
        self.assertEqual(self.codes(d, "UNKNOWN"), [])

    def test_本地published但远端列表没有_只WARN不自动改(self):
        # 关键：绝不能把「超出列表范围」当成台账错，自动改回去
        d = S.classify(self.local, self.draft, self.pub, self.cases,
                       title_matcher=matcher)
        self.assertIn("PUB_NOT_IN_LIST", self.codes(d, "WARN"))
        # localpub 只能出 WARN，不能同时被判成缺失/不可读
        hit = [x for x in d if x[2] == "localpub"]
        self.assertEqual([x[1] for x in hit], ["PUB_NOT_IN_LIST"])
        self.assertEqual([x[0] for x in hit], ["WARN"])

    def test_已发表列表读不到时本地published判UNKNOWN(self):
        d = S.classify(self.local, self.draft, [], self.cases,
                       draft_ok=True, pub_ok=False, title_matcher=matcher)
        self.assertIn("PUB_LIST_UNREADABLE", self.codes(d, "UNKNOWN"))

    def test_草稿箱读不到时绝不判REMOTE_MISSING(self):
        # 最危险的一条：把「没读到」误判成「远端没有」
        local = {"gone": {"status": "draft", "title": "标题-gone",
                          "appmsgid": "3"}}
        d = S.classify(local, {}, [], self.cases, draft_ok=False,
                       pub_ok=True, title_matcher=matcher)
        self.assertNotIn("REMOTE_MISSING", self.codes(d))
        self.assertIn("DRAFT_LIST_UNREADABLE", self.codes(d, "UNKNOWN"))

    def test_两侧都读不到时仍不判REMOTE_MISSING(self):
        local = {"gone": {"status": "draft", "title": "标题-gone",
                          "appmsgid": "3"}}
        d = S.classify(local, {}, [], self.cases, draft_ok=False,
                       pub_ok=False, title_matcher=matcher)
        self.assertNotIn("REMOTE_MISSING", self.codes(d))

    def test_标题撞车报UNKNOWN_TILECOLLISION(self):
        cases = {"a": case("a", "同名"), "b": case("b", "同名")}
        d = S.classify({}, {}, [], cases, title_matcher=matcher)
        self.assertIn("TITLE_COLLISION", self.codes(d, "UNKNOWN"))

    def test_远端孤儿草稿报INFO(self):
        local = {}
        d = S.classify(local, {"888": {"title": "认不出的草稿"}}, [],
                       self.cases, title_matcher=matcher)
        self.assertIn("REMOTE_ORPHAN", self.codes(d, "INFO"))

    def test_远端孤儿已发表报INFO(self):
        d = S.classify({}, [], [{"appmsg_id": 9, "title": "认不出"}],
                       self.cases, title_matcher=matcher)
        self.assertIn("PUB_ORPHAN", self.codes(d, "INFO"))


class TestApplyFixes(unittest.TestCase):
    """回写：只改 status、只加missing 标记，绝不删记录。

    ⚠⚠⚠ 每个用例都往**临时目录**写（`path=` 参数）。
    曾经这里直接调 `apply_fixes()`，它内部 `save_local()` 打到了真实的
    `data/wechat_published.json`，把 41 条台账覆盖成 1 条假数据，
    连`.bak` 都被第二次调用一起覆盖 —— 最后靠 `git show HEAD:` 才救回来。
    这个教训用 `test_单测绝不能碰真实台账` 钉死。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wxsync_")
        self.path = os.path.join(self.tmp, "wechat_published.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _apply(self, diffs, local):
        return S.apply_fixes(diffs, local, path=self.path)

    def test_只回写已发表不动标题(self):
        local = {"x": {"status": "draft", "title": "我改过的标题",
                       "appmsgid": "5"}}
        diffs = [("FIX", "DRAFT_GONE_BUT_PUBLISHED", "x", "")]
        n_pub, n_missing = self._apply(diffs, local)
        self.assertEqual((n_pub, n_missing), (1, 0))
        self.assertEqual(local["x"]["status"], "published")
        self.assertEqual(local["x"]["title"], "我改过的标题")   # 标题不动
        self.assertEqual(local["x"]["appmsgid"], "5")          # id 不动
        self.assertIn("synced_at", local["x"])

    def test_落盘内容与内存一致(self):
        local = {"x": {"status": "draft", "appmsgid": "5"}}
        self._apply([("FIX", "DRAFT_GONE_BUT_PUBLISHED", "x", "")], local)
        with open(self.path, encoding="utf-8") as f:
            got = json.load(f)
        self.assertEqual(got["x"]["status"], "published")

    def test_缺失只加标记不删(self):
        local = {"x": {"status": "draft", "appmsgid": "5"}}
        diffs = [("FIX", "REMOTE_MISSING", "x", "")]
        n_pub, n_missing = self._apply(diffs, local)
        self.assertEqual((n_pub, n_missing), (0, 1))
        self.assertTrue(local["x"]["missing"])
        self.assertIn("x", local)          # 记录还在
        self.assertEqual(local["x"]["status"], "draft")   # status 不变

    def test_WARN_UNKNOWN_一律不动(self):
        local = {"x": {"status": "draft"}}
        diffs = [
            ("WARN", "PUB_NOT_IN_LIST", "x", ""),
            ("UNKNOWN", "PUB_LIST_UNREADABLE", "x", ""),
            ("UNKNOWN", "TITLE_COLLISION", "x", ""),
            ("INFO", "REMOTE_ORPHAN", "", ""),
        ]
        n_pub, n_missing = self._apply(diffs, local)
        self.assertEqual((n_pub, n_missing), (0, 0))
        self.assertNotIn("missing", local["x"])

    def test_幂等_重复apply不会重复写(self):
        local = {"x": {"status": "draft"}}
        diffs = [("FIX", "DRAFT_GONE_BUT_PUBLISHED", "x", "")]
        self._apply(diffs, local)
        local["x"]["synced_at"] = "1999-01-01 00:00"
        n_pub, _ = self._apply(diffs, local)
        self.assertEqual(n_pub, 0)
        self.assertEqual(local["x"]["synced_at"], "1999-01-01 00:00")

    def test_改动前留备份(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"x": {"status": "draft"}}, f)
        local = {"x": {"status": "draft"}}
        self._apply([("FIX", "DRAFT_GONE_BUT_PUBLISHED", "x", "")], local)
        bak = self.path + ".bak"
        self.assertTrue(os.path.exists(bak))
        with open(bak, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["x"]["status"], "draft")

    def test_单测绝不能碰真实台账(self):
        """钉死那条红线：跑完全套测试后，真实台账必须一字未动。"""
        self.assertFalse(os.path.exists(S.PUBLISHED_PATH + ".tmp"))
        # 真实台账条数（41条）；变了说明有测试写到了生产文件
        real = S.load_local()
        self.assertGreater(len(real), 1,
                           "真实 data/wechat_published.json 只剩 %d 条 —— "
                           "被某个测试/调用写坏了，用 git show HEAD: 恢复"
                           % len(real))


def _begin_of(js):
    """从 eval 的 JS 里抠出 URL 的 begin 参数。

    ⚠ URL 被 json.dumps 转义过，`begin=30` 在 JS 里长成 `begin=30` 但引号是
    转义形式，直接 `search("begin=")` 仍能命中；用正则取数字最稳。
    """
    import re
    m = re.search(r"begin[^0-9]{0,8}(\d+)", js)
    return int(m.group(1)) if m else 0


class TestFetchPaging(unittest.TestCase):
    """分页：靠假 CDP 驱动，不连浏览器。"""

    class FakeCDP(object):
        """按 URL 里的 begin 参数返回对应页；pages 是 {begin: 该页条数}。"""

        def __init__(self, pages, fail_first=False):
            self.pages = pages
            self.fail_first = fail_first
            self.calls = []

        def eval(self, js, refresh_context=False):
            begin = _begin_of(js)
            self.calls.append(begin)
            if begin == 0 and self.fail_first:
                return None
            n = self.pages.get(begin, 0)
            is_pub = "appmsgpublish" in js
            if n == 0:
                if is_pub:
                    return json.dumps(
                        {"publish_page": json.dumps({"publish_list": []})})
                return json.dumps({"app_msg_list": [], "base_resp": {"ret": 0}})
            if is_pub:
                lst = [{"publish_type": 101, "publish_info": json.dumps(
                    {"msgid": begin * 1000 + i, "appmsg_info": [
                        {"appmsgid": begin * 100000 + i, "title": "p%d" % i,
                         "digest": "p%d" % i}]},
                    ensure_ascii=False)} for i in range(n)]
                return json.dumps({"publish_page": json.dumps(
                    {"publish_list": lst})})
            lst = [{"appmsgid": begin * 1000 + i, "title": "t%d" % i}
                   for i in range(n)]
            return json.dumps({"app_msg_list": lst,
                               "base_resp": {"ret": 0}})

    def test_草稿分页翻到空为止(self):
        cdp = self.FakeCDP({0: 10, 10: 10, 20: 10})
        got, ok = S.fetch_draft_remote(cdp, "T")
        self.assertTrue(ok)
        self.assertEqual(len(got), 30)
        self.assertEqual(cdp.calls, [0, 10, 20, 30])

    def test_每页满页时继续翻下一页(self):
        # 真实页大小是 10；不满页就该停。守住这条免得多余一轮请求
        cdp = self.FakeCDP({0: 7})
        got, ok = S.fetch_draft_remote(cdp, "T")
        self.assertTrue(ok)
        self.assertEqual(len(got), 7)
        self.assertEqual(cdp.calls, [0])

    def test_重复页按_appmsgid_去重(self):
        cdp = self.FakeCDP({0: 10, 10: 10, 20: 10})
        cdp.pages[20] = 10
        orig = cdp.eval

        def dup_page(js, refresh_context=False):
            # 第 3 页故意返回和第 1 页一样的 id（模拟服务端重复下发）
            if _begin_of(js) == 20:
                js = js.replace("begin=20", "begin=0") if \
                    "begin=20" in js else js
            return orig(js, refresh_context)

        got, ok = S.fetch_draft_remote(cdp, "T")
        self.assertTrue(ok)
        # 页码不重叠 ⇒ 三页共 30 条唯一
        self.assertEqual(len(got), 30)

    def test_第一页失败判ok_False_不当空(self):
        cdp = self.FakeCDP({0: 10}, fail_first=True)
        got, ok = S.fetch_draft_remote(cdp, "T")
        self.assertFalse(ok)
        self.assertEqual(got, {})

    def test_已发表双层JSON被正确解析(self):
        cdp = self.FakeCDP({0: 5})
        got, ok = S.fetch_published_remote(cdp, "T")
        self.assertTrue(ok)
        self.assertEqual(len(got), 5)
        self.assertIn("appmsg_id", got[0])

    def test_已发表列表读不到判ok_False(self):
        cdp = self.FakeCDP({0: 5}, fail_first=True)
        got, ok = S.fetch_published_remote(cdp, "T")
        self.assertFalse(ok)
        self.assertEqual(got, [])

    def test_已发表分页去重(self):
        cdp = self.FakeCDP({0: 10, 10: 10})
        got, ok = S.fetch_published_remote(cdp, "T")
        self.assertTrue(ok)
        self.assertEqual(len(got), 20)


class TestReport(unittest.TestCase):
    """报告文本：必须把「读取失败」和「结论」分开说清楚。"""

    def test_读取失败在标题里可见(self):
        txt = S.render_report([], 0, 0, 3, draft_ok=False, pub_ok=True)
        self.assertIn("读取失败", txt)
        self.assertIn("本地台账 3 条", txt)

    def test_完全一致时给出明确结论(self):
        txt = S.render_report([], 30, 12, 41)
        self.assertIn("完全一致", txt)

    def test_四种等级都有图例(self):
        txt = S.render_report([("FIX", "A", "x", "d")], 1, 1, 1)
        self.assertIn("FIX", txt)
        self.assertIn("WARN", txt)
        self.assertIn("UNKNOWN", txt)
        self.assertIn("INFO", txt)


class TestFlattenPublishItem(unittest.TestCase):
    """⚠⚠ 这是踩过的真 bug 的回归测试。

    `publish_list` 元素**只有** `publish_info` + `publish_type` 两个字段，
    没有 `appmsg_id`、也没有 `title`。只读 `it["title"]` 会全部取到 None，
    去重后 12 条被撞成 1 条 —— 害我一度以为「远端只群发了 1 篇」。
    真实结构是三层 JSON：
        publish_info(str) -> json.loads -> appmsg_info[0].{appmsgid,title}
    """

    def _item(self, titles, pt=101):
        pi = {"type": 9, "msgid": 1000000001, "appmsg_info": [
            {"appmsgid": 2247484000 + i, "title": t,
             "content_url": "https://mp.weixin.qq.com/s/x%d" % i,
             "digest": t, "is_deleted": False}
            for i, t in enumerate(titles)]}
        return {"publish_type": pt, "publish_info": json.dumps(pi,
                                                              ensure_ascii=False)}

    def test_解析出标题与appmsg_id(self):
        got = S._flatten_publish_item(self._item(["标题甲", "标题乙"]))
        self.assertEqual(len(got), 2)
        self.assertEqual([g["title"] for g in got], ["标题甲", "标题乙"])
        self.assertEqual(got[0]["appmsg_id"], 2247484000)
        self.assertEqual(got[0]["batch_msgid"], 1000000001)
        self.assertEqual(got[0]["publish_type"], 101)

    def test_多条文章不能只取头条(self):
        # 多图文的头条/次条标题各自独立，次条被漏掉 ⇒ 那篇本地会永远记着 draft
        got = S._flatten_publish_item(self._item(["头条", "次条"]))
        self.assertEqual(len(got), 2)

    def test_digest兜底(self):
        pi = {"msgid": 1, "appmsg_info": [
            {"appmsgid": 5, "digest": "只有摘要没标题"}]}
        got = S._flatten_publish_item(
            {"publish_info": json.dumps(pi, ensure_ascii=False)})
        self.assertEqual(got[0]["title"], "只有摘要没标题")

    def test_解析失败返回空而不是错标题(self):
        self.assertEqual(S._flatten_publish_item(
            {"publish_info": "{不是 json"}), [])
        self.assertEqual(S._flatten_publish_item({}), [])
        self.assertEqual(S._flatten_publish_item(
            {"publish_info": "{}"}), [])

    def test_空appmsg_info不炸(self):
        self.assertEqual(S._flatten_publish_item(
            {"publish_info": '{"msgid":1,"appmsg_info":[]}'}), [])


class TestFetchPublishedRealShape(unittest.TestCase):
    """用**真实三层结构**驱动 fetch_published_remote。"""

    class RealShapeCDP(object):
        def __init__(self, n_items, per_item=1):
            self.n_items = n_items
            self.per_item = per_item

        def eval(self, js, refresh_context=False):
            begin = _begin_of(js)
            if begin > 0 or self.n_items == 0:
                pp = {"publish_list": []}
            else:
                items = []
                for i in range(self.n_items):
                    pi = {"msgid": 1000 + i, "appmsg_info": [
                        {"appmsgid": 2247484000 + i * 10 + j,
                         "title": "文章%d-%d" % (i, j)}
                        for j in range(self.per_item)]}
                    items.append({"publish_type": 101,
                                  "publish_info": json.dumps(pi,
                                                            ensure_ascii=False)})
                pp = {"publish_list": items, "total_count": self.n_items}
            return json.dumps({"base_resp": {"ret": 0},
                               "publish_page": json.dumps(pp,
                                                          ensure_ascii=False)})

    def test_12条不再被撞成1条(self):
        """回归：真实远端就是 12 条，之前这里只返回 1 条。"""
        got, ok = S.fetch_published_remote(self.RealShapeCDP(12), "T")
        self.assertTrue(ok)
        self.assertEqual(len(got), 12)
        self.assertTrue(all(g["title"] for g in got))   # 没有 None 标题

    def test_多图文展开成多篇(self):
        got, ok = S.fetch_published_remote(
            self.RealShapeCDP(6, per_item=2), "T")
        self.assertTrue(ok)
        self.assertEqual(len(got), 12)

    def test_按appmsg_id去重(self):
        cdp = self.RealShapeCDP(6)
        orig = cdp.eval
        calls = {"n": 0}

        def dup(js, refresh_context=False):
            # 第一页反复返回同一批⇒ 模拟重复下发
            if _begin_of(js) == 0 and calls["n"] > 0:
                return orig("begin=0", refresh_context)
            calls["n"] += 1
            return orig(js, refresh_context)

        cdp.eval = dup
        got, _ = S.fetch_published_remote(cdp, "T")
        self.assertEqual(len(got), 6)

    def test_空列表判成功零条(self):
        got, ok = S.fetch_published_remote(self.RealShapeCDP(0), "T")
        self.assertTrue(ok)
        self.assertEqual(got, [])


class TestMatchPublishedByTitle(unittest.TestCase):
    """⚠ 已发表侧**只能靠标题认亲**。

    实测：本地台账记的是草稿 id（如 100000162），已发表列表里是新 id
    （2247484064）—— 群发后服务端换id，所以 id 匹配注定miss。
    """

    def setUp(self):
        self.local = {"rezi": {"title": "Rezi：AI 简历生成器", "appmsgid": "100000162"}}
        self.cases = {"rezi": {"id": "rezi", "name": "Rezi",
                               "title_want": "Rezi：AI 简历生成器"}}

    def test_靠标题认到新appmsg_id(self):
        pub = [{"appmsg_id": 2247484064, "title": "Rezi：AI 简历生成器"}]
        m = S.match_remote({}, pub, self.local, self.cases, matcher)
        self.assertEqual(list(m["pub_by_cid"].keys()), ["rezi"])
        self.assertEqual(m["pub_by_cid"]["rezi"]["appmsg_id"], 2247484064)

    def test_标题对不上才算孤儿(self):
        pub = [{"appmsg_id": 9, "title": "完全无关"}]
        m = S.match_remote({}, pub, self.local, self.cases, matcher)
        self.assertEqual(m["pub_by_cid"], {})
        self.assertEqual(len(m["pub_orphan"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)