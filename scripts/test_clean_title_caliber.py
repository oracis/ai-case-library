# -*- coding: utf-8 -*-
"""`clean_title_caliber.py` 回归锁（全部用 tmp 夹具，不碰真产物 / 真台账）。

钉死四件事：两种旁注形态都清干净、正文数据卡不动、
保护集合五源求交、清理幂等。
"""
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import clean_title_caliber as C                       # noqa: E402

ART = ("<html><head></head><body>\n"
       "<!-- 标题由模板生成，发布前请手改 -->\n"
       '<h1 style="font-size:22px">%s</h1>\n'
       "<p>官方口径 MRR $835，TrustMRR 实时核对</p>\n"
       "</body></html>\n")
H1_MASK = lambda s: re.sub(r"(<h1[^>]*>).*?(</h1>)", r"\1X\2", s,
                           flags=re.S)       # noqa: E731


class RegexTests(unittest.TestCase):

    def test_plain_shape(self):
        self.assertEqual(
            C.clean_h1("20-0：MRR $835，TrustMRR 实时核对"),
            "20-0：MRR $835")

    def test_bracket_shape(self):
        self.assertEqual(
            C.clean_h1("3dplotter：近 30 天收入 $2,179，TrustMRR 实时核对"
                       "（非订阅制，MRR 恒 0）"),
            "3dplotter：近 30 天收入 $2,179")

    def test_halfwidth_comma_in_number_not_touched(self):
        self.assertIn("$2,179", C.clean_h1(
            "X：近 30 天收入 $2,179，TrustMRR 实时核对"))

    def test_no_caliber_is_noop(self):
        t = "Codédex：把学编程包装成「冒险旅程」的互动课程平台"
        self.assertEqual(C.clean_h1(t), t)

    def test_plain_unescapes_html_entities(self):
        self.assertEqual(
            C.plain("Account &amp; Expense Manager：MRR $632"),
            "Account & Expense Manager：MRR $632")

    def test_replace_h1_only_touches_h1(self):
        raw = ART % "20-0：MRR $835，TrustMRR 实时核对"
        new, ok = C.replace_h1(raw)
        self.assertTrue(ok)
        self.assertIn('<h1 style="font-size:22px">20-0：MRR $835</h1>', new)
        self.assertEqual(new.count("TrustMRR 实时核对"), 1,
                         "正文「官方口径」数据卡的旁注必须保留")
        self.assertEqual(H1_MASK(raw), H1_MASK(new),
                         "除 h1 外必须零字节改动")

    def test_replace_h1_is_idempotent(self):
        raw = ART % "20-0：MRR $835，TrustMRR 实时核对"
        once, _ = C.replace_h1(raw)
        twice, ok = C.replace_h1(once)
        self.assertFalse(ok)
        self.assertEqual(twice, once)


class ProtectTests(unittest.TestCase):
    """五源求交，任何一源命中都得保护；全不命中才可清。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dd = self.tmp.name
        self.db = os.path.join(self.dd, "ledger.db")
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE platform_posts(case_id TEXT, "
                     "platform TEXT, state TEXT)")
        conn.execute("INSERT INTO platform_posts VALUES "
                     "('only-db','wechat','published')")
        conn.execute("INSERT INTO platform_posts VALUES "
                     "('clean-me','wechat','draft')")
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def _json(self, name, obj):
        with open(os.path.join(self.dd, name), "w", encoding="utf-8",
                  newline="") as f:
            json.dump(obj, f, ensure_ascii=False)

    def test_five_sources(self):
        self._json("wechat_published.json", {
            "only-wp": {"status": "published", "title": "x"},
            "clean-me": {"status": "draft"}})
        self._json("publish_manifest.json", {
            "only-mf": {"wechat": {"state": "published"}}})
        self._json("some_other.json", {
            "title": "T：MRR $1，TrustMRR 实时核对"})
        self._json("wechat_standalone.json", {
            "某篇": {"note": "提到 only-sa 这篇"}})
        titles = {
            "only-wp": "A", "only-mf": "B", "only-db": "C", "only-sa": "D",
            "only-title": "T：MRR $1，TrustMRR 实时核对",
            "clean-me": "E：MRR $9，TrustMRR 实时核对",
        }
        prot = C.protected_ids(titles, self.dd, self.db)
        self.assertEqual(
            prot, {"only-wp", "only-mf", "only-db", "only-sa", "only-title"})
        self.assertNotIn("clean-me", prot)

    def test_draft_status_is_not_protected(self):
        self._json("wechat_published.json", {
            "clean-me": {"status": "draft"}})
        self._json("publish_manifest.json", {})
        self._json("wechat_standalone.json", {})
        prot = C.protected_ids(
            {"clean-me": "E：MRR $9，TrustMRR 实时核对"}, self.dd, self.db)
        self.assertNotIn("clean-me", prot)


class PlanTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.art = os.path.join(self.tmp.name, "articles")
        self.dd = os.path.join(self.tmp.name, "data")
        os.makedirs(self.art)
        os.makedirs(self.dd)

    def tearDown(self):
        self.tmp.cleanup()

    def _article(self, cid, title):
        with open(os.path.join(self.art, cid + ".html"), "w",
                  encoding="utf-8", newline="") as f:
            f.write(ART % title)

    def test_scan_ignores_clean_titles(self):
        self._article("dirty", "X：MRR $1，TrustMRR 实时核对")
        self._article("clean", "Codédex：把学编程包装成冒险旅程")
        self.assertEqual(set(C.scan(self.art)), {"dirty"})

    def test_plan_then_clean_is_idempotent(self):
        self._article("a", "A：MRR $1，TrustMRR 实时核对")
        self._article("b", "B：近 30 天收入 $2，TrustMRR 实时核对"
                            "（非订阅制，MRR 恒 0）")
        # 空台账 ⇒ 没有保护对象
        for n in ("wechat_published.json", "publish_manifest.json",
                  "wechat_standalone.json"):
            with open(os.path.join(self.dd, n), "w", encoding="utf-8") as f:
                json.dump({}, f)
        _, _, prot, todo = C.plan(self.art, self.dd,
                                  os.path.join(self.dd, "missing.db"))
        self.assertEqual(prot, set())
        self.assertEqual(set(todo), {"a", "b"})
        for cid, v in todo.items():
            new, ok = C.replace_h1(v["text"])
            self.assertTrue(ok)
            with open(v["path"], "w", encoding="utf-8", newline="") as f:
                f.write(new)
        _, _, _, todo2 = C.plan(self.art, self.dd,
                                os.path.join(self.dd, "missing.db"))
        self.assertEqual(todo2, {}, "清理后复跑必须 0 个待改（幂等）")


class GuardTailTests(unittest.TestCase):
    """2026-11-04 判据校准：清完旁注后标题以金额数字收尾，不能误报「半个词」。

    但「长度恰好等于平台上限 + 尾字数字」仍是硬截断证据
    （11-02 头条「Chessigma：…拿下 10」就是 `t[:30]` 切出来的）。
    """

    def test_short_title_ending_with_digit_has_no_tail_warn(self):
        import publish_guard as G
        hits = [f for f in G.scan_title("wechat", "20-0：MRR $835")
                if f["rule"] == "标题尾字可疑"]
        self.assertEqual(hits, [])

    def test_bracket_form_after_clean_also_no_tail_warn(self):
        import publish_guard as G
        hits = [f for f in G.scan_title(
            "wechat", "3dplotter：近 30 天收入 $2,179")
                if f["rule"] == "标题尾字可疑"]
        self.assertEqual(hits, [])

    def test_limit_length_digit_still_blocks(self):
        import publish_guard as G
        limit = G.TITLE_MAX["toutiao"]
        t = "X" * (limit - 1) + "0"
        self.assertEqual(len(t), limit)
        rules = [f["rule"] for f in G.scan_title("toutiao", t)]
        self.assertIn("疑似硬截断", rules)

    def test_chinese_function_word_still_warns(self):
        import publish_guard as G
        t = "这个产品做了很久但最后还是没说完了一"
        rules = [f["rule"] for f in G.scan_title("wechat", t)]
        self.assertIn("标题尾字可疑", rules)


if __name__ == "__main__":
    unittest.main()
